#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vm_import idempotence (#129).

A second ``state: present`` used to POST another import and fail with
"This name is already in use". Check mode said it would create a VM
that already existed. The module now resolves the name first and
converges, the same way ``vm_snapshot`` does.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from unittest.mock import MagicMock, patch


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
        'ova_file_id': '41',
        'ova_file_name': None,
        'file_id': None,
        'name': 'qa-probe-import',
        'preserve_macs': False,
        'preserve_drive_format': False,
        'preferred_tier': None,
        'no_optical_drives': False,
        'override_drive_interface': 'default',
        'override_nic_interface': 'default',
        'poll_interval': 5,
        'poll_timeout': 600,
        'state': 'present',
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    base = 'ansible_collections.vergeio.vergeos.plugins.modules.vm_import'
    with patch('%s.get_vergeos_client' % base, return_value=mock_client), \
         patch('%s.HAS_PYVERGEOS' % base, True), \
         patch('%s.AnsibleModule' % base, return_value=mock_module):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            vm_import as vm_import_module,
        )
        try:
            vm_import_module.main()
        except SystemExit:
            pass


def _result(module):
    assert module.exit_json.called, (
        "expected exit_json, got fail_json: %s" % (module.fail_json.call_args,))
    return module.exit_json.call_args.kwargs


class TestVmImportIdempotence:
    def test_second_run_reports_unchanged(self):
        """The regression: a VM of this name already exists."""
        client = MagicMock()
        client.vms.list.return_value = [
            make_row({'$key': 57, 'name': 'qa-probe-import'}),
        ]
        module = make_module(base_params(), check_mode=False)

        run_main(module, client)

        result = _result(module)
        assert result['changed'] is False
        assert result['vm_id'] == '57'
        assert result['msg'] == "VM 'qa-probe-import' already exists"
        client._request.assert_not_called()
        client.files.list.assert_not_called()

    def test_check_mode_existing_vm_does_not_say_would_create(self):
        client = MagicMock()
        client.vms.list.return_value = [
            make_row({'$key': 57, 'name': 'qa-probe-import'}),
        ]
        module = make_module(base_params(), check_mode=True)

        run_main(module, client)

        result = _result(module)
        assert result['changed'] is False
        assert result['vm_id'] == '57'
        assert result['msg'] == "VM 'qa-probe-import' already exists"
        assert 'Would create' not in result['msg']
        client._request.assert_not_called()

    def test_first_run_still_posts_then_second_run_does_not(self):
        client = MagicMock()
        client.vms.list.return_value = []
        client._request.return_value = {
            '$key': 'imp-1',
            'response': {'$key': 57},
        }
        first = make_module(base_params(), check_mode=False)
        base = 'ansible_collections.vergeio.vergeos.plugins.modules.vm_import'
        with patch('%s.wait_for_import_completion' % base,
                   return_value={'status': 'complete',
                                 'vm': {'status': 'stopped', '$key': 57}}):
            run_main(first, client)

        created = _result(first)
        assert created['changed'] is True
        assert created['vm_id'] == '57'
        client._request.assert_called_once()
        assert client._request.call_args.args[0] == 'POST'

        client.vms.list.return_value = [
            make_row({'$key': 57, 'name': 'qa-probe-import'}),
        ]
        client._request.reset_mock()
        second = make_module(base_params(), check_mode=False)
        run_main(second, client)

        again = _result(second)
        assert again['changed'] is False
        assert again['vm_id'] == '57'
        assert again['msg'] == "VM 'qa-probe-import' already exists"
        client._request.assert_not_called()

    def test_check_mode_would_create_when_name_is_free(self):
        client = MagicMock()
        client.vms.list.return_value = []
        module = make_module(base_params(), check_mode=True)

        run_main(module, client)

        result = _result(module)
        assert result['changed'] is True
        assert result['msg'] == "Would create VM import (check mode)"
        assert result['payload']['name'] == 'qa-probe-import'
        assert result['payload']['file'] == '41'
        client._request.assert_not_called()
