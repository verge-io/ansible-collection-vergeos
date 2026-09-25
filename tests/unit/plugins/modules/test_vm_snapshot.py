#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vm_snapshot (#119 expiry, #128 restore).

Create used to omit expires / treat expiration=0 as "use the SDK 24h
default", so snapshots documented as never-expiring vanished the next day.
Restore called snapshot.restore() (clone path / wrong key, pyVergeOS#147)
and turned every NotFound into a false "Snapshot not found".
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import time
from unittest.mock import MagicMock, patch, call

import pytest

from pyvergeos.exceptions import NotFoundError


def make_row(data):
    obj = MagicMock()
    obj.keys.side_effect = lambda: list(data.keys())
    obj.__getitem__.side_effect = data.__getitem__
    obj.__iter__.side_effect = lambda: iter(data)
    for key, value in data.items():
        setattr(obj, key, value)
    if '$key' in data:
        obj.key = data['$key']
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
        'vm_name': 'web',
        'vm_id': None,
        'snapshot_name': None,
        'snapshot_id': None,
        'description': None,
        'expiration': None,
        'operation': 'create',
        'poll_interval': 5,
        'poll_timeout': 600,
        'state': None,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'vm_snapshot.get_vergeos_client',
               return_value=mock_client), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'vm_snapshot.HAS_PYVERGEOS', True), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'vm_snapshot.AnsibleModule', return_value=mock_module):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            vm_snapshot as mod,
        )
        try:
            mod.main()
        except SystemExit:
            pass


def make_vm(key=42, machine=7, running=False, snapshots=None):
    vm = make_row({
        '$key': key,
        'name': 'web',
        'machine': machine,
        'running': running,
        'status': 'running' if running else 'stopped',
    })
    vm.is_running = running
    mgr = MagicMock()
    mgr.machine_key = machine
    mgr.list.return_value = snapshots or []
    mgr.get.return_value = make_row({'$key': 10, 'name': 'snap', 'snap_machine': 99})
    mgr.restore.return_value = {'$key': key}
    mgr.create.side_effect = AssertionError(
        'create must POST expires itself (#146); do not call SDK create'
    )
    vm.snapshots = mgr
    return vm


def make_client(vm=None):
    client = MagicMock()
    if vm is None:
        vm = make_vm()
    client.vms.get.return_value = vm
    client.vms.list.return_value = [vm]
    client._request.return_value = {'$key': 10, 'name': 'snap', 'expires': 0}
    return client, vm


# ── #119 expiry ──────────────────────────────────────────────────────────────


def test_create_omitted_expiration_posts_expires_zero():
    module = make_module(base_params(
        snapshot_name='golden', operation='create',
    ))
    client, vm = make_client()

    run_main(module, client)

    module.fail_json.assert_not_called()
    module.exit_json.assert_called_once()
    kwargs = module.exit_json.call_args.kwargs
    assert kwargs['changed'] is True
    assert kwargs['expires'] == 0
    assert kwargs['snapshot_id'] == '10'

    client._request.assert_called_once()
    args, kw = client._request.call_args
    assert args[0] == 'POST'
    assert args[1] == 'machine_snapshots'
    body = kw['json_data']
    assert body['expires'] == 0
    assert body['name'] == 'golden'
    assert body['machine'] == 7
    assert 'description' not in body


def test_create_expiration_zero_also_means_never():
    module = make_module(base_params(
        snapshot_name='keep', expiration=0, operation='create',
    ))
    client, _vm = make_client()

    run_main(module, client)

    body = client._request.call_args.kwargs['json_data']
    assert body['expires'] == 0
    assert module.exit_json.call_args.kwargs['expires'] == 0


def test_create_future_expiration_posts_absolute_epoch():
    future = int(time.time()) + 3600
    module = make_module(base_params(
        snapshot_name='temp', expiration=future, operation='create',
        description='qa description text',
    ))
    client, _vm = make_client()

    run_main(module, client)

    body = client._request.call_args.kwargs['json_data']
    assert body['expires'] == future
    assert body['description'] == 'qa description text'


def test_create_past_expiration_fails_instead_of_one_hour_fallback():
    past = int(time.time()) - 60
    module = make_module(base_params(
        snapshot_name='stale', expiration=past, operation='create',
    ))
    client, _vm = make_client()

    run_main(module, client)

    module.exit_json.assert_not_called()
    module.fail_json.assert_called_once()
    msg = module.fail_json.call_args.kwargs['msg']
    assert 'future Unix epoch' in msg
    client._request.assert_not_called()


def test_create_forwards_description():
    module = make_module(base_params(
        snapshot_name='with-desc', operation='create',
        description='before upgrade',
    ))
    client, _vm = make_client()

    run_main(module, client)

    body = client._request.call_args.kwargs['json_data']
    assert body['description'] == 'before upgrade'


def test_create_converges_when_name_already_exists():
    existing = make_row({'$key': 55, 'name': 'golden', 'expires': 0})
    module = make_module(base_params(
        snapshot_name='golden', operation='create',
    ))
    client, vm = make_client(vm=make_vm(snapshots=[existing]))

    run_main(module, client)

    kwargs = module.exit_json.call_args.kwargs
    assert kwargs['changed'] is False
    assert kwargs['snapshot_id'] == '55'
    client._request.assert_not_called()


# ── #128 restore ─────────────────────────────────────────────────────────────


def test_restore_uses_manager_replace_original():
    module = make_module(base_params(
        operation='restore', snapshot_id='10', vm_name='web',
    ))
    client, vm = make_client(vm=make_vm(running=False))

    run_main(module, client)

    module.fail_json.assert_not_called()
    vm.snapshots.get.assert_called_once_with(key=10)
    vm.snapshots.restore.assert_called_once_with(10, replace_original=True)
    kwargs = module.exit_json.call_args.kwargs
    assert kwargs['changed'] is True
    assert kwargs['operation'] == 'restore'
    assert kwargs['snapshot_id'] == '10'


def test_restore_refuses_running_vm_even_in_check_mode():
    module = make_module(base_params(
        operation='restore', snapshot_id='10', vm_name='web',
    ), check_mode=True)
    client, vm = make_client(vm=make_vm(running=True))

    run_main(module, client)

    module.exit_json.assert_not_called()
    module.fail_json.assert_called_once()
    msg = module.fail_json.call_args.kwargs['msg']
    assert 'powered off' in msg
    vm.snapshots.restore.assert_not_called()


def test_restore_reports_missing_snapshot_from_get_only():
    module = make_module(base_params(
        operation='restore', snapshot_id='10', vm_name='web',
    ))
    client, vm = make_client(vm=make_vm(running=False))
    vm.snapshots.get.side_effect = NotFoundError('Snapshot 10 not found')

    run_main(module, client)

    module.fail_json.assert_called_once()
    assert module.fail_json.call_args.kwargs['msg'] == "Snapshot '10' not found"
    vm.snapshots.restore.assert_not_called()


def test_restore_does_not_relabel_restore_failures_as_missing_snapshot():
    module = make_module(base_params(
        operation='restore', snapshot_id='10', vm_name='web',
    ))
    client, vm = make_client(vm=make_vm(running=False))
    # get succeeds; restore raises ValueError (e.g. platform refusal)
    vm.snapshots.restore.side_effect = ValueError('VM must be powered off for in-place restore')

    run_main(module, client)

    module.fail_json.assert_called_once()
    msg = module.fail_json.call_args.kwargs['msg']
    assert 'powered off' in msg
    assert 'not found' not in msg.lower()


def test_restore_check_mode_stopped_vm_reports_changed():
    module = make_module(base_params(
        operation='restore', snapshot_id='10', vm_name='web',
    ), check_mode=True)
    client, vm = make_client(vm=make_vm(running=False))

    run_main(module, client)

    module.fail_json.assert_not_called()
    kwargs = module.exit_json.call_args.kwargs
    assert kwargs['changed'] is True
    assert 'Would restore' in kwargs['msg']
    vm.snapshots.restore.assert_not_called()
