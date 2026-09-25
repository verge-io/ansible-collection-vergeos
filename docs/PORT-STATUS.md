# Port status

Where the `vergeos_platform` work stands on `dev`.

An earlier draft of this file (2026/09/11, commit `d78acfd` on branch
`platform-port`) described an unmerged branch, 18 additive modules, and
six decisions that had not been applied. That draft is not the status of
this tree. The sections below are the tree at `3e5adca` (2026/09/25),
which added `site_info` for a read only site preflight (#148).

`galaxy.yml` is still `2.1.0`. The port is in the unreleased changelog,
ahead of tag `v2.1.0` (`be79cde`). Issue #57 is the index that tracked
the port while it was off `dev`.

## What is in the tree

| | Count | Where |
|---|---|---|
| Modules | 47 | `plugins/modules/`, listed in the README |
| Roles | 17 | `roles/` |
| Live ladders | 43 | `tests/live/verify-*.yml` |

`site_info` (#20) is one of the 47. It is a read only preflight: platform
and OS version, cloud name, storage tiers, clusters and nodes, and counts
of networks, VM recipes, and NAS services.

`meta/runtime.yml` has one action group, `recipe`
(`vm_recipe_deploy`, `vm_recipe_info`, `vm_drive_info`, `vm_nic_info`).
There is no `all`, `info`, or `management` group covering the whole
collection. The 2026/09/11 draft described those three groups growing when
the 18 modules were registered. That registration is not in this file. The
runtime comment says an `all` group covering the whole collection was left
for the rest of the port.

### The 18 modules the draft listed as additive

All 18 are in `plugins/modules/`:

| Area | Modules |
|---|---|
| Tenancy | `tenant`, `tenant_info`, `tenant_network_block`, `tenant_external_ip` |
| Firewall | `vnet_rule`, `vnet_rule_info`, `vnet_apply` |
| Backup | `snapshot_profile`, `vm_export` |
| NAS | `nas_volume`, `nas_nfs_share` |
| Credentials | `api_key`, `api_key_info` |
| SSO | `auth_source`, `auth_source_info` |
| Content | `catalog`, `file` |
| VM | `vm_clone` |

Later than that draft, and also on `dev`: `group`, `group_info`,
`permission`, `node_info`, `node_maintenance`, `physical_drive_info`,
`update`, `update_info`, `vm_recipe_deploy`, `vm_recipe_info`,
`vm_drive_info`, `vm_nic_info`, `site_info`.

### The roles the draft listed as not started

The draft named 13 operational roles as not started, with
`vm_from_recipe` already present. All 13 are in `roles/`, plus
`rbac`, `drive_health`, and `rolling_update`:

`network_policy`, `api_key_rotation`, `vm_backup`, `image_pipeline`,
`rebalance_advisor`, `restore_drill`, `billing_export`, `protect`,
`node_drain`, `health_report`, `tier_policy`, `lb_stack`, `k3s_node`,
`rbac`, `drive_health`, `rolling_update`, `vm_from_recipe`.

## The six decisions

Each was open on 2026/09/11. The measurements quoted under a decision are
from that draft (VergeOS 26.1.8). The outcome is the code on this tip.

### D1. `save()` sent an empty PUT

`vm`, `nic`, `drive`, `network`, and `user` ended their update path with
a bare `obj.save()`. pyvergeos `ResourceObject.save(**kwargs)` calls
`manager.update(key, **kwargs)`, which PUTs `json_data=kwargs`. A bare
`save()` therefore PUT `{}`, and the attributes set on the object were
never transmitted. The module reported `changed=true` either way.

Captured at the wire, updating a VM's description:

```
PUT vms/36 {}
GET vms/36
read back: ''
```

**Outcome.** Applied. Those five modules call `save(**update_data)`.

### D2. `drive` compared a `tier` field the row does not have

The live drive row has no `tier` key. It has `preferred_tier`, and the
value is a string (`'4'` on the system that was measured). Comparing
`drive_dict.get('tier')` to the requested tier was `None != 3` on every
run. Combined with D1, the write did not land either.

**Outcome.** Applied. `drive.UPDATE_FIELD_MAP` sends `tier` as
`preferred_tier`. `file` and `nas_volume` use the same mapping. The
`tier_policy` role is in the tree.

### D3. `cloud_init` `state: absent` sent a value the API rejects

`cloud_init` disabled by sending `cloudinit_datasource=''`. Measured:

```
PUT cloudinit_datasource='nocloud' : OK, reads back 'nocloud'
PUT cloudinit_datasource=''        : ValidationError: value '' is not
                                      in list for field
                                      'cloudinit_datasource'
PUT cloudinit_datasource='none'    : OK, reads back 'none'
```

The same change also drops the mutual exclusion that blocked `hostname`
next to an explicit `user_data`. The generator already treats `hostname`
as a gap filler.

**Outcome.** Applied. `state: absent` writes `none`. The empty string is
not a choice. `mutually_exclusive` is `(vm_name, vm_id)` and
`(network, network_config)`. `hostname` fills a file the caller did not
supply.

### D4. `snapshot_profile`, and the `enabled` default

Two changes, judged separately in the draft.

*Additive:* a `snapshot_profile` parameter, which the `protect` role uses
to enrol a VM.

*Behavioural:* `enabled` defaulted to `true`, so a partial update asserted
`enabled=true` unless the caller said otherwise. Against a disabled VM the
draft could not show the VM being enabled again, because D1 swallowed the
write. The draft's conclusion was that the behavioural half is a
prerequisite for fixing D1.

**Outcome.** Both halves are applied. `vm` takes `snapshot_profile` and
resolves it by name. `enabled` has no argument spec default. Create treats
an omitted value as true. An update that omits it leaves the current value
unchanged.

### D5. Bearer token auth on the shared argument spec

The draft proposed a `token` parameter with a `VERGEOS_TOKEN` fallback,
and making `username` / `password` optional.

**Outcome.** Applied under the name `api_key`, fallback `VERGEOS_API_KEY`.
`username` and `password` are optional. When both forms are set, `api_key`
wins. `get_vergeos_client()` passes it as the SDK token.

### D6. `requires_ansible: ">=2.14.0"`

2.14 was the standing `meta-runtime[unsupported-version]` lint failure.
Raising the floor is a statement of the supported version.

**Outcome.** `meta/runtime.yml` is `requires_ansible: ">=2.15.0"`.

## Findings from the draft that are still in the code

**`file`'s `filesize` settles asynchronously.** Measured on 26.1.8: right
after an upload the row's `filesize` can be short of the source, and it
reaches the true value seconds later. `allocated_bytes` is right
immediately. `plugins/modules/file.py` (`size_matches()`) and
`tests/live/verify-file.yml` still say this. The ladder polls. Idempotence
on a later run is not a module bug.

**Unit tests that patched the definition site.** The imported tests
patched `get_vergeos_client` on `module_utils.vergeos`, where the name is
defined, while each module binds its own reference at import. Importing
the module before the patch made `test_vm_clone` open real TCP connections.
Fixed by patching the module under test, and by dropping the `sys.modules`
stub of pyvergeos. Issue #66. `tests/unit/test_fixture_discipline.py`
refuses that stub.

## Not in this tree

- `site_sync`, `site_sync_info`, and a `dr_replication` role. Issue #38
  deferred them. `vm_backup` and `restore_drill` are present and do not
  call those names.
- An action group covering the whole collection. Only `recipe` is declared.
- The numbered register B1 through B7, B9 through B20 as one document.
  `docs/KNOWN-BUGS.md` records that B8 was never assigned. Entries the
  code still names are in the module comments, in
  `tests/unit/test_fixture_discipline.py`, and in
  `docs/PYVERGEOS-GAPS.md`.

## Related

- `docs/SDK-COMPATIBILITY.md`: floor `pyvergeos>=1.2.8`, verified on 1.6.1
- `docs/PYVERGEOS-GAPS.md`: the 2026/09/09 index diff, and what 1.6.1 changed
- `docs/WHY-TESTS-MISSED-THESE.md`: why fixtures built from the same wrong field names passed
- `docs/KNOWN-BUGS.md`: B8, and where the register lives
