#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for nas_volume module"""

import pytest
from unittest.mock import MagicMock, patch


from pyvergeos.exceptions import (  # real classes: a Mock here is
    APIError,                       # CALLED as a side_effect, not raised
    AuthenticationError,
    NotFoundError,
    ValidationError,
    VergeConnectionError,
)


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
        'state': 'present',
        'service': 'nas1',
        'size_gb': 100,
        'tier': None,
        'description': None,
        'read_only': False,
        'enabled': True,
        'snapshot_profile': None,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.nas_volume.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.nas_volume.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.nas_volume.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    nas_volume as nas_volume_module,
                )
                try:
                    nas_volume_module.main()
                except SystemExit:
                    pass


GB = 1073741824
VOLUME_ROW = {'$key': 7, 'name': 'backups', 'description': None,
              'maxsize': 100 * GB, 'read_only': False, 'enabled': True}


class TestNasVolumePresent:
    def test_creates_when_missing(self):
        client = MagicMock()
        client.nas_volumes.get.side_effect = NotFoundError('nope')
        client.nas_volumes.create.return_value = make_row(dict(VOLUME_ROW))

        module = make_module(base_params())
        run_main(module, client)

        client.nas_volumes.create.assert_called_once_with(
            name='backups', service='nas1', size_gb=100,
            read_only=False, enabled=True)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_create_requires_service_and_size(self):
        client = MagicMock()
        client.nas_volumes.get.side_effect = NotFoundError('nope')

        module = make_module(base_params(service=None))
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'required' in module.fail_json.call_args[1]['msg']

    def test_idempotent_when_matching(self):
        client = MagicMock()
        client.nas_volumes.get.return_value = make_row(dict(VOLUME_ROW))

        module = make_module(base_params())
        run_main(module, client)

        client.nas_volumes.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_grows_drifted_size(self):
        client = MagicMock()
        client.nas_volumes.get.return_value = make_row(dict(VOLUME_ROW))

        module = make_module(base_params(size_gb=200))
        run_main(module, client)

        client.nas_volumes.update.assert_called_once_with(
            7, maxsize=200 * GB)
        assert module.exit_json.call_args[1]['changed'] is True


class TestNasVolumeAbsent:
    def test_disables_before_delete(self):
        client = MagicMock()
        client.nas_volumes.get.return_value = make_row(dict(VOLUME_ROW))

        module = make_module(base_params(state='absent'))
        with patch('time.sleep'):
            run_main(module, client)

        client.nas_volumes.update.assert_called_once_with(7, enabled=False)
        client.nas_volumes.delete.assert_called_once_with(7)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_already_disabled_deletes_directly(self):
        row = dict(VOLUME_ROW)
        row['enabled'] = False
        client = MagicMock()
        client.nas_volumes.get.return_value = make_row(row)

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.nas_volumes.update.assert_not_called()
        client.nas_volumes.delete.assert_called_once_with(7)

    def test_absent_missing_no_change(self):
        client = MagicMock()
        client.nas_volumes.get.side_effect = NotFoundError('nope')

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.nas_volumes.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_check_mode_deletes_nothing(self):
        client = MagicMock()
        client.nas_volumes.get.return_value = make_row(dict(VOLUME_ROW))

        module = make_module(base_params(state='absent'), check_mode=True)
        run_main(module, client)

        client.nas_volumes.update.assert_not_called()
        client.nas_volumes.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True
