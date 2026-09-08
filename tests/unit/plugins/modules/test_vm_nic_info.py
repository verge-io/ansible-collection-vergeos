"""Unit tests for the vm_nic_info module."""

import pytest
from unittest.mock import MagicMock, patch

from ansible_collections.vergeio.vergeos.plugins.modules import vm_nic_info


class FakeSubManager:
    def __init__(self, rows):
        self._rows = rows
        self.called_with = None

    def list(self, **kwargs):
        self.called_with = kwargs
        return list(self._rows)


class FakeVM(dict):
    def __init__(self, row, nics=()):
        super().__init__(row)
        self.nics = FakeSubManager(nics)


class FakeClient:
    def __init__(self, vm=None):
        self.vms = MagicMock()
        self.vms.get.return_value = vm


def nic(name, vnet=3, vnet_name='External', **kw):
    row = {'$key': 1, 'name': name, 'vnet': vnet, 'vnet_name': vnet_name,
           'macaddress': '52:54:00:11:22:33'}
    row.update(kw)
    return row


def params(**over):
    base = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'vm': 'web-01',
    }
    base.update(over)
    return base


def run(client, **over):
    module = MagicMock()
    module.params = params(**over)
    module.check_mode = False
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)

    with patch.object(vm_nic_info, 'AnsibleModule', return_value=module), \
         patch.object(vm_nic_info, 'get_vergeos_client', return_value=client):
        with pytest.raises(SystemExit):
            vm_nic_info.main()
    return module


def exited(module):
    assert module.exit_json.called, (
        "expected exit_json, got fail_json: %s" % (module.fail_json.call_args,))
    return module.exit_json.call_args[1]


def test_reports_the_vms_nics():
    vm = FakeVM({'machine': 18}, nics=[nic('eth0'), nic('eth1')])
    result = exited(run(FakeClient(vm)))
    assert [n['name'] for n in result['nics']] == ['eth0', 'eth1']
    assert result['machine'] == '18'


def test_an_attached_nic_is_not_detached():
    vm = FakeVM({'machine': 18}, nics=[nic('eth0')])
    assert exited(run(FakeClient(vm)))['detached'] == []


def test_a_null_vnet_counts_as_detached():
    """A real deploy produced a VM whose eth0 carried vnet=null.

    The recipe's network question was left unanswered, and every other check
    passed on that VM.
    """
    vm = FakeVM({'machine': 18}, nics=[nic('eth0', vnet=None,
                                           vnet_name=None)])
    result = exited(run(FakeClient(vm)))
    assert [n['name'] for n in result['detached']] == ['eth0']


def test_a_missing_vnet_field_counts_as_detached():
    vm = FakeVM({'machine': 18},
                nics=[{'$key': 1, 'name': 'eth0'}])
    result = exited(run(FakeClient(vm)))
    assert [n['name'] for n in result['detached']] == ['eth0']


def test_an_empty_string_vnet_counts_as_detached():
    vm = FakeVM({'machine': 18}, nics=[nic('eth0', vnet='')])
    result = exited(run(FakeClient(vm)))
    assert [n['name'] for n in result['detached']] == ['eth0']


def test_vnet_key_zero_is_not_detached():
    """0 is a key, not an absence -- `not vnet` would have got this wrong."""
    vm = FakeVM({'machine': 18}, nics=[nic('eth0', vnet=0)])
    assert exited(run(FakeClient(vm)))['detached'] == []


def test_only_the_detached_nics_are_singled_out():
    vm = FakeVM({'machine': 18}, nics=[
        nic('eth0'),
        nic('eth1', vnet=None, vnet_name=None),
        nic('eth2'),
    ])
    result = exited(run(FakeClient(vm)))
    assert len(result['nics']) == 3
    assert [n['name'] for n in result['detached']] == ['eth1']


def test_a_vm_with_no_nics_reports_empty_lists():
    vm = FakeVM({'machine': 18}, nics=[])
    result = exited(run(FakeClient(vm)))
    assert result['nics'] == []
    assert result['detached'] == []
