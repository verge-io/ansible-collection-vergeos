# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared helpers for group membership and, later, permissions.

Today this carries what the ``member`` module needs: how to read a membership
row's reference to the thing it links, and how to recognise a platform defect
that makes a group silently unable to accept members.

The permission-side helpers (identity resolution, grant translation, set
reconciliation) arrive with the ``group`` and ``permission`` modules in #34.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type


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
