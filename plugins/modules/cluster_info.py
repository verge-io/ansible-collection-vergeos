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
    the question a drain or a rolling reboot depends on - whether a given node
    can be taken out of service without running out of room.
  - The platform accepts a drain request whether or not there is anywhere for
    the workloads to go. When there is not, the node flips to maintenance
    immediately and the migration then stalls with no destination and no
    error. This module reads the numbers that predict that, before the request
    is made.
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
    version_added: "2.2.0"
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Read-only. Supports C(check_mode) and never reports changed.
  - Capacity arithmetic uses each node's C(vm_ram), which is what the platform
    makes available to VMs, not its physical C(ram). On a measured 26.1.8 node
    those are 68352 MB and 94208 MB respectively - using the physical figure
    overstates capacity by about a third.
  - RV(drain_checks[].fits) of C(true) means the RAM is there. It is not a
    promise the migration will succeed - the platform's placement rule is
    stricter than free RAM and is not visible from outside. C(none) means it
    could not be determined, which is not the same as yes.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Get information about all clusters
  vergeio.vergeos.cluster_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
  register: cluster_info

- name: Ask whether a rolling reboot could finish
  vergeio.vergeos.cluster_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    drain_candidates: "{{ nodes_needing_restart }}"
  register: capacity

- name: Every node that will reboot must have somewhere to evacuate to
  ansible.builtin.assert:
    that: >-
      capacity.drain_checks | rejectattr('fits', 'equalto', true)
      | list | length == 0
    fail_msg: >-
      {{ capacity.drain_checks | rejectattr('fits', 'equalto', true)
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
cluster_status:
  description:
    - Live state and capacity, one entry per cluster, from the
      C(cluster_status) table.
    - Reported under its own name rather than folded into RV(clusters),
      because they are different tables with different keys and an existing
      playbook reads the latter.
  returned: always
  type: list
  elements: dict
  version_added: "2.2.0"
  sample:
    - state: "online"
      online_nodes: 2
      total_nodes: 2
      online_ram: 137472
      used_ram: 9216
      online_cores: 64
      used_cores: 5
      running_machines: 2
ram_headroom_mb:
  description:
    - Uncommitted VM RAM across online nodes, C(online_ram - used_ram),
      summed over all clusters.
  returned: always
  type: int
  version_added: "2.2.0"
  sample: 128256
drain_checks:
  description: One verdict per name in O(drain_candidates).
  returned: when O(drain_candidates) is set
  type: list
  elements: dict
  version_added: "2.2.0"
  contains:
    node:
      description: The node tested.
      type: str
      returned: always
    fits:
      description:
        - Whether the surviving nodes have the RAM. C(none) means it could not
          be determined, which is not the same as yes.
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
    target_ram_mb:
      description: VM RAM on the node being tested.
      type: int
      returned: always
    online_ram_mb:
      description: VM RAM across the cluster's online nodes.
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
  version_added: "2.2.0"
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
    fetch_nodes,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
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
        supports_check_mode=True
    )

    client = get_vergeos_client(module)

    try:
        # 'clusters' keeps the meaning it has had since 1.0.0 -- the clusters
        # table. The capacity figures live in a DIFFERENT table with different
        # keys, so they arrive under their own name rather than quietly
        # changing what an existing playbook reads.
        clusters = [dict(cluster) for cluster in client.clusters.list()]
        status = fetch_cluster_status(client)

        # fetch_nodes names its projection. The default one happens to carry
        # everything needed today, but 'running' is absent from fields=most
        # and fields=all, and a capacity gate whose peers all read as stopped
        # is a gate that refuses everything.
        nodes = fetch_nodes(client)

        result = dict(
            changed=False,
            clusters=clusters,
            cluster_status=status,
            ram_headroom_mb=sum(
                int(row.get('online_ram') or 0) - int(row.get('used_ram') or 0)
                for row in status),
            no_failover_reservation=sorted(
                name for name in failover_reserved(nodes) if name),
        )

        if module.params['drain_candidates']:
            # One cluster_status row per cluster, so the drain question is
            # asked against the cluster the node is actually in rather than
            # against whichever row came back first.
            by_cluster = {str(row.get('cluster')): row for row in status}
            checks = []
            for name in module.params['drain_candidates']:
                node = next((n for n in nodes if n.get('name') == name), {})
                cluster = node.get('cluster')
                status_row = (by_cluster.get(str(cluster))
                              or (status[0] if status else {}))
                peers = [n for n in nodes
                         if cluster is None or n.get('cluster') == cluster]
                checks.append(drain_capacity(status_row, peers, name))
            result['drain_checks'] = checks

        module.exit_json(**result)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
