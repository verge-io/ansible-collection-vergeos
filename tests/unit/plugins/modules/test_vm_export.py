#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the vm_export module."""

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
EXPORT_ROW = {'$key': 1, 'volume': VOLUME_KEY, 'status': 'idle',
              'status_info': '', 'quiesced': True, 'create_current': True,
              'max_exports': 3}
STAT_ROW = {'$key': 1, 'file_name': 'nightly', 'virtual_machines': 1,
            'export_success': 1, 'errors': 0, 'size_bytes': 1073743543,
            'duration': 20, 'timestamp': 1790261808}


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


def make_client(export_row=_UNSET, stats=_UNSET, volume_rows=None):
    if export_row is _UNSET:
        export_row = EXPORT_ROW
    if stats is _UNSET:
        stats = (STAT_ROW,)
    client = MagicMock()
    client.nas_volumes.list.return_value = volume_rows if volume_rows is not None \
        else [make_row({'$key': VOLUME_KEY, 'name': 'backups'})]
    client.volume_vm_exports.list.return_value = (
        [make_row(dict(export_row))] if export_row else [])
    client.volume_vm_exports.get.return_value = make_row(
        dict(export_row or EXPORT_ROW))
    client.volume_vm_exports.stats.return_value.list.return_value = [
        make_row(dict(row)) for row in stats]
    client.vms.list.return_value = [make_row({'$key': 41, 'name': 'app'})]
    return client


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


class TestExportConfig:
    def test_creates_when_missing(self):
        client = make_client(export_row=None)
        client.volume_vm_exports.create.return_value = make_row(
            dict(EXPORT_ROW))

        module = make_module(base_params())
        run_main(module, client)

        client.volume_vm_exports.create.assert_called_once_with(
            volume=VOLUME_KEY, quiesced=True, create_current=True,
            max_exports=3)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_missing_volume_fails(self):
        client = make_client(volume_rows=[])

        module = make_module(base_params())
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'not found' in module.fail_json.call_args[1]['msg']

    def test_idempotent_when_matching(self):
        client = make_client()

        module = make_module(base_params())
        run_main(module, client)

        client.volume_vm_exports.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_corrects_drifted_retention(self):
        client = make_client()

        module = make_module(base_params(max_exports=5))
        run_main(module, client)

        client.volume_vm_exports.update.assert_called_once_with(
            1, max_exports=5)

    def test_corrects_drifted_quiesce(self):
        client = make_client()

        module = make_module(base_params(quiesced=False))
        run_main(module, client)

        client.volume_vm_exports.update.assert_called_once_with(
            1, quiesced=False)

    def test_finds_the_config_for_this_volume_only(self):
        """One config per volume, matched on the volume column rather than
        through an SDK filter that interpolates the key unquoted."""
        client = make_client(export_row=None)
        client.volume_vm_exports.list.return_value = [
            make_row(dict(EXPORT_ROW, volume='someone-elses-volume'))]
        client.volume_vm_exports.create.return_value = make_row(
            dict(EXPORT_ROW))

        module = make_module(base_params())
        run_main(module, client)

        client.volume_vm_exports.create.assert_called_once()

    def test_absent_deletes(self):
        client = make_client()

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.volume_vm_exports.delete.assert_called_once_with(1)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_absent_missing_no_change(self):
        client = make_client(export_row=None)

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.volume_vm_exports.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_check_mode_writes_nothing(self):
        client = make_client(export_row=None)

        module = make_module(base_params(), check_mode=True)
        run_main(module, client)

        client.volume_vm_exports.create.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True


class TestStartingAnExport:
    def test_starts_and_waits(self):
        client = make_client()

        module = make_module(base_params(start=True, export_name='nightly'))
        run_main(module, client)

        client.volume_vm_exports.start_export.assert_called_once_with(
            1, name='nightly', vms=None)
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert 'export finished' in result['actions']

    def test_named_vms_are_resolved_to_keys(self):
        client = make_client()

        module = make_module(base_params(start=True, vms=['app']))
        run_main(module, client)

        assert client.volume_vm_exports.start_export.call_args[1]['vms'] == [41]

    def test_unknown_vm_fails_before_starting(self):
        client = make_client()
        client.vms.list.return_value = []

        module = make_module(base_params(start=True, vms=['ghost']))
        run_main(module, client)

        client.volume_vm_exports.start_export.assert_not_called()
        assert "'ghost'" in module.fail_json.call_args[1]['msg']

    def test_error_status_fails(self):
        client = make_client()
        client.volume_vm_exports.get.return_value = make_row(
            dict(EXPORT_ROW, status='error', status_info='out of space'))

        module = make_module(base_params(start=True))
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'out of space' in module.fail_json.call_args[1]['msg']

    def test_a_run_that_recorded_errors_is_not_a_success(self):
        """An export can end 'idle' having failed some of its VMs. Reporting
        success for a backup that did not happen is this module's worst
        failure mode."""
        client = make_client(stats=(dict(STAT_ROW, errors=2,
                                         export_success=0),))

        module = make_module(base_params(start=True))
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert '2 error(s)' in module.fail_json.call_args[1]['msg']

    def test_the_newest_statistics_row_is_the_one_reported(self):
        client = make_client(stats=(
            dict(STAT_ROW, **{'$key': 1, 'file_name': 'older',
                              'timestamp': 1}),
            dict(STAT_ROW, **{'$key': 2, 'file_name': 'newest',
                              'timestamp': 99}),
        ))

        module = make_module(base_params(start=True))
        run_main(module, client)

        assert module.exit_json.call_args[1]['last_export']['file_name'] \
            == 'newest'

    def test_no_wait_skips_the_statistics_check(self):
        client = make_client()

        module = make_module(base_params(start=True, wait=False))
        run_main(module, client)

        client.volume_vm_exports.stats.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert result['last_export'] is None

    def test_check_mode_starts_nothing(self):
        client = make_client()

        module = make_module(base_params(start=True), check_mode=True)
        run_main(module, client)

        client.volume_vm_exports.start_export.assert_not_called()


class TestDuplicateVolumeNamesAreRefused:
    def test_two_volumes_with_one_name(self):
        client = make_client(volume_rows=[
            make_row({'$key': 'aa', 'name': 'backups'}),
            make_row({'$key': 'bb', 'name': 'backups'}),
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
