#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vm_nic_info
short_description: Gather information about a VM's NICs in VergeOS
version_added: "2.1.0"
description:
  - Gather facts about the network interfaces attached to a virtual machine,
    including which network each one is attached to.
  - RV(detached) separates out NICs attached to no network at all, which is a
    state a VM can reach while every other check passes.
options:
  vm:
    description:
      - Name of the VM whose NICs to report.
    type: str
    required: true
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Report a VM's NICs
  vergeio.vergeos.vm_nic_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    vm: "web-01"
  register: nics

- name: Show what each NIC is attached to
  ansible.builtin.debug:
    msg: >-
      {{ dict(nics.nics | map(attribute='name')
              | zip(nics.nics | map(attribute='vnet_name'))) }}

- name: Refuse a VM whose NIC is attached to nothing
  ansible.builtin.assert:
    that: nics.detached | length == 0
    fail_msg: >-
      web-01 has NIC(s) attached to no network:
      {{ nics.detached | map(attribute='name') | list }}
'''

RETURN = r'''
nics:
  description: The VM's NICs, ordered as the UI orders them.
  returned: always
  type: list
  elements: dict
  contains:
    name:
      description: NIC name.
      type: str
      returned: always
    vnet:
      description: Key of the network the NIC is attached to, if any.
      type: raw
      returned: always
    vnet_name:
      description: Name of the network the NIC is attached to, if any.
      type: str
      returned: always
    macaddress:
      description: MAC address.
      type: str
      returned: always
  sample:
    - name: "eth0"
      vnet: 3
      vnet_name: "External"
      macaddress: "52:54:00:11:22:33"
detached:
  description:
    - The subset of RV(nics) attached to no network.
    - A recipe whose network question was left unanswered can produce this,
      and such a VM boots, reports C(running), and reaches nothing.
  returned: always
  type: list
  elements: dict
machine:
  description: Key of the VM's machine.
  returned: always
  type: str
  sample: "18"
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    resolve_one,
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.machine import (
    vm_nics,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


def detached(nics):
    """NICs attached to no network.

    Both shapes count: the field absent, and the field present but null. A
    real deploy produced a VM whose eth0 carried vnet=null, and treating only
    absence as detached would have called that attached.
    """
    return [n for n in nics if n.get('vnet') in (None, '')]


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        vm=dict(type='str', required=True),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    params = module.params
    client = get_vergeos_client(module)

    try:
        vm = resolve_one(module, client.vms, params['vm'], 'VM')
        nics = vm_nics(vm)

        module.exit_json(
            changed=False,
            nics=nics,
            detached=detached(nics),
            machine=str(dict(vm).get('machine') or ''),
        )

    except NotFoundError as e:
        module.fail_json(msg=f"VM '{params['vm']}' not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
