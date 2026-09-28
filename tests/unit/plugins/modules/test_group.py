#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the group module (issue #34).

The module arrived from the port with no tests and one defect, found by
declaring its field map and checking the names against a live row:

    identifier  ->  there is no such column; the value is stored in `id`

The SDK aliases the keyword on WRITE, so setting an identifier works and the
value really does land. The read side is where it broke: dict(row) carries
raw column names, so row.get('identifier') was always None, the comparison
always differed, and a group with an identifier set reported changed=True on
every run and never converged.

Sixth appearance of the compare-and-map class (#8, #10, #18, #59, #87), and
the first caught before merge rather than on a live system.
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
OIDC_ID = '0a1b2c3d-4e5f-6789-abcd-ef0123456789'


def make_row(data):
    row = MagicMock()
    row.keys.side_effect = lambda: list(data.keys())
    row.__getitem__.side_effect = data.__getitem__
    row.__iter__.side_effect = lambda: iter(data)
    return row


def make_group(key=GROUP_KEY, name='zz-operators', description='',
               email='', identifier='', enabled=True):
    """A group row as the API returns it: the identifier lives in `id`."""
    return make_row({'$key': key, 'name': name, 'description': description,
                     'email': email, 'id': identifier, 'enabled': enabled})


def make_client(groups=None, members=(), users=('alice', 'bob')):
    client = MagicMock()
    rows = [make_group()] if groups is None else list(groups)
    client.groups.list.return_value = rows
    client.users.list.return_value = [
        make_row({'$key': i + 10, 'name': n}) for i, n in enumerate(users)]

    handle = MagicMock()
    handle.list.return_value = list(members)
    client.groups.get.return_value.members = handle
    client.members = handle                      # test-side handle
    client.groups.create.return_value = make_group()
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
              'name': 'zz-operators', 'state': 'present', 'description': None,
              'email': None, 'identifier': None, 'enabled': None,
              'users': None, 'groups': None, 'exact_members': False}
    params.update(overrides)
    return params


def run(module, client):
    base = 'ansible_collections.vergeio.vergeos.plugins.modules.group'
    with patch('%s.get_vergeos_client' % base, return_value=client), \
         patch('%s.HAS_PYVERGEOS' % base, True), \
         patch('%s.AnsibleModule' % base, return_value=module):
        from ansible_collections.vergeio.vergeos.plugins.modules import group
        try:
            group.main()
        except SystemExit:
            pass


class TestTheIdentifierDefect:
    """Measured on 26.1.8: with the parameter name on both sides, three
    identical applies reported changed=True, changed=True, changed=True."""

    def test_a_group_with_an_identifier_converges(self):
        client = make_client(groups=[make_group(identifier=OIDC_ID)])
        module = make_module(base_params(identifier=OIDC_ID))

        run(module, client)

        client.groups.update.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_a_changed_identifier_is_still_detected(self):
        client = make_client(groups=[make_group(identifier=OIDC_ID)])
        module = make_module(base_params(identifier='a-different-id'))

        run(module, client)

        # The SDK's create/update take the PARAMETER name and alias it, so the
        # call still says identifier= even though the column is id.
        client.groups.update.assert_called_once_with(
            GROUP_KEY, identifier='a-different-id')
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_the_map_names_the_real_column(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import group
        assert group.UPDATE_FIELD_MAP['identifier'] == 'id'

    def test_the_projection_asks_for_every_column_it_compares(self):
        """#92's trap: a column compared but not fetched reads as None."""
        from ansible_collections.vergeio.vergeos.plugins.modules import group
        client = make_client()
        run(make_module(base_params()), client)

        # call_args is the LAST call, and key_name_map() lists groups again
        # afterwards. The lookup is the first one.
        first = client.groups.list.call_args_list[0]
        asked = set(first.kwargs['fields'])
        for column in group.COMPARISON_FIELDS:
            assert column in asked, (
                'group compares %r but does not fetch it' % column)


class TestCreateAndUpdate:
    def test_creates_a_missing_group(self):
        client = make_client(groups=[])
        module = make_module(base_params(description='ops'))

        run(module, client)

        client.groups.create.assert_called_once_with(
            name='zz-operators', description='ops')
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_an_unchanged_group_converges(self):
        client = make_client(groups=[make_group(description='ops')])
        module = make_module(base_params(description='ops'))

        run(module, client)

        client.groups.update.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_enabled_compares_as_a_boolean_not_a_string(self):
        """The API returns flags as 1/0; str(1) != str(True) reports drift
        that is not there."""
        client = make_client(groups=[make_group(enabled=1)])
        module = make_module(base_params(enabled=True))

        run(module, client)

        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_check_mode_creates_nothing(self):
        client = make_client(groups=[])
        module = make_module(base_params(), check_mode=True)

        run(module, client)

        client.groups.create.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_absent_deletes(self):
        client = make_client()
        module = make_module(base_params(state='absent'))

        run(module, client)

        client.groups.delete.assert_called_once_with(GROUP_KEY)
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_absent_on_a_missing_group_converges(self):
        client = make_client(groups=[])
        module = make_module(base_params(state='absent'))

        run(module, client)

        client.groups.delete.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False


class TestMembership:
    def test_adds_a_missing_user(self):
        client = make_client()
        module = make_module(base_params(users=['alice']))

        run(module, client)

        client.members.add_user.assert_called_once_with(10)
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_membership_is_additive_by_default(self):
        """A partial document must not be destructive: that is the normal
        case while adopting an estate."""
        client = make_client(members=[
            make_row({'member': '/v4/users/10', 'member_display': 'alice'}),
            make_row({'member': '/v4/users/11', 'member_display': 'bob'}),
        ])
        module = make_module(base_params(users=['alice']))

        run(module, client)

        client.members.remove_user.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is False

    def test_exact_members_removes_the_unlisted(self):
        client = make_client(members=[
            make_row({'member': '/v4/users/10', 'member_display': 'alice'}),
            make_row({'member': '/v4/users/11', 'member_display': 'bob'}),
        ])
        module = make_module(base_params(users=['alice'], exact_members=True))

        run(module, client)

        client.members.remove_user.assert_called_once_with(11)
        assert module.exit_json.call_args.kwargs['changed'] is True

    def test_a_nested_group_is_not_treated_as_a_user(self):
        client = make_client(members=[
            make_row({'member': '/v4/groups/10', 'member_display': 'nested'}),
        ])
        module = make_module(base_params(users=['alice']))

        run(module, client)

        # groups/10 and users/10 share a key and mean different things.
        client.members.add_user.assert_called_once_with(10)

    def test_an_unknown_member_is_refused_by_name(self):
        client = make_client()
        module = make_module(base_params(users=['nobody']))

        run(module, client)

        msg = module.fail_json.call_args.kwargs['msg']
        assert "no such user" in msg
        assert "nobody" in msg
        client.members.add_user.assert_not_called()

    def test_check_mode_changes_no_membership(self):
        client = make_client()
        module = make_module(base_params(users=['alice']), check_mode=True)

        run(module, client)

        client.members.add_user.assert_not_called()
        assert module.exit_json.call_args.kwargs['changed'] is True


class TestThePlatformDefect:
    def test_a_group_we_just_created_is_rebuilt(self):
        """A group created inside the post-delete window can never take
        members and never recovers, so retrying is pointless -- but a group
        this run created is ours to rebuild."""
        client = make_client(groups=[])
        calls = {'n': 0}

        def add_user(_key):
            calls['n'] += 1
            if calls['n'] == 1:
                raise APIError("error setting field 'members.group': "
                               "No such file or directory")
            return MagicMock()

        client.members.add_user.side_effect = add_user
        module = make_module(base_params(users=['alice']))

        with patch('ansible_collections.vergeio.vergeos.plugins.modules.'
                   'group.time.sleep'):
            run(module, client)

        client.groups.delete.assert_called_once()
        assert client.groups.create.call_count == 2
        assert 'recreated' in module.exit_json.call_args.kwargs['changed_fields']

    def test_a_pre_existing_group_is_not_deleted_behind_your_back(self):
        client = make_client(groups=[make_group()])
        client.members.add_user.side_effect = APIError(
            "error setting field 'members.group': No such file or directory")
        module = make_module(base_params(users=['alice']))

        run(module, client)

        client.groups.delete.assert_not_called()
        assert 'VergeOS defect' in module.fail_json.call_args.kwargs['msg']


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
        client.groups.list.side_effect = NotFoundError('gone')
        module = make_module(base_params())

        run(module, client)

        assert 'Resource not found' in module.fail_json.call_args.kwargs['msg']
