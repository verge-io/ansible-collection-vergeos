#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vnet_rule
short_description: Manage firewall rules on a VergeOS network
version_added: "2.2.0"
description:
  - Create, update, and delete firewall rules on a VergeOS virtual
    network, so network policy can be described declaratively.
  - Rules are matched by O(name) within the network. System rules are
    never modified or deleted.
  - Rule changes only take effect after the network applies them; by
    default the module triggers that apply after any change
    (O(apply)).
options:
  network:
    description:
      - Name of the network (vnet) the rule belongs to.
      - The network must already exist (see the M(vergeio.vergeos.network)
        module).
    type: str
    required: true
  name:
    description:
      - Rule name, unique within the network; the match key for
        idempotence.
    type: str
    required: true
  state:
    description:
      - Whether the rule should exist.
    type: str
    choices: [ present, absent ]
    default: present
  direction:
    description:
      - Traffic direction the rule matches.
    type: str
    choices: [ incoming, outgoing ]
    default: incoming
  rule_action:
    description:
      - What the rule does with matching traffic.
      - C(translate) and C(route) use O(target_ip)/O(target_ports).
    type: str
    choices: [ accept, drop, reject, translate, route ]
    default: accept
  protocol:
    description:
      - Protocol to match.
    type: str
    choices: [ tcp, udp, tcpudp, icmp, any ]
    default: any
  interface:
    description:
      - Interface the rule binds to.
    type: str
    choices: [ auto, router, dmz, wireguard, any ]
    default: auto
  source_ip:
    description:
      - Source IP, CIDR, or special value (e.g. C(vnetself)).
    type: str
  source_ports:
    description:
      - Source ports (e.g. C(22), C(1024-65535), C(80,443)).
    type: str
  destination_ip:
    description:
      - Destination IP, CIDR, or special value.
    type: str
  destination_ports:
    description:
      - Destination ports.
    type: str
  target_ip:
    description:
      - Target IP for C(translate)/C(route) actions.
    type: str
  target_ports:
    description:
      - Target ports for port translation.
    type: str
  enabled:
    description:
      - Whether the rule is enabled.
    type: bool
    default: true
  log:
    description:
      - Log matching traffic.
    type: bool
    default: false
  statistics:
    description:
      - Track packet/byte statistics for the rule.
    type: bool
    default: false
  order:
    description:
      - Explicit position in the rule list.
      - Only changed when specified.
    type: int
  pin:
    description:
      - Pin the rule to the top or bottom of the list (creation only).
    type: str
    choices: [ top, bottom ]
  description:
    description:
      - Rule description.
    type: str
  apply:
    description:
      - Apply the network's rules after a change so it takes effect.
      - Set to C(false) when batching several rule tasks; apply once at
        the end (e.g. with a final rule task, or the network's apply
        action).
      - No apply is triggered when nothing changed, and the apply is
        skipped (reported in RV(actions)) when the network is not
        running — a stopped network picks the rules up when it starts.
    type: bool
    default: true
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Allow SSH from the management subnet
  vergeio.vergeos.vnet_rule:
    network: Internal
    name: allow-ssh-mgmt
    direction: incoming
    rule_action: accept
    protocol: tcp
    source_ip: 192.0.2.0/24
    destination_ports: "22"
    state: present

- name: Port-forward external 8443 to an internal host
  vergeio.vergeos.vnet_rule:
    network: External
    name: fwd-app-tls
    direction: incoming
    rule_action: translate
    protocol: tcp
    destination_ports: "8443"
    target_ip: 10.0.0.15
    target_ports: "443"
    state: present

- name: Batch several rules, applying only once
  vergeio.vergeos.vnet_rule:
    network: Internal
    name: "{{ item.name }}"
    protocol: tcp
    destination_ports: "{{ item.ports }}"
    apply: "{{ item.apply | default(false) }}"
    state: present
  loop:
    - { name: allow-http, ports: "80" }
    - { name: allow-https, ports: "443" }
    - { name: allow-dns, ports: "53", apply: true }

- name: Remove a rule
  vergeio.vergeos.vnet_rule:
    network: Internal
    name: allow-ssh-mgmt
    state: absent
'''

RETURN = r'''
rule:
  description: State of the rule after the module ran.
  returned: when state is present
  type: dict
  sample:
    key: 12
    name: "allow-ssh-mgmt"
    direction: "incoming"
    rule_action: "accept"
    protocol: "tcp"
    source_ip: "192.0.2.0/24"
    destination_ports: "22"
    enabled: true
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
  sample: ["created rule 'allow-ssh-mgmt'", "applied rules on 'Internal'"]
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    resolve_one,
    VNET_STATUS_FIELDS,
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

# Module param -> raw API field. rule_action is named to avoid colliding
# with the play keyword 'action'.
# Module parameter -> API column on the vnet_rules table, every name checked
# against a live rule row on VergeOS 26.1.8 (35 columns). One real rename:
#
#     rule_action  ->  action        ('action' is not usable as an Ansible
#                                      option name without confusion)
#
# `order` is the interesting one, and it is NOT in this map on purpose. The
# column is `orderid`, and the two code paths reach it differently:
#
#     create   network.rules.create(order=N)   -> the SDK writes body['orderid']
#     update   network.rules.update(orderid=N) -> raw kwargs passthrough
#
# Both are correct, by different routes, which is exactly the shape that makes
# a reader "fix" one of them. Checked on the SDK source and confirmed against
# the live table rather than assumed -- see test_field_contracts.py, which
# pins both halves.
UPDATE_FIELD_MAP = {
    'direction': 'direction',
    'rule_action': 'action',
    'protocol': 'protocol',
    'interface': 'interface',
    'source_ip': 'source_ip',
    'source_ports': 'source_ports',
    'destination_ip': 'destination_ip',
    'destination_ports': 'destination_ports',
    'target_ip': 'target_ip',
    'target_ports': 'target_ports',
    'enabled': 'enabled',
    'log': 'log',
    'statistics': 'statistics',
    'description': 'description',
}

# The create path sets these unconditionally (they all carry defaults) and the
# rest only when given. Same columns, plus the identity.
CREATE_PARAM_MAP = dict(UPDATE_FIELD_MAP, name='name')

IDENTITY_PARAMS = ('name',)

# `order` maps to `orderid` on both paths; see the note above. Held separately
# so the guard can assert the pairing without the map claiming a column that
# neither path writes under that name.
ORDER_API_FIELD = 'orderid'


COMPARISON_FIELDS = tuple(sorted(set(UPDATE_FIELD_MAP.values())
                                 | {ORDER_API_FIELD, 'name', '$key',
                                    'system_rule'}))


def get_network(module, client):
    """The vnet this rule belongs to, refusing to guess between duplicates.

    ``resolve_one`` rather than the SDK's ``get(name=...)``, for the reasons in
    module_utils/vergeos.py (#72/#85). It matters more here than most: the
    wrong network means firewall rules applied to something that was never
    named, and on this platform the network carrying the UI is one bad match
    away from the one you meant.
    """
    try:
        return resolve_one(module, client.networks, module.params['network'],
                           'network', fields=VNET_STATUS_FIELDS)
    except NotFoundError:
        module.fail_json(msg="Network '%s' not found" % module.params['network'])


def find_rule(network, name):
    """One rule by name within this network, or None.

    The columns are named rather than left to the SDK's default projection: a
    column compared but not fetched reads as None, the comparison always
    differs, and the rule is rewritten on every run (#18, and #92 again).
    """
    for rule in network.rules.list(fields=list(COMPARISON_FIELDS)):
        if dict(rule).get('name') == name:
            return rule
    return None


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


def create_rule(module, network, actions):
    params = module.params
    actions.append("created rule '%s'" % params['name'])
    if module.check_mode:
        return None

    create_args = {
        'name': params['name'],
        'direction': params['direction'],
        'action': params['rule_action'],
        'protocol': params['protocol'],
        'interface': params['interface'],
        'enabled': params['enabled'],
        'log': params['log'],
        'statistics': params['statistics'],
    }
    for param in ('source_ip', 'source_ports', 'destination_ip',
                  'destination_ports', 'target_ip', 'target_ports',
                  'description'):
        if params.get(param):
            create_args[param] = params[param]
    if params.get('pin'):
        create_args['pin'] = params['pin']
    if params.get('order') is not None:
        create_args['order'] = params['order']

    return network.rules.create(**create_args)


def update_rule(module, network, rule, actions):
    params = module.params
    current = dict(rule)
    changes = {}

    for param, field in UPDATE_FIELD_MAP.items():
        value = params.get(param)
        if value is None:
            continue
        current_value = current.get(field)
        if isinstance(value, bool):
            current_value = bool(current_value)
        if current_value != value:
            changes[field] = value

    if params.get('order') is not None:
        if int(current.get('orderid') or 0) != params['order']:
            changes['orderid'] = params['order']

    if not changes:
        return False, rule

    actions.append("updated rule '%s': %s"
                   % (params['name'], ', '.join(sorted(changes))))
    if module.check_mode:
        return True, rule
    rule = network.rules.update(current['$key'], **changes)
    return True, rule


def apply_rules(module, network, actions):
    # Refreshing rules on a stopped vnet is rejected by the API
    # ("vNet is not running"); a stopped network picks the rules up when
    # it starts, so skipping is the correct converging behavior.
    if not bool(dict(network).get('running', False)):
        actions.append("apply skipped: network '%s' not running "
                       '(rules take effect on start)'
                       % module.params['network'])
        return
    actions.append("applied rules on '%s'" % module.params['network'])
    if not module.check_mode:
        network.apply_rules()


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        network=dict(type='str', required=True),
        name=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        direction=dict(type='str', default='incoming',
                       choices=['incoming', 'outgoing']),
        rule_action=dict(type='str', default='accept',
                         choices=['accept', 'drop', 'reject', 'translate', 'route']),
        protocol=dict(type='str', default='any',
                      choices=['tcp', 'udp', 'tcpudp', 'icmp', 'any']),
        interface=dict(type='str', default='auto',
                       choices=['auto', 'router', 'dmz', 'wireguard', 'any']),
        source_ip=dict(type='str'),
        source_ports=dict(type='str'),
        destination_ip=dict(type='str'),
        destination_ports=dict(type='str'),
        target_ip=dict(type='str'),
        target_ports=dict(type='str'),
        enabled=dict(type='bool', default=True),
        log=dict(type='bool', default=False),
        statistics=dict(type='bool', default=False),
        order=dict(type='int'),
        pin=dict(type='str', choices=['top', 'bottom']),
        description=dict(type='str'),
        apply=dict(type='bool', default=True),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    actions = []

    try:
        network = get_network(module, client)
        rule = find_rule(network, module.params['name'])

        if rule is not None and bool(dict(rule).get('system_rule', False)):
            module.fail_json(msg="'%s' is a system rule and cannot be managed"
                                 % module.params['name'])

        if module.params['state'] == 'absent':
            if rule is None:
                module.exit_json(changed=False, actions=actions,
                                 msg="Rule '%s' does not exist"
                                     % module.params['name'])
            actions.append("deleted rule '%s'" % module.params['name'])
            if not module.check_mode:
                network.rules.delete(dict(rule)['$key'])
            if module.params['apply']:
                apply_rules(module, network, actions)
            module.exit_json(changed=True, actions=actions)

        if rule is None:
            rule = create_rule(module, network, actions)
            changed = True
        else:
            changed, rule = update_rule(module, network, rule, actions)

        if changed and module.params['apply']:
            apply_rules(module, network, actions)

        result = {'name': module.params['name']} if rule is None \
            else rule_result(rule)
        module.exit_json(changed=changed, actions=actions, rule=result)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
