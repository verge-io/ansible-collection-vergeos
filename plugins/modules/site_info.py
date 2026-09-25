#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: site_info
short_description: Gather site preflight facts for a VergeOS system
version_added: "2.2.0"
description:
  - Report what a VergeOS system actually has, so a playbook can adapt to it
    instead of hardcoding a storage tier or a node size.
  - Returns the platform version, the cloud name, the storage tiers present,
    clusters and nodes with the capacity fields used for sizing, and the
    counts and names of networks, VM recipes, and NAS services.
  - Storage tiers come from the C(storage_tiers) table (C(client.storage_tiers)).
    The number a drive or a tenant asks for is the C(tier) column. A tier
    with no drives assigned still has a row, and still appears here.
  - Cluster capacity and drain feasibility stay on M(vergeio.vergeos.cluster_info).
    RV(ram_headroom_mb) is that module's figure, repeated here so a preflight
    can read it without a second call.
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Read-only. Supports C(check_mode) and never reports changed.
  - C(ram) on a node is physical RAM. C(vm_ram) is what the platform makes
    available to VMs. On a measured 26.1.8 node those are 94208 MB and
    68352 MB. C(ram_used) is physical RAM in use, a third number.
  - RV(largest_node_vm_ram_mb) is the largest online physical node's
    C(vm_ram). A new machine has to fit on one node, so this is the ceiling
    a sizing choice can see. It is a figure. The platform's placement rule
    can still refuse a machine that fits in it.
  - RV(ram_headroom_mb) is C(online_ram - used_ram) summed over
    C(cluster_status), the same arithmetic M(vergeio.vergeos.cluster_info)
    returns. It is uncommitted VM RAM with every node up. It is the input
    to a drain check, and the drain check itself stays on that module.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: See what this system has
  vergeio.vergeos.site_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
  register: site

- name: Use the lowest storage tier that exists here
  ansible.builtin.debug:
    msg: >-
      {{ site.cloud_name }} {{ site.os_version }} has tiers
      {{ site.storage_tiers }} and {{ site.ram_headroom_mb }} MB
      of VM RAM headroom. The largest node can host
      {{ site.largest_node_vm_ram_mb }} MB.

- name: Create a tenant on a tier this system has
  vergeio.vergeos.tenant:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: acme
    nodes:
      - name: acme-node1
        cpu_cores: 2
        ram_gb: 4
    storage:
      - tier: "{{ site.storage_tiers | min }}"
        provisioned_gb: 50
    state: present
'''

RETURN = r'''
os_version:
  description: VergeOS OS version (C(client.os_version)).
  returned: always
  type: str
  sample: "26.1.8"
version:
  description: Platform yb version (C(client.version)).
  returned: always
  type: str
  sample: "26.1.8"
cloud_name:
  description: Cloud name of the connected system.
  returned: always
  type: str
  sample: "example-lab"
storage_tiers:
  description:
    - Storage tier numbers present on this system, ascending.
    - Each number is the C(tier) column from C(storage_tiers). A tier with
      no drives assigned is included. An empty list means the table returned
      no tier rows.
  returned: always
  type: list
  elements: int
  sample: [1]
storage_tier_details:
  description:
    - One entry per C(storage_tiers) row that has a C(tier) number, including
      a tier whose C(capacity) is zero.
    - C(capacity) and C(used) are bytes. C($key) is the row key.
  returned: always
  type: list
  elements: dict
  sample:
    - tier: 1
      $key: 1
      description: ""
      capacity: 1099511627776
      used: 10737418240
clusters:
  description:
    - Clusters, with live capacity joined from C(cluster_status).
    - RAM figures are MB. Each cluster's C(ram_headroom_mb) is
      C(online_ram - used_ram) for that cluster.
  returned: always
  type: list
  elements: dict
  sample:
    - name: "cluster1"
      $key: 1
      online_nodes: 2
      total_nodes: 2
      online_ram: 137472
      used_ram: 9216
      ram_headroom_mb: 128256
      online_cores: 64
      used_cores: 5
nodes:
  description:
    - Nodes, reduced to the fields a sizing decision reads.
    - RAM figures are MB. C(ram) is physical, C(vm_ram) is what VMs can use,
      C(ram_used) is physical RAM in use.
    - C(online) is C(none) when the row did not report C(running).
  returned: always
  type: list
  elements: dict
  sample:
    - name: "node1"
      $key: 1
      cluster: 1
      cluster_name: "cluster1"
      ram: 94208
      vm_ram: 68352
      ram_used: 14601
      cores: 32
      physical: true
      online: true
      maintenance: false
ram_headroom_mb:
  description:
    - Uncommitted VM RAM across online nodes, C(online_ram - used_ram),
      summed over every C(cluster_status) row.
    - The same figure M(vergeio.vergeos.cluster_info) returns.
  returned: always
  type: int
  sample: 128256
largest_node_vm_ram_mb:
  description:
    - C(vm_ram) of the largest online physical node, in MB.
    - C(none) when no online node reported RAM.
  returned: always
  type: int
  sample: 69120
counts:
  description: How many networks, VM recipes, and NAS services exist.
  returned: always
  type: dict
  sample:
    networks: 13
    vm_recipes: 32
    nas_services: 2
names:
  description:
    - Names of those objects, sorted. Duplicate names are kept.
    - A row with no name is counted in RV(counts) and omitted here.
  returned: always
  type: dict
  sample:
    networks: ["Core", "DMZ"]
    vm_recipes: ["Ubuntu Server 22.04"]
    nas_services: ["nas1"]
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.site import (
    gather_site,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


def main():
    module = AnsibleModule(
        argument_spec=vergeos_argument_spec(),
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)

    try:
        module.exit_json(changed=False, **gather_site(client))

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
