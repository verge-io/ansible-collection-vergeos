# node_drain

Evacuate a node into maintenance mode, or bring it back.

## Verification status — read this first

| Path | Status |
|---|---|
| Preflight (read-only) | **live-verified** on VergeOS 26.1.8, two nodes |
| Unknown-node refusal | **live-verified** |
| Drain execution | **implemented, not yet live-verified** |
| Restore | **implemented, not yet live-verified** |

The drain and restore paths are written, lint-clean and unit-tested, but have
not been run against a real cluster yet, because doing so puts a node into
maintenance mode. `tests/live/verify-node-drain.yml` exercises them. Run it
deliberately, attended, with console access to the cluster:

```bash
ansible-playbook tests/live/verify-node-drain.yml
```

Until that has passed, treat the drain half as unproven. Saying so is cheaper
than discovering it during an outage.

### The ladder counts machines, not VMs

The first version of that ladder refused to run when the target had running
VMs. That sounds like the same check and is not. Measured on the lab: one node
carried **nine** running machines while `vm_info` attributed **two** of them to
it. The other seven were vnets — Core, DMZ, External and the tenant fabrics —
and `network_info` reports no node for a vnet at all, so they were invisible
to a VM-shaped check.

On a system whose UI and API ride one of those vnets, draining that node takes
away the connection the playbook is running over. The ladder would hang, fail
somewhere in the middle, and leave the node in maintenance with nobody able to
reach the API to take it out again.

`node_info` now reports `running_machines` and `unmigratable_machines` per
node, and the ladder picks a node with zero machines on it rather than
trusting a name typed on the command line.

## Safe by default

The role is **report-only unless you say otherwise**. `node_drain_confirm`
defaults to `"no"`, so the default run produces a capacity verdict and the
list of VMs that would have to move, and stops.

`node_drain_confirm` must be the **string** `"yes"`. YAML reads an unquoted
`yes` as the boolean `true`, which is not that string — so written in a vars
file as `node_drain_confirm: yes`, every gated task would skip and the run
would report a clean preflight while the operator believed they had asked for
the drain. The role refuses the boolean rather than ignoring it. Quote it.

## The capacity gate

The platform will accept a drain it cannot finish. Measured on this lab:
draining a node with an 18 GB RAM shortfall was accepted, the node flipped to
maintenance in under a second, and the migration then sat at
`status: migrating` with `migration_destination: None` for **fourteen
minutes** — no error, no timeout, no progress. Neither the UI nor the native
action checks first.

So this role asks `cluster_info` for an N-1 verdict and refuses:

```
Refusing to drain 'node2': draining 'node2' leaves 68352 MB for 87040 MB of
committed VM RAM (deficit 18688 MB). The platform would accept this and then
stall indefinitely with no error.
```

`node_drain_force: true` proceeds anyway, and the message says exactly what
you are buying.

The arithmetic lives in `cluster_info`, against the platform's own
`cluster_status` table — not reimplemented in Jinja here.

**`fits: true` is not a promise.** It means the RAM is there. Issue #24
measured a tenant node that never placed while one host had 59 GB free, so the
platform's placement rule is stricter than free RAM and is not visible from
outside. A `false` is a fact worth refusing on; a `true` is a necessary
condition, and the reason string says so out loud. `fits: none` — could not be
determined — is rejected by the gate, because a gate whose job is certainty
must not treat silence as consent.

## Completion is not the maintenance flag

The platform sets `maintenance: true` as soon as the request is *accepted*.
Measured: under a second, while 32 GB of VMs were still resident and the node
read `status: migrating`. A role that trusted that flag would restart a node
with live workloads on it — precisely the failure it exists to prevent.

The signal this role waits on is **zero running machines on the node** — VMs
and vnets both, from `node_info`'s `running_machines`.

It used to wait on zero running *VMs*, which is a different and smaller
question. A vnet is a machine and migrates like one, and `vm_info` does not
report it — so on the measured node the VM count reached zero while seven
vnets were still resident, and the role would have called the drain complete.

## Usage

Preflight, the default:

```yaml
- role: vergeio.vergeos.node_drain
  vars:
    node_drain_node: node2
```

Actually drain:

```yaml
- role: vergeio.vergeos.node_drain
  vars:
    node_drain_node: node2
    node_drain_confirm: "yes"
```

Bring it back:

```yaml
- role: vergeio.vergeos.node_drain
  vars:
    node_drain_node: node2
    node_drain_state: active
```

Restore needs no consent gate and no capacity gate — it adds capacity rather
than removing it, and it is how you recover from a drain that went wrong.

## Results

Live, on **VergeOS 26.1.8, two nodes** (`ok=33 changed=0 failed=0`):

```
FITS: draining 'node2' leaves 68352 MB for 41984 MB of committed VM RAM (26368 MB headroom)
0 running VM(s) on 'node2': []
Preflight only. Set node_drain_confirm=yes to actually drain 'node2'.

FITS: draining 'node1' leaves 69120 MB for 41984 MB of committed VM RAM (27136 MB headroom)
2 running VM(s) on 'node1': ['dr-test', 'nas1']

unknown node refused
```

Neither node entered maintenance.

## Relationship to `rolling_update`

`rolling_update` patches a whole cluster and hands the reboot sequencing to
the platform's own rolling apply. `node_drain` evacuates **one** node and
leaves it drained — for hardware work, a physical move, or an investigation.
They share the same capacity gate and the same completion signal.

## The facts it sets

- `node_drain_result` — what this run actually did: `node`, `confirmed`,
  `drained` (or `restored` and `online` on the restore path), `fits`,
  `reason`, `running_vms_before`. `drained` is keyed on the wait having *run*,
  not on consent having been given — a role that reported `drained: true` from
  the variable that requested the drain would be reporting its own input back.
- `node_drain_check` — the capacity verdict for the target: `node`, `fits`,
  `reason`, `deficit_mb`, `survivor_ram_mb`, `used_ram_mb`.
- `node_drain_nodes` — the `node_info` result for the whole cluster.

All three are reset at the start of every run. `set_fact` facts outlive the
role, so two `include_role` calls in one play share them, and the second node
would otherwise inherit the first one's verdict.
