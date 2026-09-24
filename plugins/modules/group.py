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
version_added: "2.2.0"
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

import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.rbac import (
    GROUP_IDENTITY_SETTLE_SECONDS,
    is_member_identity_defect,
    key_name_map,
    member_identity_advice,
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

# Module parameter -> API column, checked against a live groups row on
# VergeOS 26.1.8 (20 columns). Four of the five names match; one does not.
#
#   identifier  ->  id
#
# There is no `identifier` column. The SDK's GroupManager.create/update
# translate the keyword to `id` on the way out, so WRITING it works and the
# value really does land -- which is exactly what makes this hard to see. The
# read path is where it breaks: dict(row) carries the raw column names, so
# row.get('identifier') is always None, the comparison always differs, and a
# group with an identifier set reports changed=True on every single run and
# never converges.
#
# That is #18's harm reached by a different route, and the sixth time this
# class has appeared (#8, #10, #18, #59, #87). Declaring the map opts this
# module into the guard in tests/unit/plugins/modules/test_field_contracts.py.
CREATE_PARAM_MAP = {
    'name': 'name',
    'description': 'description',
    'email': 'email',
    'identifier': 'id',
    'enabled': 'enabled',
}

# name is the group's identity, not a setting.
UPDATE_FIELD_MAP = {
    'description': 'description',
    'email': 'email',
    'identifier': 'id',
    'enabled': 'enabled',
}

IDENTITY_PARAMS = ('name',)

COMPARISON_FIELDS = tuple(sorted(set(UPDATE_FIELD_MAP.values())))


def find_group(client, name):
    """One group by name, matched client-side, with its columns named.

    The projection is explicit rather than left to the SDK's default. A column
    that is compared but not fetched reads as None and the module reports
    drift forever -- that is #18, and #92 was the same trap on a joined
    column the default projection happened to include.
    """
    fields = ['$key', 'name'] + list(COMPARISON_FIELDS)
    for row in client.groups.list(fields=fields):
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
    """``$key`` for a user or group NAME, or None.

    Two columns, named explicitly -- see key_name_map() in module_utils/rbac.py
    for why the default projection is not relied on (#92).
    """
    for row in getattr(client, manager).list(fields=['$key', 'name']):
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
        # Keyed by MODULE PARAMETER, because that is what the SDK's create()
        # and update() take -- they alias identifier -> id themselves.
        wanted = {k: params[k] for k in UPDATE_FIELD_MAP
                  if params.get(k) is not None}

        if not group:
            if module.check_mode:
                module.exit_json(changed=True, changed_fields=['created'],
                                 msg="would create group '%s'." % name)
            group = client.groups.create(name=name, **wanted)
            changed_fields.append('created')
        else:
            row = dict(group)
            # Compared by API COLUMN. dict(row) carries raw column names, so
            # looking up 'identifier' here finds nothing and the group never
            # converges -- see the note on UPDATE_FIELD_MAP.
            drift = {k: v for k, v in wanted.items()
                     if differs(row.get(UPDATE_FIELD_MAP[k]), v)}
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
                    wanted_members = (
                        [('users', u) for u in plan['add_users']]
                        + [('groups', g) for g in plan['add_groups']])

                    for manager, member_name in wanted_members:
                        if find_key(client, manager, member_name) is None:
                            kind = 'user' if manager == 'users' else 'group'
                            module.fail_json(
                                msg="cannot add %s '%s' to group '%s': no such "
                                    "%s." % (kind, member_name, name, kind))

                    def add_all(key_of_group):
                        """Add every wanted member to the given group."""
                        handle = client.groups.get(key_of_group).members
                        for manager, member_name in wanted_members:
                            member_key = find_key(client, manager, member_name)
                            add = (handle.add_user if manager == 'users'
                                   else handle.add_group)
                            add(member_key)

                    try:
                        add_all(group_key)
                    except Exception as exc:                # noqa: BLE001
                        if not is_member_identity_defect(exc):
                            raise
                        # This group cannot take members and never will. If we
                        # created it moments ago we can rebuild it; if it
                        # already existed, deleting it is not ours to do.
                        if 'created' not in changed_fields:
                            module.fail_json(msg=member_identity_advice(name))
                        client.groups.delete(group_key)
                        time.sleep(GROUP_IDENTITY_SETTLE_SECONDS)
                        group_key = dict(
                            client.groups.create(name=name, **wanted))['$key']
                        changed_fields.append('recreated')
                        try:
                            add_all(group_key)
                        except Exception as exc2:           # noqa: BLE001
                            if is_member_identity_defect(exc2):
                                module.fail_json(msg=member_identity_advice(name))
                            raise

                    # Re-read the handle: group_key may have changed if the
                    # group had to be rebuilt above.
                    handle = client.groups.get(group_key).members
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
