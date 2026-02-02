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


def get_vm(client, name):
    """Get VM by name using SDK"""
    try:
        return client.vms.get(name=name)
    except NotFoundError:
        return None


def build_vm_data(module):
    """Build VM data dict from module params"""
    vm_data = {
        'name': module.params['name'],
    }

    optional_fields = [
        'description', 'enabled', 'os_family', 'cpu_cores',
        'ram', 'machine_type', 'machine_subtype', 'bios_type',
        'network', 'boot_order'
    ]

    for field in optional_fields:
        if module.params.get(field) is not None:
            vm_data[field] = module.params[field]

    return vm_data


def create_vm(module, client):
    """Create a new VM using SDK"""
    vm_data = build_vm_data(module)

    if module.check_mode:
        return True, vm_data

    vm = client.vms.create(**vm_data)
    return True, dict(vm)


def update_vm(module, client, vm):
    """Update an existing VM using SDK"""
    changed = False
    update_data = {}

    # Check which fields need updating
    fields_to_check = ['description', 'enabled', 'os_family', 'cpu_cores',
                      'ram', 'machine_type', 'machine_subtype', 'bios_type',
                      'network', 'boot_order']

    vm_dict = dict(vm)
    for field in fields_to_check:
        if module.params.get(field) is not None:
            if vm_dict.get(field) != module.params[field]:
                update_data[field] = module.params[field]
                changed = True

    if not changed:
        return False, vm_dict

    if module.check_mode:
        vm_dict.update(update_data)
        return True, vm_dict

    # Update VM attributes and save
    for key, value in update_data.items():
        setattr(vm, key, value)
    vm.save()
    return True, dict(vm)


def delete_vm(module, client, vm):
    """Delete a VM using SDK"""
    if module.check_mode:
        return True

    vm.delete()
    return True


def power_on_vm(module, client, vm):
    """Power on a VM using SDK"""
    vm_dict = dict(vm)
    if vm_dict.get('power_state') == 'running':
        return False, vm_dict

    if module.check_mode:
        vm_dict['power_state'] = 'running'
        return True, vm_dict

    vm.power_on()
    return True, dict(vm)


def power_off_vm(module, client, vm):
    """Power off a VM using SDK"""
    vm_dict = dict(vm)
    if vm_dict.get('power_state') == 'stopped':
        return False, vm_dict

    if module.check_mode:
        vm_dict['power_state'] = 'stopped'
        return True, vm_dict

    vm.power_off()
    return True, dict(vm)


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

    client = get_vergeos_client(module)
    name = module.params['name']
    state = module.params['state']

    try:
        # Get existing VM
        vm = get_vm(client, name)

        if state == 'absent':
            if vm:
                delete_vm(module, client, vm)
                module.exit_json(changed=True, msg=f"VM '{name}' deleted")
            else:
                module.exit_json(changed=False, msg=f"VM '{name}' does not exist")

        elif state == 'present':
            if vm:
                # Update existing VM
                changed, updated_vm = update_vm(module, client, vm)
                module.exit_json(changed=changed, vm=updated_vm)
            else:
                # Create new VM
                changed, new_vm = create_vm(module, client)
                module.exit_json(changed=changed, vm=new_vm)

        elif state == 'running':
            if not vm:
                # Create VM first
                changed, vm_data = create_vm(module, client)
                # Re-fetch VM after creation (skip in check mode)
                if not module.check_mode:
                    vm = get_vm(client, name)
                else:
                    vm = None
            else:
                # Check if updates needed
                update_changed, updated_vm = update_vm(module, client, vm)
                changed = update_changed
                # Re-fetch VM after update to get fresh state (skip in check mode)
                if update_changed and not module.check_mode:
                    vm = get_vm(client, name)

            # Ensure VM is running (only if we have a VM object)
            if vm:
                power_changed, vm_dict = power_on_vm(module, client, vm)
                module.exit_json(changed=changed or power_changed, vm=vm_dict)
            else:
                # Check mode - no actual VM object
                module.exit_json(changed=True, msg=f"Would power on VM '{name}' (check mode)")

        elif state == 'stopped':
            if not vm:
                module.fail_json(msg=f"VM '{name}' does not exist")

            # Check if updates needed
            update_changed, updated_vm = update_vm(module, client, vm)
            # Re-fetch VM after update to get fresh state
            if update_changed and not module.check_mode:
                vm = get_vm(client, name)

            # Ensure VM is stopped
            power_changed, vm_dict = power_off_vm(module, client, vm)
            module.exit_json(changed=update_changed or power_changed, vm=vm_dict)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
