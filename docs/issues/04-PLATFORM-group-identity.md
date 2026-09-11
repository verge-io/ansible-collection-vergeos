A group created within ~3 seconds of another group's deletion can never accept members

---

> **VergeOS platform issue, not pyvergeos.** The SDK sends a correct request
> and reports the API's own error faithfully. Reproduced identically through
> pyvergeos 1.2.4, 1.2.5 and raw HTTP.

## Summary

Deleting a group leaves the platform in a state where **the next group
created is permanently unable to accept members**. The group is created
successfully, is indistinguishable from a healthy group in every API field,
and every attempt to add a member to it fails:

```
HTTP 404
{"err":"Error creating member in system table: error setting field
        'members.group': No such file or directory"}
```

It does not recover on its own — still failing after 60 seconds. It is
repaired only when the *next* group is created, which then becomes the
affected one.

Observed on **VergeOS 26.1.8**.

## Minimal reproduction

```python
g = client.groups.create(name="trigger")
client.groups.delete(int(dict(g)["$key"]))     # arms it

victim = client.groups.create(name="victim")   # within ~3s
client.groups.members(int(dict(victim)["$key"])).add_user(user_key)
# -> 404 "error setting field 'members.group'"
```

Equivalently over HTTP: `POST /api/v4/groups`, `DELETE /api/v4/groups/<key>`,
`POST /api/v4/groups`, then
`POST /api/v4/members {"parent_group": <new key>, "member": "/v4/users/2"}`.

## Measurements

### The window is 3 to 4 seconds

Delay between deleting a group and creating the next one:

| wait | member add |
|---|---|
| 0s | **DEFECT** |
| 0.5s | **DEFECT** |
| 1s | **DEFECT** |
| 2s | **DEFECT** |
| 3s | **DEFECT** |
| 4s | OK |
| 5s | OK |
| 6s | OK |

### It never heals on its own

Same affected group, retried:

| elapsed | result |
|---|---|
| immediately | DEFECT |
| 10s | DEFECT |
| 30s | DEFECT |
| 60s | DEFECT |
| **after creating another group** | **OK** |

### It rolls forward

Creating the next group repairs the previous one and arms itself. Each row
re-tests every group created so far, using a distinct user per group so a
success cannot mask a later probe as a duplicate:

```
after creating id=7   :  id7=DEFECT
after creating id=8   :  id7=OK      id8=DEFECT
after creating id=9   :  id8=OK      id9=DEFECT
after creating id=10  :  id9=OK      id10=DEFECT
```

## Two explanations that are wrong

Both are recorded because they sound right and were the first conclusions
drawn here.

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

## What does and does not arm it

| action | arms the defect |
|---|---|
| deleting a **group** | **yes** |
| deleting a **user** | no — a group created straight afterwards works |
| creating groups back-to-back with no delete | no — six in a row, all fine |
| whether the deleted group had members | no difference |

Users and groups share the identity sequence, so a user deletion frees an
identity a group can then claim — and that case works. This is specific to
group deletion.

## Blast radius

On an affected group, everything except the member insert works:

| operation | result |
|---|---|
| add a member | **DEFECT** |
| rename / update | OK |
| read it back | OK |
| list its members | OK |
| grant a permission on its identity | OK |
| delete it | OK |

Comparing an affected group with a healthy one using `fields=all`, **every
field matches** — there is no flag, state or counter that reveals the
problem. It can only be discovered by trying to add a member.

## Why this matters

- The error message points nowhere near the cause. *"No such file or
  directory"* about a group that plainly exists is not a searchable symptom.
- The window is entered by ordinary use, not by anything exotic.
  Configuration-management tools delete and create groups in the same run
  routinely — we hit it with two Ansible playbooks run back-to-back.
- Because the group looks healthy and the damage is permanent, the natural
  remediations (retry, wait, re-run the playbook) all fail.
- A re-run *appears* to fix it, but only because creating another group
  repairs the previous one. The new group is then broken instead, so the
  problem seems to move rather than resolve.

## Expected behaviour

A group that has been created successfully should accept members, regardless
of what was deleted immediately beforehand.

## Workaround

Leave at least four seconds (we use six) between deleting a group and
creating one. A group already created inside the window cannot be repaired in
place — delete it, wait, and create it again.

## Environment

- VergeOS 26.1.8
- Reproduced through pyvergeos 1.2.4, 1.2.5 and directly over HTTP
- Request body is `{"parent_group": <key>, "member": "/v4/users/<key>"}` in
  every case
