# Known bugs

The numbered register (B1 through B7, B9 through B20) lived on the port
branches. It is not reproduced here, except B7, which this tree now
depends on. Issue #57 indexed that body of work while it was off `dev`.

The record that is on `dev`:

- `docs/PORT-STATUS.md`: what landed, and the outcome of the six decisions
  that were open on 2026/09/11.
- `docs/WHY-TESTS-MISSED-THESE.md`: why fixtures built from the same wrong
  field names passed, and the guards that are in the tree.
- `docs/PYVERGEOS-GAPS.md`: the 2026/09/09 index diff against pyvergeos
  1.2.3, and what the 1.6.1 floor changed.
- `docs/SDK-COMPATIBILITY.md`: the `pyvergeos>=1.2.8` floor.

Entries the code still names (B2, B4, B6, B16 in
`tests/unit/test_fixture_discipline.py`; the member reference and simulate
notes in `docs/PYVERGEOS-GAPS.md`) are described there.

## B7. Tag-scoped snapshot periods

Partial cloud snapshots need `include_tags`, `exclude_tags`, and
`quiesce_tags` on a snapshot profile period. Released pyVergeOS through
1.7.1 does not accept those parameters. `cloud_snapshots.create()` already
does, since the 1.6.1 line (pyVergeOS #129). The period parameters are the
gap.

pyVergeOS PR #200, released in 1.8.0, adds them. `periods.create()`,
`update()`, `save()`, and `SnapshotProfile.add_period()` take the three
names. They write `type` (`full`, `partial_include`, `partial_exclude`),
`partial_tag_args`, and `quiesce_tag_args`. `partial_tags` and
`quiesce_tags` are read-only rows. The tag keys come back as
`partial_tag_keys` and `quiesce_tag_keys`, from `partial_tags[refs[tag]]`
and `quiesce_tags[refs[tag]]`.

`snapshot_profile` calls the parameters. When a period sets any of them
and the installed `periods.create()` does not accept `include_tags`, the
module fails and names #200. A period that sets none of them is still the
all-or-nothing schedule, on the current floor. `requirements.txt` stays
`pyvergeos>=1.2.8` so older SDKs remain usable; 1.8.0 is the release that
contains #200. Issue #67.

## B8 (number skipped)

B8 was never assigned. The port register runs B1 through B7 and then
B9 through B20, with nothing between them. Nothing in `docs/`, `plugins/`,
`roles/` or `tests/` references B8. The number was left unused. It was not
a deleted entry.
