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

---

## B9 — the recipe simulate is unreachable through pyvergeos
**Status:** OPEN · **Severity:** HIGH — the flagship recipe feature does not
function · **Branch:** `feature/deploy-vm-from-recipe`
**Blocks:** `vm_recipe_deploy` entirely, and the `vm_from_recipe` role with it

A simulate POST succeeds and returns exactly the document `scan_simulate()`
was written for — but it comes back on a **non-2xx status**, so pyvergeos
raises and discards the body.

Measured, raw HTTP `POST /api/v4/vm_recipe_instances` with `simulate: true`:

```
HTTP 405
keys: ['err', 'response']
err : 'Simulation complete'
response.logs           : 28 entries (real recipe steps, incl. CREATE_OS_DRIVE)
response.cloudinit_files: ['/meta-data', '/network-config', '/user-data']
response.answers.YB_VM_KEY: 36
```

Through the SDK the same call raises:

```
type=APIError  str='Simulation complete'  status_code=405
attributes: ['add_note','args','status_code','with_traceback']   # no body
```

`client.py` dispatches on HTTP status (`else: raise APIError(...)`) and
`_extract_error_message` keeps only the message string. The 28-entry log — the
entire point of the scan — is unrecoverable.

Consequence: `vm_recipe_deploy` fails with `API error: Simulation complete`
before it can deploy or report. Confirmed live via the module:
`fatal: ... "msg": "API error: Simulation complete"`.

The simulate itself is a genuine dry run: after two simulate runs, zero
`zz-claude*` VMs existed and the only recipe instance was the pre-existing
`nas1`.

Fix does **not** require an SDK change — the simulate POST needs to bypass
`_request` and read the raw response. Worth reporting the 405-on-success to
VergeIO separately.

---

## B10 — rolling_update treats `maintenance=True` as "evacuation finished"
**Status:** OPEN · **Severity:** HIGH · **Branch:** `feature/rolling-update`
**Blocks:** `rolling_update` role

`tasks/one_node.yml` polls until the node reports `maintenance`, then asserts
evacuation is complete and restarts it. Measured on `conundrum-lab`, draining
node2 (3 running VMs, 57 GB):

```
maintenance flag True after 1 poll (~0 s)
at that moment: 1 of 3 VMs had moved; lab-node1 and lab-node3 (16 GB each)
                were still RUNNING on node2
node2 status  : 'migrating'
```

So the role would have restarted a node with 32 GB of live workloads on it —
precisely the failure it was written to prevent.

The real completion signals are the node's `status` leaving `migrating`, and
zero running VMs whose `node_name` is the drained node. The `maintenance`
boolean only records that the request was accepted.

---

## B11 — I claimed the Jinja test file was identical across branches; it is not
**Status:** OPEN (process/accuracy) · **Severity:** low

The `feature/drive-health` and `feature/rbac-as-code` commit messages say the
file is "identical to the copy on the other branches, so they merge cleanly".
Measured md5 of `tests/unit/test_jinja_filters.py`:

```
feature/site-sync-dr    fa32c792   init_plugin_loader at import time (buggy)
feature/rolling-update  547fd1c1   deferred behind functools.cache (fixed)
feature/drive-health    547fd1c1
feature/rbac-as-code    547fd1c1
```

Merging produced a real `AA` conflict on that path. Two consequences:
`site-sync-dr` still carries the version that perturbs the pre-existing
vm/inventory suites (B5's sibling), and merge order decides which lands.

---

## B12 — the collection defines no action group
**Status:** OPEN · **Severity:** low (usability)

`meta/runtime.yml` contains only `requires_ansible`. There are no
`action_groups`, so `module_defaults: group/vergeio.vergeos.all:` fails with
`could not resolve the module_defaults group vergeio.vergeos.all` — measured
while writing the lab probes.

Consequence: every task must repeat `host`/`username`/`password`/`insecure`.
The roles in this collection do exactly that, four lines per task, which is
the noise an action group exists to remove.

---

## P1 — PLATFORM: a drain with insufficient target capacity stalls silently
**Status:** OPEN (VergeOS, not ours) · **Severity:** high operationally

Not our bug, but it shapes the design. Draining node2 when node1 lacked
headroom for the remaining 32 GB left the node in `status='migrating'`
**indefinitely** — 14+ minutes with no progress, no error, and no timeout.

Per-VM state during the stall showed no migration was even attempted:

```
lab-node1: migratable=True  migration_destination=None  migrated_node=None
lab-node3: migratable=True  migration_destination=None  migrated_node=None
```

Capacity arithmetic taken beforehand predicted it: draining node2 needed
57344 MB against node1's 64512 MB of nominal headroom, but once
`lab-nethost` (24576 MB) had landed, the remaining 32768 MB no longer fit
alongside host and vSAN reservations.

Recovering was clean: `node_maintenance state=active` returned node2 to
service, all seven VMs ended on their original nodes, nothing was lost.

**This is the strongest argument for a pre-flight capacity check** — neither
the UI nor the native apply action verifies that an N-1 evacuation fits
before starting one.

## Verified CORRECT (claims that survived live testing)

- **`machine_drive_stats` row aliasing is real.** 12 of 49 rows have
  `$key != parent_drive`, including the exact case the platform repo cited:
  `stats $key=34 -> parent_drive=39`. Addressing by path would read another
  drive's counters. Note `nas1`'s drives (keys 6-9) all coincide, so a probe
  limited to that VM "disproves" it — a test passing for the wrong reason.
- **`physical_drives` field names** (`temp`, `fw`, `size`, `smart`, all
  `*_warn`) match the live row exactly; SMART triage returned
  `{'ok': 2, 'info': 0, 'warning': 0, 'critical': 0}` on two healthy drives.
- **Recipe question and option discovery works.** 32 recipes;
  `required: ['HOSTNAME','USER','PASSWORD']`, `secrets: ['PASSWORD']`,
  10 internal questions separated, and `SELECT_OS_TIER` resolved live to
  `[{'$key': 1, '$display': 1, 'tier': 1}]`.
- **`permission` module** found the existing `/#0` full-control grant by group
  name and reported `changed=False` in check mode.
- **`node_maintenance`** enter and exit both work; peer detection resolves
  correctly in both directions.

## The pattern
B2, B3, B4 and B6 are one mistake made four times: **field names were
inferred, and the unit-test fixtures encoded the same inference**, so the
tests verified self-consistency rather than correctness. Every fixture in
this collection should be traceable to a measured row.
