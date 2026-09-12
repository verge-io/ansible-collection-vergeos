#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for tenant_network_block and tenant_external_ip.

Both live in the shared vnet tables (vnet_cidrs / vnet_addresses)
scoped by owner; assignments are create/delete only at the API level,
so idempotence is match-by-key and a source-network mismatch must fail
loudly rather than guess.
"""

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
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    return obj


def make_tenant(blocks=(), ips=()):
    tenant = MagicMock()
    tenant.network_blocks.list.return_value = list(blocks)
    tenant.external_ips.list.return_value = list(ips)
    return tenant


def make_client(tenant, network_key=3):
    client = MagicMock()
    client.tenants.get.return_value = tenant
    client.networks.get.return_value = make_row({'$key': network_key,
                                                 'name': 'External'})
    return client


def make_module(params, check_mode=False):
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def run(module_name, mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.'
               'vergeos.get_vergeos_client', return_value=mock_client), \
         patch('ansible_collections.vergeio.vergeos.plugins.module_utils.'
               'vergeos.HAS_PYVERGEOS', True):
        import importlib
        mod = importlib.import_module(
            'ansible_collections.vergeio.vergeos.plugins.modules.'
            + module_name)
        with patch.object(mod, 'AnsibleModule', return_value=mock_module), \
             patch.object(mod, 'get_vergeos_client',
                          return_value=mock_client):
            with pytest.raises(SystemExit):
                mod.main()


def block_params(**overrides):
    params = {'tenant': 'customer-a', 'cidr': '203.0.113.0/28',
              'state': 'present', 'network': 'External',
              'description': None}
    params.update(overrides)
    return params


def ip_params(**overrides):
    params = {'tenant': 'customer-a', 'ip': '203.0.113.10',
              'state': 'present', 'network': 'External',
              'hostname': None, 'description': None}
    params.update(overrides)
    return params


BLOCK_ROW = {'$key': 4, 'cidr': '203.0.113.0/28', 'vnet': 3,
             'description': ''}
IP_ROW = {'$key': 9, 'ip': '203.0.113.10', 'vnet': 3, 'hostname': '',
          'description': ''}


class TestNetworkBlock:
    def test_create_when_missing(self):
        tenant = make_tenant(blocks=[])
        tenant.network_blocks.create.return_value = make_row(BLOCK_ROW)
        client = make_client(tenant)
        module = make_module(block_params())

        run('tenant_network_block', module, client)

        tenant.network_blocks.create.assert_called_once_with(
            cidr='203.0.113.0/28', network=3, description='')
        result = module.exit_json.call_args.kwargs
        assert result['changed'] is True
        assert result['network_block']['cidr'] == '203.0.113.0/28'

    def test_existing_same_network_is_converged(self):
        tenant = make_tenant(blocks=[make_row(BLOCK_ROW)])
        client = make_client(tenant)
        module = make_module(block_params())

        run('tenant_network_block', module, client)

        tenant.network_blocks.create.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_existing_other_network_fails_loudly(self):
        row = dict(BLOCK_ROW, vnet=7)
        tenant = make_tenant(blocks=[make_row(row)])
        client = make_client(tenant)
        module = make_module(block_params())

        run('tenant_network_block', module, client)

        assert 'cannot be moved' in module.fail_json.call_args.kwargs['msg']

    def test_absent_deletes(self):
        tenant = make_tenant(blocks=[make_row(BLOCK_ROW)])
        client = make_client(tenant)
        module = make_module(block_params(state='absent'))

        run('tenant_network_block', module, client)

        tenant.network_blocks.delete.assert_called_once_with(4)
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_absent_missing_is_converged(self):
        tenant = make_tenant(blocks=[])
        client = make_client(tenant)
        module = make_module(block_params(state='absent'))

        run('tenant_network_block', module, client)

        tenant.network_blocks.delete.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_check_mode_reports_without_creating(self):
        tenant = make_tenant(blocks=[])
        client = make_client(tenant)
        module = make_module(block_params(), check_mode=True)

        run('tenant_network_block', module, client)

        tenant.network_blocks.create.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_unknown_tenant_fails(self):
        client = MagicMock()
        client.tenants.get.side_effect = NotFoundError('nope')
        module = make_module(block_params())

        run('tenant_network_block', module, client)

        assert 'not found' in module.fail_json.call_args.kwargs['msg']


class TestExternalIp:
    def test_create_when_missing_passes_hostname(self):
        tenant = make_tenant(ips=[])
        tenant.external_ips.create.return_value = make_row(IP_ROW)
        client = make_client(tenant)
        module = make_module(ip_params(hostname='edge'))

        run('tenant_external_ip', module, client)

        tenant.external_ips.create.assert_called_once_with(
            ip='203.0.113.10', network=3, hostname='edge', description='')
        result = module.exit_json.call_args.kwargs
        assert result['changed'] is True
        assert result['external_ip']['ip'] == '203.0.113.10'

    def test_existing_same_network_is_converged(self):
        tenant = make_tenant(ips=[make_row(IP_ROW)])
        client = make_client(tenant)
        module = make_module(ip_params())

        run('tenant_external_ip', module, client)

        tenant.external_ips.create.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_existing_other_network_fails_loudly(self):
        row = dict(IP_ROW, vnet=7)
        tenant = make_tenant(ips=[make_row(row)])
        client = make_client(tenant)
        module = make_module(ip_params())

        run('tenant_external_ip', module, client)

        assert 'cannot be moved' in module.fail_json.call_args.kwargs['msg']

    def test_absent_deletes(self):
        tenant = make_tenant(ips=[make_row(IP_ROW)])
        client = make_client(tenant)
        module = make_module(ip_params(state='absent'))

        run('tenant_external_ip', module, client)

        tenant.external_ips.delete.assert_called_once_with(9)
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_absent_missing_is_converged(self):
        tenant = make_tenant(ips=[])
        client = make_client(tenant)
        module = make_module(ip_params(state='absent'))

        run('tenant_external_ip', module, client)

        tenant.external_ips.delete.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_check_mode_reports_without_creating(self):
        tenant = make_tenant(ips=[])
        client = make_client(tenant)
        module = make_module(ip_params(), check_mode=True)

        run('tenant_external_ip', module, client)

        tenant.external_ips.create.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_unknown_network_fails(self):
        tenant = make_tenant(ips=[])
        client = make_client(tenant)
        client.networks.get.side_effect = NotFoundError('nope')
        module = make_module(ip_params())

        run('tenant_external_ip', module, client)

        assert "Network 'External' not found" \
            in module.fail_json.call_args.kwargs['msg']
