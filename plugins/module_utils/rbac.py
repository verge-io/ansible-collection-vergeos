# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared helpers for the group and permission modules.

VergeOS attaches permissions to an IDENTITY, not to a user or a group
directly. Both users and groups carry an identity key, and a permission row
points at one of those -- which is why granting to a user and granting to a
group are the same operation with a different lookup, and why a permission
read back from the API names an identity rather than the thing you granted to.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

# The five rights, in the order the UI shows them. These are the RAW row's
# field names; the SDK's grant() spells them can_list, can_read and so on.
# Comparing a desired can_read against a row's read finds a difference every
# time and reports drift that is not there.
RIGHTS = ('list', 'read', 'create', 'modify', 'delete')


def rights_of(row):
    """The five rights of a permission row, as bools."""
    return {name: bool(row.get(name)) for name in RIGHTS}


def grant_kwargs(rights):
    """Translate the raw right names into the SDK's grant() keywords."""
    return {'can_%s' % name: bool(rights.get(name)) for name in RIGHTS}


def resolve_identity(client, user=None, group=None):
    """Identity key for a user or group NAME.

    Returns (identity_key, label, error) -- exactly one of identity_key and
    error is set.

    Names, not keys, because an RBAC document that referred to identity keys
    would be unreadable and unportable between systems. The cost is a lookup,
    and the lookup is where a typo becomes a clear error instead of a grant
    silently landing on nobody.
    """
    if (user is None) == (group is None):
        return None, None, "exactly one of user or group must be given"

    if user is not None:
        matches = [u for u in client.users.list() if dict(u).get('name') == user]
        kind, wanted = 'user', user
    else:
        matches = [g for g in client.groups.list() if dict(g).get('name') == group]
        kind, wanted = 'group', group

    if not matches:
        return None, None, "no %s named '%s'" % (kind, wanted)
    if len(matches) > 1:
        return None, None, ("%d %ss named '%s'; refusing to guess which one "
                            "to grant to" % (len(matches), kind, wanted))

    obj = matches[0]
    identity = getattr(obj, 'identity', None)
    if identity is None:
        identity = dict(obj).get('identity')
    if identity is None:
        return None, None, ("%s '%s' has no identity, so nothing can be "
                            "granted to it" % (kind, wanted))

    return identity, "%s '%s'" % (kind, wanted), None


def find_permission(client, identity_key, table, row_key=0):
    """The permission row for one (identity, table, row), or None.

    row_key 0 is the table-level grant -- "all rows of this table" -- and is a
    different permission from a grant on a specific row, so both are matched
    exactly rather than treating 0 as a wildcard.
    """
    for row in client.permissions.list(identity_key=identity_key):
        row = dict(row)
        if row.get('table') != table:
            continue
        if int(row.get('row') or 0) != int(row_key or 0):
            continue
        return row
    return None


def split_member_ref(row):
    """``(table, key)`` from a membership row's ``member`` reference.

    The raw row encodes both the kind and the identity of a member in one
    string -- ``"users/1"``, ``"groups/4"``. Measured on VergeOS 26.1.8:

        {'$key': 1, 'parent_group': 1, 'member': 'users/1',
         'member_display': 'welchums', 'creator': ''}

    ``member_type`` / ``member_name`` / ``member_key`` exist only as computed
    properties on the SDK model, which ``dict()`` discards. Reading those
    names off a raw row returns None for every member, which is
    indistinguishable from an empty group -- and with ``exact_members: true``
    that reads as "remove nobody, add everyone again".
    """
    ref = str((row or {}).get('member') or '')
    table, _, key = ref.partition('/')
    return table, key


def member_names(members, users_by_key=None, groups_by_key=None):
    """Split group membership rows into user names and group names.

    A group can contain groups as well as users, and the two are added and
    removed with different SDK calls, so they have to be told apart rather
    than flattened.

    Names are taken from ``member_display`` when the projection carries it,
    then from the caller's key maps, and only then from the raw reference.
    That last fallback is deliberate: a member whose name cannot be resolved
    is reported as ``users/7`` rather than dropped. Dropping it would shrink
    the "have" set, and under ``exact_members`` a shrunken have-set means a
    real member is re-added rather than a phantom removed -- silent either
    way. A visible ``users/7`` is a bug report.
    """
    users_by_key = users_by_key or {}
    groups_by_key = groups_by_key or {}
    users, groups = [], []

    for row in members:
        row = dict(row) if not isinstance(row, dict) else row
        table, key = split_member_ref(row)
        is_group = table == 'groups'
        lookup = groups_by_key if is_group else users_by_key

        # Order matters: a resolved name beats the raw reference, and the
        # raw reference is the last resort rather than a skip.
        name = (row.get('member_display')
                or lookup.get(str(key))
                or lookup.get(key)
                or row.get('member_name')
                or row.get('name')
                or row.get('member'))
        if not name:
            continue

        # Fall back on the old field only when the reference said nothing, so
        # a projection that carries member_type but no member still works.
        if not table:
            kind = (row.get('member_type') or '').lower()
            is_group = 'group' in kind

        (groups if is_group else users).append(str(name))

    return sorted(users), sorted(groups)


def key_name_map(client, manager):
    """``{key: name}`` for a manager, for resolving membership references."""
    out = {}
    for row in getattr(client, manager).list():
        row = dict(row)
        key = row.get('$key')
        if key is not None:
            out[str(key)] = row.get('name')
    return out


def membership_changes(have_users, have_groups, want_users, want_groups,
                       exact=False):
    """What to add, and what to remove when exact.

    Additive by default. An RBAC document that listed a group's members and
    silently removed everyone else would make a partial document destructive,
    and partial documents are the normal case while adopting an estate.
    """
    add_users = sorted(set(want_users or []) - set(have_users))
    add_groups = sorted(set(want_groups or []) - set(have_groups))
    remove_users, remove_groups = [], []

    if exact:
        remove_users = sorted(set(have_users) - set(want_users or []))
        remove_groups = sorted(set(have_groups) - set(want_groups or []))

    return {
        'add_users': add_users,
        'add_groups': add_groups,
        'remove_users': remove_users,
        'remove_groups': remove_groups,
    }
