# Porting `vergeos_platform` into this collection — status

Branch `platform-port`, cut from `integration-all` at `fbcf95d`.
All measurements below are from **conundrum-lab, VergeOS 26.1.8, 2 nodes,
pyvergeos 1.2.5, ansible-core 2.20.9**, on 2026-09-11.

## Ground rule

Base-collection behaviour is not changed without evidence and sign-off.
The split fell out of the work naturally: of the 16 patch sets in
`vergeos_platform/collection-contrib/patches/`, the 12 that are purely
additive applied to this branch cleanly, and the 4 that change existing
modules are exactly the 4 that conflicted. Those 4 are in "Waiting on a
decision" below and **none of them has been applied**.

## Landed (additive only)

18 new modules, each with unit tests and a live ladder:

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

Also: the 18 are registered in `meta/runtime.yml` action groups (`all`
30→48, `info` 12→16, `management` 18→32), without which
`module_defaults: group/vergeio.vergeos.all` silently skips them.

### Gates, before and after

| | Before | After |
|---|---|---|
| Unit tests | 558 passed | **719 passed**, 0 failed |
| ansible-lint | 1 failure, 95 warnings | **1 failure**, 109 warnings |
| Live ladders | 8 (not runnable: token-only helpers) | **11, all green** |
| `zz-*` leftovers in 13 tables | — | **zero** |

The one lint failure is pre-existing (`meta-runtime[unsupported-version]`).
The +14 warnings are `name[casing]` in the new ladders, which
`.ansible-lint` already warn-lists for this exact convention.

### Two things found while landing it

**The imported unit tests were import-order fragile.** They patched
`get_vergeos_client` on `module_utils.vergeos` — where the name is
*defined* — while each module binds its own reference at import. It
worked only because `run_main()` imported the module inside the patch
context, so whichever test ran first bound the mock for good. Pre-import
the module (all it takes is another test file importing it) and
`test_vm_clone` went from `7 passed in 0.13s` to `7 failed in 33.34s`,
the 33 seconds being real TCP attempts to `vergeos.example.com`. Fixed by
retargeting to the module under test, and by dropping the `sys.modules`
stub of pyvergeos in favour of the real exception classes.

**`file`'s `filesize` settles asynchronously.** Right after uploading a
1048576-byte file the row reads `786432`, reaching the true value seconds
later; `allocated_bytes` is right immediately. This looks like a module
bug and is not — idempotence is correct on any realistic second run. The
ladder polls rather than asserting once. Written down so nobody "fixes"
it.

## Waiting on a decision — base-behaviour changes

Each is reproduced below on 26.1.8 first-hand, not inherited from the
platform repo's notes. None is applied.

### D1 — `save()` sends an empty PUT; five modules never persist updates

`vm`, `nic`, `drive`, `network` and `user` all end their update path with
a bare `obj.save()`. pyvergeos's `ResourceObject.save(**kwargs)` calls
`manager.update(key, **kwargs)`, which PUTs `json_data=kwargs` — so a
bare `save()` PUTs `{}`. The attributes `setattr` put on the object are
never transmitted.

Captured at the wire, updating a VM's description:

```
PUT vms/36 {}          <- the whole payload
GET vms/36
read back: ''          <- the change is gone
```

The module reports `changed=true` either way. **Every update through
these five modules is a silent no-op.** This is the highest-severity
item in the port and is pre-existing on `main`.

Fix is five one-line changes (`obj.save(**update_data)`) plus regression
tests. The platform's patch does exactly this but also carries a
conftest rewrite that this branch has already done differently, which is
why it conflicts.

### D2 — `drive`'s `tier` compares a field that does not exist

The live drive row has **no `tier` key**. It has `preferred_tier`, and it
is a *string*:

```
keys: ['$key', ..., 'physical_status', 'preferred_tier', ...]
tier            -> absent
preferred_tier  -> '4'
```

So `drive_dict.get('tier') != module.params['tier']` is `None != 3`,
always true: the module reports `changed` forever, writes a field the API
does not have, and never converges. Compounded by D1, which means it
writes nothing at all.

Blocks the `tier_policy` role, which is the SPBM-parity story.

### D3 — `cloud_init` `state: absent` sends a value the API rejects

`cloud_init.py:381` disables by sending `cloudinit_datasource=''`.
Measured:

```
PUT cloudinit_datasource='nocloud' -> OK,   reads back 'nocloud'
PUT cloudinit_datasource=''        -> ValidationError: value '' is not
                                      in list for field
                                      'cloudinit_datasource'
PUT cloudinit_datasource='none'    -> OK,   reads back 'none'
```

`state: absent` therefore cannot disable cloud-init. The disable value is
`'none'`.

The same patch also relaxes `mutually_exclusive` so `hostname` can be
given alongside an explicit `user_data` — the generator already treats
`hostname` as a gap-filler, so the constraint only blocks the common
import case of custom user-data plus generated meta-data. That half is a
deliberate interface relaxation; it widens what is accepted and rejects
nothing that works today.

### D4 — `vm`: add `snapshot_profile`, and drop the `enabled` default

Two changes in one patch, and they should be judged separately.

*Additive:* a `snapshot_profile` parameter, which the `protect` role
needs to enrol VMs declaratively.

*Behavioural:* `enabled=dict(type='bool', default=True)` becomes
`enabled=dict(type='bool')`. With the default, `module.params['enabled']`
is `True` on every run unless the caller says otherwise, so any partial
update to an existing VM also asserts `enabled=True`.

Worth being precise about what I could and could not show: I ran this
against a deliberately disabled VM and the VM **stayed disabled** — no
defect visible. That is only because D1 swallows the write. Fix D1 and
this becomes live: a reconciler setting just `snapshot_profile` would
re-enable a VM someone had deliberately disabled. **So D4's behavioural
half is a prerequisite for D1, not an independent nice-to-have.**

### D5 — bearer-token auth in the shared argument spec

Adds `token` (with `VERGEOS_TOKEN` fallback) to
`vergeos_argument_spec()` and makes `username`/`password` optional, with
`get_vergeos_client()` enforcing "token, or username+password". pyvergeos
already supports it; the collection never exposed it.

Touches shared auth for **every** module, which is why it is here rather
than in the landed set. Note the platform's own helper scripts assume it:
they hard-required `VERGEOS_TOKEN` and had to be given a password
fallback before any ladder using a scratch network could run at all.

### D6 — `requires_ansible: ">=2.14.0"` is the standing lint failure

The one pre-existing ansible-lint failure, on both `main` and here. 2.14
is end-of-life. Raising it is a supported-version statement, not a code
change, so it is the owner's call.

## Not started

The 13 operational roles in `vergeos_platform/ansible/roles/` —
`network_policy`, `api_key_rotation`, `vm_backup`, `image_pipeline`,
`rebalance_advisor`, `restore_drill`, `billing_export`, `protect`,
`node_drain`, `health_report`, `tier_policy`, `lb_stack`, `k3s_node`.
(`recipe_deploy` is already here as the `vm_from_recipe` role.)

Their module dependencies are now all satisfied by the landed set,
except:

- `tier_policy` needs **D2**.
- `protect` wants **D4**'s additive half.

Five of them (`billing_export`, `health_report`, `rebalance_advisor`,
`node_drain`, and the analysis half of `tier_policy`) use builtin modules
only and carry their own pure-function unit tests, so they can land with
no risk to the base collection at all. That is the natural next tranche.

`node_drain` is worth flagging: it was written and staged on a
single-node lab with its drain execution deliberately unimplemented,
because it could not be verified. **The lab now has two nodes**, so the
`NODE2-DAY-ONE.md` sequence is unblocked for the first time.
