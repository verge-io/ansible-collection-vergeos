#!/usr/bin/python

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: proxmox_host_network
short_description: Parse captured Proxmox host networks into topology segments
version_added: "2.3.0"
description:
  - Parse captured Proxmox host network configuration into segment facts.
  - Input is the text of C(/etc/network/interfaces) and, when SDN is in
    use, the text of C(zones.cfg), C(vnets.cfg) and C(subnets.cfg). The
    operator captured those files. This module does not SSH to Proxmox
    and does not connect to VergeOS.
  - Segment ids match M(vergeio.vergeos.proxmox_net_config), so a NIC's
    segment looks up the facts directly.
  - Each segment carries C(confidence). A bridge known only by name is
    C(name-only). SDN facts that include a CIDR are C(authoritative).
  - Returns a Mermaid diagram and a Graphviz DOT diagram of the topology.
  - This is a planning and visualization feature. It does not migrate
    data or change the source hypervisor or VergeOS.
  - Host topology is fixture-tested. This collection has no Proxmox node
    to test against.
options:
  interfaces:
    description:
      - Text of C(/etc/network/interfaces).
    type: str
    default: ""
  sdn:
    description:
      - Mapping with optional keys C(zones.cfg), C(vnets.cfg) and
        C(subnets.cfg). Values are the file contents.
    type: dict
  nics:
    description:
      - Parsed NICs to draw onto the topology. Typically the C(nics)
        list from M(vergeio.vergeos.proxmox_net_config).
    type: list
    elements: dict
  diagram_path:
    description:
      - Local path for the Mermaid topology diagram. A DOT file is written
        beside it. Check mode returns the pictures and does not write.
    type: path
notes:
  - Read-only. Always reports C(changed=false). Supports C(check_mode).
  - Does not migrate data and does not apply changes.
  - Trunk translation is out of scope.
  - Cutover, applying a plan to VergeOS, is out of scope.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Parse captured host network files
  vergeio.vergeos.proxmox_host_network:
    interfaces: "{{ lookup('file', 'interfaces.txt') }}"
    sdn:
      zones.cfg: "{{ lookup('file', 'zones.cfg') }}"
      vnets.cfg: "{{ lookup('file', 'vnets.cfg') }}"
      subnets.cfg: "{{ lookup('file', 'subnets.cfg') }}"
    nics: "{{ parsed.nics }}"
    diagram_path: /tmp/topology.mmd
  register: topology
'''

RETURN = r'''
segments:
  description:
    - Map of segment id to facts. This is what the map module consumes.
  returned: success
  type: dict
bridges:
  description: Linux and OVS bridges found in the interfaces file.
  returned: success
  type: dict
bonds:
  description: Bonds found in the interfaces file.
  returned: success
  type: dict
vlan_interfaces:
  description: VLAN sub-interfaces and OVS access ports.
  returned: success
  type: dict
sdn:
  description: Parsed SDN zones, vnets and subnets.
  returned: success
  type: dict
warnings:
  description: Present when the interfaces file sources other files that were not provided.
  returned: when the interfaces file has a source line
  type: list
  elements: str
diagram_mermaid:
  description: Mermaid flowchart of the source topology.
  returned: success
  type: str
diagram_dot:
  description: Graphviz DOT of the source topology.
  returned: success
  type: str
diagram_written:
  description: Whether the diagram files were written. False in check mode.
  returned: success
  type: bool
applied:
  description: Always false. This module changes nothing.
  returned: success
  type: bool
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.net_diagram import (
    deliver,
    topology_dot,
    topology_mermaid,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.pve_topology import (
    analyse,
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            interfaces=dict(type='str', default=''),
            sdn=dict(type='dict'),
            nics=dict(type='list', elements='dict'),
            diagram_path=dict(type='path'),
        ),
        supports_check_mode=True,
    )
    try:
        result = analyse({
            "interfaces": module.params['interfaces'] or "",
            "sdn": module.params['sdn'] or {},
        })
        result["ok"] = True
        nics = module.params['nics'] or []
        deliver(
            module,
            result,
            topology_mermaid(result, nics),
            topology_dot(result, nics),
        )
    except OSError as exc:
        module.fail_json(msg="could not write diagram: %s" % exc)
    except Exception as exc:
        module.fail_json(msg="Unexpected error: %s" % exc)


if __name__ == '__main__':
    main()
