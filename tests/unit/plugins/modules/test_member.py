"""Unit tests for the member module's membership matching.

The shipped module could not work. Two defects, the first fatal:

  1. get_user() called client.users.get(username=...) against a signature of
     get(key=None, *, name=None, fields=None) -- TypeError on every run, both
     states.
  2. get_member() compared the row's `member` field, a reference, against a
     bare username. '/v4/users/2' == 'labuser' is never true, so `present`
     always re-added and `absent` never removed.

These tests pin the second, which is the one that would survive a casual fix
to the first and still be silently wrong.
"""

from ansible_collections.vergeio.vergeos.plugins.modules.member import (
    find_membership,
)

# Measured on VergeOS 26.1.8, GET members?fields=all&filter=parent_group eq 2
PREFIXED = {'$key': 4, 'parent_group': 2, 'member': '/v4/users/2',
            'system': False, 'creator': 'welchums'}

# Measured on the same system, GET groups?fields=all -- nested projection
BARE = {'$key': 1, 'parent_group': 1, 'member': 'users/1', 'creator': ''}


def test_finds_a_membership_by_the_prefixed_reference():
    assert find_membership([PREFIXED], 2) is PREFIXED
    assert find_membership([PREFIXED], '2') is PREFIXED


def test_finds_a_membership_by_the_bare_reference():
    """Both forms are real. Matching only one reintroduces the same bug."""
    assert find_membership([BARE], 1) is BARE


def test_a_username_never_matches_a_reference():
    """The original defect, pinned.

    Passing the username where the key belongs must not match, because that
    is exactly the comparison that made `absent` a no-op forever.
    """
    assert find_membership([PREFIXED], 'labuser') is None


def test_a_different_user_does_not_match():
    assert find_membership([PREFIXED], 3) is None


def test_a_group_member_is_not_mistaken_for_a_user():
    """A nested group with the same key as a user must not match.

    '/v4/groups/2' and '/v4/users/2' share a key. Matching on the trailing
    number alone would remove the wrong member.
    """
    group_row = dict(PREFIXED, member='/v4/groups/2')
    assert find_membership([group_row], 2) is None


def test_the_right_row_is_returned_from_a_mixed_list():
    rows = [
        dict(PREFIXED, **{'$key': 1, 'member': '/v4/groups/2'}),
        dict(PREFIXED, **{'$key': 2, 'member': '/v4/users/9'}),
        PREFIXED,
    ]
    assert find_membership(rows, 2) is PREFIXED


def test_malformed_rows_are_skipped_not_fatal():
    rows = [{'member': ''}, {'member': 'nonsense'}, {}, PREFIXED]
    assert find_membership(rows, 2) is PREFIXED


def test_no_members_is_no_match():
    assert find_membership([], 2) is None
