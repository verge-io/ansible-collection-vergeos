# vm_from_recipe

Deploy a VM from a VergeOS recipe and wait for it to be **usable**, not merely
present.

The deploy itself is one module call — `vergeio.vergeos.vm_recipe_deploy`. Use
that module directly if you only want to fire the deploy. This role exists for
everything that follows it, because the deploy is asynchronous: the platform
accepts it and hands back a VM key long before the VM can boot. Every stock
cloud-image recipe builds its OS drive with `media=import`, and the VM row
appears while that image is **still downloading**.

## What it does

1. Deploys the recipe (answers validated locally, then simulated server-side).
2. Waits for the VM row to appear, and checks it is the VM the deploy created.
3. Waits for drive imports to finish — in two phases, with separate budgets.
4. Asserts the VM has drives, none importing, and every NIC attached.
5. Optionally powers the VM on and waits for it to actually be running.
6. Optionally proves the guest **boots**, by watching for guest disk writes.

Steps 4–6 run whether this run deployed the VM or converged on one that was
already there. Convergence gets them because "the VM already exists" reported
as success without looking at the VM is how a half-failed earlier run gets
laundered into a pass — the next run says "nothing to do", and the caller goes
on to configure something that cannot boot.

## Usage

```yaml
- name: Deploy a web server from a recipe
  hosts: localhost
  gather_facts: false
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
          YB_RAM: 4096
          YB_NIC_ETH0: "External"
          SELECT_OS_TIER: 4
        vm_from_recipe_power_on: true
        vm_from_recipe_verify_boot: true
```

Connection details default to `VERGEOS_HOST` / `VERGEOS_USERNAME` /
`VERGEOS_PASSWORD` / `VERGEOS_INSECURE`.

Discover what a recipe accepts with `vergeio.vergeos.vm_recipe_info` rather
than transcribing it from the UI:

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
server-side simulation, creating nothing — so a check run is a genuine
preflight rather than a guess. The role then skips everything past the deploy,
because there is no VM to wait for.

That gate is the VM key the module reports, not `ansible_check_mode`. The two
differ when a *caller* wraps this role in a `check_mode: true` block:
`ansible_check_mode` reads false there, so a role gated on it would go on to
poll for a VM that was never going to exist.

## Things worth knowing

**Answer network questions explicitly.** Defaults are not sent, on the grounds
that the platform applies them. For network questions it demonstrably does
not: a real deploy that left one unanswered produced a VM whose `eth0` was
attached to nothing, and every check except the NIC assertion passed on it.

**`vm_from_recipe_power_on` is opt-in for a reason beyond taste.** Power-on
goes through the `vergeio.vergeos.vm` module, whose `enabled` parameter
defaults to `true`, so on the convergence path it would re-enable a VM someone
deliberately disabled. A VM this role just deployed is already enabled and
merely stopped, so there is nothing to clobber there.

**A boot proof needs writes, not reads.** On a VM that never boots the
firmware still reads the boot sector and sends a handful of DHCP packets, so
read counters and NIC transmit counters both move off zero on a guest sitting
at "no bootable device". Only `write_bytes` separates the two cleanly. The
`vergeio.vergeos.vm_drive_info` module documentation carries the measurements.

**Failed polls are not the verdict.** Every wait in this role is
`failed_when: false`, with an assertion after it. That is so a stuck import is
reported as the drive's name and the platform's own download percentage, rather
than as a bare attempt count.

## Not implemented

There is no `state: absent`. Deleting a recipe instance does not delete the VM
it created, so an "absent" that removed the instance would look like a teardown
while leaving the VM running. Removing a VM stays an explicit act — use
`vergeio.vergeos.vm` with `state: absent`.
