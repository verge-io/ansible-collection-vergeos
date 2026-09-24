#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: member
short_description: Manage group members in VergeOS
version_added: "1.0.0"
description:
  - Add or remove a single user's membership of a group.
  - Manages one user at a time. To declare a group's whole membership - or to
    nest groups inside groups - use M(vergeio.vergeos.group), which reconciles
    the entire set and can remove members it was not told about.
options:
  group:
    description:
      - The name of the group.
    type: str
    required: true
  name:
    description:
      - The username of the member to add/remove.
    type: str
    required: true
    aliases: [ member_name ]
  state:
    description:
      - The desired state of the membership.
    type: str
    choices: [ present, absent ]
    default: present
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Supports C(check_mode).
  - Group and user are matched by name in Python rather than with a
    server-side OData filter, which is this collection's general rule. The
    filter is not safe on the declared floor - pyvergeos 1.2.7 does not escape
    a C({) in a filter literal, so a braced name can resolve to a different
    row (pyVergeOS#100, fixed in 1.2.8). Client-side equality has no escaping
    surface and behaves identically on every supported version.
  - A group that was created within a few seconds of another group being
    deleted cannot accept members, and never recovers. That is a VergeOS
    defect, not a configuration error; this module recognises it and says so
    rather than passing on the platform's message, which is C(No such file or
    directory) about a group that plainly exists.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Add a member to a group
  vergeio.vergeos.member:
    group: "engineering"
    name: "john.doe"
    state: present

- name: Remove a member from group
  vergeio.vergeos.member:
    group: "engineering"
    name: "old.member"
    state: absent
'''

RETURN = r'''
member:
  description: Information about the group member
  returned: when state is present
  type: dict
  sample:
    parent_group: 5
    member: "john.doe"
    $key: "12345"
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    resolve_one,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


from ansible_collections.vergeio.vergeos.plugins.module_utils.rbac import (
    is_member_identity_defect,
    member_identity_advice,
    split_member_ref,
)


def resolve(module, client, manager, name, kind):
    """One user or group by name, or a named failure.

    ``resolve_one`` rather than the SDK's ``get(name=...)``: same reasoning as
    everywhere else in this collection (#72/#85), and it also refuses to guess
    if two rows share a name instead of silently taking the first.

    Note what this replaced. The previous version called
    ``client.users.get(username=...)``, and ``username`` is not a parameter
    ``UserManager.get`` has ever had -- not on 1.2.6, not on 1.6.1 -- so the
    module raised TypeError before it reached any VergeOS logic at all. See
    issue #92.
    """
    try:
        return resolve_one(module, getattr(client, manager), name, kind)
    except NotFoundError:
        module.fail_json(msg="%s '%s' not found" % (kind.capitalize(), name))


def find_membership(members, user_key):
    """The membership row linking ``user_key`` to the group, or None.

    The row identifies its member by REFERENCE, not by name. Measured on a
    real row from VergeOS 26.1.8:

        {'$key': 4, 'parent_group': 2, 'member': '/v4/users/4',
         'member_display': 'zz-b13-user', 'creator': 'welchums'}

    The previous version compared that reference against the bare username:

        '/v4/users/4' == 'zz-b13-user'   ->   False, always

    so ``present`` believed the member was always absent and re-added, and
    ``absent`` never found anyone and silently removed nothing. Same
    compare-and-map class as #8, #10, #18, #59 and #87, on a reference field
    rather than a renamed one.

    ``split_member_ref`` handles both shapes the platform sends --
    ``/v4/users/2`` from the members table and ``users/2`` from the nested
    projection on a group. Matching only one of them reintroduces the bug.
    """
    wanted = str(user_key)
    for row in members:
        table, key = split_member_ref(dict(row))
        if table == 'users' and key == wanted:
            return row
    return None


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        group=dict(type='str', required=True),
        name=dict(type='str', required=True, aliases=['member_name']),
        state=dict(type='str', default='present', choices=['present', 'absent']),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    client = get_vergeos_client(module)
    group_name = module.params['group']
    member_username = module.params['name']
    state = module.params['state']

    try:
        group = resolve(module, client, 'groups', group_name, 'group')
        user = resolve(module, client, 'users', member_username, 'user')

        group_key = dict(group)['$key']
        user_key = dict(user)['$key']

        members = client.groups.members(group_key)
        existing = find_membership(members.list(), user_key)

        if state == 'absent':
            if not existing:
                module.exit_json(
                    changed=False,
                    msg=f"'{member_username}' is not a member of "
                        f"'{group_name}'")
            if not module.check_mode:
                members.remove_user(int(user_key))
            module.exit_json(
                changed=True,
                msg=f"removed '{member_username}' from '{group_name}'")

        # state == 'present'
        if existing:
            # There is nothing on a membership row to reconcile -- it links a
            # user to a group and carries no other settable field -- so being
            # present is the whole of being correct.
            module.exit_json(
                changed=False, member=dict(existing),
                msg=f"'{member_username}' is already a member of "
                    f"'{group_name}'")

        if module.check_mode:
            module.exit_json(
                changed=True,
                msg=f"would add '{member_username}' to '{group_name}'")

        # add_user posts {'parent_group': key, 'member': '/v4/users/<key>'}.
        # The previous version posted the bare username as 'member', which is
        # not the shape the API takes.
        try:
            created = members.add_user(int(user_key))
        except Exception as exc:                            # noqa: BLE001
            if is_member_identity_defect(exc):
                module.fail_json(msg=member_identity_advice(group_name))
            raise
        module.exit_json(
            changed=True, member=dict(created),
            msg=f"added '{member_username}' to '{group_name}'")

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
