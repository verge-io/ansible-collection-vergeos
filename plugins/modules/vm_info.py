#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

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
  - VergeIO (@vergeio)
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
  description:
    - Matching virtual machines.
    - Each item is a VM row. The identifier is C($key). Power is C(status)
      (a string such as C(running) or C(stopped)) and C(running) (a bool).
      There is no C(power_state) key and no C(id) key.
    - The module asks for C(['all', 'snapshot_profile', 'tags']). On
      pyvergeos 1.6.1, C(all) is expanded with the SDK's computed fields,
      which is what puts C(status), C(running) and C(node_name) on the row.
      The sample lists the keys this collection's roles read, plus
      C(snapshot_profile) and C(tags), which the projection names because
      the default summary omits them. A live row has more columns than
      the sample shows.
  returned: always
  type: list
  elements: dict
  contains:
    "$key":
      description: VM identifier. Roles read this as C(['$key']).
      type: int
      returned: always
    name:
      description: VM name.
      type: str
      returned: always
    status:
      description: Power status string, for example C(running) or C(stopped).
      type: str
      returned: always
    running:
      description: Whether the VM is powered on.
      type: bool
      returned: always
    node_name:
      description:
        - Name of the node the VM is running on.
        - Empty when the VM is stopped.
      type: str
      returned: always
    description:
      description: VM description.
      type: str
      returned: always
    enabled:
      description: Whether the VM is enabled.
      type: bool
      returned: always
    os_family:
      description: OS family.
      type: str
      returned: always
    cpu_cores:
      description: Number of CPU cores.
      type: int
      returned: always
    ram:
      description: RAM in MB.
      type: int
      returned: always
    machine_type:
      description: QEMU machine type, stored in its expanded form.
      type: str
      returned: always
    machine:
      description: Underlying machine key. Distinct from C($key).
      type: int
      returned: always
    ha_group:
      description: HA group name, or empty.
      type: str
      returned: always
    is_snapshot:
      description: Whether the row is a snapshot rather than a VM.
      type: bool
      returned: always
    snapshot_profile:
      description:
        - Protection profile key, or empty when the VM is not enrolled.
        - Not in the SDK's default summary, so this module asks for it by name.
      type: raw
      returned: always
    tags:
      description:
        - Tag classification stored on the VM row.
        - Not in the SDK's default summary, so this module asks for it by name.
      type: raw
      returned: always
  sample:
    - "$key": 1
      name: app-01
      description: ""
      enabled: true
      os_family: linux
      cpu_cores: 1
      ram: 1024
      machine_type: pc-q35-10.0
      machine: 48
      node_name: node1
      ha_group: ""
      is_snapshot: false
      snapshot_profile: ""
      tags: ""
      status: running
      running: true
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    resolve_one,
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


# The SDK's default projection is a sensible summary of 24 columns, and it
# omits two an operator regularly needs in order to ANSWER A QUESTION rather
# than to change something:
#
#   snapshot_profile   which protection policy this VM is on (its $key)
#   tags               what it is classified as
#
# Both are ordinary columns on the vm row -- 'all' returns them, checked
# against a live row on 26.1.8 (74 columns). They are named alongside 'all'
# anyway, because that is what this module actually needs: if 'all' ever
# narrows, the names say what must not be lost, and the test asserts it.
#
# Before this, asking "which VMs are unprotected?" meant bypassing the
# collection entirely. The protect role shipped a Python script whose whole
# job was this one projection; it is deleted in the same change.
#
# 'all' as a bare STRING was wrong on pyvergeos 1.2.7: the SDK serialised
# it per-character and the API returned a single field with no error
# (pyvergeos#101). That is fixed in later releases, including 1.6.1. The
# list form is what this module sends, and it is correct on every version
# from that bug through the current floor. Same trap network_info
# documents (#25).
#
# On pyvergeos 1.6.1, a projection that contains 'all' also appends the
# manager's computed fields (pyVergeOS#117). That is why status, running
# and node_name are on the row. A raw fields=all without that expansion
# omits them.
FIELDS = ['all', 'snapshot_profile', 'tags']


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    client = get_vergeos_client(module)
    name = module.params.get('name')

    try:
        if name:
            # Get specific VM by name
            try:
                vm = resolve_one(module, client.vms, name, 'VM',
                                 fields=FIELDS)
                vms = [dict(vm)]
            except NotFoundError:
                vms = []
        else:
            # Get all VMs
            vms = [dict(vm) for vm in client.vms.list(fields=FIELDS)]

        module.exit_json(changed=False, vms=vms)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
