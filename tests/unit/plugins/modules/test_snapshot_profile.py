#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for snapshot_profile module"""

import pytest
from unittest.mock import MagicMock, patch


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
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    return obj


def make_module(params, check_mode=False):
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def default_period(**overrides):
    period = {
        'name': 'Daily',
        'frequency': 'daily',
        'retention': 604800,
        'minute': 0,
        'hour': 2,
        'day_of_week': 'any',
        'day_of_month': 0,
        'month': 0,
        'skip_missed': False,
        'max_tier': 1,
        'quiesce': False,
        'min_snapshots': 1,
        'immutable': False,
    }
    period.update(overrides)
    return period


def base_params(**overrides):
    params = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'name': 'vm-backups',
        'state': 'present',
        'description': None,
        'periods': None,
        'purge_periods': False,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.snapshot_profile.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    snapshot_profile as sp_module,
                )
                try:
                    sp_module.main()
                except SystemExit:
                    pass


PROFILE_DATA = {'$key': 8, 'name': 'vm-backups', 'description': 'backups'}

# Raw period row as the API returns it (max_tier is a string)
PERIOD_ROW = {
    '$key': 31, 'name': 'Daily', 'frequency': 'daily', 'retention': 604800,
    'minute': 0, 'hour': 2, 'day_of_week': 'any', 'day_of_month': 0,
    'month': 0, 'skip_missed': False, 'max_tier': '1', 'quiesce': False,
    'min_snapshots': 1, 'immutable': False,
}


def make_client(profile=None, periods=None):
    client = MagicMock()
    if profile is None:
        client.snapshot_profiles.get.side_effect = NotFoundError('nope')
    else:
        client.snapshot_profiles.get.return_value = profile
    manager = MagicMock()
    manager.list.return_value = periods or []
    client.snapshot_profiles.periods.return_value = manager
    return client, manager


class TestProfilePresent:
    def test_creates_profile_and_periods(self):
        client, manager = make_client()
        client.snapshot_profiles.create.return_value = make_row(dict(PROFILE_DATA))

        module = make_module(base_params(
            description='backups', periods=[default_period()]))
        run_main(module, client)

        client.snapshot_profiles.create.assert_called_once_with(
            name='vm-backups', description='backups')
        manager.create.assert_called_once()
        assert manager.create.call_args[1]['retention'] == 604800
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert "created profile 'vm-backups'" in result['actions']

    def test_idempotent_when_all_match(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[make_row(dict(PERIOD_ROW))])

        module = make_module(base_params(
            description='backups', periods=[default_period()]))
        run_main(module, client)

        client.snapshot_profiles.update.assert_not_called()
        manager.create.assert_not_called()
        manager.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_max_tier_string_compares_as_int(self):
        # API stores max_tier as '1'; desired 1 must NOT drift
        row = dict(PERIOD_ROW)
        row['max_tier'] = '1'
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)), periods=[make_row(row)])

        module = make_module(base_params(periods=[default_period(max_tier=1)]))
        run_main(module, client)

        manager.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_updates_drifted_retention(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[make_row(dict(PERIOD_ROW))])

        module = make_module(base_params(
            periods=[default_period(retention=1209600)]))
        run_main(module, client)

        manager.update.assert_called_once_with(31, retention=1209600)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_adds_missing_period(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[make_row(dict(PERIOD_ROW))])

        module = make_module(base_params(periods=[
            default_period(),
            default_period(name='Weekly', frequency='weekly',
                           retention=2592000, day_of_week='sun'),
        ]))
        run_main(module, client)

        manager.create.assert_called_once()
        assert manager.create.call_args[1]['name'] == 'Weekly'
        assert module.exit_json.call_args[1]['changed'] is True

    def test_purge_deletes_unlisted_period(self):
        stray = dict(PERIOD_ROW)
        stray.update({'$key': 32, 'name': 'Hourly'})
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[make_row(dict(PERIOD_ROW)), make_row(stray)])

        module = make_module(base_params(
            periods=[default_period()], purge_periods=True))
        run_main(module, client)

        manager.delete.assert_called_once_with(32)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_updates_description(self):
        client, manager = make_client(profile=make_row(dict(PROFILE_DATA)))
        client.snapshot_profiles.update.return_value = make_row(dict(PROFILE_DATA))

        module = make_module(base_params(description='new words'))
        run_main(module, client)

        client.snapshot_profiles.update.assert_called_once_with(
            8, description='new words')
        assert module.exit_json.call_args[1]['changed'] is True

    def test_check_mode_writes_nothing(self):
        client, manager = make_client()

        module = make_module(base_params(periods=[default_period()]),
                             check_mode=True)
        run_main(module, client)

        client.snapshot_profiles.create.assert_not_called()
        manager.create.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True


class TestProfileAbsent:
    def test_deletes_existing(self):
        client, _ = make_client(profile=make_row(dict(PROFILE_DATA)))

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.snapshot_profiles.delete.assert_called_once_with(8)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_absent_missing_no_change(self):
        client, _ = make_client()

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.snapshot_profiles.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False
