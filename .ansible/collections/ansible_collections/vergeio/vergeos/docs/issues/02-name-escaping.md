Name lookups fail for names containing an apostrophe or a backslash

---

## Summary

pyvergeos escapes a single quote SQL-style by doubling it (`'` → `''`) when
building OData filters. VergeOS rejects that form with **HTTP 422
`Invalid argument`**.

VergeOS itself accepts both characters in names, so pyvergeos can **create** a
resource it then cannot **find**.

Two characters are affected, and they fail differently:

| character | example | VergeOS accepts | `get(name=...)` |
|---|---|---|---|
| apostrophe | `zz-obrien's-vm` | yes | `ValidationError: Invalid argument` (loud) |
| backslash | `zz-back\slash` | yes | `NotFoundError` — **for a name that exists** (silent) |

Eleven other punctuation characters were tested and all work correctly, so
this is narrow — but the backslash case is silent, which makes it the more
dangerous of the two.

Affects **1.2.3 and 1.2.4** (verified on both), against VergeOS **26.1.8**.

## Code location

84 occurrences of `replace("'", "''")` across 47 files. Four are the shared
machinery that everything else inherits; the remaining 80 are copies in
individual resource modules.

| file:line | feeds |
|---|---|
| `pyvergeos/filters.py:62` | `Filter._format_single` — the fluent builder |
| `pyvergeos/filters.py:138` | `_format_value` — used by `build_filter()` |
| `pyvergeos/filters.py:172` | the `like` / wildcard path |
| `pyvergeos/resources/base.py:184` | **`ResourceManager.get(name=...)`** — inherited by every manager |

```python
# resources/base.py:182-185
if name is not None:
    # Search by name
    escaped_name = name.replace("'", "''")
    results = self.list(filter=f"name eq '{escaped_name}'", fields=fields, limit=1)
```

`resources/groups.py:593` is an example of the copied form.

## Reproduction

### At the HTTP layer — four controls

```bash
probe() {
  curl -sk -u "$VERGEOS_USERNAME:$VERGEOS_PASSWORD" \
       -G "https://$VERGEOS_HOST/api/v4/vnets" \
       --data-urlencode "fields=name" --data-urlencode "filter=$1" \
       -w '  [HTTP %{http_code}]\n'
}
probe "name eq 'DMZ'"            # control, exists
probe "name eq 'zz-nope'"        # control, absent
probe "name eq 'zz-o''brien'"    # what pyvergeos builds
probe "name eq 'zz-o\'brien'"    # backslash form
```

```
[{"name":"DMZ"}]             [HTTP 200]
[]                           [HTTP 200]
{"err":"Invalid argument"}   [HTTP 422]   <-- pyvergeos's form
[]                           [HTTP 200]   <-- backslash works
```

### Through the SDK

```python
c.groups.create(name="zz-d1-o'brien")     # VergeOS accepts it
c.groups.get(name="zz-d1-o'brien")        # ValidationError: Invalid argument
```

Every documented lookup route fails on the same name:

```
groups.get(name=...)                 -> ValidationError: Invalid argument
groups.list(filter=build_filter(...)) -> ValidationError: Invalid argument
groups.list(name=...)                -> ValidationError: Invalid argument
groups.list(LIKE wildcard)           -> ValidationError: Invalid argument
```

All three builders produce the same broken string, including the `like` path:

```
build_filter(name="o'brien")        -> name eq 'o''brien'
Filter().eq('name', "o'brien")      -> name eq 'o''brien'
build_filter(name="o'*rien")        -> name like 'o''%rien'
```

Confirmed identical on `users`, `groups`, `vms` and `networks`.

### The silent case, which matters more

```python
c.groups.create(name="zz-d1-back\\slash")   # accepted by VergeOS
c.groups.get(name="zz-d1-back\\slash")      # NotFoundError -- but it exists
```

The standard idempotence pattern then tries to create it again:

```
get    -> NotFoundError: Group 'zz-d1-back\slash' not found
create -> ConflictError: A user/group with this name already exists
```

So an idempotent re-run **fails** on a name VergeOS accepts. On a table
without a uniqueness constraint it would create a duplicate instead.

## Expected behaviour

A name containing an apostrophe or backslash should be found if it exists, and
raise `NotFoundError` only if it genuinely does not.

## Suggested fix

Escape with a backslash rather than doubling. Verified against the live API —
the backslash form returns the row:

```python
def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
```

Fixing `filters.py:62`, `:138`, `:172` and `base.py:184` covers the shared
paths. The other 80 occurrences are copy-paste and would ideally call one
shared helper so this cannot drift again.

Worth a regression test for both characters, since they fail differently —
one raises and one silently returns nothing.

## Scope note

This was initially assumed to affect punctuation generally. It does not.
Measured by creating a group per character and looking it up again:

```
VergeOS accepted 13 of 13 names.
pyvergeos could not find 2 of them: apostrophe, backslash
```

`%` and `_` were expected to break as SQL wildcards and do not, because the
lookup uses `eq` rather than `like`.

Real-world exposure is therefore limited to names that come from people or
from another system — tenants and VMs named after customers, NAS shares,
imported AD-style names — rather than machine-generated names.

## Environment

- pyvergeos 1.2.3 and 1.2.4 (identical behaviour)
- VergeOS 26.1.8
- Python 3.14
