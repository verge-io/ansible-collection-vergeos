#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for api_key module"""

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
