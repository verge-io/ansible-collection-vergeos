#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for nic module"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock

# The API stores MACs lowercase with colons and returns them that way whatever
# form was sent. Every case below is the SAME address as STORED_MAC, so a
# converged NIC must report changed=False for all of them. Issue #59 was found
# with the first one: comparing 'aa:bb:cc:00:5e:0b' against an operator's
# 'AA:BB:CC:00:5E:0B' never matches, so the module issued a PUT and reported
# changed on every run, forever.
STORED_MAC = 'aa:bb:cc:00:5e:0b'

EQUIVALENT_MACS = [
    'AA:BB:CC:00:5E:0B',   # uppercase -- the #59 reproduction
    'aa:bb:cc:00:5e:0b',   # already canonical
    'Aa:Bb:Cc:00:5e:0B',   # mixed case
    'AA-BB-CC-00-5E-0B',   # uppercase, dash-separated
    'aa-bb-cc-00-5e-0b',   # lowercase, dash-separated
    '  AA:BB:CC:00:5E:0B ',  # surrounding whitespace
]


class TestNormalizeMac:
    """Tests for the normalize_mac() helper"""

    @pytest.mark.parametrize('supplied', EQUIVALENT_MACS)
    def test_equivalent_forms_fold_to_the_stored_form(self, supplied):
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import normalize_mac
        assert normalize_mac(supplied) == STORED_MAC

    @pytest.mark.parametrize('supplied', [None, '', '   '])
    def test_empty_values_do_not_raise(self, supplied):
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import normalize_mac
        assert normalize_mac(supplied) == ''

    def test_a_different_mac_does_not_fold_to_the_same_value(self):
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import normalize_mac
        assert normalize_mac('AA:BB:CC:00:5E:0C') != STORED_MAC


def _converged_nic(make_resource, network_key=13):
    """A NIC as the API returns it: MAC lowercase, vnet resolved."""
    return make_resource({
        '$key': 7,
        'name': 'nic_0',
        'macaddress': STORED_MAC,
        'interface': 'virtio',
        'vnet': network_key,
        'enabled': True,
    })


class TestUpdateNicMacConvergence:
    """#59: a converged NIC must not report changed, whatever the MAC spelling"""

    @pytest.mark.parametrize('supplied', EQUIVALENT_MACS)
    def test_converged_nic_reports_no_change(self, supplied, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import update_nic

        module = MagicMock()
        module.params = {'mac_address': supplied, 'enabled': None, 'nic_type': None}
        module.check_mode = False

        nic = _converged_nic(make_resource)
        network = make_resource({'$key': 13, 'name': 'zz-net'})

        changed, _result = update_nic(module, MagicMock(), nic, network)

        assert changed is False, (
            "supplying %r for a NIC already storing %r reported a change; "
            "this is the #59 non-convergence" % (supplied, STORED_MAC)
        )
        nic.save.assert_not_called()

    def test_a_genuinely_different_mac_is_applied(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import update_nic

        module = MagicMock()
        module.params = {'mac_address': 'AA:BB:CC:00:5E:0C',
                         'enabled': None, 'nic_type': None}
        module.check_mode = False

        nic = _converged_nic(make_resource)
        network = make_resource({'$key': 13, 'name': 'zz-net'})

        changed, _result = update_nic(module, MagicMock(), nic, network)

        assert changed is True
        # Sent in the API's own form, not the operator's.
        nic.save.assert_called_once_with(macaddress='aa:bb:cc:00:5e:0c')

    def test_omitting_the_mac_leaves_it_alone(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import update_nic

        module = MagicMock()
        module.params = {'mac_address': None, 'enabled': None, 'nic_type': None}
        module.check_mode = False

        nic = _converged_nic(make_resource)
        network = make_resource({'$key': 13, 'name': 'zz-net'})

        changed, _result = update_nic(module, MagicMock(), nic, network)

        assert changed is False
        nic.save.assert_not_called()


class TestCreateNicMacNormalisation:
    """The create path sends the canonical form too, so the first run agrees
    with every run after it."""

    @pytest.mark.parametrize('supplied', EQUIVALENT_MACS)
    def test_create_sends_the_stored_form(self, supplied, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import create_nic

        module = MagicMock()
        module.params = {'mac_address': supplied, 'enabled': None,
                         'nic_type': 'virtio', 'network': 'zz-net'}
        module.check_mode = False

        vm = MagicMock()
        vm.nics.create.return_value = make_resource({'$key': 7, 'macaddress': STORED_MAC})

        create_nic(module, MagicMock(), vm, make_resource({'$key': 13, 'name': 'zz-net'}))

        _args, kwargs = vm.nics.create.call_args
        assert kwargs['mac_address'] == STORED_MAC

    def test_no_mac_supplied_means_no_mac_sent(self, make_resource):
        """Omitting the MAC must let the platform auto-generate one."""
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import create_nic

        module = MagicMock()
        module.params = {'mac_address': None, 'enabled': None,
                         'nic_type': 'virtio', 'network': 'zz-net'}
        module.check_mode = False

        vm = MagicMock()
        vm.nics.create.return_value = make_resource({'$key': 7})

        create_nic(module, MagicMock(), vm, make_resource({'$key': 13, 'name': 'zz-net'}))

        _args, kwargs = vm.nics.create.call_args
        assert 'mac_address' not in kwargs
