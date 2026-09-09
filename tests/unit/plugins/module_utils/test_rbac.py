"""Unit tests for the RBAC helpers."""

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils.rbac import (
    RIGHTS,
    find_permission,
    grant_kwargs,
    key_name_map,
    member_names,
    split_member_ref,
    membership_changes,
    resolve_identity,
    rights_of,
)


class Row(dict):
    """A row that also exposes model properties, as the SDK's objects do."""

    def __init__(self, row, **props):
        super().__init__(row)
        for name, value in props.items():
            setattr(self, name, value)


class FakeManager:
    def __init__(self, rows):
        self._rows = rows
        self.called_with = None

    def list(self, **kwargs):
        self.called_with = kwargs
        return list(self._rows)


class FakeClient:
    def __init__(self, users=(), groups=(), permissions=()):
        self.users = FakeManager(users)
        self.groups = FakeManager(groups)
        self.permissions = FakeManager(permissions)


# ── rights translation ───────────────────────────────────────────────────────

def test_rights_of_reads_the_raw_field_names():
    row = {'list': 1, 'read': 1, 'create': 0, 'modify': None, 'delete': False}
    assert rights_of(row) == {'list': True, 'read': True, 'create': False,
                              'modify': False, 'delete': False}


def test_grant_kwargs_prefixes_for_the_sdk():
    """The row says 'read'; the SDK's grant() says 'can_read'. Comparing a
    desired can_read against a row's read reports drift every time."""
    assert grant_kwargs({'read': True, 'list': False}) == {
        'can_list': False, 'can_read': True, 'can_create': False,
        'can_modify': False, 'can_delete': False}


def test_every_right_is_covered():
    assert set(RIGHTS) == {'list', 'read', 'create', 'modify', 'delete'}


# ── identity resolution ──────────────────────────────────────────────────────

def test_resolves_a_user_to_its_identity():
    client = FakeClient(users=[Row({'name': 'alice'}, identity=7)])
    identity, label, error = resolve_identity(client, user='alice')
    assert (identity, error) == (7, None)
    assert label == "user 'alice'"


def test_resolves_a_group_to_its_identity():
    client = FakeClient(groups=[Row({'name': 'ops'}, identity=9)])
    identity, label, error = resolve_identity(client, group='ops')
    assert (identity, error) == (9, None)
    assert label == "group 'ops'"


def test_reads_identity_from_the_raw_row_too():
    client = FakeClient(users=[Row({'name': 'alice', 'identity': 7})])
    assert resolve_identity(client, user='alice')[0] == 7


def test_an_unknown_user_is_a_clear_error():
    """This is the whole reason for naming rather than keying -- a typo
    becomes an error instead of a grant landing on nobody."""
    client = FakeClient(users=[])
    identity, _label, error = resolve_identity(client, user='alicce')
    assert identity is None
    assert "no user named 'alicce'" in error


def test_an_unknown_group_is_a_clear_error():
    assert "no group named" in resolve_identity(FakeClient(), group='nope')[2]


def test_an_ambiguous_name_is_refused_not_guessed():
    client = FakeClient(groups=[Row({'name': 'ops'}, identity=1),
                                Row({'name': 'ops'}, identity=2)])
    assert 'refusing to guess' in resolve_identity(client, group='ops')[2]


def test_both_user_and_group_is_refused():
    assert 'exactly one' in resolve_identity(
        FakeClient(), user='a', group='b')[2]


def test_neither_user_nor_group_is_refused():
    assert 'exactly one' in resolve_identity(FakeClient())[2]


def test_an_identity_less_principal_is_reported():
    client = FakeClient(users=[Row({'name': 'alice'})])
    assert 'has no identity' in resolve_identity(client, user='alice')[2]


# ── permission lookup ────────────────────────────────────────────────────────

PERMS = [
    {'$key': 1, 'table': 'vms', 'row': 0, 'read': 1},
    {'$key': 2, 'table': 'vms', 'row': 42, 'read': 1},
    {'$key': 3, 'table': 'vnets', 'row': 0, 'read': 1},
]


def test_finds_the_table_level_grant():
    client = FakeClient(permissions=PERMS)
    assert find_permission(client, 7, 'vms', 0)['$key'] == 1


def test_finds_a_row_level_grant():
    client = FakeClient(permissions=PERMS)
    assert find_permission(client, 7, 'vms', 42)['$key'] == 2


def test_a_table_level_grant_is_not_a_wildcard():
    """row 0 means all rows, but it is a DIFFERENT permission from a grant on
    a specific row, so asking for row 99 must not return the row-0 grant."""
    client = FakeClient(permissions=PERMS)
    assert find_permission(client, 7, 'vms', 99) is None


def test_a_row_level_grant_does_not_satisfy_a_table_level_lookup():
    client = FakeClient(permissions=[PERMS[1]])
    assert find_permission(client, 7, 'vms', 0) is None


def test_the_table_must_match():
    client = FakeClient(permissions=PERMS)
    assert find_permission(client, 7, 'files', 0) is None


def test_lookup_is_scoped_to_the_identity():
    client = FakeClient(permissions=PERMS)
    find_permission(client, 7, 'vms', 0)
    assert client.permissions.called_with == {'identity_key': 7}


def test_a_string_row_key_still_matches():
    client = FakeClient(permissions=[{'$key': 5, 'table': 'vms', 'row': '42'}])
    assert find_permission(client, 7, 'vms', 42)['$key'] == 5


# ── membership ───────────────────────────────────────────────────────────────

# The rows below are the shape measured on VergeOS 26.1.8, not an invented
# one. The first version of these tests used 'member_name' / 'member_type',
# which exist only as computed SDK properties -- so the tests passed while
# every group reported zero members (bug B2).
#
#   {'$key': 1, 'parent_group': 1, 'member': 'users/1',
#    'member_display': 'welchums', 'creator': ''}
#
# And with fields=all the display name is absent entirely:
#   {'$key': 1, 'parent_group': 1, 'member': 'users/1', 'creator': ''}

def test_split_member_ref_reads_kind_and_key_from_the_reference():
    assert split_member_ref({'member': 'users/1'}) == ('users', '1')
    assert split_member_ref({'member': 'groups/4'}) == ('groups', '4')
    assert split_member_ref({}) == ('', '')


def test_member_names_uses_member_display_when_present():
    users, groups = member_names([
        {'member': 'users/1', 'member_display': 'alice'},
        {'member': 'groups/4', 'member_display': 'ops'},
        {'member': 'users/2', 'member_display': 'bob'},
    ])
    assert users == ['alice', 'bob']
    assert groups == ['ops']


def test_member_names_resolves_by_key_when_display_is_absent():
    """fields=all omits member_display, so the key maps are the fallback."""
    users, groups = member_names(
        [{'member': 'users/1'}, {'member': 'groups/4'}],
        {'1': 'alice'}, {'4': 'ops'})
    assert users == ['alice']
    assert groups == ['ops']


def test_member_names_reports_the_raw_ref_rather_than_dropping_a_member():
    """An unresolvable member must stay visible.

    Dropping it shrinks the have-set, and under exact_members a shrunken
    have-set re-adds a real member instead of removing a phantom -- silent
    either way. 'users/7' is at least a bug report.
    """
    users, groups = member_names([{'member': 'users/7'}])
    assert users == ['users/7']
    assert groups == []


def test_member_names_still_honours_member_type_when_there_is_no_reference():
    users, groups = member_names([
        {'member_name': 'alice', 'member_type': 'users'},
        {'member_name': 'ops', 'member_type': 'groups'},
    ])
    assert users == ['alice']
    assert groups == ['ops']


def test_member_names_skips_rows_with_nothing_to_name_them_by():
    users, groups = member_names([{'creator': 'node1'}])
    assert users == [] and groups == []


def test_key_name_map_keys_by_string():
    class M:
        @staticmethod
        def list():
            return [{'$key': 1, 'name': 'alice'}, {'$key': 2, 'name': 'bob'}]

    class C:
        users = M()

    assert key_name_map(C(), 'users') == {'1': 'alice', '2': 'bob'}


def test_additive_membership_adds_without_removing():
    plan = membership_changes(['alice'], [], ['bob'], [])
    assert plan['add_users'] == ['bob']
    assert plan['remove_users'] == []


def test_exact_membership_removes_the_unlisted():
    plan = membership_changes(['alice', 'carol'], [], ['alice'], [],
                              exact=True)
    assert plan['remove_users'] == ['carol']


def test_exact_membership_removes_unlisted_nested_groups():
    plan = membership_changes([], ['ops', 'old'], [], ['ops'], exact=True)
    assert plan['remove_groups'] == ['old']


def test_no_change_when_membership_already_matches():
    plan = membership_changes(['alice'], ['ops'], ['alice'], ['ops'],
                              exact=True)
    assert not any(plan.values())


def test_an_empty_exact_list_empties_the_group():
    """Explicit and destructive, but asked for."""
    plan = membership_changes(['alice'], [], [], [], exact=True)
    assert plan['remove_users'] == ['alice']


def test_an_empty_additive_list_removes_nothing():
    plan = membership_changes(['alice'], [], [], [], exact=False)
    assert plan['remove_users'] == []


@pytest.mark.parametrize('want', [None, []])
def test_none_and_empty_are_handled(want):
    plan = membership_changes(['alice'], [], want, want)
    assert plan['add_users'] == []
