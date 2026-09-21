# tier_policy

Storage-tier placement as code. Declares which tier each VM's drives belong
on, audits reality against it, and optionally corrects the difference.

This is the closest thing here to an SPBM storage policy. VergeOS's model is
simpler — a preferred tier per drive rather than a capability-matched
datastore — so this is a faithful policy layer over that, not a claim of
parity.

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

## Modes

**`report`** (default) is read-only. With `tier_policy_fail_on_drift: true`
it becomes a CI gate.

**`enforce`** corrects drift through the `drive` module, then **re-audits and
asserts convergence**. That second audit is not ceremony — the platform
accepts writes it silently discards (HTTP 200, no change), so "the module
reported changed" is not evidence that anything moved. Only a fresh read is.

Enforcing against a fixture is refused up front, before the audit runs, so
the error names the actual problem.

## A dependency worth knowing about

Enforce needs the `drive` module's `tier` parameter to actually work. It did
not, until recently: the module compared a `tier` key that does not exist on
a `machine_drives` row — the field is `preferred_tier`, and it holds a
string — so it reported `changed` forever and converged never. That is fixed
in this collection and pinned by
`tests/unit/plugins/modules/test_defect_regressions.py`. If those tests ever
regress, this role stops working silently.

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

- **11 rule unit tests** — `tests/unit/roles/test_tier_policy_rules.py`
- **Live ladder** — `tests/live/verify-tier-policy.yml`, passed on
  **VergeOS 26.1.8, two nodes** (`ok=71 changed=4 failed=0`):

  fixture audit → enforce-against-a-fixture refused → an empty policy leaves
  every real drive `unclassified` → a scratch VM with a tier-4 drive against
  a tier-1 policy is detected as exactly one drift → the CI gate fires →
  enforce converges → **the tier reads back as `1` through `vm_drive_info`,
  independently of the audit** → a second enforce run changes nothing →
  zero leftovers.

The ladder's policy matches `zz-tier-*` only, so it can never retier a real
drive even under enforce.

## The fact it sets

`tier_policy_result`:

```yaml
summary: "12 drive(s): 1 on policy, 1 drift, 10 unclassified"
drift:
  - vm: app-01
    drive: data
    actual: 4
    expected: 1
    rule: "app-*/data*"
unclassified: [...]
```
