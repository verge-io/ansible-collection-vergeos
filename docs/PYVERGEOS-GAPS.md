# pyvergeos API gaps

What the platform exposes that the SDK does not reach, and what that blocks.

**Method.** `GET /api/v4` on a live VergeOS 26.1.8 system (`conundrum-lab`)
returns the platform's own index: **780 entries — 322 tables, 437 indexes,
21 views**. pyvergeos 1.2.3 references **191 endpoints**, 187 of which are
real platform tables. Diff: **135 tables unreachable**, of which **97 carry
signal** once stats-history, theming, help, console and schema-version noise
is dropped.

Everything below was queried against the live system. Row counts are from
that system, so `rows=0` usually means "nothing of this kind configured here"
or "this is a command endpoint", not "empty table".

---

## P0 — blocking work already committed

### `cluster_status` — the N-1 capacity pre-check
Not modelled. `clusters.py` has a `status` **string** property; it does not
reach the `cluster_status` table, which is where the capacity numbers live.

Measured on `conundrum-lab`:

```
online_nodes=2   online_ram=137472 MB   used_ram=87040 MB
online_cores=64  used_cores=31          running_machines=7
total_nodes=2    total_ram=137472       state='online'
```

One call gives the whole N-1 answer:

```
per-node RAM   = 137472 / 2 = 68736 MB
N-1 capacity   = 137472 - 68736 = 68736 MB
need           = 87040 MB
verdict        = DOES NOT FIT — deficit 18304 MB
```

That deficit is exactly why the drain in bug P1 stalled. **A single
`cluster_status` read predicts it before you touch anything.** Reaching it
today needs `client._request('GET','cluster_status')`.

### `machine_drive_stats` — per-drive IO counters
Not exposed as a public manager (`machine_stats.py` models `machine_stats`,
not this). Already worked around in `module_utils/machine.py` via `_request`,
and it must be read by **filter on `parent_drive`**, never by path: 12 of 49
rows on this system have `$key != parent_drive`, including
`$key=34 -> parent_drive=39`.

Used by: `vm_drive_info` stats, the `vm_from_recipe` boot proof.

---

## P1 — needed for planned or deferred features

### `snapshot_profile_period_tags` + `tag_references` — tag-scoped snapshots (bug B7)
This is the mechanism behind 26.1's headline partial-snapshot feature, and it
is where the missing `include_tags` / `exclude_tags` / `quiesce_tags` actually
live. Measured:

```
snapshot_profile_period_tags : rows=26   keys=['$key','snapshot_profile_period','refs']
tag_references               : rows=0    (nothing tagged on this system)
```

So tags attach per snapshot-profile-**period**, not per snapshot profile, and
the object linkage goes through `tag_references`. Neither table is reachable
through pyvergeos, and `cloud_snapshots.create()` has no tag parameters.

### `cloud_snapshot_actions`, `cloud_restore`, `cloud_snapshot_files`
`rows=0` — command endpoints. System-snapshot restore, file-level recovery
from a snapshot, and making remote snapshots immutable all run through these.
26.1 added "recover Files and VMware Services from system snapshots" and
"remote snapshots can now be made immutable"; none of it is reachable.

### `schedule_tasks`, `schedule_task_settings`, `task_settings`, `task_types`
Measured `schedule_tasks: rows=3`, with `day_of_month`, `enabled`,
`end_date`, `event`, `delete_after_run`. The platform has its own scheduler
and this lab already uses it. pyvergeos models `tasks` / `task_schedules` but
not these, so a role cannot register itself with the native scheduler or read
what is already scheduled.

### `tenant_status`, `tenant_node_actions`, `tenant_recipe_actions`
Tenant power/scale operations and per-tenant health. Tenants are VergeOS's
differentiator; the read-side status and the imperative actions are both
missing.

---

## P2 — real capability gaps, nothing committed depends on them yet

| Table(s) | What it unlocks |
|---|---|
| `vnet_bonds`, `vnet_bond_interfaces`, `vnet_wires` | VLAN bond config — a named 26.1 improvement |
| `vnet_rule_aliases`, `vnet_rule_references` | firewall aliases and what references a rule (safe rule deletion) |
| `repair_servers` | the repair-server half of backup/DR |
| `vmware_containers` + 6 sibling tables | 26.1 VMware Services containers and their backup/restore jobs |
| `volume_status`, `volume_share_status`, `volume_sync_progresses`, `volume_logs` | NAS health and sync progress — `dr_replication` reports none of this today |
| `storage_tier_stats` | tier capacity trending; `tier_policy` currently infers |
| `machine_device_settings_{gpu,host_gpu,nvidia_vgpu,sriov_nic,usb,tpm}` | device passthrough as code |
| `node_allocated_gpus`, `node_device_instances`, `resource_group_settings_*` | GPU/vGPU allocation as code |
| `smtp_settings`, `smtp_outbox`, `smtp_queue`, `smtp_actions` | alert delivery and its queue |
| `user_settings`, `user_devices`, `user_actions`, `group_logs` | user admin beyond create/delete; `user_devices` matters for MFA |
| `machine_drive_status`, `machine_nic_ipv4_configs` | per-drive status, per-NIC IPv4 |
| `alarm_types`, `task_event_types`, `task_logs`, `site_logs` | classifying alarms rather than string-matching them |
| `subscriptions`, `subscription_profiles` | licensing/subscription state |
| `system`, `system_actions`, `settings_actions` | `client.system` exists but the singular `system` table and its actions do not |
| `service_containers`, `service_container_storage`, `service_container_actions` | service containers as code |

---

## Not gaps — SDK reaches these correctly, worth recording

- **`nodes/{key}/maintenance_reboot`.** `nodes.restart()` POSTs here, and the
  docstring is explicit: *"safely reboots the node by first migrating
  workloads and then restarting the system."* So drain-then-reboot is one
  supported platform operation. Any role that hand-rolls
  drain → wait → restart is reimplementing it, and bug B10 shows the
  reimplementation is worse.
- **`update_sources` actions** accept `refresh, download, install, apply, all`,
  and `update_settings.update_all(force=)` is download+install+**reboot**.
  The rolling reboot is a supported API call.
- `vm_recipes`, `recipe_questions`, `recipe_sections`, `vm_recipe_instances`
  all work; only the **simulate response** is unreachable, and that is an HTTP
  status-handling bug (B9), not a missing endpoint.

---

## Two SDK defects that are not missing endpoints

These need SDK changes even though the endpoints are covered:

1. **B1 — OData escaping.** pyvergeos doubles `'` SQL-style. Measured:
   `name eq 'zz-o''brien'` → **HTTP 422 `Invalid argument`**; backslash form →
   HTTP 200. Every name-based lookup in the SDK is affected, including the
   stock `vm_info` / `vm` / `network` modules.
2. **B9 — non-2xx responses discard the body.** `client.py` dispatches on
   status code and `_extract_error_message` keeps only a string. The recipe
   simulate returns **HTTP 405** with a 28-entry log document; `APIError`
   carries `status_code=405` and nothing else. Any endpoint that answers
   usefully on a non-2xx status is unreachable.

---

## Summary

| Priority | Tables | Blocks |
|---|---|---|
| P0 | 2 | capacity pre-check; boot proof (both worked around via `_request`) |
| P1 | ~12 | partial snapshots (B7), snapshot restore, native scheduling, tenant ops |
| P2 | ~83 | GPU/device-as-code, NAS health, bonds, VMware containers, alerting |
| noise | 38 | stats history, theming, console, help, schema versions |

**Nothing currently committed is hard-blocked by a missing endpoint.** Every
P0 item is reachable through `client._request()`, which is what
`module_utils/machine.py` and `vm_recipes.py` already do. The genuine
blockers are the two SDK *defects* (B1, B9) and the P1 set for work not yet
started.
