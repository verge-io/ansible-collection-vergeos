#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the catalog module.

Catalog identity is (name [, repository]); publication scope is the
tenant recipe-grant mechanism. Catalog $keys are 40-hex STRINGS, not
ints — assertions pin that so an int-key regression cannot sneak in.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock, patch


from pyvergeos.exceptions import (  # real classes: a Mock here is
    APIError,                       # CALLED as a side_effect, not raised
    AuthenticationError,
    NotFoundError,
    ValidationError,
    VergeConnectionError,
)


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


def make_client(catalogs=(), repo_key=1, repositories=None):
    client = MagicMock()
    client.catalogs.list.return_value = list(catalogs)
    # resolve_one() lists and matches client-side; it never calls get(name=).
    if repositories is None:
        repositories = [make_row({'$key': repo_key, 'name': 'Local'})]
    client.catalog_repositories.list.return_value = list(repositories)
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
        # An empty repository table: resolve_one finds no match and raises
        # NotFoundError, which the module turns into a named message.
        client = make_client(repositories=[])
        module = make_module(base_params())

        run(module, client)

        assert "Repository 'Local' not found" \
            in module.fail_json.call_args.kwargs['msg']

    def test_duplicate_repository_names_are_refused(self):
        # Two repositories called 'Local'. The old get(name=) returned the
        # first silently; resolve_one refuses to guess (#72).
        client = make_client(repositories=[
            make_row({'$key': 1, 'name': 'Local'}),
            make_row({'$key': 7, 'name': 'Local'}),
        ])
        module = make_module(base_params())

        run(module, client)

        msg = module.fail_json.call_args.kwargs['msg']
        assert 'refusing to guess' in msg
        assert '1' in msg and '7' in msg
        client.catalogs.create.assert_not_called()


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


class TestLookupIsClientSideAndNeverFiltersByName:
    """The name must never reach a server-side OData filter.

    Two separate defects make a ``name eq '...'`` lookup unsafe here, and
    this module has now been bitten by both:

    1. Hand-rolled SQL-style escaping (``'`` -> ``''``) is rejected by
       VergeOS 26.1.8 with ValidationError "Invalid argument", so a
       catalog whose name contained an apostrophe could not be looked up,
       created, converged or DELETED -- including ``state: absent`` on a
       catalog that did not exist, which should be a no-op.
    2. Letting pyvergeos build the filter fixes (1) but inherits a
       platform defect: VergeOS 26.1.8 strips ``{...}`` from filter string
       literals, so the query silently matches a DIFFERENT catalog.
       Measured live through this module -- ``state: absent`` on
       ``zz-jw-c{x}at`` deleted ``zz-jw-cat`` and reported changed=true.
       See verge-io/engineering#20.

    So these assertions pin the *absence* of server-side name filtering,
    not a particular escape. Client-side matching is immune to both and is
    the house style everywhere else in this collection.
    """

    def test_lookup_lists_without_any_filter(self):
        client = make_client(catalogs=[make_catalog()])
        module = make_module(base_params())

        run(module, client)

        assert client.catalogs.list.call_args.args == ()
        assert client.catalogs.list.call_args.kwargs == {}

    def test_lookup_never_passes_the_name_to_the_sdk(self):
        client = make_client(catalogs=[make_catalog()])
        module = make_module(base_params())

        run(module, client)

        kwargs = client.catalogs.list.call_args.kwargs
        assert 'name' not in kwargs
        assert 'filter' not in kwargs

    @pytest.mark.parametrize('name', [
        "O'Brien's Images",     # defect (1)
        'braced{x}name',        # defect (2) -- must not match 'bracedname'
        'back\\slash',
        'quote"dbl',
        'semi;colon',
        'per%cent',
    ])
    def test_awkward_names_match_exactly_or_not_at_all(self, name):
        """A near-miss neighbour must not be selected."""
        neighbour = make_catalog(key='n' * 40, name='bracedname')
        client = make_client(catalogs=[neighbour])
        module = make_module(base_params(name=name, state='absent'))

        run(module, client)

        # nothing matched, so absent is a converged no-op and, crucially,
        # the neighbour is NOT deleted
        client.catalogs.delete.assert_not_called()
        module.fail_json.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_exact_match_is_still_found(self):
        """Client-side matching must not become so cautious it finds nothing."""
        client = make_client(catalogs=[make_catalog(name='bracedname'),
                                       make_catalog(key='z' * 40,
                                                    name='braced{x}name')])
        module = make_module(base_params(name='braced{x}name', state='absent'))

        run(module, client)

        client.catalogs.delete.assert_called_once_with('z' * 40)
        assert module.exit_json.call_args.kwargs['changed'] is True


class TestSdkErrorsAreHandled:
    """Every SDK exception the module imports must reach a named failure.

    The catch-all ``except Exception`` at the bottom of main() means an
    unhandled type does not crash -- it produces "Unexpected error: ...",
    which looks like a bug in the collection rather than a server saying no.
    Parametrising over the whole import list also keeps the imports honest:
    if one stops being handled, this fails instead of pylint quietly flagging
    an unused name.
    """

    @pytest.mark.parametrize('exc', [
        APIError, AuthenticationError, ValidationError, VergeConnectionError,
    ])
    def test_sdk_errors_do_not_fall_through_to_unexpected(self, exc):
        client = make_client()
        client.catalogs.list.side_effect = exc('server said no')
        module = make_module(base_params())

        run(module, client)

        module.fail_json.assert_called_once()
        msg = module.fail_json.call_args.kwargs['msg']
        assert 'Unexpected error' not in msg, (
            "%s reached the catch-all instead of sdk_error_handler" % exc.__name__)

    def test_not_found_is_reported_as_not_found(self):
        client = make_client()
        client.catalogs.list.side_effect = NotFoundError('gone')
        module = make_module(base_params())

        run(module, client)

        assert 'Resource not found' in module.fail_json.call_args.kwargs['msg']
