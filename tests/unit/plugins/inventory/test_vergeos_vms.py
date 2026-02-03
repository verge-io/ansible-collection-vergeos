#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for vergeos_vms inventory plugin"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


@pytest.fixture(autouse=True)
def mock_pyvergeos():
    """Mock pyvergeos SDK for all tests"""
    mock_exceptions = MagicMock()
    mock_exceptions.NotFoundError = Exception
    mock_exceptions.AuthenticationError = Exception
    mock_exceptions.ValidationError = Exception
    mock_exceptions.APIError = Exception
    mock_exceptions.VergeConnectionError = Exception

    with patch.dict('sys.modules', {
        'pyvergeos': MagicMock(),
        'pyvergeos.exceptions': mock_exceptions,
    }):
        yield


@pytest.fixture
def inventory_module():
    """Create an inventory module instance for testing"""
    with patch('ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms.HAS_PYVERGEOS', True):
        from ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms import InventoryModule
        module = InventoryModule()
        module.inventory = MagicMock()
        module.display = MagicMock()
        module._options = {}
        module.get_option = lambda key: module._options.get(key)
        return module


class TestVerifyFile:
    """Tests for verify_file method"""

    def test_accepts_vergeos_vms_yml(self, inventory_module):
        """Test that .vergeos_vms.yml extension is accepted"""
        with patch.object(inventory_module.__class__.__bases__[0], 'verify_file', return_value=True):
            assert inventory_module.verify_file('/path/to/inventory.vergeos_vms.yml') is True

    def test_accepts_vergeos_vms_yaml(self, inventory_module):
        """Test that .vergeos_vms.yaml extension is accepted"""
        with patch.object(inventory_module.__class__.__bases__[0], 'verify_file', return_value=True):
            assert inventory_module.verify_file('/path/to/inventory.vergeos_vms.yaml') is True

    def test_rejects_wrong_extension(self, inventory_module):
        """Test that wrong extensions are rejected"""
        with patch.object(inventory_module.__class__.__bases__[0], 'verify_file', return_value=True):
            assert inventory_module.verify_file('/path/to/inventory.yml') is False
            assert inventory_module.verify_file('/path/to/inventory.vergeos.yml') is False

    def test_rejects_invalid_file(self, inventory_module):
        """Test that invalid files are rejected"""
        with patch.object(inventory_module.__class__.__bases__[0], 'verify_file', return_value=False):
            assert inventory_module.verify_file('/path/to/inventory.vergeos_vms.yml') is False


class TestSanitizeGroupName:
    """Tests for _sanitize_group_name method"""

    def test_replaces_special_characters(self, inventory_module):
        """Test that special characters are replaced with underscores"""
        assert inventory_module._sanitize_group_name('my-group') == 'my_group'
        assert inventory_module._sanitize_group_name('my.group') == 'my_group'
        assert inventory_module._sanitize_group_name('my group') == 'my_group'
        assert inventory_module._sanitize_group_name('my@group!') == 'my_group_'

    def test_handles_leading_numbers(self, inventory_module):
        """Test that leading numbers are prefixed with underscore"""
        assert inventory_module._sanitize_group_name('123group') == '_123group'
        assert inventory_module._sanitize_group_name('1') == '_1'

    def test_lowercase_conversion(self, inventory_module):
        """Test that names are converted to lowercase"""
        assert inventory_module._sanitize_group_name('MyGroup') == 'mygroup'
        assert inventory_module._sanitize_group_name('UPPERCASE') == 'uppercase'

    def test_preserves_valid_characters(self, inventory_module):
        """Test that valid characters are preserved"""
        assert inventory_module._sanitize_group_name('valid_name_123') == 'valid_name_123'

    def test_handles_empty_and_numeric_input(self, inventory_module):
        """Test edge cases"""
        assert inventory_module._sanitize_group_name('') == ''
        assert inventory_module._sanitize_group_name(123) == '_123'


class TestGetHostname:
    """Tests for _get_hostname method"""

    def test_default_template(self, inventory_module):
        """Test hostname generation with default template"""
        inventory_module._options['hostname_template'] = '{site}_{name}'
        vm = {'name': 'webserver01', '$key': 1}
        hostname = inventory_module._get_hostname(vm, 'denver')
        assert hostname == 'denver_webserver01'

    def test_custom_template(self, inventory_module):
        """Test hostname generation with custom template"""
        inventory_module._options['hostname_template'] = '{name}-{site}'
        vm = {'name': 'db01', '$key': 2}
        hostname = inventory_module._get_hostname(vm, 'chicago')
        assert hostname == 'db01-chicago'

    def test_sanitizes_special_characters(self, inventory_module):
        """Test that special characters in hostname are sanitized"""
        inventory_module._options['hostname_template'] = '{site}_{name}'
        vm = {'name': 'web server.01', '$key': 1}
        hostname = inventory_module._get_hostname(vm, 'site-1')
        assert hostname == 'site-1_web_server_01'

    def test_fallback_to_key_when_no_name(self, inventory_module):
        """Test fallback to VM key when name is missing"""
        inventory_module._options['hostname_template'] = '{site}_{name}'
        vm = {'$key': 42}
        hostname = inventory_module._get_hostname(vm, 'prod')
        assert hostname == 'prod_42'


class TestMatchesFilters:
    """Tests for _matches_filters method"""

    def test_no_filters_matches_all(self, inventory_module):
        """Test that no filters matches all VMs"""
        inventory_module._options['filters'] = None
        vm = {'name': 'any-vm', 'status': 'running'}
        assert inventory_module._matches_filters(vm) is True

    def test_empty_filters_matches_all(self, inventory_module):
        """Test that empty filters matches all VMs"""
        inventory_module._options['filters'] = {}
        vm = {'name': 'any-vm', 'status': 'stopped'}
        assert inventory_module._matches_filters(vm) is True

    def test_status_filter_matches(self, inventory_module):
        """Test status filter matching"""
        inventory_module._options['filters'] = {'status': 'running'}
        assert inventory_module._matches_filters({'name': 'vm1', 'status': 'running'}) is True
        assert inventory_module._matches_filters({'name': 'vm2', 'status': 'stopped'}) is False

    def test_status_filter_fallback_to_power_state(self, inventory_module):
        """Test status filter falls back to power_state field"""
        inventory_module._options['filters'] = {'status': 'running'}
        vm = {'name': 'vm1', 'power_state': 'running'}
        assert inventory_module._matches_filters(vm) is True

    def test_name_pattern_filter(self, inventory_module):
        """Test name_pattern regex filter"""
        inventory_module._options['filters'] = {'name_pattern': '.*web.*'}
        assert inventory_module._matches_filters({'name': 'web-server-01'}) is True
        assert inventory_module._matches_filters({'name': 'webapi'}) is True
        assert inventory_module._matches_filters({'name': 'database-01'}) is False

    def test_name_pattern_case_sensitive(self, inventory_module):
        """Test that name_pattern is case sensitive by default"""
        inventory_module._options['filters'] = {'name_pattern': '.*Web.*'}
        assert inventory_module._matches_filters({'name': 'WebServer'}) is True
        assert inventory_module._matches_filters({'name': 'webserver'}) is False

    def test_generic_field_filter(self, inventory_module):
        """Test generic field filtering"""
        inventory_module._options['filters'] = {'tenant': 'acme', 'cluster': 'prod-cluster'}
        vm = {'name': 'vm1', 'tenant': 'acme', 'cluster': 'prod-cluster'}
        assert inventory_module._matches_filters(vm) is True

        vm_wrong_tenant = {'name': 'vm2', 'tenant': 'other', 'cluster': 'prod-cluster'}
        assert inventory_module._matches_filters(vm_wrong_tenant) is False

    def test_multiple_filters_all_must_match(self, inventory_module):
        """Test that all filters must match (AND logic)"""
        inventory_module._options['filters'] = {
            'status': 'running',
            'name_pattern': '.*prod.*'
        }
        assert inventory_module._matches_filters({'name': 'prod-web', 'status': 'running'}) is True
        assert inventory_module._matches_filters({'name': 'prod-web', 'status': 'stopped'}) is False
        assert inventory_module._matches_filters({'name': 'dev-web', 'status': 'running'}) is False


class TestFetchSite:
    """Tests for _fetch_site method"""

    @patch('ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms.VergeClient')
    def test_successful_fetch(self, mock_client_class, inventory_module):
        """Test successful site fetch"""
        # Setup mock VM
        mock_vm = MagicMock()
        mock_vm.__iter__ = lambda self: iter({'$key': 1, 'name': 'test-vm', 'status': 'running'}.items())
        mock_vm.get_tags.return_value = [{'tag_name': 'prod', 'tag_key': 1}]
        mock_vm.nics.list.return_value = []

        mock_client = MagicMock()
        mock_client.vms.list.return_value = [mock_vm]
        mock_client_class.return_value = mock_client

        site_config = {
            'name': 'test-site',
            'host': 'test.vergeos.local',
            'username': 'admin',
            'password': 'secret',
            'insecure': False,
            'timeout': 30
        }

        result = inventory_module._fetch_site(site_config)

        assert result['site'] == 'test-site'
        assert result['site_url'] == 'test.vergeos.local'
        assert result['error'] is None
        assert len(result['vms']) == 1
        assert result['vms'][0]['_tags'] == ['prod']

    @patch('ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms.VergeClient')
    def test_strips_protocol_from_host(self, mock_client_class, inventory_module):
        """Test that protocol is stripped from host"""
        mock_client = MagicMock()
        mock_client.vms.list.return_value = []
        mock_client_class.return_value = mock_client

        site_config = {
            'name': 'test',
            'host': 'https://test.vergeos.local',
            'username': 'admin',
            'password': 'secret'
        }

        inventory_module._fetch_site(site_config)

        # Verify client was created with stripped host
        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs['host'] == 'test.vergeos.local'

    @patch('ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms.VergeClient')
    def test_api_key_mapped_to_token(self, mock_client_class, inventory_module):
        """Test that api_key config is mapped to SDK token parameter"""
        mock_client = MagicMock()
        mock_client.vms.list.return_value = []
        mock_client_class.return_value = mock_client

        site_config = {
            'name': 'test',
            'host': 'test.vergeos.local',
            'api_key': 'my-api-key-123'
        }

        inventory_module._fetch_site(site_config)

        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs.get('token') == 'my-api-key-123'
        assert 'api_key' not in call_kwargs

    @patch('ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms.VergeClient')
    def test_connection_error_handling(self, mock_client_class, inventory_module):
        """Test that connection errors are handled gracefully"""
        from pyvergeos.exceptions import VergeConnectionError
        mock_client_class.side_effect = VergeConnectionError("Connection refused")

        site_config = {
            'name': 'offline-site',
            'host': 'offline.vergeos.local',
            'username': 'admin',
            'password': 'secret'
        }

        with patch('ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms.VergeConnectionError', VergeConnectionError):
            result = inventory_module._fetch_site(site_config)

        assert result['site'] == 'offline-site'
        assert result['error'] is not None
        assert 'Connection' in result['error']
        assert result['vms'] == []

    @patch('ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms.VergeClient')
    def test_authentication_error_handling(self, mock_client_class, inventory_module):
        """Test that authentication errors are handled gracefully"""
        from pyvergeos.exceptions import AuthenticationError
        mock_client_class.side_effect = AuthenticationError("Invalid credentials")

        site_config = {
            'name': 'test',
            'host': 'test.vergeos.local',
            'username': 'wrong',
            'password': 'wrong'
        }

        with patch('ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms.AuthenticationError', AuthenticationError):
            result = inventory_module._fetch_site(site_config)

        assert result['error'] is not None
        assert 'Authentication' in result['error']


class TestCreateGroups:
    """Tests for _create_groups method"""

    def test_site_group_created(self, inventory_module):
        """Test that site group is created"""
        inventory_module._options['group_by'] = ['site']
        inventory_module._create_groups('denver_vm1', {'name': 'vm1'}, 'denver')

        inventory_module.inventory.add_group.assert_called_with('site_denver')
        inventory_module.inventory.add_child.assert_called_with('site_denver', 'denver_vm1')

    def test_status_group_created(self, inventory_module):
        """Test that status group is created"""
        inventory_module._options['group_by'] = ['status']
        inventory_module._create_groups('host1', {'name': 'vm1', 'status': 'running'}, 'site1')

        inventory_module.inventory.add_group.assert_called_with('status_running')
        inventory_module.inventory.add_child.assert_called_with('status_running', 'host1')

    def test_tag_groups_created(self, inventory_module):
        """Test that tag groups are created"""
        inventory_module._options['group_by'] = ['tags']
        vm = {'name': 'vm1', '_tags': ['production', 'web']}
        inventory_module._create_groups('host1', vm, 'site1')

        calls = inventory_module.inventory.add_group.call_args_list
        group_names = [call[0][0] for call in calls]
        assert 'tag_production' in group_names
        assert 'tag_web' in group_names

    def test_multiple_group_dimensions(self, inventory_module):
        """Test grouping by multiple dimensions"""
        inventory_module._options['group_by'] = ['site', 'status', 'tags']
        vm = {'name': 'vm1', 'status': 'running', '_tags': ['prod']}
        inventory_module._create_groups('denver_vm1', vm, 'denver')

        calls = inventory_module.inventory.add_group.call_args_list
        group_names = [call[0][0] for call in calls]
        assert 'site_denver' in group_names
        assert 'status_running' in group_names
        assert 'tag_prod' in group_names

    def test_skips_empty_optional_fields(self, inventory_module):
        """Test that empty optional fields don't create groups"""
        inventory_module._options['group_by'] = ['tenant', 'cluster', 'os_family']
        vm = {'name': 'vm1', 'tenant': None, 'cluster': None, 'os_family': None}
        inventory_module._create_groups('host1', vm, 'site1')

        # Should not create any groups for None values
        inventory_module.inventory.add_group.assert_not_called()


class TestSetHostvars:
    """Tests for _set_hostvars method"""

    def test_ansible_host_not_set(self, inventory_module):
        """CRITICAL: Verify that ansible_host is NOT set (API-only plugin)"""
        inventory_module._options['hostvar_prefix'] = 'vergeos_'
        vm = {
            '$key': 1,
            'name': 'test-vm',
            'status': 'running',
            '_nics': [{'ipaddress': '10.0.0.50'}],
            '_tags': []
        }
        inventory_module._set_hostvars('host1', vm, 'site1', 'https://site1.local')

        # Check all set_variable calls
        calls = inventory_module.inventory.set_variable.call_args_list
        var_names = [call[0][1] for call in calls]

        # ansible_host should NOT be in the list
        assert 'ansible_host' not in var_names

        # vergeos_ip should be set for reference
        assert 'vergeos_ip' in var_names

    def test_site_info_set(self, inventory_module):
        """Test that site info is set for API connections"""
        inventory_module._options['hostvar_prefix'] = 'vergeos_'
        vm = {'$key': 1, 'name': 'vm1', '_nics': [], '_tags': []}
        inventory_module._set_hostvars('host1', vm, 'denver', 'https://denver.local')

        calls = {call[0][1]: call[0][2] for call in inventory_module.inventory.set_variable.call_args_list}
        assert calls['vergeos_site'] == 'denver'
        assert calls['vergeos_site_url'] == 'https://denver.local'

    def test_vm_identification_set(self, inventory_module):
        """Test that VM identification vars are set"""
        inventory_module._options['hostvar_prefix'] = 'vergeos_'
        vm = {'$key': 42, 'name': 'webserver', 'machine': 'abc123', '_nics': [], '_tags': []}
        inventory_module._set_hostvars('host1', vm, 'site1', 'url')

        calls = {call[0][1]: call[0][2] for call in inventory_module.inventory.set_variable.call_args_list}
        assert calls['vergeos_vm_id'] == 42
        assert calls['vergeos_name'] == 'webserver'
        assert calls['vergeos_machine'] == 'abc123'

    def test_custom_prefix(self, inventory_module):
        """Test that custom prefix is applied"""
        inventory_module._options['hostvar_prefix'] = 'vos_'
        vm = {'$key': 1, 'name': 'vm1', 'status': 'running', '_nics': [], '_tags': []}
        inventory_module._set_hostvars('host1', vm, 'site1', 'url')

        calls = inventory_module.inventory.set_variable.call_args_list
        var_names = [call[0][1] for call in calls]
        assert 'vos_site' in var_names
        assert 'vos_vm_id' in var_names
        assert 'vergeos_site' not in var_names

    def test_tags_set_from_internal_field(self, inventory_module):
        """Test that tags are extracted from _tags internal field"""
        inventory_module._options['hostvar_prefix'] = 'vergeos_'
        vm = {'$key': 1, 'name': 'vm1', '_nics': [], '_tags': ['prod', 'web']}
        inventory_module._set_hostvars('host1', vm, 'site1', 'url')

        calls = {call[0][1]: call[0][2] for call in inventory_module.inventory.set_variable.call_args_list}
        assert calls['vergeos_tags'] == ['prod', 'web']

    def test_ip_extracted_from_nics(self, inventory_module):
        """Test that IP is extracted from NICs for reference"""
        inventory_module._options['hostvar_prefix'] = 'vergeos_'
        vm = {
            '$key': 1,
            'name': 'vm1',
            '_nics': [
                {'ipaddress': '10.0.0.100', 'mac': '00:11:22:33:44:55'}
            ],
            '_tags': []
        }
        inventory_module._set_hostvars('host1', vm, 'site1', 'url')

        calls = {call[0][1]: call[0][2] for call in inventory_module.inventory.set_variable.call_args_list}
        assert calls['vergeos_ip'] == '10.0.0.100'

    def test_vm_data_excludes_internal_fields(self, inventory_module):
        """Test that vergeos_vm_data excludes internal _fields"""
        inventory_module._options['hostvar_prefix'] = 'vergeos_'
        vm = {'$key': 1, 'name': 'vm1', 'status': 'running', '_nics': [], '_tags': ['test'], '_internal': 'data'}
        inventory_module._set_hostvars('host1', vm, 'site1', 'url')

        calls = {call[0][1]: call[0][2] for call in inventory_module.inventory.set_variable.call_args_list}
        vm_data = calls['vergeos_vm_data']
        assert '_nics' not in vm_data
        assert '_tags' not in vm_data
        assert '_internal' not in vm_data
        assert 'name' in vm_data


class TestCacheOperations:
    """Tests for cache serialization and restoration"""

    def test_get_cache_data_structure(self, inventory_module):
        """Test cache data structure"""
        # Setup mock inventory state
        mock_host = MagicMock()
        mock_host.vars = {'vergeos_site': 'denver', 'vergeos_vm_id': 1}
        mock_host.name = 'denver_vm1'

        mock_group = MagicMock()
        mock_group.hosts = [mock_host]

        inventory_module.inventory.hosts = {'denver_vm1': mock_host}
        inventory_module.inventory.groups = {
            'all': MagicMock(hosts=[]),
            'ungrouped': MagicMock(hosts=[]),
            'site_denver': mock_group
        }
        inventory_module.inventory.get_host.return_value = mock_host

        cache_data = inventory_module._get_cache_data()

        assert 'hosts' in cache_data
        assert 'groups' in cache_data
        assert 'denver_vm1' in cache_data['hosts']
        assert 'site_denver' in cache_data['groups']
        assert 'all' not in cache_data['groups']
        assert 'ungrouped' not in cache_data['groups']

    def test_populate_from_cache(self, inventory_module):
        """Test restoring inventory from cache"""
        cached_data = {
            'hosts': {
                'denver_vm1': {'vergeos_site': 'denver', 'vergeos_vm_id': 1},
                'denver_vm2': {'vergeos_site': 'denver', 'vergeos_vm_id': 2}
            },
            'groups': {
                'site_denver': ['denver_vm1', 'denver_vm2'],
                'status_running': ['denver_vm1']
            }
        }

        inventory_module.inventory.hosts = {}

        def add_host_side_effect(hostname):
            inventory_module.inventory.hosts[hostname] = True

        inventory_module.inventory.add_host.side_effect = add_host_side_effect

        inventory_module._populate_from_cache(cached_data)

        # Verify hosts were added
        assert inventory_module.inventory.add_host.call_count == 2

        # Verify groups were created
        group_calls = [call[0][0] for call in inventory_module.inventory.add_group.call_args_list]
        assert 'site_denver' in group_calls
        assert 'status_running' in group_calls


class TestFetchAllSites:
    """Tests for _fetch_all_sites concurrent fetching"""

    @patch.object(MagicMock, '_fetch_site')
    def test_concurrent_fetch_multiple_sites(self, inventory_module):
        """Test that multiple sites are fetched concurrently"""
        inventory_module._options['sites'] = [
            {'name': 'site1', 'host': 'site1.local', 'username': 'u', 'password': 'p'},
            {'name': 'site2', 'host': 'site2.local', 'username': 'u', 'password': 'p'}
        ]
        inventory_module._options['max_workers'] = 10
        inventory_module._options['site_timeout'] = 60

        def mock_fetch(site_config):
            return {
                'site': site_config['name'],
                'site_url': site_config['host'],
                'vms': [],
                'error': None
            }

        inventory_module._fetch_site = mock_fetch

        results = inventory_module._fetch_all_sites()

        assert len(results) == 2
        site_names = [r['site'] for r in results]
        assert 'site1' in site_names
        assert 'site2' in site_names

    def test_site_error_continues_to_other_sites(self, inventory_module):
        """Test that one site failure doesn't stop other sites"""
        inventory_module._options['sites'] = [
            {'name': 'good-site', 'host': 'good.local', 'username': 'u', 'password': 'p'},
            {'name': 'bad-site', 'host': 'bad.local', 'username': 'u', 'password': 'p'}
        ]
        inventory_module._options['max_workers'] = 10
        inventory_module._options['site_timeout'] = 60

        def mock_fetch(site_config):
            if site_config['name'] == 'bad-site':
                return {
                    'site': 'bad-site',
                    'site_url': 'bad.local',
                    'vms': [],
                    'error': 'Connection refused'
                }
            return {
                'site': 'good-site',
                'site_url': 'good.local',
                'vms': [{'$key': 1, 'name': 'vm1'}],
                'error': None
            }

        inventory_module._fetch_site = mock_fetch

        results = inventory_module._fetch_all_sites()

        # Both sites should be in results
        assert len(results) == 2

        # Good site should have VMs
        good_result = next(r for r in results if r['site'] == 'good-site')
        assert len(good_result['vms']) == 1

        # Bad site should have error
        bad_result = next(r for r in results if r['site'] == 'bad-site')
        assert bad_result['error'] is not None

        # Warning should have been displayed
        inventory_module.display.warning.assert_called()


class TestPopulateInventory:
    """Tests for _populate_inventory method"""

    def test_skips_snapshots(self, inventory_module):
        """Test that VM snapshots are skipped"""
        inventory_module._options['include_stopped'] = True
        inventory_module._options['filters'] = None
        inventory_module._options['hostname_template'] = '{site}_{name}'
        inventory_module._options['hostvar_prefix'] = 'vergeos_'
        inventory_module._options['group_by'] = ['site']
        inventory_module._options['strict'] = False
        inventory_module._options['compose'] = None
        inventory_module._options['groups'] = None
        inventory_module._options['keyed_groups'] = None

        site_data = [{
            'site': 'test',
            'site_url': 'test.local',
            'vms': [
                {'$key': 1, 'name': 'real-vm', 'is_snapshot': False, '_nics': [], '_tags': []},
                {'$key': 2, 'name': 'snapshot-vm', 'is_snapshot': True, '_nics': [], '_tags': []}
            ],
            'error': None
        }]

        inventory_module._populate_inventory(site_data)

        # Only real VM should be added
        calls = inventory_module.inventory.add_host.call_args_list
        assert len(calls) == 1
        assert calls[0][0][0] == 'test_real-vm'

    def test_skips_stopped_when_disabled(self, inventory_module):
        """Test that stopped VMs are skipped when include_stopped=False"""
        inventory_module._options['include_stopped'] = False
        inventory_module._options['filters'] = None
        inventory_module._options['hostname_template'] = '{site}_{name}'
        inventory_module._options['hostvar_prefix'] = 'vergeos_'
        inventory_module._options['group_by'] = ['site']
        inventory_module._options['strict'] = False
        inventory_module._options['compose'] = None
        inventory_module._options['groups'] = None
        inventory_module._options['keyed_groups'] = None

        site_data = [{
            'site': 'test',
            'site_url': 'test.local',
            'vms': [
                {'$key': 1, 'name': 'running-vm', 'status': 'running', '_nics': [], '_tags': []},
                {'$key': 2, 'name': 'stopped-vm', 'status': 'stopped', '_nics': [], '_tags': []}
            ],
            'error': None
        }]

        inventory_module._populate_inventory(site_data)

        calls = inventory_module.inventory.add_host.call_args_list
        assert len(calls) == 1
        assert calls[0][0][0] == 'test_running-vm'

    def test_skips_sites_with_errors(self, inventory_module):
        """Test that sites with errors are skipped"""
        inventory_module._options['include_stopped'] = True
        inventory_module._options['filters'] = None
        inventory_module._options['hostname_template'] = '{site}_{name}'
        inventory_module._options['hostvar_prefix'] = 'vergeos_'
        inventory_module._options['group_by'] = ['site']
        inventory_module._options['strict'] = False
        inventory_module._options['compose'] = None
        inventory_module._options['groups'] = None
        inventory_module._options['keyed_groups'] = None

        site_data = [
            {
                'site': 'good-site',
                'site_url': 'good.local',
                'vms': [{'$key': 1, 'name': 'vm1', '_nics': [], '_tags': []}],
                'error': None
            },
            {
                'site': 'bad-site',
                'site_url': 'bad.local',
                'vms': [],
                'error': 'Connection failed'
            }
        ]

        inventory_module._populate_inventory(site_data)

        # Only good site's VM should be added
        calls = inventory_module.inventory.add_host.call_args_list
        assert len(calls) == 1
        assert 'good-site' in calls[0][0][0]


class TestParseValidation:
    """Tests for parse method validation"""

    def test_fails_without_pyvergeos(self):
        """Test that parse fails when pyvergeos is not installed"""
        with patch('ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms.HAS_PYVERGEOS', False):
            from ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms import InventoryModule
            from ansible.errors import AnsibleError

            module = InventoryModule()

            with pytest.raises(AnsibleError) as exc_info:
                module.parse(MagicMock(), MagicMock(), '/path/to/inv.vergeos_vms.yml')

            assert 'pyvergeos SDK is required' in str(exc_info.value)

    def test_fails_without_sites(self, inventory_module):
        """Test that parse fails when no sites configured"""
        from ansible.errors import AnsibleError

        inventory_module._options['sites'] = []

        def mock_read_config(path):
            pass

        inventory_module._read_config_data = mock_read_config

        with patch.object(inventory_module.__class__.__bases__[0], 'parse'):
            with pytest.raises(AnsibleError) as exc_info:
                inventory_module.parse(MagicMock(), MagicMock(), '/path/to/inv.yml')

            assert 'At least one site must be configured' in str(exc_info.value)

    def test_fails_without_site_name(self, inventory_module):
        """Test that parse fails when site missing name"""
        from ansible.errors import AnsibleError

        inventory_module._options['sites'] = [{'host': 'test.local', 'username': 'u', 'password': 'p'}]

        def mock_read_config(path):
            pass

        inventory_module._read_config_data = mock_read_config

        with patch.object(inventory_module.__class__.__bases__[0], 'parse'):
            with pytest.raises(AnsibleError) as exc_info:
                inventory_module.parse(MagicMock(), MagicMock(), '/path/to/inv.yml')

            assert "missing required 'name' field" in str(exc_info.value)

    def test_fails_without_credentials(self, inventory_module):
        """Test that parse fails when site missing credentials"""
        from ansible.errors import AnsibleError

        inventory_module._options['sites'] = [{'name': 'test', 'host': 'test.local'}]

        def mock_read_config(path):
            pass

        inventory_module._read_config_data = mock_read_config

        with patch.object(inventory_module.__class__.__bases__[0], 'parse'):
            with pytest.raises(AnsibleError) as exc_info:
                inventory_module.parse(MagicMock(), MagicMock(), '/path/to/inv.yml')

            assert 'api_key' in str(exc_info.value) or 'username' in str(exc_info.value)
