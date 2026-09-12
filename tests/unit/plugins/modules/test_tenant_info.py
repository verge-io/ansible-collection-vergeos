#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for tenant_info module"""

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


def make_tenant(data, nodes=None, storage=None):
    obj = make_row(data)
    obj.nodes.list.return_value = nodes or []
    obj.storage.list.return_value = storage or []
    return obj


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
        'name': None,
        'include_allocations': True,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.tenant_info.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.tenant_info.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.tenant_info.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    tenant_info as tenant_info_module,
                )
                try:
                    tenant_info_module.main()
                except SystemExit:
                    pass


TENANT_DATA = {
    '$key': 7,
    'name': 'acme',
    'description': 'ACME Corp',
    'url': None,
    'note': None,
    'expose_cloud_snapshots': True,
    'allow_branding': False,
    'running': True,
    'status': 'online',
    'state': 'online',
    'is_snapshot': False,
}


class TestTenantInfo:
    def test_lists_all_tenants(self):
        mock_client = MagicMock()
        mock_client.tenants.list.return_value = [
            make_tenant(dict(TENANT_DATA)),
            make_tenant({**TENANT_DATA, '$key': 8, 'name': 'globex'}),
        ]

        module = make_module(base_params())
        run_main(module, mock_client)

        result = module.exit_json.call_args[1]
        assert result['changed'] is False
        assert [t['name'] for t in result['tenants']] == ['acme', 'globex']

    def test_single_tenant_by_name_with_allocations(self):
        mock_client = MagicMock()
        node = make_row({'name': 'n1', 'cpu_cores': 8, 'ram': 32768,
                         'running': True})
        alloc = make_row({'tier_number': 3,
                          'provisioned': 100 * 1073741824,
                          'used': 50 * 1073741824})
        mock_client.tenants.get.return_value = make_tenant(
            dict(TENANT_DATA), nodes=[node], storage=[alloc])

        module = make_module(base_params(name='acme'))
        run_main(module, mock_client)

        mock_client.tenants.get.assert_called_once_with(name='acme')
        tenants = module.exit_json.call_args[1]['tenants']
        assert len(tenants) == 1
        assert tenants[0]['nodes'] == [
            {'name': 'n1', 'cpu_cores': 8, 'ram_gb': 32.0, 'running': True}]
        assert tenants[0]['storage'] == [
            {'tier': 3, 'provisioned_gb': 100.0, 'used_gb': 50.0}]

    def test_missing_name_returns_empty_list(self):
        mock_client = MagicMock()
        mock_client.tenants.get.side_effect = NotFoundError('nope')

        module = make_module(base_params(name='ghost'))
        run_main(module, mock_client)

        assert module.exit_json.call_args[1]['tenants'] == []

    def test_include_allocations_false_skips_subresources(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA))
        mock_client.tenants.list.return_value = [tenant]

        module = make_module(base_params(include_allocations=False))
        run_main(module, mock_client)

        tenant.nodes.list.assert_not_called()
        tenant.storage.list.assert_not_called()
        tenants = module.exit_json.call_args[1]['tenants']
        assert 'nodes' not in tenants[0]
        assert 'storage' not in tenants[0]
