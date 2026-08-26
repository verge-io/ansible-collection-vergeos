#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for nas_nfs_share module"""

import pytest
from unittest.mock import MagicMock, patch


class NotFoundError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class ValidationError(Exception):
    pass


class APIError(Exception):
    pass


class VergeConnectionError(Exception):
    pass


@pytest.fixture(autouse=True)
def mock_pyvergeos():
    exceptions = MagicMock()
    exceptions.NotFoundError = NotFoundError
    exceptions.AuthenticationError = AuthenticationError
    exceptions.ValidationError = ValidationError
    exceptions.APIError = APIError
    exceptions.VergeConnectionError = VergeConnectionError
    sdk = MagicMock()
    sdk.exceptions = exceptions
    with patch.dict('sys.modules', {
        'pyvergeos': sdk,
        'pyvergeos.exceptions': exceptions,
    }):
        yield


def make_row(data):
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    return obj


def make_module(params, check_mode=False):
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def base_params(**overrides):
    params = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'name': 'backups',
        'volume': 'backups',
        'state': 'present',
        'share_path': None,
        'description': None,
        'allowed_hosts': ['192.0.2.10'],
        'allow_all': False,
        'data_access': 'ro',
        'squash': 'root_squash',
        'async_mode': False,
        'insecure_ports': False,
        'no_acl': False,
        'enabled': True,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.nas_nfs_share.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    nas_nfs_share as share_module,
                )
                try:
                    share_module.main()
                except SystemExit:
                    pass


SHARE_ROW = {
    '$key': 'a' * 40, 'name': 'backups', 'volume_name': 'backups',
    'description': None, 'allowed_hosts': '192.0.2.10', 'allow_all': False,
    'data_access': 'ro', 'squash': 'root_squash', 'async_mode': False,
    'insecure': False, 'no_acl': False, 'enabled': True,
}


def make_client(shares=None):
    client = MagicMock()
    client.nas_volumes.get.return_value = make_row({'$key': 7, 'name': 'backups'})
    client.nfs_shares.list.return_value = shares or []
    return client


class TestSharePresent:
    def test_creates_when_missing(self):
        client = make_client()
        client.nfs_shares.create.return_value = make_row(dict(SHARE_ROW))

        module = make_module(base_params())
        run_main(module, client)

        kwargs = client.nfs_shares.create.call_args[1]
        assert kwargs['allowed_hosts'] == '192.0.2.10'
        assert kwargs['data_access'] == 'ro'
        assert module.exit_json.call_args[1]['changed'] is True

    def test_requires_hosts_or_allow_all(self):
        client = make_client()

        module = make_module(base_params(allowed_hosts=None))
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'allowed_hosts' in module.fail_json.call_args[1]['msg']

    def test_fails_when_volume_missing(self):
        client = MagicMock()
        client.nas_volumes.get.side_effect = NotFoundError('nope')

        module = make_module(base_params())
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'not found' in module.fail_json.call_args[1]['msg']

    def test_idempotent_when_matching(self):
        client = make_client(shares=[make_row(dict(SHARE_ROW))])

        module = make_module(base_params())
        run_main(module, client)

        client.nfs_shares.create.assert_not_called()
        client.nfs_shares.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_host_list_order_is_not_drift(self):
        row = dict(SHARE_ROW)
        row['allowed_hosts'] = '10.0.0.1, 192.0.2.10'
        client = make_client(shares=[make_row(row)])

        module = make_module(base_params(
            allowed_hosts=['192.0.2.10', '10.0.0.1']))
        run_main(module, client)

        client.nfs_shares.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_updates_drifted_access(self):
        client = make_client(shares=[make_row(dict(SHARE_ROW))])
        client.nfs_shares.update.return_value = make_row(dict(SHARE_ROW))

        module = make_module(base_params(data_access='rw'))
        run_main(module, client)

        client.nfs_shares.update.assert_called_once_with(
            'a' * 40, data_access='rw')
        assert module.exit_json.call_args[1]['changed'] is True


class TestShareAbsent:
    def test_deletes_existing(self):
        client = make_client(shares=[make_row(dict(SHARE_ROW))])

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.nfs_shares.delete.assert_called_once_with('a' * 40)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_absent_missing_no_change(self):
        client = make_client()

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.nfs_shares.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False
