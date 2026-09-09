# rolling_update

Prepare a VergeOS platform update, **prove the cluster can survive losing a
node**, then hand the reboots to the platform's own rolling apply.

## What this role does and does not do

It does **not** drain, wait, restart and undrain each node itself. The
platform already does exactly that:

- the `apply` update action reboots nodes one at a time, migrating workloads
  first — pyvergeos documents `update_all()` as *"Reboot nodes one at a time
  with workload migration"*;
- `nodes/{key}/maintenance_reboot` does the same for a single node —
  *"safely reboots the node by first migrating workloads and then restarting
  the system."*

An earlier version of this role reimplemented that sequence, and got it
wrong. It treated `maintenance: true` as "evacuation finished". The platform
sets that flag in under a second, while RAM is still resident — so the role
would restart a node whose workloads had not moved. Reimplementing a vendor
primitive means owning its race conditions.

What the platform does **not** do is refuse to start when there is nowhere for
the workloads to go. Measured on a two-node lab: draining a node with an
18 GB RAM shortfall was accepted, the node flipped to maintenance
immediately, and the migration then sat at `status: migrating` with
`migration_destination: None` for fourteen minutes.

So the role is a **pre-flight and a post-verify** around the platform's own
apply:

1. Run the system-wide lifecycle — check, download, install. Reboots nothing.
2. Ask, for each node due a reboot, whether the survivors can hold the
   cluster's committed VM RAM. **Refuse if not.**
3. Hand the roll to the platform.
4. Wait for it to finish, then verify the cluster came out the shape it went
   in.

## Usage

```bash
# Dry run: stages the update, reports capacity and what would reboot
ansible-playbook examples/rolling_update.yml

# For real
ansible-playbook examples/rolling_update.yml -e rolling_update_confirm=yes
```

## Things worth knowing

**Consent is a variable, not `--check`.** `rolling_update_confirm` must be the
literal string `yes` before anything reboots. A scheduler will not remember to
pass `--check`. Run it without consent and you get a full preflight — update
staged, capacity reported, plan printed — as the *default* path rather than a
mode you have to know to ask for.

**The capacity check uses `vm_ram`, not `ram`.** On a measured 26.1.8 node
those are 68352 MB and 94208 MB. `ram` is the physical stick count; `vm_ram`
is what the platform will actually let VMs have, and it is `vm_ram` that sums
to `cluster_status.online_ram`. Using the physical figure overstates capacity
by about a third — the difference between "will not fit" and "plenty of room".

**The whole of `used_ram` must fit on the survivors.** Draining moves
workloads, it does not stop them. Subtracting the drained node's share from
the requirement is the mistake that makes an impossible drain look possible.

**"Could not determine" does not pass the gate.** The capacity check returns
`fits: none` when it cannot tell — an unknown node name, for instance. The
assert rejects anything that is not explicitly `true`, because a gate whose
job is certainty must not treat silence as consent.

**A node already in maintenance is not counted as a survivor.** It is on its
way out itself, and workloads placed there would have to move again.

**`failover_ram` is reported separately.** It is the platform's own N-1
reservation. Every node on the measured lab reports `0`, which is why nothing
stopped the drain that stalled — no reservation means the cluster is sized for
all nodes being up. It is a standing configuration gap, so the role reports it
rather than failing on it.

**Waiting is separate from applying.** The apply action returns when the roll
is *accepted*, not when it completes. `rolling_update_wait` is what makes the
health gate meaningful; without it the gate would measure a cluster that has
not started rebooting yet.

**The health gate compares against a baseline captured before the run.** Not
against the live cluster afterwards, which would let a node that failed to
return quietly redefine "all online".

**Replication can gate the roll.** Set `rolling_update_max_replication_age`
and the run fails if site syncs fall behind while nodes cycle.

**`rolling_update_force`** lets nodes carrying unmigratable workloads — GPU
passthrough, for example — reboot those workloads rather than stall the roll.
It decides availability for those workloads, so it is never implied.

## Not yet verified live

The apply itself has **not** been exercised end to end. The lab reports
`installed: true, reboot_required: false`, so there is no pending update to
roll, and manufacturing one to watch it reboot was not worth the outage. What
*has* been verified against the lab:

- the capacity gate refusing a real 17920 MB / 18688 MB shortfall;
- the gate refusing an undeterminable node;
- the gate passing through to the consent stop when disabled;
- `state: applied` declining to reboot when `reboot_required` is false, in
  both normal and check mode.
