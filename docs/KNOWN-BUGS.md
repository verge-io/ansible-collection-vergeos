# Known bugs — open, tracked, not yet fixed

Live-verified against **VergeOS 26.1.8**, cloud `conundrum-lab`, collection
branches as of 2026-09-09 (updated same day after B2, B3, B9 and B10 were
fixed and B13-B15 found). Every entry records how it was measured, not how
it was inferred.

> **Re-verified 2026-09-21 against pyvergeos 1.2.7.** The original pass was
> taken on **1.2.3**, and `requirements.txt` now declares `>=1.2.7`, so the
> three entries that blamed the SDK were re-measured:
>
> | entry | on 1.2.3 | on **1.2.7** |
> |---|---|---|
> | **B1** escaping | OPEN | **FIXED** — `networks.get(name="zz-o'brien")` now raises `NotFoundError`, not `ValidationError` |
> | **B7** tag-scoped snapshot | OPEN | **still OPEN** — `CloudSnapshotManager.create()` still takes no `include_tags`/`exclude_tags` |
> | **B15** `cluster_status` | OPEN | **still OPEN** — `VergeClient` still has no `cluster_status` attribute |
>
> Entries **not** listed above have not been re-measured on 1.2.7 and still
> carry their original 1.2.3 dating.

Status key: **OPEN** = present and unfixed. **FIXED** = corrected, with the
commit. Fixed entries stay for the pattern they illustrate.

---

## B1 — pyvergeos escaped OData string literals in a form the platform rejects
**Status:** **FIXED** upstream in pyvergeos 1.2.5 (issue #72, PR #76
`4338f3b`) · **Re-verified on 1.2.7, 2026-09-21** · **Severity was:** high

pyvergeos doubled a single quote SQL-style (`'` → `''`) in
`filters.py:_format_single` and `vm_recipes.py:53`. VergeOS 26.1.8 rejects
that form. 1.2.5 replaced 84 scattered copies with a shared `quote_value()`
that emits the backslash form the platform accepts.

The platform's behaviour has **not** changed, and was never the bug — the
SQL form is simply the wrong escape. What changed is what the SDK emits.
Re-measured on 1.2.7, `GET /api/v4/vnets?fields=name&filter=...`:

| filter | result (unchanged) |
|---|---|
| `name eq 'DMZ'` (control, exists) | HTTP 200, `[{"name":"DMZ"}]` |
| `name eq 'zz-nope'` (control, absent) | HTTP 200, `[]` |
| `name eq 'zz-o''brien'` — the **old** pyvergeos form | **HTTP 422 `{"err":"Invalid argument"}`** |
| `name eq 'zz-o\'brien'` — backslash form, what 1.2.7 now emits | HTTP 200, `[]` |

At SDK level, the difference that matters:

| call | 1.2.3 | **1.2.7** |
|---|---|---|
| `client.networks.get(name="zz-o'brien")` | `ValidationError: Invalid argument` | **`NotFoundError`** |
| `client.networks.get(name="zz-nope")` | `NotFoundError` | `NotFoundError` |
| `client.networks.get(name="DMZ")` | found | found |

An awkward name is now indistinguishable from an ordinary absent one, which
is the correct outcome.

Affected stock modules while open: `vm_info`, `vm`, `network`,
`network_info`, `drive`, `nic`, `user` — all reach the API by name.

**Consequences still outstanding:**

- The client-side-matching workarounds in `module_utils/nodes.py`
  (`find_node`), `module_utils/site_sync.py` (`find_by_name`),
  `module_utils/vm_recipes.py` and `modules/member.py` were kept because
  `requirements.txt` declared `pyvergeos>=1.0.1` and the collection had to
  work on SDKs that still carried the defect. That floor is now `>=1.2.7`,
  so the *original* rationale has expired.

  **Do not remove them.** A 2026-09-21 review recommended exactly that, and
  it was wrong. They are what keeps this collection clear of
  pyvergeos#96: `client.vms.list(name=X)` silently discards the name and
  returns every non-snapshot VM. An "optimisation" from a client-side match
  to `vms.list(name=...)` would turn a correct lookup into one that matches
  everything — and the modules that do that lookup go on to update and
  delete. Revisit only once #96 is fixed and the floor is raised past it.
  See B18.
- `modules/catalog.py` did not work around B1 — it **reimplemented** it,
  hand-building `filter="name eq '%s'"` after SQL-doubling. Fixed
  2026-09-21; see `TestLookupDelegatesEscapingToTheSDK` and the apostrophe
  rung in `tests/live/verify-catalog.yml`.

---

## B2 — group membership rows parsed with invented field names
**Status:** FIXED — `14b89be` + `d69a126` on `feature/rbac-as-code`
**Severity:** high

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
**Status:** FIXED — `14b89be` · **Severity:** low

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
**Status:** OPEN (upstream pyvergeos) · **Re-verified still open on 1.2.7,
2026-09-21** · **Severity:** medium
**Blocks:** partial-snapshot-as-code (deferred feature #2)

`cloud_snapshots.create()` accepts `name`, `retention_seconds`, `retention`,
`never_expire`, `min_snapshots`, `immutable`, `private`, `wait`,
`wait_timeout`. There is no `include_tags` / `exclude_tags` /
`quiesce_tags` parameter, and `grep -ri` for `quiesce`, `ovirt` and
tag-scoped snapshot terms across the pyvergeos tree returns nothing.

Re-checked on 1.2.7: the signature is unchanged. (`quiesce` does appear
elsewhere in the SDK tree — `snapshot_profiles`, `nas_volumes`,
`volume_vm_exports` — but not on the cloud-snapshot path this entry is
about, so the original grep conclusion holds for the feature in question.)

Tag-based partial snapshots are a headline VergeOS 26.1 feature, so the SDK
lags the platform here.

---

---

## B9 — the recipe simulate is unreachable through pyvergeos
**Status:** FIXED — `4edf4cb` on `feature/deploy-vm-from-recipe`
**Severity:** HIGH — the flagship recipe feature did not function

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
**Status:** FIXED — `6bbb396` on `feature/rolling-update`, by deleting the
hand-rolled sequence rather than correcting it · **Severity:** HIGH

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
**Status:** FIXED — `bbb70c4` (site-sync-dr), `db7f87c` (deploy-vm-from-recipe)
**Severity:** low

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
**Status:** FIXED — `6d3860c` · **Severity:** low (usability)

`meta/runtime.yml` contains only `requires_ansible`. There are no
`action_groups`, so `module_defaults: group/vergeio.vergeos.all:` fails with
`could not resolve the module_defaults group vergeio.vergeos.all` — measured
while writing the lab probes.

Consequence: every task must repeat `host`/`username`/`password`/`insecure`.
The roles in this collection do exactly that, four lines per task, which is
the noise an action group exists to remove.

---

## B13 — the shipped `member` module is entirely non-functional
**Status:** FIXED — `56eaca9`. Pre-existing on `main`, not introduced here.
**Severity:** HIGH — a documented 1.0.0 module that cannot work
**Blocks:** `vergeio.vergeos.member`, both states

Two independent defects, the first fatal before the second is reached.

**1. Wrong keyword.** `get_user()` calls `client.users.get(username=...)`.
The signature is `get(key=None, *, name=None, fields=None)`. Measured live:

```
fatal: [localhost]: FAILED! => {"changed": false,
  "msg": "Unexpected error: UserManager.get() got an unexpected keyword
          argument 'username'"}
```

Both `state: present` and `state: absent` fail this way, so the module has
never added or removed anyone.

**2. Reference compared against a username.** `get_member()` does:

```python
if member_dict.get('member') == member_username:
```

`member` is a reference, not a name. Measured on a real membership row:

```
{'$key': 4, 'parent_group': 2, 'member': '/v4/users/2',
 'system': False, 'creator': 'welchums'}
```

`'/v4/users/2' == 'labuser'` is never true, so even with defect 1 fixed:
`present` would always believe the member is absent and re-add (not
idempotent), and `absent` would never find anyone and silently remove
nothing. `add_member()` also posts `member=<username>` where the API takes
`/v4/users/{key}` — pyvergeos's own `add_user()` sends the reference form.

Reproduced against a group created and populated through the API, so the
membership row definitely existed.

**Relationship to the `group` module:** `group` covers this ground
declaratively (whole membership set, nested groups, `exact_members`) and works
— verified live. So `group` is not duplicating a working module; it replaces a
broken one. `member` should be fixed or deprecated in favour of `group`, and
that is a decision for the collection owners rather than something to do
silently.

---

## B14 — `no_log` on `answers` scrubs the VM name from every message
**Status:** FIXED — `0fd7fd9` · **Severity:** low (cosmetic, but confusing)
**Branch:** `feature/deploy-vm-from-recipe`

Recipe answers are `no_log` because they carry passwords. Ansible then masks
every `no_log` VALUE anywhere in the output — and `HOSTNAME` is normally the
same string as the VM name, so the name is masked too. Measured:

```
"msg": "VM '********' already exists; this module never re-deploys over
        an existing VM."
```

The message is correct and unreadable. Not fixable by un-`no_log`-ing the
answers, which would leak the password. Options are to stop echoing the name
in messages, or to accept it. Recorded rather than guessed at.

---

## B15 — the collection ships no `cluster_status` coverage but two modules need it
**Status:** OPEN (upstream pyvergeos) · **Re-verified still open on 1.2.7,
2026-09-21** — `VergeClient` exposes `clusters` but no `cluster_status`
· **Severity:** medium
**Worked around:** `module_utils/clusters.py` via `client._request()`

Detail in `docs/PYVERGEOS-GAPS.md` under P0. Recorded here too because it is
the reason a `_request` call exists in `clusters.py`, and anyone tidying that
away will reintroduce it.

---

## P1 — PLATFORM: a drain with insufficient target capacity stalls silently
**Status:** OPEN (VergeOS, not ours) · **Severity:** high operationally
**Now detected before the fact:** `cluster_info` with `drain_candidates`
refuses the drain, and the `rolling_update` role gates on it — `6bbb396`.

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

---

## B16 — the unit suite was not testing what it claimed
**Status:** FIXED — `6178fe6` · **Severity:** medium (false confidence)

Six failures and 47 collection errors were tolerated as "main's baseline".
Every one was a test defect, and worse, several PASSING tests could not fail.

- An autouse fixture replaced `pyvergeos.exceptions` with a `MagicMock`, so
  `NotFoundError` was not an exception class. `unittest.mock` treats a
  non-exception `side_effect` as a **callable** -- it calls it and returns the
  result instead of raising. The "VM not found" test never entered the
  not-found branch.
- Patches targeted `module_utils.vergeos`, but modules bind the name at
  import. The patch never reached them.
- Seven row mocks set `__iter__`, which `dict()` ignores in favour of
  `keys()`. `dict(mock)` was `{}`, so six tests passed no matter what the row
  contained.
- `test_fails_when_sdk_not_installed` never made the SDK absent.

Result: **532 passed, 0 failed, 4.43s**, from 6 failed / 47 errors / >2min.

Worth keeping in mind as a pattern, not just a fixed bug: three of these are
the same failure mode as B4 and B2 -- a fixture that encodes an assumption,
so the test agrees with the code and both are wrong together.

---

## B17 — `requires_ansible` names an unsupported ansible-core
**Status:** OPEN — deliberately, it is a support-matrix decision
**Severity:** low

`meta/runtime.yml` declares `requires_ansible: ">=2.14.0"`. ansible-core 2.14
is end-of-life, and ansible-lint rejects it:

```
meta-runtime[unsupported-version]: 'requires_ansible' must refer to a
currently supported version such as: >=2.15.0, ...
```

It is the only lint failure left in the repository. Not changed here, because
raising the floor drops a declared supported platform, and that is a call for
the collection owners rather than a lint fix.

---

## B18 — pyvergeos `list()` returns every row when it cannot apply a filter
**Status:** OPEN (upstream pyvergeos **#96**) · **Severity:** high ·
**Measured on 1.2.7, 2026-09-21**

`client.vms.list(name='does-not-exist')` returns **every non-snapshot VM**.
`VMManager.list()` always supplies its own `is_snapshot eq false` filter,
and `ResourceManager.list()` chooses `filter` *or* `filter_kwargs` with an
`elif`, so the name never reaches the query.

Found the way these things usually are — the ordinary cleanup idiom

```python
for v in c.vms.list(name='zz-jw-tier-probe'):   # a name that did not exist
    c.vms.delete(dict(v)['$key'])
```

returned `dr-test` and attempted to delete it. It survived only because it
was running.

Two further paths in the same area: 33 managers declare a filter `**kwargs`
they never read (`vm.drives.list(name=X)` ignores the name), and a filter
kwarg of `None` empties the filter entirely (`networks.list(name=None)`
returns all 11).

**This collection is not affected**, verified call by call: every use of an
impacted manager is an unfiltered `.list()` with client-side matching, and
all eight filtered calls pass named parameters that *are* honoured —
`media`, `cidr`, `ip`, `identity_key`, `recipe_ref`, and `name` on
`catalogs`. That is luck, not design: the client-side matching exists as a
workaround for B1. See the note under B1 before "tidying" it away.

**Related, and not ours:** a filter naming a field the table does not have,
whose value contains balanced braces, makes VergeOS 26.1.8 itself discard
the filter and return the whole table — `nmae eq '{a: 1}'` returns all rows
on `vnets`, `vms` and `users`, while `nmae eq 'plain'` correctly returns
none. Filed as verge-io/engineering#20.

---

## B19 — pyvergeos does not escape `{` in filter literals, so a lookup by name can hit a different object
**Status:** OPEN (upstream pyvergeos **#100**; docs gap **docs-vergeos#58**) ·
**Severity:** high · **Measured on 26.1.8 / pyvergeos 1.2.7, 2026-09-21**

> **Revised.** First filed as a VergeOS parser defect (engineering#20, now
> closed). That was wrong twice over, because I asserted "there is no escape"
> without testing one. `\{` works, including for a lone `{`. The filter
> grammar reserves exactly three characters inside a string literal --
> `\`, `'` and `{` -- all take a backslash escape, `}` is not reserved, and a
> 92-character ASCII sweep found no others. The platform is behaving to its
> own grammar; the SDK is not following it.

`quote_value()` escapes `\` and `'` -- correctly, per pyvergeos #72/#76 --
but omits `{`. An unescaped balanced `{...}` is consumed by the filter
grammar, so the query resolves to whatever the remaining string names; an
unescaped lone `{` returns HTTP 422. VergeOS stores such names correctly and
returns them correctly by `$key`, so this is purely a filter-construction
defect:

```
key 41 -> 'zz-jw-br{x}ace'      (read back by key: correct)
key 42 -> 'zz-jw-brace'         (read back by key: correct)

GET vms?filter=name eq 'zz-jw-br{x}ace'  ->  [{"name":"zz-jw-brace"}]
```

The escape fixes it, which is the whole point:

```
name eq 'zz-jw-br{x}ace'    ->  wrong row
name eq 'zz-jw-br\{x}ace'   ->  correct row
name eq 'zz-p-{lead'        ->  HTTP 422
name eq 'zz-p-\{lead'       ->  correct row
```

Verified end-to-end: monkeypatching `quote_value` to also escape `{` made all
six probe names resolve correctly with no regression on the `'` and `\` cases.

A second, independent platform behaviour is adjacent but **not** the cause: a
filter naming a field the table does not have is treated as an empty column
rather than rejected, so `unknown eq ''` and `unknown ne 'x'` match every row.
Noted on engineering#20; not separately filed.

### Why this is in OUR bug list

Because we shipped code that was exposed to it. Reproduced end-to-end through
the `catalog` module on 2026-09-21:

```
create 'zz-jw-cat'      -> changed=true
create 'zz-jw-c{x}at'   -> changed=false   (matched the OTHER catalog; created nothing)
absent 'zz-jw-c{x}at'   -> changed=true    (DELETED 'zz-jw-cat')
```

A request to delete a catalog that never existed destroyed a different one and
reported success.

`catalog.py` was the only server-side name filter in the collection — briefly,
and by my hand: it was introduced earlier the same day while fixing the B1
escaping bug. It now matches client-side like every other name lookup here,
which is immune to this and to B18. Pinned by `TestLookupIsClientSideAndNeverFiltersByName` and by rung 10 of `verify-catalog.yml`, which creates a
neighbour differing only by a brace token and asserts it survives.

**Do not reintroduce a server-side `name eq '...'` lookup anywhere** until
pyvergeos#100 is released and `requirements.txt` floors past it. That is now the
third distinct reason (B1 escaping, B18 SDK fail-open, B19 brace stripping) —
the client-side matching that keeps getting flagged as redundant is load-bearing.

---

## B20 — a one-character answer masks the digits in every `vm_recipe_deploy` message
**Status:** FIXED — `narrow_no_log()` in `vm_recipe_deploy.py`
**Severity:** low — cosmetic, but it removed the diagnostic at the moment it was needed
**Found:** 2026-09-21, building `tests/live/verify-recipe-fuzz.yml`

B14 recorded that a `no_log` answer whose value equals the VM name masks the
name. That is the *exact-match* case, and `0fd7fd9` fixed it by not echoing the
name. This is the *substring* case, which that fix does not reach and cannot.

`AnsibleModule` collects every `no_log` value into a set of strings —
including integers, via `_return_datastructure_name`, which yields
`to_native(obj)` for `int` and `float` — and `_remove_values_conditions` then
does a plain `native_str_value.replace(omit_me, '*' * 8)` for each of them
against every string the module returns. A one-character answer therefore
masks that character *everywhere*, including inside numbers that have nothing
to do with any answer.

`answers` must stay `no_log`: recipe answers routinely carry passwords, and
ansible-core logs the invocation before the module runs, so redacting inside
the module is too late. Small integers are equally unavoidable —
`YB_CPU_CORES: 1` is the ordinary answer, not a contrived one.

### Reproduce

Two runs differing by exactly one answer. Measured against the lab, VergeOS
26.1.8, ansible-core 2.20:

```yaml
- vergeio.vergeos.vm_recipe_deploy:
    name: zz-probe
    recipe: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    answers: {YB_CPU_CORES: 5, YB_RAM: 100}   # YB_RAM's minimum is 256
  check_mode: true
  failed_when: false
```

```
with    YB_CPU_CORES: 5  ->  "... the recipe requires at least 2********6"
without YB_CPU_CORES     ->  "... the recipe requires at least 256"
```

The same run also reports `answer 'YB_RAM' is ********`, which is the
exact-match case and is working as intended.

### Signature

A module message carrying `********` inside a number, a key or a hostname
rather than in place of one. The masked run is otherwise correct: the refusal,
the exit code and the structured return values are all right — only the prose
is damaged.

### Impact, stated plainly

Nothing acts on the mangled text. The cost is that an operator reading a
failed deploy sees `requires at least 5********2` instead of the number they
need, and the same masking applies to any digit, key fragment or word that
happens to match a short answer value. It is the reason every `matching`
string in `verify-recipe-fuzz.yml`'s case table is digit-free.

### The fix

None of the three options first written here were taken. Accepting it left a
real diagnostic broken; splitting `answers` into `answers` + `secret_answers`
would have been a breaking change to a module already on `main`; and upstream
is the right long-term home but does not help anyone on ansible-core 2.20.

There is a fourth option, and it is neither breaking nor a guess:
**keep `answers` `no_log`, and narrow `module.no_log_values` afterwards.**

`AnsibleModule.__init__` logs the invocation — the path that actually matters
for credential leakage — before any module code runs, and it does so with
everything masked. `remove_values()` is applied to the RETURN data separately,
at `exit_json`/`fail_json` time, reading `self.no_log_values` as it stands
then. So by the time the module has called the API it knows the recipe's
question TYPES, and can stop masking the answers the platform itself says are
not credentials, without ever having exposed them to the logging path.

`narrow_no_log()` does exactly that, and fails closed three ways:

- a recipe that publishes no questions cannot be classified, so **nothing** is
  unmasked;
- a value any secret answer also contributes stays masked, so a password that
  happens to equal a core count is not unmasked by the core count;
- a value any other `no_log` parameter contributes — `password`, `api_key` —
  stays masked, and those are read from `module.argument_spec` rather than
  named in the function, so a `no_log` parameter added later is covered
  without anyone remembering to come back here.

Everything earlier in `main()` still runs fully masked, including the
"already exists" path from B14.

### Verified

Same marker string, classified two ways by the recipe's own question types.
`fuzz` is a substring of the recipe name the module quotes back, so whether it
survives says precisely how the answer was classified:

```
ZZ_SECRET: fuzz   (type password)  -> recipe 'zz-********-recipe'
ZZ_NOTE:   fuzz   (type string)    -> recipe 'zz-fuzz-recipe'
```

and the digits came back:

```
before: the recipe requires at least 5********2
after:  the recipe requires at least 512
```

Pinned by rungs 5–5g of `verify-recipe-fuzz.yml`, which assert **both**
directions — a fix that only restored the digits would be a credential leak,
not an improvement — plus eight unit tests on the pure helpers, including the
collision case where a password and a core count share a value.
