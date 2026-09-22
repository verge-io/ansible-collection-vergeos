# pyvergeos compatibility window

`requirements.txt` floors at `pyvergeos>=1.2.7`. That floor was chosen from
*manager availability* — which SDK version first exposed every manager the
modules call — and had never been checked against **behaviour**. This file
records what the collection was actually verified against, and how.

## What was tested

Two SDKs, same lab (VergeOS 26.1.8, `conundrum-lab`), same collection build,
same 15 live ladders, run back to back on 2026-09-22:

- **released `pyvergeos==1.2.7`** from PyPI — the current floor
- **`origin/dev`** (+ the in-flight `#112` fix), installed editable — 22
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
carrying three fixes produces byte-identical ladder results — the collection
was already routing around all three.

## The one that still matters: #100

`{...}` is **stripped** from a filter literal rather than escaped, so a name
containing braces resolves to a *different object*. Measured on `origin/dev`
against a real VM:

```
list(name='zz-i96x-f-vm')     -> 1  the right VM
list(name='zz-i9{X}6x-f-vm')  -> 1  the SAME VM — wrong object, no error
list(name='zz-i9{X}x-f-vm')   -> 0  proves the brace is STRIPPED, not a wildcard
list(name='zz-i96x{}-f-vm')   -> 1  empty braces strip too
```

**#96's fix makes this more dangerous.** On 1.2.7, `list(name=X)` returned
every row, so no caller could trust a server-side name filter and nobody was
exposed to the brace. With #96 fixed, ordinary names filter correctly and a
server-side lookup starts to look safe — while the one input that silently
hits the wrong row is unchanged. Modules that do that lookup go on to update
and delete: see B19 in `KNOWN-BUGS.md` for the reproduction where
`state: absent` on `zz-jw-c{x}at` deleted `zz-jw-cat`.

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
