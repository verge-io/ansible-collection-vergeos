#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vm_import
short_description: Import virtual machines from OVA files in VergeOS
version_added: "1.0.0"
description:
  - Import virtual machines from OVA/OVF files into VergeOS.
  - This module handles the asynchronous import process and polls until completion.
  - The imported VM will be in a stopped state when the import completes.
options:
  ova_file_id:
    description:
      - The ID of the uploaded OVA file in VergeOS.
      - This file must be uploaded to VergeOS before calling this module.
      - Mutually exclusive with I(ova_file_name).
    type: str
  ova_file_name:
    description:
      - The name of the OVA file to import (e.g., C(rhel8.ova)).
      - The module will look up the file ID automatically.
      - Mutually exclusive with I(ova_file_id).
    type: str
  file_id:
    description:
      - Deprecated. Use I(ova_file_id) instead.
      - The ID of the uploaded OVA file in VergeOS.
    type: str
  name:
    description:
      - The name to assign to the imported virtual machine.
    type: str
    required: true
  preserve_macs:
    description:
      - Whether to preserve MAC addresses from the source VM.
    type: bool
    default: false
  preserve_drive_format:
    description:
      - Whether to preserve the original drive format.
    type: bool
    default: false
  preferred_tier:
    description:
      - The preferred storage tier for the imported VM.
    type: str
  no_optical_drives:
    description:
      - Whether to exclude optical drives during import.
    type: bool
    default: false
  override_drive_interface:
    description:
      - Override the drive interface type.
    type: str
    choices: [ default, virtio, ide, sata, scsi ]
    default: default
  override_nic_interface:
    description:
      - Override the network interface type.
    type: str
    choices: [ default, virtio, e1000, rtl8139 ]
    default: default
  poll_interval:
    description:
      - Number of seconds to wait between status polls.
    type: int
    default: 5
  poll_timeout:
    description:
      - Maximum time in seconds to wait for import to complete.
    type: int
    default: 600
  state:
    description:
      - The desired state of the import.
      - C(present) starts the import and waits for completion.
      - C(absent) removes the import record (VM will remain if import completed).
    type: str
    choices: [ present, absent ]
    default: present
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Import a VM from an OVA file by name
  vergeio.vergeos.vm_import:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    ova_file_name: "rhel8.ova"
    name: "imported-vm-01"
    preserve_macs: false
    preferred_tier: "4"
    override_drive_interface: virtio
    override_nic_interface: virtio

- name: Import a VM from an OVA file by ID
  vergeio.vergeos.vm_import:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    ova_file_id: "41"
    name: "imported-vm-02"
    preserve_macs: false
    preferred_tier: "4"
    override_drive_interface: virtio
    override_nic_interface: virtio

- name: Import VM with custom timeout
  vergeio.vergeos.vm_import:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    file_id: "42"
    name: "imported-vm-02"
    poll_interval: 10
    poll_timeout: 1800

- name: Remove import record
  vergeio.vergeos.vm_import:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    file_id: "41"
    name: "imported-vm-01"
    state: absent
'''

RETURN = r'''
import_key:
  description: The key/ID of the VM import record
  returned: when state is present
  type: str
  sample: "584a61c1f3e28aed114a3a30531a7703fb7959e0"
vm_id:
  description: The ID of the created/imported VM
  returned: when state is present and import completes
  type: str
  sample: "46"
vm_name:
  description: The name of the imported VM
  returned: when state is present
  type: str
  sample: "imported-vm-01"
status:
  description: Final status of the import
  returned: when state is present
  type: str
  sample: "stopped"
import_info:
  description: Full import record information
  returned: when state is present
  type: dict
'''

import time
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


def wait_for_import_completion(client, import_key, poll_interval, poll_timeout, module):
    """
    Poll the import status until it completes or times out.

    Uses client._request() directly for raw API responses matching the
    exact behavior of the original REST-based implementation.

    Returns the final import status dict when complete.
    """
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed > poll_timeout:
            module.fail_json(
                msg=f"Import timeout after {poll_timeout} seconds",
                import_key=import_key
            )

        # Query import status via raw API call
        try:
            import_status = client._request('GET', f'vm_imports/{import_key}')
        except Exception as e:
            module.fail_json(
                msg=f"Failed to query import status: {str(e)}",
                import_key=import_key
            )

        # Check import status
        import_status_val = import_status.get('status', '')

        # Check VM status
        vm_info = import_status.get('vm', {})
        # vm might be an int (ID), dict with status, or null
        if isinstance(vm_info, dict):
            vm_status = vm_info.get('status', '')
        else:
            vm_status = ''

        # Debug: log what we're seeing (only on first few iterations to avoid spam)
        if elapsed < 30:
            module.warn(f"Polling: import_status='{import_status_val}', vm_info={vm_info}, vm_status='{vm_status}'")

        # Import is complete when:
        # 1. VM status changes from "importing" to "stopped", OR
        # 2. Import status is "complete" (vm field may be null at this point)
        if vm_status == 'stopped' or import_status_val == 'complete':
            return import_status

        # Check for failures
        if import_status.get('aborted', False):
            module.fail_json(
                msg="Import was aborted",
                import_key=import_key,
                import_info=import_status
            )

        failed_drives = import_status.get('failed_drive_count', 0)
        if failed_drives > 0:
            module.warn(f"Import has {failed_drives} failed drive(s)")

        # Wait before next poll
        time.sleep(poll_interval)


def get_file_by_name(client, file_name):
    """Look up file by name using SDK."""
    try:
        return client.files.get(name=file_name)
    except NotFoundError:
        return None


def create_vm_import(client, module):
    """Create a new VM import and wait for completion using SDK."""
    # Determine file_id from ova_file_id, ova_file_name, or deprecated file_id
    ova_file_id = module.params.get('ova_file_id')
    ova_file_name = module.params.get('ova_file_name')
    deprecated_file_id = module.params.get('file_id')

    # Determine which file identifier to use
    if ova_file_id:
        file_id = ova_file_id
    elif ova_file_name:
        file_obj = get_file_by_name(client, ova_file_name)
        if not file_obj:
            module.fail_json(msg=f"OVA file '{ova_file_name}' not found in VergeOS files")
        file_id = str(dict(file_obj).get('$key'))
    elif deprecated_file_id:
        file_id = deprecated_file_id
        module.warn("Parameter 'file_id' is deprecated, use 'ova_file_id' instead")
    else:
        module.fail_json(msg="Either 'ova_file_id' or 'ova_file_name' must be specified")

    name = module.params['name']

    # Build import payload - use raw API format matching the VergeOS REST API.
    # The 'importing' field tells the API to start importing immediately.
    payload = {
        'file': file_id,
        'name': name,
        'preserve_macs': str(module.params['preserve_macs']).lower(),
        'importing': 'true'
    }

    # Add optional parameters
    if module.params['preserve_drive_format']:
        payload['preserve_drive_format'] = str(module.params['preserve_drive_format']).lower()

    if module.params['preferred_tier']:
        payload['preferred_tier'] = module.params['preferred_tier']

    if module.params['no_optical_drives']:
        payload['no_optical_drives'] = str(module.params['no_optical_drives']).lower()

    if module.params['override_drive_interface'] != 'default':
        payload['override_drive_interface'] = module.params['override_drive_interface']

    if module.params['override_nic_interface'] != 'default':
        payload['override_nic_interface'] = module.params['override_nic_interface']

    if module.check_mode:
        module.exit_json(
            changed=True,
            msg="Would create VM import (check mode)",
            payload=payload
        )

    # Create the import via raw API call (SDK create() doesn't support
    # the 'importing' field that auto-starts the import process)
    try:
        response = client._request('POST', 'vm_imports', json_data=payload)
    except Exception as e:
        module.fail_json(msg=f"Failed to create VM import: {str(e)}")

    # Extract import key
    import_key = response.get('$key')
    vm_response = response.get('response', {})
    vm_id = vm_response.get('$key') if isinstance(vm_response, dict) else None

    if not import_key:
        module.fail_json(
            msg="Import created but no $key returned",
            response=response
        )

    # Wait for import to complete
    final_status = wait_for_import_completion(
        client,
        import_key,
        module.params['poll_interval'],
        module.params['poll_timeout'],
        module
    )

    # Extract VM ID and status from final_status
    vm_info = final_status.get('vm', {})
    if isinstance(vm_info, dict):
        vm_status = vm_info.get('status', 'stopped')
        if not vm_id:
            vm_id = vm_info.get('$key')
    elif isinstance(vm_info, int):
        vm_id = vm_info
        vm_status = 'stopped'  # Assume stopped after import
    else:
        vm_status = 'unknown'

    result = {
        'changed': True,
        'import_key': import_key,
        'vm_name': name,
        'status': vm_status,
        'import_info': final_status
    }

    if vm_id:
        result['vm_id'] = str(vm_id)

    module.exit_json(**result)


def delete_vm_import(client, module):
    """Delete a VM import record using SDK."""
    # We need to find the import by name since we don't have the key
    name = module.params['name']

    try:
        import_obj = client.vm_imports.get(name=name)
    except NotFoundError:
        module.exit_json(changed=False, msg="Import not found")

    import_key = str(dict(import_obj).get('$key'))

    if module.check_mode:
        module.exit_json(changed=True, msg="Would delete import (check mode)")

    # Delete the import
    import_obj.delete()
    module.exit_json(changed=True, msg="Import deleted", import_key=import_key)


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        ova_file_id=dict(type='str'),
        ova_file_name=dict(type='str'),
        file_id=dict(type='str'),  # Deprecated, kept for backward compatibility
        name=dict(type='str', required=True),
        preserve_macs=dict(type='bool', default=False),
        preserve_drive_format=dict(type='bool', default=False),
        preferred_tier=dict(type='str'),
        no_optical_drives=dict(type='bool', default=False),
        override_drive_interface=dict(
            type='str',
            choices=['default', 'virtio', 'ide', 'sata', 'scsi'],
            default='default'
        ),
        override_nic_interface=dict(
            type='str',
            choices=['default', 'virtio', 'e1000', 'rtl8139'],
            default='default'
        ),
        poll_interval=dict(type='int', default=5),
        poll_timeout=dict(type='int', default=600),
        state=dict(type='str', choices=['present', 'absent'], default='present'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        mutually_exclusive=[
            ('ova_file_id', 'ova_file_name'),
        ],
        required_one_of=[
            ('ova_file_id', 'ova_file_name', 'file_id'),
        ],
    )

    client = get_vergeos_client(module)

    try:
        if module.params['state'] == 'present':
            create_vm_import(client, module)
        else:
            delete_vm_import(client, module)
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
