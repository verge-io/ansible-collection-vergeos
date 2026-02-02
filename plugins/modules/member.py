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
author:
  - VergeIO
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


def get_group(client, group_name):
    """Get group by name using SDK"""
    try:
        return client.groups.get(name=group_name)
    except NotFoundError:
        return None


def get_user(client, username):
    """Verify user exists using SDK"""
    try:
        return client.users.get(username=username)
    except NotFoundError:
        return None


def get_member(client, group, member_username):
    """Get member by group and username using SDK"""
    try:
        members = list(group.members.list())
        for member in members:
            member_dict = dict(member)
            if member_dict.get('member') == member_username:
                return member
        return None
    except (NotFoundError, AttributeError):
        return None


def add_member(module, client, group, member_username):
    """Add a member to a group using SDK"""
    if module.check_mode:
        return True, {'member': member_username}

    member = group.members.create(member=member_username)
    return True, dict(member)


def update_member(module, client, member):
    """Update a member using SDK"""
    # Note: Based on Terraform provider, members don't have updatable fields beyond parent_group and member
    # This function is kept for consistency but may not need to do anything
    return False, dict(member)


def remove_member(module, client, member):
    """Remove a member from a group using SDK"""
    if module.check_mode:
        return True

    member.delete()
    return True


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
        # Get group
        group = get_group(client, group_name)
        if not group:
            module.fail_json(msg=f"Group '{group_name}' not found")

        # Verify user exists
        user = get_user(client, member_username)
        if not user:
            module.fail_json(msg=f"User '{member_username}' not found")

        # Get existing member
        member = get_member(client, group, member_username)

        if state == 'absent':
            if member:
                remove_member(module, client, member)
                module.exit_json(changed=True, msg=f"Member '{member_username}' removed from group '{group_name}'")
            else:
                module.exit_json(changed=False, msg="Member does not exist in group")

        elif state == 'present':
            if member:
                changed, updated_member = update_member(module, client, member)
                module.exit_json(changed=changed, member=updated_member)
            else:
                changed, new_member = add_member(module, client, group, member_username)
                module.exit_json(changed=changed, member=new_member)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
