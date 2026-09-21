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

## Static IPs

A VM with a static in-guest IP will collide with its own original if you boot
a clone of it on the same network. List those networks in
`restore_drill_isolate_networks` and the clone's NICs on them are disabled
before boot.

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
26.1.8, two nodes** (`ok=55 changed=14 failed=0`):

an empty VM name refused → a clone name equal to the source refused → a real
drill snapshots, clones, boots, holds the window and passes → the clone is
removed → the source is still present and still stopped → the drill is
repeatable → zero leftovers.

The ladder builds its own scratch source VM, so it never drills a real one.

## The fact it sets

`restore_drill_passed` — boolean. The role also fails the play on a
failed drill, so callers can either branch on the fact or just let it raise.
