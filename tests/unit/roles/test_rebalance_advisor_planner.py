#!/usr/bin/env python3
"""Planner tests for the rebalance advisor (pure function, no API)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'rebalance_advisor', 'files'))
from advisor import plan  # noqa: E402

CFG = {'cpu_threshold': 85.0, 'ram_threshold': 85.0, 'max_moves': 2}


def node(name, cpu=10, ram=96000, ram_used=20000, maintenance=False,
         running=True):
    return {'name': name, 'cpu_usage': cpu, 'ram': ram,
            'ram_used': ram_used, 'maintenance': maintenance,
            'running': running}


def vm(name, on, ram=4096, running=True, ha_group='', is_snapshot=False,
       excluded=False, vm_cpu=None):
    return {'name': name, 'node_name': on, 'ram': ram, 'cpu_cores': 2,
            'running': running, 'ha_group': ha_group,
            'is_snapshot': is_snapshot, 'excluded': excluded,
            'vm_cpu': vm_cpu}


class TestNoAction:
    def test_single_node_is_honest_noop(self):
        r = plan([node('n1', cpu=99)], [vm('a', 'n1')], CFG)
        assert r['proposals'] == []
        assert any('single running node' in x for x in r['notes'])

    def test_stopped_second_node_counts_as_single(self):
        r = plan([node('n1', cpu=99), node('n2', running=False)],
                 [vm('a', 'n1')], CFG)
        assert r['proposals'] == []
        assert any('single running node' in x for x in r['notes'])

    def test_no_hotspot_no_proposals(self):
        r = plan([node('n1', cpu=40), node('n2', cpu=30)],
                 [vm('a', 'n1')], CFG)
        assert r['hotspots'] == []
        assert r['proposals'] == []
        assert any('nothing to do' in x for x in r['notes'])


class TestHotspots:
    def test_cpu_hotspot_proposes_move_to_coldest(self):
        r = plan([node('hot', cpu=95), node('cold', cpu=10),
                  node('warm', cpu=50)],
                 [vm('a', 'hot')], CFG)
        assert len(r['proposals']) == 1
        p = r['proposals'][0]
        assert (p['vm'], p['from_node'], p['to_node']) == ('a', 'hot', 'cold')
        assert 'cpu 95%' in p['reason']

    def test_ram_hotspot_detected(self):
        r = plan([node('hot', ram=100000, ram_used=90000),
                  node('cold')], [vm('a', 'hot')], CFG)
        assert r['hotspots'][0]['node'] == 'hot'
        assert any('ram 90%' in s for s in r['hotspots'][0]['reasons'])
        assert len(r['proposals']) == 1

    def test_smallest_vm_chosen(self):
        r = plan([node('hot', cpu=95), node('cold')],
                 [vm('big', 'hot', ram=32768), vm('small', 'hot', ram=2048)],
                 CFG)
        assert r['proposals'][0]['vm'] == 'small'

    def test_move_budget_respected(self):
        nodes = [node('h1', cpu=95), node('h2', cpu=95),
                 node('h3', cpu=95), node('cold')]
        vms = [vm('a', 'h1'), vm('b', 'h2'), vm('c', 'h3')]
        r = plan(nodes, vms, CFG)
        assert len(r['proposals']) == 2
        assert any('move budget' in x for x in r['notes'])


class TestExclusions:
    def test_no_balance_tag_excludes_vm(self):
        r = plan([node('hot', cpu=95), node('cold')],
                 [vm('pinned', 'hot', excluded=True)], CFG)
        assert r['proposals'] == []
        assert any('no movable VM' in x for x in r['notes'])

    def test_stopped_vms_not_moved(self):
        r = plan([node('hot', cpu=95), node('cold')],
                 [vm('off', 'hot', running=False)], CFG)
        assert r['proposals'] == []

    def test_snapshots_not_moved(self):
        r = plan([node('hot', cpu=95), node('cold')],
                 [vm('snap', 'hot', is_snapshot=True)], CFG)
        assert r['proposals'] == []


class TestTargets:
    def test_maintenance_node_is_not_a_target(self):
        r = plan([node('hot', cpu=95), node('mnt', maintenance=True),
                  node('ok', cpu=20)],
                 [vm('a', 'hot')], CFG)
        assert r['proposals'][0]['to_node'] == 'ok'

    def test_no_headroom_is_honest(self):
        r = plan([node('hot', cpu=95),
                  node('full', ram=10000, ram_used=9000)],
                 [vm('a', 'hot', ram=4096)], CFG)
        assert r['proposals'] == []
        assert any('no target with headroom' in x for x in r['notes'])

    def test_anti_affinity_blocks_colocation(self):
        r = plan([node('hot', cpu=95), node('peer', cpu=20),
                  node('free', cpu=30)],
                 [vm('a', 'hot', ha_group='web'),
                  vm('twin', 'peer', ha_group='web')], CFG)
        assert r['proposals'][0]['to_node'] == 'free'

    def test_colocate_groups_do_not_block(self):
        # '+'-prefixed HA groups mean co-locate, not spread
        r = plan([node('hot', cpu=95), node('peer', cpu=20)],
                 [vm('a', 'hot', ha_group='+stack'),
                  vm('twin', 'peer', ha_group='+stack')], CFG)
        assert r['proposals'][0]['to_node'] == 'peer'

    def test_target_ram_accounts_for_earlier_proposal(self):
        # Two hotspots draining into one target must not overfill it
        nodes = [node('h1', cpu=95), node('h2', cpu=95),
                 node('cold', ram=20000, ram_used=8000)]
        vms = [vm('a', 'h1', ram=6000), vm('b', 'h2', ram=6000)]
        r = plan(nodes, vms, CFG)
        # first move lands (8000+6000=70%), second would be 100% > 85%
        assert len(r['proposals']) == 1
        assert any('no target with headroom' in x for x in r['notes'])

    def test_reasons_are_present_and_specific(self):
        r = plan([node('hot', cpu=95), node('cold')],
                 [vm('a', 'hot', vm_cpu=42)], CFG)
        reason = r['proposals'][0]['reason']
        assert 'hot' in reason and 'cold' in reason
        assert 'vm cpu 42' in reason
