#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: permission
short_description: Manage VergeOS permissions for a user or group
version_added: "2.2.0"
description:
  - Grant, reconcile and revoke the five VergeOS rights - list, read, create,
    modify, delete - on a table, or on a single row of a table, for one user
    or one group.
options:
  user:
    description:
      - Name of the user to grant to. Mutually exclusive with O(group).
    type: str
  group:
    description:
      - Name of the group to grant to. Mutually exclusive with O(user).
      - Prefer groups. A permission granted to a group survives staff changes;
        one granted to a user has to be found and removed when they leave.
    type: str
  table:
    description:
      - Table the rights apply to, for example C(vms) or C(vnets).
      - C(/) is the system root, which is how a system-wide grant is
        expressed.
    type: str
    required: true
  row:
    description:
      - Key of a single row to scope the grant to.
      - C(0), the default, is the table-level grant meaning all rows.
      - A table-level grant and a row-level grant are different permissions,
        so C(0) is matched exactly rather than treated as a wildcard.
    type: int
    default: 0
  state:
    description:
      - C(present) creates the grant if absent and reconciles its rights if
        present.
      - C(absent) revokes it.
    type: str
    choices: [ present, absent ]
    default: present
  rights:
    description:
      - Which of the five rights to grant. Any right not named is set to
        false, so this list is the whole grant rather than an addition to it.
      - Ignored when O(full_control) is set.
    type: list
    elements: str
    choices: [ list, read, create, modify, delete ]
    default: [ list ]
  full_control:
    description:
      - Grant all five rights, ignoring O(rights).
    type: bool
    default: false
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Supports C(check_mode).
  - Users and groups are named, not keyed. An RBAC document referring to
    identity keys would be unreadable and would not port between systems; the
    cost is a lookup, and the lookup is where a typo becomes a clear error
    instead of a grant silently landing on nobody.
  - VergeOS attaches permissions to an identity, which both users and groups
    carry. Granting to a user and to a group are therefore the same operation
    with a different lookup.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Let the operators group manage VMs but not delete them
  vergeio.vergeos.permission:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    group: "operators"
    table: "vms"
    rights: [list, read, create, modify]

- name: Give auditors read-only sight of the whole system
  vergeio.vergeos.permission:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    group: "auditors"
    table: "/"
    rights: [list, read]

- name: Scope a contractor to one VM
  vergeio.vergeos.permission:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    user: "contractor"
    table: "vms"
    row: 42
    rights: [list, read, modify]

- name: Revoke a grant
  vergeio.vergeos.permission:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    group: "operators"
    table: "vnets"
    state: absent
'''

RETURN = r'''
permission:
  description: The grant as it stands after the module ran.
  returned: unless O(state=absent) removed it
  type: dict
  sample:
    table: "vms"
    row: 0
    list: true
    read: true
    create: true
    modify: true
    delete: false
identity:
  description:
    - The identity the grant is attached to, described in human terms.
  returned: always
  type: str
  sample: "group 'operators'"
changed_rights:
  description:
    - Rights whose value this run changed. Empty on a no-op.
  returned: always
  type: list
  elements: str
  sample: ["modify"]
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.rbac import (
    RIGHTS,
    find_permission,
    grant_kwargs,
    resolve_identity,
    rights_of,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


# There is no parameter-to-column map here on purpose. This module's only
# rename is on the RIGHTS, not on a parameter: the row stores them under their
# bare names -- list, read, create, modify, delete -- while the SDK's grant()
# spells them can_list, can_read and so on. Comparing a desired `can_read`
# against a row's `read` finds a difference every time and reports drift that
# is not there, which is why RIGHTS and grant_kwargs() are kept separate in
# module_utils/rbac.py.
#
# That rename is checked against the real SDK signature in
# tests/unit/plugins/module_utils/test_rbac.py, which is stronger than a
# declared map: a map only says what we believe, and the signature is what is
# true. Same reasoning as tests/unit/test_sdk_call_signatures.py (#92).
COMPARISON_FIELDS = ('table', 'row') + RIGHTS


def wanted_rights(params):
    if params['full_control']:
        return {name: True for name in RIGHTS}
    asked = set(params['rights'] or [])
    return {name: name in asked for name in RIGHTS}


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        user=dict(type='str'),
        group=dict(type='str'),
        table=dict(type='str', required=True),
        row=dict(type='int', default=0),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        rights=dict(type='list', elements='str', default=['list'],
                    choices=['list', 'read', 'create', 'modify', 'delete']),
        full_control=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        mutually_exclusive=[('user', 'group')],
        required_one_of=[('user', 'group')],
        supports_check_mode=True,
    )

    params = module.params
    client = get_vergeos_client(module)

    try:
        identity, label, error = resolve_identity(
            client, user=params.get('user'), group=params.get('group'))
        if error:
            module.fail_json(msg=error)

        table = params['table']
        row_key = params['row']
        current = find_permission(client, identity, table, row_key)
        result = dict(identity=label, changed_rights=[])

        # ── absent ───────────────────────────────────────────────────────────
        if params['state'] == 'absent':
            if not current:
                module.exit_json(changed=False, **result)
            if not module.check_mode:
                client.permissions.revoke(current['$key'])
            module.exit_json(changed=True, changed_rights=['revoked'],
                             identity=label)

        # ── present ──────────────────────────────────────────────────────────
        want = wanted_rights(params)

        if not current:
            if not module.check_mode:
                created = client.permissions.grant(
                    table, identity_key=identity, row_key=row_key,
                    **grant_kwargs(want))
                result['permission'] = dict(created)
            result['changed_rights'] = sorted(n for n, v in want.items() if v)
            module.exit_json(changed=True, **result)

        have = rights_of(current)
        differing = sorted(n for n in RIGHTS if have[n] != want[n])

        if not differing:
            result['permission'] = current
            module.exit_json(changed=False, **result)

        if not module.check_mode:
            # Revoke and re-grant rather than update. The grant path is the
            # only one the SDK exposes for setting rights, and a permission is
            # identified by (identity, table, row) -- so re-granting the same
            # triple replaces the rights rather than adding a second row.
            client.permissions.revoke(current['$key'])
            created = client.permissions.grant(
                table, identity_key=identity, row_key=row_key,
                **grant_kwargs(want))
            result['permission'] = dict(created)
        else:
            result['permission'] = current

        result['changed_rights'] = differing
        module.exit_json(changed=True, **result)

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
