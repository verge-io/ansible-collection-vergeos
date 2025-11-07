#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vm
short_description: Manage virtual machines in VergeOS
version_added: "1.0.0"
description:
  - Create, update, power on/off, and delete virtual machines in VergeOS.
  - This module allows you to manage the complete lifecycle of VMs.
options:
  name:
    description:
      - The name of the virtual machine.
    type: str
    required: true
  state:
    description:
      - The desired state of the virtual machine.
      - C(present) ensures the VM exists with the specified configuration.
      - C(absent) ensures the VM is deleted.
      - C(running) ensures the VM exists and is powered on.
      - C(stopped) ensures the VM exists and is powered off.
    type: str
    choices: [ present, absent, running, stopped ]
    default: present
  description:
    description:
      - Description of the virtual machine.
    type: str
  enabled:
    description:
      - Whether the VM is enabled.
    type: bool
    default: true
  os_family:
    description:
      - The operating system family for the VM.
    type: str
    choices: [ linux, windows, other ]
  cpu_cores:
    description:
      - Number of CPU cores to assign to the VM.
    type: int
  ram:
    description:
      - Amount of RAM in MB to assign to the VM.
    type: int
  machine_type:
    description:
      - The machine type for the VM.
    type: str
    choices: [ pc, q35, virt ]
  machine_subtype:
    description:
      - The machine subtype/version.
    type: str
  bios_type:
    description:
      - BIOS type for the VM.
    type: str
    choices: [ seabios, uefi ]
  network:
    description:
      - Network configuration for the VM.
    type: str
  boot_order:
    description:
      - Boot order for the VM devices.
    type: list
    elements: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO
'''

EXAMPLES = r'''
- name: Create a new Linux VM
  vergeio.vergeos.vm:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "web-server-01"
    description: "Web server for production"
    state: present
    enabled: true
    os_family: linux
    cpu_cores: 4
    ram: 8192
    machine_type: q35

- name: Ensure VM is running
  vergeio.vergeos.vm:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "web-server-01"
    state: running

- name: Stop a VM
  vergeio.vergeos.vm:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "web-server-01"
    state: stopped

- name: Update VM configuration
  vergeio.vergeos.vm:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "web-server-01"
    cpu_cores: 8
    ram: 16384
    state: present

- name: Delete a VM
  vergeio.vergeos.vm:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "web-server-01"
    state: absent
'''

RETURN = r'''
vm:
  description: Information about the virtual machine
  returned: when state is present, running, or stopped
  type: dict
  sample:
    name: "web-server-01"
    description: "Web server for production"
    enabled: true
    os_family: "linux"
    cpu_cores: 4
    ram: 8192
    machine_type: "q35"
    power_state: "running"
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


def get_vm(api, name):
    """Get VM by name"""
    try:
        vms = api.get('vms')
        for vm in vms:
            if vm.get('name') == name:
                return vm
        return None
    except VergeOSAPIError as e:
        return None


def create_vm(module, api):
    """Create a new VM"""
    vm_data = {
        'name': module.params['name'],
    }

    if module.params.get('description'):
        vm_data['description'] = module.params['description']
    if module.params.get('enabled') is not None:
        vm_data['enabled'] = module.params['enabled']
    if module.params.get('os_family'):
        vm_data['os_family'] = module.params['os_family']
    if module.params.get('cpu_cores'):
        vm_data['cpu_cores'] = module.params['cpu_cores']
    if module.params.get('ram'):
        vm_data['ram'] = module.params['ram']
    if module.params.get('machine_type'):
        vm_data['machine_type'] = module.params['machine_type']
    if module.params.get('machine_subtype'):
        vm_data['machine_subtype'] = module.params['machine_subtype']
    if module.params.get('bios_type'):
        vm_data['bios_type'] = module.params['bios_type']
    if module.params.get('network'):
        vm_data['network'] = module.params['network']
    if module.params.get('boot_order'):
        vm_data['boot_order'] = module.params['boot_order']

    if module.check_mode:
        return True, vm_data

    try:
        result = api.post('vms', vm_data)
        return True, result
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to create VM: {str(e)}")


def update_vm(module, api, vm):
    """Update an existing VM"""
    changed = False
    update_data = {}

    # Check which fields need updating
    fields_to_check = ['description', 'enabled', 'os_family', 'cpu_cores',
                      'ram', 'machine_type', 'machine_subtype', 'bios_type',
                      'network', 'boot_order']

    for field in fields_to_check:
        if module.params.get(field) is not None:
            if vm.get(field) != module.params[field]:
                update_data[field] = module.params[field]
                changed = True

    if not changed:
        return False, vm

    if module.check_mode:
        vm.update(update_data)
        return True, vm

    try:
        vm_key = vm.get('$key')
        result = api.put(f'vms/{vm_key}', update_data)
        return True, result
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to update VM: {str(e)}")


def delete_vm(module, api, vm):
    """Delete a VM"""
    if module.check_mode:
        return True

    try:
        vm_key = vm.get('$key')
        api.delete(f'vms/{vm_key}')
        return True
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to delete VM: {str(e)}")


def power_on_vm(module, api, vm):
    """Power on a VM"""
    if vm.get('power_state') == 'running':
        return False, vm

    if module.check_mode:
        vm['power_state'] = 'running'
        return True, vm

    try:
        vm_key = vm.get('$key')
        action_data = {
            'vm': vm_key,
            'action': 'poweron'
        }
        result = api.post('vm_actions', action_data)
        return True, vm
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to power on VM: {str(e)}")


def power_off_vm(module, api, vm):
    """Power off a VM"""
    if vm.get('power_state') == 'stopped':
        return False, vm

    if module.check_mode:
        vm['power_state'] = 'stopped'
        return True, vm

    try:
        vm_key = vm.get('$key')
        action_data = {
            'vm': vm_key,
            'action': 'poweroff'
        }
        result = api.post('vm_actions', action_data)
        return True, vm
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to power off VM: {str(e)}")


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present',
                  choices=['present', 'absent', 'running', 'stopped']),
        description=dict(type='str'),
        enabled=dict(type='bool', default=True),
        os_family=dict(type='str', choices=['linux', 'windows', 'other']),
        cpu_cores=dict(type='int'),
        ram=dict(type='int'),
        machine_type=dict(type='str', choices=['pc', 'q35', 'virt']),
        machine_subtype=dict(type='str'),
        bios_type=dict(type='str', choices=['seabios', 'uefi']),
        network=dict(type='str'),
        boot_order=dict(type='list', elements='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    api = VergeOSAPI(module)
    name = module.params['name']
    state = module.params['state']

    try:
        # Get existing VM
        vm = get_vm(api, name)

        if state == 'absent':
            if vm:
                delete_vm(module, api, vm)
                module.exit_json(changed=True, msg=f"VM '{name}' deleted")
            else:
                module.exit_json(changed=False, msg=f"VM '{name}' does not exist")

        elif state == 'present':
            if vm:
                # Update existing VM
                changed, updated_vm = update_vm(module, api, vm)
                module.exit_json(changed=changed, vm=updated_vm)
            else:
                # Create new VM
                changed, new_vm = create_vm(module, api)
                module.exit_json(changed=changed, vm=new_vm)

        elif state == 'running':
            if not vm:
                # Create VM first
                changed, vm = create_vm(module, api)
                # Re-fetch VM after creation (skip in check mode)
                if not module.check_mode:
                    vm = get_vm(api, name)
            else:
                # Check if updates needed
                update_changed, updated_vm = update_vm(module, api, vm)
                changed = update_changed
                # Re-fetch VM after update to get fresh state (skip in check mode)
                if update_changed and not module.check_mode:
                    vm = get_vm(api, name)

            # Ensure VM is running (only if we have a VM object)
            if vm:
                power_changed, vm = power_on_vm(module, api, vm)
                module.exit_json(changed=changed or power_changed, vm=vm)
            else:
                # Check mode - no actual VM object
                module.exit_json(changed=True, msg=f"Would power on VM '{name}' (check mode)")

        elif state == 'stopped':
            if not vm:
                module.fail_json(msg=f"VM '{name}' does not exist")

            # Check if updates needed
            update_changed, updated_vm = update_vm(module, api, vm)
            # Re-fetch VM after update to get fresh state
            if update_changed:
                vm = get_vm(api, name)

            # Ensure VM is stopped
            power_changed, vm = power_off_vm(module, api, vm)
            module.exit_json(changed=update_changed or power_changed, vm=vm)

    except VergeOSAPIError as e:
        module.fail_json(msg=str(e))
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
