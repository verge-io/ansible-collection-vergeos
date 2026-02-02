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


def get_user(client, name):
    """Get user by name using SDK"""
    try:
        return client.users.get(name=name)
    except NotFoundError:
        return None


def create_user(module, client):
    """Create a new user using SDK"""
    if not module.params.get('user_password'):
        module.fail_json(msg="user_password is required when creating a new user")

    user_data = {
        'name': module.params['name'],
        'password': module.params['user_password'],
        'enabled': module.params.get('enabled', True),
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

    # Handle password update separately
    if module.params.get('user_password'):
        update_data['password'] = module.params['user_password']
        changed = True

    if not changed:
        return False, user_dict

    if module.check_mode:
        user_dict.update({k: v for k, v in update_data.items() if k != 'password'})
        return True, user_dict

    # Update user attributes and save
    for key, value in update_data.items():
        setattr(user, key, value)
    user.save()
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

    target_username = module.params['name']

    client = get_vergeos_client(module)
    state = module.params['state']

    try:
        user = get_user(client, target_username)

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
