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

import pytest
from pyvergeos.exceptions import (
    APIError,
    AuthenticationError,
    NotFoundError,
    ValidationError,
    VergeConnectionError,
)


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


def test_absent_check_mode_says_would_delete():
    """Check mode must not say the tag was deleted (#153, same as vm #127)."""
    tag = make_tag(7, 'qa-tag', category='App')
    client = make_client([tag])
    module = make_module(base_params(), check_mode=True)

    run_main(module, client)

    tag.delete.assert_not_called()
    result = module.exit_json.call_args[1]
    assert result['changed'] is True
    assert result['msg'] == "Would delete tag 'qa-tag'"
    assert 'deleted' not in result['msg']


def make_vm(name='testdbvm', key=42, assignments=None, get_tags_error=None):
    vm = make_row({'$key': key, 'name': name})
    vm.key = key
    vm.name = name
    if get_tags_error is not None:
        vm.get_tags.side_effect = get_tags_error
    else:
        vm.get_tags.return_value = list(assignments or [])
    return vm


def run_vm_tag(state, vm, tag=None, check_mode=False):
    """Run a VM assign/remove. tag=None means the tag lookup finds nothing."""
    client = make_client([] if tag is None else [tag])
    client.vms.list.return_value = [vm]
    module = make_module(base_params(
        category='App', state=state, vm_name=vm.name), check_mode=check_mode)
    run_main(module, client)
    return module


@pytest.mark.parametrize('state', ['present', 'absent'])
@pytest.mark.parametrize('exc_type,message,prefix', [
    (APIError, 'Internal Server Error', 'API error:'),
    (AuthenticationError, 'session expired', 'Authentication failed:'),
    (ValidationError, 'bad filter', 'Validation error:'),
    (VergeConnectionError, 'connection reset', 'Connection failed:'),
    (NotFoundError, 'tag_members missing', 'Resource not found:'),
])
def test_assignment_lookup_failure_is_reported(state, exc_type, message, prefix):
    """get_tags() errors fail the task. They are not "the VM has no tag" (#208).

    NotFoundError from the assignment read is an API failure of that read.
    An untagged VM is an empty list, covered separately.
    """
    tag = make_tag(7, 'qa-tag')
    vm = make_vm(get_tags_error=exc_type(message))
    module = run_vm_tag(state, vm, tag=tag)

    module.exit_json.assert_not_called()
    module.fail_json.assert_called_once()
    msg = module.fail_json.call_args[1]['msg']
    assert msg.startswith(prefix), msg
    assert 'Unexpected error' not in msg
    assert 'not on' not in msg
    vm.tag.assert_not_called()
    vm.untag.assert_not_called()


@pytest.mark.parametrize('state', ['present', 'absent'])
def test_non_sdk_lookup_failure_still_fails_the_task(state):
    """Anything get_tags() raises has to fail the task, not report unchanged."""
    tag = make_tag(7, 'qa-tag')
    vm = make_vm(get_tags_error=RuntimeError('decode failed'))
    module = run_vm_tag(state, vm, tag=tag)

    module.exit_json.assert_not_called()
    msg = module.fail_json.call_args[1]['msg']
    assert msg.startswith('Unexpected error:')
    assert 'decode failed' in msg
    vm.tag.assert_not_called()
    vm.untag.assert_not_called()


def test_no_assignments_means_the_tag_is_not_on_the_vm():
    """An empty assignment list is the not-on-the-VM case, not an error.

    Another tag on the VM does not count, and neither does an empty list.
    present applies this tag; absent leaves the VM alone.
    """
    tag = make_tag(7, 'qa-tag')
    present_vm = make_vm(assignments=[{'tag_key': 99, 'tag_name': 'other'}])
    absent_vm = make_vm(assignments=[])

    present = run_vm_tag('present', present_vm, tag=tag)
    present.fail_json.assert_not_called()
    present_result = present.exit_json.call_args[1]
    assert present_result['changed'] is True
    assert 'applied to' in present_result['msg']
    present_vm.tag.assert_called_once_with(7)
    present_vm.untag.assert_not_called()

    absent = run_vm_tag('absent', absent_vm, tag=tag)
    absent.fail_json.assert_not_called()
    absent_result = absent.exit_json.call_args[1]
    assert absent_result['changed'] is False
    assert 'not on' in absent_result['msg']
    assert absent_result['vm_name'] == 'testdbvm'
    absent_vm.tag.assert_not_called()
    absent_vm.untag.assert_not_called()


def test_tag_already_on_the_vm_is_left_alone():
    tag = make_tag(7, 'qa-tag')
    vm = make_vm(assignments=[{'tag_key': 7, 'tag_name': 'qa-tag'}])
    module = run_vm_tag('present', vm, tag=tag)

    module.fail_json.assert_not_called()
    result = module.exit_json.call_args[1]
    assert result['changed'] is False
    assert 'already on' in result['msg']
    vm.tag.assert_not_called()


def test_tag_on_the_vm_is_removed():
    tag = make_tag(7, 'qa-tag')
    vm = make_vm(assignments=[{'tag_key': 7, 'tag_name': 'qa-tag'}])
    module = run_vm_tag('absent', vm, tag=tag)

    module.fail_json.assert_not_called()
    result = module.exit_json.call_args[1]
    assert result['changed'] is True
    assert 'removed from' in result['msg']
    vm.untag.assert_called_once_with(7)
    vm.tag.assert_not_called()


def test_absent_vm_task_with_a_missing_tag_stays_unchanged():
    """The tag lookup's NotFoundError is still "tag does not exist"."""
    vm = make_vm()
    module = run_vm_tag('absent', vm, tag=None)

    module.fail_json.assert_not_called()
    result = module.exit_json.call_args[1]
    assert result['changed'] is False
    assert 'does not exist' in result['msg']
    vm.get_tags.assert_not_called()
    vm.untag.assert_not_called()


def test_missing_vm_is_still_reported_as_vm_not_found():
    """The VM lookup's NotFoundError stays the module's own message."""
    tag = make_tag(7, 'qa-tag')
    client = make_client([tag])
    client.vms.list.return_value = []
    module = make_module(base_params(
        category='App', state='present', vm_name='testdbvm'))

    run_main(module, client)

    module.exit_json.assert_not_called()
    msg = module.fail_json.call_args[1]['msg']
    assert msg == "VM 'testdbvm' not found"
    assert 'Unexpected error' not in msg
    assert 'Resource not found' not in msg
