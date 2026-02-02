#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vm_info module"""

import pytest
from unittest.mock import MagicMock, patch


# Mock the pyvergeos module before importing the module under test
@pytest.fixture(autouse=True)
def mock_pyvergeos():
    """Mock pyvergeos SDK for all tests"""
    with patch.dict('sys.modules', {
        'pyvergeos': MagicMock(),
        'pyvergeos.exceptions': MagicMock(),
    }):
        yield


class TestVmInfo:
    """Tests for vm_info module"""

    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True)
    def test_returns_all_vms_when_no_name_specified(self, mock_get_client):
        """Test that all VMs are returned when name is not specified"""
        # Setup mock client
        mock_client = MagicMock()
        mock_vm1 = MagicMock()
        mock_vm1.__iter__ = lambda self: iter({'$key': 1, 'name': 'vm1', 'cpu_cores': 2}.items())
        mock_vm2 = MagicMock()
        mock_vm2.__iter__ = lambda self: iter({'$key': 2, 'name': 'vm2', 'cpu_cores': 4}.items())
        mock_client.vms.list.return_value = [mock_vm1, mock_vm2]
        mock_get_client.return_value = mock_client

        # Create mock module
        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': None
        }
        mock_module.check_mode = False

        # Import and run module main function
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm_info.AnsibleModule', return_value=mock_module):
            from ansible_collections.vergeio.vergeos.plugins.modules import vm_info
            # Reset the module's exit_json calls
            mock_module.reset_mock()

            try:
                vm_info.main()
            except SystemExit:
                pass

        # Verify results
        mock_client.vms.list.assert_called_once()
        mock_module.exit_json.assert_called_once()
        call_kwargs = mock_module.exit_json.call_args[1]
        assert call_kwargs['changed'] is False
        assert 'vms' in call_kwargs

    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True)
    def test_returns_specific_vm_when_name_specified(self, mock_get_client):
        """Test that specific VM is returned when name is specified"""
        # Setup mock client
        mock_client = MagicMock()
        mock_vm = MagicMock()
        mock_vm.__iter__ = lambda self: iter({'$key': 1, 'name': 'web-server', 'cpu_cores': 4}.items())
        mock_client.vms.get.return_value = mock_vm
        mock_get_client.return_value = mock_client

        # Create mock module
        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'name': 'web-server'
        }
        mock_module.check_mode = False

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm_info.AnsibleModule', return_value=mock_module):
            from ansible_collections.vergeio.vergeos.plugins.modules import vm_info
            mock_module.reset_mock()

            try:
                vm_info.main()
            except SystemExit:
                pass

        mock_client.vms.get.assert_called_once_with(name='web-server')
        mock_module.exit_json.assert_called_once()
        call_kwargs = mock_module.exit_json.call_args[1]
        assert call_kwargs['changed'] is False

    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.get_vergeos_client')
    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True)
    def test_returns_empty_list_when_vm_not_found(self, mock_get_client):
        """Test that empty list is returned when VM is not found"""
        from pyvergeos.exceptions import NotFoundError

        # Setup mock client to raise NotFoundError
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
            'name': 'nonexistent-vm'
        }
        mock_module.check_mode = False

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm_info.AnsibleModule', return_value=mock_module):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm_info.NotFoundError', NotFoundError):
                from ansible_collections.vergeio.vergeos.plugins.modules import vm_info
                mock_module.reset_mock()

                try:
                    vm_info.main()
                except SystemExit:
                    pass

        mock_module.exit_json.assert_called_once()
        call_kwargs = mock_module.exit_json.call_args[1]
        assert call_kwargs['changed'] is False
        assert call_kwargs['vms'] == []

    def test_supports_check_mode(self):
        """Test that module supports check_mode"""
        # Import module and check argument spec
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm_info.AnsibleModule') as mock_ansible:
            mock_ansible.return_value = MagicMock()
            mock_ansible.return_value.params = {
                'host': 'test', 'username': 'test', 'password': 'test',
                'insecure': False, 'name': None
            }

            with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.get_vergeos_client'):
                with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True):
                    from ansible_collections.vergeio.vergeos.plugins.modules import vm_info
                    try:
                        vm_info.main()
                    except (SystemExit, Exception):
                        pass

            # Verify supports_check_mode was passed
            call_kwargs = mock_ansible.call_args[1]
            assert call_kwargs.get('supports_check_mode') is True
