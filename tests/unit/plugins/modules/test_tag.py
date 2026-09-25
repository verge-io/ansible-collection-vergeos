#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""tag state=absent without a category (issue #122).

Without category the module used to report that the tag does not exist
without listing tags, and deleted nothing. Absent has to look the name up
across categories: one match is deleted, no match is unchanged, and more
than one match is refused.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from unittest.mock import MagicMock, patch


def make_row(data):
    obj = MagicMock()
    stored = dict(data)
    obj.keys.side_effect = lambda: list(stored.keys())
    obj.__getitem__.side_effect = stored.__getitem__
    obj.__iter__.side_effect = lambda: iter(stored)
    for key, value in stored.items():
        setattr(obj, key, value)
    return obj


def make_tag(key, name, category='App'):
    tag = make_row({
        '$key': key,
        'name': name,
        'description': '',
        'category_key': 1,
        'category_name': category,
    })
    tag.key = key
    tag.name = name
    tag.description = ''
    tag.category_key = 1
    tag.category_name = category
    return tag


def make_module(params, check_mode=False):
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def base_params(**overrides):
    params = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'name': 'qa-tag',
        'category': None,
        'state': 'absent',
        'description': None,
        'vm_name': None,
        'vm_id': None,
    }
    params.update(overrides)
    return params


def make_client(tags):
    client = MagicMock()
    client.tags.list.return_value = list(tags)
    client.tag_categories.list.return_value = [
        make_row({'$key': 1, 'name': 'App'}),
    ]
    return client


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.tag.'
               'get_vergeos_client', return_value=mock_client), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.tag.'
               'AnsibleModule', return_value=mock_module):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            tag as tag_module,
        )
        try:
            tag_module.main()
        except SystemExit:
            pass


def test_absent_without_category_deletes_a_unique_tag():
    """The name is listed, and the one match is deleted."""
    tag = make_tag(7, 'qa-tag', category='App')
    other = make_tag(8, 'other', category='Env')
    client = make_client([other, tag])
    module = make_module(base_params())

    run_main(module, client)

    client.tags.list.assert_called()
    assert 'category_name' not in (client.tags.list.call_args.kwargs or {})
    tag.delete.assert_called_once()
    other.delete.assert_not_called()
    result = module.exit_json.call_args[1]
    assert result['changed'] is True
    assert 'deleted' in result['msg']
    module.fail_json.assert_not_called()


def test_absent_without_category_reports_missing_only_after_lookup():
    client = make_client([])
    module = make_module(base_params())

    run_main(module, client)

    client.tags.list.assert_called()
    assert 'category_name' not in (client.tags.list.call_args.kwargs or {})
    result = module.exit_json.call_args[1]
    assert result['changed'] is False
    assert "does not exist" in result['msg']
    assert 'qa-tag' in result['msg']
    module.fail_json.assert_not_called()


def test_absent_without_category_refuses_duplicate_names():
    """Two categories can hold the same tag name. Do not guess."""
    first = make_tag(11, 'qa-tag', category='App')
    second = make_tag(22, 'qa-tag', category='Env')
    client = make_client([first, second])
    module = make_module(base_params())

    run_main(module, client)

    client.tags.list.assert_called()
    first.delete.assert_not_called()
    second.delete.assert_not_called()
    module.exit_json.assert_not_called()
    module.fail_json.assert_called_once()
    msg = module.fail_json.call_args[1]['msg']
    assert 'refusing to guess' in msg
    assert '11' in msg and '22' in msg


def test_absent_with_category_still_limits_the_lookup():
    """Naming a category must keep matching inside that category."""
    tag = make_tag(7, 'qa-tag', category='App')
    client = make_client([tag])
    module = make_module(base_params(category='App'))

    run_main(module, client)

    assert client.tags.list.call_args.kwargs.get('category_name') == 'App'
    tag.delete.assert_called_once()
    assert module.exit_json.call_args[1]['changed'] is True
