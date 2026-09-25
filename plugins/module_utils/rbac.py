# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared helpers for the group, member, permission and user modules.

VergeOS attaches permissions to an IDENTITY, not to a user or a group
directly. Both users and groups carry an identity key, and a permission row
points at one of those -- which is why granting to a user and granting to a
group are the same operation with a different lookup, and why a permission
read back from the API names an identity rather than the thing you granted to.

Group membership is likewise indirect: a membership row names its member by
reference (``/v4/users/4``), not by name. Comparing that reference against a
username is issue #92, which made ``member`` non-functional in every release
up to v2.1.0.
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
    string. The platform sends that string in more than one form depending on
    the projection, and both were measured on VergeOS 26.1.8:

        GET groups?fields=all       -> 'users/1'
        GET members?fields=all      -> '/v4/users/2'
        SDK members().list()        -> '/v4/users/2' plus member_display

    pyvergeos posts the prefixed form when adding
    (``member: f"/v4/users/{key}"``), so the prefix is the platform's
    canonical shape and the bare form is the nested projection's shorthand.
    Parsing only one of them is how a correct-looking split returns
    ``('', 'v4/users/2')`` and every member silently becomes an unresolved
    user.

    So: take the last two path segments, whatever came before them.
    """
    ref = str((row or {}).get('member') or '').strip('/')
    if not ref:
        return '', ''
    parts = [p for p in ref.split('/') if p]
    if len(parts) < 2:
        return '', parts[0] if parts else ''
    return parts[-2], parts[-1]


def find_membership(members, user_key):
    """The membership row linking ``user_key`` to the group, or None.

    The row identifies its member by REFERENCE, not by name. Measured on a
    real row from VergeOS 26.1.8:

        {'$key': 4, 'parent_group': 2, 'member': '/v4/users/4',
         'member_display': 'zz-b13-user', 'creator': 'operator'}

    Comparing that reference against the bare username is always false
    (``'/v4/users/4' == 'zz-b13-user'``), which is issue #92: ``present``
    re-added forever and ``absent`` removed nothing.

    Both shapes the platform sends are real, in the same table, at the same
    time: ``/v4/users/N`` from the members table (and from ``add_user``) and
    ``users/N`` from the nested projection on a group. ``split_member_ref``
    handles both. A nested group with the same key (``groups/N``) is not a
    user and must not match.
    """
    wanted = str(user_key)
    for row in members:
        table, key = split_member_ref(dict(row))
        if table == 'users' and key == wanted:
            return row
    return None


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
    """``{key: name}`` for a manager, for resolving membership references.

    The two columns are named rather than left to the SDK's default
    projection, which is both cheaper and the rule after #92: a column that is
    read but not fetched comes back as None, and here that would turn every
    member into an unresolved raw reference.
    """
    out = {}
    for row in getattr(client, manager).list(fields=['$key', 'name']):
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


# ── the post-delete group membership defect (VergeOS platform) ──────────────
#
# Deleting a GROUP arms a defect in the platform for a few seconds. A group
# created during that window is created successfully and looks entirely
# normal, but every attempt to add a member to it fails:
#
#   HTTP 404 Error creating member in system table:
#            error setting field 'members.group': No such file or directory
#
# Measured on VergeOS 26.1.8, and two plausible-sounding explanations were
# tested and DISPROVED, so they are recorded to stop them being re-invented:
#
#   NOT identity reuse. The group that reclaimed the deleted group's identity
#   worked; a later group with a brand new identity failed:
#       delete A(id 7) -> create C(id 7) add OK -> create D(id 9) add FAIL
#
#   NOT time-healing. The affected group never recovers on its own. Retried at
#   10s, 30s and 60s: still failing. An earlier fix here retried the member add
#   six times over ten seconds; it identified the error correctly every time
#   and every attempt failed.
#
#   NOT "each new group arms itself". This was a previous conclusion here and
#   it is wrong. Creating a later group makes the affected one work again, and
#   that new group is healthy unless it was itself created inside an open
#   window:
#       delete, create A, create B            -> A OK,  B DEFECT
#       delete, create A, wait 10s, create B  -> A OK,  B OK
#
#   NOT "creating another group REPAIRS it". Also wrong, and this one matters:
#   the later group only MASKS the defect. Delete that specific group and the
#   affected group fails again. 5/5 runs; a never-affected group put through
#   the identical sequence stays fine 3/3:
#       G1 defective -> create G2 -> G1 OK -> delete G2 -> G1 DEFECT again
#   The masking is durable while G2 exists (still OK after 60s), and it is
#   exactly G2 that matters -- deleting some other, newer group has no effect.
#
# What it actually is, as far as can be seen from outside: a group created
# inside the window is PERMANENTLY defective. The group created next after it
# masks that, and only until that masking group is itself deleted.
#
#   delete, create A, create B, create C (all inside) -> only C is affected
#
# Existing memberships are never lost -- they stay listed and usable. Only new
# member inserts fail. So an affected group can work for days and then start
# rejecting members after an unrelated group deletion.
#
# This is why the fix below REBUILDS the group rather than creating a decoy:
# a group recreated outside the window is genuinely healthy and survives later
# group create/delete cycles (verified).
#
# The window, six runs at each delay, from a settled system:
#
#   wait 1 / 2 / 2.5 / 3s -> 6/6 affected
#   wait 3.5s             -> 2/6 affected     <- the edge is jittery
#   wait 4 / 5s           -> 0/6 affected
#
# so the boundary is a little under 4s and is not sharp. Hence the 6s constant
# below rather than 4.
#
# It is timed from the DELETE -- a create does not restart it:
#
#   delete, wait 2s, create A, wait 2.5s, create B -> A OK, B OK
#   (B is 4.5s after the delete but only 2.5s after A)
#
# Group deletion specifically arms it, and nothing else does. Each of these is
# a separate run from a quiet system, member add attempted immediately:
#
#   create Y                                 OK
#   create X, create Y                       OK
#   create X, DELETE X, create Y             DEFECT
#   create X, wait 4s, create Y              OK
#   create X, DELETE X, wait 4s, create Y    OK
#   create USER, create Y                    OK
#   create USER, delete USER, create Y       OK
#   create Y, update Y                       OK
#
# and creating another group is the only thing that clears it once armed:
#
#   nothing / wait 60s / create a user / update the group / update another
#   group / list groups / add a member to a DIFFERENT group   -> still DEFECT
#   create another group                                      -> OK
#
# which is why the fix below rebuilds the group rather than waiting or
# retrying. It reproduces over raw HTTP with nothing but the Python standard
# library -- no pyvergeos anywhere in the picture -- which is how we know the
# defect is in the platform rather than in the SDK or in this collection. The
# sequences above are the reproduction; each line is a separate run from a
# quiet system with the member add attempted immediately.
#
# Blast radius is narrow: on an affected group, rename, read, list members,
# grant permissions and delete all work. Only the member insert fails, and the
# group is indistinguishable from a healthy one in the API -- every field
# matches.
#
# It is also unique to this one link. The same create/delete/create/insert-a-
# child sequence was run against 15 parent/child pairs across 9 object types
# (users, vnets, vms, tenants, tags, snapshot profiles, DNS views ...) and only
# groups -> members reproduces. Sharper still: one armed group written into two
# columns of the SAME members table, in the same second --
#
#   as members.member (a member of another group)  -> OK
#   as members.group  (the group holding a member) -> DEFECT
#
# so the group record is reachable; only the members.group lookup fails, which
# is the field the error names.
MEMBER_IDENTITY_MARKER = "error setting field 'members.group'"

# How long to let the platform settle after a group delete before creating a
# group that will take members. 4s was never affected in 12 runs and 3.5s was
# affected in 2 of 6, so the boundary is jittery; 6 leaves margin without being
# slow enough to notice.
GROUP_IDENTITY_SETTLE_SECONDS = 6.0


def is_member_identity_defect(exc):
    """Whether ``exc`` is the post-delete membership defect.

    Matched on the platform's own wording rather than the status code, because
    404 on this endpoint otherwise means a genuinely missing user or group.
    """
    return MEMBER_IDENTITY_MARKER in str(exc)


def member_identity_advice(group_name):
    """What to tell an operator who has hit it.

    The platform's message -- "No such file or directory" about a group that
    plainly exists -- points nowhere near the cause, so this says what
    happened and what to do about it.
    """
    return (
        "group '%s' cannot accept members because it was created within a few "
        "seconds of another group being deleted. This is a VergeOS defect, not "
        "a configuration error, and the group does not recover on its own -- "
        "it is still broken after 60 seconds. Delete it, wait %g seconds, and "
        "create it again. When a play removes and creates groups, leave a "
        "pause between the two."
        % (group_name, GROUP_IDENTITY_SETTLE_SECONDS))
