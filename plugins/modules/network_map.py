#!/usr/bin/python

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: network_map
short_description: Build a read-only network map plan against existing VergeOS vnets
version_added: "2.3.0"
description:
  - Decide which VergeOS vnet each captured NIC belongs on, and which vnets
    a later run would propose to add.
  - Existing vnets are the list the caller passes in I(networks), usually
    the C(networks) list from M(vergeio.vergeos.network_info). This module does not call the API.
  - Resolution is an ordered chain. Each resolver answers or abstains, and
    the first answer wins. The default order is C(explicit), C(alias),
    C(vlan), C(name). C(synthesize) is omitted until the policy adds it,
    so proposing new vnets is opt-in.
  - Ambiguity is a refusal that names every candidate and stops the chain.
  - The C(vlan) resolver matches a Proxmox VLAN tag to a network whose
    C(layer2_type) is C(vlan). The id is C(vlan_id) when that field is
    set, otherwise C(layer2_id). A VXLAN network's C(layer2_id) is a
    VXLAN id and is left unmatched. When C(layer2_type) is absent,
    C(layer2_id) is left unused and an explicit C(vlan_id) (or C(vlan)
    / C(vlanid)) still matches.
  - The C(name) resolver matches a tagged access port, such as
    C(vmbr7.3001), by its segment or SDN vnet. The bare bridge name is
    reserved for the untagged bridge segment.
  - A synthesized entry in RV(creates) is a proposal. It is shaped like
    M(vergeio.vergeos.network) parameters, including C(interface_network)
    and C(rate_limit) when the policy and the captured NIC supply them.
    Those values are not applied. This module does not create, modify or
    attach a vnet.
  - Trunk NICs are refused. A trunk has no single vnet. Trunk translation
    is out of scope.
  - Returns a Mermaid diagram and a Graphviz DOT diagram of the proposed
    source-to-VergeOS mapping.
  - This is a planning and visualization feature. It does not migrate data
    or change VergeOS or the source hypervisor. Cutover, applying a guarded
    plan, is out of scope.
options:
  vms:
    description:
      - Mapping of VM name to a list of NICs from
        M(vergeio.vergeos.proxmox_net_config).
    type: dict
    required: true
  segments:
    description:
      - Segment facts from M(vergeio.vergeos.proxmox_host_network), the
        C(segments) mapping.
    type: dict
  networks:
    description:
      - Existing VergeOS vnets. Pass the C(networks) list from
        M(vergeio.vergeos.network_info).
      - Used only as read-only input. Nothing in the list is modified.
    type: list
    elements: dict
  policy:
    description:
      - Mapping policy. C(order) selects and sequences resolvers.
      - C(synthesize.interface_network) is the proposed uplink, the name
        of an existing VergeOS vnet. It is recorded on the proposal and
        not set.
      - C(on_unmapped) is C(refuse) (default), C(detach) or C(bubble).
    type: dict
  strict:
    description:
      - Fail the task when any NIC is refused. The failure still carries
        the plan. Default is to return the plan with C(ok=false).
    type: bool
    default: false
  diagram_path:
    description:
      - Local path for the Mermaid mapping diagram. A DOT file is written
        beside it. Check mode returns the pictures and does not write.
    type: path
notes:
  - Read-only. Always reports C(changed=false). Supports C(check_mode).
  - Does not call M(vergeio.vergeos.network). Uplink and rate limit are
    proposed values only.
  - Trunk ports are refused with an error that names the trunk VLANs.
  - Applying the plan during migration is out of scope.
  - Safety notes are emitted with the module warning facility and recorded
    on RV(plan.warnings). This module does not return a top-level
    C(warnings) key.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Read existing vnets
  vergeio.vergeos.network_info:
  register: vnets

- name: Plan without creating anything
  vergeio.vergeos.network_map:
    vms: "{{ captured_vms }}"
    segments: "{{ topology.segments }}"
    networks: "{{ vnets.networks }}"
    policy:
      order: [explicit, vlan, name, synthesize]
      synthesize:
        interface_network: "ext1 Switch"
        name_template: "{segment}"
    diagram_path: /tmp/network-map.mmd
  register: plan
'''

RETURN = r'''
plan:
  description:
    - The plan. RV(creates) inside it are proposals, not created vnets.
  returned: success
  type: dict
  contains:
    warnings:
      description:
        - Safety notes, such as a proposal that names no uplink.
        - A warning is not a refusal. The same notes are emitted with the module warning facility.
      returned: success
      type: list
      elements: str
decisions:
  description: One row per NIC. C(action) is map, create, detach, bubble or refuse.
  returned: success
  type: list
  elements: dict
creates:
  description:
    - Proposed vnet arguments. The same list as RV(proposals).
    - C(interface_network) and C(rate_limit) are included when known.
      They are not sent to VergeOS.
  returned: success
  type: list
  elements: dict
proposals:
  description: Same list as RV(creates). Named proposals so it is obvious nothing was created.
  returned: success
  type: list
  elements: dict
refusals:
  description: Decisions whose action is refuse, including trunk NICs.
  returned: success
  type: list
  elements: dict
ok:
  description: False when any NIC was refused.
  returned: success
  type: bool
applied:
  description: Always false.
  returned: success
  type: bool
diagram_mermaid:
  description: Mermaid flowchart of the source-to-VergeOS proposal.
  returned: success
  type: str
diagram_dot:
  description: Graphviz DOT of the same mapping.
  returned: success
  type: str
diagram_written:
  description: Whether the diagram files were written. False in check mode.
  returned: success
  type: bool
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.net_diagram import (
    deliver,
    mapping_dot,
    mapping_mermaid,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.netmap import (
    PolicyError,
    plan,
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            vms=dict(type='dict', required=True),
            segments=dict(type='dict'),
            networks=dict(type='list', elements='dict'),
            policy=dict(type='dict'),
            strict=dict(type='bool', default=False),
            diagram_path=dict(type='path'),
        ),
        supports_check_mode=True,
    )
    try:
        vms = module.params['vms'] or {}
        for name, nics in vms.items():
            if not isinstance(nics, list):
                module.fail_json(msg="vms.%s must be a list of NICs" % name)
        result = plan(
            vms,
            module.params['segments'] or {},
            module.params['networks'] or [],
            module.params['policy'] or {},
        )
        result["plan"] = {
            "policy": result["policy"],
            "decisions": result["decisions"],
            "creates": result["creates"],
            "proposals": result["proposals"],
            "refusals": result["refusals"],
            "warnings": result["warnings"],
            "ok": result["ok"],
            "applied": False,
            "summary": result["summary"],
        }
        deliver(
            module,
            result,
            mapping_mermaid(result),
            mapping_dot(result),
            strict=module.params['strict'],
        )
    except PolicyError as exc:
        module.fail_json(msg=str(exc))
    except OSError as exc:
        module.fail_json(msg="could not write diagram: %s" % exc)
    except Exception as exc:
        module.fail_json(msg="Unexpected error: %s" % exc)


if __name__ == '__main__':
    main()
