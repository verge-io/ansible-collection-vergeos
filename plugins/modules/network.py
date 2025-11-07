#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: network
short_description: Manage networks in VergeOS
version_added: "1.0.0"
description:
  - Create, update, and delete networks in VergeOS.
  - Manage network configuration including IP ranges, VLANs, and network settings.
options:
  name:
    description:
      - The name of the network.
    type: str
    required: true
  state:
    description:
      - The desired state of the network.
      - C(present) ensures the network exists with the specified configuration.
      - C(absent) ensures the network is deleted.
    type: str
    choices: [ present, absent ]
    default: present
  description:
    description:
      - Description of the network.
    type: str
  network_type:
    description:
      - Type of network to create.
    type: str
    choices: [ internal, external, vlan, overlay ]
  ip_address:
    description:
      - IP address for the network.
    type: str
  subnet_mask:
    description:
      - Subnet mask for the network.
    type: str
  gateway:
    description:
      - Default gateway for the network.
    type: str
  dhcp_enabled:
    description:
      - Whether DHCP is enabled on this network.
    type: bool
  dhcp_start:
    description:
      - Start of DHCP range.
    type: str
  dhcp_end:
    description:
      - End of DHCP range.
    type: str
  vlan_id:
    description:
      - VLAN ID for the network (if network_type is vlan).
    type: int
  dns_servers:
    description:
      - List of DNS servers for the network.
    type: list
    elements: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO
'''

EXAMPLES = r'''
- name: Create an internal network
  vergeio.vergeos.network:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "internal-network"
    description: "Internal production network"
    state: present
    network_type: internal
    ip_address: "10.0.0.0"
    subnet_mask: "255.255.255.0"
    gateway: "10.0.0.1"
    dhcp_enabled: true
    dhcp_start: "10.0.0.100"
    dhcp_end: "10.0.0.200"
    dns_servers:
      - "8.8.8.8"
      - "8.8.4.4"

- name: Create a VLAN network
  vergeio.vergeos.network:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "vlan-100"
    state: present
    network_type: vlan
    vlan_id: 100
    ip_address: "192.168.100.0"
    subnet_mask: "255.255.255.0"

- name: Delete a network
  vergeio.vergeos.network:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "old-network"
    state: absent
'''

RETURN = r'''
network:
  description: Information about the network
  returned: when state is present
  type: dict
  sample:
    name: "internal-network"
    description: "Internal production network"
    network_type: "internal"
    ip_address: "10.0.0.0"
    subnet_mask: "255.255.255.0"
    gateway: "10.0.0.1"
    dhcp_enabled: true
    id: "12345"
changed:
  description: Whether the module made any changes
  returned: always
  type: bool
  sample: true
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    VergeOSAPI,
    VergeOSAPIError,
    vergeos_argument_spec
)


def get_network(api, name):
    """Get network by name"""
    try:
        networks = api.get('vnets')
        for network in networks:
            if network.get('name') == name:
                return network
        return None
    except VergeOSAPIError:
        return None


def create_network(module, api):
    """Create a new network"""
    network_data = {
        'name': module.params['name'],
    }

    # Map our friendly parameter names to API fields
    param_mapping = {
        'description': 'description',
        'network_type': 'type',
        'ip_address': 'ip_address',
        'subnet_mask': 'subnet_mask',
        'gateway': 'gateway',
        'dhcp_enabled': 'dhcp_enabled',
        'dhcp_start': 'dhcp_start',
        'dhcp_end': 'dhcp_end',
        'vlan_id': 'layer2_id',
        'dns_servers': 'dns_servers'
    }

    for param, api_field in param_mapping.items():
        if module.params.get(param) is not None:
            network_data[api_field] = module.params[param]

    if module.check_mode:
        return True, network_data

    try:
        result = api.post('vnets', network_data)
        return True, result
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to create network: {str(e)}")


def update_network(module, api, network):
    """Update an existing network"""
    changed = False
    update_data = {}

    # Map our friendly parameter names to API fields
    param_mapping = {
        'description': 'description',
        'network_type': 'type',
        'ip_address': 'ip_address',
        'subnet_mask': 'subnet_mask',
        'gateway': 'gateway',
        'dhcp_enabled': 'dhcp_enabled',
        'dhcp_start': 'dhcp_start',
        'dhcp_end': 'dhcp_end',
        'vlan_id': 'layer2_id',
        'dns_servers': 'dns_servers'
    }

    for param, api_field in param_mapping.items():
        if module.params.get(param) is not None:
            if network.get(api_field) != module.params[param]:
                update_data[api_field] = module.params[param]
                changed = True

    if not changed:
        return False, network

    if module.check_mode:
        network.update(update_data)
        return True, network

    try:
        network_key = network.get('$key')
        result = api.put(f'vnets/{network_key}', update_data)
        return True, result
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to update network: {str(e)}")


def delete_network(module, api, network):
    """Delete a network"""
    if module.check_mode:
        return True

    try:
        network_key = network.get('$key')
        api.delete(f'vnets/{network_key}')
        return True
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to delete network: {str(e)}")


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present', choices=['present', 'absent']),
        description=dict(type='str'),
        network_type=dict(type='str', choices=['internal', 'external', 'vlan', 'overlay']),
        ip_address=dict(type='str'),
        subnet_mask=dict(type='str'),
        gateway=dict(type='str'),
        dhcp_enabled=dict(type='bool'),
        dhcp_start=dict(type='str'),
        dhcp_end=dict(type='str'),
        vlan_id=dict(type='int'),
        dns_servers=dict(type='list', elements='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    api = VergeOSAPI(module)
    name = module.params['name']
    state = module.params['state']

    try:
        network = get_network(api, name)

        if state == 'absent':
            if network:
                delete_network(module, api, network)
                module.exit_json(changed=True, msg=f"Network '{name}' deleted")
            else:
                module.exit_json(changed=False, msg=f"Network '{name}' does not exist")

        elif state == 'present':
            if network:
                changed, updated_network = update_network(module, api, network)
                module.exit_json(changed=changed, network=updated_network)
            else:
                changed, new_network = create_network(module, api)
                module.exit_json(changed=changed, network=new_network)

    except VergeOSAPIError as e:
        module.fail_json(msg=str(e))
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
