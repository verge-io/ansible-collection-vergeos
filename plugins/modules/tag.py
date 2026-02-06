#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: tag
short_description: Manage tags and VM tag assignments in VergeOS
version_added: "2.0.0"
description:
  - Create, update, and delete tags in VergeOS.
  - Apply or remove tags from virtual machines.
  - Tags must belong to a category that has the appropriate taggable resource type enabled.
options:
  name:
    description:
      - The name of the tag.
      - Must be unique within the category.
    type: str
    required: true
  category:
    description:
      - The name of the tag category this tag belongs to.
      - Required when creating a new tag or when applying tags to VMs.
    type: str
  state:
    description:
      - The desired state of the tag.
      - When C(present), ensures the tag exists and optionally applies it to a VM.
      - When C(absent), removes the tag from a VM (if vm_name/vm_id specified) or deletes the tag.
    type: str
    choices: [ present, absent ]
    default: present
  description:
    description:
      - Description of the tag.
    type: str
  vm_name:
    description:
      - Name of the VM to apply/remove the tag to/from.
      - Mutually exclusive with I(vm_id).
      - When specified, the module applies or removes the tag assignment rather than managing the tag itself.
    type: str
  vm_id:
    description:
      - ID ($key) of the VM to apply/remove the tag to/from.
      - Mutually exclusive with I(vm_name).
      - When specified, the module applies or removes the tag assignment rather than managing the tag itself.
    type: int
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Create a tag in a category
  vergeio.vergeos.tag:
    name: "DB"
    category: "App"
    description: "Database servers"
    state: present

- name: Create multiple tags
  vergeio.vergeos.tag:
    name: "{{ item }}"
    category: "App"
    state: present
  loop:
    - DB
    - WEB
    - API

- name: Apply a tag to a VM by name
  vergeio.vergeos.tag:
    name: "DB"
    category: "App"
    vm_name: "testdbvm"
    state: present

- name: Apply a tag to a VM by ID
  vergeio.vergeos.tag:
    name: "WEB"
    category: "App"
    vm_id: 123
    state: present

- name: Remove a tag from a VM
  vergeio.vergeos.tag:
    name: "DB"
    category: "App"
    vm_name: "testdbvm"
    state: absent

- name: Delete a tag entirely
  vergeio.vergeos.tag:
    name: "OldTag"
    category: "App"
    state: absent

- name: Tag all VMs with 'db' in name (used with inventory)
  vergeio.vergeos.tag:
    host: "{{ vergeos_site_url }}"
    name: "DB"
    category: "App"
    vm_name: "{{ vergeos_name }}"
    state: present
  when: "'db' in vergeos_name | lower"
  delegate_to: localhost
'''

RETURN = r'''
tag:
  description: Information about the tag
  returned: when managing tag (no vm_name/vm_id)
  type: dict
  sample:
    key: 1
    name: "DB"
    description: "Database servers"
    category_key: 1
    category_name: "App"
vm_tagged:
  description: Whether a VM was tagged/untagged
  returned: when vm_name or vm_id specified
  type: bool
  sample: true
vm_name:
  description: Name of the VM that was tagged/untagged
  returned: when vm_name or vm_id specified
  type: str
  sample: "testdbvm"
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


def get_tag(client, name, category_name):
    """Get tag by name and category using SDK"""
    try:
        return client.tags.get(name=name, category_name=category_name)
    except NotFoundError:
        return None


def get_category(client, name):
    """Get tag category by name using SDK"""
    try:
        return client.tag_categories.get(name=name)
    except NotFoundError:
        return None


def get_vm(client, name=None, vm_id=None):
    """Get VM by name or ID using SDK"""
    try:
        if vm_id is not None:
            return client.vms.get(key=vm_id)
        elif name is not None:
            return client.vms.get(name=name)
    except NotFoundError:
        return None
    return None


def tag_to_dict(tag):
    """Convert tag object to a clean dictionary for output"""
    return {
        'key': tag.key,
        'name': tag.name,
        'description': tag.description,
        'category_key': tag.category_key,
        'category_name': tag.category_name,
    }


def vm_has_tag(vm, tag_key):
    """Check if a VM already has a specific tag"""
    try:
        vm_tags = vm.get_tags()
        for t in vm_tags:
            if t.get('tag_key') == tag_key:
                return True
    except Exception:
        pass
    return False


def create_tag(module, client, category):
    """Create a new tag using SDK"""
    params = module.params

    if module.check_mode:
        return True, {
            'name': params['name'],
            'description': params.get('description'),
            'category_key': category.key,
            'category_name': category.name,
        }

    tag = client.tags.create(
        name=params['name'],
        category_key=category.key,
        description=params.get('description'),
    )
    return True, tag_to_dict(tag)


def update_tag(module, client, tag):
    """Update an existing tag using SDK"""
    params = module.params
    changed = False
    update_kwargs = {}

    current = tag_to_dict(tag)

    if params.get('description') is not None:
        if current['description'] != params['description']:
            update_kwargs['description'] = params['description']
            changed = True

    if not changed:
        return False, current

    if module.check_mode:
        current.update(update_kwargs)
        return True, current

    updated = client.tags.update(tag.key, **update_kwargs)
    return True, tag_to_dict(updated)


def delete_tag(module, client, tag):
    """Delete a tag using SDK"""
    if module.check_mode:
        return True

    tag.delete()
    return True


def apply_tag_to_vm(module, client, tag, vm):
    """Apply a tag to a VM"""
    if vm_has_tag(vm, tag.key):
        return False

    if module.check_mode:
        return True

    vm.tag(tag.key)
    return True


def remove_tag_from_vm(module, client, tag, vm):
    """Remove a tag from a VM"""
    if not vm_has_tag(vm, tag.key):
        return False

    if module.check_mode:
        return True

    vm.untag(tag.key)
    return True


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        category=dict(type='str'),
        state=dict(type='str', default='present', choices=['present', 'absent']),
        description=dict(type='str'),
        vm_name=dict(type='str'),
        vm_id=dict(type='int'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        mutually_exclusive=[
            ('vm_name', 'vm_id'),
        ],
    )

    tag_name = module.params['name']
    category_name = module.params.get('category')
    state = module.params['state']
    vm_name = module.params.get('vm_name')
    vm_id = module.params.get('vm_id')

    # Category is required for most operations
    if state == 'present' and not category_name:
        module.fail_json(msg="'category' is required when state is 'present'")

    if (vm_name or vm_id) and not category_name:
        module.fail_json(msg="'category' is required when specifying vm_name or vm_id")

    client = get_vergeos_client(module)

    try:
        # Get category if specified
        category = None
        if category_name:
            category = get_category(client, category_name)
            if not category:
                module.fail_json(msg=f"Tag category '{category_name}' not found")

        # Get existing tag
        tag = None
        if category:
            tag = get_tag(client, tag_name, category_name)

        # Handle VM tagging operations
        if vm_name or vm_id:
            # Get the VM
            vm = get_vm(client, name=vm_name, vm_id=vm_id)
            vm_identifier = vm_name or str(vm_id)

            if not vm:
                module.fail_json(msg=f"VM '{vm_identifier}' not found")

            if state == 'present':
                # Tag must exist to apply it
                if not tag:
                    module.fail_json(
                        msg=f"Tag '{tag_name}' not found in category '{category_name}'. "
                            "Create the tag first before applying it to VMs."
                    )

                changed = apply_tag_to_vm(module, client, tag, vm)
                module.exit_json(
                    changed=changed,
                    vm_tagged=changed,
                    vm_name=vm.name,
                    tag=tag_to_dict(tag),
                    msg=f"Tag '{tag_name}' {'applied to' if changed else 'already on'} VM '{vm.name}'"
                )

            elif state == 'absent':
                if not tag:
                    # Tag doesn't exist, nothing to remove
                    module.exit_json(
                        changed=False,
                        vm_tagged=False,
                        vm_name=vm.name,
                        msg=f"Tag '{tag_name}' does not exist in category '{category_name}'"
                    )

                changed = remove_tag_from_vm(module, client, tag, vm)
                module.exit_json(
                    changed=changed,
                    vm_tagged=not changed,
                    vm_name=vm.name,
                    msg=f"Tag '{tag_name}' {'removed from' if changed else 'not on'} VM '{vm.name}'"
                )

        # Handle tag CRUD operations (no VM specified)
        else:
            if state == 'absent':
                if tag:
                    delete_tag(module, client, tag)
                    module.exit_json(changed=True, msg=f"Tag '{tag_name}' deleted")
                else:
                    module.exit_json(changed=False, msg=f"Tag '{tag_name}' does not exist")

            elif state == 'present':
                if tag:
                    changed, updated_tag = update_tag(module, client, tag)
                    module.exit_json(changed=changed, tag=updated_tag)
                else:
                    changed, new_tag = create_tag(module, client, category)
                    module.exit_json(changed=changed, tag=new_tag)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
