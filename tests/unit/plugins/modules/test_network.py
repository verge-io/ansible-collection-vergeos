#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the network module's uplink and rate-limit handling"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock


def _params(**overrides):
    """Module params with every managed key present and unset.

    The module skips any parameter that is None, so a test that omits a key
    entirely would exercise a different branch than one that sets it to None.
    """
    base = {
        'name': 'zz-net', 'description': None, 'network_type': None,
        'ip_address': None, 'network': None, 'gateway': None,
        'dhcp_enabled': None, 'dhcp_start': None, 'dhcp_end': None,
        'layer2_type': None, 'vlan_id': None, 'dns_servers': None,
        'domain': None, 'mtu': None, 'on_power_loss': None,
        'interface_network': None, 'rate_limit': None,
    }
    base.update(overrides)
    return base


def _module(**overrides):
    m = MagicMock()
    m.params = _params(**overrides)
    m.check_mode = False
    return m


def _client_with_uplink(make_resource, name='ext1 Switch', key=5):
    client = MagicMock()
    client.networks.list.return_value = [
        make_resource({'$key': key, 'name': name, 'type': 'physical'})]
    return client


class TestResolveInterfaceVnet:
    """#60: operators give a vnet name; the API stores that vnet's key."""

    def test_omitted_returns_none_meaning_leave_alone(self):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import resolve_interface_vnet
        assert resolve_interface_vnet(_module(), MagicMock()) is None

    def test_empty_string_returns_empty_meaning_detach(self):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import resolve_interface_vnet
        assert resolve_interface_vnet(_module(interface_network=''), MagicMock()) == ''

    def test_a_name_resolves_to_the_key(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import resolve_interface_vnet
        client = _client_with_uplink(make_resource)
        assert resolve_interface_vnet(_module(interface_network='ext1 Switch'), client) == 5
        client.networks.list.assert_called_once()

    def test_a_missing_uplink_fails_by_name(self):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import resolve_interface_vnet
        from pyvergeos.exceptions import NotFoundError

        client = MagicMock()
        client.networks.list.return_value = []
        module = _module(interface_network='does-not-exist')
        module.fail_json.side_effect = SystemExit(1)

        with pytest.raises(SystemExit):
            resolve_interface_vnet(module, client)

        msg = module.fail_json.call_args[1]['msg']
        assert 'does-not-exist' in msg, "the failure must name the uplink asked for"


class TestCreateSendsUplinkAndRateLimit:

    def test_uplink_is_sent_as_interface_vnet(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import build_network_data
        data = build_network_data(_module(interface_network='ext1 Switch'), uplink_key=5)
        assert data['interface_vnet'] == 5

    def test_no_uplink_means_the_field_is_not_sent(self):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import build_network_data
        assert 'interface_vnet' not in build_network_data(_module(), uplink_key=None)

    @pytest.mark.parametrize('value', [100, 0])
    def test_rate_limit_is_sent_including_zero(self, value):
        """#61: 0 is an explicit 'uncapped', not the same as omitting it."""
        from ansible_collections.vergeio.vergeos.plugins.modules.network import build_network_data
        assert build_network_data(_module(rate_limit=value))['rate_limit'] == value

    def test_omitted_rate_limit_is_not_sent(self):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import build_network_data
        assert 'rate_limit' not in build_network_data(_module())


class TestUpdateConvergence:
    """Both issues require a second run to report changed=false."""

    def _existing(self, make_resource, **fields):
        base = {'$key': 20, 'name': 'zz-net', 'interface_vnet': 5,
                'rate_limit': 100, 'dnslist': '', 'dhcp_stop': ''}
        base.update(fields)
        return make_resource(base)

    def test_matching_uplink_reports_no_change(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import update_network
        client = _client_with_uplink(make_resource)
        network = self._existing(make_resource)
        changed, _result = update_network(_module(interface_network='ext1 Switch'), client, network)
        assert changed is False
        network.save.assert_not_called()

    def test_uplink_stored_as_a_string_key_still_converges(self, make_resource):
        """The API may return the key as a string; a converged uplink must not
        read as a change just because of its type."""
        from ansible_collections.vergeio.vergeos.plugins.modules.network import update_network
        client = _client_with_uplink(make_resource)
        network = self._existing(make_resource, interface_vnet='5')
        changed, _result = update_network(_module(interface_network='ext1 Switch'), client, network)
        assert changed is False

    def test_a_different_uplink_is_applied(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import update_network
        client = _client_with_uplink(make_resource, name='ext2 Switch', key=6)
        network = self._existing(make_resource)
        changed, _result = update_network(_module(interface_network='ext2 Switch'), client, network)
        assert changed is True
        network.save.assert_called_once_with(interface_vnet=6)

    def test_detaching_is_applied(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import update_network
        network = self._existing(make_resource)
        changed, _result = update_network(_module(interface_network=''), MagicMock(), network)
        assert changed is True
        network.save.assert_called_once_with(interface_vnet='')

    def test_detaching_an_already_detached_network_reports_no_change(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import update_network
        network = self._existing(make_resource, interface_vnet='')
        changed, _result = update_network(_module(interface_network=''), MagicMock(), network)
        assert changed is False

    def test_matching_rate_limit_reports_no_change(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import update_network
        network = self._existing(make_resource)
        changed, _result = update_network(_module(rate_limit=100), MagicMock(), network)
        assert changed is False

    def test_a_different_rate_limit_is_applied(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import update_network
        network = self._existing(make_resource)
        changed, _result = update_network(_module(rate_limit=250), MagicMock(), network)
        assert changed is True
        network.save.assert_called_once_with(rate_limit=250)

    def test_clearing_the_cap_with_zero_is_applied(self, make_resource):
        """#61: an unset cap and a cap of zero must not be confused."""
        from ansible_collections.vergeio.vergeos.plugins.modules.network import update_network
        network = self._existing(make_resource)
        changed, _result = update_network(_module(rate_limit=0), MagicMock(), network)
        assert changed is True
        network.save.assert_called_once_with(rate_limit=0)

    def test_omitting_the_cap_leaves_it_alone(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import update_network
        network = self._existing(make_resource)
        changed, _result = update_network(_module(), MagicMock(), network)
        assert changed is False
        network.save.assert_not_called()


class TestFieldMapsStayHonest:
    """The compare-and-map class behind #8, #10, #18 and #59."""

    def test_both_new_fields_are_in_the_update_map(self):
        from ansible_collections.vergeio.vergeos.plugins.modules.network import UPDATE_FIELD_MAP
        assert UPDATE_FIELD_MAP['rate_limit'] == 'rate_limit'

    def test_the_uplink_field_is_fetched_for_comparison(self):
        """A field the fetch never returns reads as None, so the module
        reports changed forever -- that was #18."""
        from ansible_collections.vergeio.vergeos.plugins.modules.network import (
            COMPARISON_FIELDS, UPDATE_FIELD_MAP, UPLINK_API_FIELD)
        assert UPLINK_API_FIELD in COMPARISON_FIELDS
        for api_field in UPDATE_FIELD_MAP.values():
            assert api_field in COMPARISON_FIELDS, (
                "%s is diffed but never fetched" % api_field)
