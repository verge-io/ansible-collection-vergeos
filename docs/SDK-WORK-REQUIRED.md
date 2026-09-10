# What needs to be done to pyvergeos

*Plain-language summary first, technical detail second.*

Everything here was measured against a live VergeOS 26.1.8 system
(`conundrum-lab`) with pyvergeos 1.2.3. Nothing is inferred.

---

## Plain-language summary

**pyvergeos** is the library our Ansible automation uses to talk to VergeOS.
Think of it as a translator: our tools speak to it, and it speaks to VergeOS.

There are two different kinds of problem with the translator, and they need
very different responses.

### Kind 1 — the translator gets some things wrong (3 items)

These are the urgent ones. The translator claims to handle something, and
returns a wrong answer rather than admitting it cannot. Wrong answers are far
more dangerous than missing features, because nothing looks broken.

In plain terms, today:

- a name containing an apostrophe or a backslash cannot be looked up —
  pyvergeos can create such a name and then fails to read it back. VergeOS
  itself allows both, and 11 other punctuation characters we tested work
  fine, so this is narrow;
- the recipe "practice run" works, but its report is thrown away before
  anyone can read it;
- **a user cannot be removed from the Administrators group** — the call
  reports "not a member" about someone who is.

All three are small fixes. **This is the highest-value work by a wide margin.**

Each has a runnable reproduction in `docs/repro/`.

### Kind 2 — the translator does not know some words yet (about 97 items)

VergeOS can do roughly 322 things through its interface. The translator knows
about 187 of them. The rest have to be reached by going around it.

This is less urgent than it sounds. Going around the translator works fine and
we already do it where needed. Only a handful of the missing items block work
we actually want to do.

### What we need, in priority order

1. **Fix D3 first.** Three lines, and today you cannot remove a user from the
   Administrators group through the SDK.
2. **Then D2 and D1.** Both small. D1 is narrower than first reported — two
   characters, not "punctuation generally".
3. **Add 2 specific missing items** we currently work around.
4. **Add about 12 more** to unblock planned features.
5. **The remaining ~83** are a backlog, not a blocker.

---

## Kind 1 — defects: covered, but wrong

### D1. Two characters break name lookups
**Severity: medium.** *Downgraded from "high, affects nearly everything" — that
was overstated. Corrected after testing which characters are actually
affected.*

**First, the fair question: does VergeOS even allow an apostrophe in a name?**
Yes. Tested by creating groups with 13 different characters — VergeOS accepted
**all thirteen**, including the apostrophe. So the input is reachable; nothing
prevents a customer from typing it.

**But only two of those thirteen actually break pyvergeos:**

| character | example | VergeOS creates it | `pyvergeos.get(name=)` |
|---|---|---|---|
| apostrophe | `zz-obrien's-vm` | yes | **ValidationError: Invalid argument** |
| backslash | `zz-back\slash` | yes | **NotFoundError — for a name that exists** |
| percent | `zz-100%-full` | yes | found |
| underscore | `zz_prod_db` | yes | found |
| double quote | `zz-say-"hi"` | yes | found |
| space | `zz prod db` | yes | found |
| ampersand | `zz-r&d` | yes | found |
| parentheses | `zz-db-(primary)` | yes | found |
| comma, colon, plus, hash | | yes | found |

So this is **not** "ordinary punctuation" — it is two specific characters.
`%` and `_` were expected to break as SQL wildcards and do not, because the
lookup uses `eq` rather than `like`.

**Which of the two matters more is the opposite of what you would guess.**
The apostrophe fails *loudly* — you get an error and go and look. The
backslash fails *quietly*: `get(name=...)` reports the name does not exist
when it does. Modules built on the usual pattern —

```python
try:
    existing = client.groups.get(name=n)
except NotFoundError:
    client.groups.create(name=n)          # "it wasn't there"
```

— then try to create it again. Measured: the platform's uniqueness constraint
catches it and returns `ConflictError: A user/group with this name already
exists`, immediately after reporting it did not. So the visible symptom is a
re-run that fails instead of being idempotent. On any table without a
uniqueness constraint, it would be a duplicate instead.

**How much is this worth?** Honestly: not much for machine-generated names,
which is most infrastructure. It becomes worth something wherever names come
from people or from another system — tenants and VMs named after customers,
NAS shares, imported AD-style names. The fix is small enough that it is
probably worth doing on those grounds alone, but it should not displace D3.

Technical: pyvergeos escapes a single quote by doubling it, SQL-style. VergeOS
does not accept that form.

Measured on `GET /api/v4/vnets?filter=...`:

| filter | result |
|---|---|
| `name eq 'DMZ'` | HTTP 200, one row |
| `name eq 'zz-nope'` | HTTP 200, empty |
| `name eq 'zz-o''brien'` — pyvergeos's form | **HTTP 422 `Invalid argument`** |
| `name eq 'zz-o\'brien'` — backslash form | HTTP 200 |

Affects the shipped `vm_info`, `vm`, `network`, `network_info`, `drive`,
`nic` and `user` modules — all of them look things up by name.

**Where in the code.** 84 occurrences across 47 files, all the same
`replace("'", "''")`. Three are the shared machinery; the rest are copies:

| file:line | what it feeds |
|---|---|
| `filters.py:62` | `Filter._format_single` — the fluent filter builder |
| `filters.py:138` | `_format_value` — used by `build_filter()` |
| `filters.py:172` | the `like` / wildcard path |
| `resources/base.py:184` | **`ResourceManager.get(name=...)`** — the shared lookup every manager inherits |

`base.py:184` is the one that matters most:

```python
if name is not None:
    escaped_name = name.replace("'", "''")
    results = self.list(filter=f"name eq '{escaped_name}'", fields=fields, limit=1)
```

**Fix:** escape with a backslash rather than doubling. Fixing the three in
`filters.py` plus `base.py:184` covers the shared paths; the other 80 are
copy-paste in individual resource files and should ideally call one helper.

**Reproduce:** `bash docs/repro/d1_name_escaping.sh` (the defect) and
`python docs/repro/d1_which_characters.py` (the scope — which characters
VergeOS accepts and which pyvergeos can then find)

**Sharpest demonstration:** pyvergeos can *create* a group named
`zz-claude-o'brien` and then cannot *read it back* —
`groups.get(name=...)` raises `ValidationError: Invalid argument`, while the
backslash form returns the row.

**Our workaround:** match names in Python instead of asking the server to
filter. Works, but costs a full listing on every lookup.

---

### D2. Useful replies are thrown away because of their status code
**Severity: high. Blocks the recipe feature entirely.**

Plain: VergeOS has a "practice run" feature — try a deployment and report what
*would* happen without doing it. It works. But VergeOS reports the result using
a status code that normally means "error", and pyvergeos throws away the
contents of anything it considers an error. So the practice-run report — the
entire point — is discarded before anyone can read it.

Technical: `client.py` branches on the HTTP status and
`_extract_error_message` keeps only a string.

Measured, `POST /api/v4/vm_recipe_instances` with `simulate: true`:

```
HTTP 405
keys: ['err', 'response']
err : 'Simulation complete'
response.logs           : 28 entries, the real deployment steps
response.cloudinit_files: ['/meta-data', '/network-config', '/user-data']
```

Through pyvergeos the same call raises `APIError`, carrying `status_code=405`
and nothing else. The 28-entry log is unrecoverable.

**Where in the code.**

| file:line | what it does |
|---|---|
| `client.py:585-608` | `_handle_response` — branches on status; everything non-2xx goes to the error path |
| `client.py:597` | calls `_extract_error_message`, keeping only its return value |
| `client.py:610-624` | `_extract_error_message` — returns `str`, discarding the rest of the document |
| `exceptions.py:34-39` | `APIError.__init__(message, status_code)` — there is nowhere to put a body |

```python
# client.py:607
else:
    raise APIError(error_message, status_code=response.status_code)
```

Measured on the exception: `attrs = ['add_note', 'args', 'status_code',
'with_traceback']`. The 28-entry log is unreachable.

**Fix:** keep the parsed body on the exception, e.g. `APIError.body`. Callers
can then decide what a status means. Small and backward-compatible — nothing
that reads `str(e)` or `e.status_code` changes.

**Reproduce:** `bash docs/repro/d2_discarded_body.sh`

### What is NOT part of D2

Writing that reproduction took three attempts, and the first two failures
were **my mistakes, not defects**. Recording them because they are easy to
mistake for part of the bug:

1. *"the recipe key must be quoted."* A recipe `$key` is a hash string like
   `4d10e412...`. I pasted it into a shell-built JSON body unquoted, which
   produced invalid JSON. That is a bug in my `curl` command. The API
   behaved correctly and its error message said so.

2. *"a network answer must be a vnet key, not a name."* The API takes
   `YB_NIC_ETH0: 3`, not `YB_NIC_ETH0: "External"`. That is the expected
   contract. Our collection resolves names to keys as a **convenience** --
   exactly what an automation layer should do. Not a defect either.

Neither belongs in a defect report. The reproduction script now handles
both so nobody repeats them.

### So is D2 a real defect, or just "use the right call"?

A fair challenge, because **the data does exist** -- raw HTTP returns it,
which is why our workaround works. The defect is narrower than "the data is
unavailable":

> Through pyvergeos there is **no** call, correct or otherwise, that returns
> this document. The response object is consumed inside `_handle_response`
> and only a string survives.

Tested by applying the proposed fix at runtime -- attach the parsed body to
the exception -- and re-running the identical SDK call:

```
before:  str(e)='Simulation complete'  status=405  attrs=[args, status_code]
after :  status=405  err='Simulation complete'  log entries=28
         cloudinit=['/meta-data', '/network-config', '/user-data']
```

`str(e)` and `e.status_code` are unchanged, so nothing existing breaks.

That is the whole fix, and it really is about two lines. Whether it is worth
doing is reasonable to debate -- we already have a working bypass. The
argument for doing it is that otherwise every consumer of the SDK has to
reimplement that bypass, and each one has to know that it is needed.
**Our workaround:** we bypass pyvergeos for this one call and read the raw
reply ourselves.
**Also worth raising with the VergeOS team separately:** a successful
simulation probably should not answer with 405.

---

### D3. Users cannot be removed from any group VergeOS created
**Severity: high. Security-relevant. Was understated in an earlier draft.**

Plain: pyvergeos identifies a group member by looking for a specific text
pattern. VergeOS stores that text in two different forms, and pyvergeos
recognises only one. The form it does *not* recognise is the one VergeOS uses
for memberships it created itself — which includes **the default
Administrators group on every system**.

The practical result is not just a cosmetic misreport: **`remove_user()` and
`remove_group()` cannot remove those memberships at all.**

**Where in the code.**

| file:line | what is wrong |
|---|---|
| `resources/groups.py:42-49` | `member_type` — tests `"/users/" in ref` |
| `resources/groups.py:51-66` | `member_key` — same test, then splits on `"/users/"` |
| `resources/groups.py:202` | `add_user` verification loop depends on both |
| `resources/groups.py:233` | `add_group` verification loop |
| `resources/groups.py:263` | **`remove_user`** — the triggerable failure |
| `resources/groups.py:283` | **`remove_group`** — same |

```python
# groups.py:42-49
ref = self.member_ref
if "/users/" in ref:
    return "User"
elif "/groups/" in ref:
    return "Group"
return "Unknown"
```

**Why it fails.** The platform stores the reference verbatim, in whichever
form wrote it. Both are live in the same column of the same table:

```
client.groups.members(1).list()  ->  member = 'users/1'      (written by VergeOS)
client.groups.members(2).list()  ->  member = '/v4/users/2'  (written by add_user)
```

`'/users/' in 'users/1'` is `False`. Measured against the real Administrators
group:

```
member='users/1'  member_type='Unknown'  member_key=None  member_name='welchums'
member='users/2'  member_type='Unknown'  member_key=None  member_name='labuser'
member='users/3'  member_type='Unknown'  member_key=None  member_name='vlab'
```

`member_name` is unaffected — it reads `member_display`, a separate field. So
listing members *looks* fine, which is what hides this.

**The triggerable consequence**, reproduced in a scratch group with a
platform-form membership:

```
membership: {'$key': 4, 'parent_group': 2, 'member': 'users/2',
             'member_display': 'labuser'}
members.remove_user(2)
   -> NotFoundError: User 2 is not a member of group 2
   -> still a member: ['labuser']
```

**Why this is easy to miss.** `remove_user` raises rather than silently
succeeding, so it looks loud. But the idiomatic idempotence pattern is:

```python
try:
    members.remove_user(key)
except NotFoundError:
    pass          # "already gone"
```

which converts *"cannot remove this user"* into *"nothing to do"*. Offboarding
automation written that way reports success and changes nothing.

`add_user` currently survives only because the row it just created is in the
prefixed form it posted. That is luck, not design.

**Fix:** take the last two path segments instead of substring-matching a
prefix — handles both forms and any future one:

```python
parts = [p for p in ref.strip('/').split('/') if p]
table, key = (parts[-2], parts[-1]) if len(parts) >= 2 else ('', '')
```

**Reproduce:** `python docs/repro/d3_membership_refs.py`

**Our workaround:** `module_utils/rbac.py:split_member_ref` and
`modules/member.py:find_membership` both do exactly this.

---

## Kind 2 — missing coverage

Derived from the platform's own index, not guesswork: `GET /api/v4` returns
**780 entries — 322 tables, 437 indexes, 21 views**. pyvergeos references
**191 endpoints**, 187 of them real tables. That leaves **135 unreachable**,
or **97** after removing noise (statistics history, theming, help, console,
schema versions).

### Priority A — we work around these today (2)

| Table | What it is | Why it matters |
|---|---|---|
| `cluster_status` | live capacity: `online_ram`, `used_ram`, `online_nodes` | The one read that answers "can this cluster survive losing a node?" `clusters.py` exposes a `status` *string* and never reaches these numbers. |
| `machine_drive_stats` | per-drive I/O counters | Must be filtered on `parent_drive`, not looked up by position — 12 of 49 rows have `$key != parent_drive`. |

The `cluster_status` gap has real operational weight. Draining a node on this
lab was accepted by the platform, flipped the node to maintenance in under a
second, then stalled for fourteen minutes with nowhere to move the workloads.
One `cluster_status` read predicts it exactly:

```
online_ram 137472 MB, used_ram 87040 MB, draining node2 (69120 MB)
survivors hold 68352 MB, need 87040 MB  ->  18688 MB short
```

### Priority B — blocks planned work (about 12)

- `snapshot_profile_period_tags` + `tag_references` — tag-scoped partial
  snapshots, a headline 26.1 feature. Measured 26 rows. Tags attach per
  snapshot-profile-**period**, not per profile. `cloud_snapshots.create()` has
  no tag parameters at all.
- `cloud_snapshot_actions`, `cloud_restore`, `cloud_snapshot_files` — restoring
  from a system snapshot, file-level recovery, immutable remote snapshots.
- `schedule_tasks`, `schedule_task_settings`, `task_settings`, `task_types` —
  VergeOS's own scheduler. This lab already uses it (3 rows). Automation
  cannot currently register with it or see what is scheduled.
- `tenant_status`, `tenant_node_actions`, `tenant_recipe_actions` — tenant
  health and power/scale operations. Tenants are a VergeOS differentiator.

### Priority C — backlog (about 83)

GPU/vGPU/SR-IOV/USB/TPM passthrough, `node_allocated_gpus`, network bonds,
firewall rule aliases and cross-references, repair servers, the seven VMware
Services container tables, NAS volume health and sync progress, storage tier
statistics, SMTP delivery and queue, user devices (relevant to MFA), alarm
classification. Full list in `PYVERGEOS-GAPS.md`.

---

## Two convenience gaps worth mentioning

Not missing endpoints — just missing wrappers:

- **No `apply` action wrapper.** `update_settings` wraps `check`, `download`,
  `install` and `all`, but not `apply`, even though the endpoint accepts it
  (`refresh, download, install, apply, all`). We currently call the private
  `_action('apply')`.
- **No `simulate` support** on recipe instances — `create()` never sends the
  flag. Combined with D2, the feature is unreachable through pyvergeos.

---

## Things pyvergeos gets right — worth recording

So this does not read as a list of complaints:

- `nodes.restart()` posts to `nodes/{key}/maintenance_reboot`, documented as
  *"safely reboots the node by first migrating workloads and then restarting."*
  Drain-and-reboot is one supported operation, and we should not reimplement
  it — we tried, and got it wrong.
- `update_settings.update_all(force=)` is the supported rolling reboot:
  *"reboot nodes one at a time with workload migration."*
- Firewall rules are fully covered by `client.networks.rules`.
- The recipe endpoints all work. Only the simulate *reply* is unreachable, and
  that is D2, not a coverage gap.

---

## Recommended order

| # | Work | Size | Why first |
|---|---|---|---|
| 1 | **D3** membership reference parsing | ~3 lines | `remove_user` cannot remove anyone from a VergeOS-created group, including Administrators |
| 2 | **D2** keep the body on errors | 2 lines | The only way to reach the simulate report through the SDK |
| 3 | **D1** name escaping | 4 shared sites | 2 characters; the backslash one fails silently |
| 4 | `cluster_status` | one manager | Removes a workaround; prevents a real stall |
| 5 | `machine_drive_stats` | one manager | Removes a workaround |
| 6 | Priority B set | ~12 managers | Unblocks partial snapshots, restore, scheduling, tenants |
| 7 | Priority C | ~83 | Backlog, prioritise on demand |

**Nothing we have built is hard-blocked by a missing endpoint** — every
Priority A item is reachable via `client._request()`. The genuine blockers are
the three defects, and all three are small.
