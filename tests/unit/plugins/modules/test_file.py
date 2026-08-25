#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for file module"""

import pytest
from unittest.mock import MagicMock, patch


# Real exception classes: MagicMocks in an except tuple raise TypeError
# the moment any exception passes through, and as side_effects they are
# called instead of raised.
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
    """Mock pyvergeos SDK for all tests, with real exception classes"""
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


def make_resource(data):
    """Mock SDK File: dict()-able via the mapping protocol, with the
    name/size_bytes model properties the module reads.

    dict() prefers the mapping protocol (keys + __getitem__) over iteration,
    and MagicMock auto-provides a keys attribute -- so both must be
    configured explicitly or dict(mock) silently returns {}.
    """
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    obj.name = data.get('name')
    obj.size_bytes = int(data.get('filesize') or 0)
    return obj


def make_module(params, check_mode=False):
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    # The real methods raise SystemExit; without this the code under test
    # continues past exit_json/fail_json
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def base_params(**overrides):
    params = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'name': None,
        'src': None,
        'description': None,
        'tier': None,
        'force': False,
        'state': 'present',
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.file.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import file as file_module
                try:
                    file_module.main()
                except SystemExit:
                    pass


@pytest.fixture
def src_file(tmp_path):
    """A real 4-byte local file to upload"""
    path = tmp_path / 'image.iso'
    path.write_bytes(b'abcd')
    return str(path)


class TestFilePresent:
    def test_uploads_when_not_in_catalog(self, src_file):
        mock_client = MagicMock()
        mock_client.files.list.return_value = []
        mock_client.files.upload.return_value = make_resource(
            {'$key': 41, 'name': 'image.iso', 'filesize': 4})

        module = make_module(base_params(src=src_file))
        run_main(module, mock_client)

        mock_client.files.upload.assert_called_once_with(
            src_file, name='image.iso', description=None, tier=None)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_skips_when_same_name_and_size(self, src_file):
        mock_client = MagicMock()
        existing = make_resource({'$key': 41, 'name': 'image.iso', 'filesize': 4})
        mock_client.files.list.return_value = [existing]

        module = make_module(base_params(src=src_file))
        run_main(module, mock_client)

        mock_client.files.upload.assert_not_called()
        existing.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_replaces_when_size_differs(self, src_file):
        mock_client = MagicMock()
        existing = make_resource({'$key': 41, 'name': 'image.iso', 'filesize': 999})
        mock_client.files.list.return_value = [existing]
        mock_client.files.upload.return_value = make_resource(
            {'$key': 42, 'name': 'image.iso', 'filesize': 4})

        module = make_module(base_params(src=src_file))
        run_main(module, mock_client)

        existing.delete.assert_called_once()
        mock_client.files.upload.assert_called_once()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_force_reuploads_same_size(self, src_file):
        mock_client = MagicMock()
        existing = make_resource({'$key': 41, 'name': 'image.iso', 'filesize': 4})
        mock_client.files.list.return_value = [existing]
        mock_client.files.upload.return_value = make_resource(
            {'$key': 42, 'name': 'image.iso', 'filesize': 4})

        module = make_module(base_params(src=src_file, force=True))
        run_main(module, mock_client)

        existing.delete.assert_called_once()
        mock_client.files.upload.assert_called_once()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_explicit_name_overrides_basename(self, src_file):
        mock_client = MagicMock()
        mock_client.files.list.return_value = []
        mock_client.files.upload.return_value = make_resource(
            {'$key': 41, 'name': 'renamed.iso', 'filesize': 4})

        module = make_module(base_params(src=src_file, name='renamed.iso'))
        run_main(module, mock_client)

        assert mock_client.files.upload.call_args[1]['name'] == 'renamed.iso'

    def test_check_mode_does_not_upload(self, src_file):
        mock_client = MagicMock()
        mock_client.files.list.return_value = []

        module = make_module(base_params(src=src_file), check_mode=True)
        run_main(module, mock_client)

        mock_client.files.upload.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_fails_when_src_missing(self, tmp_path):
        mock_client = MagicMock()
        module = make_module(base_params(src=str(tmp_path / 'nope.iso')))
        run_main(module, mock_client)

        module.fail_json.assert_called_once()
        mock_client.files.upload.assert_not_called()


class TestFileAbsent:
    def test_deletes_existing(self):
        mock_client = MagicMock()
        existing = make_resource({'$key': 41, 'name': 'image.iso', 'filesize': 4})
        mock_client.files.list.return_value = [existing]

        module = make_module(base_params(state='absent', name='image.iso'))
        run_main(module, mock_client)

        existing.delete.assert_called_once()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_no_change_when_missing(self):
        mock_client = MagicMock()
        mock_client.files.list.return_value = []

        module = make_module(base_params(state='absent', name='image.iso'))
        run_main(module, mock_client)

        assert module.exit_json.call_args[1]['changed'] is False

    def test_check_mode_does_not_delete(self):
        mock_client = MagicMock()
        existing = make_resource({'$key': 41, 'name': 'image.iso', 'filesize': 4})
        mock_client.files.list.return_value = [existing]

        module = make_module(base_params(state='absent', name='image.iso'),
                             check_mode=True)
        run_main(module, mock_client)

        existing.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True
