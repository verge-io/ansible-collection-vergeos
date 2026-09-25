# pyvergeos compatibility window

`requirements.txt` floors at `pyvergeos>=1.2.8`. That pin is the brace fix,
not a new set of managers. This file records what the collection was
actually verified against, and which SDK defects that verification depends
on.

## The floor

| Version | What it is |
|---|---|
| 1.2.3 | First release that exposes every manager the modules call (`physical_drives`) |
| 1.2.5 | OData apostrophe escape (pyvergeos#72) |
| 1.2.7 | Previous floor. Last release published before the brace fix. `quote_value()` still did not escape `{` |
| **1.2.8** | `quote_value()` escapes `{` (pyVergeOS#100, PR #114). Prepared in pyVergeOS#122 and **not published to PyPI** |
| **1.6.1** | First release PyPI publishes at or above 1.2.8. Contains the brace fix, plus the 1.4 through 1.6 refactors (declarative accessors, int-coercion on polymorphic refs, `cluster_status` and `machine_drive_stats`, partial snapshots, snapshot-wait). Verified through this version |

`>=1.2.8` therefore installs **1.6.1** from PyPI today. There is no 1.3, 1.4
or 1.5 package, and there is no 1.2.8 package.

## What was tested

Two SDKs, same lab (VergeOS 26.1.8), same collection build, same 15 live
ladders, run back to back on 2026-09-22. At that date `origin/dev` was 22
commits ahead of the 1.2.7 release and did not yet contain the 1.6.1 cut:

| Ladder | 1.2.7 | `origin/dev` (2026-09-22) |
|---|---|---|
| `verify-catalog` | `ok=34 changed=11` | `ok=34 changed=11` |
| `verify-api-key` | `ok=15 changed=3` | `ok=15 changed=3` |
| `verify-snapshot-profile` | `ok=12 changed=4` | `ok=12 changed=4` |
| `verify-vnet-rule` | `ok=15 changed=6` | `ok=15 changed=6` |
| `verify-file` | `ok=19 changed=6` | `ok=19 changed=6` |
| `verify-vm-clone` | `ok=23 changed=6` | `ok=23 changed=6` |
| `verify-tenant` | `ok=16 changed=5` | `ok=16 changed=5` |
| `verify-auth-source` | `ok=19 changed=5` | `ok=19 changed=5` |
| `verify-recipe-matrix` | `ok=10` | `ok=10` |
| `verify-recipe-scenarios` | `ok=28` | `ok=28` |
| `verify-recipe-deploy` | `ok=25 changed=2` | `ok=25 changed=2` |
| `verify-recipe-concurrency` | `ok=19 changed=3` | `ok=19 changed=3` |
| `verify-recipe-fuzz` | `ok=72 changed=5` | `ok=72 changed=5` |
| `verify-recipe-edges` | `ok=108 changed=20` | `ok=108 changed=20` |
| `verify-recipe-custom` | `ok=192 changed=16` | `ok=192 changed=16` |

**`failed=0` everywhere, and every task count identical.** Plus 929 unit
tests green on both, on that date.

Checked again on **2026-09-23 against published pyvergeos 1.6.1** (editable
dev reporting that version) on the same lab:

- 936 collection unit tests green.
- The ladders above green (`failed=0`), task counts matching the dev column
  where compared (catalog 34/11, api-key 15/3, snapshot-profile 12/4,
  vnet-rule 15/6, file 19/6, vm-clone 23/6, tenant 16/5, auth-source 19/5,
  and the recipe matrix / scenarios / edges / fuzz / custom rows).
- Read-only info sweep and the user / group / member lifecycle green.
- `verify-catalog` rung 10 (a name that differs from its neighbour only by a
  `{brace}` token) resolves the right object and leaves the neighbour
  untouched.

Ladders needing a NAS service (`verify-vm-export`, `verify-nas-modules`) and
the destructive sweeps (`verify-recipe-real`, `verify-recipe-bulk`) were not
in the 15-ladder matrix. They are covered elsewhere.

## Why the results are identical

Four defects were filed against pyvergeos from this collection. All four are
fixed in **1.6.1**. Three of them were already fixed, and unreleased, on the
2026-09-22 `origin/dev`. **#100 was not.** The previous revision of this
file marked #100 broken on that dev. That was true that day. The fix
shipped in 1.2.8, one patch above 1.2.7, and 1.6.1 contains it.

| Issue | 1.2.7 | 1.6.1 |
|---|---|---|
| **#96** `list()` discards kwargs when a filter is present | broken | fixed |
| **#97** `save()` bypasses the typed manager's alias translation | broken | fixed |
| **#98** `refresh()` returns a new object and leaves the receiver stale | broken | fixed |
| **#100** `quote_value()` does not escape `{` | broken | **fixed in 1.2.8** |

The ladder counts did not move when the SDK gained the first three fixes,
because the collection never relied on the broken behaviour: name lookups
are matched client-side, and the modules do not use `refresh()` or
attribute-assignment saves. #100's fix changes a braced *server-side*
filter. The collection does not send one, so that fix does not move a task
count either. The catalog brace rung is the direct check, and it passes on
1.6.1.

## Name matching stays client-side

`get(name=...)` returns the first row and cannot see a second. VergeOS does
not enforce unique names on the tables this collection looks up, so a
server-side name filter can update or delete the wrong object even when the
quoting is correct (issue #72). Client-side equality refuses the ambiguous
case and has no escaping surface.

#100 used to be a second reason to stay client-side, and it was the reason
that survived #96: once ordinary names filtered correctly, a braced name
still resolved to a different object. That reason is gone on this floor.
The lookups are not switched in this change. Switching them is a behavior
change.

## Private names the recipe code reaches

The recipe code reaches past the SDK's public surface in one place,
documented in `plugins/module_utils/vm_recipes.py`. Those names were checked
on an editable tree reporting **1.6.1**:

| What the recipe code reaches for | Present on 1.6.1 |
|---|---|
| `client._request` | yes, and returns rows as before |
| `client._connection` | yes, still a `VergeConnection` |
| `connection.session` | yes |
| `connection.api_base_url` | yes |
| `client._timeout` | yes |

On that same SDK, against VergeOS 26.1.8:

| Suite | Result |
|---|---|
| Recipe unit tests | **201 passed** |
| `verify-recipe-matrix` | `ok=10`, 31 recipes simulated clean, 1 blocked as declared |
| `verify-recipe-scenarios` | `ok=28 changed=0` |
| `verify-recipe-deploy` | `ok=25 changed=2` |
| `verify-recipe-concurrency` | `ok=19 changed=3` |
| `verify-recipe-real` (5 recipe subset) | `ok=219 changed=20`, all 5 deployed, powered on, boot proved, torn down |

`failed=0`. The three ladder counts that overlap the table above match what
1.2.7 produced.

## `vm_info` power fields

`status` and `running` are computed joins (`machine#status#...`), not
columns. A raw `fields=all` omits them. pyvergeos 1.6.1 appends the
manager's computed fields when the projection contains `all` (pyVergeOS#117),
which is why `vm_info` returns them on this floor. The row's identifier is
`$key`. There is no `power_state` key and no `id` key.

## Consequences

- The floor is `pyvergeos>=1.2.8`. 1.2.7 is no longer a supported SDK.
- Do not describe #100 as open. `quote_value()` escapes `{` from 1.2.8 on,
  and 1.6.1 is the published release that carries it.
- Client-side name matching stays, for ambiguous names (#72), not because
  braces are still stripped.

## Reproducing

```bash
source ~/.config/vergeos/verify.env
cd tests/live && export ANSIBLE_COLLECTIONS_PATH=~/.ansible/collections

pip install 'pyvergeos==1.6.1'
for p in verify-catalog verify-api-key verify-snapshot-profile \
         verify-vnet-rule verify-file verify-vm-clone verify-tenant \
         verify-auth-source verify-recipe-matrix verify-recipe-scenarios \
         verify-recipe-deploy verify-recipe-concurrency verify-recipe-fuzz \
         verify-recipe-edges verify-recipe-custom; do
  ansible-playbook $p.yml >/tmp/$p.log 2>&1
  echo "$p rc=$? :: $(grep -o 'ok=[0-9]* .*failed=[0-9]*' /tmp/$p.log | tail -1)"
done
```

An editable install of a pyvergeos working tree wins over
`pip install pyvergeos==1.6.1`; the downgrade appears to succeed and the
editable is still imported. `pip uninstall -y pyvergeos` first, and print
`pathlib.Path(pyvergeos.__file__).parent` to confirm which one you are on.
