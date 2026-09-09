#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: cluster_info
short_description: Gather information about clusters in VergeOS
version_added: "1.0.0"
description:
  - Gather facts about clusters in VergeOS.
  - Also reports live capacity from the C(cluster_status) table, and answers
    the question a rolling reboot depends on - whether a given node can be
    drained without running out of room.
  - The platform accepts a drain request whether or not there is anywhere for
    the workloads to go. When there is not, the node flips to maintenance
    immediately and the migration then stalls with no destination. This module
    reads the numbers that predict that, before the request is made.
options:
  drain_candidates:
    description:
      - Node names to test a drain against.
      - Each is answered independently, one node at a time, not as a set.
        Draining two nodes at once is a different question and this module
        does not answer it.
      - Omit to report capacity without testing any node.
    type: list
    elements: str
    version_added: "2.1.0"
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Read-only. Supports C(check_mode) and never reports changed.
  - Capacity arithmetic uses each node's C(vm_ram), which is what the platform
    makes available to VMs, not its physical C(ram). On a measured 26.1.8 node
    those are 68352 MB and 94208 MB respectively - using the physical figure
    overstates capacity by about a third.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Get information about all clusters
  vergeio.vergeos.cluster_info:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
  register: cluster_info

- name: Report cluster capacity
  vergeio.vergeos.cluster_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
  register: capacity

- name: Refuse to start a rolling reboot that cannot finish
  vergeio.vergeos.cluster_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    drain_candidates: "{{ nodes_needing_restart }}"
  register: capacity

- name: Every node must be drainable
  ansible.builtin.assert:
    that: capacity.drain_checks | rejectattr('fits') | list | length == 0
    fail_msg: >-
      {{ capacity.drain_checks | rejectattr('fits')
         | map(attribute='reason') | join('; ') }}
'''

RETURN = r'''
clusters:
  description:
    - List of clusters, from the C(clusters) table - configuration, not live
      state.
  returned: always
  type: list
  elements: dict
  sample:
    - name: "conundrum-lab"
      enabled: true
      compute: true
      storage: true
      default_cpu: "EPYC-Rome"
cluster_status:
  description:
    - Live state and capacity, one entry per cluster, from the
      C(cluster_status) table.
    - Separate from RV(clusters) because they are different tables with
      different keys. C(clusters) has a name; C(cluster_status) has the
      numbers.
  returned: always
  type: list
  elements: dict
  version_added: "2.1.0"
  sample:
    - state: "online"
      online_nodes: 2
      total_nodes: 2
      online_ram: 137472
      used_ram: 87040
      online_cores: 64
      used_cores: 31
      running_machines: 7
ram_headroom_mb:
  description:
    - Uncommitted VM RAM across online nodes, C(online_ram - used_ram).
    - Summed over all clusters.
  returned: always
  type: int
  version_added: "2.1.0"
  sample: 50432
drain_checks:
  description: One verdict per name in O(drain_candidates).
  returned: when O(drain_candidates) is set
  type: list
  elements: dict
  version_added: "2.1.0"
  contains:
    node:
      description: The node tested.
      type: str
      returned: always
    fits:
      description:
        - Whether the drain has somewhere to go. C(none) means it could not be
          determined, which is not the same as yes.
      type: bool
      returned: always
    deficit_mb:
      description: How much RAM short the surviving nodes are. C(0) when it fits.
      type: int
      returned: always
    survivor_ram_mb:
      description: VM RAM on the nodes that would remain in service.
      type: int
      returned: always
    used_ram_mb:
      description: Cluster-wide RAM committed to running machines.
      type: int
      returned: always
    reason:
      description: The verdict in words, with the numbers behind it.
      type: str
      returned: always
    estimated:
      description:
        - True when a node reported no C(vm_ram) and physical RAM was
          substituted, which overstates capacity.
      type: bool
      returned: always
  sample:
    - node: "node2"
      fits: false
      deficit_mb: 18688
      survivor_ram_mb: 68352
      used_ram_mb: 87040
no_failover_reservation:
  description:
    - Nodes whose C(failover_ram) is zero, meaning nothing is held back for a
      peer failure. A cluster with no reservation anywhere is sized for all
      nodes being up.
  returned: always
  type: list
  elements: str
  version_added: "2.1.0"
  sample: ["node1", "node2"]
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.clusters import (
    drain_capacity,
    failover_reserved,
    fetch_cluster_status,
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
        drain_candidates=dict(type='list', elements='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)

    try:
        # 'clusters' keeps the meaning it has had since 1.0.0 -- the
        # clusters table. The capacity figures live in a DIFFERENT table with
        # different keys, so they are reported under their own name rather
        # than quietly changing what an existing playbook reads.
        clusters = [dict(c) for c in client.clusters.list()]
        status = fetch_cluster_status(client)
        nodes = [dict(n) for n in client.nodes.list()]

        headroom = sum(int(c.get('online_ram') or 0) - int(c.get('used_ram') or 0)
                       for c in status)

        result = dict(
            changed=False,
            clusters=clusters,
            cluster_status=status,
            ram_headroom_mb=headroom,
            no_failover_reservation=sorted(
                n for n in failover_reserved(nodes) if n),
        )

        if module.params['drain_candidates']:
            # One cluster_status row per cluster; the drain question is asked
            # against the cluster the node is in, so pick the row by the
            # node's own cluster reference rather than assuming there is one.
            by_cluster = {str(c.get('cluster')): c for c in status}
            checks = []
            for name in module.params['drain_candidates']:
                row = next((n for n in nodes if n.get('name') == name), {})
                cluster_row = (by_cluster.get(str(row.get('cluster')))
                               or (status[0] if status else {}))
                peers = [n for n in nodes
                         if row.get('cluster') is None
                         or n.get('cluster') == row.get('cluster')]
                checks.append(drain_capacity(cluster_row, peers, name))
            result['drain_checks'] = checks

        module.exit_json(**result)

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
