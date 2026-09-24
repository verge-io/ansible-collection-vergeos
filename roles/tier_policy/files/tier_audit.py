"""VergeOS storage-tier drift audit -- SPBM-style placement policy.

Declares which storage tier each VM's drives belong on (glob rules, first
match wins) and flags drives whose configured `preferred_tier` disagrees.
Read-only: the audit never mutates anything; the tier_policy role's enforce
mode fixes drift through the collection's `drive` module and re-audits to
prove convergence.

What this can and cannot see
---------------------------
The only placement signal a `machine_drives` row exposes is `preferred_tier`,
and that is a PREFERENCE, not a residency report. Nothing in the API says
which tier a drive's blocks are actually on -- `machine_drive_stats` carries
IO counters and `used_bytes`, no tier. So "drift" here means *the drive is
configured for the wrong tier*, which is what policy-as-code can own. It does
not mean the data has moved, and this file is careful never to say it has.

Why that distinction has teeth (measured on 26.1.8)
---------------------------------------------------
The test system had exactly one storage tier::

    storage_tiers -> [{'$key': 1, 'tier': 1}]

and PUT `machine_drives/<key> {"preferred_tier": N}` behaved like this::

    N = 0    refused   "Error setting tier"
    N = 1    accepted, reads back '1'      <- the only tier that exists
    N = 2    accepted, reads back '2'
    N = 3    accepted, reads back '3'
    N = 4    accepted, reads back '4'
    N = 5    accepted, reads back '5'
    N = 6    refused   "Error setting tier"
    N = -1   refused   "Error setting tier"
    N = ''   refused   "Error setting tier"

The platform range-checks the number and does not check that the tier is
present. Seventeen drives on that system were already sitting at
`preferred_tier` 4 with no tier 4 to sit on.

That matters because enforce proves itself by reading the value back. A
policy of `tier: 3` on a one-tier system writes cleanly, reads back '3',
converges, and moves nothing -- a green run with no effect, which is the
failure mode this collection keeps finding. So the audit reports which policy
tiers the system cannot satisfy, and the role refuses to enforce them.

Scope: `media: disk` drives of real VMs (snapshots excluded). Node physical
drives, vSAN dir mounts, cdroms, efidisks and nonpersistent service-OS images
are placement the platform owns, not policy.

Usage:
  tier_audit.py --policy-json '[{"match":"db-*","tier":2}]'
  tier_audit.py --policy-json ... --fixture state.json   # offline (CI)

Output: JSON on stdout -- {drift, unclassified, unsatisfiable, ...}.
Errors: one scrubbed line on stderr, exit 2. The role runs this with
`no_log: true` (credentials are in the environment), which censors the whole
task result, so a traceback here would reach the operator as the word
"censored" and nothing else.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys

# Measured, not assumed -- see the module docstring for the probe.
TIER_MIN = 1
TIER_MAX = 5


# --------------------------------------------------------------- validation

def validate_rules(rules):
    """Problems with the policy's shape. Empty list means it is well formed.

    A malformed rule used to surface as a KeyError traceback out of a
    `no_log: true` command task -- which Ansible renders as::

        fatal: [localhost]: FAILED! => {"censored": "the output has been
        hidden due to the fact that 'no_log: true' was specified"}

    The operator saw the exit code and nothing else. Naming the bad rule here
    is the difference between a two-second fix and a bisect.
    """
    problems = []
    if not isinstance(rules, list):
        return ['the policy must be a list of rules, got %s'
                % type(rules).__name__]

    for index, rule in enumerate(rules):
        where = 'rule %d' % index
        if not isinstance(rule, dict):
            problems.append('%s is %s, expected a mapping of '
                            '{match, drive?, tier}'
                            % (where, type(rule).__name__))
            continue
        if not rule.get('match'):
            problems.append("%s has no 'match' (a VM-name glob): %r"
                            % (where, rule))
        if 'tier' not in rule:
            problems.append("%s has no 'tier': %r" % (where, rule))
            continue
        problems.extend(tier_problems(rule['tier'], where))

    return problems


def tier_problems(value, where):
    """Whether a tier number is one the platform will accept at all."""
    try:
        tier = int(value)
    except (TypeError, ValueError):
        return ["%s has a non-numeric tier %r" % (where, value)]
    if not TIER_MIN <= tier <= TIER_MAX:
        return ['%s asks for tier %d; the platform accepts %d-%d and refuses '
                'anything else with "Error setting tier"'
                % (where, tier, TIER_MIN, TIER_MAX)]
    return []


def unsatisfiable_tiers(rules, default_tier, existing_tiers):
    """Policy tiers this system has no storage tier for.

    Writing one of these succeeds and reads back, so enforce would report
    convergence for a placement that cannot happen. Reporting it is the only
    way the caller finds out.
    """
    if not existing_tiers:
        return []
    have = {int(t) for t in existing_tiers}
    wanted = {}
    for rule in rules:
        try:
            wanted.setdefault(int(rule['tier']), []).append(rule['match'])
        except (KeyError, TypeError, ValueError):
            continue
    if default_tier is not None:
        wanted.setdefault(int(default_tier), []).append('(default)')

    return [{'tier': tier,
             'rules': sorted(rules_named),
             'reason': 'no storage tier %d on this system (tiers present: %s)'
                       % (tier, ', '.join(str(t) for t in sorted(have)))}
            for tier, rules_named in sorted(wanted.items())
            if tier not in have]


# ---------------------------------------------------------------- auditor

def audit(drives, rules, default_tier=None, existing_tiers=None):
    """Pure policy evaluation. drives are joined dicts, no API access.

    drive: {vm, drive, tier}          (tier: the drive's configured
                                       preferred_tier; 0 = unreadable)
    rule:  {match, drive?, tier}      (globs; first match wins)
    default_tier: expected tier for drives no rule matches; None means
                  unmatched drives are reported unclassified, not drift.
    existing_tiers: tier numbers the system actually has. None means the
                  caller could not find out (an offline fixture), and the
                  unsatisfiable check is skipped rather than guessed.
    """
    drift, unclassified = [], []
    compliant = 0
    for d in drives:
        rule = next(
            (r for r in rules
             if fnmatch.fnmatchcase(d['vm'], r['match'])
             and fnmatch.fnmatchcase(d['drive'], r.get('drive', '*'))),
            None)
        expected = rule['tier'] if rule else default_tier
        if expected is None:
            unclassified.append({'vm': d['vm'], 'drive': d['drive'],
                                 'tier': d['tier']})
        elif int(d['tier']) != int(expected):
            drift.append({'vm': d['vm'], 'drive': d['drive'],
                          'expected': int(expected),
                          'actual': int(d['tier']),
                          'rule': rule['match'] if rule else '(default)'})
        else:
            compliant += 1

    unsatisfiable = unsatisfiable_tiers(rules, default_tier, existing_tiers)
    present = sorted(int(t) for t in existing_tiers) if existing_tiers else None

    summary = ('%d drive(s): %d on policy, %d configured for the wrong tier, '
               '%d unclassified'
               % (len(drives), compliant, len(drift), len(unclassified)))
    if unsatisfiable:
        summary += ('; %d policy tier(s) this system cannot satisfy'
                    % len(unsatisfiable))

    return {'mode': 'read-only',
            'drives_audited': len(drives),
            'compliant_count': compliant,
            'drift': drift,
            'unclassified': unclassified,
            'unsatisfiable': unsatisfiable,
            'tiers_present': present,
            'summary': summary}


# -------------------------------------------------------------- collector

def client():
    import urllib3
    urllib3.disable_warnings()
    from pyvergeos import VergeClient

    kwargs = {'host': os.environ['VERGEOS_HOST'],
              'verify_ssl': os.environ.get('VERGEOS_INSECURE', '') not in
              ('1', 'true', 'True')}
    if os.environ.get('VERGEOS_TOKEN'):
        kwargs['token'] = os.environ['VERGEOS_TOKEN']
    else:
        kwargs['username'] = os.environ['VERGEOS_USERNAME']
        kwargs['password'] = os.environ['VERGEOS_PASSWORD']
    return VergeClient(**kwargs)


def collect(conn):
    """Join machine_drives to VM names, and read the tiers that exist.

    Drive rows live in the raw `machine_drives` table (no SDK manager). The
    tier field is `preferred_tier`, a STRING (finding #18). `fields=most`
    carries machine, name, media and preferred_tier -- checked against 26.1.8
    rather than hoped for, because a projection that silently omits
    preferred_tier would read every drive as tier 0 and call the whole system
    drifted.
    """
    vm_by_machine = {}
    for row in (dict(v) for v in conn.vms.list()):
        if not row.get('is_snapshot'):
            vm_by_machine[row.get('machine')] = row.get('name')

    drives = []
    for row in conn._request('GET', 'machine_drives',
                             params={'fields': 'most'}):
        if row.get('machine') not in vm_by_machine:
            continue
        if row.get('media') != 'disk':
            continue
        try:
            tier = int(row.get('preferred_tier'))
        except (TypeError, ValueError):
            tier = 0  # unreadable tier: never silently compliant
        drives.append({'vm': vm_by_machine[row['machine']],
                       'drive': row.get('name'),
                       'tier': tier})

    tiers = sorted({int(dict(t)['tier']) for t in conn.storage_tiers.list()
                    if dict(t).get('tier') is not None})
    return drives, tiers


def scrub(text):
    """Never let a credential out through an error path."""
    out = str(text)
    for name in ('VERGEOS_PASSWORD', 'VERGEOS_TOKEN'):
        secret = os.environ.get(name)
        if secret:
            out = out.replace(secret, '<%s>' % name)
    return out


def main():
    p = argparse.ArgumentParser(description='VergeOS storage-tier drift audit')
    p.add_argument('--policy-json', required=True,
                   help='JSON list of {match, drive?, tier} rules')
    p.add_argument('--default-tier', type=int, default=None,
                   help='expected tier for unmatched drives; omit to '
                   'report them unclassified instead of drifted')
    p.add_argument('--fixture', help='JSON file with {drives: [...]} and an '
                   'optional {tiers: [...]} -- offline audit, no API access')
    args = p.parse_args()

    try:
        rules = json.loads(args.policy_json)
    except ValueError as exc:
        sys.stderr.write('--policy-json is not valid JSON: %s\n' % exc)
        return 2

    problems = validate_rules(rules)
    if args.default_tier is not None:
        problems.extend(tier_problems(args.default_tier, 'default-tier'))
    if problems:
        sys.stderr.write('tier policy is malformed:\n  %s\n'
                         % '\n  '.join(problems))
        return 2

    try:
        if args.fixture:
            with open(args.fixture) as fh:
                state = json.load(fh)
            drives = state['drives']
            tiers = state.get('tiers')
        else:
            drives, tiers = collect(client())
    except Exception as exc:                      # noqa: BLE001
        sys.stderr.write('%s: %s\n' % (type(exc).__name__, scrub(exc)))
        return 2

    result = audit(drives, rules, args.default_tier, tiers)
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
