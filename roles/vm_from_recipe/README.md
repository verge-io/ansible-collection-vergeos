# vm_from_recipe

Deploy a VM from a VergeOS recipe and wait until it is genuinely **usable**,
not merely present.

The deploy itself is a single module call, `vergeio.vergeos.vm_recipe_deploy`.
If all you want is to fire the deploy and move on, use that module directly.
This role exists for everything that has to happen afterwards, because the
deploy is asynchronous. The platform accepts it and hands back a VM key long
before that VM can boot. Every stock cloud image recipe builds its OS drive
with `media=import`, so the VM row shows up while the image is still
downloading.

## What it does

1. Deploys the recipe. Answers are validated locally first, then simulated
   server side.
2. Waits for the VM row to appear, and confirms it is the VM this deploy
   created rather than something that happened to share the name.
3. Waits for the drive imports to finish. This happens in two phases with
   separate time budgets.
4. Asserts the VM has drives, that none are still importing, and that every
   NIC is attached to something.
5. Optionally powers the VM on and waits until it really is running.
6. Optionally proves the guest **booted**, by watching for guest disk writes.

Steps 4 through 6 run whether this particular run deployed the VM or simply
found one already there. Convergence gets the same treatment on purpose.
Reporting "the VM already exists" as success without ever looking at the VM
is how a half failed earlier run gets laundered into a pass: the next run
says there is nothing to do, and the caller goes on to configure something
that cannot boot.

## Usage

```yaml
- name: Deploy a web server from a recipe
  hosts: localhost
  gather_facts: false

  pre_tasks:
    # Storage tier numbering is per system, so discover it rather than
    # writing a number in. See the note on SELECT_OS_TIER below.
    - name: Ask the recipe which tiers exist here
      vergeio.vergeos.vm_recipe_info:
        name: "Ubuntu Server 22.04 (Jammy Jellyfish)"
        questions: true
        resolve_options: true
      register: recipe

  roles:
    - role: vergeio.vergeos.vm_from_recipe
      vars:
        vm_from_recipe_name: "web-01"
        vm_from_recipe_recipe: "Ubuntu Server 22.04 (Jammy Jellyfish)"
        vm_from_recipe_answers:
          HOSTNAME: "web-01"
          USER: "ops"
          PASSWORD: "{{ vault_guest_password }}"
          YB_CPU_CORES: 2
          YB_RAM: 4096                   # MB
          YB_DRIVE_OS_SIZE: 53687091200  # BYTES, not GB. This is 50 GB.
          YB_IP_ADDR_TYPE: dhcp
          YB_NIC_ETH0: "External"
          SELECT_OS_TIER: >-
            {{ recipe.options.SELECT_OS_TIER
               | map(attribute='$key') | first }}
        vm_from_recipe_power_on: true
        vm_from_recipe_verify_boot: true
```

Connection details default to `VERGEOS_HOST`, `VERGEOS_USERNAME`,
`VERGEOS_PASSWORD` and `VERGEOS_INSECURE`.

Discover what a recipe accepts with `vergeio.vergeos.vm_recipe_info` rather
than transcribing it out of the UI:

```yaml
- vergeio.vergeos.vm_recipe_info:
    name: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    questions: true
    resolve_options: true
  register: recipe
```

See [`defaults/main.yml`](defaults/main.yml) for every variable, each with the
reasoning behind its default.

## Check mode

Under `--check` the deploy module validates the answer set and runs the real
server side simulation, creating nothing. That makes a check run a genuine
preflight rather than a guess. The role then skips everything past the
deploy, because there is no VM to wait for.

The thing it gates on is the VM key the module reports, not
`ansible_check_mode`. Those two differ when a *caller* wraps this role in a
`check_mode: true` block. `ansible_check_mode` reads false in that situation,
so a role gated on it would go off and poll for a VM that was never going to
exist.

## Things worth knowing

**Answer the network questions explicitly.** Defaults are not sent, on the
grounds that the platform applies them itself. For network questions it
demonstrably does not. A real deploy that left one unanswered produced a VM
whose `eth0` was attached to nothing, and every check except the role's NIC
assertion passed on it.

**Do not hardcode `SELECT_OS_TIER`.** It is optional with an empty default,
and an empty value makes the recipe's own OS drive step fail partway through,
so it does have to be answered. But tier numbering is per system. A single
tier system is perfectly normal, and a playbook carrying `SELECT_OS_TIER: 4`
fails on one of those with `answer 'SELECT_OS_TIER' is not a valid choice`.
Read the tiers off `vm_recipe_info` with `resolve_options: true`, as the
usage example above does, and the same playbook runs anywhere.

**`vm_from_recipe_power_on` is opt in for a reason beyond taste.** Power on
goes through the `vergeio.vergeos.vm` module, whose `enabled` parameter
defaults to `true`, so on the convergence path it would re-enable a VM that
somebody had deliberately disabled. A VM this role has only just deployed is
already enabled and merely stopped, so there is nothing to clobber there.

**`vm_from_recipe_fail_on_hints` asks more of you than it looks.** A hint is
an unanswered question whose default is empty, and this option makes any hint
fatal. That is a useful gate in CI, but the stock recipes carry several
questions that are optional, have empty defaults and are entirely fine left
alone, `SSH_KEY` and `SELECT_CREATE_UEFI` among them. Switch this on without
answering all of them and you turn a working deploy into a failing one.

One of those questions has a sharp edge worth knowing about in advance.
`YB_NIC_ETH0_INTERNAL_GATEWAY` is a network type question, so an empty string
is rejected with `network '' not found`. To satisfy the check you have to
give it the name of a real network, even when you are not creating an
internal network and the value will go unused. The practical advice is to run
once with this off, read the hints the deploy reports, answer the ones that
actually matter to you, and only then turn it on.

**A boot proof needs writes, not reads.** On a VM that never boots the
firmware still reads the boot sector and sends a handful of DHCP packets, so
read counters and NIC transmit counters both move off zero even on a guest
sitting at "no bootable device". Only `write_bytes` separates the two
cleanly. The `vergeio.vergeos.vm_drive_info` module documentation carries the
measurements.

**Failed polls are not the verdict.** Every wait in this role runs with
`failed_when: false` and an assertion after it. That is deliberate, so a
stuck import gets reported as the drive's name and the platform's own
download percentage rather than as a bare attempt count.

## Not implemented

There is no `state: absent`. Deleting a recipe instance does not delete the VM
it created, so an "absent" that removed the instance would look like a
teardown while quietly leaving the VM running. Removing a VM stays an
explicit act: use `vergeio.vergeos.vm` with `state: absent`.
