Group created shortly after another group is deleted is permanently unable to accept members

---

> **This is a VergeOS platform issue, not a pyvergeos one.** The SDK sends a
> correct request and reports the API's own error faithfully. Filed separately
> so it reaches the right team.

## Summary

Deleting a group releases its internal identity asynchronously. A group
created within roughly five seconds of that deletion claims the identity
before the platform has finished releasing it. The group is created
successfully and looks normal, but **every attempt to add a member to it fails
permanently**:

```
HTTP 404
{"err":"Error creating member in system table: error setting field
        'members.group': No such file or directory"}
```

Waiting afterwards never repairs it. The group has to be deleted and
recreated.

Observed on **VergeOS 26.1.8**.

## Reproduction

```python
g = client.groups.create(name="trigger")
client.groups.delete(int(dict(g)["$key"]))        # opens the window

victim = client.groups.create(name="victim")      # immediately
k = int(dict(victim)["$key"])
client.groups.members(k).add_user(some_user_key)  # 404, permanently
```

Equivalent with curl: `POST /api/v4/groups`, `DELETE /api/v4/groups/<key>`,
`POST /api/v4/groups`, then
`POST /api/v4/members {"parent_group": <new key>, "member": "/v4/users/2"}`.

## Measurements

**The delay that matters is between the delete and the create:**

| sequence | result |
|---|---|
| delete → **wait 6s** → create → add member | **OK** |
| delete → create immediately → add member after 0s | FAIL |
| delete → create immediately → add member after 5s | FAIL |
| delete → create immediately → add member after 15s | FAIL |
| delete → create immediately → add member after 30s | FAIL |

**The window is short:**

| wait between delete and create | add member |
|---|---|
| 0s | FAIL |
| 1s | FAIL |
| 5s | OK |
| 15s | OK |
| 30s | OK |

**Identity reuse on its own is not the problem.** Six groups created
back-to-back with no intervening delete, taking identities 7 through 12, all
accepted members:

```
key=2 identity=7  add_user=OK      key=5 identity=10 add_user=OK
key=3 identity=8  add_user=OK      key=6 identity=11 add_user=OK
key=4 identity=9  add_user=OK      key=7 identity=12 add_user=OK
```

Whereas create → add → delete repeated in a loop, where each new group reuses
the freed identity 7, fails every time after the first.

## Why it is easy to hit and hard to diagnose

- Nothing about the group looks wrong. It is listed, has the right name, and
  reports `member_count: 0`.
- The error message — *"No such file or directory"* about a group that plainly
  exists — points nowhere near the cause.
- Configuration-management tools routinely delete and create groups in the
  same run, so the window is entered by ordinary use rather than by anything
  unusual. We hit it with two Ansible playbooks run back to back.
- The damage is permanent, so a retry does not help. Our first mitigation
  retried the member add six times over ten seconds; it correctly identified
  the error every time and every attempt failed.

## Expected behaviour

Either the identity should not be offered to a new group until it has been
fully released, or the member insert should succeed once the group exists.

## Workaround

Leave at least six seconds between deleting a group and creating another. If a
group has already been created inside the window, delete it, wait, and create
it again — its membership cannot be repaired in place.

## Environment

- VergeOS 26.1.8
- Reproduced through both pyvergeos 1.2.4 and 1.2.5, and directly over HTTP
- Independent of SDK version: the request body is
  `{"parent_group": <key>, "member": "/v4/users/<key>"}` in all cases
