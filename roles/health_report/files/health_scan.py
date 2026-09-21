"""VergeOS daily-green health scan — read-only, silence-is-the-alert.

Five checks, each green / amber / red, rolled into one overall verdict
and a one-line digest suitable for a webhook. The scan never mutates
anything; a check whose data cannot be read goes RED, never silently
green — a watchdog that fails quiet is worse than no watchdog.

Checks:
  alarms    — severe levels red, warnings amber; snoozed excluded
  nodes     — any node down red, maintenance amber
  capacity  — per-tier used% against amber/red thresholds
  snapshots — newest cloud snapshot age against cadence thresholds
  nas       — NAS services WITH volumes must be running (a stopped
              zero-volume service serves nothing and is ignored)

Usage:
  health_scan.py                      # live, VERGEOS_* env for auth
  health_scan.py --fixture state.json # offline verdict run (tests/CI);
                                      # the fixture carries its own 'now'

Output: JSON on stdout — {overall, digest, checks, counts, errors}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Alarm levels observed/expected on 26.x. `status` on an alarm row is
# human-readable TEXT, not a state machine — never branch on it.
RED_LEVELS = ('error', 'critical', 'alert', 'emergency', 'failure')
INFO_LEVELS = ('info', 'information', 'notice', 'message', 'debug')


# --------------------------------------------------------------- verdicts

def _check(name, verdict, detail):
    return {'check': name, 'verdict': verdict, 'detail': detail}


def _unreadable(name, err):
    return _check(name, 'red', 'could not read %s data (%s) — treating '
                  'unreadable as red, never green' % (name, err))


def assess(state, cfg):
    """Pure verdict shaping. state is plain dicts; no API access here.

    state: {now, alarms|None, nodes|None, tiers|None, snapshots|None,
            nas|None, errors: {section: msg}}
    cfg:   {capacity_amber_pct, capacity_red_pct,
            snapshot_amber_hours, snapshot_red_hours}
    """
    now = float(state['now'])
    errors = state.get('errors') or {}
    checks = []

    # alarms -----------------------------------------------------------
    alarms = state.get('alarms')
    if alarms is None:
        checks.append(_unreadable('alarms', errors.get('alarms', '?')))
    else:
        live = [a for a in alarms
                if not (a.get('snooze') and float(a['snooze']) > now)]
        snoozed = len(alarms) - len(live)
        reds = [a for a in live if str(a.get('level', '')).lower()
                in RED_LEVELS]
        infos = [a for a in live if str(a.get('level', '')).lower()
                 in INFO_LEVELS]
        # warnings AND unknown levels: an unrecognized severity is
        # never silently green
        ambers = [a for a in live if a not in reds and a not in infos]

        def names(rows):
            return ', '.join(sorted(str(a.get('alarm_type') or '?')
                                    for a in rows))
        suffix = ' (%d snoozed excluded)' % snoozed if snoozed else ''
        if reds:
            checks.append(_check('alarms', 'red', '%d severe: %s%s'
                                 % (len(reds), names(reds), suffix)))
        elif ambers:
            checks.append(_check('alarms', 'amber', '%d warning(s): %s%s'
                                 % (len(ambers), names(ambers), suffix)))
        else:
            checks.append(_check('alarms', 'green',
                                 'no active alarms%s' % suffix))

    # nodes ------------------------------------------------------------
    nodes = state.get('nodes')
    if nodes is None:
        checks.append(_unreadable('nodes', errors.get('nodes', '?')))
    else:
        down = [n['name'] for n in nodes if not n.get('running', True)]
        maint = [n['name'] for n in nodes
                 if n.get('running', True) and n.get('maintenance')]
        if down:
            checks.append(_check('nodes', 'red', '%d/%d node(s) down: %s'
                                 % (len(down), len(nodes),
                                    ', '.join(sorted(down)))))
        elif maint:
            checks.append(_check('nodes', 'amber', 'in maintenance: %s'
                                 % ', '.join(sorted(maint))))
        else:
            checks.append(_check('nodes', 'green', '%d/%d node(s) running'
                                 % (len(nodes), len(nodes))))

    # capacity ---------------------------------------------------------
    tiers = state.get('tiers')
    if tiers is None:
        checks.append(_unreadable('capacity', errors.get('tiers', '?')))
    else:
        rated = [(t, 100.0 * t.get('used', 0) / t['capacity'])
                 for t in tiers if t.get('capacity')]
        over_red = ['tier %s at %.0f%%' % (t.get('tier'), pct)
                    for t, pct in rated if pct >= cfg['capacity_red_pct']]
        over_amber = ['tier %s at %.0f%%' % (t.get('tier'), pct)
                      for t, pct in rated
                      if cfg['capacity_amber_pct'] <= pct
                      < cfg['capacity_red_pct']]
        if over_red:
            checks.append(_check('capacity', 'red', '%s (red >= %d%%)'
                                 % ('; '.join(over_red),
                                    cfg['capacity_red_pct'])))
        elif over_amber:
            checks.append(_check('capacity', 'amber', '%s (amber >= %d%%)'
                                 % ('; '.join(over_amber),
                                    cfg['capacity_amber_pct'])))
        elif rated:
            worst_t, worst = max(rated, key=lambda r: r[1])
            checks.append(_check('capacity', 'green',
                                 'max utilization %.0f%% (tier %s)'
                                 % (worst, worst_t.get('tier'))))
        else:
            checks.append(_check('capacity', 'red',
                                 'no storage tier reports capacity'))

    # snapshots --------------------------------------------------------
    snaps = state.get('snapshots')
    if snaps is None:
        checks.append(_unreadable('snapshots', errors.get('snapshots', '?')))
    else:
        created = [float(s['created']) for s in snaps if s.get('created')]
        if not created:
            checks.append(_check('snapshots', 'red',
                                 'no cloud snapshots exist — the '
                                 'protection cadence is silent'))
        else:
            age_h = (now - max(created)) / 3600.0
            detail = ('newest cloud snapshot is %.1f h old '
                      '(%d total; amber >= %d h, red >= %d h)'
                      % (age_h, len(created),
                         cfg['snapshot_amber_hours'],
                         cfg['snapshot_red_hours']))
            if age_h >= cfg['snapshot_red_hours']:
                checks.append(_check('snapshots', 'red', detail))
            elif age_h >= cfg['snapshot_amber_hours']:
                checks.append(_check('snapshots', 'amber', detail))
            else:
                checks.append(_check('snapshots', 'green', detail))

    # nas --------------------------------------------------------------
    nas = state.get('nas')
    if nas is None:
        checks.append(_unreadable('nas', errors.get('nas', '?')))
    else:
        serving = [n for n in nas if n.get('volume_count')]
        idle = len(nas) - len(serving)
        down = [n['name'] for n in serving if not n.get('vm_running')]
        suffix = (' (%d volume-less service(s) ignored)' % idle
                  if idle else '')
        if down:
            checks.append(_check('nas', 'red', 'NAS service(s) down: %s%s'
                                 % (', '.join(sorted(down)), suffix)))
        elif serving:
            checks.append(_check('nas', 'green',
                                 '%d NAS service(s) running%s'
                                 % (len(serving), suffix)))
        else:
            checks.append(_check('nas', 'green',
                                 'no NAS services with volumes%s' % suffix))

    # roll-up ----------------------------------------------------------
    verdicts = [c['verdict'] for c in checks]
    overall = ('red' if 'red' in verdicts
               else 'amber' if 'amber' in verdicts else 'green')
    if overall == 'green':
        digest = 'GREEN — all %d checks green' % len(checks)
    else:
        digest = '%s — %s' % (overall.upper(), '; '.join(
            '%s: %s' % (c['check'], c['detail'])
            for c in checks if c['verdict'] != 'green'))

    return {'overall': overall,
            'digest': digest,
            'checks': checks,
            'counts': {v: verdicts.count(v)
                       for v in ('green', 'amber', 'red')},
            'errors': errors,
            'mode': 'read-only'}


# -------------------------------------------------------------- collector

def collect():
    """Five bulk reads, each individually fault-isolated. Env-var auth.

    nas_services MUST go through the SDK manager — the raw table 404s
    on 26.1.8 (spike-verified).
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

    state = {'now': time.time(), 'errors': {}}

    def grab(section, attr, shape):
        try:
            rows = [dict(r) for r in getattr(client, attr).list()]
            state[section] = [shape(r) for r in rows]
        except Exception as exc:  # fault-isolate: unreadable -> red
            state[section] = None
            state['errors'][section] = '%s: %s' % (type(exc).__name__, exc)

    grab('alarms', 'alarms',
         lambda r: {'level': r.get('level'), 'snooze': r.get('snooze'),
                    'alarm_type': r.get('alarm_type'),
                    'status': r.get('status')})
    grab('nodes', 'nodes',
         lambda r: {'name': r.get('name'),
                    'running': bool(r.get('running', True)),
                    'maintenance': bool(r.get('maintenance'))})
    grab('tiers', 'storage_tiers',
         lambda r: {'tier': r.get('tier'),
                    'capacity': r.get('capacity') or 0,
                    'used': r.get('used') or 0,
                    'allocated': r.get('allocated'),
                    'dedupe_ratio': r.get('dedupe_ratio')})
    grab('snapshots', 'cloud_snapshots',
         lambda r: {'name': r.get('name'), 'created': r.get('created'),
                    'expires': r.get('expires')})
    grab('nas', 'nas_services',
         lambda r: {'name': r.get('name'),
                    'vm_running': bool(r.get('vm_running')),
                    'vm_status': r.get('vm_status'),
                    'volume_count': r.get('volume_count') or 0})
    return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capacity-amber', type=float, default=80.0)
    p.add_argument('--capacity-red', type=float, default=90.0)
    p.add_argument('--snap-amber-hours', type=float, default=26.0,
                   help='daily cadence + slack')
    p.add_argument('--snap-red-hours', type=float, default=50.0,
                   help='two missed dailies')
    p.add_argument('--fixture', help='JSON file with saved state '
                   '(carries its own now) — offline run, no API access')
    args = p.parse_args()

    if args.fixture:
        with open(args.fixture) as fh:
            state = json.load(fh)
        state.setdefault('errors', {})
    else:
        state = collect()

    result = assess(state, {'capacity_amber_pct': args.capacity_amber,
                            'capacity_red_pct': args.capacity_red,
                            'snapshot_amber_hours': args.snap_amber_hours,
                            'snapshot_red_hours': args.snap_red_hours})
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
