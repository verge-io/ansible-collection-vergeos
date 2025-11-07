#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: windows_unattend
short_description: Manage Windows unattend.xml configuration for VMs in VergeOS
version_added: "1.0.0"
description:
  - Configure Windows unattend.xml for virtual machines in VergeOS.
  - Creates or updates /unattend.xml file for Windows sysprep automation.
  - Used for configuring hostname, network, and administrator password on first boot.
  - The unattend.xml file is automatically applied during Windows OOBE (Out of Box Experience).
  - Automatically enables cloudinit_datasource on the VM to activate the file delivery mechanism.
options:
  vm_name:
    description:
      - The name of the virtual machine to configure unattend.xml for.
      - Mutually exclusive with I(vm_id).
    type: str
  vm_id:
    description:
      - The ID of the virtual machine to configure unattend.xml for.
      - Mutually exclusive with I(vm_name).
    type: str
  unattend_xml:
    description:
      - Contents of the unattend.xml file.
      - Must be valid Windows unattend XML format.
      - Typically includes settings for specialize and oobeSystem passes.
      - Required when I(state=present).
    type: str
  state:
    description:
      - The desired state of unattend.xml configuration.
      - C(present) ensures unattend.xml is configured.
      - C(absent) removes the unattend.xml file.
    type: str
    choices: [ present, absent ]
    default: present
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO
notes:
  - The VM must be imported from a sysprepped Windows OVA.
  - This module automatically sets cloudinit_datasource to 'nocloud' on the VM.
  - The cloudinit_datasource enables the file delivery mechanism (virtual CD-ROM) for both Linux and Windows.
  - After the VM starts, Windows will apply the unattend.xml configuration.
  - The VM will typically reboot during the unattend process.
  - Wait 5-10 minutes for Windows setup to complete after powering on.
'''

EXAMPLES = r'''
- name: Configure Windows unattend.xml with inline content
  vergeio.vergeos.windows_unattend:
    vm_name: "win2022-server"
    unattend_xml: |
      <?xml version="1.0" encoding="utf-8"?>
      <unattend xmlns="urn:schemas-microsoft-com:unattend">
        <settings pass="specialize">
          <component name="Microsoft-Windows-Shell-Setup"
                     processorArchitecture="amd64"
                     publicKeyToken="31bf3856ad364e35"
                     language="neutral"
                     versionScope="nonSxS">
            <ComputerName>WIN2022-TEST</ComputerName>
          </component>
        </settings>
      </unattend>

- name: Configure Windows unattend.xml from file
  vergeio.vergeos.windows_unattend:
    vm_name: "win2022-server"
    unattend_xml: "{{ lookup('file', 'unattend.xml') }}"

- name: Remove unattend.xml configuration
  vergeio.vergeos.windows_unattend:
    vm_name: "win2022-server"
    state: absent
'''

RETURN = r'''
vm_id:
  description: The ID of the configured VM
  returned: always
  type: str
  sample: "46"
unattend_file:
  description: Information about the unattend.xml file
  returned: when state is present
  type: dict
  sample:
    key: "123"
    name: "/unattend.xml"
changed:
  description: Whether any changes were made
  returned: always
  type: bool
  sample: true
'''

from ansible.module_utils.basic import AnsibleModule
try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote

from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    VergeOSAPI,
    VergeOSAPIError,
    vergeos_argument_spec
)


def get_vm_id(api, module, vm_name=None, vm_id=None):
    """Get VM ID by name or return provided ID."""
    if vm_id:
        return vm_id

    if not vm_name:
        module.fail_json(msg="Either vm_name or vm_id must be specified")

    try:
        # Get all VMs and filter by name (API doesn't support ?name= filter)
        vms = api.get('vms')
        if not isinstance(vms, list):
            vms = [vms] if vms else []

        matching_vms = [vm for vm in vms if vm.get('name') == vm_name]
        if not matching_vms:
            module.fail_json(msg=f"VM '{vm_name}' not found")

        return str(matching_vms[0].get('$key'))
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to query VMs: {str(e)}")


def enable_cloudinit_datasource(api, module, vm_id):
    """Enable cloud-init datasource on the VM.

    This is required even for Windows VMs to enable the cloudinit_files mechanism.
    The cloudinit_files endpoint provides files as virtual CD-ROM drives that both
    Linux (cloud-init) and Windows (unattend.xml) can read.
    """
    if module.check_mode:
        return True

    payload = {'cloudinit_datasource': 'nocloud'}

    try:
        api.put(f"vms/{vm_id}", payload)
        return True
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to enable cloudinit datasource on VM {vm_id}: {str(e)}")


def get_cloudinit_files(api, module, vm_id):
    """Get existing cloud-init files (including unattend.xml) for a VM."""
    try:
        filter_param = quote(f"owner eq 'vms/{vm_id}'")
        files = api.get(f"cloudinit_files?filter={filter_param}")
        if not files:
            return []
        return files if isinstance(files, list) else [files]
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to query cloudinit_files: {str(e)}")


def create_unattend_file(api, module, vm_id):
    """Create the /unattend.xml file if it doesn't exist."""
    if module.check_mode:
        return "/unattend.xml"  # Return dummy ID in check mode

    payload = {
        'owner': f'vms/{vm_id}',
        'name': '/unattend.xml'
    }

    try:
        response = api.post('cloudinit_files', payload)
        return str(response.get('$key'))
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to create /unattend.xml file: {str(e)}")


def update_unattend_file(api, module, file_id, contents):
    """Update unattend.xml file contents."""
    if module.check_mode:
        return True

    payload = {
        'contents': contents,
        'render': 'no'
    }

    try:
        api.put(f"cloudinit_files/{file_id}", payload)
        return True
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to update /unattend.xml file: {str(e)}")


def delete_unattend_file(api, module, file_id):
    """Delete the unattend.xml file."""
    if module.check_mode:
        return True

    try:
        api.delete(f"cloudinit_files/{file_id}")
        return True
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to delete /unattend.xml file: {str(e)}")


def configure_unattend(api, module):
    """Configure Windows unattend.xml for a VM."""
    vm_name = module.params['vm_name']
    vm_id_param = module.params['vm_id']
    unattend_xml = module.params['unattend_xml']
    state = module.params['state']

    # Resolve VM ID
    vm_id = get_vm_id(api, module, vm_name, vm_id_param)

    # Get existing files
    existing_files = get_cloudinit_files(api, module, vm_id)
    file_map = {f['name']: str(f['$key']) for f in existing_files}

    if state == 'absent':
        # Remove unattend.xml if it exists
        if '/unattend.xml' in file_map:
            delete_unattend_file(api, module, file_map['/unattend.xml'])
            module.exit_json(
                changed=True,
                vm_id=vm_id,
                msg="Unattend.xml file removed"
            )
        else:
            module.exit_json(
                changed=False,
                vm_id=vm_id,
                msg="Unattend.xml file does not exist"
            )

    # state == 'present'
    if not unattend_xml:
        module.fail_json(msg="unattend_xml is required when state=present")

    changed = False

    # Enable cloudinit datasource (required for cloudinit_files to work)
    enable_cloudinit_datasource(api, module, vm_id)
    changed = True

    # Create or update /unattend.xml
    if '/unattend.xml' not in file_map:
        # Create the file
        file_id = create_unattend_file(api, module, vm_id)
        file_map['/unattend.xml'] = file_id
        changed = True
    else:
        file_id = file_map['/unattend.xml']

    # Update file contents
    update_unattend_file(api, module, file_id, unattend_xml)
    changed = True

    module.exit_json(
        changed=changed,
        vm_id=vm_id,
        unattend_file={
            'key': file_id,
            'name': '/unattend.xml'
        }
    )


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        vm_name=dict(type='str'),
        vm_id=dict(type='str'),
        unattend_xml=dict(type='str', no_log=True),  # no_log because it may contain passwords
        state=dict(type='str', default='present', choices=['present', 'absent']),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        mutually_exclusive=[
            ('vm_name', 'vm_id'),
        ],
        required_one_of=[
            ('vm_name', 'vm_id'),
        ],
    )

    api = VergeOSAPI(module)

    try:
        configure_unattend(api, module)
    except VergeOSAPIError as e:
        module.fail_json(msg=str(e))
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
