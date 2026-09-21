# Sanity-test ignores

`ignore-<core-version>.txt` lists deliberate `ansible-test sanity`
exceptions. The format takes **no comments and no blank lines** — every
line must be `path test-name` — so the reasoning lives here instead.

## What is ignored, and why

| Entry | Why it is ignored rather than fixed |
|---|---|
| `docs/repro/04_group_member_defect_http.py shebang` | `chmod +x`, meant to be run directly as `./script.py` when reproducing the defect by hand. The shebang is load-bearing. |
| `docs/repro/04b_group_member_scope.py shebang` | Same. |

That is the whole list. Everything else is fixed rather than suppressed.

## What this replaced

`ignore-2.14.txt` … `ignore-2.19.txt` were six **byte-identical** copies.
All four entries in each named `.claude/hooks/*.sh` — a gitignored
directory that is not in the repository — so they suppressed nothing.
There was no `ignore-2.20.txt`, and ansible-test selects the file matching
the running core, so on the version we actually test with (2.20.x) no
ignores applied at all.

The `shebang` test was failing on 19 real files at the time. Fifteen of
them were non-executable while every caller invoked them through an
explicit interpreter (`{{ ansible_playbook_python }} script.py`, or
pytest), which makes the shebang decorative — those were removed rather
than ignored. The two above are genuinely executable and kept theirs.

## Adding a version

If you start testing against another core, copy this file to
`ignore-<version>.txt`. Do not copy it pre-emptively: an ignore file for a
version nobody runs is how the previous six became stale without anyone
noticing.
