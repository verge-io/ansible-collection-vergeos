# Known bugs — open, tracked, not yet fixed

Live-verified against **VergeOS 26.1.8**, cloud `conundrum-lab`, pyvergeos
1.2.3, collection branches as of 2026-09-09. Every entry records how it was
measured, not how it was inferred.

Status key: **OPEN** = present and unfixed. **FIXED** = corrected, with the
commit. Fixed entries stay for the pattern they illustrate.

---

## B1 — pyvergeos escapes OData string literals in a form the platform rejects
**Status:** OPEN (upstream pyvergeos) · **Severity:** high · **Blocks:** any
name-based lookup through the SDK, including stock collection modules

pyvergeos doubles a single quote SQL-style (`'` → `''`) in
`filters.py:_format_single` and `vm_recipes.py:53`. VergeOS 26.1.8 rejects
that form.

Measured — `GET /api/v4/vnets?fields=name&filter=...`:

| filter | result |
|---|---|
| `name eq 'DMZ'` (control, exists) | HTTP 200, `[{"name":"DMZ"}]` |
| `name eq 'zz-nope'` (control, absent) | HTTP 200, `[]` |
| `name eq 'zz-o''brien'` — pyvergeos form | **HTTP 422 `{"err":"Invalid argument"}`** |
| `name eq 'zz-o\'brien'` — backslash form | HTTP 200, `[]` |
| `name eq 'zz-o'brien'` (control, unescaped) | HTTP 422 `{"err":"Invalid argument"}` |

At SDK level: `client.networks.get(name="zz-o'brien")` raises
`ValidationError: Invalid argument`, where an absent plain name raises
`NotFoundError`. So it fails loudly rather than silently — the
"error-document counted as a result row" failure mode belongs to hand-built
filters, not the SDK path.

Affects stock modules: `vm_info`, `vm`, `network`, `network_info`, `drive`,
`nic`, `user` all reach the API by name.

Worked around in `module_utils/vm_recipes.py` and `site_sync.py` by matching
client-side. Pinned by test so a later "optimisation" to a server-side filter
has to face it.

---

## B2 — group membership rows parsed with invented field names
**Status:** OPEN · **Severity:** high · **Branch:** `feature/rbac-as-code`
**Blocks:** `group` module membership, `group_info`, `rbac` role

`module_utils/rbac.py:member_names()` reads `member_name` and `member_type`.
Neither exists on the raw row. Measured on live `conundrum-lab`:

```
{'$key': 1, 'parent_group': 1, 'member': 'users/1',
 'member_display': 'welchums', 'creator': ''}
```

The type and key are both encoded in `member` as `"<table>/<key>"`; the name
is `member_display`. `member_type` / `member_name` / `member_key` exist only
as computed properties on the SDK model, which `dict()` discards.

Consequence: every group reports **zero members**. With
`exact_members: true` the role would try to add members who are already
there and remove nobody.

---

## B3 — group "system" flag read with the wrong field name
**Status:** OPEN · **Severity:** low · **Branch:** `feature/rbac-as-code`
**Blocks:** `group_info.system_groups` (always empty)

`group_info.py` reads `row.get('system')`. The live row has `system_group`.
Measured keys on `conundrum-lab`:

```
['$key','created','creator','description','email','enabled','id',
 'identity','member_count','name','system_group']
```

Consequence: platform-managed groups are never identified, so the `rbac`
role's "not reconciled by this role" notice never lists anything.

---

## B4 — node state read with invented field names
**Status:** FIXED — `a7b1d9f` on `feature/rolling-update` · **Severity:** high

A live node row carries `running` (there is **no** `online` key) and
`need_restart` (singular). `summarize_node` looked for `online` and
`needs_restart`, found neither, and returned `False`.

Consequence before the fix: `node_info` reported every node offline;
`node_maintenance` refused every drain ("no other online node available");
`rolling_update` selected no targets and ran with an empty health-gate
baseline, so it could not detect a lost node.

Measured node row keys include: `running`, `maintenance`, `need_restart`,
`status`, `restart_reason`, `ipmi_status`.

---

## B5 — property fallback was dead code (ResourceObject is a dict subclass)
**Status:** FIXED — `a7b1d9f` · **Severity:** medium

`pyvergeos.resources.base.ResourceObject` is declared
`class ResourceObject(dict[str, Any])`. Verified:
`issubclass(ResourceObject, dict) == True`.

So `isinstance(node, dict)` is true for every SDK object, and any
"properties first, mapping second" branch keyed on that check never consults
the properties.

Instructive detail: the first fix for B4 still passed live, because `running`
is in the raw row — the right answer arrived through the wrong path. Only
inspecting the class hierarchy found it.

---

## B6 — update_settings raw fallback used property names
**Status:** FIXED — `a7b1d9f` · **Severity:** medium

The live `update_settings` row drops the `is_` prefix entirely and uses
`*_display` for names. Measured keys:

```
['$key','anonymize_statistics','applying_updates','applying_updates_force',
 'auto_reboot','auto_refresh','auto_update','branch','branch_description',
 'branch_display','installed','max_vsan_usage','multi_cluster_update','name',
 'reboot_required','release_notes_url','snapshot_cloud_expire_seconds',
 'snapshot_cloud_on_update','source','source_display','update_time',
 'warm_reboot']
```

`_prop()` looked for `is_installed`, `is_reboot_required` etc. in the row, so
the fallback could never fire.

---

## B7 — pyvergeos cannot express a partial (tag-scoped) snapshot
**Status:** OPEN (upstream pyvergeos) · **Severity:** medium
**Blocks:** partial-snapshot-as-code (deferred feature #2)

`cloud_snapshots.create()` accepts `name`, `retention_seconds`, `retention`,
`never_expire`, `min_snapshots`, `immutable`, `private`, `wait`,
`wait_timeout`. There is no `include_tags` / `exclude_tags` /
`quiesce_tags` parameter, and `grep -ri` for `quiesce`, `ovirt` and
tag-scoped snapshot terms across the pyvergeos tree returns nothing.

Tag-based partial snapshots are a headline VergeOS 26.1 feature, so the SDK
lags the platform here.

---

## The pattern
B2, B3, B4 and B6 are one mistake made four times: **field names were
inferred, and the unit-test fixtures encoded the same inference**, so the
tests verified self-consistency rather than correctness. Every fixture in
this collection should be traceable to a measured row.
