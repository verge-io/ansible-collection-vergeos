#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: nic
short_description: Manage network interfaces for VMs in VergeOS
version_added: "1.0.0"
description:
  - Create, update, and delete network interface cards (NICs) for virtual machines in VergeOS.
  - NICs connect VMs to VergeOS virtual networks (vnets).
  - Each VM has a machine key that is used for hardware operations like NIC management.
notes:
  - VMs imported from OVA may have default NICs that need to be updated to attach to the correct network.
  - Use this module to ensure NICs are attached to the correct VergeOS virtual network.
options:
  vm_name:
    description:
      - The name of the virtual machine to attach the NIC to.
    type: str
    required: true
  network:
    description:
      - The name of the network to attach the NIC to.
    type: str
    required: true
  state:
    description:
      - The desired state of the NIC.
    type: str
    choices: [ present, absent ]
    default: present
  mac_address:
    description:
      - MAC address for the NIC. If not specified, one will be auto-generated.
    type: str
  enabled:
    description:
      - Whether the NIC is enabled.
    type: bool
    default: true
  nic_type:
    description:
      - Type of network interface.
    type: str
    choices: [ virtio, e1000, rtl8139 ]
    default: virtio
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO
'''

EXAMPLES = r'''
- name: Add a NIC to a VM
  vergeio.vergeos.nic:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    vm_name: "web-server-01"
    network: "internal-network"
    state: present
    nic_type: virtio

- name: Add a NIC with specific MAC address
  vergeio.vergeos.nic:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    vm_name: "web-server-01"
    network: "dmz-network"
    mac_address: "52:54:00:12:34:56"
    state: present

- name: Remove a NIC from a VM
  vergeio.vergeos.nic:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    vm_name: "web-server-01"
    network: "old-network"
    state: absent
'''

RETURN = r'''
nic:
  description: Information about the network interface
  returned: when state is present
  type: dict
  sample:
    vm_name: "web-server-01"
    network: "internal-network"
    mac_address: "52:54:00:12:34:56"
    enabled: true
    nic_type: "virtio"
    id: "12345"
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    VergeOSAPI,
    VergeOSAPIError,
    vergeos_argument_spec
)


def get_vm_and_machine_keys(api, vm_name):
    """Get VM key and machine key by VM name

    Returns: (vm_key, machine_key) tuple
    """
    try:
        # Get all VMs and filter by name (API doesn't support ?name= filter)
        vms = api.get('vms')
        matching_vms = [vm for vm in vms if vm.get('name') == vm_name]
        if not matching_vms:
            return None, None

        vm = matching_vms[0]
        vm_key = vm.get('$key')
        machine_key = vm.get('machine')

        return vm_key, machine_key
    except VergeOSAPIError:
        return None, None


def get_vnet_key(api, network_name):
    """Get vnet key by name"""
    try:
        vnets = api.get('vnets')
        for vnet in vnets:
            if vnet.get('name') == network_name:
                return vnet.get('$key')
        return None
    except VergeOSAPIError:
        return None


def get_nic(api, machine_key, vnet_key):
    """Get NIC by machine and vnet

    Returns the NIC if it exists with the correct vnet, or the first NIC
    for the machine if it needs to be updated.
    """
    try:
        # Get all NICs for this machine
        nics = api.get('machine_nics')
        if not isinstance(nics, list):
            nics = [nics] if nics else []

        machine_nics = [nic for nic in nics if nic.get('machine') == machine_key]

        # First, check if any NIC is already on the target vnet
        for nic in machine_nics:
            if nic.get('vnet') == vnet_key:
                return nic

        # If no NIC is on the target vnet, return the first NIC for this machine
        # (we'll update it to use the target vnet)
        if machine_nics:
            return machine_nics[0]

        # No NICs exist for this machine
        return None
    except VergeOSAPIError:
        return None


def create_nic(module, api, machine_key, vnet_key):
    """Create a new NIC"""
    # Map our friendly parameter names to API field names
    nic_data = {
        'machine': machine_key,
        'vnet': vnet_key,
        'enabled': module.params.get('enabled', True),
        'interface': module.params.get('nic_type', 'virtio-net-pci'),
    }

    if module.params.get('mac_address'):
        nic_data['macaddress'] = module.params['mac_address']

    if module.check_mode:
        return True, nic_data

    try:
        result = api.post('machine_nics', nic_data)
        return True, result
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to create NIC: {str(e)}")


def update_nic(module, api, nic, target_vnet_key):
    """Update an existing NIC"""
    changed = False
    update_data = {}

    # Check if vnet needs to be updated
    if nic.get('vnet') != target_vnet_key:
        update_data['vnet'] = target_vnet_key
        changed = True

    # Map parameter names to API fields
    param_mapping = {
        'enabled': 'enabled',
        'nic_type': 'interface',
        'mac_address': 'macaddress'
    }

    for param, api_field in param_mapping.items():
        if module.params.get(param) is not None:
            if nic.get(api_field) != module.params[param]:
                update_data[api_field] = module.params[param]
                changed = True

    if not changed:
        return False, nic

    if module.check_mode:
        nic.update(update_data)
        return True, nic

    try:
        nic_key = nic.get('$key')
        result = api.put(f'machine_nics/{nic_key}', update_data)
        return True, result
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to update NIC: {str(e)}")


def delete_nic(module, api, nic):
    """Delete a NIC"""
    if module.check_mode:
        return True

    try:
        nic_key = nic.get('$key')
        api.delete(f'machine_nics/{nic_key}')
        return True
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to delete NIC: {str(e)}")


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        vm_name=dict(type='str', required=True),
        network=dict(type='str', required=True),
        state=dict(type='str', default='present', choices=['present', 'absent']),
        mac_address=dict(type='str'),
        enabled=dict(type='bool', default=True),
        nic_type=dict(type='str', default='virtio', choices=['virtio', 'e1000', 'rtl8139']),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    api = VergeOSAPI(module)
    vm_name = module.params['vm_name']
    network_name = module.params['network']
    state = module.params['state']

    try:
        # Get VM and machine keys
        vm_key, machine_key = get_vm_and_machine_keys(api, vm_name)
        if not vm_key:
            module.fail_json(msg=f"VM '{vm_name}' not found")
        if not machine_key:
            module.fail_json(msg=f"VM '{vm_name}' has no machine key (may not be fully created yet)")

        vnet_key = get_vnet_key(api, network_name)
        if not vnet_key:
            module.fail_json(msg=f"Network '{network_name}' not found")

        # Get existing NIC
        nic = get_nic(api, machine_key, vnet_key)

        if state == 'absent':
            if nic:
                delete_nic(module, api, nic)
                module.exit_json(changed=True, msg=f"NIC removed from VM '{vm_name}'")
            else:
                module.exit_json(changed=False, msg="NIC does not exist")

        elif state == 'present':
            if nic:
                # Update existing NIC (may need to change vnet)
                changed, updated_nic = update_nic(module, api, nic, vnet_key)
                module.exit_json(changed=changed, nic=updated_nic)
            else:
                # No NIC exists, create one
                changed, new_nic = create_nic(module, api, machine_key, vnet_key)
                module.exit_json(changed=changed, nic=new_nic)

    except VergeOSAPIError as e:
        module.fail_json(msg=str(e))
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
