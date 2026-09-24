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
  proposed for a move. If that tag cannot be *read*, the advisor reports the
  hotspot and withholds every proposal — see below.
- **Anti-affinity HA groups** — a group without a `+` prefix means "spread",
  so a target that already runs a sibling is not offered.
- **Maintenance mode** — a node being drained is not a destination.
- **The move budget** (`rebalance_advisor_max_moves`, default 2) — remediation,
  not a cluster-wide reshuffle.
- **Target headroom across proposals** — two moves cannot both be sent to the
  same node on the assumption it is still empty.

## When it refuses to answer

`no-balance` is how an operator says *never propose moving this*. The lookup
that answers it used to be wrapped in `except Exception: pass`, with the
comment "tags are an opt-out convenience, never fatal". It is not a
convenience — it is a safety interlock, and swallowing its failure made the
advisor fail open, silently, in the only direction that matters: a pinned VM
came back as movable and was named in a proposal with full confidence.

Now a failed tag lookup produces no proposals at all, and a note saying why.
The hotspot is still reported — withholding the proposal is not the same as
saying nothing.

A system with *no* `no-balance` tag is not this case. "Nothing is pinned" is a
real answer, and the advisor proceeds normally.

Losing per-VM CPU (`machine_stats`) is the lesser case and only makes a
proposal less informative, so the run continues — but it says so, because a
reason that never mentions VM CPU is otherwise indistinguishable from a VM
that is idle.

## Capturing a fixture from a real cluster

```yaml
- ansible.builtin.include_role:
    name: vergeio.vergeos.rebalance_advisor
    tasks_from: capture
```

Sets `rebalance_advisor_state` to `{nodes, vms}` — exactly what
`rebalance_advisor_fixture` reads back. Save it and you have a replayable
snapshot of a cluster at the moment it was misbehaving.

That exists because a hand-written fixture agrees with the code by
construction. This collection has shipped three tests whose fixtures agreed
with the module and disagreed with the platform; a fixture the collector
itself produced cannot.

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
without a hotspot to hand — and `collect()` is tested against rows shaped the
way 26.1.8 really returns them, because a collector nobody compares to the
planner's input is this collection's recurring defect waiting to happen.

- **42 unit tests** — `tests/unit/roles/test_rebalance_advisor_planner.py`
- **Live ladder** — `tests/live/verify-rebalance-advisor.yml`, passed on
  **VergeOS 26.1.8, two nodes** (`ok=58 changed=2 failed=0`): live run →
  every proposal carries a reason and a destination → **the live collector
  produces every field the planner reads, in the units it expects** → its own
  capture replays to the same answer → a hotspot fixture proposes the
  smallest resolving move → the budget caps the count → check mode reports
  instead of crashing → a failure arrives with its reason rather than the
  word `censored`.

### Three things it got wrong before that ladder existed

**Both proposals went to the same node.** Target headroom was tracked on
copies while the target *ordering* was computed from the originals, so with
two equal cold nodes the second proposal was sent to the node the first one
had just filled, while the other sat empty. It still fit, so nothing that
only checked headroom could see it.

**`--check` aborted with a stack trace** (#28). The advisor only reads, and a
reporting role is exactly when someone reaches for check mode;
`changed_when: false` is a claim about the report, not permission to run.

**A failure arrived as the word "censored".** Credentials go in the task's
environment, so the task carries `no_log: true`, and `no_log` censors the
*entire* result — the operator got an exit code and nothing else. `advisor.py`
now writes one scrubbed line to stderr and the role re-surfaces it.

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

`nodes` carries the sampled state the decisions were made from. The planner
never edits it — the report would otherwise show a cluster that never
existed.
