"""Policy-evaluation tests for the tier audit (pure functions, no API).

The audit half of this file tests `audit()`, which was already covered. The
half that was missing is everything that decides whether a policy is WORTH
evaluating:

  * a malformed rule used to leave the audit as a KeyError traceback inside a
    `no_log: true` task, which Ansible renders as the single word "censored";
  * a policy naming a tier the system does not have writes cleanly, reads
    back, and satisfies enforce's convergence proof while the data cannot
    have moved anywhere. Measured on 26.1.8 -- one storage tier (tier 1),
    and PUT preferred_tier accepted 1-5 and refused 0, 6, -1 and ''.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'tier_policy', 'files'))
from tier_audit import (  # noqa: E402
    TIER_MAX,
    TIER_MIN,
    audit,
    tier_problems,
    unsatisfiable_tiers,
    validate_rules,
)


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
        """The platform stores preferred_tier as a string (#18)."""
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


class TestTheSummarySaysWhatItMeans:
    """`preferred_tier` is a preference, not a residency report.

    Nothing in the API says which tier a drive's blocks are on --
    machine_drive_stats carries IO counters and used_bytes, no tier. A
    summary that said "on tier 4" would be asserting something this role
    cannot read.
    """

    def test_it_does_not_claim_the_data_is_anywhere(self):
        r = audit([drive('db-1', tier=4)], [{'match': 'db-*', 'tier': 2}])
        assert 'configured for the wrong tier' in r['summary']
        assert ' on tier ' not in r['summary']


class TestRuleValidation:
    """A malformed rule used to be a traceback the operator never saw.

    The role runs the audit with `no_log: true`, because the credentials are
    in the task's environment. no_log censors the entire result, so the
    KeyError arrived as::

        fatal: [localhost]: FAILED! => {"censored": "the output has been
        hidden due to the fact that 'no_log: true' was specified"}
    """

    def test_a_good_policy_has_no_problems(self):
        assert validate_rules([{'match': 'db-*', 'tier': 1},
                               {'match': 'app-*', 'drive': 'd*',
                                'tier': 4}]) == []

    def test_empty_policy_is_valid(self):
        assert validate_rules([]) == []

    def test_a_rule_without_a_tier_is_named(self):
        problems = validate_rules([{'match': 'db-*'}])
        assert len(problems) == 1
        assert "rule 0" in problems[0] and "'tier'" in problems[0]

    def test_a_rule_without_a_match_is_named(self):
        problems = validate_rules([{'tier': 1}])
        assert any("'match'" in p for p in problems)

    def test_the_index_points_at_the_offender(self):
        problems = validate_rules([{'match': 'a', 'tier': 1},
                                   {'match': 'b', 'tier': 1},
                                   {'match': 'c'}])
        assert len(problems) == 1 and 'rule 2' in problems[0]

    def test_a_rule_that_is_not_a_mapping(self):
        assert 'rule 0' in validate_rules(['db-*'])[0]

    def test_a_policy_that_is_not_a_list(self):
        assert validate_rules({'match': 'db-*', 'tier': 1})

    def test_a_non_numeric_tier(self):
        assert 'non-numeric' in validate_rules(
            [{'match': 'a', 'tier': 'gold'}])[0]


class TestTiersThePlatformRefuses:
    """Measured, not assumed: PUT machine_drives/<key> preferred_tier=N.

        0 -> refused    1..5 -> accepted    6 -> refused
        -1 -> refused   '' -> refused       'banana' -> refused

    A policy of tier 0 or 6 fails per-drive, mid-enforce, after some drives
    have already been retiered. Catching it before the first write is the
    difference between a refusal and a half-applied policy.
    """

    def test_the_accepted_range_is_recorded(self):
        assert (TIER_MIN, TIER_MAX) == (1, 5)

    def test_tier_zero_is_refused(self):
        assert tier_problems(0, 'rule 0')

    def test_tier_six_is_refused(self):
        assert tier_problems(6, 'rule 0')

    def test_the_whole_accepted_range_passes(self):
        for tier in range(TIER_MIN, TIER_MAX + 1):
            assert tier_problems(tier, 'rule 0') == []

    def test_the_message_says_what_the_platform_does(self):
        assert 'Error setting tier' in tier_problems(9, 'rule 0')[0]

    def test_a_bad_default_tier_is_caught_the_same_way(self):
        assert tier_problems(0, 'default-tier')


class TestTiersTheSystemDoesNotHave:
    """The green run that cannot have worked.

    On the test system the only storage tier was tier 1, and seventeen drives
    were already sitting at preferred_tier 4. Writing 4 succeeds; reading it
    back returns '4'; enforce declares convergence. Nothing moved, because
    there is no tier 4 to move to.
    """

    def test_a_tier_that_exists_is_satisfiable(self):
        assert unsatisfiable_tiers([{'match': 'a', 'tier': 1}], None,
                                   [1]) == []

    def test_a_tier_that_does_not_exist_is_reported(self):
        out = unsatisfiable_tiers([{'match': 'db-*', 'tier': 4}], None, [1])
        assert out[0]['tier'] == 4
        assert out[0]['rules'] == ['db-*']
        assert 'tiers present: 1' in out[0]['reason']

    def test_every_rule_asking_for_it_is_named(self):
        out = unsatisfiable_tiers([{'match': 'db-*', 'tier': 3},
                                   {'match': 'app-*', 'tier': 3}], None, [1])
        assert out[0]['rules'] == ['app-*', 'db-*']

    def test_the_default_tier_is_checked_too(self):
        out = unsatisfiable_tiers([], 5, [1])
        assert out[0]['tier'] == 5 and out[0]['rules'] == ['(default)']

    def test_unknown_tier_list_means_skip_not_guess(self):
        """An offline fixture may not record which tiers the system had.

        Reporting every rule as unsatisfiable would be worse than reporting
        none: it would train the reader to ignore the field.
        """
        assert unsatisfiable_tiers([{'match': 'a', 'tier': 4}], None,
                                   None) == []

    def test_the_audit_surfaces_it_and_says_which_tiers_exist(self):
        r = audit([drive('db-1', tier=4)], [{'match': 'db-*', 'tier': 4}],
                  existing_tiers=[1])
        assert r['compliant_count'] == 1          # it IS configured for 4
        assert r['unsatisfiable'][0]['tier'] == 4  # and 4 does not exist
        assert r['tiers_present'] == [1]
        assert 'cannot satisfy' in r['summary']

    def test_a_satisfiable_policy_says_nothing_extra(self):
        r = audit([drive('db-1', tier=1)], [{'match': 'db-*', 'tier': 1}],
                  existing_tiers=[1])
        assert r['unsatisfiable'] == []
        assert 'cannot satisfy' not in r['summary']

    def test_the_report_always_carries_the_key(self):
        """Consumers index it unconditionally; a missing key is a crash."""
        assert audit([], [])['unsatisfiable'] == []


class TestScrubbing:
    """The error path must not be the place a password gets out."""

    def test_the_password_is_replaced(self, monkeypatch):
        from tier_audit import scrub
        monkeypatch.setenv('VERGEOS_PASSWORD', 'hunter2')
        assert scrub('login failed for hunter2') == \
            'login failed for <VERGEOS_PASSWORD>'

    def test_a_token_is_replaced_too(self, monkeypatch):
        from tier_audit import scrub
        monkeypatch.setenv('VERGEOS_TOKEN', 'abc123')
        assert 'abc123' not in scrub('bad token abc123')

    def test_nothing_set_is_a_passthrough(self, monkeypatch):
        from tier_audit import scrub
        monkeypatch.delenv('VERGEOS_PASSWORD', raising=False)
        monkeypatch.delenv('VERGEOS_TOKEN', raising=False)
        assert scrub('connection refused') == 'connection refused'
