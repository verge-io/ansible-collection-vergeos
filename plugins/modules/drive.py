#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: drive
short_description: Manage storage drives for VMs in VergeOS
version_added: "1.0.0"
description:
  - Create, update, and delete storage drives for virtual machines in VergeOS.
options:
  vm_name:
    description:
      - The name of the virtual machine to attach the drive to.
    type: str
    required: true
  name:
    description:
      - The name of the drive.
    type: str
    required: true
  state:
    description:
      - The desired state of the drive.
    type: str
    choices: [ present, absent ]
    default: present
  size:
    description:
      - Size of the drive in GB.
    type: int
  drive_type:
    description:
      - Type of storage drive.
    type: str
    choices: [ virtio, ide, sata, scsi ]
    default: virtio
  media_type:
    description:
      - Media type for the drive.
    type: str
    choices: [ disk, cdrom ]
    default: disk
  tier:
    description:
      - Storage tier for the drive.
    type: int
  read_only:
    description:
      - Whether the drive is read-only.
    type: bool
    default: false
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO
'''

EXAMPLES = r'''
- name: Add a 100GB drive to a VM
  vergeio.vergeos.drive:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    vm_name: "web-server-01"
    name: "data-disk"
    size: 100
    drive_type: virtio
    state: present

- name: Add a CD-ROM drive
  vergeio.vergeos.drive:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    vm_name: "web-server-01"
    name: "cdrom"
    media_type: cdrom
    drive_type: ide
    state: present

- name: Remove a drive
  vergeio.vergeos.drive:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    vm_name: "web-server-01"
    name: "old-disk"
    state: absent
'''

RETURN = r'''
drive:
  description: Information about the drive
  returned: when state is present
  type: dict
  sample:
    name: "data-disk"
    size: 100
    drive_type: "virtio"
    media_type: "disk"
    id: "12345"
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    VergeOSAPI,
    VergeOSAPIError,
    vergeos_argument_spec
)


def get_vm_key(api, vm_name):
    """Get VM key by name"""
    try:
        vms = api.get('vms')
        for vm in vms:
            if vm.get('name') == vm_name:
                return vm.get('$key')
        return None
    except VergeOSAPIError:
        return None


def get_drive(api, machine_key, drive_name):
    """Get drive by name"""
    try:
        # Get all drives for this machine
        drives = api.get('machine_drives')
        for drive in drives:
            if drive.get('machine') == machine_key and drive.get('name') == drive_name:
                return drive
        return None
    except VergeOSAPIError:
        return None


def create_drive(module, api, machine_key):
    """Create a new drive"""
    # Map our friendly parameter names to API field names
    drive_data = {
        'machine': machine_key,
        'name': module.params['name'],
        'interface': module.params.get('drive_type', 'virtio'),
        'media': module.params.get('media_type', 'disk'),
        'readonly': module.params.get('read_only', False),
    }

    if module.params.get('size'):
        # Convert GB to bytes (API expects disksize in bytes)
        drive_data['disksize'] = module.params['size'] * 1024 * 1024 * 1024
    if module.params.get('tier') is not None:
        drive_data['preferred_tier'] = module.params['tier']

    if module.check_mode:
        return True, drive_data

    try:
        result = api.post('machine_drives', drive_data)
        return True, result
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to create drive: {str(e)}")


def update_drive(module, api, drive):
    """Update an existing drive"""
    changed = False
    update_data = {}

    # Map parameter names to API fields
    param_mapping = {
        'drive_type': 'interface',
        'tier': 'preferred_tier',
        'read_only': 'readonly'
    }

    for param, api_field in param_mapping.items():
        if module.params.get(param) is not None:
            if drive.get(api_field) != module.params[param]:
                update_data[api_field] = module.params[param]
                changed = True

    # Handle size separately (needs conversion to bytes)
    if module.params.get('size'):
        size_bytes = module.params['size'] * 1024 * 1024 * 1024
        if drive.get('disksize') != size_bytes:
            update_data['disksize'] = size_bytes
            changed = True

    if not changed:
        return False, drive

    if module.check_mode:
        drive.update(update_data)
        return True, drive

    try:
        drive_key = drive.get('$key')
        result = api.put(f'machine_drives/{drive_key}', update_data)
        return True, result
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to update drive: {str(e)}")


def delete_drive(module, api, drive):
    """Delete a drive"""
    if module.check_mode:
        return True

    try:
        drive_key = drive.get('$key')
        api.delete(f'machine_drives/{drive_key}')
        return True
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to delete drive: {str(e)}")


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        vm_name=dict(type='str', required=True),
        name=dict(type='str', required=True),
        state=dict(type='str', default='present', choices=['present', 'absent']),
        size=dict(type='int'),
        drive_type=dict(type='str', default='virtio', choices=['virtio', 'ide', 'sata', 'scsi']),
        media_type=dict(type='str', default='disk', choices=['disk', 'cdrom']),
        tier=dict(type='int'),
        read_only=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    api = VergeOSAPI(module)
    vm_name = module.params['vm_name']
    drive_name = module.params['name']
    state = module.params['state']

    try:
        # Get VM key
        machine_key = get_vm_key(api, vm_name)
        if not machine_key:
            module.fail_json(msg=f"VM '{vm_name}' not found")

        # Get existing drive
        drive = get_drive(api, machine_key, drive_name)

        if state == 'absent':
            if drive:
                delete_drive(module, api, drive)
                module.exit_json(changed=True, msg=f"Drive '{drive_name}' removed")
            else:
                module.exit_json(changed=False, msg="Drive does not exist")

        elif state == 'present':
            if drive:
                changed, updated_drive = update_drive(module, api, drive)
                module.exit_json(changed=changed, drive=updated_drive)
            else:
                changed, new_drive = create_drive(module, api, machine_key)
                module.exit_json(changed=changed, drive=new_drive)

    except VergeOSAPIError as e:
        module.fail_json(msg=str(e))
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
