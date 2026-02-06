#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: cloud_init
short_description: Manage cloud-init configuration for VMs in VergeOS
version_added: "1.0.0"
description:
  - Configure cloud-init for virtual machines in VergeOS.
  - Enable cloud-init datasource and manage cloud-init configuration files.
  - Handles /user-data, /meta-data, and /network-config files.
  - Cloud-init files are automatically removed when the VM is deleted.
options:
  vm_name:
    description:
      - The name of the virtual machine to configure cloud-init for.
      - Mutually exclusive with I(vm_id).
    type: str
  vm_id:
    description:
      - The ID of the virtual machine to configure cloud-init for.
      - Mutually exclusive with I(vm_name).
    type: str
  datasource:
    description:
      - The cloud-init datasource type.
      - Set to C(nocloud) to enable cloud-init.
      - Set to empty string or omit to disable cloud-init.
    type: str
    choices: [ nocloud, '' ]
  user_data:
    description:
      - Contents of the /user-data cloud-init file.
      - Typically contains #cloud-config YAML.
      - "Example C(#cloud-config\\nmanage_etc_hosts: true\\nhostname: myhost)"
    type: str
  meta_data:
    description:
      - Contents of the /meta-data cloud-init file.
      - Typically contains instance-id and local-hostname.
      - "Example C(instance-id: myhost-001\\nlocal-hostname: myhost)"
    type: str
  network_config:
    description:
      - Contents of the /network-config cloud-init file.
      - Defines network configuration in Netplan v2 format.
      - "Example C(version: 2\\nethernets:\\n  eth0:\\n    dhcp4: false\\n    addresses: [10.0.0.5/24])"
    type: str
  hostname:
    description:
      - Convenience parameter to set hostname in both user-data and meta-data.
      - Generates standard user-data and meta-data if not explicitly provided.
      - Mutually exclusive with explicit I(user_data) or I(meta_data).
    type: str
  network:
    description:
      - Convenience parameter to configure simple static network.
      - Must be a dict with keys C(interface), C(address), C(gateway), C(nameservers).
      - Generates network-config if not explicitly provided via I(network_config).
    type: dict
    suboptions:
      interface:
        description: Network interface name (e.g., eth0, ens1)
        type: str
        required: true
      address:
        description: IP address with CIDR notation (e.g., 10.0.0.5/24)
        type: str
        required: true
      gateway:
        description: Default gateway IP address
        type: str
        required: true
      nameservers:
        description: List of DNS nameserver IPs
        type: list
        elements: str
        default: ['8.8.8.8', '1.1.1.1']
  state:
    description:
      - The desired state of cloud-init configuration.
      - C(present) ensures cloud-init is configured.
      - C(absent) disables cloud-init and removes cloud-init files.
    type: str
    choices: [ present, absent ]
    default: present
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Enable cloud-init with hostname only
  vergeio.vergeos.cloud_init:
    vm_name: "imported-vm"
    datasource: nocloud
    hostname: "webserver-01"

- name: Configure cloud-init with static network
  vergeio.vergeos.cloud_init:
    vm_name: "imported-vm"
    datasource: nocloud
    hostname: "webserver-01"
    network:
      interface: ens1
      address: "192.168.1.7/24"
      gateway: "192.168.1.1"
      nameservers:
        - "8.8.8.8"
        - "1.1.1.1"

- name: Configure cloud-init with explicit content
  vergeio.vergeos.cloud_init:
    vm_name: "imported-vm"
    datasource: nocloud
    user_data: |
      #cloud-config
      manage_etc_hosts: true
      hostname: webserver-01
    meta_data: |
      instance-id: webserver-01
      local-hostname: webserver-01
    network_config: |
      version: 2
      ethernets:
        ens1:
          dhcp4: false
          addresses: [192.168.10.10/24]
          gateway4: 192.168.1.1
          nameservers:
            addresses: [8.8.8.8, 1.1.1.1]

- name: Disable cloud-init
  vergeio.vergeos.cloud_init:
    vm_name: "imported-vm"
    state: absent
'''

RETURN = r'''
vm_id:
  description: The ID of the configured VM
  returned: always
  type: str
  sample: "46"
cloudinit_files:
  description: List of created/updated cloud-init file IDs
  returned: when state is present
  type: list
  elements: dict
  sample:
    - key: "123"
      name: "/user-data"
    - key: "124"
      name: "/meta-data"
    - key: "125"
      name: "/network-config"
datasource:
  description: The cloud-init datasource that was configured
  returned: when state is present
  type: str
  sample: "nocloud"
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


def get_vm(client, module, vm_name=None, vm_id=None):
    """Get VM from name or ID using SDK."""
    if vm_id:
        try:
            return client.vms.get(key=vm_id)
        except NotFoundError:
            module.fail_json(msg=f"VM with ID '{vm_id}' not found")

    if vm_name:
        try:
            return client.vms.get(name=vm_name)
        except NotFoundError:
            module.fail_json(msg=f"VM '{vm_name}' not found")

    module.fail_json(msg="Either vm_name or vm_id must be provided")


def enable_cloudinit_datasource(client, module, vm_key, datasource):
    """Enable cloud-init datasource on the VM."""
    if module.check_mode:
        return True

    client._request('PUT', f'vms/{vm_key}', json_data={'cloudinit_datasource': datasource})
    return True


def get_cloudinit_files(client, module, vm_key):
    """Get existing cloud-init files for a VM using SDK."""
    try:
        return list(client.cloudinit_files.list_for_vm(int(vm_key)))
    except (NotFoundError, AttributeError):
        return []


def create_cloudinit_file(client, module, vm_key, filename, contents):
    """Create a cloud-init file using SDK."""
    if module.check_mode:
        return filename  # Return dummy ID in check mode

    file_obj = client.cloudinit_files.create(
        vm_key=int(vm_key),
        name=filename,
        contents=contents,
        render='No'
    )
    return str(dict(file_obj).get('$key'))


def update_cloudinit_file(client, module, file_key, contents):
    """Update cloud-init file contents using SDK."""
    if module.check_mode:
        return True

    client.cloudinit_files.update(
        key=int(file_key),
        contents=contents,
        render='No'
    )
    return True


def delete_cloudinit_files(client, module, vm_key):
    """Delete all cloud-init files for a VM using SDK."""
    files = get_cloudinit_files(client, module, vm_key)

    if module.check_mode:
        return len(files) > 0

    changed = False
    for file_obj in files:
        try:
            file_obj.delete()
            changed = True
        except Exception as e:
            file_dict = dict(file_obj)
            module.warn(f"Failed to delete cloud-init file {file_dict.get('$key')}: {str(e)}")

    return changed


def generate_user_data(hostname):
    """Generate standard user-data content."""
    return f"#cloud-config\nmanage_etc_hosts: true\nhostname: {hostname}"


def generate_meta_data(hostname):
    """Generate standard meta-data content."""
    return f"instance-id: {hostname}-001\nlocal-hostname: {hostname}"


def generate_network_config(interface, address, gateway, nameservers):
    """Generate standard network-config content."""
    ns_yaml = "\n      ".join([f"- {ns}" for ns in nameservers])
    return (
        f"version: 2\n"
        f"ethernets:\n"
        f"  {interface}:\n"
        f"    dhcp4: false\n"
        f"    addresses: [{address}]\n"
        f"    gateway4: {gateway}\n"
        f"    nameservers:\n"
        f"      addresses:\n"
        f"      {ns_yaml}"
    )


def configure_cloudinit(client, module):
    """Configure cloud-init for a VM using SDK."""
    vm_name = module.params['vm_name']
    vm_id_param = module.params['vm_id']
    datasource = module.params.get('datasource', 'nocloud')
    hostname = module.params.get('hostname')
    network = module.params.get('network')

    # Get VM
    vm = get_vm(client, module, vm_name, vm_id_param)
    vm_key = str(dict(vm).get('$key'))

    changed = False
    result = {
        'vm_id': vm_key,
        'cloudinit_files': []
    }

    # Enable cloud-init datasource
    if datasource:
        enable_cloudinit_datasource(client, module, vm_key, datasource)
        changed = True
        result['datasource'] = datasource

    # Get existing cloud-init files
    existing_files = get_cloudinit_files(client, module, vm_key)
    file_map = {dict(f)['name']: str(dict(f).get('$key')) for f in existing_files}

    # Prepare content
    user_data_content = module.params.get('user_data')
    meta_data_content = module.params.get('meta_data')
    network_config_content = module.params.get('network_config')

    # Generate content from convenience parameters
    if hostname and not user_data_content:
        user_data_content = generate_user_data(hostname)
    if hostname and not meta_data_content:
        meta_data_content = generate_meta_data(hostname)
    if network and not network_config_content:
        network_config_content = generate_network_config(
            network['interface'],
            network['address'],
            network['gateway'],
            network.get('nameservers', ['8.8.8.8', '1.1.1.1'])
        )

    # Process each cloud-init file
    files_to_update = []
    if user_data_content:
        files_to_update.append(('/user-data', user_data_content))
    if meta_data_content:
        files_to_update.append(('/meta-data', meta_data_content))
    if network_config_content:
        files_to_update.append(('/network-config', network_config_content))

    for filename, content in files_to_update:
        if filename not in file_map:
            # Create file with contents in one call
            file_id = create_cloudinit_file(client, module, vm_key, filename, content)
            changed = True
        else:
            # Update existing file
            file_id = file_map[filename]
            update_cloudinit_file(client, module, file_id, content)
            changed = True

        result['cloudinit_files'].append({
            'key': file_id,
            'name': filename
        })

    result['changed'] = changed
    module.exit_json(**result)


def remove_cloudinit(client, module):
    """Remove cloud-init configuration from a VM using SDK."""
    vm_name = module.params['vm_name']
    vm_id_param = module.params['vm_id']

    # Get VM
    vm = get_vm(client, module, vm_name, vm_id_param)
    vm_key = str(dict(vm).get('$key'))

    # Disable cloud-init datasource
    enable_cloudinit_datasource(client, module, vm_key, '')

    # Delete cloud-init files
    changed = delete_cloudinit_files(client, module, vm_key)

    module.exit_json(
        changed=changed,
        vm_id=vm_key,
        msg="Cloud-init disabled and files removed"
    )


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        vm_name=dict(type='str'),
        vm_id=dict(type='str'),
        datasource=dict(type='str', choices=['nocloud', '']),
        user_data=dict(type='str'),
        meta_data=dict(type='str'),
        network_config=dict(type='str'),
        hostname=dict(type='str'),
        network=dict(
            type='dict',
            options=dict(
                interface=dict(type='str', required=True),
                address=dict(type='str', required=True),
                gateway=dict(type='str', required=True),
                nameservers=dict(
                    type='list',
                    elements='str',
                    default=['8.8.8.8', '1.1.1.1']
                )
            )
        ),
        state=dict(type='str', choices=['present', 'absent'], default='present'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        required_one_of=[('vm_name', 'vm_id')],
        mutually_exclusive=[
            ('vm_name', 'vm_id'),
            ('hostname', 'user_data'),
            ('hostname', 'meta_data'),
            ('network', 'network_config'),
        ],
    )

    # Validate required parameters for present state
    if module.params['state'] == 'present':
        if not module.params.get('datasource'):
            module.params['datasource'] = 'nocloud'

        has_content = any([
            module.params.get('user_data'),
            module.params.get('meta_data'),
            module.params.get('network_config'),
            module.params.get('hostname'),
            module.params.get('network')
        ])

        if not has_content:
            module.fail_json(
                msg="At least one of user_data, meta_data, network_config, hostname, or network must be provided"
            )

    client = get_vergeos_client(module)

    try:
        if module.params['state'] == 'present':
            configure_cloudinit(client, module)
        else:
            remove_cloudinit(client, module)
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
