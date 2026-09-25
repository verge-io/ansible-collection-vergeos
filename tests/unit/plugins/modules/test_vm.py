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
        assert call_kwargs['msg'] == "VM 'delete-me' deleted"

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
        assert call_kwargs['msg'] == "Would delete VM 'delete-me'"
        assert 'deleted' not in call_kwargs['msg']


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


class TestRamRounding:
    """RAM that is not a multiple of 256 must converge. See issue #123.

    VMManager.create rounds UP to a multiple of 256 MB. The update path used
    to send the raw number, and the platform floors that. ram: 1000 therefore
    created a VM at 1024 and the next identical run stored 768, then reported
    changed forever. Create and update both have to use the rounded-up value.
    """

    def _params(self, **overrides):
        params = {
            'host': 'vergeos.example.com', 'username': 'admin',
            'password': 'secret', 'insecure': False,
            'name': 'zz-vm', 'state': 'present', 'description': None,
            'enabled': None, 'os_family': None, 'cpu_cores': None,
            'ram': 1000, 'machine_type': None, 'bios_type': None,
            'boot_order': None, 'snapshot_profile': None,
            'power_timeout': 60,
        }
        params.update(overrides)
        return params

    def _run(self, client, params, check_mode=False):
        module = MagicMock()
        module.params = params
        module.check_mode = check_mode
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

    def _fn(self):
        from ansible_collections.vergeio.vergeos.plugins.modules.vm import (
            normalize_ram,
        )
        return normalize_ram

    @pytest.mark.parametrize('requested,stored', [
        (1000, 1024),
        (1300, 1536),
        (2000, 2048),
        (1, 256),
        (256, 256),
        (257, 512),
        (8192, 8192),
    ])
    def test_rounds_up_to_a_multiple_of_256(self, requested, stored):
        assert self._fn()(requested) == stored

    def test_ram_1000_converges_on_the_second_run_and_stores_1024(
            self, make_resource):
        """Create stores 1024. The identical playbook then changes nothing."""
        client = MagicMock()
        created = {}

        def create(**kwargs):
            created.update(kwargs)
            created['$key'] = 1
            return make_resource(dict(created))

        client.vms.create.side_effect = create
        client.vms.list.return_value = []

        first = self._run(client, self._params(ram=1000))
        assert client.vms.create.call_args.kwargs['ram'] == 1024
        assert created['ram'] == 1024
        assert first.exit_json.call_args.kwargs['changed'] is True

        stored = make_resource({
            '$key': 1, 'name': 'zz-vm', 'ram': created['ram'],
        })
        client.vms.list.return_value = [stored]
        second = self._run(client, self._params(ram=1000))

        stored.save.assert_not_called()
        assert second.exit_json.call_args.kwargs['changed'] is False

    def test_an_update_writes_the_rounded_up_value(self, make_resource):
        """A VM left at the platform's floored value is repaired to 1024."""
        stored = make_resource({'$key': 1, 'name': 'zz-vm', 'ram': 768})
        stored.save.return_value = stored
        client = MagicMock()
        client.vms.list.return_value = [stored]

        module = self._run(client, self._params(ram=1000))

        stored.save.assert_called_once_with(ram=1024)
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_a_numeric_string_already_at_1024_converges(self, make_resource):
        stored = make_resource({'$key': 1, 'name': 'zz-vm', 'ram': '1024'})
        client = MagicMock()
        client.vms.list.return_value = [stored]

        module = self._run(client, self._params(ram=1000))

        stored.save.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_another_change_does_not_resend_converged_ram(self, make_resource):
        stored = make_resource({
            '$key': 1, 'name': 'zz-vm', 'ram': 1024, 'description': '',
        })
        stored.save.return_value = stored
        client = MagicMock()
        client.vms.list.return_value = [stored]

        self._run(client, self._params(ram=1000, description='web'))

        stored.save.assert_called_once_with(description='web')


class TestIssue87NonColumnFields:
    """machine_subtype, bios_type and network are not VM columns (#87).

    The API accepts an unknown field with HTTP 200 and discards it, so
    sending the option name reported success and changed on every later run.
    machine_subtype and network are not options. bios_type is written as the
    boolean uefi, on both the create and the update path.
    """

    def _module(self, **overrides):
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        params = {key: None for key in vm.CREATE_PARAM_MAP}
        params.update(overrides)
        module = MagicMock()
        module.params = params
        module.check_mode = False
        return module

    def test_create_writes_uefi_and_drops_the_removed_names(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        module = self._module(
            name='zz-vm', enabled=True, bios_type='uefi',
            machine_subtype='q35', network='Core',
        )
        client = MagicMock()
        client.vms.create.return_value = make_resource(
            {'$key': 1, 'name': 'zz-vm', 'uefi': True})

        changed, _row = vm.create_vm(module, client)

        assert changed is True
        sent = client.vms.create.call_args.kwargs
        assert sent['uefi'] is True
        for name in ('bios_type', 'machine_subtype', 'network'):
            assert name not in sent, (
                'create sent %r, which is not a VM column' % name)

    def test_create_seabios_writes_uefi_false(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        module = self._module(name='zz-vm', enabled=True, bios_type='seabios')
        client = MagicMock()
        client.vms.create.return_value = make_resource(
            {'$key': 1, 'name': 'zz-vm', 'uefi': False})

        vm.create_vm(module, client)

        assert client.vms.create.call_args.kwargs['uefi'] is False

    def test_update_converges_when_uefi_already_matches(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        stored = make_resource(
            {'$key': 1, 'name': 'zz-vm', 'uefi': True, 'description': 'd'})
        module = self._module(
            name='zz-vm', bios_type='uefi',
            machine_subtype='q35', network='Core',
        )

        changed, _row = vm.update_vm(module, MagicMock(), stored)

        assert changed is False
        stored.save.assert_not_called()

    def test_update_converges_when_uefi_is_the_integer_one(self, make_resource):
        """A numeric 1 is the same firmware choice as True."""
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        stored = make_resource({'$key': 1, 'name': 'zz-vm', 'uefi': 1})
        module = self._module(name='zz-vm', bios_type='uefi')

        changed, _row = vm.update_vm(module, MagicMock(), stored)

        assert changed is False
        stored.save.assert_not_called()

    def test_update_sets_uefi_when_the_vm_is_seabios(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        stored = make_resource({'$key': 1, 'name': 'zz-vm', 'uefi': False})
        stored.save.return_value = stored
        module = self._module(
            name='zz-vm', bios_type='uefi',
            machine_subtype='q35', network='Core',
        )

        changed, _row = vm.update_vm(module, MagicMock(), stored)

        assert changed is True
        sent = stored.save.call_args.kwargs
        assert sent == {'uefi': True}

    def test_update_clears_uefi_for_seabios(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        stored = make_resource({'$key': 1, 'name': 'zz-vm', 'uefi': True})
        stored.save.return_value = stored
        module = self._module(name='zz-vm', bios_type='seabios')

        changed, _row = vm.update_vm(module, MagicMock(), stored)

        assert changed is True
        assert stored.save.call_args.kwargs == {'uefi': False}


class TestAbsentRefusesRunningVm:
    """state=absent on a running VM must fail in check mode too (#127).

    Check mode used to report changed=true and "deleted". The real run
    then failed "Virtual Machine must be stopped to delete". A dry run
    of a teardown has to predict that refusal. A stopped VM in check
    mode is worded "would delete", never as though the delete happened.
    """

    REFUSAL = (
        "Virtual Machine must be stopped to delete. "
        "Set state=stopped on VM '%s' first."
    )

    def _run(self, client, name, check_mode):
        module = MagicMock()
        module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': name,
            'state': 'absent',
        }
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

    def _client(self, make_resource, **row):
        client = MagicMock()
        data = {'$key': 1, 'name': 'qa-probe-snap'}
        data.update(row)
        vm = make_resource(data)
        client.vms.list.return_value = [vm]
        client.vm_row = vm
        return client

    @pytest.mark.parametrize('check_mode', [False, True])
    @pytest.mark.parametrize('row', [
        {'status': 'running', 'running': True},
        {'status': 'running'},
        {'running': True},
        {'running': 1, 'status': 'stopped'},
    ])
    def test_running_vm_is_refused_in_both_modes(self, make_resource,
                                                 check_mode, row):
        client = self._client(make_resource, **row)
        module = self._run(client, 'qa-probe-snap', check_mode)

        client.vm_row.delete.assert_not_called()
        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        assert module.fail_json.call_args.kwargs['msg'] == (
            self.REFUSAL % 'qa-probe-snap')

    def test_check_and_real_mode_share_one_message(self, make_resource):
        real = self._run(
            self._client(make_resource, status='running', running=True),
            'qa-probe-snap', False)
        dry = self._run(
            self._client(make_resource, status='running', running=True),
            'qa-probe-snap', True)
        assert real.fail_json.call_args.kwargs['msg'] == (
            dry.fail_json.call_args.kwargs['msg'])

    def test_stopped_vm_check_mode_would_delete(self, make_resource):
        client = self._client(
            make_resource, status='stopped', running=False)
        module = self._run(client, 'qa-probe-snap', True)

        client.vm_row.delete.assert_not_called()
        module.fail_json.assert_not_called()
        result = module.exit_json.call_args.kwargs
        assert result['changed'] is True
        assert result['msg'] == "Would delete VM 'qa-probe-snap'"
        assert 'deleted' not in result['msg']

    def test_stopped_vm_is_deleted_for_real(self, make_resource):
        client = self._client(
            make_resource, status='stopped', running=False)
        module = self._run(client, 'qa-probe-snap', False)

        client.vm_row.delete.assert_called_once()
        module.fail_json.assert_not_called()
        result = module.exit_json.call_args.kwargs
        assert result['changed'] is True
        assert result['msg'] == "VM 'qa-probe-snap' deleted"
