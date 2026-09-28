#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vnet_rule_info
short_description: Gather information about firewall rules on a VergeOS network
version_added: "2.2.0"
description:
  - List the firewall rules of a virtual network, optionally filtered by
    name or direction.
  - System rules are included and flagged (C(system_rule)) so callers can
    exclude them — for example when purging rules a policy does not
    declare (exact enforcement).
  - Also reports whether the network has un-applied rule changes pending
    (RV(needs_rule_apply)).
options:
  network:
    description:
      - Name of the network (vnet).
    type: str
    required: true
  name:
    description:
      - Only return the rule with this name.
    type: str
  direction:
    description:
      - Only return rules matching this direction.
    type: str
    choices: [ incoming, outgoing ]
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: All rules on a network
  vergeio.vergeos.vnet_rule_info:
    network: Internal
  register: rules

- name: Non-system incoming rules only
  vergeio.vergeos.vnet_rule_info:
    network: External
    direction: incoming
  register: incoming

- name: Names of rules a policy does not manage
  ansible.builtin.set_fact:
    unmanaged: >-
      {{ incoming.rules | rejectattr('system_rule')
         | rejectattr('name', 'in', managed_names) | map(attribute='name')
         | list }}
'''

RETURN = r'''
rules:
  description: Matching rules on the network.
  returned: always
  type: list
  elements: dict
  sample:
    - key: 12
      name: "allow-ssh-mgmt"
      direction: "incoming"
      rule_action: "accept"
      protocol: "tcp"
      source_ip: "192.0.2.0/24"
      destination_ports: "22"
      enabled: true
      order: 4
      system_rule: false
needs_rule_apply:
  description: Whether the network has rule changes waiting for an apply.
  returned: always
  type: bool
  sample: false
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    resolve_one,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


def rule_result(rule):
    data = dict(rule)
    return {
        'key': data.get('$key'),
        'name': data.get('name'),
        'direction': data.get('direction'),
        'rule_action': data.get('action'),
        'protocol': data.get('protocol'),
        'interface': data.get('interface'),
        'source_ip': data.get('source_ip'),
        'source_ports': data.get('source_ports'),
        'destination_ip': data.get('destination_ip'),
        'destination_ports': data.get('destination_ports'),
        'target_ip': data.get('target_ip'),
        'target_ports': data.get('target_ports'),
        'enabled': bool(data.get('enabled', True)),
        'log': bool(data.get('log', False)),
        'statistics': bool(data.get('statistics', False)),
        'order': data.get('orderid'),
        'description': data.get('description'),
        'system_rule': bool(data.get('system_rule', False)),
    }


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        network=dict(type='str', required=True),
        name=dict(type='str'),
        direction=dict(type='str', choices=['incoming', 'outgoing']),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    name = module.params.get('name')
    direction = module.params.get('direction')

    try:
        try:
            # resolve_one, not get(name=): an _info module that quietly
            # reports a DIFFERENT network's firewall rules is worse than one
            # that refuses, because the answer looks right (#72/#85).
            network = resolve_one(module, client.networks,
                                  module.params['network'], 'network')
        except NotFoundError:
            module.fail_json(msg="Network '%s' not found"
                             % module.params['network'])

        rules = []
        for rule in network.rules.list():
            r = rule_result(rule)
            if name is not None and r['name'] != name:
                continue
            if direction is not None and r['direction'] != direction:
                continue
            rules.append(r)

        net = dict(network)
        module.exit_json(changed=False, rules=rules,
                         needs_rule_apply=bool(net.get('need_fw_apply')))

    except (AuthenticationError, ValidationError, APIError,
            VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg="Unexpected error: %s" % e)


if __name__ == '__main__':
    main()
