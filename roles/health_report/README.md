# health_report

Five read-only checks rolled into one green/amber/red digest, suitable for a
schedule, a webhook, or a pre-change gate.

## Doctrine: silence is the alert

Point this at a webhook and run it on a timer. The digest arriving is the
signal that the watchdog itself is alive; the day it **stops** arriving is the
red flag. That only works if the scan is honest about what it could not read,
so **a check whose data cannot be fetched reports RED, never green**. A
watchdog that fails quiet is worse than no watchdog.

The role never mutates anything. There is nothing to clean up and nothing to
undo.

## The five checks

| Check | Green | Amber | Red |
|---|---|---|---|
| `alarms` | none active | warnings | severe |
| `nodes` | all running | one in maintenance | any down |
| `capacity` | under the amber threshold | `>= 80%` used on a tier | `>= 90%` |
| `snapshots` | newest cloud snapshot recent | `>= 26 h` | `>= 50 h` |
| `nas` | services with volumes are running | — | one is not |

Two deliberate details, both of which were false alarms before they were
fixed:

- **Snoozed alarms are excluded.** An operator who snoozed something has
  already made the call.
- **A NAS service with zero volumes is ignored, not red.** A stopped
  volume-less service row (`Services 26.1.8-0` on the lab) serves nothing.
  Counting it produced a permanent false red.

## Usage

```yaml
- hosts: localhost
  roles:
    - role: vergeio.vergeos.health_report
      vars:
        health_report_webhook_url: "{{ vault_ops_webhook }}"
```

As a gate in front of a change:

```yaml
- role: vergeio.vergeos.health_report
  vars:
    health_report_fail_on: red      # or 'amber' to be stricter
```

Variables and their defaults are in `defaults/main.yml`; the full schema,
including the accepted values for `health_report_fail_on`, is in
`meta/argument_specs.yml` and is enforced before the scan runs.

## Results

`assess()` is a pure function of a state document, which is what makes the
verdict logic testable without a cluster:

- **26 unit tests** — `tests/unit/roles/test_health_report_verdicts.py`
- **Live ladder** — `tests/live/verify-health-report.yml`, passed on
  **VergeOS 26.1.8, two nodes** (`ok=48 changed=0 failed=0`): live structural
  run → healthy fixture all-green → sick fixture red → gate fires on red →
  gate holds on green → a nonsense gate value refused by the argument spec
  before the scan runs.

The live run on the lab reported `amber` honestly, on two real deprecation
warnings, with nodes `2/2 running`, capacity at 4%, and the newest cloud
snapshot 0.7 h old.

## Offline runs

`health_report_fixture` points the verdict logic at a saved state document
instead of the API. The fixture carries its own `now`, so snapshot-age checks
are deterministic. Two are shipped in `tests/fixtures/`:
`health-healthy.json` and `health-sick.json`.

## The fact it sets

`health_report_result` — the parsed report:

```yaml
overall: amber            # green | amber | red
digest:  "AMBER — alarms: 2 warning(s): deprecated, deprecated"
checks:                   # always five, one per check
  - check: alarms
    verdict: amber
    detail: "2 warning(s): deprecated, deprecated"
errors: {}                # populated when a section could not be read
```

Callers should branch on `overall` or on a specific check's `verdict`, not on
the digest text.
