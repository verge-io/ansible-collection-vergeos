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
# Adding a module here opts it into the guard, which is the point: the guard
# is only as good as its coverage. 'vm' is deliberately ABSENT -- adding it is
# what found #87 (machine_subtype, bios_type and network are not VM fields),
# and it goes in once that is fixed.
MAPPED_MODULES = ['network', 'nic', 'drive', 'user', 'catalog', 'api_key']


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

    def test_catalog(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import catalog
        for api_field in catalog.UPDATE_FIELD_MAP.values():
            assert api_field in catalog.COMPARISON_FIELDS, (
                "catalog diffs %r but never fetches it" % api_field)

    def test_api_key(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import api_key
        for api_field in api_key.UPDATE_FIELD_MAP.values():
            assert api_field in api_key.COMPARISON_FIELDS, (
                "api_key diffs %r but never fetches it" % api_field)

    def test_api_key_fetches_every_column_it_returns(self):
        """find_keys() asks for LIST_FIELDS explicitly rather than trusting
        the SDK's default projection. If the two lists ever disagree, the
        module returns None for a documented field and -- worse -- matches on
        a user_name it never fetched, so every run creates another key."""
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            api_keys as shared,
        )
        for column in shared.RESULT_FIELD_MAP.values():
            assert column in shared.LIST_FIELDS, (
                "api_keys returns %r but does not fetch it" % column)


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

    def test_catalog_update_is_a_subset_of_create(self):
        """Unlike network, catalog's two paths are deliberately different:
        name and repository are the catalog's identity, not settings. What
        must hold is that nothing is updatable that cannot be created, which
        would be a parameter with no way to reach its initial value."""
        from ansible_collections.vergeio.vergeos.plugins.modules import catalog
        create = set(catalog.CREATE_PARAM_MAP)
        update = set(catalog.UPDATE_FIELD_MAP)
        assert update <= create, (
            "catalog can update parameters it cannot create: %s"
            % sorted(update - create))
        assert create - update == set(catalog.IDENTITY_PARAMS), (
            "the create-only parameters should be exactly the identity ones. "
            "create-only=%s IDENTITY_PARAMS=%s"
            % (sorted(create - update), sorted(catalog.IDENTITY_PARAMS)))

    def test_api_key_update_is_a_subset_of_create(self):
        """user and name are the key's identity: a different pair is a
        different key, which is a create plus a revoke."""
        from ansible_collections.vergeio.vergeos.plugins.modules import api_key
        create = set(api_key.CREATE_PARAM_MAP)
        update = set(api_key.UPDATE_FIELD_MAP)
        assert update <= create, (
            "api_key can update parameters it cannot create: %s"
            % sorted(update - create))
        assert create - update == set(api_key.IDENTITY_PARAMS)

    def test_api_key_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import api_key
        spec = _argument_spec_options(api_key)
        for param in set(api_key.CREATE_PARAM_MAP) | set(api_key.UPDATE_FIELD_MAP):
            assert param in spec, (
                "api_key maps %r to an API field but does not accept it as an "
                "option -- the mapping is dead code" % param)

    def test_catalog_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import catalog
        spec = _argument_spec_options(catalog)
        for param in set(catalog.CREATE_PARAM_MAP) | set(catalog.UPDATE_FIELD_MAP):
            assert param in spec, (
                "catalog maps %r to an API field but does not accept it as an "
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
        'drive': {
            'tier': 'issue #8 -- the API field is preferred_tier',
            'read_only': 'the API field is readonly, without the underscore',
        },
        'user': {
            'full_name': 'the API field is displayname',
            'user_password': 'the API field is password',
        },
        # api_key needs no renames on the write path either -- but its READ
        # path does: the API spells the last-login pair lastlogin_*, and that
        # mapping lives in module_utils/api_keys.py so the info module cannot
        # drift from it.
        'api_key': {},
        # catalog needs no renames: all five parameters are real columns on
        # the live table (checked on 26.1.8). The entry is empty rather than
        # absent so the parametrised test below covers it and fails loudly if
        # someone adds catalog to MAPPED_MODULES' sibling maps without
        # thinking about which names the API actually uses.
        'catalog': {},
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


class TestVmIsNotYetMapped:
    """`vm` is excluded from MAPPED_MODULES on purpose.

    Declaring its field map is exactly what surfaced #87: `machine_subtype`,
    `bios_type` and `network` are not columns on a VM, so the API discards
    them with HTTP 200 and the VM never converges. Adding `vm` to
    MAPPED_MODULES before #87 is fixed would simply make this suite red.

    This test is the reminder, and it expires by itself: once vm declares a
    map, it fails until vm is added to MAPPED_MODULES.
    """

    def test_vm_joins_the_guard_once_it_declares_a_map(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        if hasattr(vm, 'UPDATE_FIELD_MAP'):
            assert 'vm' in MAPPED_MODULES, (
                "vm now declares UPDATE_FIELD_MAP, so add it to "
                "MAPPED_MODULES -- see #87")
