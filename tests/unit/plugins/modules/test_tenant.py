#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for tenant module"""

import pytest
from unittest.mock import MagicMock, patch


# Real exception classes: MagicMocks in an except tuple raise TypeError
# the moment any exception passes through, and as side_effects they are
# called instead of raised.
class NotFoundError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class ValidationError(Exception):
    pass


class APIError(Exception):
    pass


class VergeConnectionError(Exception):
    pass


@pytest.fixture(autouse=True)
def mock_pyvergeos():
    """Mock pyvergeos SDK for all tests, with real exception classes"""
    exceptions = MagicMock()
    exceptions.NotFoundError = NotFoundError
    exceptions.AuthenticationError = AuthenticationError
    exceptions.ValidationError = ValidationError
    exceptions.APIError = APIError
    exceptions.VergeConnectionError = VergeConnectionError
    sdk = MagicMock()
    sdk.exceptions = exceptions
    with patch.dict('sys.modules', {
        'pyvergeos': sdk,
        'pyvergeos.exceptions': exceptions,
    }):
        yield


def make_row(data):
    """Mock a mapping-protocol SDK resource row.

    dict() prefers the mapping protocol (keys + __getitem__) over
    iteration, and MagicMock auto-provides a keys attribute -- so both
    must be configured explicitly or dict(mock) silently returns {}.
    """
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    return obj


def make_tenant(data, nodes=None, storage=None):
    """Mock an SDK Tenant with scoped node/storage managers."""
    obj = make_row(data)
    obj.key = data.get('$key')
    obj.nodes.list.return_value = nodes or []
    obj.storage.list.return_value = storage or []
    return obj


def make_module(params, check_mode=False):
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    # The real methods raise SystemExit; without this the code under test
    # continues past exit_json/fail_json
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def base_params(**overrides):
    params = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'name': 'acme',
        'state': 'present',
        'description': None,
        'url': None,
        'note': None,
        'tenant_password': None,
        'require_password_change': False,
        'expose_cloud_snapshots': None,
        'allow_branding': None,
        'nodes': None,
        'purge_nodes': False,
        'storage': None,
        'purge_storage': False,
        'wait_timeout': 180,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.tenant.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import tenant as tenant_module
                try:
                    tenant_module.main()
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
    'running': False,
    'status': 'offline',
    'is_snapshot': False,
}


class TestTenantPresent:
    def test_creates_when_missing(self):
        mock_client = MagicMock()
        mock_client.tenants.get.side_effect = NotFoundError('nope')
        created = make_tenant(dict(TENANT_DATA))
        mock_client.tenants.create.return_value = created

        module = make_module(base_params(description='ACME Corp'))
        run_main(module, mock_client)

        mock_client.tenants.create.assert_called_once_with(
            description='ACME Corp', name='acme')
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert "created tenant 'acme'" in result['actions']

    def test_create_passes_password_and_change_flag(self):
        mock_client = MagicMock()
        mock_client.tenants.get.side_effect = NotFoundError('nope')
        mock_client.tenants.create.return_value = make_tenant(dict(TENANT_DATA))

        module = make_module(base_params(
            tenant_password='hunter2', require_password_change=True))
        run_main(module, mock_client)

        kwargs = mock_client.tenants.create.call_args[1]
        assert kwargs['password'] == 'hunter2'
        assert kwargs['require_password_change'] is True

    def test_no_change_when_settings_match(self):
        mock_client = MagicMock()
        mock_client.tenants.get.return_value = make_tenant(dict(TENANT_DATA))

        module = make_module(base_params(description='ACME Corp'))
        run_main(module, mock_client)

        mock_client.tenants.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_updates_drifted_settings(self):
        mock_client = MagicMock()
        existing = make_tenant(dict(TENANT_DATA))
        mock_client.tenants.get.return_value = existing
        mock_client.tenants.update.return_value = existing

        module = make_module(base_params(description='New description'))
        run_main(module, mock_client)

        mock_client.tenants.update.assert_called_once_with(
            7, description='New description')
        assert module.exit_json.call_args[1]['changed'] is True

    def test_check_mode_creates_nothing(self):
        mock_client = MagicMock()
        mock_client.tenants.get.side_effect = NotFoundError('nope')

        module = make_module(base_params(
            nodes=[{'name': 'n1', 'cpu_cores': 4, 'ram_gb': 16,
                    'cluster': 1, 'description': ''}]), check_mode=True)
        run_main(module, mock_client)

        mock_client.tenants.create.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert "created node 'n1'" in result['actions']

    def test_refuses_to_manage_snapshot(self):
        mock_client = MagicMock()
        snap = dict(TENANT_DATA)
        snap['is_snapshot'] = True
        mock_client.tenants.get.return_value = make_tenant(snap)

        module = make_module(base_params())
        run_main(module, mock_client)

        module.fail_json.assert_called_once()
        assert 'snapshot' in module.fail_json.call_args[1]['msg']


class TestTenantNodes:
    def _node(self, name, cpu, ram_mb, key=11):
        return make_row({'$key': key, 'name': name,
                         'cpu_cores': cpu, 'ram': ram_mb})

    def test_creates_missing_node(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA))
        mock_client.tenants.get.return_value = tenant

        module = make_module(base_params(
            nodes=[{'name': 'n1', 'cpu_cores': 8, 'ram_gb': 32,
                    'cluster': 1, 'description': ''}]))
        run_main(module, mock_client)

        tenant.nodes.create.assert_called_once_with(
            cpu_cores=8, ram_mb=32768, cluster=1, name='n1', description='')
        assert module.exit_json.call_args[1]['changed'] is True

    def test_updates_drifted_node(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA),
                             nodes=[self._node('n1', 4, 16384)])
        mock_client.tenants.get.return_value = tenant

        module = make_module(base_params(
            nodes=[{'name': 'n1', 'cpu_cores': 8, 'ram_gb': 16,
                    'cluster': 1, 'description': ''}]))
        run_main(module, mock_client)

        tenant.nodes.update.assert_called_once_with(11, cpu_cores=8)
        tenant.nodes.create.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_matching_node_no_change(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA),
                             nodes=[self._node('n1', 8, 32768)])
        mock_client.tenants.get.return_value = tenant

        module = make_module(base_params(
            nodes=[{'name': 'n1', 'cpu_cores': 8, 'ram_gb': 32,
                    'cluster': 1, 'description': ''}]))
        run_main(module, mock_client)

        tenant.nodes.create.assert_not_called()
        tenant.nodes.update.assert_not_called()
        tenant.nodes.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_purge_deletes_unlisted_node(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA),
                             nodes=[self._node('n1', 8, 32768, key=11),
                                    self._node('stray', 4, 16384, key=12)])
        mock_client.tenants.get.return_value = tenant

        module = make_module(base_params(
            nodes=[{'name': 'n1', 'cpu_cores': 8, 'ram_gb': 32,
                    'cluster': 1, 'description': ''}],
            purge_nodes=True))
        run_main(module, mock_client)

        tenant.nodes.delete.assert_called_once_with(12)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_unlisted_node_kept_without_purge(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA),
                             nodes=[self._node('stray', 4, 16384)])
        mock_client.tenants.get.return_value = tenant

        module = make_module(base_params(nodes=[]))
        run_main(module, mock_client)

        tenant.nodes.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False


class TestTenantStorage:
    def _alloc(self, tier, provisioned, key=21):
        return make_row({'$key': key, 'tier_number': tier,
                         'provisioned': provisioned})

    def test_creates_missing_tier(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA))
        mock_client.tenants.get.return_value = tenant

        module = make_module(base_params(
            storage=[{'tier': 3, 'provisioned_gb': 100}]))
        run_main(module, mock_client)

        tenant.storage.create.assert_called_once_with(
            tier=3, provisioned_gb=100)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_resizes_drifted_tier(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA),
                             storage=[self._alloc(3, 100 * 1073741824)])
        mock_client.tenants.get.return_value = tenant

        module = make_module(base_params(
            storage=[{'tier': 3, 'provisioned_gb': 200}]))
        run_main(module, mock_client)

        tenant.storage.update_by_tier.assert_called_once_with(
            3, provisioned_bytes=200 * 1073741824)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_matching_storage_no_change(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA),
                             storage=[self._alloc(3, 100 * 1073741824)])
        mock_client.tenants.get.return_value = tenant

        module = make_module(base_params(
            storage=[{'tier': 3, 'provisioned_gb': 100}]))
        run_main(module, mock_client)

        tenant.storage.create.assert_not_called()
        tenant.storage.update_by_tier.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_purge_deletes_unlisted_tier(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA),
                             storage=[self._alloc(1, 1073741824, key=21),
                                      self._alloc(4, 1073741824, key=22)])
        mock_client.tenants.get.return_value = tenant

        module = make_module(base_params(
            storage=[{'tier': 1, 'provisioned_gb': 1}],
            purge_storage=True))
        run_main(module, mock_client)

        tenant.storage.delete_by_tier.assert_called_once_with(4)
        assert module.exit_json.call_args[1]['changed'] is True


class TestTenantPower:
    def test_powers_on_when_state_running(self):
        mock_client = MagicMock()
        stopped = make_tenant(dict(TENANT_DATA))
        running_data = dict(TENANT_DATA)
        running_data['running'] = True
        running = make_tenant(running_data)

        def get_se(*args, **kwargs):
            if 'name' in kwargs:
                return stopped
            return running
        mock_client.tenants.get.side_effect = get_se

        module = make_module(base_params(state='running'))
        run_main(module, mock_client)

        mock_client.tenants.power_on.assert_called_once_with(7)
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert 'powered on' in result['actions']

    def test_no_power_change_when_already_running(self):
        mock_client = MagicMock()
        running_data = dict(TENANT_DATA)
        running_data['running'] = True
        mock_client.tenants.get.return_value = make_tenant(running_data)

        module = make_module(base_params(state='running'))
        run_main(module, mock_client)

        mock_client.tenants.power_on.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_powers_off_when_state_stopped(self):
        mock_client = MagicMock()
        running_data = dict(TENANT_DATA)
        running_data['running'] = True
        running = make_tenant(running_data)
        stopped = make_tenant(dict(TENANT_DATA))

        def get_se(*args, **kwargs):
            if 'name' in kwargs:
                return running
            return stopped
        mock_client.tenants.get.side_effect = get_se

        module = make_module(base_params(state='stopped'))
        run_main(module, mock_client)

        mock_client.tenants.power_off.assert_called_once_with(7)
        assert module.exit_json.call_args[1]['changed'] is True


class TestTenantAbsent:
    def test_deletes_stopped_tenant(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA))
        mock_client.tenants.get.return_value = tenant

        module = make_module(base_params(state='absent'))
        run_main(module, mock_client)

        tenant.delete.assert_called_once()
        mock_client.tenants.power_off.assert_not_called()
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert "deleted tenant 'acme'" in result['actions']

    def test_powers_off_then_deletes_running_tenant(self):
        mock_client = MagicMock()
        running_data = dict(TENANT_DATA)
        running_data['running'] = True
        running = make_tenant(running_data)
        stopped = make_tenant(dict(TENANT_DATA))

        def get_se(*args, **kwargs):
            if 'name' in kwargs:
                return running
            return stopped
        mock_client.tenants.get.side_effect = get_se

        module = make_module(base_params(state='absent'))
        run_main(module, mock_client)

        mock_client.tenants.power_off.assert_called_once_with(7)
        stopped.delete.assert_called_once()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_absent_missing_no_change(self):
        mock_client = MagicMock()
        mock_client.tenants.get.side_effect = NotFoundError('nope')

        module = make_module(base_params(state='absent'))
        run_main(module, mock_client)

        assert module.exit_json.call_args[1]['changed'] is False

    def test_check_mode_deletes_nothing(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA))
        mock_client.tenants.get.return_value = tenant

        module = make_module(base_params(state='absent'), check_mode=True)
        run_main(module, mock_client)

        tenant.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True
