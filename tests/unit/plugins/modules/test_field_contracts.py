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
# is only as good as its coverage. 'vm' was the last holdout: adding it is what
# found #87 (machine_subtype, bios_type and network are not VM fields), and it
# joins now that #87 is fixed. Every module with a field map is covered.
MAPPED_MODULES = ['network', 'nic', 'drive', 'user', 'catalog', 'api_key',
                  'group', 'vnet_rule', 'vm', 'nas_volume', 'nas_nfs_share',
                  'vm_export', 'tenant', 'auth_source', 'file']


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

    def test_vm(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        for api_field in vm.UPDATE_FIELD_MAP.values():
            assert api_field in vm.COMPARISON_FIELDS, (
                "vm diffs %r but never fetches it" % api_field)

    def test_vnet_rule(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import vnet_rule
        for api_field in vnet_rule.UPDATE_FIELD_MAP.values():
            assert api_field in vnet_rule.COMPARISON_FIELDS, (
                "vnet_rule diffs %r but never fetches it" % api_field)
        assert vnet_rule.ORDER_API_FIELD in vnet_rule.COMPARISON_FIELDS

    def test_group(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import group
        for api_field in group.UPDATE_FIELD_MAP.values():
            assert api_field in group.COMPARISON_FIELDS, (
                "group diffs %r but never fetches it" % api_field)

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

    def test_nas_volume(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            nas_volume,
        )
        for api_field in nas_volume.UPDATE_FIELD_MAP.values():
            assert api_field in nas_volume.COMPARISON_FIELDS, (
                "nas_volume diffs %r but never fetches it" % api_field)
        for column in nas_volume.COMPARISON_FIELDS:
            assert column in nas_volume.VOLUME_FIELDS, (
                'nas_volume compares %r but does not fetch it' % column)

    def test_nas_volume_asks_for_the_key_by_name(self):
        """`fields=all` on the volumes table does not include $key -- it
        returns `id` instead. A projection that forgot it would leave every
        update and delete targeting None."""
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            nas_volume,
        )
        assert '$key' in nas_volume.VOLUME_FIELDS

    def test_nas_nfs_share(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            nas_nfs_share,
        )
        for api_field in nas_nfs_share.UPDATE_FIELD_MAP.values():
            assert api_field in nas_nfs_share.COMPARISON_FIELDS, (
                "nas_nfs_share diffs %r but never fetches it" % api_field)
        for column in nas_nfs_share.COMPARISON_FIELDS:
            assert column in nas_nfs_share.SHARE_FIELDS, (
                'nas_nfs_share compares %r but does not fetch it' % column)

    def test_vm_export(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            vm_export,
        )
        for api_field in vm_export.UPDATE_FIELD_MAP.values():
            assert api_field in vm_export.COMPARISON_FIELDS, (
                "vm_export diffs %r but never fetches it" % api_field)
        for column in vm_export.COMPARISON_FIELDS:
            assert column in vm_export.EXPORT_FIELDS, (
                'vm_export compares %r but does not fetch it' % column)

    def test_tenant(self):
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            tenants as shared,
        )
        from ansible_collections.vergeio.vergeos.plugins.modules import tenant
        assert tenant.UPDATE_FIELD_MAP is shared.UPDATE_FIELD_MAP, (
            'tenant and tenant_info must read the same contract')
        for api_field in shared.UPDATE_FIELD_MAP.values():
            assert api_field in shared.COMPARISON_FIELDS, (
                "tenant diffs %r but never fetches it" % api_field)
        for column in shared.COMPARISON_FIELDS:
            assert column in shared.TENANT_FIELDS, (
                'tenant compares %r but does not fetch it' % column)

    def test_tenant_names_the_joins_it_depends_on(self):
        """`running` and `status` are not columns on a tenant -- they are
        joins on the status row. The module decides whether to power the
        tenant on from `running`, so a projection that omits it reports every
        tenant as stopped and powers on something already running (#97's
        shape, on a different table)."""
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            tenants as shared,
        )
        assert 'status#running as running' in shared.TENANT_FIELDS
        assert 'status#status as status' in shared.TENANT_FIELDS
        assert 'running' not in shared.TENANT_FIELDS
        assert 'status' not in shared.TENANT_FIELDS

    def test_tenant_storage_tier_number_is_a_join_not_the_tier_column(self):
        """The `tier` COLUMN on tenant_storage holds the storage tier's KEY.
        The tier NUMBER -- what the module's `tier` option means -- exists
        only as a join. Matching allocations on `tier` would compare a key to
        a number and silently find nothing, so every run would try to create
        an allocation that already exists."""
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            tenants as shared,
        )
        assert 'tier#tier as tier_number' in shared.STORAGE_FIELDS
        assert 'tier' not in shared.STORAGE_FIELDS

    def test_tenant_node_fetches_the_host_it_was_placed_on(self):
        """Issue #24: an unplaceable tenant node is not reported as a failure
        anywhere. `host_node` being empty is the only visible difference
        between "never placed" and "placed and still booting"."""
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            tenants as shared,
        )
        assert 'machine#status#node#$display as host_node' in shared.NODE_FIELDS

    def test_auth_source(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            auth_source,
        )
        for api_field in auth_source.UPDATE_FIELD_MAP.values():
            assert api_field in auth_source.COMPARISON_FIELDS, (
                "auth_source diffs %r but never fetches it" % api_field)
        # settings is the exception and deliberately so: it is fetched by key
        # with include_settings, because the API returns client_secret in
        # cleartext and the list projection must not carry it.
        for column in set(auth_source.COMPARISON_FIELDS) - {'settings'}:
            assert column in auth_source.SOURCE_FIELDS, (
                'auth_source compares %r but does not fetch it' % column)
        assert 'settings' not in auth_source.SOURCE_FIELDS

    def test_file(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import file
        for api_field in file.UPDATE_FIELD_MAP.values():
            assert api_field in file.COMPARISON_FIELDS, (
                "file diffs %r but never fetches it" % api_field)
        for column in file.COMPARISON_FIELDS:
            assert column in file.FILE_FIELDS, (
                'file compares %r but does not fetch it' % column)

    def test_file_fetches_the_size_idempotence_turns_on(self):
        """`filesize` is not in UPDATE_FIELD_MAP -- it is not settable -- but
        it is the whole basis of "already uploaded". A projection that dropped
        it would re-upload the entire catalogue on every run."""
        from ansible_collections.vergeio.vergeos.plugins.modules import file
        assert 'filesize' in file.FILE_FIELDS

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

    def test_vm_update_is_a_subset_of_create(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        create = set(vm.CREATE_PARAM_MAP)
        update = set(vm.UPDATE_FIELD_MAP)
        assert update <= create
        assert create - update == set(vm.IDENTITY_PARAMS)

    def test_vm_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        spec = _argument_spec_options(vm)
        for param in set(vm.CREATE_PARAM_MAP) | set(vm.UPDATE_FIELD_MAP):
            assert param in spec, (
                "vm maps %r to an API field but does not accept it as an "
                "option -- the mapping is dead code" % param)

    def test_vm_bios_type_is_translated_not_sent(self):
        """#87's subtlest part. `bios_type` stays as an option because
        'seabios'/'uefi' reads better than a flag -- but it is not a field.
        The column is the boolean `uefi`, and the translation is the fix."""
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        assert vm.UPDATE_FIELD_MAP['bios_type'] == 'uefi'
        assert vm.bios_to_uefi('uefi') is True
        assert vm.bios_to_uefi('seabios') is False

    def test_vm_fetches_boot_order_which_the_default_projection_omits(self):
        """Found while fixing #87, in the same file: boot_order is compared
        but is not in the SDK's default projection, so it read as None and the
        VM reported changed on every run."""
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        assert 'boot_order' in vm.VM_FIELDS
        for column in set(vm.COMPARISON_FIELDS) - {'snapshot_profile'}:
            assert column in vm.VM_FIELDS, (
                'vm compares %r but does not fetch it' % column)

    def test_vnet_rule_update_is_a_subset_of_create(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import vnet_rule
        create = set(vnet_rule.CREATE_PARAM_MAP)
        update = set(vnet_rule.UPDATE_FIELD_MAP)
        assert update <= create
        assert create - update == set(vnet_rule.IDENTITY_PARAMS)

    def test_vnet_rule_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import vnet_rule
        spec = _argument_spec_options(vnet_rule)
        for param in set(vnet_rule.CREATE_PARAM_MAP) | set(vnet_rule.UPDATE_FIELD_MAP):
            assert param in spec, (
                "vnet_rule maps %r to an API field but does not accept it as "
                "an option -- the mapping is dead code" % param)

    def test_vnet_rule_order_reaches_orderid_on_both_paths(self):
        """The one asymmetry-shaped thing in this module that is NOT a bug.

        The column is `orderid`. create() passes `order=` and lets the SDK
        write body['orderid']; update() passes `orderid=` straight through,
        because update is a raw kwargs passthrough. Both correct, by different
        routes -- which is precisely the shape that invites someone to "fix"
        one of them into a silent no-op. Both halves are pinned here.
        """
        import inspect
        import re
        from pyvergeos.resources.rules import NetworkRuleManager
        from ansible_collections.vergeio.vergeos.plugins.modules import vnet_rule

        assert vnet_rule.ORDER_API_FIELD == 'orderid'
        assert 'order' not in vnet_rule.UPDATE_FIELD_MAP
        assert 'order' not in vnet_rule.CREATE_PARAM_MAP

        # create(): the SDK takes `order` and must still be translating it.
        assert 'order' in inspect.signature(NetworkRuleManager.create).parameters
        create_source = inspect.getsource(NetworkRuleManager.create)
        assert 'body["orderid"] = order' in create_source, (
            "pyvergeos no longer maps create(order=) to orderid; vnet_rule's "
            "create path now writes a column that does not exist")

        # update(): the module must send the raw column name itself.
        module_source = open(vnet_rule.__file__).read()
        assert re.search(r"changes\['orderid'\]", module_source), (
            "vnet_rule's update path no longer sends orderid")

    def test_group_update_is_a_subset_of_create(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import group
        create = set(group.CREATE_PARAM_MAP)
        update = set(group.UPDATE_FIELD_MAP)
        assert update <= create
        assert create - update == set(group.IDENTITY_PARAMS)

    def test_group_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import group
        spec = _argument_spec_options(group)
        for param in set(group.CREATE_PARAM_MAP) | set(group.UPDATE_FIELD_MAP):
            assert param in spec, (
                "group maps %r to an API field but does not accept it as an "
                "option -- the mapping is dead code" % param)

    def test_group_identifier_is_not_compared_under_its_parameter_name(self):
        """The sixth recurrence, caught before merge.

        There is no `identifier` column on a group; the value is stored in
        `id`. The SDK aliases the keyword on WRITE, so setting it works and
        the value lands -- which is what made it invisible. Reading is where
        it broke: dict(row).get('identifier') is always None, so the group
        reported changed=True on every run, forever.
        """
        from ansible_collections.vergeio.vergeos.plugins.modules import group
        assert group.UPDATE_FIELD_MAP['identifier'] == 'id'
        assert 'identifier' not in group.COMPARISON_FIELDS
        assert 'id' in group.COMPARISON_FIELDS

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

    def test_nas_volume_update_is_a_subset_of_create(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            nas_volume,
        )
        create = set(nas_volume.CREATE_PARAM_MAP)
        update = set(nas_volume.UPDATE_FIELD_MAP)
        assert update <= create
        assert create - update == set(nas_volume.IDENTITY_PARAMS)

    def test_nas_volume_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            nas_volume,
        )
        spec = _argument_spec_options(nas_volume)
        for param in set(nas_volume.CREATE_PARAM_MAP) \
                | set(nas_volume.UPDATE_FIELD_MAP):
            assert param in spec, (
                'nas_volume maps %r to an API field but does not accept it '
                'as an option -- the mapping is dead code' % param)

    def test_nas_volume_columns_and_sdk_keywords_are_kept_apart(self):
        """The resize bug in one assertion.

        `maxsize` is the column and `size_gb` is the SDK keyword; `tier` is
        the option and `preferred_tier` is the column. Sending either of the
        column names to update() raises TypeError, and sending either of the
        keywords to the API silently discards it. One dict for both jobs is
        what the two maps exist to prevent.
        """
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            nas_volume,
        )
        assert nas_volume.UPDATE_FIELD_MAP['size_gb'] == 'maxsize'
        assert nas_volume.UPDATE_KWARG_MAP['size_gb'] == 'size_gb'
        assert nas_volume.UPDATE_FIELD_MAP['tier'] == 'preferred_tier'
        assert nas_volume.UPDATE_KWARG_MAP['tier'] == 'tier'
        assert set(nas_volume.UPDATE_FIELD_MAP) == set(
            nas_volume.UPDATE_KWARG_MAP), (
            'every mapped parameter needs both a column to read and a keyword '
            'to write')

    def test_nas_nfs_share_update_is_a_subset_of_create(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            nas_nfs_share,
        )
        create = set(nas_nfs_share.CREATE_PARAM_MAP)
        update = set(nas_nfs_share.UPDATE_FIELD_MAP)
        assert update <= create
        assert create - update == set(nas_nfs_share.IDENTITY_PARAMS)

    def test_nas_nfs_share_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            nas_nfs_share,
        )
        spec = _argument_spec_options(nas_nfs_share)
        for param in set(nas_nfs_share.CREATE_PARAM_MAP) \
                | set(nas_nfs_share.UPDATE_FIELD_MAP):
            assert param in spec, (
                'nas_nfs_share maps %r to an API field but does not accept it '
                'as an option -- the mapping is dead code' % param)

    def test_nas_nfs_share_async_is_read_from_the_column_not_the_option(self):
        """The ninth instance of #75's class, caught before merge.

        `async` is a Python keyword, so the option and the SDK keyword are
        both `async_mode` -- but the COLUMN is `async`. The module used one
        dict for both, so it asked a live share row for `async_mode`, got
        None, and compared bool(None) with the parameter. Setting
        async_mode=true meant changed=true and a PUT on every single run.
        """
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            nas_nfs_share,
        )
        assert nas_nfs_share.UPDATE_FIELD_MAP['async_mode'] == 'async'
        assert nas_nfs_share.UPDATE_KWARG_MAP['async_mode'] == 'async_mode'
        assert nas_nfs_share.UPDATE_FIELD_MAP['insecure_ports'] == 'insecure'
        assert nas_nfs_share.UPDATE_KWARG_MAP['insecure_ports'] == 'insecure'
        assert set(nas_nfs_share.UPDATE_FIELD_MAP) == set(
            nas_nfs_share.UPDATE_KWARG_MAP)

    def test_vm_export_update_is_a_subset_of_create(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            vm_export,
        )
        create = set(vm_export.CREATE_PARAM_MAP)
        update = set(vm_export.UPDATE_FIELD_MAP)
        assert update <= create
        assert create - update == set(vm_export.IDENTITY_PARAMS)

    def test_vm_export_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            vm_export,
        )
        spec = _argument_spec_options(vm_export)
        for param in set(vm_export.CREATE_PARAM_MAP) \
                | set(vm_export.UPDATE_FIELD_MAP):
            assert param in spec, (
                'vm_export maps %r to an API field but does not accept it as '
                'an option -- the mapping is dead code' % param)

    def test_tenant_update_is_a_subset_of_create(self):
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            tenants as shared,
        )
        create = set(shared.CREATE_PARAM_MAP)
        update = set(shared.UPDATE_FIELD_MAP)
        assert update <= create
        assert create - update == set(shared.IDENTITY_PARAMS)

    def test_tenant_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import tenant
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            tenants as shared,
        )
        spec = _argument_spec_options(tenant)
        for param in set(shared.CREATE_PARAM_MAP) | set(shared.UPDATE_FIELD_MAP):
            assert param in spec, (
                'tenant maps %r to an API field but does not accept it as an '
                'option -- the mapping is dead code' % param)

    def test_tenant_password_flag_is_renamed_on_the_way_out(self):
        """`require_password_change` is not a column; `change_password` is.
        The SDK's create() does the rename, so passing the COLUMN name would
        land in **kwargs and also work -- two spellings, one of which stops
        working the day the SDK stops translating. The map records which one
        this module relies on."""
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            tenants as shared,
        )
        assert shared.CREATE_PARAM_MAP['require_password_change'] == \
            'change_password'
        assert 'require_password_change' not in shared.UPDATE_FIELD_MAP

    def test_auth_source_update_is_a_subset_of_create(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            auth_source,
        )
        create = set(auth_source.CREATE_PARAM_MAP)
        update = set(auth_source.UPDATE_FIELD_MAP)
        assert update <= create
        assert create - update == set(auth_source.IDENTITY_PARAMS)

    def test_auth_source_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            auth_source,
        )
        spec = _argument_spec_options(auth_source)
        for param in set(auth_source.CREATE_PARAM_MAP) \
                | set(auth_source.UPDATE_FIELD_MAP):
            assert param in spec, (
                'auth_source maps %r to an API field but does not accept it '
                'as an option -- the mapping is dead code' % param)

    def test_auth_source_driver_is_create_only(self):
        """Swapping the provider under an existing source would repoint
        everyone who logs in through it, so the module refuses rather than
        trying. It is identity, not a setting."""
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            auth_source,
        )
        assert 'driver' in auth_source.IDENTITY_PARAMS
        assert 'driver' not in auth_source.UPDATE_FIELD_MAP
        assert 'driver' in auth_source.CREATE_PARAM_MAP

    def test_file_update_is_a_subset_of_create(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import file
        create = set(file.CREATE_PARAM_MAP)
        update = set(file.UPDATE_FIELD_MAP)
        assert update <= create
        assert create - update == set(file.IDENTITY_PARAMS)

    def test_file_mapped_parameters_are_all_real_options(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import file
        spec = _argument_spec_options(file)
        for param in set(file.CREATE_PARAM_MAP) | set(file.UPDATE_FIELD_MAP):
            assert param in spec, (
                'file maps %r to an API field but does not accept it as an '
                'option -- the mapping is dead code' % param)

    def test_file_tier_is_preferred_tier_for_the_third_time(self):
        """#8 on drives, again on nas_volume, and again here. The option is
        `tier`; the column is `preferred_tier`, and it stores a string."""
        from ansible_collections.vergeio.vergeos.plugins.modules import file
        assert file.UPDATE_FIELD_MAP['tier'] == 'preferred_tier'
        assert 'tier' not in file.COMPARISON_FIELDS

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
        'vm': {
            'machine_subtype': 'issue #87 -- no such column, and nothing holds '
                               'the value; machine_type carries the expanded '
                               'form already',
            'bios_type': 'issue #87 -- the column is the boolean uefi',
            'network': "issue #87 -- a VM's networks are its NICs",
        },
        'vnet_rule': {
            'rule_action': "the API column is action; 'action' cannot be an "
                           'Ansible option name without confusion',
            'order': 'the API column is orderid -- and the two paths reach it '
                     'differently, which is why it is not in the map at all',
        },
        'group': {
            'identifier': 'the API column is id -- there is no identifier '
                          'column, and the SDK aliases it only on WRITE, so '
                          'the read side compared against nothing and the '
                          'group never converged',
        },
        'nas_volume': {
            'size_gb': 'the column is maxsize, and it holds BYTES',
            'tier': 'issue #8 all over again on the volumes table -- the '
                    'column is preferred_tier, and it is stored as a string',
        },
        'nas_nfs_share': {
            'async_mode': 'the column is async; async_mode is the option and '
                          'the SDK keyword, because async is a Python keyword',
            'insecure_ports': 'the column is insecure; the option is renamed '
                              'to avoid the connection option of that name',
        },
        # vm_export needs no renames: quiesced, create_current and max_exports
        # are all real columns on a live volume_vm_exports row (26.1.8).
        'vm_export': {},
        'tenant': {
            'require_password_change': 'the column is change_password; the '
                                       "SDK's create() does the rename",
        },
        'file': {
            'tier': 'issue #8 for the third time, on a third table -- the '
                    'column is preferred_tier and it stores a string',
        },
        # auth_source needs no renames: all seven mapped parameters are real
        # columns on a live auth_sources row (26.1.8, 16 columns). Empty
        # rather than absent so the parametrised test below still covers it.
        'auth_source': {},
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

    def _ladder_fields(self, var_name='network_api_fields'):
        import os
        import yaml
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.abspath(os.path.join(here, '..', '..', '..', '..'))
        path = os.path.join(root, 'tests', 'live', 'verify-field-contract.yml')
        assert os.path.exists(path), "the live ladder is missing: %s" % path
        plays = yaml.safe_load(open(path))
        for play in plays:
            fields = (play.get('vars') or {}).get(var_name)
            if fields:
                return set(fields)
        pytest.fail("%s not found in the live ladder" % var_name)

    def test_vm_ladder_field_list_matches_the_module(self):
        """vm is the module this guard was built for -- #87 was three
        parameters that were not columns, and the ladder is what proves the
        remaining ones are."""
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        expected = set(vm.UPDATE_FIELD_MAP.values())
        actual = self._ladder_fields('vm_api_fields')
        assert actual == expected, (
            "verify-field-contract.yml's vm_api_fields has drifted from "
            "vm.UPDATE_FIELD_MAP. ladder-only=%s module-only=%s"
            % (sorted(actual - expected), sorted(expected - actual)))

    def test_ladder_field_list_matches_the_module(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import network
        expected = set(network.UPDATE_FIELD_MAP.values()) | {network.UPLINK_API_FIELD}
        actual = self._ladder_fields()
        assert actual == expected, (
            "tests/live/verify-field-contract.yml is out of step with "
            "network.UPDATE_FIELD_MAP. ladder-only=%s module-only=%s"
            % (sorted(actual - expected), sorted(expected - actual)))


class TestEveryDeclaredMapIsGuarded:
    """A module that declares a field map but is not in MAPPED_MODULES is
    guarded by nothing.

    This replaces the `vm` placeholder that existed while #87 was open. The
    placeholder did its job -- declaring vm's map is what surfaced #87, and it
    failed until vm joined the list -- but the rule it encoded is general, not
    about vm: the guard is only as good as its coverage, and coverage is opt-in.
    """

    def test_no_module_declares_a_map_without_joining_the_guard(self):
        import glob
        import importlib
        import os

        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.abspath(os.path.join(here, '..', '..', '..', '..'))
        pattern = os.path.join(root, 'plugins', 'modules', '*.py')

        unguarded = []
        for path in sorted(glob.glob(pattern)):
            name = os.path.basename(path)[:-3]
            if name.startswith('__'):
                continue
            mod = importlib.import_module(
                'ansible_collections.vergeio.vergeos.plugins.modules.%s' % name)
            if hasattr(mod, 'UPDATE_FIELD_MAP') and name not in MAPPED_MODULES:
                unguarded.append(name)

        assert not unguarded, (
            "these modules declare UPDATE_FIELD_MAP but are not in "
            "MAPPED_MODULES, so nothing audits them: %s" % unguarded)
