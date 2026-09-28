"""Planner and collector tests for the rebalance advisor.

The planner half was already covered. The two halves that were not:

  * the COLLECTOR, which is where this collection's recurring defect lives --
    code reading a field the projection does not carry, getting None, and
    reporting a confident wrong answer. Nothing tested that collect() emits
    what plan() reads.
  * the `no-balance` opt-out failing open. Its lookup was wrapped in a bare
    `except Exception: pass`, so a tags endpoint that errored turned a pinned
    VM into a movable one with no note, no warning and no failure.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'rebalance_advisor', 'files'))
from advisor import (  # noqa: E402
    EXCLUDE_TAG,
    NODE_FIELDS,
    VM_FIELDS,
    collect,
    excluded_vm_keys,
    plan,
    sample_nodes,
    scrub,
    vm_cpu_by_machine,
)

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

    def test_the_input_is_not_mutated(self):
        """plan() accounts for headroom by bookkeeping, on its own copies.

        The caller prints `nodes` in the report; if the planner edited them
        in place the report would show a cluster state that never existed.
        """
        nodes = [node('hot', cpu=95), node('cold', ram_used=1000)]
        plan(nodes, [vm('a', 'hot', ram=4096)], CFG)
        assert nodes[1]['ram_used'] == 1000


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


class TestWhenPinningCannotBeChecked:
    """Failing open, silently, in the one direction that matters.

    `no-balance` is how an operator says *never propose moving this*. The
    lookup that answers it was wrapped in `except Exception: pass`, so a
    tags endpoint that errored produced an empty exclusion set -- which is
    indistinguishable from "nothing is pinned" -- and the advisor went on to
    name pinned VMs with full confidence.
    """

    UNKNOWN = dict(CFG, exclusions_known='tags unreadable (ConnectionError)')

    def test_no_proposals_are_made(self):
        r = plan([node('hot', cpu=95), node('cold')],
                 [vm('maybe-pinned', 'hot')], self.UNKNOWN)
        assert r['proposals'] == []

    def test_the_hotspot_is_still_reported(self):
        """Withholding the proposal is not the same as saying nothing."""
        r = plan([node('hot', cpu=95), node('cold')],
                 [vm('a', 'hot')], self.UNKNOWN)
        assert r['hotspots'][0]['node'] == 'hot'

    def test_the_reason_is_in_the_notes(self):
        r = plan([node('hot', cpu=95), node('cold')],
                 [vm('a', 'hot')], self.UNKNOWN)
        assert self.UNKNOWN['exclusions_known'] in r['notes']

    def test_a_system_with_no_tags_is_not_this_case(self):
        """"Nothing is pinned" is a real answer, not a failure to answer."""
        r = plan([node('hot', cpu=95), node('cold')],
                 [vm('a', 'hot')], dict(CFG, exclusions_known=True))
        assert len(r['proposals']) == 1

    def test_the_default_is_to_trust_the_caller(self):
        assert plan([node('hot', cpu=95), node('cold')],
                    [vm('a', 'hot')], CFG)['proposals']


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

    def test_earlier_placements_change_the_target_ORDER_too(self):
        """Not just the headroom check -- the ordering.

        Two identical cold nodes and two hotspots. The target list used to be
        sorted over the ORIGINAL node dicts while only the copies were
        updated, so both proposals went to the same node while the other sat
        empty. It still fits, so no test that only checked headroom could see
        it.
        """
        nodes = [node('h1', cpu=95), node('h2', cpu=95),
                 node('c1', cpu=10, ram=100000, ram_used=10000),
                 node('c2', cpu=10, ram=100000, ram_used=10000)]
        vms = [vm('a', 'h1', ram=20000), vm('b', 'h2', ram=20000)]
        targets = [p['to_node'] for p in plan(nodes, vms, CFG)['proposals']]
        assert len(targets) == 2
        assert sorted(targets) == ['c1', 'c2'], (
            'both proposals were sent to %s while the other node stayed '
            'empty' % targets[0])

    def test_reasons_are_present_and_specific(self):
        r = plan([node('hot', cpu=95), node('cold')],
                 [vm('a', 'hot', vm_cpu=42)], CFG)
        reason = r['proposals'][0]['reason']
        assert 'hot' in reason and 'cold' in reason
        assert 'vm cpu 42' in reason


# ------------------------------------------------------------- collector

class FakeRow(dict):
    """SDK rows are dict-like; the collector wraps them in dict()."""


class FakeManager:
    def __init__(self, rows):
        self.rows = rows

    def list(self, **kwargs):
        return [FakeRow(r) for r in self.rows]


class FakeClient:
    """Rows shaped as 26.1.8 returned them, keys and all.

    nodes.list() and vms.list() default projections, verified against the
    live system rather than invented -- the point of this fixture is to be
    the platform's answer, not the code's.
    """

    NODES = [
        {'$key': 1, 'name': 'node1', 'machine': 1, 'cpu_usage': 0,
         'ram': 94208, 'ram_used': 14526, 'maintenance': False,
         'running': True, 'status': 'running', 'cores': 32},
        {'$key': 2, 'name': 'node2', 'machine': 31, 'cpu_usage': 0,
         'ram': 94208, 'ram_used': 8028, 'maintenance': False,
         'running': True, 'status': 'running', 'cores': 32},
    ]
    VMS = [
        {'$key': 1, 'name': 'app-01', 'machine': 48, 'node_name': 'node1',
         'ram': 1024, 'cpu_cores': 1, 'running': True, 'ha_group': '',
         'is_snapshot': False, 'status': 'running'},
        {'$key': 11, 'name': 'golden-image', 'machine': 20,
         'node_name': None, 'ram': 2048, 'cpu_cores': 2, 'running': False,
         'ha_group': '', 'is_snapshot': False, 'status': 'stopped'},
    ]
    TAGS = [{'$key': 1, 'name': 'gold'}, {'$key': 3, 'name': 'DB'}]
    TAG_MEMBERS = []
    MACHINE_STATS = [{'machine': 48, 'vmusage_cpu': 7},
                     {'machine': 1, 'vmusage_cpu': 0}]

    def __init__(self, tags=None, tag_members=None, machine_stats=None,
                 raises=None):
        self.nodes = FakeManager(self.NODES)
        self.vms = FakeManager(self.VMS)
        self.tags = FakeManager(self.TAGS if tags is None else tags)
        self._tag_members = (self.TAG_MEMBERS if tag_members is None
                             else tag_members)
        self._machine_stats = (self.MACHINE_STATS if machine_stats is None
                               else machine_stats)
        self._raises = raises or {}

    def _request(self, method, endpoint, params=None):
        if endpoint in self._raises:
            raise self._raises[endpoint]
        if endpoint == 'tag_members':
            return self._tag_members
        if endpoint == 'machine_stats':
            return self._machine_stats
        raise AssertionError('unexpected endpoint %r' % endpoint)


class TestTheCollectorProducesWhatThePlannerReads:
    """The contract nothing checked.

    Every defect in this collection's running tally has the same shape: a
    field name that is accepted, discarded or absent, and code that reads the
    result as if it were an answer. A collector whose output nobody compares
    to the planner's input is that defect waiting to happen.
    """

    def test_node_rows_carry_exactly_the_declared_fields(self):
        nodes, unused_vms, unused_cfg = collect(FakeClient(), 1, 0)
        assert set(nodes[0]) == set(NODE_FIELDS)

    def test_vm_rows_carry_exactly_the_declared_fields(self):
        unused_nodes, vms, unused_cfg = collect(FakeClient(), 1, 0)
        assert set(vms[0]) == set(VM_FIELDS)

    def test_the_collected_state_plans_without_a_keyerror(self):
        nodes, vms, cfg = collect(FakeClient(), 1, 0)
        result = plan(nodes, vms, dict(CFG, **{
            'exclusions_known': cfg['exclusions_known']}))
        assert result['hotspots'] == []       # an idle lab really is idle

    def test_node_ram_and_vm_ram_are_the_same_unit(self):
        """Both MB on 26.1.8 -- node 94208, VM 1024. The planner adds a VM's
        ram to a node's ram_used, so a unit mismatch would be silent and
        wrong by 1024x, which is exactly issue #27's shape."""
        nodes, vms, unused = collect(FakeClient(), 1, 0)
        assert nodes[0]['ram'] == 94208 and vms[0]['ram'] == 1024

    def test_cpu_is_averaged_over_the_samples(self):
        client = FakeClient()
        readings = iter([[dict(client.NODES[0], cpu_usage=90)],
                         [dict(client.NODES[0], cpu_usage=10)]])
        client.nodes.list = lambda **kw: next(readings)
        assert sample_nodes(client, 2, 0)[0]['cpu_usage'] == 50

    def test_a_stopped_vm_is_collected_as_stopped(self):
        unused, vms, unused2 = collect(FakeClient(), 1, 0)
        assert [v['running'] for v in vms] == [True, False]

    def test_vm_cpu_is_joined_on_the_machine_key(self):
        """node_name joins to the node; vmusage_cpu joins on `machine`, which
        is a different key from the VM's own $key -- app-01 is $key 1 and
        machine 48, and machine_stats also has a row for machine 1."""
        unused, vms, unused2 = collect(FakeClient(), 1, 0)
        assert vms[0]['vm_cpu'] == 7


class TestTheExclusionLookup:
    def test_no_such_tag_means_nothing_is_pinned(self):
        keys, problem = excluded_vm_keys(FakeClient())
        assert (keys, problem) == (set(), None)

    def test_a_tagged_vm_is_found_through_the_member_join(self):
        client = FakeClient(
            tags=[{'$key': 9, 'name': EXCLUDE_TAG}],
            tag_members=[{'tag': 9, 'member': 'vms/11'},
                         {'tag': 9, 'member': 'nodes/1'},
                         {'tag': 2, 'member': 'vms/1'}])
        keys, problem = excluded_vm_keys(client)
        assert keys == {11} and problem is None

    def test_a_failure_is_reported_not_swallowed(self):
        client = FakeClient(tags=[{'$key': 9, 'name': EXCLUDE_TAG}],
                            raises={'tag_members': RuntimeError('boom')})
        keys, problem = excluded_vm_keys(client)
        assert keys == set()
        assert 'RuntimeError' in problem and 'boom' in problem

    def test_collect_turns_that_failure_into_a_withheld_run(self):
        client = FakeClient(tags=[{'$key': 9, 'name': EXCLUDE_TAG}],
                            raises={'tag_members': RuntimeError('boom')})
        unused, unused2, cfg = collect(client, 1, 0)
        assert cfg['exclusions_known'] is not True
        assert 'proposals are withheld' in cfg['exclusions_known']

    def test_the_tagged_vm_arrives_at_the_planner_as_excluded(self):
        client = FakeClient(tags=[{'$key': 9, 'name': EXCLUDE_TAG}],
                            tag_members=[{'tag': 9, 'member': 'vms/1'}])
        unused, vms, unused2 = collect(client, 1, 0)
        assert [v['excluded'] for v in vms] == [True, False]


class TestMissingStatsAreANoteNotASilence:
    """Losing per-VM CPU only makes a proposal less informative -- but a
    reason that never mentions VM CPU is otherwise indistinguishable from a
    VM that is idle."""

    def test_the_run_continues(self):
        client = FakeClient(raises={'machine_stats': RuntimeError('nope')})
        nodes, vms, cfg = collect(client, 1, 0)
        assert nodes and vms
        assert cfg['exclusions_known'] is True

    def test_and_says_so(self):
        client = FakeClient(raises={'machine_stats': RuntimeError('nope')})
        unused, unused2, cfg = collect(client, 1, 0)
        assert any('per-VM CPU is unavailable' in n for n in cfg['notes'])

    def test_a_working_lookup_adds_no_noise(self):
        unused, unused2, cfg = collect(FakeClient(), 1, 0)
        assert cfg['notes'] == []

    def test_the_helper_reports_its_own_problem(self):
        stats, problem = vm_cpu_by_machine(
            FakeClient(raises={'machine_stats': RuntimeError('nope')}))
        assert stats == {} and 'nope' in problem


class TestScrubbing:
    @pytest.mark.parametrize('var', ['VERGEOS_PASSWORD', 'VERGEOS_TOKEN'])
    def test_secrets_never_reach_stderr(self, monkeypatch, var):
        monkeypatch.setenv(var, 's3cret')
        assert 's3cret' not in scrub('auth failed for s3cret')

    def test_nothing_set_is_a_passthrough(self, monkeypatch):
        monkeypatch.delenv('VERGEOS_PASSWORD', raising=False)
        monkeypatch.delenv('VERGEOS_TOKEN', raising=False)
        assert scrub('connection refused') == 'connection refused'
