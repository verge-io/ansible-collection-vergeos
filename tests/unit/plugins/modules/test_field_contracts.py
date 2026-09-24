#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Structural guard for the compare-and-map defect class (issue #75).

Four times now the same bug has shipped:

    #8   tier          sent where the API field is preferred_tier
    #10  ip_address    sent where the API field is ipaddress
    #18  dhcp_end      sent where the API field is dhcp_stop
         dns_servers   correct on create, wrong on update
         subnet_mask   no such field at all
    #59  mac_address   right field name, case-sensitive value comparison

Each was found by hand on a live system, and each fix was specific to the
field reported. The reason it keeps recurring is that the VergeOS API accepts
unknown field names with HTTP 200 and discards them, so nothing fails loudly.

Three properties catch the class. Two of them need a live system and live in
tests/live/verify-field-contract.yml. The third is structural and runs here,
in CI, on every PR:

  1. every field name the module sends exists on the resource   -- live
  2. every documented parameter round-trips                     -- live
  3. a second apply reports changed=false                       -- live, plus
                                                                   the
                                                                   per-module
                                                                   unit tests

  0. (this file) the maps are internally consistent: nothing is diffed that
     is never fetched, nothing is documented that cannot be set, and the
     create and update paths agree on which parameters they handle.

Property 0 is not a substitute for 1 and 2 -- it cannot know what the API
calls a field. It catches the *asymmetries*, which is how #18's dns_servers
defect behaved: correct on create, silently discarded on update.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import re

import pytest


def _module_source(name):
    import importlib
    mod = importlib.import_module(
        'ansible_collections.vergeio.vergeos.plugins.modules.%s' % name)
    return mod


def _documented_options(mod):
    import yaml
    doc = yaml.safe_load(re.search(r"DOCUMENTATION = r?'''(.*?)'''",
                                   open(mod.__file__).read(), re.S).group(1))
    return set(doc.get('options') or {})


def _argument_spec_options(mod):
    """Parse argument_spec keys out of the source.

    Calling main() is not an option -- it builds an AnsibleModule -- so the
    keys are read from the argument_spec.update(...) call itself.
    """
    src = open(mod.__file__).read()
    block = re.search(r'argument_spec\.update\((.*?)\n    \)', src, re.S).group(1)
    return set(re.findall(r'^\s{8}(\w+)=dict\(', block, re.M))


# Modules that declare their API field mapping explicitly. Adding a module
# here is the point: it opts that module into the guard.
MAPPED_MODULES = ['network', 'nic']


class TestDocumentationMatchesArgumentSpec:
    """A documented option that is not in argument_spec cannot be set, and an
    option in argument_spec that is not documented cannot be found."""

    @pytest.mark.parametrize('name', MAPPED_MODULES)
    def test_every_documented_option_is_settable(self, name):
        mod = _module_source(name)
        documented = _documented_options(mod)
        spec = _argument_spec_options(mod)
        # The shared auth fragment contributes host/username/password/etc.
        orphans = documented - spec
        assert not orphans, (
            "%s documents options that are not in argument_spec, so they "
            "cannot be set: %s" % (name, sorted(orphans)))

    @pytest.mark.parametrize('name', MAPPED_MODULES)
    def test_every_module_specific_option_is_documented(self, name):
        mod = _module_source(name)
        documented = _documented_options(mod)
        spec = _argument_spec_options(mod)
        undocumented = spec - documented
        assert not undocumented, (
            "%s accepts options it does not document: %s"
            % (name, sorted(undocumented)))


class TestEveryDiffedFieldIsFetched:
    """#18: a field that is diffed but never fetched reads as None, so the
    module reports changed on every run and never converges."""

    def test_network(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import network
        for api_field in network.UPDATE_FIELD_MAP.values():
            assert api_field in network.COMPARISON_FIELDS, (
                "network diffs %r but never fetches it" % api_field)
        assert network.UPLINK_API_FIELD in network.COMPARISON_FIELDS


class TestCreateAndUpdatePathsAgree:
    """#18's subtlest failure: dns_servers was correct on create and silently
    discarded on update. Nothing compared the two paths, so the asymmetry
    survived the #8 and #10 fixes."""

    def test_network_create_and_update_cover_the_same_parameters(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import network
        create = set(network.CREATE_PARAM_MAP)
        update = set(network.UPDATE_FIELD_MAP)
        assert create == update, (
            "the network create and update paths handle different parameters. "
            "create-only=%s update-only=%s -- a parameter handled on only one "
            "path is #18's dns_servers defect."
            % (sorted(create - update), sorted(update - create)))

    def test_network_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import network
        spec = _argument_spec_options(network)
        for param in set(network.UPDATE_FIELD_MAP) | set(network.CREATE_PARAM_MAP):
            assert param in spec, (
                "network maps %r to an API field but does not accept it as an "
                "option -- the mapping is dead code" % param)

    def test_nic_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import nic
        spec = _argument_spec_options(nic)
        for param in nic.UPDATE_FIELD_MAP:
            assert param in spec, (
                "nic maps %r to an API field but does not accept it as an "
                "option" % param)


class TestNoKnownBadFieldNamesComeBack:
    """Named regression guards. Each of these shipped once; if one reappears
    the test names the issue it is repeating."""

    KNOWN_BAD = {
        'network': {
            'dhcp_end': 'issue #18 -- the API field is dhcp_stop',
            'dns_servers': 'issue #18 -- the API field is dnslist',
            'subnet_mask': 'issue #18 -- no such field exists on a vnet',
            'ip_address': 'issue #10 -- the API field is ipaddress',
        },
        'nic': {
            'mac_address': 'issue #59 -- the API field is macaddress',
        },
    }

    @pytest.mark.parametrize('name', MAPPED_MODULES)
    def test_no_module_parameter_name_is_used_as_an_api_field(self, name):
        mod = _module_source(name)
        bad = self.KNOWN_BAD[name]
        for param, api_field in mod.UPDATE_FIELD_MAP.items():
            if param in bad and api_field == param:
                pytest.fail(
                    "%s sends %r as the API field name again -- %s"
                    % (name, param, bad[param]))

    def test_subnet_mask_is_not_reintroduced(self):
        """It is not a vnet field; it was removed in #73 after being silently
        discarded since the first commit."""
        from ansible_collections.vergeio.vergeos.plugins.modules import network
        assert 'subnet_mask' not in _argument_spec_options(network)
        assert 'subnet_mask' not in network.UPDATE_FIELD_MAP
        assert 'subnet_mask' not in network.CREATE_PARAM_MAP


class TestTheLiveLadderCannotDriftFromTheCode:
    """Closes the loop.

    tests/live/verify-field-contract.yml asserts that a list of API field
    names exists on a live vnet. That list is only meaningful if it is the
    same list the module actually sends -- otherwise the ladder can go green
    while the module sends something else entirely.

    This test keeps the two in step offline, in CI, with no live system. The
    ladder keeps the code honest against the API; this keeps the ladder
    honest against the code.
    """

    def _ladder_fields(self):
        import os
        import yaml
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.abspath(os.path.join(here, '..', '..', '..', '..'))
        path = os.path.join(root, 'tests', 'live', 'verify-field-contract.yml')
        assert os.path.exists(path), "the live ladder is missing: %s" % path
        plays = yaml.safe_load(open(path))
        for play in plays:
            fields = (play.get('vars') or {}).get('network_api_fields')
            if fields:
                return set(fields)
        pytest.fail("network_api_fields not found in the live ladder")

    def test_ladder_field_list_matches_the_module(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import network
        expected = set(network.UPDATE_FIELD_MAP.values()) | {network.UPLINK_API_FIELD}
        actual = self._ladder_fields()
        assert actual == expected, (
            "tests/live/verify-field-contract.yml is out of step with "
            "network.UPDATE_FIELD_MAP. ladder-only=%s module-only=%s"
            % (sorted(actual - expected), sorted(expected - actual)))
