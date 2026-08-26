#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vm_clone module"""

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
        'name': 'app-drill',
        'source': 'app',
        'snapshot': None,
        'preserve_macs': False,
        'wait': True,
        'wait_timeout': 300,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.vm_clone.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    vm_clone as vm_clone_module,
                )
                try:
                    vm_clone_module.main()
                except SystemExit:
                    pass


def make_source(snapshots):
    source = make_row({'$key': 41, 'name': 'app'})
    source.snapshots.list.return_value = snapshots
    return source


def snap(key, name, created):
    return make_row({'$key': key, 'name': name, 'created': created})


def make_client(source=None, clone_visible=None, snap_vm=None):
    """clone_visible: row returned for the clone name after the clone.
    snap_vm: mock VM row for the snapshot's own vms entry (key 24)."""
    client = MagicMock()
    calls = {'n': 0}

    def get_se(*args, **kwargs):
        if kwargs.get('name') == 'app':
            if source is None:
                raise NotFoundError('nope')
            return source
        if args and args[0] == 24:
            return snap_vm
        # clone name lookups: first (idempotence) always missing, later
        # (wait loop) return clone_visible when configured
        calls['n'] += 1
        if calls['n'] > 1 and clone_visible is not None:
            return clone_visible
        raise NotFoundError('nope')
    client.vms.get.side_effect = get_se

    def request_se(method, path, params=None, **kwargs):
        if path.startswith('machine_snapshots/'):
            return {'$key': path.split('/')[1], 'snap_machine': 77}
        if path == 'vms':
            return [{'$key': 24, 'name': 'snap_x'}]
        return None
    client._request.side_effect = request_se
    return client


class TestVmClone:
    def test_clones_from_named_snapshot(self):
        source = make_source([snap(9, 'nightly', 100), snap(10, 'weekly', 50)])
        snap_vm = MagicMock()
        client = make_client(source=source, snap_vm=snap_vm,
                             clone_visible=make_row({'$key': 51, 'name': 'app-drill'}))

        module = make_module(base_params(snapshot='weekly'))
        with patch('time.sleep'):
            run_main(module, client)

        # the machine_snapshots row queried is the chosen snapshot (key 10)
        assert client._request.call_args_list[0][0][1] == 'machine_snapshots/10'
        snap_vm.clone.assert_called_once_with(
            name='app-drill', preserve_macs=False)
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert result['vm']['snapshot'] == 'weekly'

    def test_uses_newest_snapshot_when_omitted(self):
        source = make_source([snap(9, 'old', 100), snap(10, 'new', 200)])
        snap_vm = MagicMock()
        client = make_client(source=source, snap_vm=snap_vm,
                             clone_visible=make_row({'$key': 51, 'name': 'app-drill'}))

        module = make_module(base_params())
        with patch('time.sleep'):
            run_main(module, client)

        assert client._request.call_args_list[0][0][1] == 'machine_snapshots/10'
        snap_vm.clone.assert_called_once()

    def test_idempotent_when_clone_exists(self):
        client = MagicMock()
        client.vms.get.return_value = make_row({'$key': 51, 'name': 'app-drill'})

        module = make_module(base_params())
        run_main(module, client)

        assert module.exit_json.call_args[1]['changed'] is False

    def test_fails_when_source_missing(self):
        client = make_client(source=None)

        module = make_module(base_params())
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'not found' in module.fail_json.call_args[1]['msg']

    def test_fails_when_no_snapshots(self):
        source = make_source([])
        client = make_client(source=source)

        module = make_module(base_params())
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'no snapshots' in module.fail_json.call_args[1]['msg']

    def test_fails_when_named_snapshot_missing(self):
        source = make_source([snap(9, 'nightly', 100)])
        client = make_client(source=source)

        module = make_module(base_params(snapshot='ghost'))
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert "Snapshot 'ghost'" in module.fail_json.call_args[1]['msg']

    def test_check_mode_restores_nothing(self):
        source = make_source([snap(9, 'nightly', 100)])
        client = make_client(source=source)

        module = make_module(base_params(), check_mode=True)
        run_main(module, client)

        source.restore.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True
