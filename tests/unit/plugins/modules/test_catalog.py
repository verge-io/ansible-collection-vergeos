#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the catalog module.

Catalog identity is (name [, repository]); publication scope is the
tenant recipe-grant mechanism. Catalog $keys are 40-hex STRINGS, not
ints — assertions pin that so a int-key regression cannot sneak in.
"""

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


CAT_KEY = 'b170cf3baba9f588d38b4145096edfdc34ce7e8b'


def make_row(data):
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    return obj


def make_catalog(key=CAT_KEY, name='Golden Images', repository=1,
                 scope='private', enabled=True, description=''):
    return make_row({'$key': key, 'id': key, 'name': name,
                     'repository': repository,
                     'repository_display': 'Local',
                     'description': description,
                     'publishing_scope': scope, 'enabled': enabled})


def make_client(catalogs=(), repo_key=1):
    client = MagicMock()
    client.catalogs.list.return_value = list(catalogs)
    client.catalog_repositories.get.return_value = make_row(
        {'$key': repo_key, 'name': 'Local'})
    return client


def make_module(params, check_mode=False):
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def base_params(**overrides):
    params = {'name': 'Golden Images', 'state': 'present',
              'repository': 'Local', 'description': None,
              'publishing_scope': None, 'enabled': None}
    params.update(overrides)
    return params


def run(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.module_utils.'
               'vergeos.get_vergeos_client', return_value=mock_client), \
         patch('ansible_collections.vergeio.vergeos.plugins.module_utils.'
               'vergeos.HAS_PYVERGEOS', True):
        import importlib
        mod = importlib.import_module(
            'ansible_collections.vergeio.vergeos.plugins.modules.catalog')
        with patch.object(mod, 'AnsibleModule', return_value=mock_module), \
             patch.object(mod, 'get_vergeos_client',
                          return_value=mock_client):
            with pytest.raises(SystemExit):
                mod.main()


class TestCreate:
    def test_create_with_scope(self):
        client = make_client(catalogs=[])
        client.catalogs.create.return_value = make_catalog(scope='tenant')
        module = make_module(base_params(publishing_scope='tenant',
                                         description='golden'))

        run(module, client)

        client.catalogs.create.assert_called_once_with(
            name='Golden Images', repository=1, description='golden',
            publishing_scope='tenant')
        result = module.exit_json.call_args.kwargs
        assert result['changed'] is True
        assert result['catalog']['key'] == CAT_KEY

    def test_create_without_repository_fails(self):
        client = make_client(catalogs=[])
        module = make_module(base_params(repository=None))

        run(module, client)

        assert 'repository is required' \
            in module.fail_json.call_args.kwargs['msg']

    def test_check_mode_does_not_create(self):
        client = make_client(catalogs=[])
        module = make_module(base_params(), check_mode=True)

        run(module, client)

        client.catalogs.create.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is True


class TestUpdate:
    def test_converged_catalog_is_unchanged(self):
        client = make_client(catalogs=[make_catalog(scope='tenant')])
        module = make_module(base_params(publishing_scope='tenant'))

        run(module, client)

        client.catalogs.update.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_scope_drift_updates_by_string_key(self):
        client = make_client(catalogs=[make_catalog(scope='private')])
        client.catalogs.update.return_value = make_catalog(scope='tenant')
        module = make_module(base_params(publishing_scope='tenant'))

        run(module, client)

        client.catalogs.update.assert_called_once_with(
            CAT_KEY, publishing_scope='tenant')
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_enabled_unset_is_preserved(self):
        client = make_client(catalogs=[make_catalog(enabled=False,
                                                    scope='tenant')])
        module = make_module(base_params(publishing_scope='tenant'))

        run(module, client)

        client.catalogs.update.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_repository_scopes_the_match(self):
        # same name in another repo (key 3) must not match repo Local (1)
        other = make_catalog(key='f' * 40, repository=3)
        client = make_client(catalogs=[other])
        client.catalogs.create.return_value = make_catalog()
        module = make_module(base_params())

        run(module, client)

        # not found in Local -> created there, other repo untouched
        client.catalogs.create.assert_called_once()
        client.catalogs.update.assert_not_called()


class TestMatching:
    def test_ambiguous_name_without_repository_refused(self):
        cats = [make_catalog(), make_catalog(key='f' * 40, repository=3)]
        client = make_client(catalogs=cats)
        module = make_module(base_params(repository=None))

        run(module, client)

        assert 'disambiguate' in module.fail_json.call_args.kwargs['msg']

    def test_unknown_repository_fails(self):
        client = make_client()
        client.catalog_repositories.get.side_effect = NotFoundError('x')
        module = make_module(base_params())

        run(module, client)

        assert "Repository 'Local' not found" \
            in module.fail_json.call_args.kwargs['msg']


class TestAbsent:
    def test_absent_deletes_by_string_key(self):
        client = make_client(catalogs=[make_catalog()])
        module = make_module(base_params(state='absent'))

        run(module, client)

        client.catalogs.delete.assert_called_once_with(CAT_KEY)
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_absent_missing_is_converged(self):
        client = make_client(catalogs=[])
        module = make_module(base_params(state='absent'))

        run(module, client)

        client.catalogs.delete.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False
