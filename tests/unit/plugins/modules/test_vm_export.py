#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vm_export module"""

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
        'volume': 'backups',
        'state': 'present',
        'quiesced': True,
        'create_current': True,
        'max_exports': 3,
        'start': False,
        'export_name': None,
        'vms': None,
        'wait': True,
        'wait_timeout': 3600,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm_export.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm_export.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm_export.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    vm_export as vm_export_module,
                )
                try:
                    vm_export_module.main()
                except SystemExit:
                    pass


VOLUME_DATA = {'$key': 7, 'name': 'backups'}
EXPORT_ROW = {'$key': 2, 'volume': 7, 'quiesced': True,
              'create_current': True, 'max_exports': 3, 'status': 'idle'}


def make_client(export=None):
    client = MagicMock()
    client.nas_volumes.get.return_value = make_row(dict(VOLUME_DATA))
    if export is None:
        client.volume_vm_exports.get.side_effect = NotFoundError('nope')
    else:
        client.volume_vm_exports.get.return_value = export
    return client


class TestVmExportPresent:
    def test_fails_when_volume_missing(self):
        client = MagicMock()
        client.nas_volumes.get.side_effect = NotFoundError('nope')

        module = make_module(base_params())
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'not found' in module.fail_json.call_args[1]['msg']

    def test_creates_config_when_missing(self):
        client = make_client()
        client.volume_vm_exports.create.return_value = make_row(dict(EXPORT_ROW))

        module = make_module(base_params())
        run_main(module, client)

        client.volume_vm_exports.create.assert_called_once_with(
            volume=7, quiesced=True, create_current=True, max_exports=3)
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert "created export config on 'backups'" in result['actions']

    def test_idempotent_when_config_matches(self):
        client = make_client(export=make_row(dict(EXPORT_ROW)))

        module = make_module(base_params())
        run_main(module, client)

        client.volume_vm_exports.create.assert_not_called()
        client.volume_vm_exports.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_updates_drifted_max_exports(self):
        client = make_client(export=make_row(dict(EXPORT_ROW)))
        client.volume_vm_exports.update.return_value = make_row(dict(EXPORT_ROW))

        module = make_module(base_params(max_exports=5))
        run_main(module, client)

        client.volume_vm_exports.update.assert_called_once_with(
            2, max_exports=5)
        assert module.exit_json.call_args[1]['changed'] is True


class TestVmExportStart:
    def test_start_runs_and_waits_until_idle(self):
        client = MagicMock()
        client.nas_volumes.get.return_value = make_row(dict(VOLUME_DATA))

        building = dict(EXPORT_ROW)
        building['status'] = 'building'
        states = iter([make_row(building), make_row(dict(EXPORT_ROW))])

        def get_se(*args, **kwargs):
            if 'volume' in kwargs:
                return make_row(dict(EXPORT_ROW))
            return next(states)
        client.volume_vm_exports.get.side_effect = get_se

        module = make_module(base_params(start=True, export_name='nightly'))
        with patch('time.sleep'):
            run_main(module, client)

        client.volume_vm_exports.start_export.assert_called_once_with(
            2, name='nightly', vms=None)
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert 'export finished' in result['actions']

    def test_start_resolves_vm_names(self):
        client = make_client(export=make_row(dict(EXPORT_ROW)))
        client.vms.get.return_value = make_row({'$key': 41, 'name': 'app'})

        module = make_module(base_params(start=True, vms=['app'], wait=False))
        run_main(module, client)

        client.volume_vm_exports.start_export.assert_called_once_with(
            2, name=None, vms=[41])

    def test_error_status_fails(self):
        client = MagicMock()
        client.nas_volumes.get.return_value = make_row(dict(VOLUME_DATA))

        errored = dict(EXPORT_ROW)
        errored['status'] = 'error'

        def get_se(*args, **kwargs):
            if 'volume' in kwargs:
                return make_row(dict(EXPORT_ROW))
            return make_row(errored)
        client.volume_vm_exports.get.side_effect = get_se

        module = make_module(base_params(start=True))
        with patch('time.sleep'):
            run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'error' in module.fail_json.call_args[1]['msg']


class TestVmExportAbsent:
    def test_deletes_existing_config(self):
        client = make_client(export=make_row(dict(EXPORT_ROW)))

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.volume_vm_exports.delete.assert_called_once_with(2)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_absent_missing_no_change(self):
        client = make_client()

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.volume_vm_exports.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False
