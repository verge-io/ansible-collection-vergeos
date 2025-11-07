#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: member
short_description: Manage group members in VergeOS
version_added: "1.0.0"
description:
  - Add or remove members from groups in VergeOS.
  - Manage user membership within groups.
options:
  group:
    description:
      - The name of the group.
    type: str
    required: true
  username:
    description:
      - The username of the member to add/remove.
    type: str
    required: true
  state:
    description:
      - The desired state of the membership.
    type: str
    choices: [ present, absent ]
    default: present
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO
'''

EXAMPLES = r'''
- name: Add a member to a group
  vergeio.vergeos.member:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    group: "engineering"
    username: "john.doe"
    state: present

- name: Remove a member from group
  vergeio.vergeos.member:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    group: "engineering"
    username: "old.member"
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
    VergeOSAPI,
    VergeOSAPIError,
    vergeos_argument_spec
)


def get_group_key(api, group_name):
    """Get group key by name"""
    try:
        groups = api.get('groups')
        for group in groups:
            if group.get('name') == group_name:
                return group.get('$key')
        return None
    except VergeOSAPIError:
        return None


def get_user_name(api, username):
    """Verify user exists and return username"""
    try:
        users = api.get('users')
        for user in users:
            if user.get('username') == username:
                return user.get('username')
        return None
    except VergeOSAPIError:
        return None


def get_member(api, group_key, member_username):
    """Get member by group and username"""
    try:
        # Get all members for this group
        members = api.get('members')
        for member in members:
            if member.get('parent_group') == group_key and member.get('member') == member_username:
                return member
        return None
    except VergeOSAPIError:
        return None


def add_member(module, api, group_key, member_username):
    """Add a member to a group"""
    member_data = {
        'parent_group': group_key,
        'member': member_username,
    }

    if module.check_mode:
        return True, member_data

    try:
        result = api.post('members', member_data)
        return True, result
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to add member: {str(e)}")


def update_member(module, api, member):
    """Update a member"""
    # Note: Based on Terraform provider, members don't have updatable fields beyond parent_group and member
    # This function is kept for consistency but may not need to do anything
    return False, member


def remove_member(module, api, member):
    """Remove a member from a group"""
    if module.check_mode:
        return True

    try:
        member_key = member.get('$key')
        api.delete(f'members/{member_key}')
        return True
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to remove member: {str(e)}")


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        group=dict(type='str', required=True),
        username=dict(type='str', required=True),
        state=dict(type='str', default='present', choices=['present', 'absent']),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    api = VergeOSAPI(module)
    group_name = module.params['group']
    member_username = module.params['username']
    state = module.params['state']

    try:
        # Get group key
        group_key = get_group_key(api, group_name)
        if not group_key:
            module.fail_json(msg=f"Group '{group_name}' not found")

        # Verify user exists
        user_exists = get_user_name(api, member_username)
        if not user_exists:
            module.fail_json(msg=f"User '{member_username}' not found")

        # Get existing member
        member = get_member(api, group_key, member_username)

        if state == 'absent':
            if member:
                remove_member(module, api, member)
                module.exit_json(changed=True, msg=f"Member '{member_username}' removed from group '{group_name}'")
            else:
                module.exit_json(changed=False, msg="Member does not exist in group")

        elif state == 'present':
            if member:
                changed, updated_member = update_member(module, api, member)
                module.exit_json(changed=changed, member=updated_member)
            else:
                changed, new_member = add_member(module, api, group_key, member_username)
                module.exit_json(changed=changed, member=new_member)

    except VergeOSAPIError as e:
        module.fail_json(msg=str(e))
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
