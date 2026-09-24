"""VergeOS rebalance advisor -- ADS-style hotspot remediation proposals.

Recommend-only by design: the advisor never migrates anything. It reads node
and VM state, finds sustained hotspots, and proposes the smallest set of live
migrations that would relieve them, each with a human-readable reason.
Applying proposals stays a human decision -- a live migration is the one
operation here whose blast radius is a running workload.

Usage:
  advisor.py                       # live, VERGEOS_* env for auth
  advisor.py --fixture state.json  # offline planner run (tests/CI)
  advisor.py --dump-state          # capture a fixture from the live system

Output: JSON on stdout -- {nodes, hotspots, proposals, notes}.
Errors: one scrubbed line on stderr, exit 2. The role runs this with
`no_log: true`, which censors the whole task result, so a traceback here
reaches the operator as the word "censored" and nothing else.

Field contract
--------------
NODE_FIELDS and VM_FIELDS are the only keys plan() may read, and exactly what
collect() must produce. They are declared rather than implied because this
collection keeps finding the same defect: code reading a field the projection
does not carry, getting None, and reporting a confident wrong answer. Checked
against 26.1.8 -- `nodes.list()` carries cpu_usage/ram/ram_used/maintenance/
running, and `vms.list()` carries node_name/ram/cpu_cores/running/ha_group/
is_snapshot/machine, all in the default projection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

EXCLUDE_TAG = 'no-balance'

NODE_FIELDS = ('name', 'cpu_usage', 'ram', 'ram_used', 'maintenance',
               'running')
VM_FIELDS = ('name', 'node_name', 'ram', 'cpu_cores', 'running', 'ha_group',
             'is_snapshot', 'excluded', 'vm_cpu')

# What the advisor says when it cannot tell whether a VM is pinned. Named so
# the tests and the ladder can assert on it rather than on prose.
EXCLUSIONS_UNKNOWN_NOTE = (
    "the '{tag}' tag could not be read ({why}), so a proposal could name a "
    "VM that is pinned -- hotspots are reported, proposals are withheld")


def exclusions_unknown_note(why):
    return EXCLUSIONS_UNKNOWN_NOTE.format(tag=EXCLUDE_TAG, why=why)


# ---------------------------------------------------------------- planner

def plan(nodes, vms, cfg):
    """Pure planner. nodes/vms are plain dicts; no API access here.

    node: NODE_FIELDS
    vm:   VM_FIELDS
    cfg:  {cpu_threshold, ram_threshold, max_moves, exclusions_known?}
    """
    notes = []
    proposals = []

    live_nodes = [n for n in nodes if n.get('running', True)]
    if len(live_nodes) < 2:
        notes.append(
            'single running node -- nothing to balance; proposals are only '
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
        notes.append('no node above thresholds -- nothing to do')
        return {'hotspots': [], 'proposals': [], 'notes': notes}

    # The one place this refuses to answer. `no-balance` is an opt-out an
    # operator sets precisely so a VM is never proposed for a move; if the
    # tag could not be read, every proposal is a guess about whether it is
    # allowed. Reporting the hotspot is still useful. Naming a VM is not.
    #
    # An empty tag list is not this case -- a system with no tags at all
    # answers the question fine, and the answer is "nothing is pinned".
    if cfg.get('exclusions_known', True) is not True:
        notes.append(cfg['exclusions_known'])
        return {'hotspots': hotspots, 'proposals': [], 'notes': notes}

    # Mutable working copies. Every headroom decision -- the check AND the
    # ordering -- reads these, so a target that has already been given a VM
    # this run looks as full as it will be.
    working = {n['name']: dict(n) for n in live_nodes}
    hot_names = {h['node'] for h in hotspots}

    def groups_on(node_name):
        return {v['ha_group'] for v in vms
                if v.get('node_name') == node_name and v.get('ha_group')
                and not str(v['ha_group']).startswith('+')}

    for spot in hotspots:
        if len(proposals) >= cfg['max_moves']:
            notes.append('move budget (%d) reached -- remaining hotspots '
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
            # Sorted over the working copies, not the originals. Sorting the
            # originals looks identical and quietly sends every proposal in a
            # run to the same node, because the earlier placements are not
            # reflected in the order.
            targets = sorted(
                (n for n in working.values()
                 if n['name'] not in hot_names and not n.get('maintenance')),
                key=lambda n: (n.get('cpu_usage', 0), ram_pct(n)))
            for target in targets:
                would = 100.0 * (target.get('ram_used', 0) + vm.get('ram', 0)) \
                    / target['ram'] if target.get('ram') else 100.0
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
                target['ram_used'] = target.get('ram_used', 0) + vm.get('ram', 0)
                placed = True
                break
            if placed:
                break
        if not placed:
            notes.append("hotspot '%s': no target with headroom "
                         "(or anti-affinity blocks every fit) -- "
                         "cluster may simply be full" % spot['node'])

    return {'hotspots': hotspots, 'proposals': proposals, 'notes': notes}


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


def sample_nodes(conn, samples, interval):
    """Average node CPU over N samples, so a spike is not a hotspot."""
    series = {}
    latest = {}
    for i in range(max(1, samples)):
        for row in (dict(n) for n in conn.nodes.list()):
            series.setdefault(row['name'], []).append(row.get('cpu_usage') or 0)
            latest[row['name']] = row
        if i + 1 < samples:
            time.sleep(interval)

    return [{'name': name,
             'cpu_usage': sum(readings) / len(readings),
             'ram': latest[name].get('ram') or 0,
             'ram_used': latest[name].get('ram_used') or 0,
             'maintenance': bool(latest[name].get('maintenance')),
             'running': bool(latest[name].get('running', True))}
            for name, readings in sorted(series.items())]


def excluded_vm_keys(conn):
    """VM keys carrying the no-balance tag, and whether we could tell.

    Returns (keys, problem). `problem` is None when the answer is trustworthy
    -- including the ordinary case of a system with no such tag, where the
    answer is simply "nothing is pinned".

    This used to be wrapped in a bare `except Exception: pass` with the
    comment "tags are an opt-out convenience, never fatal". It is not a
    convenience: it is the mechanism by which an operator says *do not
    propose moving this VM*, and swallowing its failure makes the advisor
    fail open, silently, in the one direction that matters.
    """
    try:
        tag_keys = {dict(t).get('$key') for t in conn.tags.list()
                    if dict(t).get('name') == EXCLUDE_TAG}
        if not tag_keys:
            return set(), None
        keys = set()
        # member format is "vms/<key>" -- the same join protect_ctl.py uses.
        for member_row in conn._request('GET', 'tag_members',
                                        params={'fields': 'most'}):
            member = str(member_row.get('member', ''))
            if member_row.get('tag') in tag_keys and member.startswith('vms/'):
                keys.add(int(member.split('/', 1)[1]))
        return keys, None
    except Exception as exc:                      # noqa: BLE001
        return set(), '%s: %s' % (type(exc).__name__, scrub(exc))


def vm_cpu_by_machine(conn):
    """Per-VM CPU, for the reason text. Absent is a note, not a failure.

    Unlike the tag lookup, this only makes a proposal less informative. It
    still says so, because "the reason never mentions vm cpu" is otherwise
    indistinguishable from "the VM is idle".
    """
    try:
        return {row.get('machine'): row.get('vmusage_cpu')
                for row in conn._request('GET', 'machine_stats',
                                         params={'fields': 'most'})}, None
    except Exception as exc:                      # noqa: BLE001
        return {}, ('per-VM CPU is unavailable (%s: %s), so proposals name '
                    'the node load but not the VM share'
                    % (type(exc).__name__, scrub(exc)))


def collect(conn, samples, interval):
    """Bulk reads + optional CPU re-sampling. Returns (nodes, vms, cfg_notes).

    cfg_notes is {notes: [...], exclusions_known: True | '<why not>'}.
    """
    nodes = sample_nodes(conn, samples, interval)

    excluded, tag_problem = excluded_vm_keys(conn)
    stats, stats_problem = vm_cpu_by_machine(conn)

    vms = []
    for row in (dict(v) for v in conn.vms.list()):
        vms.append({'name': row.get('name'),
                    'node_name': row.get('node_name'),
                    'ram': row.get('ram') or 0,
                    'cpu_cores': row.get('cpu_cores') or 0,
                    'running': bool(row.get('running')),
                    'ha_group': row.get('ha_group') or '',
                    'is_snapshot': bool(row.get('is_snapshot')),
                    'excluded': row.get('$key') in excluded,
                    'vm_cpu': stats.get(row.get('machine'))})

    notes = [n for n in (stats_problem,) if n]
    known = True if tag_problem is None \
        else exclusions_unknown_note(tag_problem)
    return nodes, vms, {'notes': notes, 'exclusions_known': known}


def scrub(text):
    """Never let a credential out through an error path."""
    out = str(text)
    for name in ('VERGEOS_PASSWORD', 'VERGEOS_TOKEN'):
        secret = os.environ.get(name)
        if secret:
            out = out.replace(secret, '<%s>' % name)
    return out


def main():
    p = argparse.ArgumentParser(description='VergeOS rebalance advisor')
    p.add_argument('--cpu-threshold', type=float, default=85.0)
    p.add_argument('--ram-threshold', type=float, default=85.0)
    p.add_argument('--max-moves', type=int, default=2)
    p.add_argument('--samples', type=int, default=3,
                   help='CPU samples to average (sustained, not spikes)')
    p.add_argument('--interval', type=float, default=5.0,
                   help='seconds between samples')
    p.add_argument('--fixture', help='JSON file with {nodes, vms} -- '
                   'offline planner run, no API access')
    p.add_argument('--dump-state', action='store_true',
                   help='print the collected {nodes, vms} and stop. A '
                        'fixture captured from a real system beats one '
                        'written by hand to agree with the code.')
    args = p.parse_args()

    extra_notes = []
    try:
        if args.fixture:
            with open(args.fixture) as fh:
                state = json.load(fh)
            nodes, vms = state['nodes'], state['vms']
            cfg_notes = {'notes': [], 'exclusions_known': True}
        else:
            nodes, vms, cfg_notes = collect(client(), args.samples,
                                            args.interval)
        extra_notes = cfg_notes['notes']
    except Exception as exc:                      # noqa: BLE001
        sys.stderr.write('%s: %s\n' % (type(exc).__name__, scrub(exc)))
        return 2

    if args.dump_state:
        json.dump({'nodes': nodes, 'vms': vms}, sys.stdout, indent=2)
        print()
        return 0

    result = plan(nodes, vms, {'cpu_threshold': args.cpu_threshold,
                               'ram_threshold': args.ram_threshold,
                               'max_moves': args.max_moves,
                               'exclusions_known':
                                   cfg_notes['exclusions_known']})
    result['notes'] = extra_notes + result['notes']
    result['nodes'] = nodes
    result['mode'] = 'recommend-only'
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
