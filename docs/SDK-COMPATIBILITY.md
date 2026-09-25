# pyvergeos compatibility window

`requirements.txt` floors at `pyvergeos>=1.2.7`. That floor was chosen from
*manager availability*, meaning which SDK version first exposed every manager
the modules call, and had never been checked against **behaviour**. This file
records what the collection was actually verified against, and how.

## What was tested

Two SDKs, same lab (VergeOS 26.1.8, `conundrum-lab`), same collection build,
same 15 live ladders, run back to back on 2026-09-22:

- **released `pyvergeos==1.2.7`** from PyPI, the current floor
- **`origin/dev`** (plus the in flight `#112` fix), installed editable, 22
  commits ahead of the 1.2.7 release

| Ladder | 1.2.7 | `origin/dev` |
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
tests green on both.

Ladders needing a NAS service (`verify-vm-export`, `verify-nas-modules`) and
the destructive sweeps (`verify-recipe-real`, `verify-recipe-bulk`) were not
included; they are covered elsewhere and their cost is disproportionate for a
matrix like this.

## Why the results are identical, and why that is not luck

Four defects were filed against pyvergeos from this collection. Three are
fixed on `origin/dev` and unreleased:

| Issue | 1.2.7 | `origin/dev` |
|---|---|---|
| **#96** `list()` discards kwargs when a filter is present | broken | fixed |
| **#97** `save()` bypasses the typed manager's alias translation | broken | fixed |
| **#98** `refresh()` returns a new object, leaves the receiver stale | broken | fixed |
| **#100** `quote_value()` does not escape `{` | broken | **still broken** |

The collection is unaffected by the first three **because it never relied on
the broken behaviour**: name lookups are matched client-side, and the modules
do not use `refresh()` or attribute-assignment saves. That is why an SDK
carrying three fixes produces byte identical ladder results: the collection
was already routing around all three.

## The one that still matters: #100

`{...}` is **stripped** from a filter literal rather than escaped, so a name
containing braces resolves to a *different object*. Measured on `origin/dev`
against a real VM:

```
list(name='zz-i96x-f-vm')     -> 1  the right VM
list(name='zz-i9{X}6x-f-vm')  -> 1  the SAME VM, wrong object, no error
list(name='zz-i9{X}x-f-vm')   -> 0  proves the brace is STRIPPED, not a wildcard
list(name='zz-i96x{}-f-vm')   -> 1  empty braces strip too
```

**#96's fix makes this more dangerous.** On 1.2.7, `list(name=X)` returned
every row, so no caller could trust a server-side name filter and nobody was
exposed to the brace. With #96 fixed, ordinary names filter correctly and a
server side lookup starts to look safe, while the one input that silently
hits the wrong row is unchanged. Modules that do that lookup go on to update
and delete: see B19 in `KNOWN-BUGS.md` for the reproduction where
`state: absent` on `zz-jw-c{x}at` deleted `zz-jw-cat`.

## Three of those ladders cannot run from this branch

`verify-recipe-fuzz`, `verify-recipe-edges` and `verify-recipe-custom` all
author a scratch catalog first, so they call `vergeio.vergeos.catalog`. That
module is not on this branch, and running any of the three here stops at:

```
couldn't resolve module/action 'vergeio.vergeos.catalog'
```

The counts recorded for them above were measured on a branch that carried the
catalog module. They are real numbers, they just are not reproducible from
here. Either land this work on a base that has `catalog`, or read those three
rows as history rather than as something you can re-run today.

The remaining recipe ladders have no such dependency and do run from this
branch: `verify-recipe-matrix`, `verify-recipe-scenarios`,
`verify-recipe-deploy`, `verify-recipe-concurrency`, and the destructive
`verify-recipe-real` and `verify-recipe-bulk`.

## Checked again on 2026-09-23, against a newer SDK

The floor is written `pyvergeos>=1.2.7` with no upper bound, and the recipe
code deliberately reaches past the SDK's public surface in one place. That is
documented and justified in `plugins/module_utils/vm_recipes.py`, but it does
mean an unbounded floor is a standing bet that four private names keep
working.

So they were checked against an editable `pyvergeos` working tree reporting
version **1.6.1**, well ahead of the 1.2.7 release:

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

`failed=0` throughout, and the three ladder counts that overlap with the table
above are identical to what 1.2.7 produced.

This is not a claim that the floor should move. The private names surviving
one newer version is evidence, not a guarantee, and #100 is still the reason
not to swap client side name matching for `list(name=...)`. It is here so the
next person knows the bet has been checked at least once above the floor.

## Node-scoped physical drives (pyVergeOS#143)

`physical_drive_info` with `node:` used to call
`PhysicalDriveManager(client, node_key=...)`. On every released pyvergeos
from the 1.2.7 floor through 1.6.1, that manager builds
`filter="node eq <key>"`. `machine_drive_phys` has no `node` column, so the
list is empty, `changed=false`, and nothing warns. `drive_health` passes
`drive_health_node` straight through, so a per-node scan then reads as
healthy.

The parent_drive walk that fixes it (`nodes.machine` → `machine_drives` →
`parent_drive eq ...`, method `_parent_drive_filter_for_node`) is on
pyVergeOS `dev` only (issue #143 / PR #144). No release tag contains it.

The module calls the scoped manager only when that method is a real
function on the installed class. Otherwise it lists the fleet and matches
`node_name` client-side, which works on 1.2.7. A resolved node with no
drives warns (`no drives matched node ...`) instead of returning a silent
empty report. The floor stays `pyvergeos>=1.2.7`.

## Consequences

- **Do not raise the floor past `1.2.7` yet.** The fixes are unreleased.
- **Do not replace client-side name matching with `list(name=...)`** when the
  floor does move. #100 is the reason, and it survives #96.
- When #100 ships, re-run this matrix before changing either.

## Reproducing

```bash
source ~/.config/vergeos/verify.env
cd tests/live && export ANSIBLE_COLLECTIONS_PATH=~/.ansible/collections

pip install pyvergeos==1.2.7          # or: pip install -e /path/to/pyVergeOS
for p in verify-catalog verify-api-key verify-snapshot-profile \
         verify-vnet-rule verify-file verify-vm-clone verify-tenant \
         verify-auth-source verify-recipe-matrix verify-recipe-scenarios \
         verify-recipe-deploy verify-recipe-concurrency verify-recipe-fuzz \
         verify-recipe-edges verify-recipe-custom; do
  ansible-playbook $p.yml >/tmp/$p.log 2>&1
  echo "$p rc=$? :: $(grep -o 'ok=[0-9]* .*failed=[0-9]*' /tmp/$p.log | tail -1)"
done
```

⚠️ An editable install of a pyvergeos working tree wins over
`pip install pyvergeos==1.2.7`; the downgrade appears to succeed and the
editable is still imported. `pip uninstall -y pyvergeos` first, and print
`pathlib.Path(pyvergeos.__file__).parent` to confirm which one you are on.
That mistake produced a "both versions behave identically" result here before
either had been swapped.
