#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

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
  username:
    description:
      - The username for the user account.
    type: str
    required: true
  state:
    description:
      - The desired state of the user.
    type: str
    choices: [ present, absent ]
    default: present
  password:
    description:
      - Password for the user account.
      - Required when creating a new user.
    type: str
    no_log: true
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
    type: bool
    default: true
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
  - VergeIO
'''

EXAMPLES = r'''
- name: Create a new admin user
  vergeio.vergeos.user:
    host: "192.168.1.100"
    username: "admin"
    password: "admin_password"
    username: "john.doe"
    password: "user_password"
    email: "john.doe@example.com"
    full_name: "John Doe"
    role: admin
    state: present

- name: Create a readonly user
  vergeio.vergeos.user:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    username: "viewer"
    password: "viewer_password"
    role: readonly
    state: present

- name: Update user email
  vergeio.vergeos.user:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    username: "john.doe"
    email: "john.new@example.com"
    state: present

- name: Disable a user
  vergeio.vergeos.user:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    username: "john.doe"
    enabled: false
    state: present

- name: Delete a user
  vergeio.vergeos.user:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    username: "old.user"
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
    VergeOSAPI,
    VergeOSAPIError,
    vergeos_argument_spec
)


def get_user(api, username):
    """Get user by username"""
    try:
        users = api.get('users')
        for user in users:
            if user.get('username') == username:
                return user
        return None
    except VergeOSAPIError:
        return None


def create_user(module, api):
    """Create a new user"""
    if not module.params.get('password'):
        module.fail_json(msg="Password is required when creating a new user")

    user_data = {
        'username': module.params['username'],
        'password': module.params['password'],
        'enabled': module.params.get('enabled', True),
    }

    optional_params = ['email', 'full_name', 'role', 'groups']
    for param in optional_params:
        if module.params.get(param) is not None:
            user_data[param] = module.params[param]

    if module.check_mode:
        user_data_copy = user_data.copy()
        user_data_copy.pop('password', None)
        return True, user_data_copy

    try:
        result = api.post('users', user_data)
        return True, result
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to create user: {str(e)}")


def update_user(module, api, user):
    """Update an existing user"""
    changed = False
    update_data = {}

    fields_to_check = ['email', 'full_name', 'enabled', 'role', 'groups']
    for field in fields_to_check:
        if module.params.get(field) is not None:
            if user.get(field) != module.params[field]:
                update_data[field] = module.params[field]
                changed = True

    # Handle password update separately
    if module.params.get('password'):
        update_data['password'] = module.params['password']
        changed = True

    if not changed:
        return False, user

    if module.check_mode:
        user.update({k: v for k, v in update_data.items() if k != 'password'})
        return True, user

    try:
        user_id = user.get('id')
        result = api.put(f'users/{user_id}', update_data)
        return True, result
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to update user: {str(e)}")


def delete_user(module, api, user):
    """Delete a user"""
    if module.check_mode:
        return True

    try:
        user_id = user.get('id')
        api.delete(f'users/{user_id}')
        return True
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to delete user: {str(e)}")


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        username=dict(type='str', required=True),
        state=dict(type='str', default='present', choices=['present', 'absent']),
        password=dict(type='str', no_log=True),
        email=dict(type='str'),
        full_name=dict(type='str'),
        enabled=dict(type='bool', default=True),
        role=dict(type='str', choices=['admin', 'user', 'readonly']),
        groups=dict(type='list', elements='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    # Rename the module parameter to avoid conflict
    api_argument_spec = vergeos_argument_spec()
    api_params = {}
    for key in api_argument_spec.keys():
        api_params[key] = module.params[key]

    # Create a modified module params without 'username' conflict
    temp_params = module.params.copy()
    target_username = temp_params.pop('username')

    api = VergeOSAPI(module)
    state = module.params['state']

    try:
        user = get_user(api, target_username)

        if state == 'absent':
            if user:
                delete_user(module, api, user)
                module.exit_json(changed=True, msg=f"User '{target_username}' deleted")
            else:
                module.exit_json(changed=False, msg=f"User '{target_username}' does not exist")

        elif state == 'present':
            if user:
                changed, updated_user = update_user(module, api, user)
                module.exit_json(changed=changed, user=updated_user)
            else:
                changed, new_user = create_user(module, api)
                module.exit_json(changed=changed, user=new_user)

    except VergeOSAPIError as e:
        module.fail_json(msg=str(e))
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
