# Deploying VMs from recipes

A recipe is the thing you pick in the VergeOS UI when you create a new VM.
Somebody has already decided what the VM looks like, which OS image it pulls,
how its drives are laid out and what cloud init should do on first boot. What
is left for you is a short list of questions: what to call it, how big it
should be, which network it lands on.

Automating a recipe deploy is really just answering those questions from a
playbook instead of a form. This guide walks through doing that well, and is
fairly blunt about the places it goes wrong, because most of them are not
obvious from the outside.

## The short version

```yaml
- name: Deploy a VM from a recipe
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Ask the recipe what it wants to know
      vergeio.vergeos.vm_recipe_info:
        name: "Ubuntu Server 22.04 (Jammy Jellyfish)"
        questions: true
        resolve_options: true
      register: recipe

    - name: Deploy
      vergeio.vergeos.vm_recipe_deploy:
        name: "web-01"
        recipe: "Ubuntu Server 22.04 (Jammy Jellyfish)"
        answers:
          HOSTNAME: "web-01"
          USER: "ops"
          PASSWORD: "{{ vault_guest_password }}"
          YB_CPU_CORES: 2
          YB_RAM: 4096                   # MB
          YB_DRIVE_OS_SIZE: 53687091200  # BYTES. This is 50 GB.
          YB_IP_ADDR_TYPE: dhcp
          YB_NIC_ETH0: "External"
          SELECT_OS_TIER: >-
            {{ recipe.options.SELECT_OS_TIER | map(attribute='$key') | first }}
```

Connection details fall back to `VERGEOS_HOST`, `VERGEOS_USERNAME`,
`VERGEOS_PASSWORD` and `VERGEOS_INSECURE`, so they are left out above.

If that is all you needed, the rest of this document is about why the tier
answer is written the way it is, and what happens after the module returns.

## Step one: ask before you answer

Question names look like `YB_RAM`, `SELECT_OS_TIER` and `YB_NIC_ETH0`. You are
not going to guess them, and transcribing them out of the UI is how typos get
into playbooks. Ask the system instead:

```yaml
- vergeio.vergeos.vm_recipe_info:
    name: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    questions: true
    resolve_options: true
  register: recipe
```

`questions: true` returns the question set. `resolve_options: true` is the
flag that matters more than it sounds. Some questions are backed by a table on
the system rather than a fixed list of choices, and without this you get told
the name of the table. With it, you get the values that are genuinely valid on
the system you are pointed at.

`examples/recipe_discover.yml` is a runnable version of this that sorts the
questions into the three groups worth thinking about:

* **Required.** The deploy refuses without them. For the stock Linux recipes
  that is `HOSTNAME`, `USER` and `PASSWORD`.
* **Optional with an empty default.** These are the interesting ones. Nothing
  forces you to answer them, and for several of them an empty value is fine.
  For others an empty value fails partway through building the VM, which is a
  much worse place to find out. The deploy module reports these as `hints`.
* **Optional with a real default.** Mostly safe to leave alone. Mostly. See
  the network trap below.

## Step two: preflight

Check mode on `vm_recipe_deploy` is not a guess. It validates your answers
locally, then asks the platform to simulate the entire deploy, which renders
cloud init and walks every step the real thing would take, creating nothing.

```bash
ansible-playbook examples/recipe_preflight.yml
```

That example exits non zero when the answer set would not deploy, so it drops
straight into CI or into the morning of a change window. It is worth running
against every system you intend to deploy to, not just one, because some
answers are only valid on one system. Which brings us to the traps.

## The traps

### Storage tier numbering is per system

`SELECT_OS_TIER` is optional and its default is empty, and an empty value
makes the recipe's own OS drive step fail. So it has to be answered.

The mistake is answering it with a number you wrote down somewhere. Tier
numbering is a property of the system, not of the recipe. A system with a
single tier is completely normal, and a playbook carrying `SELECT_OS_TIER: 4`
fails on every one of those with:

```
answer 'SELECT_OS_TIER' is not a valid choice, valid: 1=1
```

The fix is to read the tier at run time from the lookup you already did:

```yaml
SELECT_OS_TIER: >-
  {{ recipe.options.SELECT_OS_TIER | map(attribute='$key') | first }}
```

Now the same playbook runs against a one tier lab and a four tier production
system without edits. Every example in this collection does it this way.

### Network questions default to building a new network

`YB_NIC_ETH0` has a default, so it looks like one of the safe ones. Its
default is the literal `__new_internal__`, which means "create a brand new
internal network for this VM".

The deploy accepts that quite happily. What you get is a VM sitting on a
network nothing else is on, and every other check passes: the VM exists, it
has drives, it boots. It just cannot talk to anything. On a fleet it is worse,
because each VM gets its own new network and they cannot reach each other
either.

Answer it with the name of a network you meant:

```yaml
YB_NIC_ETH0: "External"
```

The `vm_nic_info` module exists partly for this. It can report NICs attached
to no network at all, which is the shape this failure takes.

### The deploy is asynchronous

`vm_recipe_deploy` returns once the platform has accepted the deploy and told
it the key of the VM being built. At that moment the VM row exists and the OS
image is still downloading. Every stock cloud image recipe builds its OS drive
with `media=import`, so there is a window, often several minutes long, where
the VM is present and cannot boot.

If the next task in your playbook is going to SSH to it, configure it, or add
it to a load balancer, the module on its own is not enough. Use the
`vm_from_recipe` role, which waits for the import, checks that what got built
is complete, powers the VM on and then proves the guest booted by watching for
guest disk writes.

```yaml
roles:
  - role: vergeio.vergeos.vm_from_recipe
    vars:
      vm_from_recipe_name: "web-01"
      vm_from_recipe_recipe: "Ubuntu Server 22.04 (Jammy Jellyfish)"
      vm_from_recipe_answers: { ... }
      vm_from_recipe_power_on: true
      vm_from_recipe_verify_boot: true
```

Boot proof watches writes rather than reads on purpose. A VM that never boots
still has its firmware read the boot sector and send a few DHCP packets, so
read counters and NIC counters both move off zero on a guest sitting at "no
bootable device". Only written bytes separate the two.

### Your VM name may appear as asterisks

The `answers` parameter is marked `no_log`, because recipe answers routinely
carry a guest password. A side effect catches everyone once: Ansible scrubs
every value in that mapping from the task output, so a non secret answer that
happens to equal another string in the result gets masked too.

In practice this bites `HOSTNAME`, which is usually the same string as the VM
name, so the VM name shows up as `********` in that task's output. That is
Ansible protecting the mapping, not an error. The module's own messages
identify the VM by key for exactly this reason. Use `vm_key`, or read the name
back with a separate `vm_info` task.

### fail_on_hints asks more of you than it looks

A hint is an unanswered question whose default is empty. `fail_on_hints: true`
makes any hint fatal, which sounds like the responsible setting.

Against the stock recipes it is stricter than it appears. A plain Ubuntu
deploy reports six hints, and every one of them is for a question that is
perfectly fine left alone: `SSH_KEY`, `SELECT_CREATE_UEFI`, `YB_CLUSTER`, and
the gateway, nameserver and internal gateway questions when you are using
DHCP. Turn the option on without answering all six and you have converted a
working deploy into a failing one.

One of the six has a sharp edge. `YB_NIC_ETH0_INTERNAL_GATEWAY` is a network
type question, so answering it with an empty string is rejected:

```
network '' not found (answer 'YB_NIC_ETH0_INTERNAL_GATEWAY')
```

To satisfy the check you have to give it the name of a real network, even
though you are not creating an internal network and the value goes unused.

The practical advice: leave `fail_on_hints` off, run once, read the hints the
deploy reports, answer the ones that genuinely matter for your build, and only
then consider turning it on.

## Running it twice

`vm_recipe_deploy` uses the VM name as its idempotence key. A second run finds
the existing VM, reports `already_existed: true` and `changed: false`, and
builds nothing.

The role does the same, but it deliberately does not stop there. It re-runs
the drive, NIC and boot checks on the VM it found. That is on purpose.
Reporting "the VM already exists" as success without ever looking at the VM is
how a half failed earlier run gets laundered into a pass: the next run says
there is nothing to do, and the playbook goes on to configure something that
cannot boot.

## Building more than one

`examples/recipe_fleet.yml` shows the usual pattern, which is a loop over
`include_role` with a per VM entry carrying name, cores, RAM and disk size.
Two details in it are worth copying:

* Look the storage tier up **once**, before the loop, rather than once per VM.
* Preflight **every** VM before building any of them. Without that, a typo in
  the third entry is something you discover after two VMs are already on disk.

The loop is serial. The first deploy of a given distro also pulls that
distro's cloud image over the network, so budget several minutes for the first
VM and much less for the rest, since the image is on the system by then.

## Tearing down

There is deliberately no `state: absent` on `vm_recipe_deploy`, and none in
the role.

Deleting a recipe instance does not delete the VM it created. An `absent` that
removed the instance would look like a teardown in the playbook output while
quietly leaving the VM running and consuming resources. Removing a VM stays an
explicit act:

```yaml
- vergeio.vergeos.vm:
    name: "web-01"
    state: absent
```

## When something goes wrong

| What you see | What it means |
|---|---|
| `answer 'SELECT_OS_TIER' is not a valid choice` | A tier number was hardcoded and does not exist here. Read it from `resolve_options` instead |
| `missing required answer 'PASSWORD'` | A required question was left out. `recipe_discover.yml` lists them |
| `network 'x' not found` | A network answer named something that is not on this system. Check the name, including case |
| VM exists but will not boot | The image import may still be running. The role waits for this; the module does not |
| VM boots but reaches nothing | A network question was probably left at `__new_internal__`. Check with `vm_nic_info` |
| VM name shows as `********` | Expected. `answers` is `no_log`. Use `vm_key`, or read the name back with `vm_info` |
| `fail_on_hints is set and questions are unanswered` | Read the hints, answer the ones that matter, see the section above |

## Where to look next

* `examples/recipe_discover.yml` for what a system offers
* `examples/recipe_preflight.yml` for a CI gate
* `examples/deploy_from_recipe.yml` for one VM via the modules
* `examples/vm_from_recipe_role.yml` for one VM waited on and boot proved
* `examples/recipe_fleet.yml` for several at once
* `roles/vm_from_recipe/defaults/main.yml` for every role variable, each with
  the reasoning behind its default
