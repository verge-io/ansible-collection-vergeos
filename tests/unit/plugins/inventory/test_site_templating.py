#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Site templating and site-failure handling for the inventory plugin (#26).

The plugin documented `password: "{{ lookup('env', 'VERGEOS_PASSWORD') }}"`
in six places and never templated it. The literal Jinja string went to the
API as the password, authentication failed, and the plugin fail-softed to an
empty inventory with rc 0 -- which in CI reads as "no hosts matched" and goes
green.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import inspect

import pytest
from unittest.mock import MagicMock, patch

from ansible.errors import AnsibleError
from ansible.parsing.dataloader import DataLoader


def _plugin():
    from ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms import (
        InventoryModule)
    im = InventoryModule()
    im.display = MagicMock()
    return im


class TestTemplateSites:
    """Jinja in a site field must be resolved, not passed through."""

    def _render(self, sites, mapping):
        """Template `sites` with a Templar stubbed to a known mapping.

        The real trust model is exercised by the live check in the PR; here
        we only need to prove the plugin *calls* the templar on every string
        and reassembles the structure correctly.
        """
        im = _plugin()
        loader = MagicMock()

        import ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms as mod
        real_templar = mod.Templar
        try:
            fake = MagicMock()
            fake.template.side_effect = lambda v: mapping.get(v, v)
            mod.Templar = MagicMock(return_value=fake)
            return im._template_sites(sites, loader)
        finally:
            mod.Templar = real_templar

    def test_a_jinja_password_is_resolved(self):
        out = self._render(
            [{'name': 'lab', 'host': 'h', 'username': 'u',
              'password': "{{ lookup('env', 'VERGEOS_PASSWORD') }}"}],
            {"{{ lookup('env', 'VERGEOS_PASSWORD') }}": 's3cret'})
        assert out[0]['password'] == 's3cret', (
            "the literal Jinja string reached the API as the password -- #26")

    def test_every_string_field_is_templated_not_just_password(self):
        """host, username and api_key are all documented with lookup()."""
        out = self._render(
            [{'name': 'lab', 'host': '{{H}}', 'username': '{{U}}',
              'api_key': '{{K}}'}],
            {'{{H}}': 'vergeos.local', '{{U}}': 'admin', '{{K}}': 'tok'})
        assert out[0]['host'] == 'vergeos.local'
        assert out[0]['username'] == 'admin'
        assert out[0]['api_key'] == 'tok'

    def test_non_string_values_survive_unchanged(self):
        out = self._render(
            [{'name': 'lab', 'host': 'h', 'insecure': True, 'timeout': 60}], {})
        assert out[0]['insecure'] is True
        assert out[0]['timeout'] == 60

    def test_nested_structures_are_templated(self):
        out = self._render([{'name': 'lab', 'tags': ['{{T}}'],
                             'extra': {'k': '{{V}}'}}],
                           {'{{T}}': 'prod', '{{V}}': 'x'})
        assert out[0]['tags'] == ['prod']
        assert out[0]['extra']['k'] == 'x'

    def test_every_site_is_templated(self):
        out = self._render([{'name': 'a', 'password': '{{P1}}'},
                            {'name': 'b', 'password': '{{P2}}'}],
                           {'{{P1}}': 'one', '{{P2}}': 'two'})
        assert [s['password'] for s in out] == ['one', 'two']


def _load_sites(path, text):
    """Load a site list the way the plugin's config reader does.

    ansible-core 2.19+ only templates strings marked trusted.
    ``_read_config_data()`` loads with ``trusted_as_template=True``. Older
    cores have no such argument and template every string.
    """
    path.write_text(text)
    loader = DataLoader()
    kwargs = {}
    if 'trusted_as_template' in inspect.signature(loader.load_from_file).parameters:
        kwargs['trusted_as_template'] = True
    data = loader.load_from_file(str(path), **kwargs)
    return loader, data['sites']


_SITE_YAML = """\
plugin: vergeio.vergeos.vergeos_vms
sites:
  - name: lab
    host: "{{ lookup('env', 'VERGEOS_HOST') }}"
    username: "{{ lookup('env', 'VERGEOS_USERNAME') }}"
    password: "{{ lookup('env', 'VERGEOS_PASSWORD') }}"
    api_key: "{{ lookup('env', 'VERGEOS_API_KEY') }}"
    insecure: true
    timeout: 45
"""


class TestLookupEnv:
    """The documented lookup('env') form, through the real templar."""

    def test_site_fields_resolve_lookup_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv('VERGEOS_HOST', 'vergeos.example.com')
        monkeypatch.setenv('VERGEOS_USERNAME', 'admin')
        monkeypatch.setenv('VERGEOS_PASSWORD', 's3cret-from-env')
        monkeypatch.setenv('VERGEOS_API_KEY', 'tok-from-env')

        loader, sites = _load_sites(tmp_path / 'lab.vergeos_vms.yml', _SITE_YAML)
        out = _plugin()._template_sites(sites, loader)

        assert out[0]['host'] == 'vergeos.example.com'
        assert out[0]['username'] == 'admin'
        assert out[0]['password'] == 's3cret-from-env'
        assert out[0]['api_key'] == 'tok-from-env'
        assert out[0]['insecure'] is True
        assert out[0]['timeout'] == 45
        assert '{{' not in out[0]['password']

    def test_parse_connects_with_the_resolved_password(self, tmp_path, monkeypatch):
        """The value that reaches the client is the env var, not the Jinja."""
        monkeypatch.setenv('VERGEOS_HOST', 'vergeos.example.com')
        monkeypatch.setenv('VERGEOS_USERNAME', 'admin')
        monkeypatch.setenv('VERGEOS_PASSWORD', 's3cret-from-env')
        monkeypatch.delenv('VERGEOS_API_KEY', raising=False)

        text = """\
plugin: vergeio.vergeos.vergeos_vms
sites:
  - name: lab
    host: "{{ lookup('env', 'VERGEOS_HOST') }}"
    username: "{{ lookup('env', 'VERGEOS_USERNAME') }}"
    password: "{{ lookup('env', 'VERGEOS_PASSWORD') }}"
    insecure: true
"""
        loader, sites = _load_sites(tmp_path / 'lab.vergeos_vms.yml', text)

        im = _plugin()
        im.inventory = MagicMock()
        im._options = {
            'sites': sites,
            'cache': False,
            'max_workers': 2,
            'site_timeout': 30,
            'strict_sites': False,
        }
        im.get_option = im._options.get
        im._read_config_data = lambda path: None
        im._populate_inventory = lambda site_data: None

        seen = {}

        def fetch(site_config):
            seen['site'] = site_config
            return {
                'site': site_config['name'],
                'site_url': site_config['host'],
                'vms': [],
                'error': None,
            }

        im._fetch_site = fetch

        plugin_mod = (
            'ansible_collections.vergeio.vergeos.plugins.inventory.vergeos_vms')
        with patch.object(im.__class__.__bases__[0], 'parse', lambda *a, **k: None):
            with patch(plugin_mod + '.HAS_PYVERGEOS', True):
                im.parse(im.inventory, loader, str(tmp_path / 'lab.vergeos_vms.yml'))

        assert seen['site']['password'] == 's3cret-from-env'
        assert seen['site']['host'] == 'vergeos.example.com'
        assert seen['site']['username'] == 'admin'
        assert '{{' not in seen['site']['password']


class TestSiteFailureHandling:
    """A partial inventory that looks complete is worse than an error."""

    def _check(self, site_data, strict_sites=False):
        im = _plugin()
        im.get_option = MagicMock(side_effect=lambda k: {'strict_sites': strict_sites}[k])
        return im._check_site_failures(site_data)

    def test_all_sites_ok_is_silent(self):
        self._check([{'site': 'a', 'error': None}, {'site': 'b', 'error': None}])

    def test_every_site_failing_is_always_fatal(self):
        """Even with strict_sites false -- an empty inventory exits 0 and
        reads as 'no hosts matched', which is indistinguishable from
        success."""
        with pytest.raises(AnsibleError) as exc:
            self._check([{'site': 'a', 'error': 'Authentication failed'}],
                        strict_sites=False)
        assert 'Every configured site failed' in str(exc.value)
        assert 'Authentication failed' in str(exc.value), (
            "the error must carry the underlying cause, not just a count")

    def test_partial_failure_warns_by_default(self):
        im = _plugin()
        im.get_option = MagicMock(return_value=False)
        im._check_site_failures([{'site': 'a', 'error': 'boom'},
                                 {'site': 'b', 'error': None}])
        im.display.warning.assert_called_once()
        msg = im.display.warning.call_args[0][0]
        assert 'incomplete' in msg
        assert 'strict_sites' in msg, "the warning must name the way to escalate it"

    def test_partial_failure_is_fatal_under_strict_sites(self):
        with pytest.raises(AnsibleError) as exc:
            self._check([{'site': 'a', 'error': 'boom'},
                         {'site': 'b', 'error': None}], strict_sites=True)
        assert '1 of 2 sites failed' in str(exc.value)

    def test_the_failing_site_is_named(self):
        with pytest.raises(AnsibleError) as exc:
            self._check([{'site': 'denver', 'error': 'timeout'},
                         {'site': 'chicago', 'error': None}], strict_sites=True)
        assert 'denver' in str(exc.value)
        assert 'chicago' not in str(exc.value)
