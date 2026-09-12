#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vnet_rule_info module"""

import pytest
from unittest.mock import MagicMock, patch


from pyvergeos.exceptions import (  # real classes: a Mock here is
    APIError,                       # CALLED as a side_effect, not raised
    AuthenticationError,
    NotFoundError,
    ValidationError,
    VergeConnectionError,
)


def make_rule(key=1, name='allow-ssh', direction='incoming',
              system_rule=False, enabled=True):
    data = {'$key': key, 'name': name, 'direction': direction,
            'action': 'accept', 'protocol': 'tcp', 'interface': 'auto',
            'source_ip': '', 'source_ports': '', 'destination_ip': '',
            'destination_ports': '22', 'target_ip': '', 'target_ports': '',
            'enabled': enabled, 'log': False, 'statistics': False,
            'orderid': key, 'description': '', 'system_rule': system_rule}
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    return obj


def make_network(rules, pending=False):
    data = {'$key': 3, 'name': 'Internal', 'need_fw_apply': pending}
    net = MagicMock()
    net.keys.return_value = list(data.keys())
    net.__getitem__.side_effect = lambda k: data[k]
    net.rules.list.return_value = rules
    return net


def make_module(params):
    module = MagicMock()
    module.params = params
    module.check_mode = False
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def base_params(**overrides):
    params = {'host': 'vergeos.example.com', 'username': 'admin',
              'password': 'secret', 'insecure': False,
              'network': 'Internal', 'name': None, 'direction': None}
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.vnet_rule_info.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vnet_rule_info.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.vnet_rule_info.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    vnet_rule_info as info_module,
                )
                try:
                    info_module.main()
                except SystemExit:
                    pass


class TestVnetRuleInfo:
    def test_returns_all_rules_with_system_flag(self):
        mock_client = MagicMock()
        mock_client.networks.get.return_value = make_network(
            [make_rule(key=1, name='System UI', system_rule=True),
             make_rule(key=2, name='allow-ssh')])

        module = make_module(base_params())
        run_main(module, mock_client)

        result = module.exit_json.call_args[1]
        assert result['changed'] is False
        assert len(result['rules']) == 2
        assert result['rules'][0]['system_rule'] is True
        assert result['rules'][1]['system_rule'] is False

    def test_filters_by_name(self):
        mock_client = MagicMock()
        mock_client.networks.get.return_value = make_network(
            [make_rule(name='a'), make_rule(key=2, name='b')])

        module = make_module(base_params(name='b'))
        run_main(module, mock_client)

        rules = module.exit_json.call_args[1]['rules']
        assert [r['name'] for r in rules] == ['b']

    def test_filters_by_direction(self):
        mock_client = MagicMock()
        mock_client.networks.get.return_value = make_network(
            [make_rule(name='in', direction='incoming'),
             make_rule(key=2, name='out', direction='outgoing')])

        module = make_module(base_params(direction='outgoing'))
        run_main(module, mock_client)

        rules = module.exit_json.call_args[1]['rules']
        assert [r['name'] for r in rules] == ['out']

    def test_reports_pending_apply(self):
        mock_client = MagicMock()
        mock_client.networks.get.return_value = make_network([], pending=True)

        module = make_module(base_params())
        run_main(module, mock_client)

        result = module.exit_json.call_args[1]
        assert result['needs_rule_apply'] is True
        assert result['rules'] == []

    def test_missing_network_fails(self):
        mock_client = MagicMock()
        mock_client.networks.get.side_effect = NotFoundError('nope')

        module = make_module(base_params())
        run_main(module, mock_client)

        module.fail_json.assert_called_once()
        assert 'not found' in module.fail_json.call_args[1]['msg']
