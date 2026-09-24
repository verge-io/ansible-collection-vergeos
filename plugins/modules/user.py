#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: user
short_description: Manage users in VergeOS
version_added: "1.0.0"
description:
  - Create, update, and delete user accounts in VergeOS.
options:
  name:
    description:
      - The username for the user account to manage.
    type: str
    required: true
  state:
    description:
      - The desired state of the user.
    type: str
    choices: [ present, absent ]
    default: present
  user_password:
    description:
      - Password for the user account.
      - Required when creating a new user.
      - >-
        Whether an existing user's password is rewritten is governed by
        O(update_password).
    type: str
  update_password:
    description:
      - When to apply O(user_password) to a user that already exists.
      - V(on_create) only sets the password when the user is created.
      - V(always) rewrites the password on every run.
      - >-
        The default is V(on_create) because VergeOS never returns a stored
        password, so there is nothing to compare against. Before 2.1.0 the
        password was rewritten unconditionally, which made any task that
        supplied O(user_password) report RV(ignore:changed) on every run and
        never converge.
      - >-
        V(always) is the correct choice for password rotation, and it will
        report changed every run by design.
    type: str
    choices: [ always, on_create ]
    default: on_create
    version_added: "2.1.0"
  email:
    description:
      - Email address for the user.
    type: str
  full_name:
    description:
      - Full name of the user.
    type: str
  enabled:
    description:
      - Whether the user account is enabled.
      - Defaults to C(true) when creating. Omit to leave unchanged on update.
    type: bool
  role:
    description:
      - Role assigned to the user.
    type: str
    choices: [ admin, user, readonly ]
  groups:
    description:
      - List of groups the user belongs to.
    type: list
    elements: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Create a new admin user
  vergeio.vergeos.user:
    name: "john.doe"
    user_password: "secure_password"
    email: "john.doe@example.com"
    full_name: "John Doe"
    role: admin
    state: present

- name: Create a readonly user
  vergeio.vergeos.user:
    name: "viewer"
    user_password: "viewer_password"
    role: readonly
    state: present

- name: Update user email
  vergeio.vergeos.user:
    name: "john.doe"
    email: "john.new@example.com"
    state: present

- name: Disable a user
  vergeio.vergeos.user:
    name: "john.doe"
    enabled: false
    state: present

- name: Delete a user
  vergeio.vergeos.user:
    name: "old.user"
    state: absent
'''

RETURN = r'''
user:
  description: Information about the user
  returned: when state is present
  type: dict
  sample:
    username: "john.doe"
    email: "john.doe@example.com"
    full_name: "John Doe"
    enabled: true
    role: "admin"
    id: "12345"
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    resolve_one,
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


def get_user(module, client, name):
    """Get user by name using SDK"""
    try:
        return resolve_one(module, client.users, name, 'user')
    except NotFoundError:
        return None


def create_user(module, client):
    """Create a new user using SDK"""
    if not module.params.get('user_password'):
        module.fail_json(msg="user_password is required when creating a new user")

    user_data = {
        'name': module.params['name'],
        'password': module.params['user_password'],
        'enabled': module.params['enabled'] if module.params['enabled'] is not None else True,
    }

    # Map module params to SDK fields
    if module.params.get('email'):
        user_data['email'] = module.params['email']
    if module.params.get('full_name'):
        user_data['displayname'] = module.params['full_name']

    if module.check_mode:
        user_data_copy = user_data.copy()
        user_data_copy.pop('password', None)
        return True, user_data_copy

    user = client.users.create(**user_data)
    return True, dict(user)


def update_user(module, client, user):
    """Update an existing user using SDK"""
    changed = False
    update_data = {}

    user_dict = dict(user)

    # Check simple fields
    if module.params.get('email') is not None:
        if user_dict.get('email') != module.params['email']:
            update_data['email'] = module.params['email']
            changed = True

    if module.params.get('enabled') is not None:
        if user_dict.get('enabled') != module.params['enabled']:
            update_data['enabled'] = module.params['enabled']
            changed = True

    # Map full_name to displayname
    if module.params.get('full_name') is not None:
        if user_dict.get('displayname') != module.params['full_name']:
            update_data['displayname'] = module.params['full_name']
            changed = True

    # Password: there is nothing to diff against -- VergeOS does not return a
    # stored password -- so rewriting it unconditionally meant the task could
    # never converge. Only rewrite when explicitly asked to.
    if (module.params.get('user_password')
            and module.params.get('update_password') == 'always'):
        update_data['password'] = module.params['user_password']
        changed = True

    if not changed:
        return False, user_dict

    if module.check_mode:
        user_dict.update({k: v for k, v in update_data.items() if k != 'password'})
        return True, user_dict

    user = user.save(**update_data)
    return True, dict(user)


def delete_user(module, client, user):
    """Delete a user using SDK"""
    if module.check_mode:
        return True

    user.delete()
    return True


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present', choices=['present', 'absent']),
        user_password=dict(type='str', no_log=True),
        update_password=dict(type='str', default='on_create',
                             choices=['always', 'on_create']),
        email=dict(type='str'),
        full_name=dict(type='str'),
        enabled=dict(type='bool'),
        role=dict(type='str', choices=['admin', 'user', 'readonly']),
        groups=dict(type='list', elements='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    target_username = module.params['name']

    client = get_vergeos_client(module)
    state = module.params['state']

    try:
        user = get_user(module, client, target_username)

        if state == 'absent':
            if user:
                delete_user(module, client, user)
                module.exit_json(changed=True, msg=f"User '{target_username}' deleted")
            else:
                module.exit_json(changed=False, msg=f"User '{target_username}' does not exist")

        elif state == 'present':
            if user:
                changed, updated_user = update_user(module, client, user)
                module.exit_json(changed=changed, user=updated_user)
            else:
                changed, new_user = create_user(module, client)
                module.exit_json(changed=changed, user=new_user)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
