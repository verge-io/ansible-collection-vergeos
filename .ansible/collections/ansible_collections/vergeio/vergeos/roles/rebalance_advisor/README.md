# rebalance_advisor

Finds sustained CPU/RAM hotspots and proposes the smallest set of live
migrations that would resolve them, with a written reason for each.

## Recommend-only, and that is the design

The role **never migrates anything**. A live migration is the one operation in
this collection whose blast radius is a running workload, so applying a
proposal stays a human action — through the UI, `vrg`, or a deliberate
follow-up task.

This is honestly an *advisor*, not DRS. It reacts to a hotspot that is already
there; it does not continuously optimise placement.

## What it respects

- **`no-balance` tag** — a VM carrying it, in any tag category, is never
  proposed for a move.
- **Anti-affinity HA groups** — a group without a `+` prefix means "spread",
  so a target that already runs a sibling is not offered.
- **Maintenance mode** — a node being drained is not a destination.
- **The move budget** (`rebalance_advisor_max_moves`, default 2) — remediation,
  not a cluster-wide reshuffle.
- **Target headroom across proposals** — two moves cannot both be sent to the
  same node on the assumption it is still empty.

## Sampling

CPU is sampled `rebalance_advisor_samples` times (default 3) at
`rebalance_advisor_interval` seconds (default 5) and averaged. One sample
catches a spike and proposes a move that is not warranted; averaging is what
makes "sustained" mean something.

## Usage

```yaml
- hosts: localhost
  roles:
    - role: vergeio.vergeos.rebalance_advisor
```

Every proposal is printed with its reason. Read them before acting on any of
them — a proposal a human cannot evaluate is one they will either
rubber-stamp or ignore, and both are worse than no advisor.

## Results

`plan()` is a pure function of `{nodes, vms}`, which is what makes it testable
without a hotspot to hand:

- **16 planner unit tests** — `tests/unit/roles/test_rebalance_advisor_planner.py`
- **Live ladder** — `tests/live/verify-rebalance-advisor.yml`, passed on
  **VergeOS 26.1.8, two nodes** (`ok=25 changed=0 failed=0`): live run →
  every proposal carries a reason and a destination → a fixture with a known
  hotspot produces a resolving move → the move budget caps the count.

On the lab the live run reported, correctly:

```
mode=recommend-only hotspots=0 proposals=0
notes=no node above thresholds — nothing to do
```

## The fact it sets

`rebalance_advisor_result`:

```yaml
mode: recommend-only
hotspots: []              # nodes above threshold
proposals:                # smallest resolving set, budget-capped
  - vm: app-01
    to_node: node2
    reason: "node1 CPU 91% sustained; app-01 is 22% of it; node2 at 31%"
notes: []                 # why nothing was proposed, when nothing was
```
