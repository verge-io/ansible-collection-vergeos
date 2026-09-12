A group created within ~4 seconds of another group's deletion cannot accept members

---

> **VergeOS platform issue, not pyvergeos.** The SDK sends a correct request
> and reports the API's own error faithfully. Everything below was produced
> with `curl`, with no SDK involved.

## Summary

Deleting a group opens a window of roughly four seconds. A group created
inside that window is created successfully, is indistinguishable from a
healthy group in every API field, and cannot accept members:

```
HTTP 404
{"err":"Error creating member in system table: error setting field
        'members.group': No such file or directory"}
```

It does not recover on its own — still failing after 60 seconds. Creating
another group makes it *appear* to work, but that is masking, not repair:
delete the masking group and the affected group fails again.

Observed on **VergeOS 26.1.8** (`conundrum-lab`, 2-node).

## The rules, as measured

1. A **group deletion** opens a window a little under 4 seconds long. Nothing
   else opens one — not a group create, not a group edit, not creating or
   deleting a user.
2. The window is timed from the **delete**. Creating a group does not restart
   it.
3. The **last** group created inside that window cannot accept members.
   Groups created earlier in the same window are fine.
4. That group never recovers by itself.
5. Creating another group makes it accept members again — but this is a
   **mask, not a repair**. The specific group created next after it is the
   masking one; delete that group and the affected group fails again. Any
   further group create masks it once more.
6. If that masking group is itself the last one created inside an open window,
   it is permanently defective too.
7. **Existing memberships are never lost.** They stay listed and usable. Only
   new member inserts fail, so an affected group can work for weeks and then
   start rejecting members after an unrelated group deletion.
8. Only the `members.group` link is affected. Everything else about the group
   — including being added as a member of *another* group — works.

## Reproduction

Run against a system with no group activity for ten seconds or so. `$VOS_USER`,
`$VOS_PASS` and `$VOS_HOST` are shell variables; nothing else is needed.

### 1. Baseline — a group created normally accepts members

```console
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"name":"zz-healthy"}' \
       'https://$VOS_HOST/api/v4/groups'
{"location":"\/v4\/groups\/2","dbpath":"groups\/2","$row":2,"$key":"2"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"parent_group":2,"member":"users/2"}' \
       'https://$VOS_HOST/api/v4/members'
{"location":"\/v4\/members\/4","dbpath":"members\/4","$row":4,"$key":"4"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/members/4'
[]
HTTP 200

```

### 2. Arming it — create a group, delete it, create another within four seconds

```console
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"name":"zz-trigger"}' \
       'https://$VOS_HOST/api/v4/groups'
{"location":"\/v4\/groups\/3","dbpath":"groups\/3","$row":3,"$key":"3"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/groups/3'
[]
HTTP 200

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"name":"zz-victim"}' \
       'https://$VOS_HOST/api/v4/groups'
{"location":"\/v4\/groups\/4","dbpath":"groups\/4","$row":4,"$key":"4"}
HTTP 201

```

### 3. The affected group looks completely normal

```console
# every field the API will return, for the group that is about to fail
$ curl -sk -u "$VOS_USER:$VOS_PASS" \
       'https://$VOS_HOST/api/v4/groups/4?fields=all' | python3 -m json.tool
{
    "$key": 4,
    "name": "zz-victim",
    "enabled": true,
    "id": "zz-victim",
    "email": "",
    "created": 1789159583,
    "description": "",
    "members": [],
    "membership": [],
    "identity": "8",
    "auth_source": null,
    "permissions": [],
    "system_permissions": [],
    "subscriptions": [],
    "oidc_application_groups": [],
    "logs": [
        {
            "$key": 29
        }
    ],
    "system_group": false,
    "tags": [],
    "creator": "welchums"
}

# and the healthy group from step 1, for comparison — the only differences
# are the key, the name and the identity number
$ curl -sk -u "$VOS_USER:$VOS_PASS" \
       'https://$VOS_HOST/api/v4/groups/2?fields=all' | python3 -m json.tool
{
    "$key": 2,
    "name": "zz-healthy",
    "enabled": true,
    "id": "zz-healthy",
    "email": "",
    "created": 1789159582,
    "description": "",
    "members": [],
    "membership": [],
    "identity": "7",
    "auth_source": null,
    "permissions": [],
    "system_permissions": [],
    "subscriptions": [],
    "oidc_application_groups": [],
    "logs": [
        {
            "$key": 24
        },
        {
            "$key": 27
        },
        {
            "$key": 28
        }
    ],
    "system_group": false,
    "tags": [],
    "creator": "welchums"
}

```

### 4. Adding a user to it fails, permanently

```console
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"parent_group":4,"member":"users/1"}' \
       'https://$VOS_HOST/api/v4/members'
{"err":"Error creating member in system table: error setting field 'members.group': No such file or directory"}
HTTP 404

# the identical request against the healthy group, to show the request
# itself and the user are both fine
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"parent_group":2,"member":"users/1"}' \
       'https://$VOS_HOST/api/v4/members'
{"location":"\/v4\/members\/4","dbpath":"members\/4","$row":4,"$key":"4"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/members/4'
[]
HTTP 200

```

### 5. Every other operation on the affected group succeeds

```console
# put the affected group INSIDE another group. Same members table, same
# group, the other column — and it works.
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"parent_group":1,"member":"groups/4"}' \
       'https://$VOS_HOST/api/v4/members'
{"location":"\/v4\/members\/4","dbpath":"members\/4","$row":4,"$key":"4"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/members/4'
[]
HTTP 200

# grant it a permission
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"identity":"8","table":"/","row":0,"list":true,"read":true}' \
       'https://$VOS_HOST/api/v4/permissions'
{"location":"\/v4\/permissions\/7","dbpath":"permissions\/7","$row":7,"$key":"7"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/permissions/7'
[]
HTTP 200

# edit it
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X PUT \
       -H 'Content-Type: application/json' \
       -d '{"description":"still perfectly editable"}' \
       'https://$VOS_HOST/api/v4/groups/4'
[]
HTTP 200

# list its members
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X GET \
       'https://$VOS_HOST/api/v4/members?fields=all&filter=parent_group%20eq%204'
[]
HTTP 200

```

### 6. Waiting does not repair it

```console
# sleep 60
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"parent_group":4,"member":"users/1"}' \
       'https://$VOS_HOST/api/v4/members'
{"err":"Error creating member in system table: error setting field 'members.group': No such file or directory"}
HTTP 404

```

### 7. Creating another group makes it work again (temporarily — see below)

```console
# the window closed long ago, so this new group is itself healthy
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"name":"zz-next"}' \
       'https://$VOS_HOST/api/v4/groups'
{"location":"\/v4\/groups\/3","dbpath":"groups\/3","$row":3,"$key":"3"}
HTTP 201

# the group that failed for the last two minutes now works
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"parent_group":4,"member":"users/1"}' \
       'https://$VOS_HOST/api/v4/members'
{"location":"\/v4\/members\/4","dbpath":"members\/4","$row":4,"$key":"4"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/members/4'
[]
HTTP 200

# and so does the group just created
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"parent_group":3,"member":"users/3"}' \
       'https://$VOS_HOST/api/v4/members'
{"location":"\/v4\/members\/4","dbpath":"members\/4","$row":4,"$key":"4"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/members/4'
[]
HTTP 200

```

### 8. But inside the window, the LAST group created is the broken one

```console
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/groups/4'
[]
HTTP 200

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/groups/3'
[]
HTTP 200

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/groups/2'
[]
HTTP 200

# settle for 8 seconds, then: create, delete, create A, create B
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"name":"zz-trigger"}' \
       'https://$VOS_HOST/api/v4/groups'
{"location":"\/v4\/groups\/2","dbpath":"groups\/2","$row":2,"$key":"2"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/groups/2'
[]
HTTP 200

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"name":"zz-a"}' \
       'https://$VOS_HOST/api/v4/groups'
{"location":"\/v4\/groups\/3","dbpath":"groups\/3","$row":3,"$key":"3"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"name":"zz-b"}' \
       'https://$VOS_HOST/api/v4/groups'
{"location":"\/v4\/groups\/4","dbpath":"groups\/4","$row":4,"$key":"4"}
HTTP 201

# A was created inside the window too, but B followed it — A is fine
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"parent_group":3,"member":"users/1"}' \
       'https://$VOS_HOST/api/v4/members'
{"location":"\/v4\/members\/4","dbpath":"members\/4","$row":4,"$key":"4"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/members/4'
[]
HTTP 200

# B is the last group created inside the window, and B is the broken one
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"parent_group":4,"member":"users/2"}' \
       'https://$VOS_HOST/api/v4/members'
{"err":"Error creating member in system table: error setting field 'members.group': No such file or directory"}
HTTP 404

```

### 9. The workaround — delete it, wait, create it again

```console
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/groups/4'
[]
HTTP 200

# sleep 6
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"name":"zz-b"}' \
       'https://$VOS_HOST/api/v4/groups'
{"location":"\/v4\/groups\/2","dbpath":"groups\/2","$row":2,"$key":"2"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X POST \
       -H 'Content-Type: application/json' \
       -d '{"parent_group":2,"member":"users/2"}' \
       'https://$VOS_HOST/api/v4/members'
{"location":"\/v4\/members\/4","dbpath":"members\/4","$row":4,"$key":"4"}
HTTP 201

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/members/4'
[]
HTTP 200

```

### 10. Clean up

```console
$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/groups/3'
[]
HTTP 200

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X DELETE \
       'https://$VOS_HOST/api/v4/groups/2'
[]
HTTP 200

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X GET \
       'https://$VOS_HOST/api/v4/groups?fields=$key,name'
[{"$key":1,"name":"Administrators (default)"}]
HTTP 200

$ curl -sk -u "$VOS_USER:$VOS_PASS" -X GET \
       'https://$VOS_HOST/api/v4/members?fields=all'
[{"$key":1,"parent_group":1,"member":"users\/1","system":false,"creator":""},{"$key":2,"parent_group":1,"member":"users\/2","system":false,"creator":"node1"},{"$key":3,"parent_group":1,"member":"users\/3","system":false,"creator":"welchums"}]
HTTP 200

```

## Measurements

### The window is a little under four seconds, and the edge is not sharp

Delay between deleting a group and creating the next one, with nothing else in
between. Six runs at each delay, each from a settled system:

```
wait   r1  r2  r3  r4  r5  r6
1      X   X   X   X   X   X      6/6 affected
2      X   X   X   X   X   X      6/6 affected
2.5    X   X   X   X   X   X      6/6 affected
3      X   X   X   X   X   X      6/6 affected
3.5    .   X   .   .   X   .      2/6 affected
4      .   .   .   .   .   .      0/6 affected
5      .   .   .   .   .   .      0/6 affected

X = cannot add members,  . = fine
```

Up to 3 seconds it is reliable; 3.5 seconds is a coin toss; 4 seconds and
beyond was never affected in twelve attempts. One isolated 3-second sample in
a separate run also came back clean, so treat the edge as jittery rather than
exact. Any workaround should allow margin — we wait 6 seconds.

### The clock runs from the delete, not from each create

| sequence | A | B |
|---|---|---|
| delete, create A, create B (both inside) | OK | **DEFECT** |
| delete, create A, create B, create C (all inside) | OK / OK | **DEFECT** (C) |
| delete, wait 2s, create A, wait 2.5s, create B | OK | OK |
| delete, create A, wait 10s, create B | OK | OK |

Row 3 is the discriminator: B is 4.5s after the delete (outside the window)
but only 2.5s after A (well inside one, if a create restarted the clock). B is
fine, so the clock is not restarted. Row 4 shows a group created after the window has closed is healthy,
and repairs A in passing.

### It never heals on its own

Same affected group, left alone and retried:

| elapsed | result |
|---|---|
| immediately | DEFECT |
| 2s | DEFECT |
| 5s | DEFECT |
| 10s | DEFECT |
| 30s | DEFECT |
| 60s | DEFECT |

### What does and does not open a window

Each row is a separate run from a quiet system, with the member add attempted
immediately after the last step and nothing in between:

| sequence | member add |
|---|---|
| create Y | OK |
| create X, create Y | OK |
| **create X, DELETE X, create Y** | **DEFECT** |
| create X, wait 4s, create Y | OK |
| create X, DELETE X, wait 4s, create Y | OK |
| create USER, create Y | OK |
| create USER, delete USER, create Y | OK |
| create Y, update Y | OK |

Only a **group delete** does it. Users and groups share the identity sequence,
so a user deletion frees an identity a group can then claim — and that case
works.

### What makes it start working again

The affected group is left alone and one action is performed before the member
add is attempted:

| action | member add |
|---|---|
| nothing (control) | DEFECT |
| wait 60s | DEFECT |
| create a user | DEFECT |
| update the affected group | DEFECT |
| update a different group | DEFECT |
| list groups (read only) | DEFECT |
| add a member to a *different* group | DEFECT |
| **create another group** | **OK** |

### …but that is masking, not repair

The group create does not fix anything. It hides the fault for exactly as long
as the group that did the hiding still exists. Five consecutive runs, plus a
control using a group that was never affected:

```
arm -> G1 defective -> wait 10s -> create G2 -> delete G2 -> probe G1

  run 1: G1 defective=DEFECT  after G2=OK  after deleting G2=DEFECT
  run 2: G1 defective=DEFECT  after G2=OK  after deleting G2=DEFECT
  run 3: G1 defective=DEFECT  after G2=OK  after deleting G2=DEFECT
  run 4: G1 defective=DEFECT  after G2=OK  after deleting G2=DEFECT
  run 5: G1 defective=DEFECT  after G2=OK  after deleting G2=DEFECT

control (G1 never affected — created in a quiet period):
  run 1: G1 start=OK  after G2=OK  after deleting G2=OK
  run 2: G1 start=OK  after G2=OK  after deleting G2=OK
  run 3: G1 start=OK  after G2=OK  after deleting G2=OK
```

The masking is durable while the masking group exists — still working after 60
seconds — and it is that **specific** group that matters, not simply the newest
one:

```
G1 defective, then G2, then G3 created
  with G2 and G3 present   -> OK
  after deleting G3        -> OK        (G3 is newest, but irrelevant)
  after deleting G2        -> DEFECT    (G2 came right after G1)

  after creating G4        -> OK        (G4 is now the masking group)
  after deleting G3        -> OK        (still irrelevant)
  after deleting G4        -> DEFECT
```

A group deletion cannot break a group that was never affected. Three healthy,
well-spaced groups, deleting the newest and then the oldest, left every
survivor working.

### Existing memberships survive

```
add two members while masked -> HTTP 201, HTTP 201
members: ['users/1', 'users/2']
delete the masking group
members still listed: ['users/1', 'users/2']
adding a THIRD member       -> DEFECT
```

Nothing is lost and nothing looks wrong. The group simply stops accepting new
members.

### Deleting and recreating the group is a genuine fix

```
defective group                        -> DEFECT
delete it, wait 6s, create it again    -> OK
after an unrelated group create+delete -> OK
after another one                      -> OK
```

Unlike masking, this survives later group churn.

## Scope: no other object type behaves this way

The same create / delete / create / insert-a-child sequence was run against
every object type in the lab that can be created and deleted cheaply —
fourteen parent/child pairs across nine types, each run twice, once with no
preceding delete and once with one.

| parent -> child | control | after delete + create |
|---|---|---|
| **groups -> members** (a user into the group) | OK | **DEFECT** |
| groups -> permissions | OK | OK |
| groups -> members (the group *as* a member of another) | OK | OK |
| users -> user_api_keys | OK | OK |
| users -> members (the user into an existing group) | OK | OK |
| tag_categories -> tags | OK | OK |
| snapshot_profiles -> snapshot_profile_periods | OK | OK |
| vnets -> vnet_rules | OK | OK |
| vnets -> vnet_dns_views | OK | OK |
| vnets -> vnet_addresses | OK | OK |
| vnet_dns_views -> vnet_dns_zones | OK | OK |
| vms -> machine_nics | OK | OK |
| vms -> machine_drives | OK | OK |
| tenants -> tenant_storage | OK | OK |

One pair out of fourteen. Deleting and recreating a user, vnet, VM, tenant,
tag category or snapshot profile and then using it is fine.

### It is one column, not one object

Step 5 of the transcript above is the sharpest version of that test: a single
affected group, in the same second, written into two different columns of the
**same** `members` table.

| the affected group is referenced as | result |
|---|---|
| `members.member` — it is a member of another group | **OK** (HTTP 201) |
| `members.group` — it is the group holding a member | **DEFECT** (HTTP 404) |

The group's record is not unreachable. The platform finds it perfectly well
when resolving `members.member`. Only the `members.group` lookup fails — which
is exactly the field the error message names.

## Three explanations that were tested and are wrong

Recorded because each sounded right and each was a working conclusion here at
some point.

**Not identity reuse.** The group that reclaimed the deleted group's identity
worked; a later group with a brand-new identity failed:

```
A id=7, B id=8
delete A          -> identity 7 is free
C id=7  (reused)  -> add member OK
D id=9  (fresh)   -> add member FAIL
```

**Not a transient race.** Sixty seconds of waiting does not repair the
affected group, and retrying the member add is useless — a retry loop
identified the error correctly six times over ten seconds and every attempt
failed.

**Not "each new group arms itself."** This was a previous conclusion here and
it is wrong. A group created after the window has closed is healthy — step 7 of
the transcript. The appearance of the fault "rolling forward" only occurs when
several groups are created inside a single window, where the last one is the
affected one.

**Not "creating another group repairs it."** Also a previous conclusion here,
also wrong, and this is the one that matters most: the later group only masks
the fault, for exactly as long as that group exists. Step 7 of the transcript
looks like a repair and is not one.

## Blast radius

On an affected group, everything except the member insert works:

| operation | result |
|---|---|
| add a member | **DEFECT** |
| be added as a member of another group | OK |
| grant a permission on it | OK |
| rename / update | OK |
| read it back | OK |
| list its members | OK |
| delete it | OK |

Comparing an affected group with a healthy one using `fields=all` — step 3 of
the transcript — **every field matches** except the key, the name and the
identity number. There is no flag, state or counter that reveals the problem.
It can only be discovered by trying to add a member.

The failed insert is also **silent in the audit log**. A successful add writes
`Added '<user>' to members`; a failed one writes nothing at all, so there is no
trace of the attempt in the group's log or the system log.

## Why this matters

- The error message points nowhere near the cause. *"No such file or
  directory"* about a group that plainly exists is not a searchable symptom.
- The window is entered by ordinary use. Configuration-management tools delete
  and create groups in the same run routinely — we hit it with two Ansible
  playbooks run back-to-back.
- Because the group looks healthy and does not self-heal, the natural
  remediations (retry, wait, re-run) all fail.
- A re-run often *appears* to fix it, because it creates another group. On a
  system where groups are being created and deleted in the same run, that can
  move the failure to a different group rather than resolving it.

## Expected behaviour

A group that has been created successfully should accept members, regardless
of what was deleted immediately beforehand.

## Workaround

Leave at least four seconds (we use six) between deleting a group and creating
one.

A group already created inside the window cannot be repaired in place. **Do not
rely on creating a decoy group** — that only masks the fault until the decoy is
deleted. Delete the affected group, wait, and create it again, as in step 9
above; that is a genuine fix and survives later group churn.

## Reproduction scripts

Both need only the Python standard library. No SDK, no `pip install`.

- `docs/repro/04_group_member_defect_http.py` — the window, the lack of
  self-healing, and the workaround.
- `docs/repro/04b_group_member_scope.py` — the fourteen parent/child pairs and
  the two-column test.

## Environment

- VergeOS 26.1.8
- Reproduced with `curl`, with Python's `urllib`, and through pyvergeos
  1.2.4 and 1.2.5 — identical behaviour in all four
- Request body is `{"parent_group": <key>, "member": "users/<key>"}`; the
  `/v4/users/<key>` form behaves identically
