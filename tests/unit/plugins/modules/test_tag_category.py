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


def make_tag(key, name):
    tag = MagicMock()
    tag.key = key
    tag.name = name
    return tag


def assign(client, counts):
    """client.tags.members(key).list() returns ``counts[key]`` rows."""
    def members(tag_key):
        manager = MagicMock()
        manager.list.return_value = [object()] * counts[tag_key]
        return manager

    client.tags.members.side_effect = members


def run(capsys, client, task, failed=False):
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
    # ansible-core 2.19+ refuses to decode module args unless a serialization
    # profile is set ("No serialization profile was specified"). 2.15 has no
    # _ANSIBLE_PROFILE; leaving it unset there keeps the pre-2.19 path.
    saved_args = basic._ANSIBLE_ARGS
    saved_profile = getattr(basic, '_ANSIBLE_PROFILE', None)
    basic._ANSIBLE_ARGS = json.dumps(
        {'ANSIBLE_MODULE_ARGS': args}).encode('utf-8')
    if hasattr(basic, '_ANSIBLE_PROFILE'):
        basic._ANSIBLE_PROFILE = 'legacy'
    try:
        with patch.object(mod, 'get_vergeos_client', return_value=client):
            with pytest.raises(SystemExit) as caught:
                mod.main()
    finally:
        basic._ANSIBLE_ARGS = saved_args
        if hasattr(basic, '_ANSIBLE_PROFILE'):
            basic._ANSIBLE_PROFILE = saved_profile

    captured = capsys.readouterr()
    text = (captured.out + captured.err).strip()
    expected = 1 if failed else 0
    assert caught.value.code == expected, text
    result = json.loads(text.splitlines()[-1])
    if failed:
        assert result.get('failed') is True
    return result


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


def test_absent_refuses_a_category_that_still_has_tags(capsys):
    """Issue #153. Tags present and no force must not delete anything."""
    category = make_category()
    client = make_client([category])
    client.tags.list.return_value = [make_tag(5, 'DB'), make_tag(6, 'WEB')]

    result = run(capsys, client, {'state': 'absent'}, failed=True)

    client.tags.list.assert_called_once_with(category_key=CATEGORY_KEY)
    client.tags.members.assert_not_called()
    category.delete.assert_not_called()
    assert "DB" in result['msg'] and "WEB" in result['msg']
    assert 'force=true' in result['msg']
    assert 'deleted' not in result['msg']


def test_absent_check_mode_refuses_a_category_that_still_has_tags(capsys):
    """Check mode must not claim a cascading delete succeeded."""
    category = make_category()
    client = make_client([category])
    client.tags.list.return_value = [make_tag(5, 'DB')]

    result = run(capsys, client, {
        'state': 'absent',
        '_ansible_check_mode': True,
    }, failed=True)

    category.delete.assert_not_called()
    assert result['msg'].startswith('Refusing to delete')
    assert "'DB'" in result['msg']
    assert 'deleted' not in result['msg']


def test_force_deletes_and_reports_the_cascade(capsys):
    """force=true deletes, and names each tag with its assignment count."""
    category = make_category()
    client = make_client([category])
    client.tags.list.return_value = [make_tag(5, 'DB'), make_tag(6, 'WEB')]
    assign(client, {5: 2, 6: 0})

    result = run(capsys, client, {'state': 'absent', 'force': True})

    category.delete.assert_called_once()
    assert result['changed'] is True
    assert result['msg'].startswith("Tag category 'App' deleted")
    assert not result['msg'].startswith('Would')
    assert 'DB (2 assignments)' in result['msg']
    assert 'WEB (0 assignments)' in result['msg']
    assert result['deleted_tags'] == [
        {'name': 'DB', 'key': 5, 'assignments': 2},
        {'name': 'WEB', 'key': 6, 'assignments': 0},
    ]


def test_force_check_mode_reports_the_cascade_without_deleting(capsys):
    """A dry run of a forced delete shows the blast radius and writes nothing."""
    category = make_category()
    client = make_client([category])
    client.tags.list.return_value = [make_tag(5, 'DB'), make_tag(9, 'gold')]
    assign(client, {5: 1, 9: 3})

    result = run(capsys, client, {
        'state': 'absent',
        'force': True,
        '_ansible_check_mode': True,
    })

    category.delete.assert_not_called()
    assert result['changed'] is True
    assert result['msg'].startswith('Would delete tag category')
    assert 'deleted' not in result['msg']
    assert 'DB (1 assignment)' in result['msg']
    assert 'gold (3 assignments)' in result['msg']
    assert result['deleted_tags'] == [
        {'name': 'DB', 'key': 5, 'assignments': 1},
        {'name': 'gold', 'key': 9, 'assignments': 3},
    ]


def test_empty_category_deletes_without_force(capsys):
    """No tags means there is nothing to cascade. force is not required."""
    category = make_category()
    client = make_client([category])
    client.tags.list.return_value = []

    result = run(capsys, client, {'state': 'absent'})

    client.tags.list.assert_called_once_with(category_key=CATEGORY_KEY)
    client.tags.members.assert_not_called()
    category.delete.assert_called_once()
    assert result['changed'] is True
    assert result['msg'] == "Tag category 'App' deleted"
    assert 'deleted_tags' not in result


def test_empty_category_check_mode_says_would_delete(capsys):
    """Check mode on an empty category reports the delete and does not do it."""
    category = make_category()
    client = make_client([category])
    client.tags.list.return_value = []

    result = run(capsys, client, {
        'state': 'absent',
        '_ansible_check_mode': True,
    })

    category.delete.assert_not_called()
    assert result['changed'] is True
    assert result['msg'] == "Would delete tag category 'App'"
    assert 'deleted' not in result['msg']
