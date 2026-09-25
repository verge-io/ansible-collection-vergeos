# Why our tests passed while the code was broken

*Written for anyone, technical or not. No background assumed.*

The failure mode below is historical. The guards that exist for it are
named at the end, and they are in this tree.

## The short version

We found several bugs that our automated tests said did not exist. The tests
were not badly written by accident. They were wrong in a specific, repeatable
way that is worth understanding, because it will happen again if we do not
change how we work.

**One sentence:** we were checking our work against our own assumptions
instead of against the real product.

## What a "test" is, and what went wrong

When we write software that talks to VergeOS, we also write small automatic
checks called **tests**. A test runs the code and confirms it did the right
thing. Tests run in seconds and do not need a real VergeOS system, which is
why we lean on them.

To run without a real system, a test has to **pretend**. It hands the code a
made up piece of data and checks what the code does with it.

That pretending is where this went wrong.

### An analogy

Imagine you are writing instructions for someone collecting a parcel:

> "Go to the desk and ask for the parcel under **Reference Number**."

To check your instructions, you build a pretend front desk out of cardboard,
and you label the pretend form **Reference Number**, because that is what you
assumed it was called.

Your instructions work perfectly against your cardboard desk.

At the real depot, the form is called **Order ID**. There is no "Reference
Number" anywhere. Your instructions fail immediately.

Your test passed because **you built the test out of the same wrong assumption
as the instructions.** The test could never have caught the mistake. It was a
copy of the mistake.

That is exactly what happened to us, three separate times.

## The three real cases

### 1. Every server looked switched off

VergeOS tells us whether a physical server is running. We wrote code that
looked for a field called `online`.

There is no field called `online`. The real one is called `running`.

Our pretend data said `online`, so the test passed. Against a real system,
every server reported as **switched off**, even though both were running fine.

**What that would have caused in production:** our upgrade tool decided there
were no servers to upgrade, and its safety check ("did every server come back
after the upgrade?") started from an empty list, so it could not have noticed
a server failing to come back. A safety net with a hole in it, reporting
success.

### 2. Every group looked empty

VergeOS groups contain users. We wrote code looking for fields called
`member_name` and `member_type`.

The real data has a field called `member` containing something like
`users/1`, plus a separate `member_display` with the actual name.

Result: **every group reported zero members.** The test passed because the
pretend data used our invented names.

**What that would have caused in production:** we have a setting that means
"make this group's membership exactly this list, remove anyone else." Because
the code believed every group was empty, it would have concluded that nobody
needed removing and that everyone needed adding, silently doing nothing
about accounts that should have been taken out.

### 3. Tests that ran but checked nothing

This one is different. Here the tests were not built from a wrong assumption.
They simply were not testing anything, while appearing to pass.

Three separate causes, all subtle:

- A pretend "error" was set up incorrectly, so instead of the code
  experiencing a failure, it received a harmless object. The test claimed to
  check "what happens when a machine is not found." That situation never
  occurred during the test.

- Pretend data was assembled in a way the programming language quietly
  ignores, so the code received an **empty** record instead of the intended
  one. Seven tests did this. One of them failed and drew attention; the other
  six passed, and would have passed no matter what the data contained.

- Some tests substituted a fake in the wrong place, a bit like changing the
  phone number in the directory after someone has already written it down.
  The code kept using the real thing, and the fake sat unused.

We had been treating six failing tests as "normal background noise." Every one
was a defect in the tests themselves.

## The thing that makes this genuinely hard

You might reasonably ask: *why not just look up the correct field names once
and write them down?*

We tried that. It is not sufficient, and here is why.

**The name of a piece of information depends on how you ask for it.**

When we ask VergeOS for a list of servers one way, the answer includes
`running`. Ask for "all fields", which sounds like it should return more,
and `running` is **not there at all**.

The same happens with group members. The identifier comes back as `users/1` in
one case and `/v4/users/2` in another. Same information. Same product. Same
table of data.

It gets one step stranger. Those two forms exist **side by side in the same
place at the same time**, because VergeOS stores whatever form was used when
the record was created. Memberships created by VergeOS itself are in the short
form. Memberships created by our tools are in the long form. Both are correct.
Both are present right now.

So "what is this field called?" and "what does its value look like?" have no
single answer. They depend on the exact request, and sometimes on who created
the record years ago.

**This is why writing the names down by hand cannot work.** Any note we write
is a snapshot of one situation, presented as a general rule.

We proved this the hard way: the fix for problem 1 above shipped with a
comment confidently stating where the `running` field comes from, and that
comment was wrong. The person writing it had already found and fixed the bug,
was specifically paying attention, and still got it wrong. That is the clearest
possible evidence that care and attention are not the answer here.

## What changed in the test suite

The guards live in the tree. They are not a proposal.

**1. Real answers are recorded, and the recording names the question.**
`tests/capture_api_fixtures.py` asks a live system the questions the code
asks and saves the replies under `tests/fixtures/api/`. Each capture records
the call that produced it. `running` is present on `client.nodes.list()` and
absent from `GET /nodes?fields=all`. A membership row is `users/1` through
one call and `/v4/users/2` through another. The shape belongs to the call.

**2. Pretend data is built from those recordings.**
`tests/unit/api_fixtures.py` `row()` starts from a captured row and applies
only the overrides the test names.

**3. An invented field name fails the test.**
`row()` raises when an override names a field the capture does not contain.
The message has this shape:

```
'online' is not a field the platform sent for 'nodes'.
  call captured : <the call stored on the capture>
  fields sent   : maintenance, name, need_restart, running, ...
```

`tests/unit/test_fixture_discipline.py` reintroduces the historical names
(`online`, `needs_restart`, `member_name`, `member_type`, `is_installed`,
`is_reboot_required`) and asserts that `row()` refuses them. It also asserts
that the real names (`running`, `member`, `member_display`, `installed`,
`reboot_required`) are accepted. A guard that refused everything would also
appear to pass.

**4. Separate checks for tests that check nothing.**
The same file scans the unit suite for the vacuous mock patterns: stubbing
`pyvergeos` out of `sys.modules` (issue #66), `dict(mock)` collapsing to
`{}`, and a patch aimed at the definition site rather than the module under
test. Each check is itself tested by the patterns it names.

When those checks were first written, the suite that contained them was
recorded as 553 tests, all passing, in about 4 seconds. The suite has grown
since. `docs/SDK-COMPATIBILITY.md` records 936 collection unit tests green
on 2026/09/23 against pyvergeos 1.6.1, before `site_info` landed. This page
does not restate a count from a later day.

## What else covers the same class of miss

These shipped after the explainer was first written. They do not replace the
fixture guard. They catch different slices of the same mistake.

- **Continuous integration (#66).** Unit tests and `ansible-test sanity` run
  on `ansible-core` 2.15 and 2.20. `ansible-lint` runs at the production
  profile. A collection build runs. `sanity` is a required check on `dev`
  and `main`.
- **Sanity on the advertised floor (#77).** `ansible-core` 2.15 runs more
  sanity tests than 2.20. The 2.15 run had been crashing on an unparsable
  `DOCUMENTATION` block and skipping the rest. Both cores are in the
  workflow.
- **The field contract harness (#75).**
  `tests/live/verify-field-contract.yml` asserts, against a live system,
  that a field a module sends exists on the resource, that the value
  round trips, and that a second apply reports `changed=false`.
  `tests/unit/plugins/modules/test_field_contracts.py` checks the structural
  half in CI, including that the live ladder has not drifted from the code.
  The fixture guard catches "this name was never sent." The contract harness
  catches "this name was sent and the platform discarded it."

## What this costs and what it buys

**Costs:** occasional access to a real VergeOS system to refresh the
recordings. The recordings are checked in with personal details such as
email addresses removed.

**Buys:** a test that uses a field name the captured call never sent fails
while it is being written.

**Limit, unchanged:** this does not catch every kind of mistake. It catches
"I used a name that does not exist on this capture." It does not catch "I
used a real name that means something different from what I think." That
still requires a run against a real system. The contract harness is that
run for the modules it covers. It is not a substitute for the rest.
