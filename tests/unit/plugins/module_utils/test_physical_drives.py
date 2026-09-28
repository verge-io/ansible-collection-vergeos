"""Unit tests for SMART triage."""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import json
import os

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils.physical_drives import (
    by_severity,
    classify,
)


def drive(**kw):
    # location is the SLOT on its node, not a node-qualified path. The
    # previous value here was 'node1:/dev/sdc', which no live row has ever
    # looked like -- and the captured fixture two directories away said so all
    # along. That fiction is what let physical_drive_info ship a node filter
    # that matched `node in location` and therefore matched nothing.
    row = {'serial': 'S1', 'node_name': 'node1', 'location': 'nvme0',
           'smart': True, 'vsan_read_errors': 0, 'vsan_write_errors': 0}
    row.update(kw)
    return row


def captured_rows():
    """The rows captured from a live 26.1.8 system.

    tests/fixtures/api/physical_drives.json says of itself: "Do not hand-edit:
    the point of these files is that no human chose the field names." It was
    captured and then read by nothing at all, which is the only reason the
    invented location above survived next to it.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, '..', '..', '..', '..'))
    path = os.path.join(root, 'tests', 'fixtures', 'api',
                        'physical_drives.json')
    with open(path) as handle:
        return json.load(handle)['rows']


# ── the ladder ───────────────────────────────────────────────────────────────

def test_a_clean_drive_is_ok():
    out = classify(drive())
    assert out['severity'] == 'ok'
    assert out['reasons'] == []


@pytest.mark.parametrize('flag', [
    'realloc_sectors_warn', 'current_pending_sector_warn',
    'offline_uncorrectable_warn',
])
def test_sector_failures_are_critical(flag):
    """These are the drive reporting it could not read or write something."""
    assert classify(drive(**{flag: True}))['severity'] == 'critical'


@pytest.mark.parametrize('flag', ['wear_level_warn', 'temp_warn'])
def test_wear_and_heat_are_warnings_not_critical(flag):
    """Plan-a-replacement, not pull-it-now -- and a hot drive is often the
    rack's problem rather than the drive's."""
    assert classify(drive(**{flag: True}))['severity'] == 'warning'


def test_age_alone_is_only_info():
    """Treating power-on hours as a warning makes every long-lived healthy
    drive shout."""
    assert classify(drive(hours_warn=True))['severity'] == 'info'


def test_critical_outranks_warning():
    out = classify(drive(temp_warn=True, realloc_sectors_warn=True))
    assert out['severity'] == 'critical'


def test_warning_outranks_info():
    assert classify(drive(hours_warn=True,
                          temp_warn=True))['severity'] == 'warning'


# ── vSAN errors beat SMART ───────────────────────────────────────────────────

def test_vsan_read_errors_are_critical():
    """A SMART flag is the drive's prediction; a vSAN error is the platform
    reporting an operation actually failed. The measurement wins."""
    out = classify(drive(vsan_read_errors=3))
    assert out['severity'] == 'critical'
    assert 'actually failed' in out['reasons'][0]


def test_vsan_write_errors_are_critical():
    assert classify(drive(vsan_write_errors=1))['severity'] == 'critical'


def test_vsan_errors_are_critical_even_with_no_smart_flags():
    out = classify(drive(vsan_read_errors=1, temp_warn=False))
    assert out['severity'] == 'critical'


def test_the_last_vsan_error_is_quoted_when_present():
    out = classify(drive(vsan_read_errors=1, vsan_last_error='medium error'))
    assert 'medium error' in out['reasons'][0]


def test_a_non_numeric_error_count_does_not_raise():
    assert classify(drive(vsan_read_errors=None))['severity'] == 'ok'
    assert classify(drive(vsan_read_errors=''))['severity'] == 'ok'


# ── repairing ────────────────────────────────────────────────────────────────

def test_repairing_is_surfaced_but_is_not_a_severity():
    """A drive can rebuild while perfectly healthy -- it may have just been
    replaced -- but pulling a second drive mid-repair is how a rebuild becomes
    a data-loss event."""
    out = classify(drive(vsan_repairing=True))
    assert out['repairing'] is True
    assert out['severity'] == 'ok'


def test_not_repairing_by_default():
    assert classify(drive())['repairing'] is False


# ── SMART disabled ───────────────────────────────────────────────────────────

def test_smart_disabled_is_info_not_ok():
    """Its health flags are silent, so it reads as healthy whether it is or
    not."""
    out = classify(drive(smart=False))
    assert out['severity'] == 'info'
    assert any('SMART is not enabled' in r for r in out['reasons'])


def test_smart_disabled_does_not_mask_a_real_failure():
    out = classify(drive(smart=False, realloc_sectors_warn=True))
    assert out['severity'] == 'critical'


def test_smart_enabled_is_normalised_onto_the_output():
    assert classify(drive(smart=True))['smart_enabled'] is True
    assert classify(drive(smart=False))['smart_enabled'] is False


# ── raw vs model field names ─────────────────────────────────────────────────

def test_the_raw_row_spelling_is_read():
    """The SDK model renames these. Reading only the model's spelling reports
    0 for everything on a raw row -- indistinguishable from a healthy drive."""
    out = classify(drive(temp=71, temp_warn=True))
    assert 'temp=71' in out['reasons'][0]


def test_the_model_spelling_is_also_read():
    out = classify(drive(temperature=71, temp_warn=True))
    assert 'temp=71' in out['reasons'][0]


# ── overrides ────────────────────────────────────────────────────────────────

def test_wear_can_be_promoted_to_critical():
    out = classify(drive(wear_level_warn=True),
                   critical_flags=['wear_level_warn'])
    assert out['severity'] == 'critical'


def test_heat_can_be_demoted_by_dropping_it_from_both_lists():
    out = classify(drive(temp_warn=True),
                   critical_flags=[], warning_flags=[])
    assert out['severity'] == 'ok'


def test_an_empty_critical_list_still_lets_warnings_through():
    out = classify(drive(temp_warn=True), critical_flags=[])
    assert out['severity'] == 'warning'


# ── selection ────────────────────────────────────────────────────────────────

def test_by_severity_selects_the_named_levels():
    rows = [classify(drive(serial='a')),
            classify(drive(serial='b', temp_warn=True)),
            classify(drive(serial='c', realloc_sectors_warn=True))]
    assert [r['serial'] for r in by_severity(rows, 'warning', 'critical')] \
        == ['b', 'c']


def test_by_severity_with_no_match_is_empty():
    assert by_severity([classify(drive())], 'critical') == []


def test_classify_does_not_mutate_its_input():
    row = drive()
    classify(row)
    assert 'severity' not in row


# ── the captured fixture, which nothing used to read ─────────────────────────

class TestAgainstTheCapturedRows:
    """Every field name this code reads must exist on a row a live system
    actually returned. The API accepts and discards unknown names, and a
    health module that reads a field that is not there reports a failing drive
    as healthy -- the worst direction for this particular module to be wrong
    in."""

    def test_every_triage_flag_is_a_real_column(self):
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            physical_drives as pd,
        )
        columns = set(captured_rows()[0])
        missing = sorted(
            flag for flag in
            pd.CRITICAL_FLAGS + pd.WARNING_FLAGS + pd.INFO_FLAGS
            if flag not in columns)
        assert not missing, (
            'these are not columns on a physical drive: %s. A flag that does '
            'not exist reads as None for every drive, so every drive passes.'
            % missing)

    def test_every_renamed_field_is_a_real_column(self):
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            physical_drives as pd,
        )
        columns = set(captured_rows()[0])
        missing = sorted(raw for raw in pd.RAW.values()
                         if raw not in columns)
        assert not missing, 'not columns: %s' % missing

    def test_the_hand_written_drive_helper_invents_nothing(self):
        """A test fixture that disagrees with the captured one is a test that
        proves the code works against a system that does not exist."""
        columns = set(captured_rows()[0]) | {'node_name'}
        invented = sorted(k for k in drive() if k not in columns)
        assert not invented, (
            'drive() uses field names no live row has: %s' % invented)

    def test_location_is_a_slot_and_repeats_across_nodes(self):
        """The fact the node filter was built on, stated as a test.

        Both captured drives report location "nvme0". It is the slot on its
        own node, so it cannot identify a node and it does not distinguish two
        drives on a cluster of identical hardware.
        """
        locations = [row.get('location') for row in captured_rows()]
        assert len(locations) > 1
        assert len(set(locations)) == 1, (
            'the captured rows no longer share a location; this test was '
            'pinning the ambiguity that made node_name necessary')
        assert all(':' not in str(loc) for loc in locations)

    def test_there_is_no_node_column(self):
        """Which is why the module has to join for it, and why the SDK's own
        `filter="node eq <key>"` scoping returns an empty list."""
        columns = set(captured_rows()[0])
        assert 'node' not in columns
        assert 'node_name' not in columns

    def test_a_captured_row_classifies_without_raising(self):
        for row in captured_rows():
            out = classify(row)
            assert out['severity'] in ('ok', 'info', 'warning', 'critical')
            assert isinstance(out['reasons'], list)
