#!/usr/bin/env python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Powering a vnet on and off (#97).

Firewall rules could be staged from Ansible and never made to take effect:
`vnet_rule` skips its apply on a stopped network and `vnet_apply` reports
"not running; staged rules take effect when it starts". Both are correct.
Neither could start it, and nothing else in the collection could either.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from unittest.mock import MagicMock

import pytest

from ansible_collections.vergeio.vergeos.plugins.modules import network as net


def _module(check_mode=False, **overrides):
    params = {'name': 'zz-net', 'state': 'running', 'apply_rules': True,
              'power_timeout': 5}
    params.update(overrides)
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    module.fail_json.side_effect = SystemExit(1)
    return module


def _net(running=True, **over):
    """A vnet object shaped like one fetched through NETWORK_FIELDS."""
    row = {'$key': 7, 'name': 'zz-net', 'running': running,
           'need_restart': False}
    row.update(over)
    obj = MagicMock()
    obj.keys = row.keys
    obj.__getitem__ = lambda _self, key: row[key]
    obj.row = row
    return obj


# ── the projection, which is the whole trap ──────────────────────────────────

class TestThePowerStateIsFetchedAndNotAssumed:
    """`running` is a join through the vnet's router machine, not a column.

    Measured on 26.1.8: it is in the SDK's default projection and absent from
    `GET /vnets?fields=all` -- 98 fields, none of them `running`. This module
    names its projection, so a field not listed simply does not arrive. Before
    #97 the module fetched COMPARISON_FIELDS, which has no power field at all.
    """

    def test_the_fetch_list_asks_for_the_power_join(self):
        assert 'machine#status#running as running' in net.NETWORK_FIELDS

    def test_and_for_need_restart_which_is_a_real_column(self):
        assert 'need_restart' in net.NETWORK_FIELDS

    def test_the_fetch_list_is_a_superset_of_the_diff_list(self):
        """Adding power must not have dropped a field the comparison needs --
        a field that was never fetched reads as None and never converges."""
        assert set(net.COMPARISON_FIELDS) <= set(net.NETWORK_FIELDS)

    def test_the_diff_list_is_unchanged_by_the_power_fields(self):
        """COMPARISON_FIELDS is what verify-field-contract.yml asserts against
        a live vnet. Power is fetched, not diffed."""
        assert not set(net.POWER_FIELDS) & set(net.COMPARISON_FIELDS)


class TestUnknownIsNotStopped:
    def test_a_row_without_the_field_has_no_opinion(self):
        assert net.is_running({'name': 'zz-net'}) is None

    def test_a_null_is_also_no_opinion(self):
        """The API returns null for a field it did not populate. Reading null
        as False is how "state unknown" became "definitely off"."""
        assert net.is_running({'running': None}) is None

    @pytest.mark.parametrize('value,expected', [
        (True, True), (False, False), (1, True), (0, False),
    ])
    def test_a_present_value_is_read_as_a_bool(self, value, expected):
        assert net.is_running({'running': value}) is expected

    def test_powering_on_an_unreadable_row_is_refused_not_guessed(self):
        """Guessing is not conservative in either direction: read as stopped,
        a running vnet is powered on again every run; read as running, a
        stopped one is never started."""
        module = _module()
        with pytest.raises(SystemExit):
            net.power_on_network(module, MagicMock(), _net(running=None))
        msg = module.fail_json.call_args[1]['msg']
        assert 'machine#status#running as running' in msg
        assert 'fields=all' in msg

    def test_powering_off_an_unreadable_row_is_refused_too(self):
        module = _module(state='stopped')
        with pytest.raises(SystemExit):
            net.power_off_network(module, MagicMock(), _net(running=None))
        assert module.fail_json.called


# ── idempotence ──────────────────────────────────────────────────────────────

class TestAlreadyInTheDesiredStateIsNoChange:
    def test_starting_a_running_vnet_changes_nothing(self):
        obj = _net(running=True)
        changed, _row = net.power_on_network(_module(), MagicMock(), obj)
        assert changed is False
        obj.power_on.assert_not_called()

    def test_stopping_a_stopped_vnet_changes_nothing(self):
        obj = _net(running=False)
        changed, _row = net.power_off_network(
            _module(state='stopped'), MagicMock(), obj)
        assert changed is False
        obj.power_off.assert_not_called()

    def test_a_converged_run_does_not_call_the_power_endpoint_at_all(self):
        """The action posts to vnet_actions. A converged run that still posts
        is a converged run that still restarts a router."""
        obj = _net(running=True)
        net.power_on_network(_module(), MagicMock(), obj)
        assert not obj.method_calls


# ── check mode ───────────────────────────────────────────────────────────────

class TestCheckMode:
    def test_starting_reports_the_change_without_making_it(self):
        obj = _net(running=False)
        changed, row = net.power_on_network(
            _module(check_mode=True), MagicMock(), obj)
        assert changed is True
        assert row['running'] is True
        obj.power_on.assert_not_called()

    def test_stopping_reports_the_change_without_making_it(self):
        obj = _net(running=True)
        changed, row = net.power_off_network(
            _module(check_mode=True, state='stopped'), MagicMock(), obj)
        assert changed is True
        assert row['running'] is False
        obj.power_off.assert_not_called()

    def test_restarting_reports_a_change_without_making_it(self):
        obj = _net(running=True)
        changed, _row = net.restart_network(
            _module(check_mode=True, state='restarted'), MagicMock(), obj)
        assert changed is True
        obj.restart.assert_not_called()


# ── apply_rules is explicit ──────────────────────────────────────────────────

class TestApplyRulesIsNamedRatherThanIncidental:
    """It decides whether a staged rule set becomes live, which is the whole
    point of being able to power a vnet at all."""

    def test_power_on_passes_it_through(self, monkeypatch):
        monkeypatch.setattr(net, 'wait_for_power',
                            lambda *a, **k: {'running': True})
        obj = _net(running=False)
        net.power_on_network(_module(apply_rules=False), MagicMock(), obj)
        obj.power_on.assert_called_once_with(apply_rules=False)

    def test_power_on_defaults_to_applying_them(self, monkeypatch):
        monkeypatch.setattr(net, 'wait_for_power',
                            lambda *a, **k: {'running': True})
        obj = _net(running=False)
        net.power_on_network(_module(apply_rules=True), MagicMock(), obj)
        obj.power_on.assert_called_once_with(apply_rules=True)

    def test_restart_passes_it_through(self, monkeypatch):
        monkeypatch.setattr(net, 'get_network', lambda *a, **k: _net())
        obj = _net(running=True)
        net.restart_network(
            _module(state='restarted', apply_rules=False), MagicMock(), obj)
        obj.restart.assert_called_once_with(apply_rules=False)

    def test_power_off_takes_no_rule_argument(self, monkeypatch):
        """There is nothing to apply on the way down, and passing one would be
        a parameter that reads as if it did something."""
        monkeypatch.setattr(net, 'wait_for_power',
                            lambda *a, **k: {'running': False})
        obj = _net(running=True)
        net.power_off_network(_module(state='stopped'), MagicMock(), obj)
        obj.power_off.assert_called_once_with()


# ── waiting ──────────────────────────────────────────────────────────────────

class TestWaitingFailsRatherThanGivingUp:
    """A power task that quietly stops waiting and reports success is how a
    play goes green against a stopped router."""

    def test_it_returns_the_row_once_the_state_is_reached(self, monkeypatch):
        monkeypatch.setattr(net.time, 'sleep', lambda _s: None)
        monkeypatch.setattr(net, 'get_network', lambda *a, **k: _net(running=True))
        row = net.wait_for_power(_module(), MagicMock(), 'zz-net', True)
        assert row['running'] is True

    def test_it_fails_on_expiry_and_says_what_it_read(self, monkeypatch):
        monkeypatch.setattr(net.time, 'sleep', lambda _s: None)
        monkeypatch.setattr(net, 'get_network', lambda *a, **k: _net(running=False))
        module = _module(power_timeout=1)
        with pytest.raises(SystemExit):
            net.wait_for_power(module, MagicMock(), 'zz-net', True)
        msg = module.fail_json.call_args[1]['msg']
        assert 'did not reach running' in msg
        assert 'running=False' in msg

    def test_a_vnet_that_vanishes_mid_wait_is_a_failure_not_a_success(
            self, monkeypatch):
        monkeypatch.setattr(net.time, 'sleep', lambda _s: None)
        monkeypatch.setattr(net, 'get_network', lambda *a, **k: None)
        module = _module()
        with pytest.raises(SystemExit):
            net.wait_for_power(module, MagicMock(), 'zz-net', True)
        assert 'disappeared' in module.fail_json.call_args[1]['msg']


# ── restart ──────────────────────────────────────────────────────────────────

class TestRestart:
    def test_restarting_a_stopped_vnet_is_refused(self):
        module = _module(state='restarted')
        with pytest.raises(SystemExit):
            net.restart_network(module, MagicMock(), _net(running=False))
        assert 'nothing to restart' in module.fail_json.call_args[1]['msg']

    def test_restarting_a_running_vnet_is_always_a_change(self, monkeypatch):
        """An event, not a state to converge on: there is no reading of the
        system that means "has already been restarted for this reason"."""
        monkeypatch.setattr(net, 'get_network', lambda *a, **k: _net())
        changed, _row = net.restart_network(
            _module(state='restarted'), MagicMock(), _net(running=True))
        assert changed is True


# ── what the module hands back ───────────────────────────────────────────────

class TestTheResultSurfacesTheRestartDebt:
    """Measured on 26.1.8: changing mtu on a RUNNING vnet sets need_restart,
    and until the restart happens the live router keeps the old value. A run
    that reports changed on such a field has changed the record, not the
    router -- an operator who cannot see that has no way to know a restart is
    owed.
    """

    def test_need_restart_is_reported(self):
        assert net.power_result({'need_restart': True})['need_restart'] is True

    def test_a_missing_need_restart_reads_false(self):
        assert net.power_result({})['need_restart'] is False

    def test_running_is_reported(self):
        assert net.power_result({'running': True})['running'] is True

    def test_an_unreadable_power_state_is_reported_as_none_not_false(self):
        assert net.power_result({'name': 'zz-net'})['running'] is None

    def test_the_network_key_still_carries_the_row(self):
        """Existing playbooks read `network`. Power is additive."""
        row = {'$key': 7, 'name': 'zz-net', 'mtu': 1400}
        assert net.power_result(row)['network'] == row


class TestThePowerStatesAreActuallyWired:
    """That the documented choices and the argument_spec agree is already
    guarded for `network` by test_field_contracts.py. What that cannot see is
    whether a new choice reaches anything -- an option that accepts a value
    and does nothing looks identical from the outside.

    So these run main() for real, with the client and AnsibleModule replaced,
    and assert which power call came out the other end.
    """

    def _run(self, state, running, check_mode=False):
        from unittest.mock import patch

        obj = _net(running=running)
        client = MagicMock()
        client.networks.list.return_value = [obj]

        module = _module(check_mode=check_mode, state=state)
        module.exit_json.side_effect = SystemExit(0)

        with patch.object(net, 'AnsibleModule', return_value=module), \
             patch.object(net, 'get_vergeos_client', return_value=client), \
             patch.object(net, 'get_network', return_value=obj), \
             patch.object(net, 'update_network', return_value=(False, dict(obj))), \
             patch.object(net, 'wait_for_power', return_value=dict(obj)):
            with pytest.raises(SystemExit):
                net.main()
        return obj, module

    def test_running_powers_on(self):
        obj, _module_ = self._run('running', running=False)
        obj.power_on.assert_called_once()
        obj.power_off.assert_not_called()

    def test_stopped_powers_off(self):
        obj, _module_ = self._run('stopped', running=True)
        obj.power_off.assert_called_once()
        obj.power_on.assert_not_called()

    def test_restarted_restarts(self):
        obj, _module_ = self._run('restarted', running=True)
        obj.restart.assert_called_once()
        obj.power_on.assert_not_called()
        obj.power_off.assert_not_called()

    def test_present_touches_no_power_call_at_all(self):
        """The states have to stay separable. `present` converging a vnet's
        configuration must not start or stop it as a side effect."""
        obj, _module_ = self._run('present', running=False)
        obj.power_on.assert_not_called()
        obj.power_off.assert_not_called()
        obj.restart.assert_not_called()

    def test_every_state_reports_the_two_power_facts(self):
        for state in ('present', 'running', 'stopped', 'restarted'):
            _obj, module = self._run(state, running=True)
            result = module.exit_json.call_args[1]
            assert 'running' in result, '%s did not report running' % state
            assert 'need_restart' in result, (
                '%s did not report need_restart' % state)


class TestRestartWaitsForTheDebtToClear:
    """`need_restart` does not clear synchronously.

    Measured on 26.1.8 by the live ladder failing: the module read the flag
    back immediately after restart() and got True, and the same flag was False
    moments later. Reading straight through reports a restart that has not
    finished.
    """

    def test_it_waits_rather_than_reading_straight_through(self, monkeypatch):
        monkeypatch.setattr(net.time, 'sleep', lambda _s: None)
        seen = iter([
            _net(running=True, need_restart=True),
            _net(running=True, need_restart=True),
            _net(running=True, need_restart=False),
        ])
        monkeypatch.setattr(net, 'get_network', lambda *a, **k: next(seen))
        row = net.wait_for_restart(_module(), MagicMock(), 'zz-net')
        assert row['need_restart'] is False

    def test_a_reset_that_leaves_it_down_is_not_a_restart(self, monkeypatch):
        """Both halves of the post-condition. A flag cleared by the router
        falling over is not the outcome that was asked for."""
        monkeypatch.setattr(net.time, 'sleep', lambda _s: None)
        monkeypatch.setattr(
            net, 'get_network',
            lambda *a, **k: _net(running=False, need_restart=False))
        module = _module(power_timeout=1)
        with pytest.raises(SystemExit):
            net.wait_for_restart(module, MagicMock(), 'zz-net')
        msg = module.fail_json.call_args[1]['msg']
        assert 'running=False' in msg

    def test_an_uncleared_debt_expires_loudly(self, monkeypatch):
        monkeypatch.setattr(net.time, 'sleep', lambda _s: None)
        monkeypatch.setattr(
            net, 'get_network',
            lambda *a, **k: _net(running=True, need_restart=True))
        module = _module(power_timeout=1)
        with pytest.raises(SystemExit):
            net.wait_for_restart(module, MagicMock(), 'zz-net')
        assert 'need_restart=True' in module.fail_json.call_args[1]['msg']


class TestDeletingARunningNetwork:
    """The API refuses: "Network must be stopped to delete".

    Found by this PR's own ladder teardown failing. Before the module could
    power a vnet off there was no way out of that from the collection at all,
    so `state: absent` on a running network failed with an opaque API error.
    """

    def test_a_running_network_is_stopped_first(self, monkeypatch):
        monkeypatch.setattr(net, 'wait_for_power', lambda *a, **k: {})
        obj = _net(running=True)
        monkeypatch.setattr(net, 'get_network', lambda *a, **k: obj)
        net.delete_network(_module(state='absent'), MagicMock(), obj)
        obj.power_off.assert_called_once()
        obj.delete.assert_called_once()

    def test_a_stopped_network_is_not_power_cycled_on_the_way_out(self):
        obj = _net(running=False)
        net.delete_network(_module(state='absent'), MagicMock(), obj)
        obj.power_off.assert_not_called()
        obj.delete.assert_called_once()

    def test_check_mode_deletes_nothing(self):
        obj = _net(running=True)
        net.delete_network(
            _module(check_mode=True, state='absent'), MagicMock(), obj)
        obj.delete.assert_not_called()
        obj.power_off.assert_not_called()
