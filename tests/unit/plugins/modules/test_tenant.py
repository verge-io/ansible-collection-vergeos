#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the tenant module.

Name lookups go through ``list()``, not ``get(name=)``: the module uses
``resolve_one``, which matches client-side and refuses to guess between
duplicates (#72/#85). ``get()`` is still used, by KEY, while waiting for a
power transition -- so the two are mocked separately here, which is also what
lets a test say "the tenant was stopped when we looked it up and running when
we polled".
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


import pytest
from unittest.mock import MagicMock, patch


# Real exception classes: MagicMocks in an except tuple raise TypeError
# the moment any exception passes through, and as side_effects they are
# called instead of raised.
from pyvergeos.exceptions import (  # real classes: a Mock here is
    APIError,                       # CALLED as a side_effect, not raised
    AuthenticationError,
    NotFoundError,
    ValidationError,
    VergeConnectionError,
)


def make_row(data):
    """Mock a mapping-protocol SDK resource row.

    dict() prefers the mapping protocol (keys + __getitem__) over
    iteration, and MagicMock auto-provides a keys attribute -- so both
    must be configured explicitly or dict(mock) silently returns {}.
    """
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    obj.__iter__.side_effect = lambda: iter(data)
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
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.tenant.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.tenant.HAS_PYVERGEOS', True):
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
        mock_client.tenants.list.return_value = []
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
        mock_client.tenants.list.return_value = []
        mock_client.tenants.create.return_value = make_tenant(dict(TENANT_DATA))

        module = make_module(base_params(
            tenant_password='hunter2', require_password_change=True))
        run_main(module, mock_client)

        kwargs = mock_client.tenants.create.call_args[1]
        assert kwargs['password'] == 'hunter2'
        assert kwargs['require_password_change'] is True

    def test_no_change_when_settings_match(self):
        mock_client = MagicMock()
        mock_client.tenants.list.return_value = [make_tenant(dict(TENANT_DATA))]

        module = make_module(base_params(description='ACME Corp'))
        run_main(module, mock_client)

        mock_client.tenants.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_updates_drifted_settings(self):
        mock_client = MagicMock()
        existing = make_tenant(dict(TENANT_DATA))
        mock_client.tenants.list.return_value = [existing]
        mock_client.tenants.update.return_value = existing

        module = make_module(base_params(description='New description'))
        run_main(module, mock_client)

        mock_client.tenants.update.assert_called_once_with(
            7, description='New description')
        assert module.exit_json.call_args[1]['changed'] is True

    def test_check_mode_creates_nothing(self):
        mock_client = MagicMock()
        mock_client.tenants.list.return_value = []

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
        mock_client.tenants.list.return_value = [make_tenant(snap)]

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
        mock_client.tenants.list.return_value = [tenant]

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
        mock_client.tenants.list.return_value = [tenant]

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
        mock_client.tenants.list.return_value = [tenant]

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
        mock_client.tenants.list.return_value = [tenant]

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
        mock_client.tenants.list.return_value = [tenant]

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
        mock_client.tenants.list.return_value = [tenant]

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
        mock_client.tenants.list.return_value = [tenant]

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
        mock_client.tenants.list.return_value = [tenant]

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
        mock_client.tenants.list.return_value = [tenant]

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

        mock_client.tenants.list.return_value = [stopped]
        mock_client.tenants.get.return_value = running

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
        mock_client.tenants.list.return_value = [make_tenant(running_data)]

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

        mock_client.tenants.list.return_value = [running]
        mock_client.tenants.get.return_value = stopped

        module = make_module(base_params(state='stopped'))
        run_main(module, mock_client)

        mock_client.tenants.power_off.assert_called_once_with(7)
        assert module.exit_json.call_args[1]['changed'] is True


class TestTenantAbsent:
    def test_deletes_stopped_tenant(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA))
        mock_client.tenants.list.return_value = [tenant]

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

        mock_client.tenants.list.return_value = [running]
        mock_client.tenants.get.return_value = stopped

        module = make_module(base_params(state='absent'))
        run_main(module, mock_client)

        mock_client.tenants.power_off.assert_called_once_with(7)
        stopped.delete.assert_called_once()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_absent_missing_no_change(self):
        mock_client = MagicMock()
        mock_client.tenants.list.return_value = []

        module = make_module(base_params(state='absent'))
        run_main(module, mock_client)

        assert module.exit_json.call_args[1]['changed'] is False

    def test_check_mode_deletes_nothing(self):
        mock_client = MagicMock()
        tenant = make_tenant(dict(TENANT_DATA))
        mock_client.tenants.list.return_value = [tenant]

        module = make_module(base_params(state='absent'), check_mode=True)
        run_main(module, mock_client)

        tenant.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True


class TestDuplicateNamesAreRefused:
    def test_two_tenants_with_one_name(self):
        """Powering off or deleting the wrong tenant is not recoverable by
        re-running (#72/#85)."""
        mock_client = MagicMock()
        mock_client.tenants.list.return_value = [
            make_tenant(dict(TENANT_DATA, **{'$key': 7})),
            make_tenant(dict(TENANT_DATA, **{'$key': 8})),
        ]

        module = make_module(base_params(state='absent'))
        run_main(module, mock_client)

        assert 'refusing to guess' in module.fail_json.call_args[1]['msg']


class TestPlacementDiagnostic:
    """Issue #24: a tenant node the platform cannot place is not reported as
    a failure. The tenant goes Starting -> Stopped with no alarm, no task
    failure and no status_info, and the module used to say only:

        Timed out after 180s waiting for tenant to be running
    """

    def _client_that_never_starts(self, host_node=None):
        client = MagicMock()
        stopped = make_tenant(dict(TENANT_DATA))
        client.tenants.list.return_value = [stopped]
        client.tenants.get.return_value = stopped    # still stopped on poll
        client.tenants.nodes.return_value.list.return_value = [
            make_row({'$key': 11, 'name': 'acme-node1', 'cpu_cores': 4,
                      'ram': 16384, 'status': 'stopped',
                      'host_node': host_node}),
        ]
        client._request.return_value = [
            {'online_ram': 137472, 'used_ram': 74752,
             'online_cores': 64, 'used_cores': 31},
        ]
        client.nodes.list.return_value = [
            make_row({'$key': 1, 'name': 'node1', 'ram': 94208,
                      'vm_ram': 68352, 'failover_ram': 0, 'cores': 32,
                      'maintenance': False, 'running': True}),
            make_row({'$key': 2, 'name': 'node2', 'ram': 94208,
                      'vm_ram': 69120, 'failover_ram': 0, 'cores': 32,
                      'maintenance': False, 'running': True}),
        ]
        return client

    def test_timeout_says_the_node_was_never_placed(self):
        client = self._client_that_never_starts()

        module = make_module(base_params(state='running', wait_timeout=0))
        with patch('time.sleep'):
            run_main(module, client)

        msg = module.fail_json.call_args[1]['msg']
        assert 'Timed out' in msg
        assert 'never given a physical host' in msg
        assert 'acme-node1' in msg
        assert '16384 MB' in msg

    def test_timeout_reports_the_capacity_the_platform_used(self):
        client = self._client_that_never_starts()

        module = make_module(base_params(state='running', wait_timeout=0))
        with patch('time.sleep'):
            run_main(module, client)

        msg = module.fail_json.call_args[1]['msg']
        assert '137472 MB' in msg          # online_ram
        assert '74752 MB' in msg           # used_ram
        assert 'node1 68352 MB' in msg     # per-node vm_ram, not physical ram
        assert '94208' not in msg, (
            'physical ram overstates what is available to VMs by about a '
            'third; vm_ram is the figure to quote')

    def test_a_placed_node_is_reported_as_placed(self):
        """"Placed and still booting" is a different problem from "never
        placed", and the message must not conflate them."""
        client = self._client_that_never_starts(host_node='node2')

        module = make_module(base_params(state='running', wait_timeout=0))
        with patch('time.sleep'):
            run_main(module, client)

        msg = module.fail_json.call_args[1]['msg']
        assert 'never given a physical host' not in msg
        assert "'acme-node1' on node2" in msg

    def test_the_structured_detail_is_returned_too(self):
        client = self._client_that_never_starts()

        module = make_module(base_params(state='running', wait_timeout=0))
        with patch('time.sleep'):
            run_main(module, client)

        placement = module.fail_json.call_args[1]['placement']
        assert placement['nodes'][0]['host_node'] is None
        assert placement['nodes'][0]['ram_mb'] == 16384
        assert placement['capacity']['used_ram_mb'] == 74752
        assert placement['capacity']['largest_node_vm_ram_mb'] == 69120

    def test_no_verdict_is_offered(self):
        """Issue #24 proposed N-1 headroom as the placement rule. Measured
        again on 26.1.8 it is refuted in both directions -- see
        module_utils/clusters.py -- so the module reports figures and stops.
        A confident wrong mechanism is worse than the silence it replaces."""
        client = self._client_that_never_starts()

        module = make_module(base_params(state='running', wait_timeout=0))
        with patch('time.sleep'):
            run_main(module, client)

        msg = module.fail_json.call_args[1]['msg'].lower()
        for word in ('headroom', 'n-1', 'will not fit', 'cannot fit',
                     'insufficient'):
            assert word not in msg, (
                'the message asserts a placement rule (%r) that measurement '
                'does not support' % word)

    def test_a_capacity_read_that_fails_does_not_mask_the_timeout(self):
        client = self._client_that_never_starts()
        client._request.side_effect = APIError('no')

        module = make_module(base_params(state='running', wait_timeout=0))
        with patch('time.sleep'):
            run_main(module, client)

        msg = module.fail_json.call_args[1]['msg']
        assert 'Timed out' in msg
        assert 'never given a physical host' in msg
        assert 'could not be read' in msg

    def test_a_stop_that_times_out_says_nothing_about_placement(self):
        """Placement is a power-ON problem. A stop that hangs is something
        else, and borrowing this explanation for it would mislead."""
        client = self._client_that_never_starts()
        running = make_tenant(dict(TENANT_DATA, running=True))
        client.tenants.list.return_value = [running]
        client.tenants.get.return_value = running

        module = make_module(base_params(state='stopped', wait_timeout=0))
        with patch('time.sleep'):
            run_main(module, client)

        msg = module.fail_json.call_args[1]['msg']
        assert 'Timed out' in msg
        assert 'host' not in msg


class TestSdkErrorsAreHandled:
    """The exception classes are imported for this, and only this.

    They must be the REAL classes: a MagicMock in an `except` tuple raises
    TypeError the moment anything passes through it, and as a side_effect it
    is called rather than raised -- so a stub would make these tests pass
    while proving nothing.
    """

    @pytest.mark.parametrize('exc', [
        APIError, AuthenticationError, ValidationError, VergeConnectionError,
    ])
    def test_sdk_errors_do_not_fall_through_to_unexpected(self, exc):
        client = MagicMock()
        client.tenants.list.side_effect = exc('server said no')
        module = make_module(base_params())

        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'Unexpected error' not in module.fail_json.call_args[1]['msg']

    def test_a_missing_tenant_is_absence_not_an_error(self):
        """NotFoundError from resolve_one means "create it", not "fail"."""
        client = MagicMock()
        client.tenants.list.return_value = []
        client.tenants.create.return_value = make_tenant(dict(TENANT_DATA))
        module = make_module(base_params())

        run_main(module, client)

        module.fail_json.assert_not_called()
        client.tenants.create.assert_called_once()
        assert isinstance(NotFoundError('x'), Exception)


class TestNoLogIsDecidedNotDefaulted:
    def test_require_password_change_declares_no_log_false(self):
        """ansible-core warns on any option whose NAME looks like a secret:

            Module did not set no_log for require_password_change

        on every run, including check mode. The flag is a boolean, not a
        credential, and `no_log=False` is the only way to say so. Left off, the
        example playbook ships a warning with it.
        """
        import re
        from ansible_collections.vergeio.vergeos.plugins.modules import tenant
        source = open(tenant.__file__).read()
        assert re.search(
            r"require_password_change=dict\([^)]*no_log=False", source), (
            'require_password_change must declare no_log=False explicitly')

    def test_the_actual_secret_is_still_no_log(self):
        import re
        from ansible_collections.vergeio.vergeos.plugins.modules import tenant
        source = open(tenant.__file__).read()
        assert re.search(r"tenant_password=dict\([^)]*no_log=True", source), (
            'tenant_password is a credential and must stay masked')
