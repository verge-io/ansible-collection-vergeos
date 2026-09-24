# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Waiting for a VM to actually reach the power state it was asked for (#114).

What was there before:

    vm.power_on()
    for _attempt in range(30):
        time.sleep(2)
        vm.refresh()
        if dict(vm).get('status') == 'running':
            break
    return True, dict(vm)          # reached whichever way the loop ended

Driven directly against a VM whose status never changed, that returned
``changed=True`` with ``status='starting'`` after thirty refreshes and sixty
seconds. The play goes green against a VM that is not running.

The platform refuses an over-provisioned start synchronously -- measured,
"Machine exceeds the max amount of ram allowed on this cluster" -- which is
why this survived. What it does not refuse is a start it accepts and then does
not finish.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from unittest.mock import MagicMock

import pytest

from ansible_collections.vergeio.vergeos.plugins.modules import vm as vm_mod


def _module(check_mode=False, **over):
    params = {'name': 'zz-vm', 'power_timeout': 60}
    params.update(over)
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    module.fail_json.side_effect = SystemExit(1)
    return module


class FakeVM(dict):
    """A VM that reaches ``arrives_at`` after that many refreshes.

    ``None`` means it never gets there -- the case the old loop fell through.
    """

    def __init__(self, status, becomes=None, arrives_at=None):
        super().__init__({'$key': 1, 'name': 'zz-vm', 'status': status,
                          'running': status == 'running'})
        self._becomes = becomes
        self._arrives_at = arrives_at
        self.refreshes = 0
        self.power_on_calls = []
        self.power_off_calls = []

    def refresh(self):
        self.refreshes += 1
        if self._arrives_at is not None and self.refreshes >= self._arrives_at:
            self['status'] = self._becomes
            self['running'] = self._becomes == 'running'

    def power_on(self):
        self.power_on_calls.append(True)

    def power_off(self, force=False):
        self.power_off_calls.append(force)


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """A clock that only moves when the module sleeps.

    Patching sleep alone is not enough: the wait compares against
    `time.time()`, so a three-second timeout with instant sleeps becomes a
    three-second busy loop. Four such tests cost twelve seconds of a suite
    that runs in five.
    """
    now = [1000.0]
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(vm_mod.time, 'sleep', fake_sleep)
    monkeypatch.setattr(vm_mod.time, 'time', lambda: now[0])
    return slept


# ── the defect ───────────────────────────────────────────────────────────────

class TestAWaitThatExpiresIsAFailure:
    def test_powering_on_a_vm_that_never_arrives_fails(self):
        module = _module(power_timeout=3)
        vm = FakeVM('stopped')
        with pytest.raises(SystemExit):
            vm_mod.power_on_vm(module, MagicMock(), vm)
        assert module.fail_json.called, (
            'the wait expired and the module reported success -- this is #114')

    def test_and_says_which_state_it_wanted_and_what_it_read(self):
        module = _module(power_timeout=3)
        vm = FakeVM('starting')
        with pytest.raises(SystemExit):
            vm_mod.power_on_vm(module, MagicMock(), vm)
        msg = module.fail_json.call_args[1]['msg']
        assert "did not reach 'running'" in msg
        assert "status 'starting'" in msg
        assert 'zz-vm' in msg

    def test_it_names_the_knob_that_would_allow_longer(self):
        """A failure an operator cannot act on is a failure they route
        around."""
        module = _module(power_timeout=3)
        with pytest.raises(SystemExit):
            vm_mod.power_on_vm(module, MagicMock(), FakeVM('stopped'))
        assert 'power_timeout' in module.fail_json.call_args[1]['msg']

    def test_powering_off_a_vm_that_never_stops_fails_too(self):
        module = _module(power_timeout=3)
        vm = FakeVM('running')
        with pytest.raises(SystemExit):
            vm_mod.power_off_vm(module, MagicMock(), vm)
        assert "did not reach 'stopped'" in module.fail_json.call_args[1]['msg']

    def test_the_power_call_was_still_made(self):
        """The failure is about the outcome, not the request. A retry must not
        assume the first attempt never happened."""
        module = _module(power_timeout=3)
        vm = FakeVM('stopped')
        with pytest.raises(SystemExit):
            vm_mod.power_on_vm(module, MagicMock(), vm)
        assert vm.power_on_calls == [True]


# ── the ordinary path ────────────────────────────────────────────────────────

class TestAVMThatArrivesIsReported:
    def test_it_returns_the_row_once_the_state_is_reached(self):
        vm = FakeVM('stopped', becomes='running', arrives_at=1)
        changed, row = vm_mod.power_on_vm(_module(), MagicMock(), vm)
        assert changed is True
        assert row['status'] == 'running'

    def test_a_few_polls_are_allowed(self):
        vm = FakeVM('stopped', becomes='running', arrives_at=4)
        changed, _row = vm_mod.power_on_vm(_module(), MagicMock(), vm)
        assert changed is True
        assert vm.refreshes == 4

    def test_it_keeps_polling_until_the_timeout_and_not_forever(
            self, _no_real_sleeping):
        """With a one-second poll and a three-second budget: look, sleep,
        look, sleep, look, sleep, look -- then give up."""
        module = _module(power_timeout=3)
        vm = FakeVM('stopped')
        with pytest.raises(SystemExit):
            vm_mod.power_on_vm(module, MagicMock(), vm)
        assert vm.refreshes == 4
        assert _no_real_sleeping == [1, 1, 1]

    def test_stopping_works_the_same_way(self):
        vm = FakeVM('running', becomes='stopped', arrives_at=2)
        changed, row = vm_mod.power_off_vm(
            _module(), MagicMock(), vm)
        assert changed is True
        assert row['status'] == 'stopped'

    def test_the_state_is_read_before_the_first_sleep(self, _no_real_sleeping):
        """Measured on 26.1.8: a VM reports running about half a second after
        the power call returns. The old loop slept two seconds BEFORE looking,
        so every power task cost at least that whether it needed to or not."""
        vm = FakeVM('stopped', becomes='running', arrives_at=1)
        vm_mod.power_on_vm(_module(), MagicMock(), vm)
        assert _no_real_sleeping == [], (
            'the module waited before it had looked even once')

    def test_power_off_is_forced_which_is_why_it_is_reliable(self):
        vm = FakeVM('running', becomes='stopped', arrives_at=1)
        vm_mod.power_off_vm(_module(), MagicMock(), vm)
        assert vm.power_off_calls == [True]


class TestAlreadyInTheDesiredState:
    def test_starting_a_running_vm_changes_nothing_and_waits_for_nothing(self):
        vm = FakeVM('running')
        changed, _row = vm_mod.power_on_vm(_module(), MagicMock(), vm)
        assert changed is False
        assert vm.refreshes == 0
        assert vm.power_on_calls == []

    def test_stopping_a_stopped_vm_changes_nothing(self):
        vm = FakeVM('stopped')
        changed, _row = vm_mod.power_off_vm(_module(), MagicMock(), vm)
        assert changed is False
        assert vm.power_off_calls == []

    def test_the_running_flag_is_honoured_as_well_as_the_status(self):
        """#110: `running` and `status` come from the same projection and
        either one arriving is enough to know the answer."""
        vm = FakeVM('stopped')
        vm['running'] = True
        changed, _row = vm_mod.power_on_vm(_module(), MagicMock(), vm)
        assert changed is False


class TestCheckMode:
    def test_starting_reports_the_change_without_making_it(self):
        vm = FakeVM('stopped')
        changed, row = vm_mod.power_on_vm(
            _module(check_mode=True), MagicMock(), vm)
        assert changed is True
        assert row['status'] == 'running'
        assert vm.power_on_calls == []
        assert vm.refreshes == 0

    def test_stopping_reports_the_change_without_making_it(self):
        vm = FakeVM('running')
        changed, row = vm_mod.power_off_vm(
            _module(check_mode=True), MagicMock(), vm)
        assert changed is True
        assert row['status'] == 'stopped'
        assert vm.power_off_calls == []


class TestTheTimeoutIsTheOperatorsToSet:
    def test_zero_means_check_once(self):
        """Which is what makes the expiry path testable against a real system
        without contriving a VM that cannot start."""
        module = _module(power_timeout=0)
        vm = FakeVM('initializing')
        with pytest.raises(SystemExit):
            vm_mod.power_on_vm(module, MagicMock(), vm)
        assert vm.refreshes == 1

    def test_zero_still_succeeds_if_it_is_already_there(self):
        """"Check once" has to mean a check, not an automatic failure."""
        vm = FakeVM('stopped', becomes='running', arrives_at=1)
        changed, row = vm_mod.power_on_vm(
            _module(power_timeout=0), MagicMock(), vm)
        assert changed is True
        assert row['status'] == 'running'

    def test_a_negative_timeout_behaves_like_zero_rather_than_looping_forever(self):
        module = _module(power_timeout=-5)
        with pytest.raises(SystemExit):
            vm_mod.power_on_vm(module, MagicMock(), FakeVM('stopped'))
        assert module.fail_json.called

    def test_the_default_is_the_sixty_seconds_it_always_waited(self):
        import re
        source = open(vm_mod.__file__).read()
        assert re.search(r"power_timeout=dict\(type='int', default=60\)",
                         source), (
            'the default changed; the old code waited 60s before giving up '
            'and anything shorter would start failing runs that used to pass')
