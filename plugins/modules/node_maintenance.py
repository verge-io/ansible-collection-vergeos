#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: node_maintenance
short_description: Put a VergeOS node into or out of maintenance mode
version_added: "2.1.0"
description:
  - Enter or leave maintenance mode on a physical node. Entering maintenance
    evacuates the node's running workloads to the rest of the cluster.
  - Also restarts a node with O(state=restarted), which is what applying an
    installed update requires.
options:
  name:
    description:
      - Node name. Matched exactly.
    type: str
    required: true
  state:
    description:
      - C(maintenance) puts the node into maintenance mode, evacuating it.
      - C(active) takes it back out.
      - C(restarted) restarts the node. It does not enter maintenance first -
        do that explicitly, so that draining and rebooting stay separate,
        reviewable steps.
    type: str
    choices: [ maintenance, active, restarted ]
    required: true
  force:
    description:
      - Allow the operation when no other node is available to take the
        workloads.
      - Without this, entering maintenance on the last usable node is refused.
        Evacuating a node with nowhere to evacuate to does not move the
        workloads somewhere safe - it stops them.
    type: bool
    default: false
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Supports C(check_mode).
  - This module does not wait. Entering maintenance and restarting are both
    asynchronous; poll with M(vergeio.vergeos.node_info) when you need to know
    the node has finished.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Drain a node before working on it
  vergeio.vergeos.node_maintenance:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "node2"
    state: maintenance

- name: Restart the node to apply an installed update
  vergeio.vergeos.node_maintenance:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "node2"
    state: restarted

- name: Return the node to service
  vergeio.vergeos.node_maintenance:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "node2"
    state: active
'''

RETURN = r'''
node:
  description: The node as it stood when the module returned.
  returned: always
  type: dict
  sample:
    name: "node2"
    online: true
    maintenance: true
    needs_restart: false
available_peers:
  description:
    - Names of the online, non-maintenance nodes that could take this node's
      workloads.
  returned: always
  type: list
  elements: str
  sample: ["node1", "node3"]
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.nodes import (
    find_node,
    other_online_nodes,
    summarize_node,
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
        name=dict(type='str', required=True),
        state=dict(type='str', required=True,
                   choices=['maintenance', 'active', 'restarted']),
        force=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    params = module.params
    name = params['name']
    state = params['state']
    client = get_vergeos_client(module)

    try:
        node = find_node(client, name)
        if not node:
            module.fail_json(msg="no node named '%s'." % name)

        nodes = [summarize_node(dict(n)) for n in client.nodes.list()]
        peers = other_online_nodes(nodes, name)
        result = dict(node=node,
                      available_peers=[p.get('name') for p in peers])

        # ── active ───────────────────────────────────────────────────────────
        if state == 'active':
            if not node['maintenance']:
                module.exit_json(changed=False, **result)
            if not module.check_mode:
                client.nodes.disable_maintenance(node['$key'])
                result['node'] = find_node(client, name) or node
            module.exit_json(changed=True, **result)

        # ── maintenance ──────────────────────────────────────────────────────
        if state == 'maintenance':
            if node['maintenance']:
                module.exit_json(changed=False, **result)

            # Evacuating a node with nowhere to evacuate TO does not move the
            # workloads somewhere safe; it stops them. Refusing is the honest
            # default, and force makes the choice explicit and visible.
            if not peers and not params['force']:
                module.fail_json(
                    msg="refusing to drain '%s': no other online node is "
                        "available to take its workloads, so evacuating it "
                        "would stop them rather than move them. Set force "
                        "if that is what you intend." % name,
                    **result)

            if not module.check_mode:
                client.nodes.enable_maintenance(node['$key'])
                result['node'] = find_node(client, name) or node
            module.exit_json(changed=True, **result)

        # ── restarted ────────────────────────────────────────────────────────
        # Always reported as changed: a restart is an event, not a state to
        # converge on, and there is no way to ask "has this node already been
        # restarted for this reason".
        if not peers and not params['force']:
            module.fail_json(
                msg="refusing to restart '%s': no other online node is "
                    "available, so restarting it takes the cluster down. Set "
                    "force if that is what you intend." % name,
                **result)

        if not module.check_mode:
            client.nodes.restart(node['$key'])
        module.exit_json(
            changed=True,
            msg="restart requested for '%s'; it is asynchronous, so poll "
                "node_info to see it come back." % name,
            **result)

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
