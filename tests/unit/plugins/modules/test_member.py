#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the member module (issue #92).

The module shipped broken in every release up to v2.1.0 and had no test file
at all. Three defects, any one of them fatal:

  1. client.users.get(username=...)  -- a keyword no pyvergeos version has
  2. row['member'] == username       -- '/v4/users/4' never equals 'alice'
  3. members.create(member=username) -- the API takes a reference, not a name

Only (2) and (3) are testable here. (1) is not: a MagicMock accepts any
keyword argument, which is exactly why the bug survived. It is caught in
tests/unit/test_sdk_call_signatures.py, against the real SDK.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock, patch

from pyvergeos.exceptions import (
    APIError,
    AuthenticationError,
    NotFoundError,
    ValidationError,
    VergeConnectionError,
)


GROUP_KEY = 2
USER_KEY = 4


def make_row(data):
    row = MagicMock()
    row.keys.side_effect = lambda: list(data.keys())
    row.__getitem__.side_effect = data.__getitem__
    row.__iter__.side_effect = lambda: iter(data)
    return row


def make_membership(ref='/v4/users/4', key=4, display='alice'):
    """A real membership row, as measured on VergeOS 26.1.8."""
    return make_row({'$key': key, 'parent_group': GROUP_KEY, 'member': ref,
                     'member_display': display, 'creator': 'admin'})


def make_client(groups=('developers',), users=('alice',), memberships=()):
    client = MagicMock()
    client.groups.list.return_value = [
        make_row({'$key': GROUP_KEY, 'name': name}) for name in groups]
    client.users.list.return_value = [
        make_row({'$key': USER_KEY, 'name': name}) for name in users]
    members = MagicMock()
    members.list.return_value = list(memberships)
    members.add_user.return_value = make_membership()
    client.groups.members.return_value = members
    client.members = members          # test-side handle
    return client


def make_module(params, check_mode=False):
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def base_params(**overrides):
    params = {'host': 'vergeos.example.com', 'username': 'admin',
              'password': 'secret', 'insecure': False, 'api_key': None,
              'group': 'developers', 'name': 'alice', 'state': 'present'}
    params.update(overrides)
    return params


def run(module, client):
    base = 'ansible_collections.vergeio.vergeos.plugins.modules.member'
    with patch('%s.get_vergeos_client' % base, return_value=client), \
         patch('%s.HAS_PYVERGEOS' % base, True), \
         patch('%s.AnsibleModule' % base, return_value=module):
        from ansible_collections.vergeio.vergeos.plugins.modules import member
        try:
            member.main()
        except SystemExit:
            pass


class TestPresent:
    def test_adds_a_missing_member_by_key(self):
        client = make_client()
        module = make_module(base_params())

        run(module, client)

        # add_user(key), not create(member=<username>) -- defect 3.
        client.members.add_user.assert_called_once_with(USER_KEY)
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_an_existing_member_converges(self):
        """Defect 2. The row says '/v4/users/4'; the old code compared that
        against 'alice' and re-added forever."""
        client = make_client(memberships=[make_membership()])
        module = make_module(base_params())

        run(module, client)

        client.members.add_user.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    @pytest.mark.parametrize('ref', ['/v4/users/4', 'users/4', '/users/4'])
    def test_every_reference_shape_the_platform_sends_is_matched(self, ref):
        """The members table returns '/v4/users/2'; the nested projection on a
        group returns 'users/2'. Matching one and not the other is the same
        bug with a smaller blast radius."""
        client = make_client(memberships=[make_membership(ref=ref)])
        module = make_module(base_params())

        run(module, client)

        client.members.add_user.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_a_membership_of_a_different_user_is_not_mistaken_for_this_one(self):
        client = make_client(memberships=[make_membership(ref='/v4/users/9')])
        module = make_module(base_params())

        run(module, client)

        client.members.add_user.assert_called_once_with(USER_KEY)

    def test_a_nested_group_is_not_mistaken_for_a_user(self):
        """A group can contain groups. 'groups/4' and 'users/4' share a key
        and mean different things."""
        client = make_client(memberships=[make_membership(ref='/v4/groups/4')])
        module = make_module(base_params())

        run(module, client)

        client.members.add_user.assert_called_once_with(USER_KEY)

    def test_check_mode_adds_nothing(self):
        client = make_client()
        module = make_module(base_params(), check_mode=True)

        run(module, client)

        client.members.add_user.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is True


class TestAbsent:
    def test_removes_an_existing_member_by_key(self):
        client = make_client(memberships=[make_membership()])
        module = make_module(base_params(state='absent'))

        run(module, client)

        client.members.remove_user.assert_called_once_with(USER_KEY)
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_absent_on_a_non_member_converges(self):
        client = make_client()
        module = make_module(base_params(state='absent'))

        run(module, client)

        client.members.remove_user.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_check_mode_removes_nothing(self):
        client = make_client(memberships=[make_membership()])
        module = make_module(base_params(state='absent'), check_mode=True)

        run(module, client)

        client.members.remove_user.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is True


class TestLookupFailures:
    def test_unknown_group_is_named(self):
        client = make_client(groups=[])
        module = make_module(base_params())

        run(module, client)

        assert "Group 'developers' not found" \
            in module.fail_json.call_args.kwargs['msg']

    def test_unknown_user_is_named(self):
        client = make_client(users=[])
        module = make_module(base_params())

        run(module, client)

        assert "User 'alice' not found" \
            in module.fail_json.call_args.kwargs['msg']

    def test_duplicate_group_names_are_refused(self):
        client = MagicMock()
        client.groups.list.return_value = [
            make_row({'$key': 2, 'name': 'developers'}),
            make_row({'$key': 5, 'name': 'developers'}),
        ]
        module = make_module(base_params())

        run(module, client)

        assert 'refusing to guess' in module.fail_json.call_args.kwargs['msg']


class TestThePlatformDefect:
    def test_the_member_insert_defect_gets_an_explanation(self):
        """VergeOS answers "No such file or directory" about a group that
        plainly exists. Passing that through sends the operator nowhere."""
        client = make_client()
        client.members.add_user.side_effect = APIError(
            "404 Error creating member in system table: error setting field "
            "'members.group': No such file or directory")
        module = make_module(base_params())

        run(module, client)

        msg = module.fail_json.call_args.kwargs['msg']
        assert 'VergeOS defect' in msg
        assert 'does not recover on its own' in msg
        assert 'developers' in msg

    def test_an_unrelated_404_is_not_swallowed(self):
        client = make_client()
        client.members.add_user.side_effect = APIError('404 no such user')
        module = make_module(base_params())

        run(module, client)

        assert 'VergeOS defect' not in module.fail_json.call_args.kwargs['msg']


class TestSdkErrorsAreHandled:
    @pytest.mark.parametrize('exc', [
        APIError, AuthenticationError, ValidationError, VergeConnectionError,
    ])
    def test_sdk_errors_do_not_fall_through_to_unexpected(self, exc):
        client = make_client()
        client.groups.list.side_effect = exc('server said no')
        module = make_module(base_params())

        run(module, client)

        module.fail_json.assert_called_once()
        assert 'Unexpected error' not in module.fail_json.call_args.kwargs['msg']

    def test_not_found_is_reported_as_not_found(self):
        client = make_client()
        client.groups.members.side_effect = NotFoundError('gone')
        module = make_module(base_params())

        run(module, client)

        assert 'Resource not found' in module.fail_json.call_args.kwargs['msg']
