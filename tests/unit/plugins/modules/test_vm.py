#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vm module"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock, patch


# `mock_pyvergeos` now lives in tests/unit/conftest.py, which stubs the SDK
# only when it is genuinely absent and gives the stub real exception
# classes. The local copy replaced pyvergeos.exceptions with a MagicMock,
# which made every NotFoundError un-raisable. See issue #66.


class TestVmStatePresent:
    """Tests for vm module with state=present"""

    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_creates_vm_when_not_exists(self, mock_get_client, make_resource):
        """Test that VM is created when it doesn't exist"""
        from pyvergeos.exceptions import NotFoundError

        # Setup mock client
        mock_client = MagicMock()
        mock_client.vms.list.return_value = []
        mock_new_vm = MagicMock()
        mock_new_vm_state = {
            '$key': 1, 'name': 'new-vm', 'cpu_cores': 4, 'ram': 8192
        }
        mock_new_vm = make_resource(mock_new_vm_state)
        mock_client.vms.create.return_value = mock_new_vm
        mock_get_client.return_value = mock_client

        # Create mock module
        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': 'new-vm',
            'state': 'present',
            'description': 'Test VM',
            'enabled': True,
            'os_family': 'linux',
            'cpu_cores': 4,
            'ram': 8192,
            'machine_type': 'q35',
            'machine_subtype': None,
            'bios_type': None,
            'network': None,
            'boot_order': None,
            'power_timeout': 60
        }
        mock_module.check_mode = False

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.AnsibleModule', return_value=mock_module):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.NotFoundError', NotFoundError):
                from ansible_collections.vergeio.vergeos.plugins.modules import vm
                mock_module.reset_mock()

                try:
                    vm.main()
                except SystemExit:
                    pass

        mock_client.vms.create.assert_called_once()
        mock_module.exit_json.assert_called_once()
        call_kwargs = mock_module.exit_json.call_args[1]
        assert call_kwargs['changed'] is True

    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_updates_vm_when_exists_with_changes(self, mock_get_client, make_resource):
        """Test that VM is updated when it exists with different config"""
        # Setup mock client with existing VM
        mock_client = MagicMock()
        mock_vm = MagicMock()
        mock_vm_state = {
            '$key': 1, 'name': 'existing-vm', 'cpu_cores': 2, 'ram': 4096
        }
        mock_vm = make_resource(mock_vm_state)
        mock_client.vms.list.return_value = [mock_vm]
        mock_get_client.return_value = mock_client

        # Create mock module with updated params
        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': 'existing-vm',
            'state': 'present',
            'description': None,
            'enabled': True,
            'os_family': None,
            'cpu_cores': 4,  # Changed from 2
            'ram': 8192,     # Changed from 4096
            'machine_type': None,
            'machine_subtype': None,
            'bios_type': None,
            'network': None,
            'boot_order': None,
            'power_timeout': 60
        }
        mock_module.check_mode = False

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.AnsibleModule', return_value=mock_module):
            from ansible_collections.vergeio.vergeos.plugins.modules import vm
            mock_module.reset_mock()

            try:
                vm.main()
            except SystemExit:
                pass

        # Verify VM save was called (update)
        mock_vm.save.assert_called_once()
        mock_module.exit_json.assert_called_once()
        call_kwargs = mock_module.exit_json.call_args[1]
        assert call_kwargs['changed'] is True

    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_partial_update_sends_changes_and_preserves_enabled(self, mock_get_client, make_resource):
        """Issues #80/#83: save() receives the changed fields; omitted enabled is not sent"""
        mock_client = MagicMock()
        mock_vm = MagicMock()
        mock_vm_state = {
            '$key': 1, 'name': 'existing-vm', 'enabled': False, 'description': ''
        }
        mock_vm = make_resource(mock_vm_state)
        mock_vm.save.return_value = {'$key': 1, 'name': 'existing-vm', 'enabled': False, 'description': 'x'}
        mock_client.vms.list.return_value = [mock_vm]
        mock_get_client.return_value = mock_client

        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com', 'username': 'admin', 'password': 'secret',
            'insecure': False, 'name': 'existing-vm', 'state': 'present',
            'description': 'x', 'enabled': None, 'os_family': None, 'cpu_cores': None,
            'ram': None, 'machine_type': None, 'machine_subtype': None,
            'bios_type': None, 'network': None, 'boot_order': None,
            'power_timeout': 60
        }
        mock_module.check_mode = False

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.AnsibleModule', return_value=mock_module):
            from ansible_collections.vergeio.vergeos.plugins.modules import vm
            mock_module.reset_mock()
            try:
                vm.main()
            except SystemExit:
                pass

        mock_vm.save.assert_called_once_with(description='x')
        assert mock_module.exit_json.call_args[1]['changed'] is True

    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_no_change_when_vm_matches(self, mock_get_client, make_resource):
        """Test that no change when VM already matches desired state"""
        # Setup mock client with VM that matches params
        mock_client = MagicMock()
        mock_vm = MagicMock()
        mock_vm_state = {
            '$key': 1, 'name': 'existing-vm', 'cpu_cores': 4, 'ram': 8192
        }
        mock_vm = make_resource(mock_vm_state)
        mock_client.vms.list.return_value = [mock_vm]
        mock_get_client.return_value = mock_client

        # Create mock module with matching params
        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': 'existing-vm',
            'state': 'present',
            'description': None,
            'enabled': None,
            'os_family': None,
            'cpu_cores': 4,
            'ram': 8192,
            'machine_type': None,
            'machine_subtype': None,
            'bios_type': None,
            'network': None,
            'boot_order': None,
            'power_timeout': 60
        }
        mock_module.check_mode = False

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.AnsibleModule', return_value=mock_module):
            from ansible_collections.vergeio.vergeos.plugins.modules import vm
            mock_module.reset_mock()

            try:
                vm.main()
            except SystemExit:
                pass

        mock_vm.save.assert_not_called()
        mock_module.exit_json.assert_called_once()
        call_kwargs = mock_module.exit_json.call_args[1]
        assert call_kwargs['changed'] is False


class TestVmStateAbsent:
    """Tests for vm module with state=absent"""

    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_deletes_vm_when_exists(self, mock_get_client, make_resource):
        """Test that VM is deleted when it exists"""
        # Setup mock client
        mock_client = MagicMock()
        mock_vm = MagicMock()
        mock_vm_state = {'$key': 1, 'name': 'delete-me'}
        mock_vm = make_resource(mock_vm_state)
        mock_client.vms.list.return_value = [mock_vm]
        mock_get_client.return_value = mock_client

        # Create mock module
        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': 'delete-me',
            'state': 'absent',
            'description': None,
            'enabled': True,
            'os_family': None,
            'cpu_cores': None,
            'ram': None,
            'machine_type': None,
            'machine_subtype': None,
            'bios_type': None,
            'network': None,
            'boot_order': None,
            'power_timeout': 60
        }
        mock_module.check_mode = False

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.AnsibleModule', return_value=mock_module):
            from ansible_collections.vergeio.vergeos.plugins.modules import vm
            mock_module.reset_mock()

            try:
                vm.main()
            except SystemExit:
                pass

        mock_vm.delete.assert_called_once()
        mock_module.exit_json.assert_called_once()
        call_kwargs = mock_module.exit_json.call_args[1]
        assert call_kwargs['changed'] is True

    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_no_change_when_vm_not_exists(self, mock_get_client):
        """Test that no change when VM doesn't exist for absent state"""
        from pyvergeos.exceptions import NotFoundError

        # Setup mock client
        mock_client = MagicMock()
        mock_client.vms.list.return_value = []
        mock_get_client.return_value = mock_client

        # Create mock module
        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': 'nonexistent-vm',
            'state': 'absent',
            'description': None,
            'enabled': True,
            'os_family': None,
            'cpu_cores': None,
            'ram': None,
            'machine_type': None,
            'machine_subtype': None,
            'bios_type': None,
            'network': None,
            'boot_order': None,
            'power_timeout': 60
        }
        mock_module.check_mode = False

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.AnsibleModule', return_value=mock_module):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.NotFoundError', NotFoundError):
                from ansible_collections.vergeio.vergeos.plugins.modules import vm
                mock_module.reset_mock()

                try:
                    vm.main()
                except SystemExit:
                    pass

        mock_module.exit_json.assert_called_once()
        call_kwargs = mock_module.exit_json.call_args[1]
        assert call_kwargs['changed'] is False


class TestVmStatePower:
    """Tests for vm module with state=running/stopped"""

    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_powers_on_stopped_vm(self, mock_get_client, make_resource):
        """Test that stopped VM is powered on"""
        # Setup mock client with stopped VM
        mock_client = MagicMock()
        # The module reads 'status', not 'power_state'. Modelling the wrong
        # field made the VM look neither running nor stopped, so the post-
        # power wait loop ran all 30 iterations -- 60 seconds per test.
        # The mock now transitions on power_on(), as the platform does.
        vm_state = {'$key': 1, 'name': 'my-vm', 'status': 'stopped'}
        mock_vm = make_resource(vm_state)
        mock_vm.power_on.side_effect = lambda *a, **k: vm_state.update(status='running')
        mock_client.vms.list.return_value = [mock_vm]
        mock_get_client.return_value = mock_client

        # Create mock module
        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': 'my-vm',
            'state': 'running',
            'description': None,
            'enabled': True,
            'os_family': None,
            'cpu_cores': None,
            'ram': None,
            'machine_type': None,
            'machine_subtype': None,
            'bios_type': None,
            'network': None,
            'boot_order': None,
            'power_timeout': 60
        }
        mock_module.check_mode = False

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.AnsibleModule', return_value=mock_module):
            from ansible_collections.vergeio.vergeos.plugins.modules import vm
            mock_module.reset_mock()

            try:
                vm.main()
            except SystemExit:
                pass

        mock_vm.power_on.assert_called_once()
        mock_module.exit_json.assert_called_once()
        call_kwargs = mock_module.exit_json.call_args[1]
        assert call_kwargs['changed'] is True

    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_powers_off_running_vm(self, mock_get_client, make_resource):
        """Test that running VM is powered off"""
        # Setup mock client with running VM
        mock_client = MagicMock()
        # See the note in test_powers_on_stopped_vm: 'status' is the field the
        # module reads, and the mock transitions on power_off().
        vm_state = {'$key': 1, 'name': 'my-vm', 'status': 'running'}
        mock_vm = make_resource(vm_state)
        mock_vm.power_off.side_effect = lambda *a, **k: vm_state.update(status='stopped')
        mock_client.vms.list.return_value = [mock_vm]
        mock_get_client.return_value = mock_client

        # Create mock module
        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': 'my-vm',
            'state': 'stopped',
            'description': None,
            'enabled': True,
            'os_family': None,
            'cpu_cores': None,
            'ram': None,
            'machine_type': None,
            'machine_subtype': None,
            'bios_type': None,
            'network': None,
            'boot_order': None,
            'power_timeout': 60
        }
        mock_module.check_mode = False

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.AnsibleModule', return_value=mock_module):
            from ansible_collections.vergeio.vergeos.plugins.modules import vm
            mock_module.reset_mock()

            try:
                vm.main()
            except SystemExit:
                pass

        mock_vm.power_off.assert_called_once()


class TestVmCheckMode:
    """Tests for vm module check_mode"""

    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_check_mode_does_not_create(self, mock_get_client):
        """Test that check_mode doesn't actually create VM"""
        from pyvergeos.exceptions import NotFoundError

        mock_client = MagicMock()
        mock_client.vms.list.return_value = []
        mock_get_client.return_value = mock_client

        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': 'new-vm',
            'state': 'present',
            'description': None,
            'enabled': True,
            'os_family': None,
            'cpu_cores': 4,
            'ram': 8192,
            'machine_type': None,
            'machine_subtype': None,
            'bios_type': None,
            'network': None,
            'boot_order': None,
            'power_timeout': 60
        }
        mock_module.check_mode = True

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.AnsibleModule', return_value=mock_module):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.NotFoundError', NotFoundError):
                from ansible_collections.vergeio.vergeos.plugins.modules import vm
                mock_module.reset_mock()

                try:
                    vm.main()
                except SystemExit:
                    pass

        mock_client.vms.create.assert_not_called()
        mock_module.exit_json.assert_called_once()
        call_kwargs = mock_module.exit_json.call_args[1]
        assert call_kwargs['changed'] is True  # Would change, but didn't

    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_check_mode_does_not_delete(self, mock_get_client, make_resource):
        """Test that check_mode doesn't actually delete VM"""
        mock_client = MagicMock()
        mock_vm = MagicMock()
        mock_vm_state = {'$key': 1, 'name': 'delete-me'}
        mock_vm = make_resource(mock_vm_state)
        mock_client.vms.list.return_value = [mock_vm]
        mock_get_client.return_value = mock_client

        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': 'delete-me',
            'state': 'absent',
            'description': None,
            'enabled': True,
            'os_family': None,
            'cpu_cores': None,
            'ram': None,
            'machine_type': None,
            'machine_subtype': None,
            'bios_type': None,
            'network': None,
            'boot_order': None,
            'power_timeout': 60
        }
        mock_module.check_mode = True

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.AnsibleModule', return_value=mock_module):
            from ansible_collections.vergeio.vergeos.plugins.modules import vm
            mock_module.reset_mock()

            try:
                vm.main()
            except SystemExit:
                pass

        mock_vm.delete.assert_not_called()
        mock_module.exit_json.assert_called_once()
        call_kwargs = mock_module.exit_json.call_args[1]
        assert call_kwargs['changed'] is True  # Would change, but didn't


class TestMachineTypeMatching:
    """A machine-type alias is stored expanded, so literal comparison fails.

    The platform expands 'q35' to 'pc-q35-10.0' on write. Comparing the alias
    against the stored value literally never matched, so a fully converged VM
    reported changed on every run and re-sent the field. Verified live on
    26.1.8, where examples/create_vm.yml reported changed forever.
    """

    def _fn(self):
        from ansible_collections.vergeio.vergeos.plugins.modules.vm import (
            machine_type_matches,
        )
        return machine_type_matches

    @pytest.mark.parametrize('desired,current', [
        ('q35', 'pc-q35-10.0'),
        ('q35', 'pc-q35-9.2'),
        ('pc', 'pc-i440fx-3.1'),
        ('pc-q35-10.0', 'pc-q35-10.0'),
    ])
    def test_converged(self, desired, current):
        assert self._fn()(desired, current) is True

    @pytest.mark.parametrize('desired,current', [
        # Different family -- a real change.
        ('q35', 'pc-i440fx-3.1'),
        ('pc', 'pc-q35-10.0'),
        # Pinning an exact version against a different one is a real change.
        ('pc-q35-9.2', 'pc-q35-10.0'),
        # Never treat a missing value as converged.
        ('q35', None),
        ('q35', ''),
    ])
    def test_needs_change(self, desired, current):
        assert self._fn()(desired, current) is False

    def test_alias_does_not_match_unrelated_prefix(self):
        # 'pc' must not swallow every pc-* type; it means i440fx.
        assert self._fn()('pc', 'pc-q35-10.0') is False


class TestSnapshotProfileEnrolment:
    """The half of #22 that never reached main, arriving with #39.

    `vm.py` used to carry resolve_snapshot_profile() reading a parameter it
    did not declare -- unreachable code advertising a feature that was not
    there (#95). Deleted in #96, restored here with the module it needs.

    The trap it has to avoid, measured on 26.1.8: `snapshot_profile` is NOT in
    the SDK's default projection for a vm, so the row the module already holds
    says None whatever the VM is enrolled in. Comparing against that makes
    enrolment report changed on every run, and makes clearing a silent no-op
    -- None and '' both look empty.
    """

    def _client(self, current='', profile_key=7):
        client = MagicMock()
        vm_row = MagicMock()
        data = {'$key': 3, 'name': 'zz-vm', 'enabled': True}
        vm_row.keys.side_effect = lambda: list(data.keys())
        vm_row.__getitem__.side_effect = data.__getitem__
        vm_row.__iter__.side_effect = lambda: iter(data)
        vm_row.save.return_value = vm_row
        client.vms.list.return_value = [vm_row]

        # The explicit-projection read, which is the only place the current
        # value can honestly come from.
        prof_row = MagicMock()
        pdata = {'$key': 3, 'snapshot_profile': current}
        prof_row.keys.side_effect = lambda: list(pdata.keys())
        prof_row.__getitem__.side_effect = pdata.__getitem__
        prof_row.__iter__.side_effect = lambda: iter(pdata)
        client.vms.get.return_value = prof_row

        p = MagicMock()
        pd = {'$key': profile_key, 'name': 'nightly'}
        p.keys.side_effect = lambda: list(pd.keys())
        p.__getitem__.side_effect = pd.__getitem__
        p.__iter__.side_effect = lambda: iter(pd)
        client.snapshot_profiles.list.return_value = [p]

        client.vm_row = vm_row
        return client

    def _params(self, **overrides):
        params = {
            'host': 'vergeos.example.com', 'username': 'admin',
            'password': 'secret', 'insecure': False, 'api_key': None,
            'name': 'zz-vm', 'state': 'present', 'description': None,
            'enabled': None, 'os_family': None, 'cpu_cores': None,
            'ram': None, 'machine_type': None, 'machine_subtype': None,
            'bios_type': None, 'network': None, 'boot_order': None,
            'snapshot_profile': None,
        }
        params.update(overrides)
        return params

    def _run(self, client, params, check_mode=False):
        module = MagicMock()
        module.params = params
        module.check_mode = check_mode
        module.exit_json.side_effect = SystemExit
        module.fail_json.side_effect = SystemExit
        base = 'ansible_collections.vergeio.vergeos.plugins.modules.vm'
        with patch('%s.get_vergeos_client' % base, return_value=client), \
             patch('%s.HAS_PYVERGEOS' % base, True), \
             patch('%s.AnsibleModule' % base, return_value=module):
            from ansible_collections.vergeio.vergeos.plugins.modules import vm
            try:
                vm.main()
            except SystemExit:
                pass
        return module

    def test_the_current_value_is_read_with_an_explicit_field_list(self):
        client = self._client(current='7')
        self._run(client, self._params(snapshot_profile='nightly'))

        client.vms.get.assert_called_once_with(
            3, fields=['$key', 'snapshot_profile'])

    def test_an_enrolled_vm_converges(self):
        """Without the explicit read this reports changed forever."""
        client = self._client(current='7')
        module = self._run(client, self._params(snapshot_profile='nightly'))

        client.vm_row.save.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_enrolling_sends_the_profile_key_not_its_name(self):
        client = self._client(current='')
        module = self._run(client, self._params(snapshot_profile='nightly'))

        client.vm_row.save.assert_called_once_with(snapshot_profile='7')
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_an_empty_string_clears_the_enrolment(self):
        """Not a no-op: '' is the documented way to remove a VM from its
        profile, and `if params.get(...)` would swallow it."""
        client = self._client(current='7')
        module = self._run(client, self._params(snapshot_profile=''))

        client.vm_row.save.assert_called_once_with(snapshot_profile='')
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_clearing_an_unenrolled_vm_converges(self):
        client = self._client(current='')
        module = self._run(client, self._params(snapshot_profile=''))

        client.vm_row.save.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_omitting_it_leaves_the_enrolment_alone(self):
        client = self._client(current='7')
        module = self._run(client, self._params(description=None))

        client.vms.get.assert_not_called()
        client.vm_row.save.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_an_unknown_profile_is_a_named_failure(self):
        client = self._client(current='')
        client.snapshot_profiles.list.return_value = []
        module = self._run(client, self._params(snapshot_profile='nope'))

        assert "Snapshot profile 'nope' not found" \
            in module.fail_json.call_args.kwargs['msg']
        client.vm_row.save.assert_not_called()

    def test_creating_with_a_profile_resolves_it(self):
        client = self._client(current='')
        client.vms.list.return_value = []
        created = MagicMock()
        cdata = {'$key': 9, 'name': 'zz-vm'}
        created.keys.side_effect = lambda: list(cdata.keys())
        created.__getitem__.side_effect = cdata.__getitem__
        created.__iter__.side_effect = lambda: iter(cdata)
        client.vms.create.return_value = created

        self._run(client, self._params(snapshot_profile='nightly'))

        assert client.vms.create.call_args.kwargs['snapshot_profile'] == 7
