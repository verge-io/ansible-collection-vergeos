#!/usr/bin/python

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: network_guard
short_description: Evaluate a network map plan against the blast-radius guard
version_added: "2.3.0"
description:
  - Evaluate a plan from M(vergeio.vergeos.network_map) and return a verdict
    per NIC. The verdict says whether attaching that NIC would be safe. It
    does not attach anything.
  - C(detached) (the default) approves creating the NIC record unconnected
    and records the intended target. C(bubble) approves forcing every NIC
    onto one named vnet, and states that isolation of that vnet is an
    operator assertion. C(live) approves a real target only when the source
    VM is confirmed down.
  - Anything not confirmed down counts as running, including a missing
    power state. The override is a list of VM names and applies only in
    C(live) mode. There is no global override.
  - This is a planning and visualization feature. It does not migrate data,
    create vnets, or attach NICs. Cutover is out of scope.
  - Safety notes, including a proposal that would hold a live source
    gateway, are emitted with the module warning facility. Notes already
    recorded on the plan stay on that plan's C(warnings). This module
    does not return a top-level C(warnings) key.
options:
  plan:
    description:
      - Plan from M(vergeio.vergeos.network_map), or the registered result
        of that task.
    type: dict
    required: true
  source_states:
    description:
      - Mapping of VM name to the source power state.
      - Confirmed down states are C(stopped), C(shutdown), C(off),
        C(poweroff) and C(down). Anything else, including a VM that is
        absent from the mapping, counts as running.
    type: dict
  existing_nics:
    description:
      - NICs that already exist in VergeOS, as a read-only list. A MAC
        owned by a different VM is a refusal. Re-reading the same VM is
        not a conflict.
      - Accepts the output of M(vergeio.vergeos.vm_nic_info) (keys
        C(macaddress), C(machine), C(vnet_name) or C(vnet)), as well as
        generic rows with C(mac_address) or C(mac), C(vm_name) or C(vm),
        and C(network).
      - If a row contains a MAC address without a VM identifier, it is
        treated as an unconfirmed VM conflict and refused.
    type: list
    elements: dict
  attach_mode:
    description:
      - Override the plan's attach mode for this evaluation.
      - Omit to use the plan policy, which defaults to C(detached).
    type: str
    choices: [detached, bubble, live]
  bubble_network:
    description:
      - Vnet name used when I(attach_mode=bubble). Required in that mode
        when the plan does not already name one.
    type: str
  live_attach_running_override:
    description:
      - VM names that may be evaluated as attachable while their source is
        not confirmed down. Honoured only when the mode is C(live).
    type: list
    elements: str
  strict:
    description:
      - Fail the task when any NIC is refused. The failure still carries
        the verdicts.
    type: bool
    default: false
  diagram_path:
    description:
      - Local path for a Mermaid diagram of the verdicts. A DOT file is
        written beside it. Check mode returns the pictures and does not write.
    type: path
notes:
  - Read-only. Always reports C(changed=false). Supports C(check_mode).
  - Does not migrate data and does not apply the plan.
  - Trunk refusals in the plan stay refusals. The guard does not rehabilitate them.
  - Safety notes are emitted with the module warning facility, not a
    top-level C(warnings) return key.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Evaluate the plan while the source is still running
  vergeio.vergeos.network_guard:
    plan: "{{ plan }}"
    source_states:
      web01: running
    attach_mode: live
  register: guard

- name: The default detached evaluation
  vergeio.vergeos.network_guard:
    plan: "{{ plan.plan }}"
    diagram_path: /tmp/network-guard.mmd
'''

RETURN = r'''
results:
  description: One verdict per plan decision. C(verdict) is approve or refuse.
  returned: success
  type: list
  elements: dict
  sample:
    - vm: web01
      nic: net0
      verdict: refuse
      attach_to: null
      intended_target: prod-web
      why: source is running
refusals:
  description: Rows whose verdict is refuse.
  returned: success
  type: list
  elements: dict
ok:
  description: False when any verdict is refuse.
  returned: success
  type: bool
attach_mode:
  description: The mode this evaluation used.
  returned: success
  type: str
applied:
  description: Always false.
  returned: success
  type: bool
diagram_mermaid:
  description: Mermaid flowchart of the per-VM verdicts.
  returned: success
  type: str
diagram_dot:
  description: Graphviz DOT of the same verdicts.
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
    guard_dot,
    guard_mermaid,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.netguard import (
    GuardError,
    assess,
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            plan=dict(type='dict', required=True),
            source_states=dict(type='dict'),
            existing_nics=dict(type='list', elements='dict'),
            attach_mode=dict(type='str', choices=['detached', 'bubble', 'live']),
            bubble_network=dict(type='str'),
            live_attach_running_override=dict(type='list', elements='str'),
            strict=dict(type='bool', default=False),
            diagram_path=dict(type='path'),
        ),
        supports_check_mode=True,
    )
    try:
        options = {}
        if module.params['attach_mode']:
            options['attach_mode'] = module.params['attach_mode']
        if module.params['bubble_network']:
            options['bubble_network'] = module.params['bubble_network']
        if module.params['live_attach_running_override']:
            options['live_attach_running_override'] = module.params['live_attach_running_override']
        result = assess(
            module.params['plan'] or {},
            module.params['source_states'] or {},
            module.params['existing_nics'] or [],
            options,
        )
        deliver(
            module,
            result,
            guard_mermaid(result),
            guard_dot(result),
            strict=module.params['strict'],
        )
    except GuardError as exc:
        module.fail_json(msg=str(exc))
    except OSError as exc:
        module.fail_json(msg="could not write diagram: %s" % exc)
    except Exception as exc:
        module.fail_json(msg="Unexpected error: %s" % exc)


if __name__ == '__main__':
    main()
