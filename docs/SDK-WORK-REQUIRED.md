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

All three are small fixes. **This is the highest-value work by a wide margin.**

### Kind 2 — the translator does not know some words yet (about 97 items)

VergeOS can do roughly 322 things through its interface. The translator knows
about 187 of them. The rest have to be reached by going around it.

This is less urgent than it sounds. Going around the translator works fine and
we already do it where needed. Only a handful of the missing items block work
we actually want to do.

### What we need, in priority order

1. **Fix the three wrong answers.** Small, contained, high impact.
2. **Add 2 specific missing items** we currently work around.
3. **Add about 12 more** to unblock planned features.
4. **The remaining ~83** are a backlog, not a blocker.

---

## Kind 1 — defects: covered, but wrong

### D1. Searching by name breaks on ordinary punctuation
**Severity: high. Affects nearly everything.**

Plain: if you ask for something by name and the name contains an apostrophe —
`O'Brien`, `Customer's VM` — the request is rejected. Not "not found";
rejected as malformed.

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

**Fix:** escape with a backslash rather than doubling.
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

**Fix:** keep the parsed body on the exception, e.g. `APIError.body`. Callers
can then decide what a status means. This is a small, backward-compatible
addition.
**Our workaround:** we bypass pyvergeos for this one call and read the raw
reply ourselves.
**Also worth raising with the VergeOS team separately:** a successful
simulation probably should not answer with 405.

---

### D3. Group membership is misread on the most important groups
**Severity: high. Silent — no error, just wrong answers.**

Plain: pyvergeos identifies a group member by looking for a specific text
pattern. VergeOS stores that text in two different forms, and pyvergeos only
recognises one. For memberships VergeOS created itself — including the
**default Administrators group on every system** — it reports "Unknown".

Technical: `GroupMember.member_type` and `member_key` both test
`"/users/" in ref`. The platform stores the reference verbatim in whichever
form created it, and both forms are live in the same column:

```
client.groups.members(1).list()  ->  member = 'users/1'      (platform-created)
client.groups.members(2).list()  ->  member = '/v4/users/2'  (created by add_user)
```

`'/users/' in 'users/1'` is `False`, so:

```
member=users/1  ->  member_type='Unknown'   member_key=None
member=users/2  ->  member_type='Unknown'   member_key=None
member=users/3  ->  member_type='Unknown'   member_key=None
```

`member_name` is unaffected — it reads `member_display`.

**Why this has not blown up yet:** `add_user()` verifies its own work by
listing members and matching on `member_type == "User"`. It only survives
because the row it just created is in the prefixed form it posted. Had that
row been stored bare, `add_user()` would raise
`ValueError("Failed to add user to group")` **after successfully adding the
user** — reporting failure for an action that worked.

**Fix:** take the last two path segments instead of substring-matching a
prefix. Handles both forms:

```python
parts = [p for p in ref.strip('/').split('/') if p]
table, key = parts[-2], parts[-1]      # 'users', '1'
```

**Our workaround:** `module_utils/rbac.py:split_member_ref` does exactly this.

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
| 1 | **D1** name escaping | one function | Affects every name lookup, including shipped modules |
| 2 | **D2** keep the body on errors | small, additive | Unblocks the recipe feature outright |
| 3 | **D3** membership reference parsing | ~3 lines | Silently wrong on the Administrators group |
| 4 | `cluster_status` | one manager | Removes a workaround; prevents a real stall |
| 5 | `machine_drive_stats` | one manager | Removes a workaround |
| 6 | Priority B set | ~12 managers | Unblocks partial snapshots, restore, scheduling, tenants |
| 7 | Priority C | ~83 | Backlog, prioritise on demand |

**Nothing we have built is hard-blocked by a missing endpoint** — every
Priority A item is reachable via `client._request()`. The genuine blockers are
the three defects, and all three are small.
