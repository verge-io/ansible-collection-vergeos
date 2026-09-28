#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""drive size on an existing drive (issue #124).

size used to be applied only at create. On an existing drive a larger size
reported ok and the disk stayed the same size. The platform grows a drive
through PUT disksize (bytes) and rejects a shrink with "Disks are not
allowed to shrink".
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock, patch


GiB = 1024 ** 3


def make_row(data):
    obj = MagicMock()
    stored = dict(data)
    obj.keys.side_effect = lambda: list(stored.keys())
    obj.__getitem__.side_effect = stored.__getitem__
    obj.__iter__.side_effect = lambda: iter(stored)
    for key, value in stored.items():
        setattr(obj, key, value)

    def save(**kwargs):
        stored.update(kwargs)
        return make_row(dict(stored))

    obj.save.side_effect = save
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
        'vm_name': 'web-01',
        'name': 'data',
        'state': 'present',
        'size': None,
        'drive_type': None,
        'media_type': 'disk',
        'tier': None,
        'read_only': None,
    }
    params.update(overrides)
    return params


def make_client(disksize=5 * GiB):
    drive = make_row({
        '$key': 12,
        'name': 'data',
        'interface': 'virtio-scsi',
        'media': 'disk',
        'preferred_tier': '4',
        'readonly': False,
        'disksize': disksize,
    })
    vm = make_row({'$key': 41, 'name': 'web-01', 'machine': 7})
    vm.drives.list.return_value = [drive]
    client = MagicMock()
    client.vms.list.return_value = [vm]
    client.drive = drive
    return client


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.drive.'
               'get_vergeos_client', return_value=mock_client), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.drive.'
               'AnsibleModule', return_value=mock_module):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            drive as drive_module,
        )
        try:
            drive_module.main()
        except SystemExit:
            pass


def test_grow_reports_changed_and_writes_disksize():
    """A larger size writes disksize in bytes and reports changed."""
    client = make_client(disksize=5 * GiB)
    module = make_module(base_params(size=10))

    run_main(module, client)

    client.drive.save.assert_called_once_with(disksize=10 * GiB)
    assert 10 * GiB == 10737418240
    result = module.exit_json.call_args[1]
    assert result['changed'] is True
    assert result['drive']['disksize'] == 10 * GiB
    module.fail_json.assert_not_called()


def test_grow_in_check_mode_reports_changed_and_writes_nothing():
    client = make_client(disksize=5 * GiB)
    module = make_module(base_params(size=10), check_mode=True)

    run_main(module, client)

    client.drive.save.assert_not_called()
    result = module.exit_json.call_args[1]
    assert result['changed'] is True
    assert result['drive']['disksize'] == 10 * GiB


def test_shrink_fails_with_the_platform_message():
    """A smaller size must not report ok. The platform refuses it."""
    client = make_client(disksize=10 * GiB)
    module = make_module(base_params(size=5))

    run_main(module, client)

    client.drive.save.assert_not_called()
    module.exit_json.assert_not_called()
    module.fail_json.assert_called_once()
    assert module.fail_json.call_args[1]['msg'] == \
        "Disks are not allowed to shrink"


def test_shrink_in_check_mode_fails_rather_than_reporting_ok():
    client = make_client(disksize=10 * GiB)
    module = make_module(base_params(size=5), check_mode=True)

    run_main(module, client)

    client.drive.save.assert_not_called()
    module.exit_json.assert_not_called()
    assert module.fail_json.call_args[1]['msg'] == \
        "Disks are not allowed to shrink"


@pytest.mark.parametrize('disksize', [5 * GiB, str(5 * GiB)])
def test_identical_size_is_unchanged(disksize):
    client = make_client(disksize=disksize)
    module = make_module(base_params(size=5))

    run_main(module, client)

    client.drive.save.assert_not_called()
    assert module.exit_json.call_args[1]['changed'] is False
    module.fail_json.assert_not_called()


def test_omitted_size_leaves_the_drive_alone():
    client = make_client(disksize=5 * GiB)
    module = make_module(base_params(size=None))

    run_main(module, client)

    client.drive.save.assert_not_called()
    assert module.exit_json.call_args[1]['changed'] is False
