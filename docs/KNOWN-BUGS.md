# Known bugs

The numbered register (B1 through B7, B9 through B20) lived on the port
branches. It is not reproduced here. Issue #57 indexed that body of work
while it was off `dev`.

The record that is on `dev`:

- `docs/PORT-STATUS.md` — what landed, and the outcome of the six decisions
  that were open on 2026-09-11.
- `docs/WHY-TESTS-MISSED-THESE.md` — why fixtures built from the same wrong
  field names passed, and the guards that are in the tree.
- `docs/PYVERGEOS-GAPS.md` — the 2026-09-09 index diff against pyvergeos
  1.2.3, and what the 1.6.1 floor changed.
- `docs/SDK-COMPATIBILITY.md` — the `pyvergeos>=1.2.8` floor.

Entries the code still names (B2, B4, B6, B16 in
`tests/unit/test_fixture_discipline.py`; the member-ref and simulate notes
in `docs/PYVERGEOS-GAPS.md`) are described there.

## B8 (number skipped)

B8 was never assigned. The port register runs B1 through B7 and then
B9 through B20, with nothing between them. Nothing in `docs/`, `plugins/`,
`roles/` or `tests/` references B8. The number was left unused. It was not
a deleted entry.
