#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vm module"""

import pytest
from unittest.mock import MagicMock, patch


# There is deliberately no fixture stubbing pyvergeos out of sys.modules here.
# There used to be, and it was the reason several tests in this file failed.
#
# Replacing 'pyvergeos.exceptions' with a MagicMock makes NotFoundError a Mock
# attribute rather than an exception class. Setting that as a side_effect does
# not raise -- unittest.mock treats a non-exception side_effect as a callable,
# calls it, and returns the result. So `client.vms.get` handed back a MagicMock
# instead of raising, dict() of a MagicMock is {}, and the module returned
# [{}] where the test expected []. The test looked like it was exercising the
# not-found path and was exercising nothing.
#
# pyvergeos is a declared requirement (requirements.txt), so it is present
# whenever these tests run and there is nothing to stub.


def as_row(mock, row):
    """Make a MagicMock decode as ``row`` under dict(), without giving up the
    mock.

    dict() prefers the mapping protocol, and MagicMock auto-provides keys(),
    so dict(MagicMock()) is {} -- an __iter__ override never gets a look in.
    These tests used to set __iter__ and every one of them was therefore
    comparing against an empty row: the module saw drift in every field, and
    the one test that asserted "nothing was saved" failed while the rest
    passed for the wrong reason.
    """
    mock.keys = lambda: row.keys()
    mock.__getitem__ = lambda self, key: row[key]
    return mock


class TestVmStatePresent:
    """Tests for vm module with state=present"""

    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_creates_vm_when_not_exists(self, mock_get_client):
        """Test that VM is created when it doesn't exist"""
        from pyvergeos.exceptions import NotFoundError

        # Setup mock client
        mock_client = MagicMock()
        mock_client.vms.get.side_effect = NotFoundError("VM not found")
        mock_new_vm = MagicMock()
        as_row(mock_new_vm, {
            '$key': 1, 'name': 'new-vm', 'cpu_cores': 4, 'ram': 8192
        })
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
            'boot_order': None
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
    def test_updates_vm_when_exists_with_changes(self, mock_get_client):
        """Test that VM is updated when it exists with different config"""
        # Setup mock client with existing VM
        mock_client = MagicMock()
        mock_vm = MagicMock()
        as_row(mock_vm, {
            '$key': 1, 'name': 'existing-vm', 'cpu_cores': 2, 'ram': 4096
        })
        mock_client.vms.get.return_value = mock_vm
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
            'boot_order': None
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

    # Patch targets are plugins.modules.vm.*, not module_utils.vergeos.*:
    # each module binds its own reference to get_vergeos_client at import,
    # so patching where the name is DEFINED silently misses once any other
    # test has already imported vm. (Retargeted on merge; main's copy of
    # this test patched the definition site.)
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.modules.vm.HAS_PYVERGEOS', True)
    def test_partial_update_sends_changes_and_preserves_enabled(self, mock_get_client):
        """Issues #80/#83: save() receives the changed fields; omitted enabled is not sent"""
        mock_client = MagicMock()
        mock_vm = MagicMock()
        # A plain dict, NOT an __iter__ override. MagicMock auto-provides
        # keys(), and dict() prefers the mapping protocol, so assigning
        # __iter__ is ignored and dict(mock) decodes as {} -- which made this
        # test's premise (a VM with enabled=False) never actually apply. It
        # passed for the wrong reason. See B16 and
        # tests/unit/test_fixture_discipline.py.
        vm_row = {'$key': 1, 'name': 'existing-vm',
                  'enabled': False, 'description': ''}
        mock_vm.keys.return_value = list(vm_row.keys())
        mock_vm.__getitem__.side_effect = lambda k: vm_row[k]
        mock_vm.save.return_value = {'$key': 1, 'name': 'existing-vm', 'enabled': False, 'description': 'x'}
        mock_client.vms.get.return_value = mock_vm
        mock_get_client.return_value = mock_client

        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com', 'username': 'admin', 'password': 'secret',
            'insecure': False, 'name': 'existing-vm', 'state': 'present',
            'description': 'x', 'enabled': None, 'os_family': None, 'cpu_cores': None,
            'ram': None, 'machine_type': None, 'machine_subtype': None,
            'bios_type': None, 'network': None, 'boot_order': None
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
    def test_no_change_when_vm_matches(self, mock_get_client):
        """Test that no change when VM already matches desired state"""
        # Setup mock client with VM that matches params
        mock_client = MagicMock()
        mock_vm = MagicMock()
        as_row(mock_vm, {
            '$key': 1, 'name': 'existing-vm', 'cpu_cores': 4, 'ram': 8192
        })
        mock_client.vms.get.return_value = mock_vm
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
            'boot_order': None
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
    def test_deletes_vm_when_exists(self, mock_get_client):
        """Test that VM is deleted when it exists"""
        # Setup mock client
        mock_client = MagicMock()
        mock_vm = MagicMock()
        as_row(mock_vm, {'$key': 1, 'name': 'delete-me'})
        mock_client.vms.get.return_value = mock_vm
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
            'boot_order': None
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
        mock_client.vms.get.side_effect = NotFoundError("VM not found")
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
            'boot_order': None
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
    def test_powers_on_stopped_vm(self, mock_get_client):
        """Test that stopped VM is powered on"""
        # Setup mock client with stopped VM
        mock_client = MagicMock()
        mock_vm = MagicMock()
        # A live row the mock reads through, so refresh() can change it. The
        # module polls up to 30 times with a 2 second sleep between reads; a
        # refresh() that never changes anything burns the full 60 seconds and
        # proves only that the loop can time out. Flipping the status on the
        # first refresh keeps the test instant AND actually exercises the wait.
        row = {'$key': 1, 'name': 'my-vm', 'status': 'stopped'}
        as_row(mock_vm, row)
        mock_vm.refresh.side_effect = lambda: row.update(status='running')
        mock_client.vms.get.return_value = mock_vm
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
            'boot_order': None
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
    def test_powers_off_running_vm(self, mock_get_client):
        """Test that running VM is powered off"""
        # Setup mock client with running VM
        mock_client = MagicMock()
        mock_vm = MagicMock()
        row = {'$key': 1, 'name': 'my-vm', 'status': 'running', 'running': True}
        as_row(mock_vm, row)
        mock_vm.refresh.side_effect = lambda: row.update(
            status='stopped', running=False)
        mock_client.vms.get.return_value = mock_vm
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
            'boot_order': None
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
        mock_client.vms.get.side_effect = NotFoundError("VM not found")
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
            'boot_order': None
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
    def test_check_mode_does_not_delete(self, mock_get_client):
        """Test that check_mode doesn't actually delete VM"""
        mock_client = MagicMock()
        mock_vm = MagicMock()
        as_row(mock_vm, {'$key': 1, 'name': 'delete-me'})
        mock_client.vms.get.return_value = mock_vm
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
            'boot_order': None
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
