# Known bugs

The numbered register (B1 through B7, B9 through B20) was kept on the port
branches and was never merged onto `dev`. It is not reproduced here. Issue
#57 is the index for that body of work.

`docs/PORT-STATUS.md` was the same kind of record, written before most of
the port landed, and it is not on `dev` either. It is not restored: a second
status page next to #57 is how the two drifted apart.

## B8 (number skipped)

B8 was never assigned. The port register runs B1 through B7 and then
B9 through B20, with nothing between them. Nothing in `docs/`, `plugins/`,
`roles/` or `tests/` references B8. The number was left unused. It was not
a deleted entry.
