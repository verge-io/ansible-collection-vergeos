#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: group_info
short_description: Gather information about VergeOS groups and permissions
version_added: "2.1.0"
description:
  - Report groups, optionally with their members and the permissions granted
    to them.
  - Answers the question C(who can do what), which is otherwise a click-through
    exercise.
options:
  name:
    description:
      - Report only the group of this name. Matched exactly.
    type: str
  members:
    description:
      - Also report each group's members.
    type: bool
    default: false
  permissions:
    description:
      - Also report the permissions granted to each group.
    type: bool
    default: false
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Report every group with its members and rights
  vergeio.vergeos.group_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    members: true
    permissions: true
  register: rbac

- name: Show who can delete VMs
  ansible.builtin.debug:
    msg: >-
      {{ rbac.groups
         | selectattr('permissions', 'defined')
         | map(attribute='name') | list }}
'''

RETURN = r'''
groups:
  description:
    - Matching groups. Each carries C(members) and C(permissions) when those
      were requested.
  returned: always
  type: list
  elements: dict
  sample:
    - name: "operators"
      enabled: true
      members:
        users: ["alice", "bob"]
        groups: []
      permissions:
        - table: "vms"
          row: 0
          scope: "vms#0"
          list: true
          read: true
system_groups:
  description:
    - Names of groups the platform created and manages itself. Reconciling
      these is not something to do casually.
  returned: always
  type: list
  elements: str
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.rbac import (
    member_names,
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


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
        members=dict(type='bool', default=False),
        permissions=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    params = module.params
    client = get_vergeos_client(module)

    try:
        groups = []
        system = []

        for obj in client.groups.list():
            row = dict(obj)
            if params.get('name') and row.get('name') != params['name']:
                continue
            if row.get('system'):
                system.append(row.get('name'))

            if params['members']:
                raw = client.groups.get(row['$key']).members.list()
                users, nested = member_names([dict(m) for m in raw])
                row['members'] = {'users': users, 'groups': nested}

            if params['permissions']:
                identity = getattr(obj, 'identity', None) or row.get('identity')
                grants = []
                if identity is not None:
                    for perm in client.permissions.list(identity_key=identity):
                        perm = dict(perm)
                        row_key = int(perm.get('row') or 0)
                        grants.append(dict(
                            table=perm.get('table'),
                            row=row_key,
                            # A table-level grant (row 0) and a grant on one
                            # row of the same table are DIFFERENT permissions.
                            # Comparing on the table alone treats a row-level
                            # grant as covering a table-wide one, which is the
                            # direction that silently keeps too much access.
                            scope="%s#%d" % (perm.get('table'), row_key),
                            row_display=perm.get('rowdisplay'),
                            **rights_of(perm)))
                row['permissions'] = grants

            groups.append(row)

        if params.get('name') and not groups:
            module.fail_json(msg="no group named '%s'." % params['name'])

        module.exit_json(changed=False, groups=groups,
                         system_groups=sorted(n for n in system if n))

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
