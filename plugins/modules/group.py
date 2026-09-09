#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: group
short_description: Manage VergeOS groups and their membership
version_added: "2.1.0"
description:
  - Create, reconcile and remove groups, and manage who is in them.
  - A group can contain users and other groups.
options:
  name:
    description:
      - Group name, and the identity this module reconciles on.
    type: str
    required: true
  state:
    description:
      - C(present) creates the group if absent and reconciles it if present.
      - C(absent) removes it. Permissions granted to the group go with it.
    type: str
    choices: [ present, absent ]
    default: present
  description:
    description:
      - Group description.
    type: str
  email:
    description:
      - Group email address.
    type: str
  identifier:
    description:
      - External identity linking value, for example the object ID an OIDC
        provider sends. This is how an SSO group maps onto a VergeOS group.
    type: str
  enabled:
    description:
      - Whether the group is enabled. Left as-is when not specified.
    type: bool
  users:
    description:
      - Usernames that should be in the group.
      - Additive unless O(exact_members) is set.
    type: list
    elements: str
  groups:
    description:
      - Names of groups that should be members of this group.
      - Additive unless O(exact_members) is set.
    type: list
    elements: str
  exact_members:
    description:
      - Remove members not named in O(users) and O(groups).
      - Off by default. A partial RBAC document that silently removed everyone
        it did not mention would be destructive, and partial documents are the
        normal case while adopting an estate.
    type: bool
    default: false
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Supports C(check_mode).
  - Users inside a tenant are out of scope; they need tenant-context
    authentication. Point this module at the tenant's own URL instead.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Ensure the operators group exists with its members
  vergeio.vergeos.group:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "operators"
    description: "Day-to-day VM operations"
    users:
      - alice
      - bob

- name: Enforce membership exactly, removing anyone else
  vergeio.vergeos.group:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "operators"
    users:
      - alice
      - bob
    exact_members: true

- name: Map an SSO group onto a VergeOS group
  vergeio.vergeos.group:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "sso-admins"
    identifier: "0a1b2c3d-4e5f-6789-abcd-ef0123456789"

- name: Nest a group inside another
  vergeio.vergeos.group:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "all-staff"
    groups:
      - operators
      - auditors
'''

RETURN = r'''
group:
  description: The group as it stands after the module ran.
  returned: unless O(state=absent) removed it
  type: dict
  sample:
    name: "operators"
    description: "Day-to-day VM operations"
    enabled: true
members:
  description: Who is in the group now.
  returned: unless O(state=absent) removed it
  type: dict
  contains:
    users:
      description: Usernames in the group.
      type: list
      elements: str
      returned: always
    groups:
      description: Names of groups in the group.
      type: list
      elements: str
      returned: always
  sample:
    users: ["alice", "bob"]
    groups: []
changed_fields:
  description:
    - What this run changed. Empty on a no-op.
  returned: always
  type: list
  elements: str
  sample: ["description", "members"]
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.rbac import (
    key_name_map,
    member_names,
    membership_changes,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )

MUTABLE = ('description', 'email', 'identifier', 'enabled')


def find_group(client, name):
    for row in client.groups.list():
        if dict(row).get('name') == name:
            return row
    return None


def differs(have, want):
    """Whether a live field value differs from what was asked for.

    Booleans compare as booleans and everything else as strings: the API
    returns 1/0 for flags and numbers-as-strings elsewhere, so a naive ==
    reports drift on every run for values that already match.
    """
    if isinstance(want, bool):
        return bool(have) != want
    return str(have or '') != str(want)


def find_key(client, manager, name):
    for row in getattr(client, manager).list():
        row_d = dict(row)
        if row_d.get('name') == name:
            return row_d.get('$key')
    return None


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        description=dict(type='str'),
        email=dict(type='str'),
        identifier=dict(type='str'),
        enabled=dict(type='bool'),
        users=dict(type='list', elements='str'),
        groups=dict(type='list', elements='str'),
        exact_members=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    params = module.params
    name = params['name']
    client = get_vergeos_client(module)

    try:
        group = find_group(client, name)
        changed_fields = []

        # ── absent ───────────────────────────────────────────────────────────
        if params['state'] == 'absent':
            if not group:
                module.exit_json(changed=False, changed_fields=[])
            if not module.check_mode:
                client.groups.delete(dict(group)['$key'])
            module.exit_json(
                changed=True, changed_fields=['removed'],
                msg="removed group '%s'. Permissions granted to it went with "
                    "it." % name)

        # ── create ───────────────────────────────────────────────────────────
        wanted = {k: params[k] for k in MUTABLE if params.get(k) is not None}

        if not group:
            if module.check_mode:
                module.exit_json(changed=True, changed_fields=['created'],
                                 msg="would create group '%s'." % name)
            group = client.groups.create(name=name, **wanted)
            changed_fields.append('created')
        else:
            row = dict(group)
            drift = {k: v for k, v in wanted.items()
                     if differs(row.get(k), v)}
            if drift:
                changed_fields += sorted(drift)
                if not module.check_mode:
                    client.groups.update(row['$key'], **drift)
                    group = find_group(client, name) or group

        group_row = dict(group)
        group_key = group_row['$key']

        # ── membership ───────────────────────────────────────────────────────
        # Membership rows name their members by reference ("users/1"), so the
        # key maps are what turn that into a name when the projection omits
        # member_display. Built once and reused; both lists are small.
        users_by_key = key_name_map(client, 'users')
        groups_by_key = key_name_map(client, 'groups')

        members = client.groups.get(group_key).members.list()
        have_users, have_groups = member_names(
            [dict(m) for m in members], users_by_key, groups_by_key)

        if params['users'] is not None or params['groups'] is not None:
            plan = membership_changes(
                have_users, have_groups,
                params.get('users') or [], params.get('groups') or [],
                exact=params['exact_members'])

            if any(plan.values()):
                changed_fields.append('members')
                if not module.check_mode:
                    handle = client.groups.get(group_key).members
                    for username in plan['add_users']:
                        key = find_key(client, 'users', username)
                        if key is None:
                            module.fail_json(
                                msg="cannot add '%s' to group '%s': no such "
                                    "user." % (username, name))
                        handle.add_user(key)
                    for other in plan['add_groups']:
                        key = find_key(client, 'groups', other)
                        if key is None:
                            module.fail_json(
                                msg="cannot add group '%s' to group '%s': no "
                                    "such group." % (other, name))
                        handle.add_group(key)
                    for username in plan['remove_users']:
                        key = find_key(client, 'users', username)
                        if key is not None:
                            handle.remove_user(key)
                    for other in plan['remove_groups']:
                        key = find_key(client, 'groups', other)
                        if key is not None:
                            handle.remove_group(key)

                    members = client.groups.get(group_key).members.list()
                    have_users, have_groups = member_names(
                        [dict(m) for m in members],
                        key_name_map(client, 'users'),
                        key_name_map(client, 'groups'))

        module.exit_json(
            changed=bool(changed_fields),
            changed_fields=sorted(set(changed_fields)),
            group=group_row,
            members={'users': have_users, 'groups': have_groups},
        )

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
