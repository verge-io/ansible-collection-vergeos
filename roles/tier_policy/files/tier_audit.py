#!/usr/bin/env python3
"""VergeOS storage-tier drift audit — SPBM-style placement policy.

Declares which storage tier each VM's drives belong on (glob rules,
first match wins) and flags drives whose configured `preferred_tier`
disagrees. Read-only: the audit never mutates anything; the tier_policy
role's enforce mode fixes drift through the collection's `drive` module
and re-audits to prove convergence.

Scope: `media: disk` drives of real VMs (snapshots excluded). Node
physical drives, vSAN dir mounts and nonpersistent service-OS images
are placement the platform owns, not policy.

Usage:
  tier_audit.py --policy-json '[{"match":"db-*","tier":2}]'
  tier_audit.py --policy-json ... --fixture state.json   # offline (CI)

Output: JSON on stdout — {drift, unclassified, compliant_count, ...}.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys


# ---------------------------------------------------------------- auditor

def audit(drives, rules, default_tier=None):
    """Pure policy evaluation. drives are joined dicts, no API access.

    drive: {vm, drive, tier}          (tier: int, 0 = unreadable)
    rule:  {match, drive?, tier}      (globs; first match wins)
    default_tier: expected tier for drives no rule matches; None means
                  unmatched drives are reported unclassified, not drift.
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

    return {'mode': 'read-only',
            'drives_audited': len(drives),
            'compliant_count': compliant,
            'drift': drift,
            'unclassified': unclassified,
            'summary': '%d drive(s): %d compliant, %d drift, '
                       '%d unclassified'
                       % (len(drives), compliant, len(drift),
                          len(unclassified))}


# -------------------------------------------------------------- collector

def collect():
    """Join machine_drives to VM names. Env-var auth.

    Drive rows live in the raw `machine_drives` table (no SDK manager);
    the tier field is `preferred_tier`, a STRING (finding #18).
    """
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
    client = VergeClient(**kwargs)

    vm_by_machine = {}
    for row in (dict(v) for v in client.vms.list()):
        if not row.get('is_snapshot'):
            vm_by_machine[row.get('machine')] = row.get('name')

    drives = []
    for row in client._request('GET', 'machine_drives',
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
    return drives


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--policy-json', required=True,
                   help='JSON list of {match, drive?, tier} rules')
    p.add_argument('--default-tier', type=int, default=None,
                   help='expected tier for unmatched drives; omit to '
                   'report them unclassified instead of drifted')
    p.add_argument('--fixture', help='JSON file with {drives: [...]} — '
                   'offline audit run, no API access')
    args = p.parse_args()

    rules = json.loads(args.policy_json)
    if args.fixture:
        with open(args.fixture) as fh:
            drives = json.load(fh)['drives']
    else:
        drives = collect()

    result = audit(drives, rules, args.default_tier)
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
