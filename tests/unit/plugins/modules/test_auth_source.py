#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the auth_source and auth_source_info modules.

The load-bearing behavior: on VergeOS 26.1.8 a settings update REPLACES
the stored document (the SDK docstring claims merge) -- so the module
must always transmit the complete declared settings, and its drift
comparison must ignore the server-injected `debug` mirror key. The raw
API also returns client_secret in cleartext; the info module strips it.
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

        changed, settings_changed, _ = m.update_source(
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

        changed, settings_changed, _ = m.update_source(
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

        changed, settings_changed, _ = m.update_source(
            module, client, source, [])

        assert changed is True and settings_changed is False
        client.auth_sources.update.assert_called_once_with(3, menu=True)

    def test_check_mode_does_not_call_the_api(self):
        m = _mod()
        source = make_source(menu=False)
        module = make_module(base_params(menu=True), check_mode=True)
        client = MagicMock()

        changed, _, _ = m.update_source(module, client, source, [])

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
