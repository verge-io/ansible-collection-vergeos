#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for api_key module"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock, patch


from pyvergeos.exceptions import (  # real classes: a Mock here is
    APIError,                       # CALLED as a side_effect, not raised
    AuthenticationError,
    NotFoundError,
    ValidationError,
    VergeConnectionError,
)


def make_key(key=1, name='ansible-runner', user_name='automation',
             description='', ip_allow='', ip_deny='', created=100,
             lastlogin=0, lastlogin_ip=''):
    data = {'$key': key, 'name': name, 'user_name': user_name,
            'description': description, 'ip_allow_list': ip_allow,
            'ip_deny_list': ip_deny, 'created': created,
            'lastlogin_stamp': lastlogin, 'lastlogin_ip': lastlogin_ip}
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    return obj


def make_created(key=5, secret='one-time-secret'):
    created = MagicMock()
    created.key = key
    created.secret = secret
    return created


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
        'user': 'automation',
        'name': 'ansible-runner',
        'state': 'present',
        'description': None,
        'ip_allow_list': None,
        'ip_deny_list': None,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.api_key.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.api_key.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.api_key.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    api_key as api_key_module,
                )
                try:
                    api_key_module.main()
                except SystemExit:
                    pass


class TestApiKeyCreate:
    def test_create_returns_secret_and_changed(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = []
        mock_client.api_keys.create.return_value = make_created(key=5)
        mock_client.api_keys.get.return_value = make_key(key=5)

        module = make_module(base_params())
        run_main(module, mock_client)

        mock_client.api_keys.create.assert_called_once()
        assert mock_client.api_keys.create.call_args[1]['user'] == 'automation'
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert result['secret'] == 'one-time-secret'
        assert result['api_key']['name'] == 'ansible-runner'

    def test_create_passes_ip_lists_as_lists(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = []
        mock_client.api_keys.create.return_value = make_created()
        mock_client.api_keys.get.return_value = make_key()

        module = make_module(base_params(
            ip_allow_list=['192.0.2.0/24', '198.51.100.1/32']))
        run_main(module, mock_client)

        create_kwargs = mock_client.api_keys.create.call_args[1]
        assert create_kwargs['ip_allow_list'] == ['192.0.2.0/24',
                                                  '198.51.100.1/32']

    def test_create_check_mode_makes_no_calls(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = []

        module = make_module(base_params(), check_mode=True)
        run_main(module, mock_client)

        mock_client.api_keys.create.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert 'secret' not in result

    def test_match_is_scoped_to_user(self):
        # Same key name under another user must not block creation
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = [
            make_key(user_name='someone-else')]
        mock_client.api_keys.create.return_value = make_created()
        mock_client.api_keys.get.return_value = make_key()

        module = make_module(base_params())
        run_main(module, mock_client)

        mock_client.api_keys.create.assert_called_once()


class TestApiKeyUpdate:
    def test_idempotent_when_identical(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = [
            make_key(description='automation key',
                     ip_allow='192.0.2.0/24')]

        module = make_module(base_params(
            description='automation key',
            ip_allow_list=['192.0.2.0/24']))
        run_main(module, mock_client)

        mock_client.api_keys.update.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is False

    def test_unset_params_do_not_drift(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = [
            make_key(description='keep me', ip_allow='192.0.2.0/24')]

        module = make_module(base_params())
        run_main(module, mock_client)

        mock_client.api_keys.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_updates_description_only(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = [
            make_key(key=3, description='old')]
        mock_client.api_keys.update.return_value = make_key(
            key=3, description='new')

        module = make_module(base_params(description='new'))
        run_main(module, mock_client)

        mock_client.api_keys.update.assert_called_once_with(
            3, description='new')
        assert module.exit_json.call_args[1]['changed'] is True

    def test_updates_ip_allow_as_joined_string(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = [
            make_key(key=3, ip_allow='192.0.2.0/24')]
        mock_client.api_keys.update.return_value = make_key(
            key=3, ip_allow='192.0.2.0/24,198.51.100.1/32')

        module = make_module(base_params(
            ip_allow_list=['192.0.2.0/24', '198.51.100.1/32']))
        run_main(module, mock_client)

        mock_client.api_keys.update.assert_called_once_with(
            3, ip_allow_list='192.0.2.0/24,198.51.100.1/32')

    def test_multiple_matches_refuse(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = [
            make_key(key=1), make_key(key=2)]

        module = make_module(base_params())
        run_main(module, mock_client)

        module.fail_json.assert_called_once()
        assert 'refusing' in module.fail_json.call_args[1]['msg']


class TestApiKeyAbsent:
    def test_absent_deletes_all_matches(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = [
            make_key(key=1), make_key(key=2)]

        module = make_module(base_params(state='absent'))
        run_main(module, mock_client)

        assert mock_client.api_keys.delete.call_count == 2
        deleted = [c[0][0] for c in mock_client.api_keys.delete.call_args_list]
        assert deleted == [1, 2]
        assert module.exit_json.call_args[1]['changed'] is True

    def test_absent_noop_when_missing(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = []

        module = make_module(base_params(state='absent'))
        run_main(module, mock_client)

        mock_client.api_keys.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_absent_check_mode_deletes_nothing(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = [make_key(key=1)]

        module = make_module(base_params(state='absent'), check_mode=True)
        run_main(module, mock_client)

        mock_client.api_keys.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True


class TestTheRowIsAlwaysFetchedWithItsColumnsNamed:
    """The module matches on user_name, which is a join rather than a stored
    column and therefore only present if asked for.

    The port relied on the SDK's default projection including it. It does, on
    1.6.1 -- but a default is not a contract, and the failure mode if it ever
    changed is silent and expensive: every row compares unequal, so every run
    creates another key and nothing errors. LIST_FIELDS makes it explicit.
    """

    def test_find_keys_names_the_columns_it_reads(self):
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            api_keys as shared,
        )
        client = MagicMock()
        client.api_keys.list.return_value = []
        client.api_keys.create.return_value = make_created()
        client.api_keys.get.return_value = make_key(key=5)
        run_main(make_module(base_params()), client)

        client.api_keys.list.assert_called_once_with(fields=shared.LIST_FIELDS)
        assert 'user_name' in shared.LIST_FIELDS

    def test_create_reads_the_row_back(self):
        """create() returns only an id and the one-time secret, so the
        documented api_key return value has to come from a real read."""
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            api_keys as shared,
        )
        client = MagicMock()
        client.api_keys.list.return_value = []
        client.api_keys.create.return_value = make_created(key=5)
        client.api_keys.get.return_value = make_key(key=5, description='d')
        module = make_module(base_params())

        run_main(module, client)

        client.api_keys.get.assert_called_once_with(5, fields=shared.LIST_FIELDS)
        result = module.exit_json.call_args.kwargs
        assert result['secret'] == 'one-time-secret'
        assert result['api_key']['description'] == 'd'

    def test_update_reads_the_row_back_instead_of_trusting_the_put(self):
        """The SDK builds its model from whatever the PUT returned, which is
        not a full row. Returning that directly reports None for every field
        the caller just set."""
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            api_keys as shared,
        )
        client = MagicMock()
        client.api_keys.list.return_value = [make_key(key=7, description='old')]
        client.api_keys.update.return_value = MagicMock()  # a thin PUT response
        client.api_keys.get.return_value = make_key(key=7, description='new')
        module = make_module(base_params(description='new'))

        run_main(module, client)

        client.api_keys.update.assert_called_once_with(7, description='new')
        client.api_keys.get.assert_called_once_with(7, fields=shared.LIST_FIELDS)
        result = module.exit_json.call_args.kwargs
        assert result['changed'] is True
        assert result['api_key']['description'] == 'new'


class TestTheSharedResultMapping:
    """api_key and api_key_info return the same shape. The mapping lives in
    module_utils so the two cannot drift -- which is the whole point of #75,
    applied to a read path rather than a write path."""

    def test_the_lastlogin_columns_are_renamed_not_invented(self):
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            api_keys as shared,
        )
        assert shared.RESULT_FIELD_MAP['last_login'] == 'lastlogin_stamp'
        assert shared.RESULT_FIELD_MAP['last_login_ip'] == 'lastlogin_ip'

    def test_both_modules_use_the_same_mapping(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            api_key, api_key_info,
        )
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            api_keys as shared,
        )
        assert api_key.key_result is shared.key_result
        assert api_key_info.key_result is shared.key_result

    def test_comma_joined_columns_are_presented_as_lists(self):
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            api_keys as shared,
        )
        row = shared.key_result(make_key(ip_allow='10.0.0.0/8,192.0.2.1/32'))
        assert row['ip_allow_list'] == ['10.0.0.0/8', '192.0.2.1/32']
        assert row['ip_deny_list'] == []

    def test_the_secret_is_never_in_a_returned_row(self):
        """It is not on the row at all, and must not be reconstructed onto
        one: the return value is logged by default."""
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            api_keys as shared,
        )
        assert 'secret' not in shared.RESULT_FIELD_MAP
        assert 'secret' not in shared.key_result(make_key())


class TestSdkErrorsAreHandled:
    """Every SDK exception the module imports must reach a named failure.

    The catch-all ``except Exception`` at the bottom of main() means an
    unhandled type does not crash -- it produces "Unexpected error: ...",
    which reads like a bug in the collection rather than a server saying no.
    Parametrising the whole import list also keeps the imports honest: these
    five were imported and never used since the port, which pylint reports as
    dead weight and which left the error paths entirely untested.
    """

    @pytest.mark.parametrize('exc', [
        APIError, AuthenticationError, ValidationError, VergeConnectionError,
    ])
    def test_sdk_errors_do_not_fall_through_to_unexpected(self, exc):
        client = MagicMock()
        client.api_keys.list.side_effect = exc('server said no')
        module = make_module(base_params())

        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'Unexpected error' not in module.fail_json.call_args.kwargs['msg']

    def test_not_found_is_reported_as_not_found(self):
        client = MagicMock()
        client.api_keys.list.side_effect = NotFoundError('no such user')
        module = make_module(base_params())

        run_main(module, client)

        assert 'Resource not found' in module.fail_json.call_args.kwargs['msg']
