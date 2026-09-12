#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vnet_rule module"""

import pytest
from unittest.mock import MagicMock, patch


from pyvergeos.exceptions import (  # real classes: a Mock here is
    APIError,                       # CALLED as a side_effect, not raised
    AuthenticationError,
    NotFoundError,
    ValidationError,
    VergeConnectionError,
)


def make_row(data):
    """Mock a mapping-protocol SDK resource row (explicit keys +
    __getitem__ so dict(mock) decodes instead of returning {})."""
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    return obj


def make_network(rules=None, running=True):
    data = {'$key': 3, 'name': 'Internal', 'running': running}
    net = MagicMock()
    net.keys.return_value = list(data.keys())
    net.__getitem__.side_effect = lambda k: data[k]
    net.rules.list.return_value = rules or []
    return net


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
        'network': 'Internal',
        'name': 'allow-ssh',
        'state': 'present',
        'direction': 'incoming',
        'rule_action': 'accept',
        'protocol': 'tcp',
        'interface': 'auto',
        'source_ip': None,
        'source_ports': None,
        'destination_ip': None,
        'destination_ports': '22',
        'target_ip': None,
        'target_ports': None,
        'enabled': True,
        'log': False,
        'statistics': False,
        'order': None,
        'pin': None,
        'description': None,
        'apply': True,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.vnet_rule.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vnet_rule.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.vnet_rule.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    vnet_rule as vnet_rule_module,
                )
                try:
                    vnet_rule_module.main()
                except SystemExit:
                    pass


RULE_DATA = {
    '$key': 12,
    'name': 'allow-ssh',
    'direction': 'incoming',
    'action': 'accept',
    'protocol': 'tcp',
    'interface': 'auto',
    'source_ip': None,
    'source_ports': None,
    'destination_ip': None,
    'destination_ports': '22',
    'target_ip': None,
    'target_ports': None,
    'enabled': True,
    'log': False,
    'statistics': False,
    'orderid': 5,
    'description': None,
    'system_rule': False,
}


class TestVnetRulePresent:
    def test_creates_when_missing(self):
        mock_client = MagicMock()
        network = make_network()
        network.rules.create.return_value = make_row(dict(RULE_DATA))
        mock_client.networks.get.return_value = network

        module = make_module(base_params())
        run_main(module, mock_client)

        network.rules.create.assert_called_once_with(
            name='allow-ssh', direction='incoming', action='accept',
            protocol='tcp', interface='auto', enabled=True, log=False,
            statistics=False, destination_ports='22')
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert "created rule 'allow-ssh'" in result['actions']

    def test_no_change_when_rule_matches(self):
        mock_client = MagicMock()
        network = make_network(rules=[make_row(dict(RULE_DATA))])
        mock_client.networks.get.return_value = network

        module = make_module(base_params())
        run_main(module, mock_client)

        network.rules.create.assert_not_called()
        network.rules.update.assert_not_called()
        network.apply_rules.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_updates_drifted_ports(self):
        mock_client = MagicMock()
        existing = make_row(dict(RULE_DATA))
        network = make_network(rules=[existing])
        network.rules.update.return_value = existing
        mock_client.networks.get.return_value = network

        module = make_module(base_params(destination_ports='2222'))
        run_main(module, mock_client)

        network.rules.update.assert_called_once_with(
            12, destination_ports='2222')
        assert module.exit_json.call_args[1]['changed'] is True

    def test_translate_rule_passes_targets(self):
        mock_client = MagicMock()
        network = make_network()
        network.rules.create.return_value = make_row(dict(RULE_DATA))
        mock_client.networks.get.return_value = network

        module = make_module(base_params(
            name='fwd-tls', rule_action='translate',
            destination_ports='8443', target_ip='10.0.0.15',
            target_ports='443'))
        run_main(module, mock_client)

        kwargs = network.rules.create.call_args[1]
        assert kwargs['action'] == 'translate'
        assert kwargs['target_ip'] == '10.0.0.15'
        assert kwargs['target_ports'] == '443'

    def test_fails_when_network_missing(self):
        mock_client = MagicMock()
        mock_client.networks.get.side_effect = NotFoundError('nope')

        module = make_module(base_params())
        run_main(module, mock_client)

        module.fail_json.assert_called_once()
        assert 'not found' in module.fail_json.call_args[1]['msg']

    def test_check_mode_writes_nothing(self):
        mock_client = MagicMock()
        network = make_network()
        mock_client.networks.get.return_value = network

        module = make_module(base_params(), check_mode=True)
        run_main(module, mock_client)

        network.rules.create.assert_not_called()
        network.apply_rules.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert "created rule 'allow-ssh'" in result['actions']


class TestVnetRuleApply:
    def test_apply_called_after_change(self):
        mock_client = MagicMock()
        network = make_network()
        network.rules.create.return_value = make_row(dict(RULE_DATA))
        mock_client.networks.get.return_value = network

        module = make_module(base_params())
        run_main(module, mock_client)

        network.apply_rules.assert_called_once()
        assert "applied rules on 'Internal'" in \
            module.exit_json.call_args[1]['actions']

    def test_apply_false_skips_apply(self):
        mock_client = MagicMock()
        network = make_network()
        network.rules.create.return_value = make_row(dict(RULE_DATA))
        mock_client.networks.get.return_value = network

        module = make_module(base_params(apply=False))
        run_main(module, mock_client)

        network.apply_rules.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_no_apply_when_no_change(self):
        mock_client = MagicMock()
        network = make_network(rules=[make_row(dict(RULE_DATA))])
        mock_client.networks.get.return_value = network

        module = make_module(base_params())
        run_main(module, mock_client)

        network.apply_rules.assert_not_called()

    def test_apply_skipped_when_network_stopped(self):
        mock_client = MagicMock()
        network = make_network(running=False)
        network.rules.create.return_value = make_row(dict(RULE_DATA))
        mock_client.networks.get.return_value = network

        module = make_module(base_params())
        run_main(module, mock_client)

        network.apply_rules.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert any('apply skipped' in a for a in result['actions'])


class TestVnetRuleSystem:
    def test_refuses_to_update_system_rule(self):
        mock_client = MagicMock()
        system = dict(RULE_DATA)
        system['system_rule'] = True
        network = make_network(rules=[make_row(system)])
        mock_client.networks.get.return_value = network

        module = make_module(base_params(destination_ports='2222'))
        run_main(module, mock_client)

        module.fail_json.assert_called_once()
        assert 'system rule' in module.fail_json.call_args[1]['msg']

    def test_refuses_to_delete_system_rule(self):
        mock_client = MagicMock()
        system = dict(RULE_DATA)
        system['system_rule'] = True
        network = make_network(rules=[make_row(system)])
        mock_client.networks.get.return_value = network

        module = make_module(base_params(state='absent'))
        run_main(module, mock_client)

        network.rules.delete.assert_not_called()
        module.fail_json.assert_called_once()


class TestVnetRuleAbsent:
    def test_deletes_existing_rule_and_applies(self):
        mock_client = MagicMock()
        network = make_network(rules=[make_row(dict(RULE_DATA))])
        mock_client.networks.get.return_value = network

        module = make_module(base_params(state='absent'))
        run_main(module, mock_client)

        network.rules.delete.assert_called_once_with(12)
        network.apply_rules.assert_called_once()
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert "deleted rule 'allow-ssh'" in result['actions']

    def test_absent_missing_no_change_no_apply(self):
        mock_client = MagicMock()
        network = make_network()
        mock_client.networks.get.return_value = network

        module = make_module(base_params(state='absent'))
        run_main(module, mock_client)

        network.rules.delete.assert_not_called()
        network.apply_rules.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_check_mode_deletes_nothing(self):
        mock_client = MagicMock()
        network = make_network(rules=[make_row(dict(RULE_DATA))])
        mock_client.networks.get.return_value = network

        module = make_module(base_params(state='absent'), check_mode=True)
        run_main(module, mock_client)

        network.rules.delete.assert_not_called()
        network.apply_rules.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True
