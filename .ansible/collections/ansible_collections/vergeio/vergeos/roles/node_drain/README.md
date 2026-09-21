# node_drain

Evacuate a node into maintenance mode, or bring it back.

## Verification status — read this first

| Path | Status |
|---|---|
| Preflight (read-only) | **live-verified** on VergeOS 26.1.8, two nodes |
| Unknown-node refusal | **live-verified** |
| Drain execution | **implemented, not yet live-verified** |
| Restore | **implemented, not yet live-verified** |

The drain and restore paths are written and lint-clean but have not been run
against a real cluster yet, because doing so puts a node into maintenance
mode. `tests/live/verify-node-drain.yml` exists and will exercise them; it
refuses to run unless the target node has **zero running VMs**, so it can
never migrate a live workload. Run it deliberately:

```bash
ansible-playbook tests/live/verify-node-drain.yml -e drain_target=node2
```

Until that has passed, treat the drain half as unproven. Saying so is cheaper
than discovering it during an outage.

## Safe by default

The role is **report-only unless you say otherwise**. `node_drain_confirm`
defaults to `"no"`, so the default run produces a capacity verdict and the
list of VMs that would have to move, and stops.

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

## Completion is not the maintenance flag

The platform sets `maintenance: true` as soon as the request is *accepted*.
Measured: under a second, while 32 GB of VMs were still resident and the node
read `status: migrating`. A role that trusted that flag would restart a node
with live workloads on it — precisely the failure it exists to prevent.

The signal this role waits on is **zero running VMs whose `node_name` is the
drained node**.

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

- `node_drain_check` — the N-1 verdict for the target: `node`, `fits`,
  `reason`, `deficit_mb`, `survivor_ram_mb`, `used_ram_mb`.
- `node_drain_nodes` — the `node_info` result for the whole cluster.
