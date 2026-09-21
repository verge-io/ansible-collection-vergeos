#!/usr/bin/env python3
"""Policy-evaluation tests for the tier audit (pure function, no API)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'tier_policy', 'files'))
from tier_audit import audit  # noqa: E402


def drive(vm, name='disk1', tier=1):
    return {'vm': vm, 'drive': name, 'tier': tier}


class TestMatching:
    def test_compliant_drive(self):
        r = audit([drive('db-1', tier=2)], [{'match': 'db-*', 'tier': 2}])
        assert r['compliant_count'] == 1
        assert r['drift'] == [] and r['unclassified'] == []

    def test_drift_flagged_with_rule_named(self):
        r = audit([drive('db-1', tier=4)], [{'match': 'db-*', 'tier': 2}])
        assert r['drift'] == [{'vm': 'db-1', 'drive': 'disk1',
                               'expected': 2, 'actual': 4,
                               'rule': 'db-*'}]

    def test_first_match_wins(self):
        rules = [{'match': 'db-1', 'tier': 1}, {'match': 'db-*', 'tier': 3}]
        r = audit([drive('db-1', tier=1)], rules)
        assert r['compliant_count'] == 1
        assert r['drift'] == []

    def test_drive_glob_scopes_a_rule(self):
        rules = [{'match': 'app-*', 'drive': 'data*', 'tier': 2},
                 {'match': 'app-*', 'tier': 1}]
        r = audit([drive('app-1', 'data0', tier=2),
                   drive('app-1', 'os', tier=1)], rules)
        assert r['compliant_count'] == 2

    def test_no_rule_no_default_is_unclassified_not_drift(self):
        r = audit([drive('random-vm', tier=4)],
                  [{'match': 'db-*', 'tier': 2}])
        assert r['drift'] == []
        assert r['unclassified'] == [{'vm': 'random-vm', 'drive': 'disk1',
                                      'tier': 4}]

    def test_default_tier_turns_unmatched_into_policy(self):
        r = audit([drive('random-vm', tier=4)],
                  [{'match': 'db-*', 'tier': 2}], default_tier=1)
        assert r['unclassified'] == []
        assert r['drift'][0]['rule'] == '(default)'
        assert r['drift'][0]['expected'] == 1


class TestEdges:
    def test_unreadable_tier_zero_is_never_compliant(self):
        r = audit([drive('db-1', tier=0)], [{'match': 'db-*', 'tier': 2}])
        assert r['drift'][0]['actual'] == 0

    def test_string_tiers_normalized(self):
        # collector emits ints, but a hand-written fixture may not
        r = audit([drive('db-1', tier='2')], [{'match': 'db-*', 'tier': '2'}])
        assert r['compliant_count'] == 1

    def test_empty_policy_all_unclassified(self):
        r = audit([drive('a'), drive('b')], [])
        assert len(r['unclassified']) == 2
        assert r['summary'].endswith('2 unclassified')

    def test_empty_drives(self):
        r = audit([], [{'match': '*', 'tier': 1}])
        assert r['drives_audited'] == 0
        assert r['summary'].startswith('0 drive(s)')

    def test_glob_is_case_sensitive(self):
        r = audit([drive('DB-1', tier=4)], [{'match': 'db-*', 'tier': 2}])
        assert r['drift'] == []
        assert len(r['unclassified']) == 1
