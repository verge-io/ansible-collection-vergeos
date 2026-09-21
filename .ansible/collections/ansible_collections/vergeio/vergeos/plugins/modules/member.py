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
    server-side OData filter. This began as a workaround - pyvergeos used to
    escape an apostrophe SQL-style, which VergeOS rejects with HTTP 422
    rather than returning no match. That was fixed upstream in pyvergeos
    1.2.5, and this collection now requires 1.2.7, so the workaround is no
    longer needed. It is kept for now because these lists are small and
    switching to a server-side filter is a behaviour change to working code.
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
)


def find_by_name(client, manager, name):
    """One row by name from a manager, or None. Matched client-side.

    Not ``manager.get(name=...)``: that builds an OData filter, and pyvergeos
    used to escape a literal apostrophe SQL-style, which VergeOS 26.1.8
    rejects with HTTP 422 rather than returning no match. Group and user
    names are user supplied, so that was reachable.

    Fixed upstream in pyvergeos 1.2.5 and re-verified on 1.2.7 (2026-09-21):
    ``networks.get(name="zz-o'brien")`` now raises ``NotFoundError``, the
    same as any other absent name. ``requirements.txt`` declares ``>=1.2.7``,
    so the original reason is gone. Kept anyway, for now: these lists are
    small, the cost is a full listing, and moving to a server-side filter is
    a behaviour change that deserves its own decision rather than being
    slipped in with a documentation correction.
    """
    for row in getattr(client, manager).list():
        if dict(row).get('name') == name:
            return row
    return None


def find_membership(members, user_key):
    """The membership row linking ``user_key`` to the group, or None.

    The row identifies its member by reference, not by name. Measured on a
    real row from VergeOS 26.1.8:

        {'$key': 4, 'parent_group': 2, 'member': '/v4/users/2',
         'system': False, 'creator': 'welchums'}

    The previous version of this module compared that reference against the
    bare username, which can never be equal -- so ``present`` believed the
    member was always absent and re-added, and ``absent`` never found anyone
    and silently removed nothing.

    Both reference forms are handled. ``/v4/users/2`` comes back from the
    members table and is what pyvergeos POSTs; ``users/2`` comes back from
    the nested projection on a group. Matching only one of them reintroduces
    the same class of bug.
    """
    wanted = str(user_key)
    for row in members:
        parts = [p for p in str(dict(row).get('member') or '').split('/') if p]
        if len(parts) < 2:
            continue
        table, key = parts[-2], parts[-1]
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
        group = find_by_name(client, 'groups', group_name)
        if not group:
            module.fail_json(msg=f"Group '{group_name}' not found")

        user = find_by_name(client, 'users', member_username)
        if not user:
            module.fail_json(msg=f"User '{member_username}' not found")

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
