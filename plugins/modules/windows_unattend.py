#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

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
  - Reports C(changed) only when the datasource or /unattend.xml is written.
    An identical re-apply, including in check mode, reports C(changed=false).
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
  - VergeIO (@vergeio)
notes:
  - The VM must be imported from a sysprepped Windows OVA.
  - This module sets cloudinit_datasource to 'nocloud' when the VM is not already using it.
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
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    resolve_one,
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    cloudinit_datasource_differs,
    cloudinit_file_needs_update,
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


def get_vm(client, module, vm_name=None, vm_id=None):
    """Get VM from name or ID using SDK."""
    if vm_id:
        try:
            return client.vms.get(key=vm_id)
        except NotFoundError:
            module.fail_json(msg=f"VM with ID '{vm_id}' not found")

    if not vm_name:
        module.fail_json(msg="Either vm_name or vm_id must be specified")

    try:
        return resolve_one(module, client.vms, vm_name, 'VM')
    except NotFoundError:
        module.fail_json(msg=f"VM '{vm_name}' not found")


def enable_cloudinit_datasource(client, module, vm_key):
    """Enable cloud-init datasource on the VM.

    This is required even for Windows VMs to enable the cloudinit_files mechanism.
    The cloudinit_files endpoint provides files as virtual CD-ROM drives that both
    Linux (cloud-init) and Windows (unattend.xml) can read.
    """
    if module.check_mode:
        return True

    client._request('PUT', f'vms/{vm_key}', json_data={'cloudinit_datasource': 'nocloud'})
    return True


def get_cloudinit_files(client, module, vm_key):
    """Get existing cloud-init files (including unattend.xml) for a VM."""
    try:
        return list(client.cloudinit_files.list_for_vm(int(vm_key)))
    except (NotFoundError, AttributeError):
        return []


def create_unattend_file(client, module, vm_key, contents):
    """Create the /unattend.xml file with contents using SDK."""
    if module.check_mode:
        return "/unattend.xml"  # Return dummy ID in check mode

    file_obj = client.cloudinit_files.create(
        vm_key=int(vm_key),
        name='/unattend.xml',
        contents=contents,
        render='No'
    )
    return str(dict(file_obj).get('$key'))


def update_unattend_file(client, module, file_key, contents):
    """Update unattend.xml file contents using SDK."""
    if module.check_mode:
        return True

    client.cloudinit_files.update(
        key=int(file_key),
        contents=contents,
        render='No'
    )
    return True


def delete_unattend_file(client, module, file_obj):
    """Delete the unattend.xml file using SDK."""
    if module.check_mode:
        return True

    file_obj.delete()
    return True


def configure_unattend(client, module):
    """Configure Windows unattend.xml for a VM using SDK."""
    vm_name = module.params['vm_name']
    vm_id_param = module.params['vm_id']
    unattend_xml = module.params['unattend_xml']
    state = module.params['state']

    # Get VM
    vm = get_vm(client, module, vm_name, vm_id_param)
    vm_dict = dict(vm)
    vm_key = str(vm_dict.get('$key'))

    # Get existing files
    existing_files = get_cloudinit_files(client, module, vm_key)
    file_rows = {}
    file_objs = {}
    for file_obj in existing_files:
        row = dict(file_obj)
        file_rows[row['name']] = row
        file_objs[row['name']] = file_obj

    if state == 'absent':
        # Remove unattend.xml if it exists
        if '/unattend.xml' in file_objs:
            delete_unattend_file(client, module, file_objs['/unattend.xml'])
            module.exit_json(
                changed=True,
                vm_id=vm_key,
                msg="Unattend.xml file removed"
            )
        else:
            module.exit_json(
                changed=False,
                vm_id=vm_key,
                msg="Unattend.xml file does not exist"
            )

    # state == 'present'
    if not unattend_xml:
        module.fail_json(msg="unattend_xml is required when state=present")

    changed = False

    # cloudinit_datasource is required for the file to be delivered. PUT it
    # only when the VM is not already on nocloud.
    if cloudinit_datasource_differs(vm_dict.get('cloudinit_datasource'), 'nocloud'):
        enable_cloudinit_datasource(client, module, vm_key)
        changed = True

    # Create or update /unattend.xml. GET of the file row does not include
    # contents; get_content() does. Check mode reads and does not write.
    row = file_rows.get('/unattend.xml')
    if row is None:
        file_id = create_unattend_file(client, module, vm_key, unattend_xml)
        changed = True
    else:
        file_id = str(row.get('$key'))
        if cloudinit_file_needs_update(client, file_id, unattend_xml, row.get('render')):
            update_unattend_file(client, module, file_id, unattend_xml)
            changed = True

    module.exit_json(
        changed=changed,
        vm_id=vm_key,
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

    client = get_vergeos_client(module)

    try:
        configure_unattend(client, module)
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
