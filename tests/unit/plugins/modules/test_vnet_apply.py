#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vnet_apply module"""

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


def make_network(pending=False, running=True):
    """A vnet row as the module asks for it.

    `running` is NOT a vnet column -- it is a join through the router machine,
    which the SDK requests as "machine#status#running as running". A raw
    GET /vnets?fields=all does not return it, measured on 26.1.8. The module
    therefore has to name the join in its projection, and this row models what
    comes back when it does. If `running` ever read as None, every network
    would look stopped and the module would never apply anything while
    reporting success.
    """
    data = {'$key': 3, 'name': 'Internal', 'running': running,
            'need_fw_apply': pending}
    net = MagicMock()
    net.keys.side_effect = lambda: list(data.keys())
    net.__getitem__.side_effect = data.__getitem__
    net.__iter__.side_effect = lambda: iter(data)
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
        mock_client.networks.list.return_value = [network]

        module = make_module(base_params())
        run_main(module, mock_client)

        network.apply_rules.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is False
        assert result['pending'] is False

    def test_applies_when_pending_and_running(self):
        mock_client = MagicMock()
        network = make_network(pending=True, running=True)
        mock_client.networks.list.return_value = [network]

        module = make_module(base_params())
        run_main(module, mock_client)

        network.apply_rules.assert_called_once()
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert result['applied'] is True

    def test_skips_when_pending_but_stopped(self):
        mock_client = MagicMock()
        network = make_network(pending=True, running=False)
        mock_client.networks.list.return_value = [network]

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
        mock_client.networks.list.return_value = [network]

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
        mock_client.networks.list.return_value = [network]

        module = make_module(base_params(), check_mode=True)
        run_main(module, mock_client)

        network.apply_rules.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert result['applied'] is False

    def test_fails_when_network_missing(self):
        mock_client = MagicMock()
        mock_client.networks.list.return_value = []

        module = make_module(base_params())
        run_main(module, mock_client)

        module.fail_json.assert_called_once()
        assert 'not found' in module.fail_json.call_args[1]['msg']


class TestTheProjectionNamesTheJoin:
    """The bug this module is one typo away from.

    `running` is a join, not a column. If it were requested by a name the API
    does not answer, it would read as None on every network, the module would
    take the "not running" branch every time, and it would report success
    while applying nothing. That is a silent failure of the module's entire
    purpose, so the projection is pinned rather than assumed.
    """

    def test_the_join_is_requested_by_its_alias_form(self):
        from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos \
            import VNET_STATUS_FIELDS
        assert 'machine#status#running as running' in VNET_STATUS_FIELDS
        assert 'need_fw_apply' in VNET_STATUS_FIELDS

    def test_the_lookup_uses_that_projection(self):
        from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos \
            import VNET_STATUS_FIELDS
        mock_client = MagicMock()
        mock_client.networks.list.return_value = [make_network()]
        run_main(make_module(base_params()), mock_client)

        assert (mock_client.networks.list.call_args.kwargs['fields']
                == VNET_STATUS_FIELDS)


class TestSdkErrorsAreHandled:
    """Every SDK exception the module imports must reach a named failure
    rather than the catch-all, which reads like a bug in the collection
    instead of a server saying no. Parametrising the import list also keeps
    the imports honest -- these five were imported and never used."""

    @pytest.mark.parametrize('exc', [
        APIError, AuthenticationError, ValidationError, VergeConnectionError,
    ])
    def test_sdk_errors_do_not_fall_through_to_unexpected(self, exc):
        mock_client = MagicMock()
        mock_client.networks.list.side_effect = exc('server said no')
        module = make_module(base_params())

        run_main(module, mock_client)

        module.fail_json.assert_called_once()
        assert 'Unexpected error' not in module.fail_json.call_args[1]['msg']

    def test_a_missing_network_is_named(self):
        mock_client = MagicMock()
        mock_client.networks.list.return_value = []
        module = make_module(base_params())

        run_main(module, mock_client)

        assert "Network 'Internal' not found" in module.fail_json.call_args[1]['msg']

    def test_duplicate_network_names_are_refused(self):
        """Applying firewall rules to the wrong vnet is not a small mistake."""
        mock_client = MagicMock()
        mock_client.networks.list.return_value = [make_network(),
                                                  make_network()]
        module = make_module(base_params())

        run_main(module, mock_client)

        assert 'refusing to guess' in module.fail_json.call_args[1]['msg']
