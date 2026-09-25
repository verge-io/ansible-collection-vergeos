#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: tag_category
short_description: Manage tag categories in VergeOS
version_added: "2.0.0"
description:
  - Create, update, and delete tag categories in VergeOS.
  - Tag categories organize tags and define which resource types can be tagged.
  - A category must have at least one taggable resource type enabled.
options:
  name:
    description:
      - The name of the tag category.
      - Must be unique within the VergeOS system.
    type: str
    required: true
  state:
    description:
      - The desired state of the tag category.
      - When C(absent), the category will be deleted.
      - Note that a category cannot be deleted if it contains tags.
    type: str
    choices: [ present, absent ]
    default: present
  description:
    description:
      - Description of the tag category.
    type: str
  single_tag_selection:
    description:
      - If true, only one tag from this category can be applied to a resource.
      - Useful for mutually exclusive tags like environments (dev/staging/prod).
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_vms:
    description:
      - Allow tags in this category to be applied to virtual machines.
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_networks:
    description:
      - Allow tags in this category to be applied to networks (vnets).
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_volumes:
    description:
      - Allow tags in this category to be applied to volumes.
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_network_rules:
    description:
      - Allow tags in this category to be applied to network rules.
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_vmware_containers:
    description:
      - Allow tags in this category to be applied to VMware containers.
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_users:
    description:
      - Allow tags in this category to be applied to users.
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_tenant_nodes:
    description:
      - Allow tags in this category to be applied to tenant nodes.
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_sites:
    description:
      - Allow tags in this category to be applied to sites.
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_nodes:
    description:
      - Allow tags in this category to be applied to nodes.
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_groups:
    description:
      - Allow tags in this category to be applied to groups.
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_clusters:
    description:
      - Allow tags in this category to be applied to clusters.
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
  taggable_tenants:
    description:
      - Allow tags in this category to be applied to tenants.
      - Defaults to C(false) when creating. Omit to leave unchanged on update.
    type: bool
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Create a tag category for application types
  vergeio.vergeos.tag_category:
    name: "App"
    description: "Application type tags"
    taggable_vms: true
    state: present

- name: Create an environment category with single selection
  vergeio.vergeos.tag_category:
    name: "Environment"
    description: "Deployment environment (only one can be selected)"
    taggable_vms: true
    taggable_networks: true
    single_tag_selection: true
    state: present

- name: Create a category for multiple resource types
  vergeio.vergeos.tag_category:
    name: "CostCenter"
    description: "Cost center allocation"
    taggable_vms: true
    taggable_volumes: true
    taggable_networks: true
    taggable_tenants: true
    state: present

- name: Update category to allow tagging clusters
  vergeio.vergeos.tag_category:
    name: "App"
    taggable_clusters: true
    state: present

- name: Delete a tag category
  vergeio.vergeos.tag_category:
    name: "OldCategory"
    state: absent
'''

RETURN = r'''
category:
  description: Information about the tag category
  returned: when state is present
  type: dict
  sample:
    key: 1
    name: "App"
    description: "Application type tags"
    single_tag_selection: false
    taggable_vms: true
    taggable_networks: false
    taggable_volumes: false
    taggable_tenants: false
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


# Omitted on update, false on create. These must not carry an argument-spec
# default: Ansible would pass False for every flag a task leaves out, and a
# description-only edit would turn tagging off (#121).
CATEGORY_FLAGS = (
    'single_tag_selection',
    'taggable_vms',
    'taggable_networks',
    'taggable_volumes',
    'taggable_network_rules',
    'taggable_vmware_containers',
    'taggable_users',
    'taggable_tenant_nodes',
    'taggable_sites',
    'taggable_nodes',
    'taggable_groups',
    'taggable_clusters',
    'taggable_tenants',
)


def _false_when_omitted(params, name):
    """Create-time default for a flag.

    An omitted option is present and None. ``dict.get(name, False)`` does
    not substitute in that case, because the key exists.
    """
    value = params.get(name)
    if value is None:
        return False
    return value


def get_category(module, client, name):
    """Get tag category by name using SDK"""
    try:
        return resolve_one(module, client.tag_categories, name, 'tag category')
    except NotFoundError:
        return None


def category_to_dict(category):
    """Convert category object to a clean dictionary for output"""
    return {
        'key': category.key,
        'name': category.name,
        'description': category.description,
        'single_tag_selection': category.is_single_tag_selection,
        'taggable_vms': category.taggable_vms,
        'taggable_networks': category.taggable_networks,
        'taggable_volumes': category.taggable_volumes,
        'taggable_network_rules': category.taggable_network_rules,
        'taggable_vmware_containers': category.taggable_vmware_containers,
        'taggable_users': category.taggable_users,
        'taggable_tenant_nodes': category.taggable_tenant_nodes,
        'taggable_sites': category.taggable_sites,
        'taggable_nodes': category.taggable_nodes,
        'taggable_groups': category.taggable_groups,
        'taggable_clusters': category.taggable_clusters,
        'taggable_tenants': category.taggable_tenants,
    }


def create_category(module, client):
    """Create a new tag category using SDK"""
    params = module.params
    flags = {name: _false_when_omitted(params, name) for name in CATEGORY_FLAGS}

    if module.check_mode:
        preview = {
            'name': params['name'],
            'description': params.get('description'),
        }
        preview.update(flags)
        return True, preview

    category = client.tag_categories.create(
        name=params['name'],
        description=params.get('description'),
        **flags,
    )
    return True, category_to_dict(category)


def update_category(module, client, category):
    """Update an existing tag category using SDK"""
    params = module.params
    changed = False
    update_kwargs = {}

    current = category_to_dict(category)

    # Check each field for changes
    if params.get('description') is not None:
        if current['description'] != params['description']:
            update_kwargs['description'] = params['description']
            changed = True

    for field in CATEGORY_FLAGS:
        if params.get(field) is not None:
            if current[field] != params[field]:
                update_kwargs[field] = params[field]
                changed = True

    if not changed:
        return False, current

    if module.check_mode:
        current.update(update_kwargs)
        return True, current

    updated = client.tag_categories.update(category.key, **update_kwargs)
    return True, category_to_dict(updated)


def delete_category(module, client, category):
    """Delete a tag category using SDK"""
    if module.check_mode:
        return True

    category.delete()
    return True


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present', choices=['present', 'absent']),
        description=dict(type='str'),
        single_tag_selection=dict(type='bool'),
        taggable_vms=dict(type='bool'),
        taggable_networks=dict(type='bool'),
        taggable_volumes=dict(type='bool'),
        taggable_network_rules=dict(type='bool'),
        taggable_vmware_containers=dict(type='bool'),
        taggable_users=dict(type='bool'),
        taggable_tenant_nodes=dict(type='bool'),
        taggable_sites=dict(type='bool'),
        taggable_nodes=dict(type='bool'),
        taggable_groups=dict(type='bool'),
        taggable_clusters=dict(type='bool'),
        taggable_tenants=dict(type='bool'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    category_name = module.params['name']
    state = module.params['state']

    client = get_vergeos_client(module)

    try:
        category = get_category(module, client, category_name)

        if state == 'absent':
            if category:
                delete_category(module, client, category)
                module.exit_json(changed=True, msg=f"Tag category '{category_name}' deleted")
            else:
                module.exit_json(changed=False, msg=f"Tag category '{category_name}' does not exist")

        elif state == 'present':
            if category:
                changed, updated_category = update_category(module, client, category)
                module.exit_json(changed=changed, category=updated_category)
            else:
                changed, new_category = create_category(module, client)
                module.exit_json(changed=changed, category=new_category)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
