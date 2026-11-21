#!/usr/bin/python

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: proxmox_net_config
short_description: Parse captured Proxmox VM NICs into network segments
version_added: "2.3.0"
description:
  - Parse captured Proxmox VE NIC configuration into segment records.
  - Accepts C(qm config) text or a mapping of C(netN) lines. The text is
    data the operator already captured. This module does not connect to
    Proxmox and does not connect to VergeOS.
  - The segment is the C(bridge, vlan) pair. An unconnected NIC, one with
    no C(bridge), stays unconnected. A trunk is parsed and marked as its
    own segment. Trunk translation is out of scope; the map module refuses
    trunk NICs.
  - This is a planning and visualization feature. It does not migrate data
    or change the source hypervisor or VergeOS.
options:
  config:
    description:
      - Raw C(qm config) text. Only C(netN) lines are read.
      - Mutually exclusive with I(nets).
    type: str
  nets:
    description:
      - Mapping of C(netN) keys to NIC definition strings.
      - Mutually exclusive with I(config).
    type: dict
  diagram_path:
    description:
      - Local path for a Mermaid diagram of the parsed NICs and segments.
      - A Graphviz DOT file is written beside it. Check mode returns the
        pictures and does not write.
    type: path
notes:
  - Read-only. Always reports C(changed=false). Supports C(check_mode).
  - Does not migrate data and does not apply changes.
  - Trunk ports are parsed, not translated.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Parse captured qm config
  vergeio.vergeos.proxmox_net_config:
    config: |
      net0: virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=20,rate=12
      net1: virtio=AA:BB:CC:DD:EE:02,bridge=vmbr0,trunks=10;20
  register: parsed

- name: Write the segment diagram locally
  vergeio.vergeos.proxmox_net_config:
    config: "{{ lookup('file', 'qm-config.txt') }}"
    diagram_path: /tmp/segments.mmd
'''

RETURN = r'''
nics:
  description: Parsed NICs, in numeric netN order.
  returned: success
  type: list
  elements: dict
  sample:
    - key: net0
      mac: "AA:BB:CC:DD:EE:01"
      model: virtio
      bridge: vmbr0
      tag: 20
      trunks: []
      segment: vmbr0.20
      connected: true
      rate_mbyte_s: 12.0
segments:
  description: NICs grouped by segment id. A null segment is unconnected.
  returned: success
  type: list
  elements: dict
  sample:
    - segment: vmbr0.20
      nic_keys:
        - net0
diagram_mermaid:
  description: Mermaid flowchart of the parsed NICs and segments.
  returned: success
  type: str
diagram_dot:
  description: Graphviz DOT of the same picture.
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
from ansible_collections.vergeio.vergeos.plugins.module_utils.pve_net_parse import (
    NetParseError,
    group_segments,
    parse_config,
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            config=dict(type='str'),
            nets=dict(type='dict'),
            diagram_path=dict(type='path'),
        ),
        supports_check_mode=True,
        mutually_exclusive=[('config', 'nets')],
        required_one_of=[('config', 'nets')],
    )
    try:
        payload = module.params['nets'] if module.params['nets'] is not None else module.params['config']
        nics = parse_config(payload)
        segments = group_segments(nics)
        # The parser has no host topology. The diagram is the NIC-to-segment
        # picture, which is the source view this module can draw.
        picture = {"segments": {row["segment"]: {"segment": row["segment"]}
                                for row in segments if row["segment"]}}
        deliver(
            module,
            {"nics": nics, "segments": segments, "ok": True},
            topology_mermaid(picture, nics),
            topology_dot(picture, nics),
        )
    except NetParseError as exc:
        module.fail_json(msg=str(exc))
    except OSError as exc:
        module.fail_json(msg="could not write diagram: %s" % exc)
    except Exception as exc:
        module.fail_json(msg="Unexpected error: %s" % exc)


if __name__ == '__main__':
    main()
