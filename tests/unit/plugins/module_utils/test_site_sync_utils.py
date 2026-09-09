"""Unit tests for the site-sync helpers."""

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils.site_sync import (
    find_by_name,
    row_field,
    seconds_since,
    stale,
    summarize,
)


class FakeManager:
    def __init__(self, rows):
        self._rows = rows

    def list(self, **kwargs):
        return list(self._rows)


NOW = 1_000_000


# ── seconds_since ────────────────────────────────────────────────────────────

def test_seconds_since_computes_age():
    assert seconds_since(NOW - 300, now=NOW) == 300


@pytest.mark.parametrize('value', [None, 0, '', 'not-a-number', -1])
def test_seconds_since_is_none_when_there_is_no_timestamp(value):
    assert seconds_since(value, now=NOW) is None


def test_seconds_since_never_goes_negative():
    """A clock skew must not read as a sync that ran in the future."""
    assert seconds_since(NOW + 500, now=NOW) == 0


# ── summarize ────────────────────────────────────────────────────────────────

def test_summarize_adds_age_and_health():
    row = summarize({'name': 's', 'online': True, 'last_run': NOW - 60},
                    now=NOW)
    assert row['seconds_since_last_run'] == 60
    assert row['healthy'] is True


def test_summarize_reads_last_sync_for_incoming_rows():
    """Outgoing rows carry last_run; incoming rows carry last_sync."""
    row = summarize({'name': 's', 'online': True, 'last_sync': NOW - 90},
                    now=NOW)
    assert row['seconds_since_last_run'] == 90


def test_an_offline_sync_is_not_healthy():
    assert summarize({'online': False}, now=NOW)['healthy'] is False


def test_a_sync_in_error_is_not_healthy():
    assert summarize({'online': True, 'error': True}, now=NOW)['healthy'] is False


def test_an_unknown_state_is_not_healthy():
    """Conservative on purpose. A watchdog that reports green on data it
    could not read is worse than no watchdog."""
    assert summarize({}, now=NOW)['healthy'] is False


def test_summarize_does_not_mutate_its_input():
    original = {'name': 's', 'online': True}
    summarize(original, now=NOW)
    assert 'healthy' not in original


# ── stale ────────────────────────────────────────────────────────────────────

def test_a_sync_older_than_the_budget_is_stale():
    rows = [summarize({'name': 'old', 'last_run': NOW - 7200}, now=NOW)]
    assert [r['name'] for r in stale(rows, 3600)] == ['old']


def test_a_recent_sync_is_not_stale():
    rows = [summarize({'name': 'fresh', 'last_run': NOW - 60}, now=NOW)]
    assert stale(rows, 3600) == []


def test_a_sync_that_never_ran_is_stale():
    """The whole point: a replication target configured once and never
    exercised is the failure this surfaces."""
    rows = [summarize({'name': 'never'}, now=NOW)]
    assert [r['name'] for r in stale(rows, 3600)] == ['never']


def test_no_budget_means_no_staleness_check():
    rows = [summarize({'name': 'never'}, now=NOW)]
    assert stale(rows, None) == []
    assert stale(rows, 0) == []


def test_a_sync_exactly_at_the_budget_is_not_stale():
    rows = [summarize({'name': 'edge', 'last_run': NOW - 3600}, now=NOW)]
    assert stale(rows, 3600) == []


# ── row_field ────────────────────────────────────────────────────────────────

def test_row_field_maps_the_verbose_sdk_names():
    """The SDK spells two retry knobs more verbosely than the table does;
    comparing against the SDK spelling reports drift on every run."""
    assert row_field('queue_retry_interval_seconds') == 'queue_retry_interval'
    assert row_field('queue_retry_interval_multiplier') == 'queue_retry_multiplier'


def test_row_field_passes_through_unmapped_names():
    assert row_field('threads') == 'threads'


# ── find_by_name ─────────────────────────────────────────────────────────────

def test_find_by_name_finds_the_sync():
    mgr = FakeManager([{'name': 'a', '$key': 1}, {'name': 'b', '$key': 2}])
    assert find_by_name(mgr, 'b')['$key'] == 2


def test_find_by_name_returns_none_when_absent():
    assert find_by_name(FakeManager([{'name': 'a'}]), 'z') is None


def test_find_by_name_matches_exactly_not_by_prefix():
    mgr = FakeManager([{'name': 'to-dr-site-2', '$key': 1},
                       {'name': 'to-dr-site', '$key': 2}])
    assert find_by_name(mgr, 'to-dr-site')['$key'] == 2
