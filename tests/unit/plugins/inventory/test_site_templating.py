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

import pytest
from unittest.mock import MagicMock

from ansible.errors import AnsibleError


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
