#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vnet_apply module"""

import pytest
from unittest.mock import MagicMock, patch


from pyvergeos.exceptions import (  # real classes: a Mock here is
    APIError,                       # CALLED as a side_effect, not raised
    AuthenticationError,
    NotFoundError,
    ValidationError,
    VergeConnectionError,
)


def make_network(pending=False, running=True):
    data = {'$key': 3, 'name': 'Internal', 'running': running,
            'need_fw_apply': pending}
    net = MagicMock()
    net.keys.return_value = list(data.keys())
    net.__getitem__.side_effect = lambda k: data[k]
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
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.vnet_apply.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.vnet_apply.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.vnet_apply.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    vnet_apply as vnet_apply_module,
                )
                try:
                    vnet_apply_module.main()
                except SystemExit:
                    pass


class TestVnetApply:
    def test_no_change_when_nothing_pending(self):
        mock_client = MagicMock()
        network = make_network(pending=False)
        mock_client.networks.get.return_value = network

        module = make_module(base_params())
        run_main(module, mock_client)

        network.apply_rules.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is False
        assert result['pending'] is False

    def test_applies_when_pending_and_running(self):
        mock_client = MagicMock()
        network = make_network(pending=True, running=True)
        mock_client.networks.get.return_value = network

        module = make_module(base_params())
        run_main(module, mock_client)

        network.apply_rules.assert_called_once()
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert result['applied'] is True

    def test_skips_when_pending_but_stopped(self):
        mock_client = MagicMock()
        network = make_network(pending=True, running=False)
        mock_client.networks.get.return_value = network

        module = make_module(base_params())
        run_main(module, mock_client)

        network.apply_rules.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is False
        assert result['pending'] is True
        assert 'not running' in result['msg']

    def test_stopped_network_does_not_claim_nothing_is_pending(self):
        """A stopped network is the realistic case, and it must say so.

        The platform never sets need_fw_apply on a stopped vnet -- there is no
        running router to apply rules to -- so the real state after staging a
        policy against a stopped network is pending=False, running=False
        (verified live on 26.1.8).

        The old code tested `pending` first, so this state reported "No pending
        rule changes": indistinguishable from a converged network, and it hid
        the fact that a freshly staged policy was not live. The dedicated
        "not running" branch existed but was unreachable. See issue #19.
        """
        mock_client = MagicMock()
        network = make_network(pending=False, running=False)
        mock_client.networks.get.return_value = network

        module = make_module(base_params())
        run_main(module, mock_client)

        network.apply_rules.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is False
        assert 'not running' in result['msg']
        assert 'No pending rule changes' not in result['msg']

    def test_check_mode_reports_but_does_not_apply(self):
        mock_client = MagicMock()
        network = make_network(pending=True, running=True)
        mock_client.networks.get.return_value = network

        module = make_module(base_params(), check_mode=True)
        run_main(module, mock_client)

        network.apply_rules.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert result['applied'] is False

    def test_fails_when_network_missing(self):
        mock_client = MagicMock()
        mock_client.networks.get.side_effect = NotFoundError('nope')

        module = make_module(base_params())
        run_main(module, mock_client)

        module.fail_json.assert_called_once()
        assert 'not found' in module.fail_json.call_args[1]['msg']
