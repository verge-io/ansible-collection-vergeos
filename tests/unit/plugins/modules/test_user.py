#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the user module (issue #120, on the membership path from #92).

``role`` and ``groups`` were accepted and discarded. ``role: admin`` created
an ordinary user. There is no role column; access is a permission. ``groups``
is membership of a group, written with ``add_user(key)`` and matched by
reference, which is the path ``member`` uses.

The client double is deliberately not a MagicMock. A MagicMock accepts
``users.get(username=...)`` and ``members.create(member=<name>)``, which is
why #92 shipped: the test that should have caught the wrong call succeeded.
``UserAPI.get`` and ``MemberAPI.add_user`` have the real signatures, and
``MemberAPI.create`` refuses the call.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os

import pytest
from unittest.mock import MagicMock, patch

from pyvergeos.exceptions import (
    APIError,
    AuthenticationError,
    NotFoundError,
    ValidationError,
    VergeConnectionError,
)


USER_KEY = 4
GROUP_KEY = 2
OTHER_GROUP_KEY = 8


def make_row(data):
    row = MagicMock()
    stored = dict(data)
    row.keys.side_effect = lambda: list(stored.keys())
    row.__getitem__.side_effect = stored.__getitem__
    row.__iter__.side_effect = lambda: iter(stored)

    def save(**kwargs):
        stored.update(kwargs)
        return make_row(stored)

    row.save.side_effect = save
    return row


class UserAPI:
    """The slice of UserManager this module is allowed to call.

    ``get`` takes ``name``, never ``username``. ``create`` takes the real
    keywords and nothing else -- ``role`` and ``groups`` are not among them.
    """

    ALLOWED = {
        'displayname', 'email', 'user_type', 'enabled', 'change_password',
        'physical_access', 'two_factor_enabled', 'two_factor_type',
        'two_factor_setup_required', 'ssh_keys',
    }

    def __init__(self, rows):
        self.rows = list(rows)
        self.created = []

    def list(self, **kwargs):
        return list(self.rows)

    def get(self, key=None, *, name=None, fields=None):
        raise AssertionError(
            'users.get() is not the lookup this module uses; resolve_one '
            'lists and matches in Python')

    def create(self, name, password, **kwargs):
        unknown = sorted(set(kwargs) - self.ALLOWED)
        if unknown:
            raise TypeError(
                'UserManager.create() got an unexpected keyword argument %r'
                % unknown[0])
        self.created.append((name, password, dict(kwargs)))
        return make_row({
            '$key': USER_KEY,
            'name': name,
            'email': kwargs.get('email') or '',
            'displayname': kwargs.get('displayname') or '',
            'enabled': kwargs.get('enabled', True),
        })


class MemberAPI:
    """GroupMemberManager, minus the call the broken module used.

    ``add_user(user_key)`` is the whole signature. ``create(member=<name>)``
    and ``add_user(member=...)`` both raise TypeError here; a MagicMock
    would have recorded either and the test would still pass.
    """

    def __init__(self, rows):
        self.rows = [dict(row) for row in rows]
        self.added = []

    def list(self):
        return [dict(row) for row in self.rows]

    def add_user(self, user_key):
        self.added.append(user_key)
        return {
            '$key': 99,
            'parent_group': GROUP_KEY,
            'member': '/v4/users/%s' % user_key,
        }

    def create(self, *args, **kwargs):
        raise TypeError(
            'GroupMemberManager.create() is the wrong call; use add_user(key)')


class GroupAPI:
    def __init__(self, rows, memberships):
        self.rows = list(rows)
        self.memberships = dict(memberships)
        self.member_calls = []

    def list(self, **kwargs):
        return list(self.rows)

    def members(self, group_key):
        self.member_calls.append(group_key)
        if group_key not in self.memberships:
            self.memberships[group_key] = MemberAPI([])
        return self.memberships[group_key]


def make_client(users=None, groups=None, memberships=None):
    """One user ``alice`` and one group ``ops``, neither linked, by default."""
    if users is None:
        users = [make_row({'$key': USER_KEY, 'name': 'alice', 'email': '',
                           'displayname': '', 'enabled': True})]
    if groups is None:
        groups = [{'$key': GROUP_KEY, 'name': 'ops'}]
    if memberships is None:
        memberships = {GROUP_KEY: MemberAPI([])}
    client = MagicMock()
    client.users = UserAPI(users)
    client.groups = GroupAPI(groups, memberships)
    return client


def make_module(params, check_mode=False):
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def base_params(**overrides):
    params = {
        'host': 'vergeos.example.com', 'username': 'admin',
        'password': 'secret', 'insecure': False, 'api_key': None,
        'name': 'alice', 'state': 'present', 'user_password': None,
        'update_password': 'on_create', 'email': None, 'full_name': None,
        'enabled': None, 'role': None, 'groups': None,
    }
    params.update(overrides)
    return params


def run(module, client):
    base = 'ansible_collections.vergeio.vergeos.plugins.modules.user'
    with patch('%s.get_vergeos_client' % base, return_value=client), \
         patch('%s.HAS_PYVERGEOS' % base, True), \
         patch('%s.AnsibleModule' % base, return_value=module):
        from ansible_collections.vergeio.vergeos.plugins.modules import user
        try:
            user.main()
        except SystemExit:
            pass


def result_of(module):
    assert module.exit_json.called, module.fail_json.call_args
    return module.exit_json.call_args.kwargs


def failure_of(module):
    assert module.fail_json.called, module.exit_json.call_args
    return module.fail_json.call_args.kwargs['msg']


class TestRoleIsRejected:
    @pytest.mark.parametrize('role', ['admin', 'user', 'readonly'])
    def test_every_accepted_role_fails_and_points_at_permission(self, role):
        client = make_client(users=[])
        module = make_module(base_params(role=role, user_password='secret'))

        run(module, client)

        msg = failure_of(module)
        assert 'permission' in msg
        assert 'vergeio.vergeos.group' in msg
        assert client.users.created == []
        assert client.groups.member_calls == []

    def test_role_is_rejected_before_a_delete(self):
        """A task that still says role=admin and state=absent must not
        succeed. Success would teach the operator that role is real."""
        client = make_client()
        module = make_module(base_params(role='admin', state='absent'))

        run(module, client)

        assert 'permission' in failure_of(module)
        client.users.rows[0].delete.assert_not_called()


class TestGroupsUseTheMemberPath:
    def test_a_missing_membership_is_added_by_key(self):
        client = make_client()
        module = make_module(base_params(groups=['ops']))

        run(module, client)

        # add_user(key), not create(member=<username>) -- the #92 defect.
        assert client.groups.memberships[GROUP_KEY].added == [USER_KEY]
        assert all(isinstance(key, int) for key in
                   client.groups.memberships[GROUP_KEY].added)
        exited = result_of(module)
        assert exited['changed'] is True
        assert exited['groups'] == ['ops']
        assert client.users.created == []

    def test_creating_a_user_does_not_send_role_or_groups_to_the_api(self):
        client = make_client(users=[])
        module = make_module(base_params(
            user_password='secret', email='a@example.com',
            full_name='Alice', groups=['ops']))

        run(module, client)

        assert len(client.users.created) == 1
        name, password, kwargs = client.users.created[0]
        assert name == 'alice'
        assert password == 'secret'
        assert kwargs['email'] == 'a@example.com'
        assert kwargs['displayname'] == 'Alice'
        assert kwargs['enabled'] is True
        assert 'role' not in kwargs
        assert 'groups' not in kwargs
        assert client.groups.memberships[GROUP_KEY].added == [USER_KEY]

    def test_an_unknown_group_is_named_and_the_user_is_not_created(self):
        client = make_client(users=[])
        module = make_module(base_params(
            user_password='secret', groups=['missing']))

        run(module, client)

        assert "Group 'missing' not found" in failure_of(module)
        assert client.users.created == []
        assert client.groups.member_calls == []

    def test_an_unknown_group_does_not_update_an_existing_user(self):
        client = make_client()
        module = make_module(base_params(email='new@example.com',
                                         groups=['missing']))

        run(module, client)

        assert "Group 'missing' not found" in failure_of(module)
        client.users.rows[0].save.assert_not_called()

    def test_duplicate_group_names_are_refused(self):
        client = make_client(groups=[
            {'$key': 2, 'name': 'ops'},
            {'$key': 5, 'name': 'ops'},
        ])
        module = make_module(base_params(groups=['ops']))

        run(module, client)

        assert 'refusing to guess' in failure_of(module)
        assert client.groups.member_calls == []

    @pytest.mark.parametrize('ref', ['/v4/users/4', 'users/4', '/users/4'])
    def test_an_existing_membership_converges(self, ref):
        """The row says '/v4/users/4' or 'users/4'. Comparing that to
        'alice' re-adds forever. Both forms are real."""
        client = make_client(memberships={
            GROUP_KEY: MemberAPI([{'member': ref, 'member_display': 'alice'}]),
        })
        module = make_module(base_params(groups=['ops']))

        run(module, client)

        assert client.groups.memberships[GROUP_KEY].added == []
        exited = result_of(module)
        assert exited['changed'] is False
        assert exited['groups'] == ['ops']

    def test_a_different_users_membership_is_not_this_user(self):
        client = make_client(memberships={
            GROUP_KEY: MemberAPI([{'member': '/v4/users/9'}]),
        })
        module = make_module(base_params(groups=['ops']))

        run(module, client)

        assert client.groups.memberships[GROUP_KEY].added == [USER_KEY]

    def test_a_nested_group_with_the_same_key_is_not_the_user(self):
        client = make_client(memberships={
            GROUP_KEY: MemberAPI([{'member': '/v4/groups/4'}]),
        })
        module = make_module(base_params(groups=['ops']))

        run(module, client)

        assert client.groups.memberships[GROUP_KEY].added == [USER_KEY]

    def test_groups_are_additive(self):
        """Listing one group must not remove the user from another. An empty
        list adds nothing. Omitting the option does not touch membership."""
        other = MemberAPI([{'member': '/v4/users/4'}])
        client = make_client(
            groups=[{'$key': GROUP_KEY, 'name': 'ops'},
                    {'$key': OTHER_GROUP_KEY, 'name': 'auditors'}],
            memberships={
                GROUP_KEY: MemberAPI([]),
                OTHER_GROUP_KEY: other,
            })
        module = make_module(base_params(groups=['ops']))

        run(module, client)

        assert client.groups.memberships[GROUP_KEY].added == [USER_KEY]
        assert other.added == []

    def test_a_second_named_group_is_added_and_the_first_is_left(self):
        client = make_client(
            groups=[{'$key': GROUP_KEY, 'name': 'ops'},
                    {'$key': OTHER_GROUP_KEY, 'name': 'auditors'}],
            memberships={
                GROUP_KEY: MemberAPI([{'member': 'users/4'}]),
                OTHER_GROUP_KEY: MemberAPI([]),
            })
        module = make_module(base_params(groups=['ops', 'auditors']))

        run(module, client)

        assert client.groups.memberships[GROUP_KEY].added == []
        assert client.groups.memberships[OTHER_GROUP_KEY].added == [USER_KEY]
        assert result_of(module)['groups'] == ['ops', 'auditors']

    def test_duplicate_names_in_the_list_add_once(self):
        client = make_client()
        module = make_module(base_params(groups=['ops', 'ops']))

        run(module, client)

        assert client.groups.memberships[GROUP_KEY].added == [USER_KEY]
        assert result_of(module)['groups'] == ['ops']

    def test_an_empty_list_adds_nothing(self):
        client = make_client()
        module = make_module(base_params(groups=[]))

        run(module, client)

        assert client.groups.member_calls == []
        exited = result_of(module)
        assert exited['changed'] is False
        assert exited['groups'] == []

    def test_omitting_groups_does_not_touch_membership(self):
        client = make_client()
        module = make_module(base_params())

        run(module, client)

        assert client.groups.member_calls == []
        assert 'groups' not in result_of(module)

    def test_check_mode_adds_nothing(self):
        client = make_client()
        module = make_module(base_params(groups=['ops']), check_mode=True)

        run(module, client)

        assert client.groups.memberships[GROUP_KEY].added == []
        assert result_of(module)['changed'] is True

    def test_check_mode_on_a_converged_membership_reports_nothing(self):
        client = make_client(memberships={
            GROUP_KEY: MemberAPI([{'member': '/v4/users/4'}]),
        })
        module = make_module(base_params(groups=['ops']), check_mode=True)

        run(module, client)

        assert client.groups.memberships[GROUP_KEY].added == []
        assert result_of(module)['changed'] is False

    def test_check_mode_on_create_does_not_create_or_add(self):
        client = make_client(users=[])
        module = make_module(base_params(user_password='secret', groups=['ops']),
                             check_mode=True)

        run(module, client)

        assert client.users.created == []
        assert client.groups.memberships[GROUP_KEY].added == []
        exited = result_of(module)
        assert exited['changed'] is True
        assert exited['groups'] == ['ops']
        assert 'password' not in exited['user']

    def test_absent_deletes_and_does_not_add_membership(self):
        client = make_client()
        module = make_module(base_params(state='absent', groups=['ops']))

        run(module, client)

        client.users.rows[0].delete.assert_called_once()
        assert client.groups.member_calls == []
        assert result_of(module)['changed'] is True

    def test_the_member_insert_defect_is_explained(self):
        client = make_client()
        client.groups.memberships[GROUP_KEY].add_user = MagicMock(
            side_effect=APIError(
                "404 Error creating member in system table: error setting "
                "field 'members.group': No such file or directory"))
        # The replacement is a MagicMock, which would accept a bad call.
        # Bind the strict method's refusal back on, then let the defect win
        # only for the real signature.
        module = make_module(base_params(groups=['ops']))

        run(module, client)

        msg = failure_of(module)
        assert 'VergeOS defect' in msg
        assert 'ops' in msg


class TestTheDoublesRejectTheOldCalls:
    """The bug survived because MagicMock accepts every keyword. These
    doubles must not."""

    def test_users_get_rejects_username(self):
        with pytest.raises(TypeError, match='username'):
            UserAPI([]).get(username='alice')

    def test_create_rejects_role_and_groups(self):
        with pytest.raises(TypeError, match='role'):
            UserAPI([]).create('alice', 'secret', role='admin')
        with pytest.raises(TypeError, match='groups'):
            UserAPI([]).create('alice', 'secret', groups=['ops'])

    def test_add_user_rejects_a_member_keyword_and_create_is_refused(self):
        handle = MemberAPI([])
        with pytest.raises(TypeError):
            handle.add_user(member='alice')
        with pytest.raises(TypeError, match='add_user'):
            handle.create(member='alice')


class TestAccountLifecycleStillWorks:
    def test_create_requires_a_password(self):
        client = make_client(users=[])
        module = make_module(base_params())

        run(module, client)

        assert 'user_password is required' in failure_of(module)
        assert client.users.created == []

    def test_an_existing_user_with_no_drift_converges(self):
        client = make_client()
        module = make_module(base_params(email=''))

        run(module, client)

        client.users.rows[0].save.assert_not_called()
        assert result_of(module)['changed'] is False

    def test_email_drift_is_written_as_email(self):
        client = make_client()
        module = make_module(base_params(email='new@example.com'))

        run(module, client)

        client.users.rows[0].save.assert_called_once_with(
            email='new@example.com')
        assert result_of(module)['changed'] is True

    def test_delete_is_idempotent_when_the_user_is_already_gone(self):
        client = make_client(users=[])
        module = make_module(base_params(state='absent'))

        run(module, client)

        assert result_of(module)['changed'] is False


class TestSdkErrorsAreHandled:
    @pytest.mark.parametrize('exc', [
        APIError, AuthenticationError, ValidationError, VergeConnectionError,
    ])
    def test_sdk_errors_do_not_fall_through_to_unexpected(self, exc):
        client = make_client()

        def boom(**kwargs):
            raise exc('server said no')

        client.users.list = boom
        module = make_module(base_params())

        run(module, client)

        module.fail_json.assert_called_once()
        assert 'Unexpected error' not in failure_of(module)

    def test_not_found_during_membership_is_not_found(self):
        client = make_client()

        def missing(_key):
            raise NotFoundError('gone')

        client.groups.members = missing
        module = make_module(base_params(groups=['ops']))

        run(module, client)

        assert 'Resource not found' in failure_of(module)


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..', '..', '..'))


class TestTheAdminExampleGrantsAdmin:
    def test_module_examples_do_not_set_role_and_do_grant(self):
        path = os.path.join(_root(), 'plugins', 'modules', 'user.py')
        source = open(path).read()
        examples = source.split("EXAMPLES = r'''", 1)[1].split("'''", 1)[0]
        assert 'role:' not in examples
        assert 'vergeio.vergeos.permission' in examples
        assert 'full_control: true' in examples
        assert 'groups:' in examples

    def test_manage_users_playbook_grants_admin_with_permission(self):
        path = os.path.join(_root(), 'examples', 'manage_users.yml')
        text = open(path).read()
        assert 'role:' not in text
        assert 'vergeio.vergeos.permission' in text
        assert 'full_control: true' in text
        assert 'Create a new admin user' in text
