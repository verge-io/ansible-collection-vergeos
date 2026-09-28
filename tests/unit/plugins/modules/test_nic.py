#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for nic module"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock, patch

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


class TestGetNicMatchesByNetwork:
    """#118: a NIC on a different network is not this NIC."""

    def _vm(self, make_resource, *rows):
        vm = MagicMock()
        vm.nics.list.return_value = [make_resource(row) for row in rows]
        return vm

    def test_returns_the_nic_on_the_target_network(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import get_nic

        on_a = {'$key': 83, 'vnet': 13}
        on_b = {'$key': 85, 'vnet': 15}
        vm = self._vm(make_resource, on_a, on_b)

        found = get_nic(None, vm, make_resource({'$key': 15, 'name': 'netb'}))

        assert dict(found)['$key'] == 85

    def test_returns_none_when_nothing_is_on_the_target_network(self, make_resource):
        """The old fallback returned nics[0] here, which is the whole bug."""
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import get_nic

        vm = self._vm(make_resource, {'$key': 83, 'vnet': 13})

        found = get_nic(None, vm, make_resource({'$key': 15, 'name': 'netb'}))

        assert found is None

    def test_returns_none_when_the_vm_has_no_nics(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import get_nic

        vm = self._vm(make_resource)

        assert get_nic(None, vm, make_resource({'$key': 15})) is None

    def test_a_network_field_still_counts_as_the_attachment(self, make_resource):
        """The SDK has exposed the vnet key under either name."""
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import get_nic

        vm = self._vm(make_resource, {'$key': 85, 'network': 15})

        found = get_nic(None, vm, make_resource({'$key': 15, 'name': 'netb'}))

        assert dict(found)['$key'] == 85

    def test_a_missing_machine_nic_list_is_no_nic(self, make_resource):
        from ansible_collections.vergeio.vergeos.plugins.modules.nic import get_nic
        from pyvergeos.exceptions import NotFoundError

        vm = MagicMock()
        vm.nics.list.side_effect = NotFoundError('no machine')

        assert get_nic(None, vm, make_resource({'$key': 15})) is None


class NIC(dict):
    """A NIC row the module can dict(), save() and delete()."""

    def __init__(self, data, board):
        super().__init__(data)
        self._board = board

    def save(self, **fields):
        self.update(fields)
        self._board.saves.append(dict(fields))
        return self

    def delete(self):
        self._board.rows.remove(self)
        self._board.deletes.append(self['$key'])


class NICBoard:
    """The VM's NIC collection, shared across module runs."""

    def __init__(self, rows):
        self.rows = []
        self.saves = []
        self.deletes = []
        self.creates = 0
        self._next = 90
        for row in rows:
            self.rows.append(NIC(dict(row), self))

    def list(self):
        return list(self.rows)

    def create(self, **nic_data):
        self.creates += 1
        self._next += 1
        vnet = {'qa-probe-neta': 13, 'qa-probe-netb': 15}[nic_data['network']]
        nic = NIC({
            '$key': self._next,
            'name': 'nic_%d' % len(self.rows),
            'vnet': vnet,
            'enabled': nic_data.get('enabled', True),
            'interface': nic_data.get('interface', 'virtio'),
            'macaddress': nic_data.get('mac_address', ''),
        }, self)
        self.rows.append(nic)
        return nic

    def attachments(self):
        return [(n['$key'], n['vnet']) for n in self.rows]


class _VM(dict):
    def __init__(self, board):
        super().__init__({
            '$key': 4,
            'name': 'qa-probe-nic3',
            'machine': 9,
        })
        self.nics = board


class _Names:
    def __init__(self, rows):
        self._rows = list(rows)

    def list(self, **kwargs):
        return list(self._rows)


def _client(board):
    client = MagicMock()
    client.vms = _Names([_VM(board)])
    client.networks = _Names([
        {'$key': 13, 'name': 'qa-probe-neta'},
        {'$key': 15, 'name': 'qa-probe-netb'},
    ])
    return client


def _params(**over):
    base = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'vm_name': 'qa-probe-nic3',
        'network': 'qa-probe-neta',
        'state': 'present',
        'mac_address': None,
        'enabled': None,
        'nic_type': None,
        'nic_index': None,
    }
    base.update(over)
    return base


def _run(board, check_mode=False, **over):
    from ansible_collections.vergeio.vergeos.plugins.modules import nic as nic_mod

    module = MagicMock()
    module.params = _params(**over)
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)

    with patch.object(nic_mod, 'AnsibleModule', return_value=module), \
         patch.object(nic_mod, 'get_vergeos_client', return_value=_client(board)):
        with pytest.raises(SystemExit):
            nic_mod.main()
    return module


def _exited(module):
    assert module.exit_json.called, (
        "expected exit_json, got fail_json: %s" % (module.fail_json.call_args,))
    return module.exit_json.call_args[1]


class TestNicStateIsScopedToTheNamedNetwork:
    """#118, as measured: absent must not eat a neighbour, and two NICs
    declared in a loop must still be there on the second run."""

    @pytest.mark.parametrize('check_mode', [False, True])
    def test_absent_for_a_network_the_vm_is_not_on_deletes_nothing(self, check_mode):
        board = NICBoard([{'$key': 83, 'vnet': 13, 'interface': 'virtio',
                           'enabled': True}])

        result = _exited(_run(board, check_mode=check_mode,
                              network='qa-probe-netb', state='absent'))

        assert result['changed'] is False
        assert board.deletes == []
        assert board.attachments() == [(83, 13)]

    def test_absent_twice_removes_only_the_named_network(self):
        board = NICBoard([
            {'$key': 83, 'vnet': 13, 'interface': 'virtio', 'enabled': True},
            {'$key': 85, 'vnet': 15, 'interface': 'virtio', 'enabled': True},
        ])

        first = _exited(_run(board, network='qa-probe-netb', state='absent'))
        assert first['changed'] is True
        assert board.attachments() == [(83, 13)]

        second = _exited(_run(board, network='qa-probe-netb', state='absent'))
        assert second['changed'] is False
        assert board.deletes == [85]
        assert board.attachments() == [(83, 13)]

    def test_present_adds_a_nic_instead_of_repointing_another_network(self):
        board = NICBoard([{'$key': 83, 'vnet': 13, 'interface': 'virtio',
                           'enabled': True}])

        result = _exited(_run(board, network='qa-probe-netb', state='present'))

        assert result['changed'] is True
        assert board.saves == []
        assert board.creates == 1
        assert board.attachments() == [(83, 13), (91, 15)]

    def test_a_two_nic_present_loop_converges(self):
        """The shape any multi-homed VM playbook takes. The first pass adds
        both NICs; the second pass changes nothing and both networks stay."""
        board = NICBoard([])
        networks = ['qa-probe-neta', 'qa-probe-netb']

        first = [_exited(_run(board, network=name)) for name in networks]
        assert [r['changed'] for r in first] == [True, True]
        assert {vnet for _key, vnet in board.attachments()} == {13, 15}
        assert board.creates == 2
        assert board.saves == []

        second = [_exited(_run(board, network=name)) for name in networks]
        assert [r['changed'] for r in second] == [False, False]
        assert {vnet for _key, vnet in board.attachments()} == {13, 15}
        assert board.creates == 2
        assert board.deletes == []

    def test_nic_index_moves_that_nic_and_then_converges(self):
        """The OVA case: one imported NIC, moved onto the target network
        only because the play asked for that index."""
        board = NICBoard([{'$key': 83, 'vnet': 13, 'interface': 'virtio',
                           'enabled': True}])

        moved = _exited(_run(board, network='qa-probe-netb', nic_index=0))
        assert moved['changed'] is True
        assert board.creates == 0
        assert board.saves == [{'vnet': 15}]
        assert board.attachments() == [(83, 15)]

        again = _exited(_run(board, network='qa-probe-netb', nic_index=0))
        assert again['changed'] is False
        assert board.creates == 0
        assert board.attachments() == [(83, 15)]

    def test_nic_index_is_ignored_once_the_target_network_already_has_a_nic(self):
        board = NICBoard([
            {'$key': 83, 'vnet': 13, 'interface': 'virtio', 'enabled': True},
            {'$key': 85, 'vnet': 15, 'interface': 'virtio', 'enabled': True},
        ])

        result = _exited(_run(board, network='qa-probe-netb', nic_index=0))

        assert result['changed'] is False
        assert board.saves == []
        assert board.attachments() == [(83, 13), (85, 15)]

    @pytest.mark.parametrize('index', [1, -1])
    def test_nic_index_out_of_range_fails_and_moves_nothing(self, index):
        board = NICBoard([{'$key': 83, 'vnet': 13, 'interface': 'virtio',
                           'enabled': True}])

        module = _run(board, network='qa-probe-netb', nic_index=index)

        assert module.fail_json.called
        assert not module.exit_json.called
        assert board.creates == 0
        assert board.saves == []
        assert board.attachments() == [(83, 13)]

    def test_nic_index_with_absent_is_refused(self):
        board = NICBoard([{'$key': 83, 'vnet': 13, 'interface': 'virtio',
                           'enabled': True}])

        module = _run(board, network='qa-probe-netb', state='absent',
                      nic_index=0)

        assert module.fail_json.called
        assert not module.exit_json.called
        assert board.deletes == []
        assert board.attachments() == [(83, 13)]
