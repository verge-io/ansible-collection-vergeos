# restore_drill

Proves a snapshot actually restores: snapshot → clone → boot → stability
window → verdict → teardown.

This is the half of a backup strategy people skip. **A backup you have never
restored is a hypothesis.** `vm_backup` produces the backups; this one is the
evidence they work.

## What the verdict actually means

The drill passes when **the clone boots and stays running for N seconds**.
That is a real test — an OS-less VM, a corrupt boot disk, or a guest that
panics on start all fail it — but it is not an application health check. If
you need "the database came up and accepted a query", layer a guest check on
top where you have the tooling. The role does not claim more than it measures.

## The source VM is never touched

Two safeguards:

- Everything the drill creates is a **clone under a different name**; the
  source is only ever read and snapshotted.
- The role **refuses outright** if the clone name resolves to the source
  name. A drill must never write to the thing it is verifying.

The ladder asserts the source is still present and still stopped afterwards.

## Cleanup happens even when the drill fails

Everything that creates something is inside a `block:`, with teardown in
`always:`. This matters more than it looks: the case this role exists to
detect is *the clone did not boot*, which is exactly the case where an
earlier version would have bailed out and left a half-booted clone behind.

## Static IPs, and an isolation list that isolates

A VM with a static in-guest IP will collide with its own original if you boot
a clone of it on the same network. List those networks in
`restore_drill_isolate_networks` and the clone's NICs on them are disabled
before boot.

**Every name in that list must match a NIC the clone actually has, or the
role refuses to boot.** That refusal is the point. The `nic` module creates a
NIC when the VM has none on that network, so looping the list straight into
it did the opposite of isolating — it attached the clone to a network the
original never had — and a network name that was stale, or capitalised
differently, isolated nothing at all while every task reported success. An
isolation list that matches nothing is precisely the condition under which
the collision this option exists to prevent actually happens.

The role also **reads the NICs back** afterwards and asserts they are
disabled, rather than trusting `changed`.

## A verdict that is not inherited

`restore_drill_passed` is set with `set_fact`, and facts outlive the role.
Two drills in one play share them, so a drill that fails after a drill that
passed would have reported the earlier verdict. Measured: without the reset
the role now does first, the ladder's failing drill came back `true`.

## Usage

```yaml
- role: vergeio.vergeos.restore_drill
  vars:
    restore_drill_vm: app-server
    restore_drill_boot_seconds: 120
    restore_drill_isolate_networks: [Production]
```

To test what your schedule is actually producing, rather than a snapshot
taken seconds ago:

```yaml
    restore_drill_take_snapshot: false     # drill the newest existing one
```

That is the more honest drill, and worth running periodically even if the
default is the convenient one.

## Results

**Live ladder** — `tests/live/verify-restore-drill.yml`, passed on **VergeOS
26.1.8, two nodes** (`ok=92 changed=19 failed=0`):

an empty VM name refused → a clone name equal to the source refused → a real
drill snapshots, clones, boots, holds the window and passes → the clone is
removed → the source is still present and still stopped → the drill is
repeatable → **a drill that should fail does, with a verdict that is false
rather than inherited, and leaves no clone** → an isolation list that matches
no NIC is refused before the boot → zero leftovers.

That failure rung is the one the ported ladder promised in its header and did
not have. Every other rung proves the role can say yes. A restore drill that
can only say yes is worse than no drill, because it converts "we have never
tested a restore" into "our restores are tested".

The ladder builds its own scratch source VM, so it never drills a real one.

## The fact it sets

`restore_drill_passed` — boolean. The role also fails the play on a
failed drill, so callers can either branch on the fact or just let it raise.
