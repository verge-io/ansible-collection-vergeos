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


def member_names(members):
    """Split group membership rows into user and group names.

    A group can contain groups as well as users, and the two are removed with
    different calls, so they have to be told apart rather than flattened.
    """
    users, groups = [], []
    for row in members:
        row = dict(row) if not isinstance(row, dict) else row
        name = row.get('member_name') or row.get('name')
        kind = (row.get('member_type') or '').lower()
        if not name:
            continue
        if 'group' in kind:
            groups.append(name)
        else:
            users.append(name)
    return sorted(users), sorted(groups)


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
