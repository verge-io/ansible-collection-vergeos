# Issues prepared for verge-io/pyvergeos

Three defect reports, ready to file. Each was verified against a live
VergeOS 26.1.8 system on pyvergeos **1.2.3 and 1.2.4**, and each carries a
runnable reproduction.

| file | title | severity |
|---|---|---|
| `01-group-member-refs.md` | `remove_user()` / `remove_group()` cannot remove any membership VergeOS created itself | high |
| `02-name-escaping.md` | Name lookups fail for names containing an apostrophe or a backslash | medium |
| `03-discarded-response-body.md` | Non-2xx responses discard the body, making the recipe simulate unreachable | medium |

**All three of the above were filed, fixed upstream, and released in pyvergeos
1.2.5** (issues #71/#72/#73, PRs #75/#76/#77). Verified fixed against the lab
on 2026-09-11. They are kept here as the record of what was reported.

A fourth was found while verifying those fixes. It is **not** a pyvergeos
issue and goes to a different team:

| file | title | severity |
|---|---|---|
| `04-PLATFORM-group-identity.md` | A group created within ~3s of another group's deletion can never accept members | high |

Root-caused over several rounds. Two earlier explanations were tested and
**disproved** — it is not identity reuse, and it is not a race that heals —
and both are recorded in the issue so they are not re-invented. Reproduction:
`python docs/repro/04_group_member_defect.py`.

Duplicate check performed against all 70 existing issues and PRs on
2026-09-10: none of these are already reported. The nearest neighbour, #49,
is about recipe answer keys and was fixed in #52/#53.

## Filing

These were **not** filed automatically — there is no `gh` CLI or GitHub
credential in this environment. The first line of each file is the issue
title; everything after the `---` is the body.

With the GitHub CLI:

```bash
cd docs/issues
for f in 01-group-member-refs.md 02-name-escaping.md 03-discarded-response-body.md; do
  title=$(head -1 "$f")
  body=$(tail -n +3 "$f")
  gh issue create --repo verge-io/pyvergeos --title "$title" --body "$body"
done
```

Or paste each into <https://github.com/verge-io/pyvergeos/issues/new>.

## Suggested order

**01 first.** It is roughly three lines to fix, and today a user cannot be
removed from the Administrators group through the SDK. Combined with the
common `except NotFoundError: pass` idempotence pattern, offboarding
automation can report success while removing nobody.

02 and 03 are both small and neither blocks anything that has no workaround.
