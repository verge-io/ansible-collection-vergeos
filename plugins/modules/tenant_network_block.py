#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: tenant_network_block
short_description: Assign network blocks (CIDRs) to VergeOS tenants
version_added: "2.2.0"
description:
  - Assign or remove routed network blocks on a tenant, completing the
    tenant-per-customer story (tenant lifecycle is the C(tenant)
    module; this module manages what gets routed to it).
  - Blocks are matched by O(tenant) and O(cidr).
  - A block's source network is fixed at assignment. If an existing
    block was assigned from a different network than O(network) names,
    the module fails loudly instead of guessing - remove and re-add the
    block to move it.
options:
  tenant:
    description:
      - Name of the tenant.
    type: str
    required: true
  cidr:
    description:
      - Network block in CIDR notation (for example C(192.0.2.0/24)),
        the match key for idempotence within the tenant.
    type: str
    required: true
  state:
    description:
      - Whether the block should be assigned.
    type: str
    choices: [ present, absent ]
    default: present
  network:
    description:
      - Name of the network the block is assigned from. Required when
        O(state=present).
    type: str
  description:
    description:
      - Free-form description, applied at assignment only (the API has
        no update path for an assigned block).
    type: str
notes:
  - Supports C(check_mode).
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Route a customer block to their tenant
  vergeio.vergeos.tenant_network_block:
    tenant: customer-a
    network: External
    cidr: 203.0.113.0/28
    description: customer-a public block
    state: present

- name: Remove the block
  vergeio.vergeos.tenant_network_block:
    tenant: customer-a
    cidr: 203.0.113.0/28
    state: absent
'''

RETURN = r'''
network_block:
  description: State of the block after the module ran.
  returned: when state is present
  type: dict
  sample:
    key: 4
    cidr: "203.0.113.0/28"
    network: 3
    description: "customer-a public block"
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
  sample: ["assigned block 203.0.113.0/28 to tenant 'customer-a'"]
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


TENANT_FIELDS = ['$key', 'name']
NETWORK_FIELDS = ['$key', 'name']


def get_tenant(module, client):
    """The named tenant, refusing to guess between duplicates (#72/#85)."""
    try:
        return resolve_one(module, client.tenants, module.params['tenant'],
                           'tenant', fields=TENANT_FIELDS)
    except NotFoundError:
        module.fail_json(msg="Tenant '%s' not found" % module.params['tenant'])


def resolve_network_key(module, client, name):
    try:
        network = resolve_one(module, client.networks, name, 'network',
                              fields=NETWORK_FIELDS)
    except NotFoundError:
        module.fail_json(msg="Network '%s' not found" % name)
    return dict(network)['$key']


def find_block(tenant, cidr):
    matches = tenant.network_blocks.list(cidr=cidr)
    return matches[0] if matches else None


def block_result(block):
    data = dict(block)
    return {
        'key': data.get('$key'),
        'cidr': data.get('cidr'),
        'network': data.get('vnet'),
        'description': data.get('description') or '',
    }


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        tenant=dict(type='str', required=True),
        cidr=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        network=dict(type='str'),
        description=dict(type='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        required_if=[('state', 'present', ['network'])],
    )

    client = get_vergeos_client(module)
    actions = []
    params = module.params

    try:
        tenant = get_tenant(module, client)
        block = find_block(tenant, params['cidr'])

        if params['state'] == 'absent':
            if block is None:
                module.exit_json(changed=False, actions=actions)
            actions.append("removed block %s from tenant '%s'"
                           % (params['cidr'], params['tenant']))
            if not module.check_mode:
                tenant.network_blocks.delete(dict(block)['$key'])
            module.exit_json(changed=True, actions=actions)

        # state: present
        net_key = resolve_network_key(module, client, params['network'])

        if block is not None:
            current_net = dict(block).get('vnet')
            if current_net != net_key:
                module.fail_json(
                    msg="block %s on tenant '%s' is assigned from network "
                        "key %s, not '%s'; a block cannot be moved - remove "
                        "it (state: absent) and re-add it from the right "
                        "network." % (params['cidr'], params['tenant'],
                                      current_net, params['network']))
            module.exit_json(changed=False, actions=actions,
                             network_block=block_result(block))

        actions.append("assigned block %s to tenant '%s'"
                       % (params['cidr'], params['tenant']))
        if module.check_mode:
            module.exit_json(changed=True, actions=actions)

        created = tenant.network_blocks.create(
            cidr=params['cidr'], network=net_key,
            description=params.get('description') or '')
        module.exit_json(changed=True, actions=actions,
                         network_block=block_result(created))

    except (AuthenticationError, ValidationError, APIError,
            VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except NotFoundError as e:
        module.fail_json(msg="Resource not found: %s" % e)
    except Exception as e:
        module.fail_json(msg="Unexpected error: %s" % e)


if __name__ == '__main__':
    main()
