#!/usr/bin/env python3
"""Verdict-shaping tests for the health scan (pure function, no API)."""

import os
import sys

# The scan ships as a role file, not an importable module, so the path is
# built from this test's location rather than from the collection root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'health_report', 'files'))
from health_scan import assess  # noqa: E402

NOW = 1_787_750_000
CFG = {'capacity_amber_pct': 80.0, 'capacity_red_pct': 90.0,
       'snapshot_amber_hours': 26.0, 'snapshot_red_hours': 50.0}

H = 3600


def state(**over):
    """A fully green baseline; override sections per test."""
    base = {
        'now': NOW,
        'errors': {},
        'alarms': [],
        'nodes': [{'name': 'node1', 'running': True, 'maintenance': False}],
        'tiers': [{'tier': 1, 'capacity': 1000, 'used': 300}],
        'snapshots': [{'name': 'daily', 'created': NOW - 1 * H}],
        'nas': [{'name': 'nas1', 'vm_running': True, 'volume_count': 1}],
    }
    base.update(over)
    return base


def check(report, name):
    return next(c for c in report['checks'] if c['check'] == name)


class TestGreenBaseline:
    def test_all_green(self):
        r = assess(state(), CFG)
        assert r['overall'] == 'green'
        assert r['counts'] == {'green': 5, 'amber': 0, 'red': 0}
        assert r['digest'] == 'GREEN — all 5 checks green'

    def test_report_is_read_only(self):
        assert assess(state(), CFG)['mode'] == 'read-only'


class TestAlarms:
    def test_warning_is_amber(self):
        r = assess(state(alarms=[{'level': 'warning',
                                  'alarm_type': '2fa_not_enabled'}]), CFG)
        c = check(r, 'alarms')
        assert c['verdict'] == 'amber'
        assert '2fa_not_enabled' in c['detail']
        assert r['overall'] == 'amber'

    def test_error_is_red(self):
        r = assess(state(alarms=[{'level': 'error',
                                  'alarm_type': 'vsan_offline'}]), CFG)
        assert check(r, 'alarms')['verdict'] == 'red'
        assert r['overall'] == 'red'

    def test_unknown_level_is_never_silently_green(self):
        r = assess(state(alarms=[{'level': 'sparkle',
                                  'alarm_type': 'mystery'}]), CFG)
        assert check(r, 'alarms')['verdict'] == 'amber'

    def test_info_level_is_green(self):
        r = assess(state(alarms=[{'level': 'info',
                                  'alarm_type': 'fyi'}]), CFG)
        assert check(r, 'alarms')['verdict'] == 'green'

    def test_snoozed_alarm_excluded_but_counted(self):
        r = assess(state(alarms=[{'level': 'warning', 'alarm_type': 'smtp',
                                  'snooze': NOW + 10 * H}]), CFG)
        c = check(r, 'alarms')
        assert c['verdict'] == 'green'
        assert '1 snoozed excluded' in c['detail']

    def test_expired_snooze_counts_again(self):
        r = assess(state(alarms=[{'level': 'warning', 'alarm_type': 'smtp',
                                  'snooze': NOW - 10 * H}]), CFG)
        assert check(r, 'alarms')['verdict'] == 'amber'


class TestNodes:
    def test_down_node_is_red(self):
        r = assess(state(nodes=[
            {'name': 'node1', 'running': True, 'maintenance': False},
            {'name': 'node2', 'running': False, 'maintenance': False}]), CFG)
        c = check(r, 'nodes')
        assert c['verdict'] == 'red'
        assert 'node2' in c['detail']

    def test_maintenance_is_amber(self):
        r = assess(state(nodes=[
            {'name': 'node1', 'running': True, 'maintenance': True}]), CFG)
        assert check(r, 'nodes')['verdict'] == 'amber'

    def test_down_beats_maintenance(self):
        r = assess(state(nodes=[
            {'name': 'node1', 'running': False, 'maintenance': True},
            {'name': 'node2', 'running': True, 'maintenance': True}]), CFG)
        assert check(r, 'nodes')['verdict'] == 'red'


class TestCapacity:
    def test_amber_threshold_inclusive(self):
        r = assess(state(tiers=[{'tier': 1, 'capacity': 1000,
                                 'used': 800}]), CFG)
        assert check(r, 'capacity')['verdict'] == 'amber'

    def test_red_threshold_inclusive(self):
        r = assess(state(tiers=[{'tier': 1, 'capacity': 1000,
                                 'used': 900}]), CFG)
        c = check(r, 'capacity')
        assert c['verdict'] == 'red'
        assert 'tier 1 at 90%' in c['detail']

    def test_zero_capacity_tier_skipped(self):
        r = assess(state(tiers=[{'tier': 0, 'capacity': 0, 'used': 0},
                                {'tier': 1, 'capacity': 1000,
                                 'used': 100}]), CFG)
        c = check(r, 'capacity')
        assert c['verdict'] == 'green'
        assert 'tier 1' in c['detail']

    def test_all_tiers_capacityless_is_red(self):
        r = assess(state(tiers=[{'tier': 0, 'capacity': 0, 'used': 0}]),
                   CFG)
        assert check(r, 'capacity')['verdict'] == 'red'


class TestSnapshots:
    def test_no_snapshots_is_red(self):
        r = assess(state(snapshots=[]), CFG)
        c = check(r, 'snapshots')
        assert c['verdict'] == 'red'
        assert 'cadence is silent' in c['detail']

    def test_stale_beyond_amber(self):
        r = assess(state(snapshots=[{'created': NOW - 30 * H}]), CFG)
        assert check(r, 'snapshots')['verdict'] == 'amber'

    def test_stale_beyond_red(self):
        r = assess(state(snapshots=[{'created': NOW - 60 * H}]), CFG)
        assert check(r, 'snapshots')['verdict'] == 'red'

    def test_newest_wins_over_old_backlog(self):
        r = assess(state(snapshots=[{'created': NOW - 500 * H},
                                    {'created': NOW - 1 * H}]), CFG)
        assert check(r, 'snapshots')['verdict'] == 'green'


class TestNas:
    def test_serving_nas_down_is_red(self):
        r = assess(state(nas=[{'name': 'nas1', 'vm_running': False,
                               'volume_count': 1}]), CFG)
        c = check(r, 'nas')
        assert c['verdict'] == 'red'
        assert 'nas1' in c['detail']

    def test_stopped_volumeless_service_ignored(self):
        # e.g. the 'Services 26.1.8-0' system row: stopped, 0 volumes
        r = assess(state(nas=[
            {'name': 'Services 26.1.8-0', 'vm_running': False,
             'volume_count': 0},
            {'name': 'nas1', 'vm_running': True, 'volume_count': 1}]), CFG)
        c = check(r, 'nas')
        assert c['verdict'] == 'green'
        assert 'ignored' in c['detail']

    def test_no_nas_at_all_is_green(self):
        r = assess(state(nas=[]), CFG)
        assert check(r, 'nas')['verdict'] == 'green'


class TestFaultIsolation:
    def test_unreadable_section_is_red_not_green(self):
        r = assess(state(alarms=None,
                         errors={'alarms': 'HTTPError: 500'}), CFG)
        c = check(r, 'alarms')
        assert c['verdict'] == 'red'
        assert 'HTTPError: 500' in c['detail']
        assert r['overall'] == 'red'

    def test_one_bad_section_leaves_others_standing(self):
        r = assess(state(nas=None, errors={'nas': 'boom'}), CFG)
        assert check(r, 'nodes')['verdict'] == 'green'
        assert len(r['checks']) == 5


class TestRollup:
    def test_red_beats_amber(self):
        r = assess(state(
            alarms=[{'level': 'warning', 'alarm_type': 'w'}],
            nodes=[{'name': 'n1', 'running': False}]), CFG)
        assert r['overall'] == 'red'

    def test_digest_names_only_non_green_checks(self):
        r = assess(state(alarms=[{'level': 'warning',
                                  'alarm_type': 'smtp'}]), CFG)
        assert r['digest'].startswith('AMBER — alarms:')
        assert 'nodes' not in r['digest']
