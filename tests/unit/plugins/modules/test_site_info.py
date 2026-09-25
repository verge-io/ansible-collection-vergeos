#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the site_info module."""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from unittest.mock import MagicMock, patch

from pyvergeos.exceptions import (
    APIError,
    AuthenticationError,
    VergeConnectionError,
)


def make_module():
    module = MagicMock()
    module.params = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
    }
    module.check_mode = False
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def make_client():
    client = MagicMock()
    client.os_version = '26.1.8'
    client.version = '26.1.8'
    client.cloud_name = 'example-lab'
    client.storage_tiers.list.return_value = [
        {'$key': 9, 'tier': 4, 'capacity': 0, 'used': 0, 'description': ''},
        {'$key': 1, 'tier': 1, 'capacity': 100, 'used': 40, 'description': 'fast'},
    ]
    client.clusters.list.return_value = [
        {'$key': 1, 'name': 'cluster1',
         'online_ram': 1, 'used_ram': 1},
    ]
    client._request.return_value = [
        {'cluster': 1, 'online_ram': 137472, 'used_ram': 9216,
         'online_nodes': 2, 'total_nodes': 2,
         'online_cores': 64, 'used_cores': 5},
    ]
    client.nodes.list.return_value = [
        {'name': 'node2', '$key': 2, 'cluster': 1, 'cluster_name': 'cluster1',
         'ram': 94208, 'vm_ram': 69120, 'ram_used': 8016, 'cores': 32,
         'physical': True, 'running': True, 'maintenance': False},
        {'name': 'node1', '$key': 1, 'cluster': 1, 'cluster_name': 'cluster1',
         'ram': 94208, 'vm_ram': 68352, 'ram_used': 14601, 'cores': 32,
         'physical': True, 'running': True, 'maintenance': False},
    ]
    client.networks.list.return_value = [{'name': 'DMZ'}, {'name': 'Core'}]
    client.vm_recipes.list.return_value = [
        {'name': 'Ubuntu'}, {'name': 'Rocky'}, {'$key': 3},
    ]
    client.nas_services.list.return_value = [{'name': 'nas1'}, {'name': 'nas2'}]
    return client


def run_main(module, client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.site_info.AnsibleModule',
               return_value=module), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.site_info.get_vergeos_client',
               return_value=client), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.site_info.HAS_PYVERGEOS',
               True):
        from ansible_collections.vergeio.vergeos.plugins.modules import site_info
        try:
            site_info.main()
        except SystemExit:
            pass


class TestSiteInfo:
    def test_reports_the_live_system(self):
        client = make_client()
        module = make_module()
        run_main(module, client)

        result = module.exit_json.call_args[1]
        assert result['changed'] is False
        assert result['os_version'] == '26.1.8'
        assert result['version'] == '26.1.8'
        assert result['cloud_name'] == 'example-lab'
        # tier 4 has no capacity and its row key is 9. The number is 4.
        assert result['storage_tiers'] == [1, 4]
        assert result['storage_tier_details'][1]['tier'] == 4
        assert result['storage_tier_details'][1]['$key'] == 9
        assert result['storage_tier_details'][1]['capacity'] == 0
        assert result['ram_headroom_mb'] == 128256
        assert result['clusters'][0]['ram_headroom_mb'] == 128256
        assert result['clusters'][0]['online_ram'] == 137472
        assert [node['name'] for node in result['nodes']] == ['node1', 'node2']
        assert result['nodes'][0]['vm_ram'] == 68352
        assert result['nodes'][0]['ram_used'] == 14601
        assert result['largest_node_vm_ram_mb'] == 69120
        assert result['counts'] == {
            'networks': 2, 'vm_recipes': 3, 'nas_services': 2}
        assert result['names']['networks'] == ['Core', 'DMZ']
        assert result['names']['vm_recipes'] == ['Rocky', 'Ubuntu']
        assert result['names']['nas_services'] == ['nas1', 'nas2']

    def test_tiers_come_from_storage_tiers(self):
        client = make_client()
        module = make_module()
        run_main(module, client)

        client.storage_tiers.list.assert_called_once_with()
        client.physical_drives.list.assert_not_called()

    def test_empty_tier_table_is_an_empty_list(self):
        client = make_client()
        client.storage_tiers.list.return_value = []
        module = make_module()
        run_main(module, client)

        result = module.exit_json.call_args[1]
        assert result['storage_tiers'] == []
        assert result['storage_tier_details'] == []
        assert module.fail_json.call_count == 0

    def test_api_error_fails_the_module(self):
        client = make_client()
        client.storage_tiers.list.side_effect = APIError('storage tiers unavailable')
        module = make_module()
        run_main(module, client)

        module.exit_json.assert_not_called()
        assert 'API error' in module.fail_json.call_args[1]['msg']

    def test_authentication_error_fails_the_module(self):
        client = make_client()
        client.nodes.list.side_effect = AuthenticationError('denied')
        module = make_module()
        run_main(module, client)

        assert 'Authentication failed' in module.fail_json.call_args[1]['msg']

    def test_connection_error_fails_the_module(self):
        client = make_client()
        client.networks.list.side_effect = VergeConnectionError('down')
        module = make_module()
        run_main(module, client)

        assert 'Connection failed' in module.fail_json.call_args[1]['msg']

    def test_unexpected_error_fails_the_module(self):
        client = make_client()
        client.nas_services.list.side_effect = RuntimeError('boom')
        module = make_module()
        run_main(module, client)

        assert module.fail_json.call_args[1]['msg'] == 'Unexpected error: boom'
