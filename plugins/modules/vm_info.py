#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vm_info
short_description: Gather information about VMs in VergeOS
version_added: "1.0.0"
description:
  - Gather facts about virtual machines in VergeOS.
  - Can retrieve information about all VMs or filter by name.
options:
  name:
    description:
      - Name of a specific VM to query.
      - If not specified, returns information about all VMs.
    type: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO
'''

EXAMPLES = r'''
- name: Get information about all VMs
  vergeio.vergeos.vm_info:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
  register: all_vms

- name: Get information about a specific VM
  vergeio.vergeos.vm_info:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "web-server-01"
  register: vm_info

- name: Display VM information
  ansible.builtin.debug:
    var: vm_info.vms
'''

RETURN = r'''
vms:
  description: List of virtual machines
  returned: always
  type: list
  elements: dict
  sample:
    - name: "web-server-01"
      description: "Web server"
      cpu_cores: 4
      ram: 8192
      power_state: "running"
      id: "12345"
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    VergeOSAPI,
    VergeOSAPIError,
    vergeos_argument_spec
)


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    api = VergeOSAPI(module)
    name = module.params.get('name')

    try:
        vms = api.get('vms')

        if name:
            # Filter by name
            filtered_vms = [vm for vm in vms if vm.get('name') == name]
            module.exit_json(changed=False, vms=filtered_vms)
        else:
            module.exit_json(changed=False, vms=vms)

    except VergeOSAPIError as e:
        module.fail_json(msg=str(e))
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
