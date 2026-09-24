"""Unit tests for the node and platform-update helpers."""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible_collections.vergeio.vergeos.tests.unit import api_fixtures as F
from ansible_collections.vergeio.vergeos.plugins.module_utils.nodes import (
    machine_census,
    other_online_nodes,
    summarize_node,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.updates import (
    apply_installed,
    settings_summary,
    stages_to_run,
)


# ── nodes ────────────────────────────────────────────────────────────────────

def test_summarize_reads_the_real_raw_row_spelling():
    """These are the field names a live VergeOS 26.1.8 node row actually uses.

    Note `running` -- there is no `online` key at all -- and `need_restart`,
    singular. The first version of this suite invented `online` and
    `needs_restart` as raw fields, so it passed while every real node
    reported as offline.
    """
    row = summarize_node({'name': 'n1', 'running': True, 'maintenance': True,
                          'need_restart': True})
    assert row['online'] is True
    assert row['maintenance'] is True
    assert row['needs_restart'] is True


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
    assert summarize_node({'need_restart': True})['needs_restart'] is True


def test_a_null_field_is_not_read_as_false():
    """The API returns null for a field it did not populate. Treating null as
    False is how "state unknown" silently became "definitely off"."""
    assert summarize_node({'running': None})['online'] is False


class FakeNode(dict):
    """A node row that also exposes the SDK's computed properties."""

    def __init__(self, row, **props):
        super().__init__(row)
        for name, value in props.items():
            setattr(self, name, value)


def test_the_model_properties_win_over_the_raw_row():
    """dict() of a node discards its properties, so callers must pass the
    object. When they do, the computed value is authoritative."""
    node = FakeNode({'running': None, 'maintenance': None},
                    is_online=True, is_maintenance=False, needs_restart=True)
    row = summarize_node(node)
    assert row['online'] is True
    assert row['maintenance'] is False
    assert row['needs_restart'] is True


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


# ── the raw spelling the platform really sends ───────────────────────────────

class TestTheFallbackIsReadAgainstTheCaptureAndNotAgainstItself:
    """`test_summary_falls_back_to_the_raw_row` above hands _prop a row keyed
    `is_installed` and `branch_name` -- the PROPERTY names.

    That is the exact mistake this module's own RAW_FIELD comment records
    (B6): the first version used the property names for the fallback too,
    which made the fallback dead code that looked like a safety net. A test
    written the same way passes whether or not RAW_FIELD exists, so it proves
    nothing about the shape the platform sends.

    These read the captured update_settings row instead, where the spelling is
    the platform's: `installed`, `reboot_required`, `branch_display`.
    """

    def test_the_capture_uses_the_raw_spelling_not_the_property_one(self):
        fields = F.fields('update_settings')
        assert {'installed', 'reboot_required', 'applying_updates',
                'auto_update', 'branch_display', 'source_display'} <= fields
        assert not {'is_installed', 'is_reboot_required'} & fields

    def test_a_plain_captured_row_summarises_correctly(self):
        row = F.row('update_settings', installed=True, reboot_required=True,
                    applying_updates=False, branch_display='stable-26.1')
        summary = settings_summary(row)
        assert summary['installed'] is True
        assert summary['reboot_required'] is True
        assert summary['applying'] is False
        assert summary['branch'] == 'stable-26.1'

    def test_the_raw_row_is_handed_back_whole(self):
        """Callers splat the summary into exit_json, and `settings` is the
        documented escape hatch for a field this normalisation does not
        name."""
        row = F.row('update_settings')
        assert settings_summary(row)['settings'] == dict(row)

    def test_an_uninstalled_capture_reads_as_uninstalled(self):
        row = F.row('update_settings', installed=False)
        assert settings_summary(row)['installed'] is False


class TestApplyingGoesThroughThePlatformsOwnRoll:
    """`apply_installed` used to prefer a narrower `_action('apply')` and fall
    back to `update_all`.

    Measured on pyvergeos 1.7.0: `_action` is defined on the update-settings
    MANAGER, not on the settings model `get()` returns. So `getattr(settings,
    '_action', None)` always found None, the fallback was the only path ever
    taken, and the branch above it was unreachable code that read like the
    main one -- the same defect RAW_FIELD records, made twice in one module.
    """

    class FakeSettings(dict):
        def __init__(self):
            super().__init__()
            self.calls = []

        def update_all(self, force=False):
            self.calls.append(('update_all', force))
            return {'task': 1}

    def test_it_calls_update_all(self):
        settings = self.FakeSettings()
        apply_installed(settings)
        assert settings.calls == [('update_all', False)]

    def test_force_is_passed_through_and_never_implied(self):
        settings = self.FakeSettings()
        apply_installed(settings, force=True)
        assert settings.calls == [('update_all', True)]

    def test_an_action_attribute_on_the_model_is_not_consulted(self):
        """Pinning the measurement. If a future SDK grows `_action` on the
        model, this test failing is the notification -- rather than the
        behaviour changing silently under an upgrade."""
        settings = self.FakeSettings()
        settings._action = lambda *a, **k: pytest.fail(
            'apply went through _action, which was never reachable when this '
            'was measured; re-measure before trusting it')
        apply_installed(settings)
        assert settings.calls == [('update_all', False)]


class TestTheMachineCensus:
    """What is resident on a node is not the same question as which VMs are
    placed there.

    Measured on the lab: one node carried NINE running machines while
    `vms.list()` attributed TWO of them to it. The other seven were vnets, and
    `networks.list()` reports no node for a vnet at all -- so a drain check
    written over VMs calls the node empty while it is hosting the fabric that
    the API is being reached over.
    """

    class FakeNodes:
        def __init__(self, rows):
            self.rows = rows
            self.field_calls = []

        def list(self, **kwargs):
            self.field_calls.append(kwargs.get('fields'))
            return [dict(r) for r in self.rows]

    class FakeClient:
        def __init__(self, nodes):
            self.nodes = nodes

    def _client(self, rows):
        return self.FakeClient(self.FakeNodes(rows))

    def test_it_counts_machines_not_vms(self):
        client = self._client([
            {'name': 'node1', 'running_machines': [{'machine': 1}] * 9},
            {'name': 'node2', 'running_machines': []},
        ])
        census = machine_census(client)
        assert census['node1']['running_machines'] == 9
        assert census['node2']['running_machines'] == 0

    def test_unmigratable_machines_are_counted_separately(self):
        """Those are the ones a drain STOPS rather than moves, which is
        exactly the decision `force` exists for."""
        client = self._client([{'name': 'node1', 'running_machines': [
            {'machine': 1, 'migratable': True},
            {'machine': 2, 'migratable': False},
        ]}])
        assert machine_census(client)['node1'] == {
            'running_machines': 2, 'unmigratable_machines': 1}

    def test_a_missing_migratable_flag_is_not_counted_as_unmigratable(self):
        """None means the row did not say. Guessing 'cannot migrate' would
        refuse drains that are fine."""
        client = self._client([{'name': 'node1',
                                'running_machines': [{'machine': 1}]}])
        assert machine_census(client)['node1']['unmigratable_machines'] == 0

    def test_it_asks_for_the_field_by_name(self):
        """running_machines is absent from the SDK's default projection. A
        census that used the default would report every node as empty."""
        client = self._client([{'name': 'node1', 'running_machines': []}])
        machine_census(client)
        assert client.nodes.field_calls[0], (
            'machine_census used the default projection, which does not carry '
            'running_machines')
        assert 'running_machines' in client.nodes.field_calls[0]

    def test_a_null_list_is_zero_rather_than_a_traceback(self):
        client = self._client([{'name': 'node1', 'running_machines': None}])
        assert machine_census(client)['node1']['running_machines'] == 0

    def test_a_non_list_is_zero_rather_than_a_traceback(self):
        client = self._client([{'name': 'node1', 'running_machines': 'lots'}])
        assert machine_census(client)['node1']['running_machines'] == 0
