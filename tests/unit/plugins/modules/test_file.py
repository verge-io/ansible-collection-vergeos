#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the file module."""

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


SRC = '/tmp/zz-unit-file.img'
SIZE = 8388608
FILE_ROW = {'$key': 73, 'name': 'zz-unit-file.img', 'filesize': SIZE,
            'allocated_bytes': SIZE, 'description': 'before',
            'preferred_tier': '1'}


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
        'name': 'zz-unit-file.img',
        'src': SRC,
        'description': None,
        'tier': None,
        'force': False,
        'state': 'present',
    }
    params.update(overrides)
    return params


def make_client(row=None, uploaded_key=73, reread=None):
    """reread: what a re-read by key answers with. Defaults to the settled
    row, which is what the platform does once filesize catches up."""
    client = MagicMock()
    client.files.list.return_value = [make_row(dict(row))] if row else []
    client.files.upload.return_value = make_row({'$key': uploaded_key})
    client._request.return_value = dict(reread if reread is not None
                                        else FILE_ROW)
    return client


def put_bodies(client):
    """json_data of every PUT the module made.

    Not call_args: the last _request is the re-read that follows a write, so
    asserting on it would test the mock rather than the module.
    """
    return [call.kwargs['json_data']
            for call in client._request.call_args_list
            if call.args and call.args[0] == 'PUT']


def run_main(mock_module, mock_client, exists=True, size=SIZE):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.file.get_vergeos_client',
               return_value=mock_client), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.file.HAS_PYVERGEOS', True), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.file.AnsibleModule',
               return_value=mock_module), \
         patch('os.path.isfile', return_value=exists), \
         patch('os.path.getsize', return_value=size), \
         patch('time.sleep'):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            file as file_module,
        )
        try:
            file_module.main()
        except SystemExit:
            pass


class TestUpload:
    def test_uploads_when_missing(self):
        client = make_client()

        module = make_module(base_params())
        run_main(module, client)

        client.files.upload.assert_called_once()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_returns_the_full_row_not_the_create_response(self):
        """upload() answers with what the create call returned, which carries
        almost nothing -- name, filesize, description and tier all read None.
        Handing that back reports an empty catalog entry for a successful
        upload."""
        client = make_client()

        module = make_module(base_params())
        run_main(module, client)

        returned = module.exit_json.call_args[1]['file']
        assert returned['filesize'] == SIZE
        assert returned['name'] == 'zz-unit-file.img'

    def test_same_name_and_size_is_not_a_change(self):
        client = make_client(row=FILE_ROW)

        module = make_module(base_params())
        run_main(module, client)

        client.files.upload.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_a_different_size_replaces(self):
        wrong = dict(FILE_ROW, filesize=99, allocated_bytes=99)
        client = make_client(row=wrong, reread=wrong)

        module = make_module(base_params())
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.'
                   'file.SIZE_SETTLE_SECONDS', 0):
            run_main(module, client)

        client.files.upload.assert_called_once()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_force_replaces_a_matching_file(self):
        client = make_client(row=FILE_ROW)

        module = make_module(base_params(force=True))
        run_main(module, client)

        client.files.upload.assert_called_once()

    def test_a_missing_source_file_fails(self):
        client = make_client()

        module = make_module(base_params())
        run_main(module, client, exists=False)

        module.fail_json.assert_called_once()
        assert 'does not exist' in module.fail_json.call_args[1]['msg']

    def test_check_mode_uploads_nothing(self):
        client = make_client()

        module = make_module(base_params(), check_mode=True)
        run_main(module, client)

        client.files.upload.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True


class TestTheFilesizeSettleWindow:
    """`filesize` settles asynchronously. Measured on 26.1.8: an 8388608-byte
    file reads 8126464 for four seconds after the upload.

    Idempotence turns entirely on that number, so a run inside the window
    concludes the catalog copy is wrong, DELETES it, and re-uploads -- for an
    ISO, gigabytes of transfer and a gap where the file is not there.
    """

    def test_an_unsettled_size_does_not_trigger_a_re_upload(self):
        unsettled = dict(FILE_ROW, filesize=8126464)
        client = make_client(row=unsettled)
        # allocated_bytes is right immediately, so no waiting is even needed
        client._request.return_value = dict(FILE_ROW)

        module = make_module(base_params())
        run_main(module, client)

        client.files.upload.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_it_re_reads_when_neither_number_matches_yet(self):
        """allocation rounds up, so allocated_bytes is not always equal to the
        source size even when the file is correct. Then the only answer is to
        look again."""
        unsettled = dict(FILE_ROW, filesize=8126464, allocated_bytes=12582912)
        client = make_client(row=unsettled)
        client._request.return_value = dict(FILE_ROW)   # settled on re-read

        module = make_module(base_params())
        run_main(module, client)

        client.files.upload.assert_not_called()
        assert client._request.called

    def test_a_genuinely_different_file_is_still_replaced(self):
        """The settle allowance must not turn into "never replace anything"."""
        wrong = dict(FILE_ROW, filesize=99, allocated_bytes=99)
        client = make_client(row=wrong, reread=wrong)   # never settles

        module = make_module(base_params())
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.'
                   'file.SIZE_SETTLE_SECONDS', 0):
            run_main(module, client)

        client.files.upload.assert_called_once()


class TestMetadataConvergesWithoutReuploading:
    """description and tier were accepted, stored once at upload, and then
    ignored forever -- #18's shape. They are real columns and the API takes a
    PUT on the row, even though pyvergeos models no update() for files."""

    def test_description_drift_is_corrected_in_place(self):
        client = make_client(row=FILE_ROW)

        module = make_module(base_params(description='after'))
        run_main(module, client)

        client.files.upload.assert_not_called()
        assert put_bodies(client) == [{'description': 'after'}]
        assert any(call.args[:2] == ('PUT', 'files/73')
                   for call in client._request.call_args_list)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_tier_is_written_to_preferred_tier_as_a_string(self):
        """#8 for the third time: the option is `tier`, the column is
        `preferred_tier`, and it stores '2' rather than 2."""
        client = make_client(row=FILE_ROW)

        module = make_module(base_params(tier=2))
        run_main(module, client)

        assert put_bodies(client) == [{'preferred_tier': '2'}]

    def test_a_tier_stored_as_a_string_compares_equal_to_the_int(self):
        client = make_client(row=FILE_ROW)

        module = make_module(base_params(tier=1))
        run_main(module, client)

        assert module.exit_json.call_args[1]['changed'] is False

    def test_matching_metadata_is_not_a_change(self):
        client = make_client(row=FILE_ROW)

        module = make_module(base_params(description='before', tier=1))
        run_main(module, client)

        assert module.exit_json.call_args[1]['changed'] is False

    def test_check_mode_writes_no_metadata(self):
        client = make_client(row=FILE_ROW)

        module = make_module(base_params(description='after'), check_mode=True)
        run_main(module, client)

        assert put_bodies(client) == []
        assert module.exit_json.call_args[1]['changed'] is True


class TestAbsent:
    def test_deletes_when_present(self):
        row = make_row(dict(FILE_ROW))
        client = make_client()
        client.files.list.return_value = [row]

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        row.delete.assert_called_once()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_absent_missing_no_change(self):
        client = make_client()

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        assert module.exit_json.call_args[1]['changed'] is False

    def test_check_mode_deletes_nothing(self):
        row = make_row(dict(FILE_ROW))
        client = make_client()
        client.files.list.return_value = [row]

        module = make_module(base_params(state='absent'), check_mode=True)
        run_main(module, client)

        row.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True


class TestSdkErrorsAreHandled:
    @pytest.mark.parametrize('exc', [
        APIError, AuthenticationError, ValidationError, VergeConnectionError,
    ])
    def test_sdk_errors_do_not_fall_through_to_unexpected(self, exc):
        client = make_client()
        client.files.list.side_effect = exc('server said no')
        module = make_module(base_params())

        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'Unexpected error' not in module.fail_json.call_args[1]['msg']
