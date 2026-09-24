#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: node_info
short_description: Gather information about VergeOS nodes
version_added: "2.1.0"
description:
  - Gather facts about physical nodes, including online state, maintenance
    mode, resource usage, and whether a node is waiting for a restart.
options:
  name:
    description:
      - Report only the node of this name. Matched exactly.
    type: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Report every node
  vergeio.vergeos.node_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
  register: nodes

- name: Show which nodes are waiting for a restart
  ansible.builtin.debug:
    msg: "{{ nodes.needs_restart | map(attribute='name') | list }}"

- name: Refuse to drain when the cluster cannot absorb it
  ansible.builtin.assert:
    that: nodes.online | length >= 2
    fail_msg: "a single online node has nowhere to evacuate to"

- name: Which nodes are carrying nothing right now
  ansible.builtin.debug:
    msg: >-
      {{ nodes.nodes | selectattr('running_machines', 'equalto', 0)
         | map(attribute='name') | list }}
'''

RETURN = r'''
nodes:
  description: Matching nodes.
  returned: always
  type: list
  elements: dict
  contains:
    name:
      description: Node name.
      type: str
      returned: always
    online:
      description: Whether the node is online.
      type: bool
      returned: always
    maintenance:
      description: Whether the node is in maintenance mode.
      type: bool
      returned: always
    needs_restart:
      description: Whether the node is waiting for a restart.
      type: bool
      returned: always
    running_machines:
      description:
        - How many machines are resident on the node - VMs and vnets both.
        - Not the same as the number of VMs placed there. A vnet is a machine
          and C(network_info) reports no node for one, so a check written over
          VMs alone can call a node empty while it is hosting the fabric.
      type: int
      returned: always
      version_added: "2.2.0"
    unmigratable_machines:
      description:
        - How many of those machines the platform says it cannot migrate.
          Draining the node stops these rather than moving them.
      type: int
      returned: always
      version_added: "2.2.0"
  sample:
    - name: "node1"
      online: true
      maintenance: false
      needs_restart: false
      running_machines: 9
      unmigratable_machines: 0
      cores: 32
online:
  description: The subset of RV(nodes) that are online.
  returned: always
  type: list
  elements: dict
maintenance:
  description: The subset of RV(nodes) in maintenance mode.
  returned: always
  type: list
  elements: dict
needs_restart:
  description:
    - The subset of RV(nodes) waiting for a restart, which is what an update
      leaves behind once it has been installed.
  returned: always
  type: list
  elements: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.nodes import (
    list_nodes,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    params = module.params
    client = get_vergeos_client(module)

    try:
        nodes = list_nodes(client)
        if params.get('name'):
            nodes = [n for n in nodes if n.get('name') == params['name']]
            if not nodes:
                module.fail_json(msg="no node named '%s'." % params['name'])

        module.exit_json(
            changed=False,
            nodes=nodes,
            online=[n for n in nodes if n['online']],
            maintenance=[n for n in nodes if n['maintenance']],
            needs_restart=[n for n in nodes if n['needs_restart']],
        )

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
