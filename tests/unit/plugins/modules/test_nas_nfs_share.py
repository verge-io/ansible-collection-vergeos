#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the nas_nfs_share module."""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock, patch


from pyvergeos.exceptions import (  # real classes: a Mock here is
    APIError,                       # CALLED as a side_effect, not raised
    AuthenticationError,
    ValidationError,
    VergeConnectionError,
)


_UNSET = object()
VOLUME_KEY = 'c3437883534918dcf2abbb3e9b9622b865226c68'
SHARE_KEY = '197926550255fe1c9d57d6f22698995625f7f009'
SHARE_ROW = {
    '$key': SHARE_KEY, 'name': 'backups', 'volume': VOLUME_KEY,
    'description': '', 'allowed_hosts': '192.0.2.10', 'allow_all': False,
    'data_access': 'ro', 'squash': 'root_squash', 'async': False,
    'insecure': False, 'no_acl': False, 'enabled': True,
}


def make_row(data):
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    obj.__iter__.side_effect = lambda: iter(data)
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
        'volume': 'backups-vol',
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


def make_client(share_row=_UNSET, volume_rows=None):
    if share_row is _UNSET:
        share_row = SHARE_ROW
    client = MagicMock()
    client.nas_volumes.list.return_value = volume_rows if volume_rows is not None \
        else [make_row({'$key': VOLUME_KEY, 'name': 'backups-vol'})]
    client.nfs_shares.list.return_value = (
        [make_row(dict(share_row))] if share_row else [])
    return client


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.nas_nfs_share.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.nas_nfs_share.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.nas_nfs_share.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    nas_nfs_share as share_module,
                )
                try:
                    share_module.main()
                except SystemExit:
                    pass


class TestSharePresent:
    def test_creates_when_missing(self):
        client = make_client(share_row=None)
        client.nfs_shares.create.return_value = make_row(dict(SHARE_ROW))

        module = make_module(base_params())
        run_main(module, client)

        created = client.nfs_shares.create.call_args[1]
        assert created['name'] == 'backups'
        assert created['volume'] == VOLUME_KEY
        assert created['allowed_hosts'] == '192.0.2.10'
        assert module.exit_json.call_args[1]['changed'] is True

    def test_create_uses_the_volume_key_not_its_name(self):
        """`volume` is a real column holding the key. Two volumes may share a
        name (#72), and the share would land on whichever the API picked."""
        client = make_client(share_row=None)
        client.nfs_shares.create.return_value = make_row(dict(SHARE_ROW))

        run_main(make_module(base_params()), client)

        assert client.nfs_shares.create.call_args[1]['volume'] == VOLUME_KEY

    def test_missing_volume_fails(self):
        client = make_client(share_row=None, volume_rows=[])

        module = make_module(base_params())
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'not found' in module.fail_json.call_args[1]['msg']

    def test_hosts_are_required_unless_allow_all(self):
        client = make_client(share_row=None)

        module = make_module(base_params(allowed_hosts=None))
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'allowed_hosts is required' in \
            module.fail_json.call_args[1]['msg']

    def test_idempotent_when_matching(self):
        client = make_client()

        module = make_module(base_params())
        run_main(module, client)

        client.nfs_shares.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_corrects_drifted_data_access(self):
        client = make_client()

        module = make_module(base_params(data_access='rw'))
        run_main(module, client)

        client.nfs_shares.update.assert_called_once_with(
            SHARE_KEY, data_access='rw')

    def test_async_is_read_from_the_async_column(self):
        """The bug this module shipped with. The column is `async`; the option
        and the SDK keyword are both `async_mode`. Reading `async_mode` off a
        share row always gave None, so a share with async on reported drift on
        every run and PUT the same value forever."""
        client = make_client(share_row=dict(SHARE_ROW, **{'async': True}))

        module = make_module(base_params(async_mode=True))
        run_main(module, client)

        client.nfs_shares.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_async_drift_is_written_with_the_sdk_keyword(self):
        client = make_client()

        module = make_module(base_params(async_mode=True))
        run_main(module, client)

        client.nfs_shares.update.assert_called_once_with(
            SHARE_KEY, async_mode=True)

    def test_insecure_ports_is_read_from_the_insecure_column(self):
        client = make_client(share_row=dict(SHARE_ROW, insecure=True))

        module = make_module(base_params(insecure_ports=True))
        run_main(module, client)

        client.nfs_shares.update.assert_not_called()

    def test_insecure_drift_is_written_with_the_sdk_keyword(self):
        client = make_client()

        module = make_module(base_params(insecure_ports=True))
        run_main(module, client)

        client.nfs_shares.update.assert_called_once_with(
            SHARE_KEY, insecure=True)

    def test_host_lists_compare_as_sets(self):
        """Order and whitespace are not drift; the API stores one string."""
        client = make_client(
            share_row=dict(SHARE_ROW, allowed_hosts='192.0.2.11,192.0.2.10'))

        module = make_module(
            base_params(allowed_hosts=['192.0.2.10', '192.0.2.11']))
        run_main(module, client)

        client.nfs_shares.update.assert_not_called()

    def test_changed_host_list_is_sent_as_a_comma_string(self):
        client = make_client()

        module = make_module(base_params(allowed_hosts=['192.0.2.99']))
        run_main(module, client)

        client.nfs_shares.update.assert_called_once_with(
            SHARE_KEY, allowed_hosts='192.0.2.99')

    def test_check_mode_writes_nothing(self):
        client = make_client()

        module = make_module(base_params(data_access='rw'), check_mode=True)
        run_main(module, client)

        client.nfs_shares.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True


class TestShareAbsent:
    def test_deletes_when_present(self):
        client = make_client()

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.nfs_shares.delete.assert_called_once_with(SHARE_KEY)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_absent_missing_no_change(self):
        client = make_client(share_row=None)

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.nfs_shares.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_check_mode_deletes_nothing(self):
        client = make_client()

        module = make_module(base_params(state='absent'), check_mode=True)
        run_main(module, client)

        client.nfs_shares.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True


class TestDuplicateVolumeNamesAreRefused:
    def test_two_volumes_with_one_name(self):
        client = make_client(share_row=None, volume_rows=[
            make_row({'$key': 'aa', 'name': 'backups-vol'}),
            make_row({'$key': 'bb', 'name': 'backups-vol'}),
        ])

        module = make_module(base_params())
        run_main(module, client)

        assert 'refusing to guess' in module.fail_json.call_args[1]['msg']


class TestSdkErrorsAreHandled:
    @pytest.mark.parametrize('exc', [
        APIError, AuthenticationError, ValidationError, VergeConnectionError,
    ])
    def test_sdk_errors_do_not_fall_through_to_unexpected(self, exc):
        client = make_client()
        client.nas_volumes.list.side_effect = exc('server said no')
        module = make_module(base_params())

        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'Unexpected error' not in module.fail_json.call_args[1]['msg']
