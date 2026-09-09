#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for module_utils/vergeos.py"""

import pytest
from unittest.mock import MagicMock, patch


class TestGetVergeosClient:
    """Tests for get_vergeos_client() factory function"""

    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True)
    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.VergeClient')
    def test_creates_client_with_correct_params(self, mock_client_class):
        """Test that client is created with correct parameters from module"""
        from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import get_vergeos_client

        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False
        }

        get_vergeos_client(mock_module)

        mock_client_class.assert_called_once_with(
            host='vergeos.example.com',
            username='admin',
            password='secret',
            verify_ssl=True
        )

    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True)
    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.VergeClient')
    def test_strips_https_prefix(self, mock_client_class):
        """Test that https:// prefix is stripped from host"""
        from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import get_vergeos_client

        mock_module = MagicMock()
        mock_module.params = {
            'host': 'https://vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False
        }

        get_vergeos_client(mock_module)

        mock_client_class.assert_called_once_with(
            host='vergeos.example.com',
            username='admin',
            password='secret',
            verify_ssl=True
        )

    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True)
    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.VergeClient')
    def test_strips_http_prefix(self, mock_client_class):
        """Test that http:// prefix is stripped from host"""
        from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import get_vergeos_client

        mock_module = MagicMock()
        mock_module.params = {
            'host': 'http://vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False
        }

        get_vergeos_client(mock_module)

        mock_client_class.assert_called_once_with(
            host='vergeos.example.com',
            username='admin',
            password='secret',
            verify_ssl=True
        )

    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True)
    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.VergeClient')
    def test_insecure_disables_ssl_verification(self, mock_client_class):
        """Test that insecure=True sets verify_ssl=False"""
        from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import get_vergeos_client

        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': True
        }

        get_vergeos_client(mock_module)

        mock_client_class.assert_called_once_with(
            host='vergeos.example.com',
            username='admin',
            password='secret',
            verify_ssl=False
        )

    @patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', False)
    def test_fails_when_sdk_not_installed(self):
        """The SDK-missing guard fires and explains itself.

        HAS_PYVERGEOS has to be forced False. pyvergeos is a declared
        requirement, so it IS installed when these tests run -- without the
        patch the guard is skipped, get_vergeos_client goes on to build a real
        client, and the test spends six seconds failing to resolve
        vergeos.example.com before erroring on a connection problem instead of
        checking the guard.

        fail_json is mocked, so it returns instead of raising SystemExit the
        way the real one does. Execution therefore continues past it, which is
        precisely how this test used to reach the network at all.
        """
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            vergeos as vergeos_utils,
        )

        mock_module = MagicMock()
        mock_module.params = {
            'host': 'vergeos.example.com',
            'username': 'admin',
            'password': 'secret',
            'insecure': False
        }
        mock_module.fail_json.side_effect = SystemExit(1)

        with patch.object(vergeos_utils, 'HAS_PYVERGEOS', False):
            with pytest.raises(SystemExit):
                vergeos_utils.get_vergeos_client(mock_module)

        mock_module.fail_json.assert_called_once()
        assert 'pyvergeos' in mock_module.fail_json.call_args[1]['msg']


class TestSdkErrorHandler:
    """Tests for sdk_error_handler() function"""

    def test_handles_not_found_error(self):
        """Test handling of NotFoundError"""
        from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import sdk_error_handler

        mock_module = MagicMock()

        # Create a mock exception that passes isinstance check
        with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.NotFoundError', Exception):
            sdk_error_handler(mock_module, Exception("VM not found"))

        mock_module.fail_json.assert_called_once()
        assert 'not found' in mock_module.fail_json.call_args[1]['msg'].lower() or \
               'error' in mock_module.fail_json.call_args[1]['msg'].lower()

    def test_handles_authentication_error(self):
        """Test handling of AuthenticationError"""
        from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import sdk_error_handler

        mock_module = MagicMock()

        with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.AuthenticationError', Exception):
            sdk_error_handler(mock_module, Exception("Invalid credentials"))

        mock_module.fail_json.assert_called_once()


class TestVergeosArgumentSpec:
    """Tests for vergeos_argument_spec() function"""

    def test_returns_required_fields(self):
        """Test that argument spec includes required auth fields"""
        from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import vergeos_argument_spec

        spec = vergeos_argument_spec()

        assert 'host' in spec
        assert 'username' in spec
        assert 'password' in spec
        assert 'insecure' in spec

    def test_password_has_no_log(self):
        """Test that password field has no_log=True"""
        from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import vergeos_argument_spec

        spec = vergeos_argument_spec()

        assert spec['password'].get('no_log') is True

    def test_insecure_defaults_to_false(self):
        """Test that insecure defaults to False"""
        from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import vergeos_argument_spec

        spec = vergeos_argument_spec()

        assert spec['insecure'].get('default') is False
