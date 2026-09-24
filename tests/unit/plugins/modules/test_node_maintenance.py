"""Unit tests for the node_maintenance and update modules."""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock, patch

from ansible_collections.vergeio.vergeos.tests.unit.api_fixtures import row
from ansible_collections.vergeio.vergeos.plugins.modules import (
    node_maintenance,
    update as update_mod,
)


class FakeNodes:
    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.enabled = []
        self.disabled = []
        self.restarted = []

    def list(self, **kwargs):
        return [dict(r) for r in self.rows]

    def enable_maintenance(self, key):
        self.enabled.append(key)
        for r in self.rows:
            if r['$key'] == key:
                r['maintenance'] = True

    def disable_maintenance(self, key):
        self.disabled.append(key)
        for r in self.rows:
            if r['$key'] == key:
                r['maintenance'] = False

    def restart(self, key):
        self.restarted.append(key)


class FakeClient:
    def __init__(self, nodes=()):
        self.nodes = FakeNodes(nodes)


def node(name, key, online=True, maintenance=False, needs_restart=False):
    """A node row built from a CAPTURED one, not written by hand.

    The keyword names here are the collection's normalised vocabulary. What
    they map onto -- running, need_restart -- is the platform's, and
    api_fixtures.row() refuses anything the platform did not actually send.

    That refusal is the point. The first version of this helper returned
    {'online': ..., 'needs_restart': ...}, which is what summarize_node was
    reading, so the tests agreed with the code and both were wrong: every node
    reported offline against a real system (B4). Written against the capture,
    the mistake is a failing test rather than a shipped bug.
    """
    return row('nodes', name=name, running=online,
               maintenance=maintenance, need_restart=needs_restart,
               **{'$key': key})


TWO = [node('n1', 1), node('n2', 2)]


def run_nm(client, check_mode=False, **over):
    base = {'host': 'h', 'username': 'u', 'password': 'p', 'insecure': False,
            'name': 'n1', 'state': 'maintenance', 'force': False}
    base.update(over)
    module = MagicMock()
    module.params = base
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)
    with patch.object(node_maintenance, 'AnsibleModule', return_value=module), \
         patch.object(node_maintenance, 'get_vergeos_client', return_value=client):
        with pytest.raises(SystemExit):
            node_maintenance.main()
    return module


def exited(module):
    assert module.exit_json.called, (
        "expected exit_json, got fail_json: %s" % (module.fail_json.call_args,))
    return module.exit_json.call_args[1]


def failed(module):
    assert module.fail_json.called, (
        "expected fail_json, got exit_json: %s" % (module.exit_json.call_args,))
    return module.fail_json.call_args[1]


# ── maintenance ──────────────────────────────────────────────────────────────

def test_drains_a_node_when_a_peer_can_take_the_load():
    client = FakeClient(TWO)
    result = exited(run_nm(client))
    assert result['changed'] is True
    assert client.nodes.enabled == [1]
    assert result['available_peers'] == ['n2']


def test_draining_an_already_drained_node_is_a_no_op():
    client = FakeClient([node('n1', 1, maintenance=True), node('n2', 2)])
    result = exited(run_nm(client))
    assert result['changed'] is False
    assert client.nodes.enabled == []


def test_draining_the_last_usable_node_is_refused():
    """Evacuating with nowhere to evacuate to does not move the workloads
    somewhere safe; it stops them."""
    client = FakeClient([node('n1', 1)])
    msg = failed(run_nm(client))['msg']
    assert 'no other online node' in msg
    assert client.nodes.enabled == []


def test_an_offline_peer_does_not_count_as_somewhere_to_go():
    client = FakeClient([node('n1', 1), node('n2', 2, online=False)])
    assert 'refusing to drain' in failed(run_nm(client))['msg']


def test_a_peer_already_in_maintenance_does_not_count():
    client = FakeClient([node('n1', 1), node('n2', 2, maintenance=True)])
    assert 'refusing to drain' in failed(run_nm(client))['msg']


def test_force_drains_the_last_node_anyway():
    client = FakeClient([node('n1', 1)])
    result = exited(run_nm(client, force=True))
    assert result['changed'] is True
    assert client.nodes.enabled == [1]


def test_check_mode_drains_nothing():
    client = FakeClient(TWO)
    result = exited(run_nm(client, check_mode=True))
    assert result['changed'] is True
    assert client.nodes.enabled == []


def test_an_unknown_node_fails():
    assert "no node named" in failed(run_nm(FakeClient(TWO), name='nope'))['msg']


# ── active ───────────────────────────────────────────────────────────────────

def test_active_takes_a_node_out_of_maintenance():
    client = FakeClient([node('n1', 1, maintenance=True), node('n2', 2)])
    result = exited(run_nm(client, state='active'))
    assert result['changed'] is True
    assert client.nodes.disabled == [1]


def test_active_on_a_live_node_is_a_no_op():
    client = FakeClient(TWO)
    result = exited(run_nm(client, state='active'))
    assert result['changed'] is False
    assert client.nodes.disabled == []


def test_active_never_needs_a_peer():
    """Returning a node to service adds capacity; it cannot strand anything."""
    client = FakeClient([node('n1', 1, maintenance=True)])
    assert exited(run_nm(client, state='active'))['changed'] is True


# ── restarted ────────────────────────────────────────────────────────────────

def test_restart_requests_a_restart():
    client = FakeClient(TWO)
    result = exited(run_nm(client, state='restarted'))
    assert result['changed'] is True
    assert client.nodes.restarted == [1]
    assert 'asynchronous' in result['msg']


def test_restarting_the_last_node_is_refused():
    client = FakeClient([node('n1', 1)])
    assert 'takes the cluster down' in failed(
        run_nm(FakeClient([node('n1', 1)]), state='restarted'))['msg']
    assert client.nodes.restarted == []


def test_restart_does_not_enter_maintenance_first():
    """Draining and rebooting stay separate, reviewable steps."""
    client = FakeClient(TWO)
    exited(run_nm(client, state='restarted'))
    assert client.nodes.enabled == []


def test_check_mode_restarts_nothing():
    client = FakeClient(TWO)
    exited(run_nm(client, check_mode=True, state='restarted'))
    assert client.nodes.restarted == []


# ── update module ────────────────────────────────────────────────────────────

class FakeSettings(dict):
    def __init__(self, row, **props):
        super().__init__(row)
        self.calls = []
        for name, value in props.items():
            setattr(self, name, value)

    def check(self):
        self.calls.append('check')

    def download(self):
        self.calls.append('download')

    def install(self):
        self.calls.append('install')
        self['is_installed'] = True


class FakeUpdateClient:
    def __init__(self, settings):
        self._settings = settings
        self.update_settings = MagicMock()
        self.update_settings.get.return_value = settings


def run_update(settings, check_mode=False, state='checked'):
    module = MagicMock()
    module.params = {'host': 'h', 'username': 'u', 'password': 'p',
                     'insecure': False, 'state': state}
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)
    client = FakeUpdateClient(settings)
    with patch.object(update_mod, 'AnsibleModule', return_value=module), \
         patch.object(update_mod, 'get_vergeos_client', return_value=client):
        with pytest.raises(SystemExit):
            update_mod.main()
    return module


def test_checked_runs_only_the_check():
    s = FakeSettings({})
    result = exited(run_update(s, state='checked'))
    assert s.calls == ['check']
    assert result['stages_run'] == ['checked']


def test_installed_runs_the_whole_lifecycle_in_order():
    s = FakeSettings({})
    exited(run_update(s, state='installed'))
    assert s.calls == ['check', 'download', 'install']


def test_nothing_runs_when_updates_are_already_installed():
    s = FakeSettings({'is_installed': True})
    result = exited(run_update(s, state='installed'))
    assert s.calls == []
    assert result['changed'] is False


def test_check_mode_runs_no_stage():
    s = FakeSettings({})
    result = exited(run_update(s, check_mode=True, state='installed'))
    assert s.calls == []
    assert result['changed'] is True


def test_an_update_already_being_applied_is_refused():
    """Two update runs interleaving is not something to discover afterwards."""
    s = FakeSettings({'is_applying_updates': True})
    assert 'applying updates right now' in failed(
        run_update(s, state='installed'))['msg']
    assert s.calls == []


def test_the_update_module_never_reboots():
    s = FakeSettings({})
    exited(run_update(s, state='installed'))
    assert 'restart' not in s.calls and 'reboot' not in s.calls
