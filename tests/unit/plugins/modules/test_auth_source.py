#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the auth_source and auth_source_info modules.

The load-bearing behavior: on VergeOS 26.1.8 a settings update REPLACES
the stored document (the SDK docstring claims merge) -- so the module
must always transmit the complete declared settings, and its drift
comparison must ignore the server-injected `debug` mirror key. The raw
API also returns client_secret in cleartext; the info module strips it.
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


FULL_SETTINGS = {'client_id': 'id', 'client_secret': 's3cret',
                 'scope': 'openid profile email'}


def make_source(key=3, name='Corporate Azure', driver='azure', menu=False,
                debug=False, settings=None, **colors):
    data = {'$key': key, 'name': name, 'driver': driver, 'menu': menu,
            'debug': debug,
            'button_background_color': colors.get('bg', ''),
            'button_color': colors.get('fg', ''),
            'button_fa_icon': colors.get('icon', ''),
            'icon_color': colors.get('icon_color', '')}
    if settings is not None:
        data['settings'] = settings
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    obj.key = key
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
        'name': 'Corporate Azure',
        'state': 'present',
        'driver': 'azure',
        'settings': None,
        'menu': None,
        'debug': None,
        'button_background_color': None,
        'button_color': None,
        'button_fa_icon': None,
        'icon_color': None,
    }
    params.update(overrides)
    return params


def _mod():
    from ansible_collections.vergeio.vergeos.plugins.modules import (
        auth_source,
    )
    return auth_source


def _info():
    from ansible_collections.vergeio.vergeos.plugins.modules import (
        auth_source_info,
    )
    return auth_source_info


class TestSettingsDiffer:
    def test_server_debug_key_is_not_drift(self):
        m = _mod()
        current = dict(FULL_SETTINGS, debug=False)
        assert m.settings_differ(current, dict(FULL_SETTINGS)) is False

    def test_value_change_is_drift(self):
        m = _mod()
        desired = dict(FULL_SETTINGS, scope='openid email')
        assert m.settings_differ(dict(FULL_SETTINGS), desired) is True

    def test_removed_key_is_drift(self):
        # replace semantics: a declared doc missing a live key means
        # the user wants that key gone
        m = _mod()
        desired = {k: v for k, v in FULL_SETTINGS.items()
                   if k != 'client_secret'}
        assert m.settings_differ(dict(FULL_SETTINGS), desired) is True

    def test_none_current_vs_empty_desired(self):
        m = _mod()
        assert m.settings_differ(None, {}) is False


class TestUpdate:
    def test_converged_source_is_unchanged(self):
        m = _mod()
        source = make_source(settings=dict(FULL_SETTINGS, debug=False))
        module = make_module(base_params(settings=dict(FULL_SETTINGS)))
        client = MagicMock()

        changed, settings_changed, unused = m.update_source(
            module, client, source, [])

        assert changed is False
        assert settings_changed is False
        client.auth_sources.update.assert_not_called()

    def test_settings_drift_sends_the_complete_document(self):
        m = _mod()
        source = make_source(settings=dict(FULL_SETTINGS, debug=False))
        desired = dict(FULL_SETTINGS, scope='openid email')
        module = make_module(base_params(settings=desired))
        client = MagicMock()

        changed, settings_changed, unused = m.update_source(
            module, client, source, [])

        assert changed is True and settings_changed is True
        client.auth_sources.update.assert_called_once_with(
            3, settings=desired)

    def test_driver_change_fails_loudly(self):
        m = _mod()
        source = make_source(driver='okta')
        module = make_module(base_params(driver='azure'))

        with pytest.raises(SystemExit):
            m.update_source(module, MagicMock(), source, [])
        msg = module.fail_json.call_args.kwargs['msg']
        assert 'cannot be changed' in msg

    def test_menu_flip_without_settings(self):
        m = _mod()
        source = make_source(menu=False)
        module = make_module(base_params(menu=True))
        client = MagicMock()

        changed, settings_changed, unused = m.update_source(
            module, client, source, [])

        assert changed is True and settings_changed is False
        client.auth_sources.update.assert_called_once_with(3, menu=True)

    def test_check_mode_does_not_call_the_api(self):
        m = _mod()
        source = make_source(menu=False)
        module = make_module(base_params(menu=True), check_mode=True)
        client = MagicMock()

        changed = m.update_source(module, client, source, [])[0]

        assert changed is True
        client.auth_sources.update.assert_not_called()


class TestCreate:
    def test_create_passes_settings_and_styling(self):
        m = _mod()
        module = make_module(base_params(settings=dict(FULL_SETTINGS),
                                         menu=True,
                                         button_fa_icon='bi-microsoft'))
        client = MagicMock()

        m.create_source(module, client, [])

        client.auth_sources.create.assert_called_once_with(
            name='Corporate Azure', driver='azure',
            settings=FULL_SETTINGS, menu=True,
            button_fa_icon='bi-microsoft')

    def test_create_applies_debug_as_followup(self):
        # the SDK's create() has no debug parameter
        m = _mod()
        module = make_module(base_params(debug=True))
        client = MagicMock()
        client.auth_sources.create.return_value = make_source()

        m.create_source(module, client, [])

        client.auth_sources.update.assert_called_once_with(3, debug=True)

    def test_check_mode_does_not_create(self):
        m = _mod()
        module = make_module(base_params(), check_mode=True)
        client = MagicMock()

        assert m.create_source(module, client, []) is None
        client.auth_sources.create.assert_not_called()


class TestInfoRedaction:
    def test_client_secret_is_stripped(self):
        info = _info()
        source = make_source(settings=dict(FULL_SETTINGS))

        result = info.source_result(source, include_settings=True)

        assert result['settings']['client_id'] == 'id'
        assert 'client_secret' not in result['settings']

    def test_settings_omitted_by_default(self):
        info = _info()
        source = make_source(settings=dict(FULL_SETTINGS))

        result = info.source_result(source, include_settings=False)

        assert 'settings' not in result

    def test_result_never_carries_raw_row(self):
        info = _info()
        result = info.source_result(make_source(), include_settings=True)
        assert result['settings'] == {}
        assert result['name'] == 'Corporate Azure'


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'auth_source.get_vergeos_client', return_value=mock_client), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'auth_source.HAS_PYVERGEOS', True), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'auth_source.AnsibleModule', return_value=mock_module):
        try:
            _mod().main()
        except SystemExit:
            pass


class TestFindSource:
    """The lookup path had no test at all: every test above calls a helper
    directly, so nothing ran main() and nothing touched find_source."""

    def test_the_name_never_reaches_the_api_as_a_filter(self):
        """Not about duplicates -- auth source names carry a unique
        constraint, so there is nothing to disambiguate. It is about
        pyVergeOS#100: get(name=) builds an OData filter out of a display name
        that a human typed. The brace-stripping fix landed in pyvergeos
        1.2.8, which is now the floor. The lookup stays client-side.
        """
        client = MagicMock()
        client.auth_sources.list.return_value = [make_source()]

        module = make_module(base_params())
        run_main(module, client)

        assert client.auth_sources.get.call_args_list == [] or all(
            'name' not in call.kwargs
            for call in client.auth_sources.get.call_args_list)

    def test_settings_are_fetched_by_key_not_listed(self):
        """The list projection must not carry settings: the API returns
        client_secret in cleartext to anyone who reads them."""
        m = _mod()
        client = MagicMock()
        client.auth_sources.list.return_value = [make_source()]
        client.auth_sources.get.return_value = make_source(
            settings=dict(FULL_SETTINGS, debug=False))

        module = make_module(base_params(settings=dict(FULL_SETTINGS)))
        run_main(module, client)

        client.auth_sources.get.assert_called_once_with(
            3, include_settings=True)
        assert 'settings' not in m.SOURCE_FIELDS

    def test_settings_are_not_fetched_when_not_declared(self):
        """A task that does not manage settings should not pull a client
        secret across the wire to ignore it."""
        client = MagicMock()
        client.auth_sources.list.return_value = [make_source()]

        module = make_module(base_params(menu=True))
        run_main(module, client)

        client.auth_sources.get.assert_not_called()

    def test_a_missing_source_is_created(self):
        client = MagicMock()
        client.auth_sources.list.return_value = []
        client.auth_sources.create.return_value = make_source()

        module = make_module(base_params())
        run_main(module, client)

        client.auth_sources.create.assert_called_once()
        assert module.exit_json.call_args[1]['changed'] is True

    def test_absent_on_a_missing_source_is_no_change(self):
        client = MagicMock()
        client.auth_sources.list.return_value = []

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.auth_sources.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_absent_deletes(self):
        client = MagicMock()
        client.auth_sources.list.return_value = [make_source()]

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.auth_sources.delete.assert_called_once_with(3)
        assert module.exit_json.call_args[1]['changed'] is True


def run_info(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'auth_source_info.get_vergeos_client', return_value=mock_client), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'auth_source_info.HAS_PYVERGEOS', True), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'auth_source_info.AnsibleModule', return_value=mock_module):
        try:
            _info().main()
        except SystemExit:
            pass


def info_params(**overrides):
    params = {'name': None, 'include_settings': False}
    params.update(overrides)
    return params


class TestInfoNameLookup:
    """auth_source_info was the remaining get(name=) call (#72).

    Zero matches stay an empty list. One match is that source. More than
    one match fails instead of returning whichever row the API listed first.
    """

    def test_unknown_name_is_an_empty_list(self):
        client = MagicMock()
        client.auth_sources.list.return_value = []
        module = make_module(info_params(name='missing'))

        run_info(module, client)

        result = module.exit_json.call_args.kwargs
        assert result['changed'] is False
        assert result['auth_sources'] == []
        client.auth_sources.get.assert_not_called()

    def test_one_match_is_returned(self):
        client = MagicMock()
        client.auth_sources.list.return_value = [
            make_source(key=1, name='Other'),
            make_source(key=3, name='Corporate Azure'),
        ]
        module = make_module(info_params(name='Corporate Azure'))

        run_info(module, client)

        sources = module.exit_json.call_args.kwargs['auth_sources']
        assert [s['key'] for s in sources] == [3]
        assert sources[0]['name'] == 'Corporate Azure'
        assert 'name' not in client.auth_sources.list.call_args.kwargs
        assert 'filter' not in client.auth_sources.list.call_args.kwargs
        client.auth_sources.get.assert_not_called()

    def test_duplicate_names_are_refused(self):
        client = MagicMock()
        client.auth_sources.list.return_value = [
            make_source(key=3, name='Corporate Azure'),
            make_source(key=9, name='Corporate Azure'),
        ]
        module = make_module(info_params(name='Corporate Azure'))

        run_info(module, client)

        msg = module.fail_json.call_args.kwargs['msg']
        assert 'refusing to guess' in msg
        assert '3' in msg and '9' in msg
        module.exit_json.assert_not_called()
        client.auth_sources.get.assert_not_called()

    def test_settings_are_fetched_by_key_and_the_secret_is_stripped(self):
        client = MagicMock()
        client.auth_sources.list.return_value = [make_source()]
        client.auth_sources.get.return_value = make_source(
            settings=dict(FULL_SETTINGS))
        module = make_module(info_params(name='Corporate Azure',
                                         include_settings=True))

        run_info(module, client)

        client.auth_sources.get.assert_called_once_with(3, include_settings=True)
        assert 'name' not in client.auth_sources.get.call_args.kwargs
        settings = module.exit_json.call_args.kwargs['auth_sources'][0]['settings']
        assert settings['client_id'] == 'id'
        assert 'client_secret' not in settings


class TestSdkErrorsAreHandled:
    @pytest.mark.parametrize('exc', [
        APIError, AuthenticationError, ValidationError, VergeConnectionError,
    ])
    def test_sdk_errors_do_not_fall_through_to_unexpected(self, exc):
        client = MagicMock()
        client.auth_sources.list.side_effect = exc('server said no')
        module = make_module(base_params())

        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'Unexpected error' not in module.fail_json.call_args[1]['msg']

    def test_not_found_means_absent_not_failure(self):
        client = MagicMock()
        client.auth_sources.list.return_value = []
        client.auth_sources.create.return_value = make_source()
        module = make_module(base_params())

        run_main(module, client)

        module.fail_json.assert_not_called()
        assert isinstance(NotFoundError('x'), Exception)
