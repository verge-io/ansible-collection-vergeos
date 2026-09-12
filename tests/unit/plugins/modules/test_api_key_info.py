#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for api_key_info module"""

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
             ip_allow='', description=''):
    data = {'$key': key, 'name': name, 'user_name': user_name,
            'description': description, 'ip_allow_list': ip_allow,
            'ip_deny_list': '', 'created': 100,
            'lastlogin_stamp': 0, 'lastlogin_ip': ''}
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    return obj


def make_module(params):
    module = MagicMock()
    module.params = params
    module.check_mode = False
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def base_params(**overrides):
    params = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'user': None,
        'name': None,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.api_key_info.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.api_key_info.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.api_key_info.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    api_key_info as api_key_info_module,
                )
                try:
                    api_key_info_module.main()
                except SystemExit:
                    pass


class TestApiKeyInfo:
    def test_returns_all_keys_without_secrets(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = [
            make_key(key=1, ip_allow='192.0.2.0/24,198.51.100.1/32'),
            make_key(key=2, name='other', user_name='bob')]

        module = make_module(base_params())
        run_main(module, mock_client)

        result = module.exit_json.call_args[1]
        assert result['changed'] is False
        assert len(result['api_keys']) == 2
        first = result['api_keys'][0]
        assert first['ip_allow_list'] == ['192.0.2.0/24', '198.51.100.1/32']
        for row in result['api_keys']:
            assert 'secret' not in row

    def test_filters_by_user(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = [
            make_key(key=1, user_name='automation'),
            make_key(key=2, user_name='bob')]

        module = make_module(base_params(user='bob'))
        run_main(module, mock_client)

        rows = module.exit_json.call_args[1]['api_keys']
        assert len(rows) == 1
        assert rows[0]['user_name'] == 'bob'

    def test_filters_by_name(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = [
            make_key(key=1, name='ansible-runner'),
            make_key(key=2, name='grafana')]

        module = make_module(base_params(name='grafana'))
        run_main(module, mock_client)

        rows = module.exit_json.call_args[1]['api_keys']
        assert len(rows) == 1
        assert rows[0]['name'] == 'grafana'

    def test_empty_system_returns_empty_list(self):
        mock_client = MagicMock()
        mock_client.api_keys.list.return_value = []

        module = make_module(base_params())
        run_main(module, mock_client)

        assert module.exit_json.call_args[1]['api_keys'] == []
