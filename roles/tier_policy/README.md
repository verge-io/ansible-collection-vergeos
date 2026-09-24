# tier_policy

Storage-tier placement as code. Declares which tier each VM's drives belong
on, audits reality against it, and optionally corrects the difference.

This is the closest thing here to an SPBM storage policy. VergeOS's model is
simpler — a preferred tier per drive rather than a capability-matched
datastore — so this is a faithful policy layer over that, not a claim of
parity.

## What "drift" means here, precisely

The only placement signal a `machine_drives` row carries is `preferred_tier`,
and that is a *preference*. Nothing in the API reports which tier a drive's
blocks are actually on — `machine_drive_stats` has IO counters and
`used_bytes`, and no tier at all.

So drift means **the drive is configured for the wrong tier**, which is what
policy-as-code can own. The role never says "the drive is on tier 4", because
it cannot know that.

## Rules

First match wins, per drive:

```yaml
tier_policy_rules:
  - {match: "db-*",  tier: 1}                  # every drive of db-*
  - {match: "app-*", drive: "data*", tier: 1}  # just its data drives
  - {match: "app-*", tier: 4}                  # everything else on app-*
```

A drive no rule matches is reported `unclassified` and **never touched**.
That is deliberate: a policy should only own what it names.

`tier_policy_default_tier` opts every unmatched drive in. Under `enforce`
that will retier VMs you never named, so it is left unset by default and the
defaults file says why.

## The tier that exists in the policy and nowhere else

Measured on 26.1.8. The system had exactly one storage tier:

```
storage_tiers -> [{"$key": 1, "tier": 1}]
```

and `PUT machine_drives/<key> {"preferred_tier": N}` behaved like this:

| N | result |
|---|--------|
| `0` | refused — *Error setting tier* |
| `1` | accepted, reads back `'1'` — **the only tier that exists** |
| `2`–`5` | accepted, reads back |
| `6`, `-1`, `''`, `'banana'` | refused — *Error setting tier* |

The platform range-checks the number and does not check that the tier is
present. Seventeen drives on that system were already sitting at
`preferred_tier` 4 with no tier 4 to sit on.

That matters because **enforce proves itself by reading the value back**. A
policy of `tier: 3` on a one-tier system writes cleanly, reads back `'3'`,
converges, reports green, and cannot have moved a byte — a passing run with
no effect, which is exactly the failure this collection keeps finding.

So the audit reports `unsatisfiable` tiers, naming the rules that ask for
them and the tiers that do exist, and enforce refuses them:

```
tier 3 (db-*): no storage tier 3 on this system (tiers present: 1, 2, 4)
```

Report mode reports them either way. `tier_policy_require_tiers_exist: false`
lets you stage a policy ahead of adding the tier.

Tiers the *platform* refuses outright (0, 6, anything non-numeric) are caught
before the first write, so a bad policy is a refusal rather than a run that
fails partway through with some drives already retiered.

## Modes

**`report`** (default) is read-only. With `tier_policy_fail_on_drift: true`
it becomes a CI gate.

**`enforce`** corrects drift through the `drive` module, then **re-audits and
asserts convergence**. That second audit is not ceremony — the platform
accepts writes it silently discards (HTTP 200, no change), so "the module
reported changed" is not evidence that anything moved. Only a fresh read is.

Enforcing against a fixture is refused up front, before the audit runs, so
the error names the actual problem.

Under check mode the re-audit is skipped, because no write was attempted and
there is nothing to prove. The role reports what it *would* retier and keeps
the pre-enforce answer. It decides that from whether the re-audit ran, **not**
from `ansible_check_mode` — see below.

## Two traps this role walked into

**`ansible_check_mode` answers a different question.** It reports the
`--check` flag on the command line, and nothing else. A caller who wraps this
role in a `check_mode: true` block gets tasks that are skipped and a variable
that still reads `false`. The convergence assert was gated on
`not ansible_check_mode`, so a check-mode run wrote nothing, still saw the
drift it had been told not to fix, and *failed* — on a run that did exactly
what it was asked. The live ladder found it; nothing else could have, because
no unit test runs a playbook. Gating is now on whether the re-audit ran, which
is the same kind of evidence `vm_from_recipe` gates on, and
`tests/unit/test_role_check_mode.py` now refuses `ansible_check_mode` in any
role.

**`no_log` censors the diagnostic too.** Credentials go in the task's
environment rather than argv, so the audit carries `no_log: true` — and
`no_log` hides the *whole* task result. A rule missing its `tier` arrived as:

```
fatal: [localhost]: FAILED! => {"censored": "the output has been hidden
due to the fact that 'no_log: true' was specified for this result"}
```

An exit code and nothing else. `tier_audit.py` now validates the policy before
it touches the API, writes one scrubbed line to stderr, and the role
re-surfaces it: `rule 0 has no 'tier': {'match': 'db-*'}`.

## A dependency worth knowing about

Enforce needs the `drive` module's `tier` parameter to actually work. It did
not, until recently: the module compared a `tier` key that does not exist on
a `machine_drives` row — the field is `preferred_tier`, and it holds a
string — so it reported `changed` forever and converged never. That is #8,
fixed in this collection and pinned by
`tests/unit/plugins/modules/test_field_contracts.py`. If that regresses, this
role stops working silently.

## Usage

```yaml
- role: vergeio.vergeos.tier_policy
  vars:
    tier_policy_rules:
      - {match: "db-*", tier: 1}
    tier_policy_mode: report
    tier_policy_fail_on_drift: true
```

Adopt in report mode first. Read what it calls drift, confirm you agree, then
switch to enforce.

## Results

- **37 unit tests** — `tests/unit/roles/test_tier_policy_rules.py`
- **Live ladder** — `tests/live/verify-tier-policy.yml`, passed on
  **VergeOS 26.1.8, two nodes** (`ok=122 changed=5 failed=0`):

  fixture audit → a policy tier the system lacks is reported → a malformed
  rule names itself instead of arriving as `censored` → a tier the platform
  refuses is caught before the first write → enforce-against-a-fixture
  refused → an empty policy leaves every real drive `unclassified` →
  **`tiers_present` matches `storage_tiers` read independently** → a scratch
  VM with a tier-4 drive against a tier-1 policy is detected as exactly one
  drift → the CI gate fires → **enforcing an absent tier is refused and
  writes nothing** → check mode writes nothing and keeps the pre-enforce
  answer → enforce converges → **the tier reads back as `1` through
  `vm_drive_info`, independently of the audit** → a second enforce run
  changes nothing → zero leftovers.

The ladder's policy matches `zz-tier-*` only, so it can never retier a real
drive even under enforce.

## The fact it sets

`tier_policy_result`:

```yaml
summary: "12 drive(s): 1 on policy, 1 configured for the wrong tier, 10 unclassified"
tiers_present: [1]        # null offline, when the fixture did not record it
drift:
  - vm: app-01
    drive: data
    actual: 4             # what preferred_tier says, not where the data is
    expected: 1
    rule: "app-*/data*"
unclassified: [...]
unsatisfiable:            # policy tiers this system has no storage tier for
  - tier: 4
    rules: ["app-*"]
    reason: "no storage tier 4 on this system (tiers present: 1)"
```
