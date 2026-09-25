#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for tag_category (issue #121).

taggable_* and single_tag_selection used to default to false in the
argument spec. Ansible then passed False for every flag a task left out,
so a description-only update turned tagging off and a later attempt to
tag a new VM failed with "API error: Operation not permitted".

These tests go through AnsibleModule, not a hand-built params dict.
A params dict with the flags set to None passes on the unfixed module
too, because the bug is the default the spec injects.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import json

import pytest
from unittest.mock import MagicMock, patch

from ansible.module_utils import basic


CATEGORY_KEY = 7

# The twelve taggable_* options plus single_tag_selection.
FLAGS = (
    'single_tag_selection',
    'taggable_vms',
    'taggable_networks',
    'taggable_volumes',
    'taggable_network_rules',
    'taggable_vmware_containers',
    'taggable_users',
    'taggable_tenant_nodes',
    'taggable_sites',
    'taggable_nodes',
    'taggable_groups',
    'taggable_clusters',
    'taggable_tenants',
)

# A category that can tag VMs, networks and users, with single selection.
# A description-only edit must leave every one of these alone.
LIVE_FLAGS = {
    'single_tag_selection': True,
    'taggable_vms': True,
    'taggable_networks': True,
    'taggable_volumes': False,
    'taggable_network_rules': False,
    'taggable_vmware_containers': False,
    'taggable_users': True,
    'taggable_tenant_nodes': False,
    'taggable_sites': False,
    'taggable_nodes': False,
    'taggable_groups': False,
    'taggable_clusters': False,
    'taggable_tenants': False,
}


def make_category(description='old text', **flag_overrides):
    """A category row the module can both dict() and read attributes from."""
    flags = dict(LIVE_FLAGS)
    flags.update(flag_overrides)
    data = {
        '$key': CATEGORY_KEY,
        'key': CATEGORY_KEY,
        'name': 'App',
        'description': description,
        'is_single_tag_selection': flags['single_tag_selection'],
    }
    for name in FLAGS:
        if name == 'single_tag_selection':
            continue
        data[name] = flags[name]

    row = MagicMock()
    row.keys.side_effect = lambda: list(data.keys())
    row.__getitem__.side_effect = data.__getitem__
    row.__iter__.side_effect = lambda: iter(data)
    for key, value in data.items():
        setattr(row, key, value)
    return row


def make_client(categories):
    client = MagicMock()
    client.tag_categories.list.return_value = list(categories)
    return client


def run(capsys, client, task):
    """Run tag_category.main() through a real AnsibleModule."""
    import importlib
    mod = importlib.import_module(
        'ansible_collections.vergeio.vergeos.plugins.modules.tag_category')

    args = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'name': 'App',
        'state': 'present',
    }
    args.update(task)
    saved = basic._ANSIBLE_ARGS
    basic._ANSIBLE_ARGS = json.dumps(
        {'ANSIBLE_MODULE_ARGS': args}).encode('utf-8')
    try:
        with patch.object(mod, 'get_vergeos_client', return_value=client):
            with pytest.raises(SystemExit) as caught:
                mod.main()
    finally:
        basic._ANSIBLE_ARGS = saved

    captured = capsys.readouterr()
    text = (captured.out + captured.err).strip()
    assert caught.value.code == 0, text
    return json.loads(text.splitlines()[-1])


def test_description_only_update_leaves_every_flag(capsys):
    """Issue #121. A description edit must not turn tagging off."""
    client = make_client([make_category()])
    client.tag_categories.update.return_value = make_category(
        description='new text')

    result = run(capsys, client, {'description': 'new text'})

    client.tag_categories.update.assert_called_once_with(
        CATEGORY_KEY, description='new text')
    assert result['changed'] is True
    returned = result['category']
    assert returned['description'] == 'new text'
    for name, value in LIVE_FLAGS.items():
        assert returned[name] is value, (
            '%s changed from %r to %r on a description-only update'
            % (name, value, returned[name]))


def test_same_description_with_flags_omitted_is_unchanged(capsys):
    """Re-applying a category without restating its flags changes nothing."""
    client = make_client([make_category(description='old text')])

    result = run(capsys, client, {'description': 'old text'})

    client.tag_categories.update.assert_not_called()
    assert result['changed'] is False
    for name, value in LIVE_FLAGS.items():
        assert result['category'][name] is value


def test_create_defaults_omitted_flags_to_false(capsys):
    """Create still defaults each omitted flag to false."""
    client = make_client([])
    client.tag_categories.create.return_value = make_category(
        description=None, **{name: False for name in FLAGS})

    result = run(capsys, client, {})

    client.tag_categories.create.assert_called_once_with(
        name='App',
        description=None,
        **{name: False for name in FLAGS})
    assert result['changed'] is True


def test_create_keeps_an_explicit_flag(capsys):
    """An explicit true on create is not replaced by the false default."""
    flags = {name: False for name in FLAGS}
    flags['taggable_vms'] = True
    flags['single_tag_selection'] = True
    client = make_client([])
    client.tag_categories.create.return_value = make_category(**flags)

    run(capsys, client, {
        'taggable_vms': True,
        'single_tag_selection': True,
    })

    sent = client.tag_categories.create.call_args.kwargs
    assert sent['taggable_vms'] is True
    assert sent['single_tag_selection'] is True
    for name in FLAGS:
        if name in ('taggable_vms', 'single_tag_selection'):
            continue
        assert sent[name] is False


def test_explicit_false_clears_only_that_flag(capsys):
    """Removing the default must not remove the ability to turn a flag off."""
    client = make_client([make_category()])
    updated_flags = dict(LIVE_FLAGS)
    updated_flags['taggable_vms'] = False
    client.tag_categories.update.return_value = make_category(
        **updated_flags)

    result = run(capsys, client, {'taggable_vms': False})

    client.tag_categories.update.assert_called_once_with(
        CATEGORY_KEY, taggable_vms=False)
    assert result['changed'] is True
    assert result['category']['taggable_vms'] is False
    assert result['category']['taggable_networks'] is True
    assert result['category']['single_tag_selection'] is True
