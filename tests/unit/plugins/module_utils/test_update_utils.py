"""Unit tests for the node and platform-update helpers."""

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils.nodes import (
    other_online_nodes,
    summarize_node,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.updates import (
    settings_summary,
    stages_to_run,
)


# ── nodes ────────────────────────────────────────────────────────────────────

def test_summarize_reads_the_raw_row_spelling():
    row = summarize_node({'name': 'n1', 'online': True, 'maintenance': True})
    assert row['online'] is True
    assert row['maintenance'] is True


def test_summarize_reads_the_model_spelling():
    """Node state is spelled differently depending on the projection.

    Reading only one spelling reports every node as not in maintenance, which
    is the answer that makes a drain look unnecessary.
    """
    row = summarize_node({'name': 'n1', 'is_online': True,
                          'is_maintenance': True})
    assert row['online'] is True
    assert row['maintenance'] is True


def test_an_unknown_state_is_not_online():
    """An upgrade loop that treats unknown as online drains the next node
    while the previous one is still down."""
    assert summarize_node({'name': 'n1'})['online'] is False


def test_needs_restart_defaults_false():
    assert summarize_node({'name': 'n1'})['needs_restart'] is False


def test_needs_restart_is_read_when_present():
    assert summarize_node({'needs_restart': True})['needs_restart'] is True


def test_summarize_keeps_the_original_fields():
    row = summarize_node({'name': 'n1', 'cores': 32})
    assert row['cores'] == 32


NODES = [
    {'name': 'n1', 'online': True, 'maintenance': False},
    {'name': 'n2', 'online': True, 'maintenance': False},
    {'name': 'n3', 'online': False, 'maintenance': False},
    {'name': 'n4', 'online': True, 'maintenance': True},
]


def test_peers_exclude_the_node_itself():
    assert 'n1' not in [n['name'] for n in other_online_nodes(NODES, 'n1')]


def test_peers_exclude_offline_nodes():
    assert 'n3' not in [n['name'] for n in other_online_nodes(NODES, 'n1')]


def test_peers_exclude_nodes_already_in_maintenance():
    """A node on its way out is not somewhere to evacuate to -- workloads
    placed there would only have to move again."""
    assert 'n4' not in [n['name'] for n in other_online_nodes(NODES, 'n1')]


def test_peers_finds_the_usable_ones():
    assert [n['name'] for n in other_online_nodes(NODES, 'n1')] == ['n2']


def test_a_lone_node_has_no_peers():
    assert other_online_nodes([NODES[0]], 'n1') == []


# ── update settings ──────────────────────────────────────────────────────────

class FakeSettings(dict):
    """A settings row that also exposes the SDK's model properties."""

    def __init__(self, row, **props):
        super().__init__(row)
        for name, value in props.items():
            setattr(self, name, value)


def test_summary_prefers_the_model_property():
    s = FakeSettings({'is_installed': False}, is_installed=True,
                     branch_name='26.1')
    assert settings_summary(s)['installed'] is True
    assert settings_summary(s)['branch'] == '26.1'


def test_summary_falls_back_to_the_raw_row():
    """A row fetched with a narrower projection is a plain mapping. Reading
    only the property reports 'nothing pending' on the shape it does not
    recognise -- which quietly skips an upgrade."""
    s = FakeSettings({'is_installed': True, 'branch_name': '26.1'})
    assert settings_summary(s)['installed'] is True
    assert settings_summary(s)['branch'] == '26.1'


def test_summary_defaults_are_false_and_empty():
    s = settings_summary(FakeSettings({}))
    assert s['installed'] is False
    assert s['reboot_required'] is False
    assert s['applying'] is False
    assert s['branch'] == ''


# ── stage planning ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('target,expected', [
    ('checked', ['checked']),
    ('downloaded', ['checked', 'downloaded']),
    ('installed', ['checked', 'downloaded', 'installed']),
])
def test_each_stage_implies_the_ones_before_it(target, expected):
    assert stages_to_run(target, {'installed': False}) == expected


@pytest.mark.parametrize('target', ['checked', 'downloaded', 'installed'])
def test_nothing_runs_once_updates_are_installed(target):
    """The platform records that updates are installed, not that a check was
    performed, so 'installed' is the only skippable consequence there is."""
    assert stages_to_run(target, {'installed': True}) == []


def test_an_unknown_stage_is_refused():
    with pytest.raises(ValueError):
        stages_to_run('rebooted', {'installed': False})
