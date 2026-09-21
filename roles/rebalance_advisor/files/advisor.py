#!/usr/bin/env python3
"""VergeOS rebalance advisor — ADS-style hotspot remediation proposals.

Recommend-only by design: the advisor never migrates anything. It reads
node and VM state (three bulk API calls + optional CPU re-samples),
finds sustained hotspots, and proposes the smallest set of live
migrations that would relieve them, each with a human-readable reason.
Applying proposals stays a human/live-migration decision until the lab
can live-verify an apply path (needs >= 2 nodes — see GAP-ANALYSIS #4).

Usage:
  advisor.py                      # live, VERGEOS_* env for auth
  advisor.py --fixture state.json # offline planner run (tests/CI)

Output: JSON on stdout — {nodes, hotspots, proposals, notes}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

EXCLUDE_TAG = 'no-balance'


# ---------------------------------------------------------------- planner

def plan(nodes, vms, cfg):
    """Pure planner. nodes/vms are plain dicts; no API access here.

    node: {name, cpu_usage, ram, ram_used, maintenance, running}
    vm:   {name, node_name, ram, cpu_cores, running, ha_group,
           is_snapshot, excluded (bool), vm_cpu (optional)}
    cfg:  {cpu_threshold, ram_threshold, max_moves}
    """
    notes = []
    proposals = []

    live_nodes = [n for n in nodes if n.get('running', True)]
    if len(live_nodes) < 2:
        notes.append(
            'single running node — nothing to balance; proposals are only '
            'possible with >= 2 nodes')
        return {'hotspots': [], 'proposals': [], 'notes': notes}

    def ram_pct(n):
        return 100.0 * n.get('ram_used', 0) / n['ram'] if n.get('ram') else 0.0

    hotspots = []
    for n in live_nodes:
        reasons = []
        if n.get('cpu_usage', 0) > cfg['cpu_threshold']:
            reasons.append('cpu %.0f%% > %d%%'
                           % (n['cpu_usage'], cfg['cpu_threshold']))
        if ram_pct(n) > cfg['ram_threshold']:
            reasons.append('ram %.0f%% > %d%%'
                           % (ram_pct(n), cfg['ram_threshold']))
        if reasons:
            hotspots.append({'node': n['name'], 'reasons': reasons})

    if not hotspots:
        notes.append('no node above thresholds — nothing to do')
        return {'hotspots': [], 'proposals': [], 'notes': notes}

    by_name = {n['name']: dict(n) for n in live_nodes}
    hot_names = {h['node'] for h in hotspots}

    def groups_on(node_name):
        return {v['ha_group'] for v in vms
                if v.get('node_name') == node_name and v.get('ha_group')
                and not str(v['ha_group']).startswith('+')}

    for spot in hotspots:
        if len(proposals) >= cfg['max_moves']:
            notes.append('move budget (%d) reached — remaining hotspots '
                         'left for the next run' % cfg['max_moves'])
            break

        candidates = [
            v for v in vms
            if v.get('node_name') == spot['node']
            and v.get('running') and not v.get('is_snapshot')
            and not v.get('excluded')
        ]
        if not candidates:
            notes.append("hotspot '%s': no movable VM (all stopped, "
                         "snapshots, or '%s'-tagged)"
                         % (spot['node'], EXCLUDE_TAG))
            continue

        # Smallest RAM first: the least-disruptive move that helps.
        candidates.sort(key=lambda v: (v.get('ram', 0), v.get('name', '')))

        placed = False
        for vm in candidates:
            targets = sorted(
                (n for n in live_nodes
                 if n['name'] not in hot_names and not n.get('maintenance')),
                key=lambda n: (n.get('cpu_usage', 0), ram_pct(n)))
            for target in targets:
                t = by_name[target['name']]
                would = 100.0 * (t.get('ram_used', 0) + vm.get('ram', 0)) \
                    / t['ram'] if t.get('ram') else 100.0
                if would > cfg['ram_threshold']:
                    continue
                if vm.get('ha_group') \
                        and not str(vm['ha_group']).startswith('+') \
                        and vm['ha_group'] in groups_on(target['name']):
                    continue
                reason = ("%s: %s; move '%s' (%d MB%s) -> %s "
                          "(target would be at %.0f%% ram)"
                          % (spot['node'], ' and '.join(spot['reasons']),
                             vm['name'], vm.get('ram', 0),
                             ', vm cpu %s' % vm['vm_cpu']
                             if vm.get('vm_cpu') else '',
                             target['name'], would))
                proposals.append({'vm': vm['name'],
                                  'from_node': spot['node'],
                                  'to_node': target['name'],
                                  'reason': reason})
                t['ram_used'] = t.get('ram_used', 0) + vm.get('ram', 0)
                placed = True
                break
            if placed:
                break
        if not placed:
            notes.append("hotspot '%s': no target with headroom "
                         "(or anti-affinity blocks every fit) — "
                         "cluster may simply be full" % spot['node'])

    return {'hotspots': hotspots, 'proposals': proposals, 'notes': notes}


# -------------------------------------------------------------- collector

def collect(samples, interval):
    """Three bulk reads + optional CPU re-sampling. Env-var auth."""
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

    cpu_series = {}
    for i in range(max(1, samples)):
        for row in (dict(n) for n in client.nodes.list()):
            cpu_series.setdefault(row['name'], []).append(
                row.get('cpu_usage') or 0)
            if i == 0:
                cpu_series.setdefault('_row:%s' % row['name'], []).append(row)
        if i + 1 < samples:
            time.sleep(interval)

    nodes = []
    for name, series in cpu_series.items():
        if name.startswith('_row:'):
            continue
        row = cpu_series['_row:%s' % name][0]
        nodes.append({'name': name,
                      'cpu_usage': sum(series) / len(series),
                      'ram': row.get('ram') or 0,
                      'ram_used': row.get('ram_used') or 0,
                      'maintenance': bool(row.get('maintenance')),
                      'running': bool(row.get('running', True))})

    excluded_vm_keys = set()
    try:
        tag_keys = {dict(t).get('$key') for t in client.tags.list()
                    if dict(t).get('name') == EXCLUDE_TAG}
        if tag_keys:
            for m in client._request('GET', 'tag_members',
                                     params={'fields': 'most'}):
                member = str(m.get('member', ''))
                if m.get('tag') in tag_keys and member.startswith('vms/'):
                    excluded_vm_keys.add(int(member.split('/', 1)[1]))
    except Exception:
        pass  # tags are an opt-out convenience, never fatal

    stats_by_machine = {}
    try:
        for row in client._request('GET', 'machine_stats',
                                   params={'fields': 'most'}):
            stats_by_machine[row.get('machine')] = row
    except Exception:
        pass

    vms = []
    for row in (dict(v) for v in client.vms.list()):
        stats = stats_by_machine.get(row.get('machine'), {})
        vms.append({'name': row.get('name'),
                    'node_name': row.get('node_name'),
                    'ram': row.get('ram') or 0,
                    'cpu_cores': row.get('cpu_cores') or 0,
                    'running': bool(row.get('running')),
                    'ha_group': row.get('ha_group') or '',
                    'is_snapshot': bool(row.get('is_snapshot')),
                    'excluded': row.get('$key') in excluded_vm_keys,
                    'vm_cpu': stats.get('vmusage_cpu')})
    return nodes, vms


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpu-threshold', type=float, default=85.0)
    p.add_argument('--ram-threshold', type=float, default=85.0)
    p.add_argument('--max-moves', type=int, default=2)
    p.add_argument('--samples', type=int, default=3,
                   help='CPU samples to average (sustained, not spikes)')
    p.add_argument('--interval', type=float, default=5.0,
                   help='seconds between samples')
    p.add_argument('--fixture', help='JSON file with {nodes, vms} — '
                   'offline planner run, no API access')
    args = p.parse_args()

    if args.fixture:
        with open(args.fixture) as fh:
            state = json.load(fh)
        nodes, vms = state['nodes'], state['vms']
    else:
        nodes, vms = collect(args.samples, args.interval)

    result = plan(nodes, vms, {'cpu_threshold': args.cpu_threshold,
                               'ram_threshold': args.ram_threshold,
                               'max_moves': args.max_moves})
    result['nodes'] = nodes
    result['mode'] = 'recommend-only'
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
