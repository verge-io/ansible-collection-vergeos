remove_user() and remove_group() cannot remove any membership VergeOS created itself

---

## Summary

`GroupMember.member_type` and `GroupMember.member_key` recognise only one of
the two reference formats VergeOS stores in `members.member`. For the format
they do not recognise, both return `Unknown` / `None`.

Because `remove_user()`, `remove_group()`, `add_user()` and `add_group()` all
match on those two properties, **a user or group cannot be removed from any
group whose membership VergeOS created** — which includes the default
**Administrators** group on every system. The call raises
`NotFoundError: User <n> is not a member of group <m>` about a member that
demonstrably is.

Affects **1.2.3 and 1.2.4** (verified on both), against VergeOS **26.1.8**.

## Root cause

VergeOS stores the member reference **verbatim, in whichever format wrote
it**. Both formats are live in the same column of the same table at the same
time:

```
client.groups.members(1).list()   ->  member = 'users/1'       (written by VergeOS)
client.groups.members(2).list()   ->  member = '/v4/users/2'   (written by add_user)
```

`pyvergeos/resources/groups.py:42-66` tests for a leading-slash prefix:

```python
@property
def member_type(self) -> str:
    """Get the member type ('User' or 'Group')."""
    ref = self.member_ref
    if "/users/" in ref:          # <-- False for 'users/1'
        return "User"
    elif "/groups/" in ref:
        return "Group"
    return "Unknown"
```

`'/users/' in 'users/1'` is `False`, so `member_type` returns `'Unknown'` and
`member_key` returns `None`.

`member_name` is unaffected because it reads a different field
(`member_display`), which is why **listing members looks perfectly correct**
and hides this.

## Affected call sites

| line | method | effect |
|---|---|---|
| `groups.py:202` | `add_user` | verification loop — survives by luck, see below |
| `groups.py:233` | `add_group` | same |
| `groups.py:263` | **`remove_user`** | cannot find the member; raises `NotFoundError` |
| `groups.py:283` | **`remove_group`** | same |

`add_user()` and `add_group()` survive only because the row they just created
is in the prefixed format they posted (`groups.py:194`, `:224`). If the
platform ever stored it bare, they would raise
`ValueError("Failed to add user to group")` **after successfully adding the
user**.

## Reproduction

Read-only apart from two scratch groups, which are deleted.

```python
import os
from pyvergeos import VergeClient
from pyvergeos.exceptions import NotFoundError

c = VergeClient(host=os.environ["VERGEOS_HOST"],
                username=os.environ["VERGEOS_USERNAME"],
                password=os.environ["VERGEOS_PASSWORD"], verify_ssl=False)
c.connect()

# --- 1. the default Administrators group, as VergeOS created it ---
for m in c.groups.members(1).list():
    print(dict(m).get("member"), m.member_type, m.member_key, m.member_name)

# --- 2. an A/B on one scratch group ---
parent = c.groups.create(name="zz-probe-parent")
child  = c.groups.create(name="zz-probe-child")
pk, ck = int(dict(parent)["$key"]), int(dict(child)["$key"])
mgr = c.groups.members(pk)

mgr.add_group(ck)                       # writes '/v4/groups/<ck>'
mgr.remove_group(ck)                    # works

c._request("POST", "members",           # writes 'groups/<ck>', as VergeOS does
           json_data={"parent_group": pk, "member": "groups/%d" % ck})
try:
    mgr.remove_group(ck)                # same call, same row, different format
except NotFoundError as e:
    print("FAILED:", e)
    print("still a member:", [dict(r).get("member_display") for r in mgr.list()])

for k in (pk, ck):
    c.groups.delete(k)
```

### Actual output (pyvergeos 1.2.4, VergeOS 26.1.8)

```
users/1 Unknown None welchums
users/2 Unknown None labuser
users/3 Unknown None vlab

add_group() row: {'$key': 4, 'parent_group': 2, 'member': '/v4/groups/3', ...}
   -> member_type='Group' member_key=3
   remove_group() -> removed OK

membership rewritten platform-style: [{'member': 'groups/3', ...}]
   -> member_type='Unknown' member_key=None member_name='zz-d3-child'
   remove_group() -> NotFoundError: Group 3 is not a member of group 2
   still a member: ['zz-d3-child']
```

The A/B is the point: **same group, same call, same member — only the stored
string format differs.**

## Expected behaviour

`member_type` / `member_key` should resolve both formats, so `remove_user()`
and `remove_group()` work regardless of which component created the
membership.

## Why this is easy to miss

`remove_user()` raises rather than silently succeeding, so it looks loud. But
the idiomatic idempotence pattern is:

```python
try:
    members.remove_user(key)
except NotFoundError:
    pass          # "already gone"
```

which converts *"cannot remove this user"* into *"nothing to do"*. Offboarding
automation written that way reports success and removes nobody — hence the
security-relevant framing.

## Suggested fix

Parse the trailing path segments instead of substring-matching a prefix.
Handles both current formats and is robust to a third:

```python
def _split_ref(ref: str) -> tuple[str, str]:
    parts = [p for p in str(ref or "").strip("/").split("/") if p]
    if len(parts) < 2:
        return "", ""
    return parts[-2], parts[-1]     # ('users', '1')


@property
def member_type(self) -> str:
    table, _ = _split_ref(self.member_ref)
    return {"users": "User", "groups": "Group"}.get(table, "Unknown")


@property
def member_key(self) -> int | None:
    _, key = _split_ref(self.member_ref)
    try:
        return int(key)
    except (TypeError, ValueError):
        return None
```

A regression test should assert both `'users/1'` and `'/v4/users/1'` resolve
to `("User", 1)`.

## Environment

- pyvergeos 1.2.3 and 1.2.4 (identical behaviour)
- VergeOS 26.1.8
- Python 3.14
