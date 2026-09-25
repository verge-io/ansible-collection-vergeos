# pyvergeos API gaps

What a live platform index exposed that pyvergeos 1.2.3 did not reach, and
which of those rows this collection still reads by hand.

## Measurement

**2026/09/09, VergeOS 26.1.8, pyvergeos 1.2.3.** `GET /api/v4` returned the
platform's own index: **780 entries (322 tables, 437 indexes, 21 views)**.
pyvergeos 1.2.3 referenced **191 endpoints**, 187 of which are real platform
tables. Diff: **135 tables unreachable**, of which **97 carry signal** once
stats history, theming, help, console, and schema version noise is dropped.

The 97 names are `docs/pyvergeos-missing-signal.txt`. That file is the
1.2.3 diff. It is not a diff against pyvergeos 1.6.1.

Row counts below are from that system. `rows=0` usually means nothing of
that kind was configured there, or the endpoint is a command, not "the
table is empty."

The collection floor is now `pyvergeos>=1.2.8`. PyPI resolves that pin to
**1.6.1**. See `docs/SDK-COMPATIBILITY.md`. This page was not diffed again
against 1.6.1. Where this repository already records a change, the section
says so. Everything else stays as the 2026/09/09 measurement.

## Modelled on 1.6.1, still read with `_request`

These two were the P0 rows in the 1.2.3 diff. Both have public managers on
the current floor. The collection does not call the managers.

### `cluster_status`

pyvergeos 1.5.0 models `client.cluster_status` and the scoped
`cluster.cluster_status` accessor (pyVergeOS#127). 1.6.1 includes them.

`fetch_cluster_status()` in `plugins/module_utils/clusters.py` still does
`client._request('GET', 'cluster_status', params={'fields': 'all'})`.
`ClusterStatus.can_lose_one_node()` is not a substitute for the drain check:
it divides `online_ram` evenly across `online_nodes`, so on uneven nodes a
true result is not a guarantee that the cluster can lose one node (pyvergeos
1.6.1, #135). The drain check uses each node's `vm_ram`.

`cluster_info`, `site_info`, and `tenant` read capacity through
`fetch_cluster_status()`.

The 2026/09/09 measurement, kept because it is what the drain arithmetic
was checked against:

```
online_nodes=2   online_ram=137472 MB   used_ram=87040 MB
online_cores=64  used_cores=31          running_machines=7
total_nodes=2    total_ram=137472       state='online'
```

```
per node RAM       = 137472 / 2 = 68736 MB
N minus 1 capacity = 137472 minus 68736 = 68736 MB
need               = 87040 MB
verdict            = DOES NOT FIT, deficit 18304 MB
```

That even split is the arithmetic `can_lose_one_node()` uses. It is why
the collection does not call it.

### `machine_drive_stats`

pyvergeos 1.5.0 models `client.machine_drive_stats` and
`drive.drive_stats` (pyVergeOS#128). 1.6.1 includes them.

`drive_write_stats()` in `plugins/module_utils/machine.py` still GETs the
table with `client._request()` and a `parent_drive eq <key>` filter. The
stats row must be addressed by that filter, never by path.
`machine_drive_stats/<n>` resolves to the row whose own `$key` is `n`, and
that row can belong to a different drive. Measured on 26.1.8: `$key` 34
carried `parent_drive` 39. A counter read by path reports another VM's IO
and reports success. The same aliasing applies to
`machine_nic_stats` / `parent_nic`.

The comment in `machine.py` still names `pyvergeos>=1.2.7` as the reason
the call was not switched. The floor is now `>=1.2.8`, and 1.6.1 has the
manager. The reason that remains is the filter. A later switch has to keep
it.

Used by `vm_drive_info` and the `vm_from_recipe` boot proof.

## P1. Listed as unreachable from pyvergeos 1.2.3

Not referenced by name anywhere under `plugins/`. Not checked again against
1.6.1.

### `snapshot_profile_period_tags` + `tag_references`

This is the mechanism behind partial snapshots. `include_tags`,
`exclude_tags`, and `quiesce_tags` live here, per snapshot profile
**period**, with the object linkage through `tag_references`. Measured:

```
snapshot_profile_period_tags : rows=26   keys=['$key','snapshot_profile_period','refs']
tag_references               : rows=0    (nothing tagged on this system)
```

`snapshot_profile` in this collection does not pass tag parameters.
`cloud_snapshots.create()` had no tag parameters in the 1.2.3 SDK.

### `cloud_snapshot_actions`, `cloud_restore`, `cloud_snapshot_files`

`rows=0` on that system, which means command endpoints. System snapshot
restore, file level recovery from a snapshot, and making remote snapshots
immutable run through these. None of them is called by this collection.

### `schedule_tasks`, `schedule_task_settings`, `task_settings`, `task_types`

Measured `schedule_tasks: rows=3`, with `day_of_month`, `enabled`,
`end_date`, `event`, `delete_after_run`. pyvergeos 1.2.3 modelled `tasks`
/ `task_schedules` and not these. No role in this tree registers itself
with the native scheduler.

### `tenant_status`, `tenant_node_actions`, `tenant_recipe_actions`

Tenant power and scale operations, and per tenant health. `tenant` and
`tenant_info` are in the tree. They do not call these three tables.

## P2. Same measurement, nothing in this tree calls them

| Table(s) | What it would unlock |
|---|---|
| `vnet_bonds`, `vnet_bond_interfaces`, `vnet_wires` | VLAN bond config |
| `vnet_rule_aliases`, `vnet_rule_references` | firewall aliases, and what references a rule |
| `repair_servers` | the repair server half of backup/DR |
| `vmware_containers` + sibling tables | VMware Services containers and their backup/restore jobs |
| `volume_status`, `volume_share_status`, `volume_sync_progresses`, `volume_logs` | NAS health and sync progress |
| `storage_tier_stats` | tier capacity trending |
| `machine_device_settings_{gpu,host_gpu,nvidia_vgpu,sriov_nic,usb,tpm}` | device passthrough |
| `node_allocated_gpus`, `node_device_instances`, `resource_group_settings_*` | GPU/vGPU allocation |
| `smtp_settings`, `smtp_outbox`, `smtp_queue`, `smtp_actions` | alert delivery and its queue |
| `user_settings`, `user_devices`, `user_actions`, `group_logs` | user admin beyond create/delete; `user_devices` for MFA |
| `machine_drive_status`, `machine_nic_ipv4_configs` | per drive status, per NIC IPv4 |
| `alarm_types`, `task_event_types`, `task_logs`, `site_logs` | classifying alarms rather than matching strings |
| `subscriptions`, `subscription_profiles` | licensing state |
| `system`, `system_actions`, `settings_actions` | the singular `system` table and its actions (`client.system` is a different object) |
| `service_containers`, `service_container_storage`, `service_container_actions` | service containers |

## Not gaps in the 1.2.3 SDK, and worth keeping

- **`nodes/{key}/maintenance_reboot`.** `nodes.restart()` POSTs here. The
  SDK docstring describes it as migrating workloads and then restarting.
  `rolling_update` hands the reboot to the platform's rolling apply rather
  than doing drain, wait, and restart by hand.
- **`update_sources` actions** accept `refresh, download, install, apply,
  all`, and `update_settings.update_all(force=)` is download, install, and
  reboot.
- `vm_recipes`, `recipe_questions`, `recipe_sections`, and
  `vm_recipe_instances` are reachable. The simulate response is a separate
  problem, below.

## SDK defects that are not missing endpoints

These were the most valuable items on the 2026/09/09 page: the endpoint
existed and behaved incorrectly. Two are fixed on the current floor. One
is worked around in this collection.

### Member references (the old B18 note)

`GroupMember.member_type` and `member_key` tested `"/users/" in ref`. The
platform stores the member reference verbatim as whichever form wrote it,
and both forms were live in the same column:

```
client.groups.members(1).list()   :  member = 'users/1'      (created by the platform)
client.groups.members(2).list()   :  member = '/v4/users/2'  (SDK add_user)
```

`'/users/' in 'users/1'` is false, so a membership created by the platform,
including the default Administrators group, was reported as
`member_type='Unknown'` and `member_key=None`. `member_name` was
unaffected; it reads `member_display`.

**In this collection.** Membership decisions do not use those two
properties. `plugins/module_utils/rbac.py` `split_member_ref()` takes the
last two path segments, which accepts both `users/N` and `/v4/users/N`.
The `member` module uses that parse (issue #92). This page does not
test `GroupMember.member_type` again on 1.6.1.

### OData quoting (the old B1 note)

pyvergeos doubled `'` SQL style. Measured: `name eq 'zz-o''brien'` returned
HTTP 422 `Invalid argument`; the backslash form returned HTTP 200.

**On this floor.** pyvergeos 1.2.5 switched to `quote_value()`, which emits
the backslash form. 1.2.8 escapes `{` as well (pyVergeOS#100). 1.6.1
contains both. Name lookup in this collection stays on the client because
VergeOS does not enforce unique names on the tables we look up (issue
#72), not because quoting is still wrong. `resolve_one()` in
`plugins/module_utils/vergeos.py` and `docs/SDK-COMPATIBILITY.md` record
that.

### Bodies outside 2xx (the old B9 note)

`client._request` dispatches on status code and keeps the error as a
string. Recipe simulate answers **HTTP 405** with a log document. Going
through `_request` drops the document.

**In this collection.** `post_instance()` in
`plugins/module_utils/vm_recipes.py` uses `raw_request()` and returns
`(status_code, document)`. Three constraints are still true there:

1. pyvergeos `create()` does not send `simulate`.
2. `create()` discards the POST body, which is where `response.vm` reports
   the new VM's key.
3. Simulate replies on a status that is not 2xx, so `_request` throws the
   document away.

When a released pyvergeos grows a `simulate` argument that returns the raw
document, `post_instance()` is the call site that changes.

## Summary

| Item | Against pyvergeos 1.2.3 (2026/09/09) | On this tree, floor 1.6.1 |
|---|---|---|
| `cluster_status` | not modelled | modelled; collection still uses `_request` because `can_lose_one_node()` is an even split |
| `machine_drive_stats` | not modelled | modelled; collection still filters `parent_drive`, because access by path key addresses `$key` |
| P1 tables (~12) | unreachable | not called by this collection; not diffed again |
| P2 tables | unreachable | not called by this collection; not diffed again |
| OData `'` and `{` | `'` broken; `{` broken until 1.2.8 | fixed on the floor; lookups stay on the client for ambiguous names (#72) |
| Member `member_type` / `member_key` | wrong on bare `users/N` | collection parses the ref itself (#92); SDK property not tested again here |
| Simulate body on HTTP 405 | discarded by `_request` | kept by `raw_request()` |

The list of 97 names is `docs/pyvergeos-missing-signal.txt`.
