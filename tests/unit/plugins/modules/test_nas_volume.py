#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the nas_volume module.

Note what the previous version of this file did, because it is the clearest
example of the trap in this repository:

    client.nas_volumes.update.assert_called_once_with(7, maxsize=200 * GB)

That assertion passed. It also described a call that raises TypeError against
the real SDK, because NASVolumeManager.update() has no `maxsize` parameter --
the mock accepted the keyword happily and the test enshrined the bug. Only
tests/unit/test_sdk_call_signatures.py can catch that shape; these tests cover
the logic around it.
"""

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
GB = 1073741824
VOLUME_KEY = 'c3437883534918dcf2abbb3e9b9622b865226c68'
VOLUME_ROW = {'$key': VOLUME_KEY, 'name': 'backups', 'description': '',
              'maxsize': 100 * GB, 'preferred_tier': '1', 'read_only': False,
              'enabled': True, 'snapshot_profile': None}


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
        'state': 'present',
        'service': 'nas-01',
        'size_gb': 100,
        'tier': None,
        'description': None,
        'read_only': False,
        'enabled': True,
        'snapshot_profile': None,
    }
    params.update(overrides)
    return params


def make_client(volume_row=_UNSET, profiles=None):
    """A client whose name lookups go through list(), not get(name=).

    resolve_one matches client-side and refuses to guess between duplicates
    (#72/#85), so every mock here returns rows from list().
    """
    if volume_row is _UNSET:
        volume_row = VOLUME_ROW
    client = MagicMock()
    client.nas_volumes.list.return_value = (
        [make_row(dict(volume_row))] if volume_row else [])
    client.nas_services.list.return_value = [
        make_row({'$key': 2, 'name': 'nas-01'})]
    client.snapshot_profiles.list.return_value = [
        make_row(dict(row)) for row in (profiles or [{'$key': 2,
                                                      'name': 'HIPAA'}])]
    return client


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


class TestNasVolumePresent:
    def test_creates_when_missing(self):
        client = make_client(volume_row=None)
        client.nas_volumes.create.return_value = make_row(dict(VOLUME_ROW))

        module = make_module(base_params())
        run_main(module, client)

        client.nas_volumes.create.assert_called_once_with(
            name='backups', service=2, size_gb=100,
            read_only=False, enabled=True)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_create_resolves_the_service_to_a_key(self):
        """The SDK would resolve a service NAME itself, with an OData filter
        built from an unquoted literal. Resolving it here keeps the duplicate
        refusal and stays clear of pyVergeOS#100's escaping."""
        client = make_client(volume_row=None)
        client.nas_volumes.create.return_value = make_row(dict(VOLUME_ROW))

        run_main(make_module(base_params()), client)

        assert client.nas_volumes.create.call_args[1]['service'] == 2

    def test_create_requires_service_and_size(self):
        client = make_client(volume_row=None)

        module = make_module(base_params(service=None))
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'required' in module.fail_json.call_args[1]['msg']

    def test_idempotent_when_matching(self):
        client = make_client()

        module = make_module(base_params())
        run_main(module, client)

        client.nas_volumes.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_grows_drifted_size_with_the_keyword_the_sdk_takes(self):
        """The resize bug. The column is `maxsize` and holds bytes; the SDK's
        update() keyword is `size_gb` and does the multiplication itself."""
        client = make_client()

        module = make_module(base_params(size_gb=200))
        run_main(module, client)

        client.nas_volumes.update.assert_called_once_with(
            VOLUME_KEY, size_gb=200)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_size_that_matches_the_byte_count_is_not_drift(self):
        client = make_client()

        run_main(make_module(base_params(size_gb=100)), client)

        client.nas_volumes.update.assert_not_called()

    def test_corrects_drifted_tier(self):
        """#8's defect on a different table: the column is preferred_tier,
        stored as a string, and the SDK keyword is `tier`."""
        client = make_client()

        module = make_module(base_params(tier=3))
        run_main(module, client)

        client.nas_volumes.update.assert_called_once_with(VOLUME_KEY, tier=3)

    def test_tier_stored_as_a_string_compares_equal_to_the_int(self):
        client = make_client()

        run_main(make_module(base_params(tier=1)), client)

        client.nas_volumes.update.assert_not_called()

    def test_attaches_a_snapshot_profile_by_name(self):
        client = make_client()

        module = make_module(base_params(snapshot_profile='HIPAA'))
        run_main(module, client)

        client.nas_volumes.update.assert_called_once_with(
            VOLUME_KEY, snapshot_profile=2)

    def test_matching_snapshot_profile_is_not_drift(self):
        row = dict(VOLUME_ROW, snapshot_profile=2)
        client = make_client(volume_row=row)

        run_main(make_module(base_params(snapshot_profile='HIPAA')), client)

        client.nas_volumes.update.assert_not_called()

    def test_unknown_snapshot_profile_fails(self):
        client = make_client()
        client.snapshot_profiles.list.return_value = []

        module = make_module(base_params(snapshot_profile='ghost'))
        run_main(module, client)

        module.fail_json.assert_called_once()
        assert "'ghost'" in module.fail_json.call_args[1]['msg']

    def test_check_mode_writes_nothing(self):
        client = make_client()

        module = make_module(base_params(size_gb=200), check_mode=True)
        run_main(module, client)

        client.nas_volumes.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True


class TestNasVolumeAbsent:
    def test_disables_before_delete(self):
        client = make_client()

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.nas_volumes.update.assert_called_once_with(
            VOLUME_KEY, enabled=False)
        client.nas_volumes.delete.assert_called_once_with(VOLUME_KEY)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_already_disabled_deletes_directly(self):
        client = make_client(volume_row=dict(VOLUME_ROW, enabled=False))

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.nas_volumes.update.assert_not_called()
        client.nas_volumes.delete.assert_called_once_with(VOLUME_KEY)

    def test_retries_while_the_drive_is_still_online(self):
        """Measured on 26.1.8: after disabling, `mounted` goes false about a
        second before the delete stops being refused. A fixed sleep is a
        guess; retrying the delete is the predicate we actually want."""
        client = make_client()
        client.nas_volumes.delete.side_effect = [
            APIError('Unable to delete machine drive: Unable to delete '
                     'online drive'),
            None,
        ]

        module = make_module(base_params(state='absent'))
        with patch('time.sleep'):
            run_main(module, client)

        assert client.nas_volumes.delete.call_count == 2
        assert module.exit_json.call_args[1]['changed'] is True

    def test_any_other_delete_error_is_not_retried(self):
        """Retrying an error that will never clear just delays the report."""
        client = make_client()
        client.nas_volumes.delete.side_effect = APIError('Permission denied')

        module = make_module(base_params(state='absent'))
        with patch('time.sleep'):
            run_main(module, client)

        assert client.nas_volumes.delete.call_count == 1
        module.fail_json.assert_called_once()

    def test_gives_up_with_a_useful_message(self):
        client = make_client()
        client.nas_volumes.delete.side_effect = APIError(
            'Unable to delete online drive')

        module = make_module(base_params(state='absent'))
        with patch('time.sleep'), \
                patch('time.time', side_effect=[0, 10 ** 6, 10 ** 6]):
            run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'still online' in module.fail_json.call_args[1]['msg']

    def test_absent_missing_no_change(self):
        client = make_client(volume_row=None)

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.nas_volumes.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_check_mode_deletes_nothing(self):
        client = make_client()

        module = make_module(base_params(state='absent'), check_mode=True)
        run_main(module, client)

        client.nas_volumes.update.assert_not_called()
        client.nas_volumes.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True


class TestDuplicateNamesAreRefused:
    def test_two_volumes_with_one_name(self):
        """Updating or deleting the wrong volume destroys data, and no re-run
        undoes it (#72/#85)."""
        client = make_client()
        client.nas_volumes.list.return_value = [
            make_row(dict(VOLUME_ROW, **{'$key': 'aa'})),
            make_row(dict(VOLUME_ROW, **{'$key': 'bb'})),
        ]

        module = make_module(base_params(state='absent'))
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
