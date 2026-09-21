# Why our tests passed while the code was broken

*Written for anyone, technical or not. No background assumed.*

---

## The short version

We found several bugs that our automated tests said did not exist. The tests
were not badly written by accident — they were wrong in a specific, repeatable
way that is worth understanding, because it will happen again if we do not
change how we work.

**One sentence:** we were checking our work against our own assumptions
instead of against the real product.

---

## What a "test" is, and what went wrong

When we write software that talks to VergeOS, we also write small automatic
checks called **tests**. A test runs the code and confirms it did the right
thing. Tests run in seconds and do not need a real VergeOS system, which is
why we lean on them.

To run without a real system, a test has to **pretend**. It hands the code a
made-up piece of data and checks what the code does with it.

That pretending is where this went wrong.

### An analogy

Imagine you are writing instructions for someone collecting a parcel:

> "Go to the desk and ask for the parcel under **Reference Number**."

To check your instructions, you build a pretend front desk out of cardboard,
and you label the pretend form **Reference Number** — because that is what you
assumed it was called.

Your instructions work perfectly against your cardboard desk.

At the real depot, the form is called **Order ID**. There is no "Reference
Number" anywhere. Your instructions fail immediately.

Your test passed because **you built the test out of the same wrong assumption
as the instructions.** The test could never have caught the mistake — it was a
copy of the mistake.

That is exactly what happened to us, three separate times.

---

## The three real cases

### 1. Every server looked switched off

VergeOS tells us whether a physical server is running. We wrote code that
looked for a field called `online`.

There is no field called `online`. The real one is called `running`.

Our pretend data said `online`, so the test passed. Against a real system,
every server reported as **switched off**, even though both were running fine.

**What that would have caused in production:** our upgrade tool decided there
were no servers to upgrade, and its safety check — "did every server come back
after the upgrade?" — started from an empty list, so it could not have noticed
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
needed removing and that everyone needed adding — silently doing nothing
about accounts that should have been taken out.

### 3. Tests that ran but checked nothing

This one is different. Here the tests were not built from a wrong assumption —
they simply were not testing anything, while appearing to pass.

Three separate causes, all subtle:

- A pretend "error" was set up incorrectly, so instead of the code
  experiencing a failure, it received a harmless object. The test claimed to
  check "what happens when a machine is not found." That situation never
  occurred during the test.

- Pretend data was assembled in a way the programming language quietly
  ignores, so the code received an **empty** record instead of the intended
  one. Seven tests did this. One of them failed and drew attention; the other
  six passed — and would have passed no matter what the data contained.

- Some tests substituted a fake in the wrong place, a bit like changing the
  phone number in the directory after someone has already written it down.
  The code kept using the real thing, and the fake sat unused.

We had been treating six failing tests as "normal background noise." Every one
was a defect in the tests themselves.

---

## The thing that makes this genuinely hard

You might reasonably ask: *why not just look up the correct field names once
and write them down?*

We tried that. It is not sufficient, and here is why.

**The name of a piece of information depends on how you ask for it.**

When we ask VergeOS for a list of servers one way, the answer includes
`running`. Ask for "all fields" — which sounds like it should return more —
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
comment confidently stating where the `running` field comes from — and that
comment was wrong. The person writing it had already found and fixed the bug,
was specifically paying attention, and still got it wrong. That is the clearest
possible evidence that care and attention are not the answer here.

---

## What we changed

We stopped letting people invent the pretend data.

**1. We record real answers from a real VergeOS system.**
A script asks a live system the exact questions our code asks, and saves the
real replies into the project. Each saved reply records *which question
produced it*, because — as above — the answer's shape depends on the question.

**2. Pretend data must now be built from those recordings.**
Tests start from a real recorded record and change only the values they care
about.

**3. Inventing a field name is now an immediate, loud failure.**
If someone writes a test using a field VergeOS never sent, the test refuses to
run and explains itself:

```
'online' is not a field the platform sent for 'nodes'.
  call captured : client.nodes.list()
  fields sent   : maintenance, name, need_restart, running, ...
```

We checked this against the actual historical bugs. All three would have been
caught the moment they were written.

**4. Separate automatic checks for the "tests that check nothing" problem.**
Three patterns are now blocked outright. Each check has itself been tested by
deliberately reintroducing the bug to confirm the alarm sounds — because an
alarm nobody has ever heard ring is not evidence of safety.

Those checks found four more instances on their first run that a careful manual
review had missed — including a shared helper used across many tests whose own
description promised something it did not do.

**Result:** 553 tests, all passing, in about 4 seconds. Previously the suite
took over two minutes, and a portion of it was not testing anything.

---

## What this costs and what it buys

**Costs:** we need occasional access to a real VergeOS system to refresh the
recordings, and the recordings are checked into the project (with personal
details such as email addresses removed).

**Buys:** a whole category of bug — the kind that passes every test and fails
instantly in front of a customer — now gets caught while someone is typing,
rather than in production.

**Worth being honest about:** this does not catch every kind of mistake. It
catches "I used a name that does not exist." It does not catch "I used a real
name that means something different from what I think." That still requires
running against a real system, which we also now do.
