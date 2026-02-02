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


def get_vm(client, vm_name):
    """Get VM by name using SDK"""
    try:
        return client.vms.get(name=vm_name)
    except NotFoundError:
        return None


def get_network(client, network_name):
    """Get network by name using SDK"""
    try:
        return client.networks.get(name=network_name)
    except NotFoundError:
        return None


def get_nic(client, vm, target_network):
    """Get NIC by VM and network using SDK

    Returns the NIC if it exists with the correct network, or the first NIC
    for the VM if it needs to be updated.
    """
    try:
        nics = list(vm.nics.list())
        target_network_key = dict(target_network).get('$key')

        # First, check if any NIC is already on the target network
        for nic in nics:
            nic_dict = dict(nic)
            if nic_dict.get('vnet') == target_network_key:
                return nic

        # If no NIC is on the target network, return the first NIC for this VM
        # (we'll update it to use the target network)
        if nics:
            return nics[0]

        # No NICs exist for this VM
        return None
    except (NotFoundError, AttributeError):
        return None


def create_nic(module, client, vm, network):
    """Create a new NIC using SDK"""
    network_key = dict(network).get('$key')

    # Map our friendly parameter names to API field names
    nic_data = {
        'vnet': network_key,
        'enabled': module.params.get('enabled', True),
        'interface': module.params.get('nic_type', 'virtio-net-pci'),
    }

    if module.params.get('mac_address'):
        nic_data['macaddress'] = module.params['mac_address']

    if module.check_mode:
        return True, nic_data

    nic = vm.nics.create(**nic_data)
    return True, dict(nic)


def update_nic(module, client, nic, target_network):
    """Update an existing NIC using SDK"""
    changed = False
    update_data = {}

    nic_dict = dict(nic)
    target_network_key = dict(target_network).get('$key')

    # Check if vnet needs to be updated
    if nic_dict.get('vnet') != target_network_key:
        update_data['vnet'] = target_network_key
        changed = True

    # Map parameter names to API fields
    param_mapping = {
        'enabled': 'enabled',
        'nic_type': 'interface',
        'mac_address': 'macaddress'
    }

    for param, api_field in param_mapping.items():
        if module.params.get(param) is not None:
            if nic_dict.get(api_field) != module.params[param]:
                update_data[api_field] = module.params[param]
                changed = True

    if not changed:
        return False, nic_dict

    if module.check_mode:
        nic_dict.update(update_data)
        return True, nic_dict

    # Update NIC attributes and save
    for key, value in update_data.items():
        setattr(nic, key, value)
    nic.save()
    return True, dict(nic)


def delete_nic(module, client, nic):
    """Delete a NIC using SDK"""
    if module.check_mode:
        return True

    nic.delete()
    return True


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

    client = get_vergeos_client(module)
    vm_name = module.params['vm_name']
    network_name = module.params['network']
    state = module.params['state']

    try:
        # Get VM
        vm = get_vm(client, vm_name)
        if not vm:
            module.fail_json(msg=f"VM '{vm_name}' not found")

        vm_dict = dict(vm)
        if not vm_dict.get('machine'):
            module.fail_json(msg=f"VM '{vm_name}' has no machine key (may not be fully created yet)")

        # Get network
        network = get_network(client, network_name)
        if not network:
            module.fail_json(msg=f"Network '{network_name}' not found")

        # Get existing NIC
        nic = get_nic(client, vm, network)

        if state == 'absent':
            if nic:
                delete_nic(module, client, nic)
                module.exit_json(changed=True, msg=f"NIC removed from VM '{vm_name}'")
            else:
                module.exit_json(changed=False, msg="NIC does not exist")

        elif state == 'present':
            if nic:
                # Update existing NIC (may need to change network)
                changed, updated_nic = update_nic(module, client, nic, network)
                module.exit_json(changed=changed, nic=updated_nic)
            else:
                # No NIC exists, create one
                changed, new_nic = create_nic(module, client, vm, network)
                module.exit_json(changed=changed, nic=new_nic)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
