#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: tenant_external_ip
short_description: Assign external IPs to VergeOS tenants
version_added: "2.2.0"
description:
  - Assign or remove single external IP addresses on a tenant,
    completing the tenant-per-customer story alongside
    C(tenant_network_block).
  - IPs are matched by O(tenant) and O(ip).
  - An IP's source network is fixed at assignment. If an existing
    assignment came from a different network than O(network) names, the
    module fails loudly instead of guessing - remove and re-add the IP
    to move it.
options:
  tenant:
    description:
      - Name of the tenant.
    type: str
    required: true
  ip:
    description:
      - The IP address to assign, the match key for idempotence within
        the tenant.
    type: str
    required: true
  state:
    description:
      - Whether the IP should be assigned.
    type: str
    choices: [ present, absent ]
    default: present
  network:
    description:
      - Name of the network the IP is assigned from. Required when
        O(state=present).
    type: str
  hostname:
    description:
      - Optional DNS hostname, applied at assignment only.
    type: str
  description:
    description:
      - Free-form description, applied at assignment only (the API has
        no update path for an assigned IP).
    type: str
notes:
  - Supports C(check_mode).
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Give the tenant a public IP
  vergeio.vergeos.tenant_external_ip:
    tenant: customer-a
    network: External
    ip: 203.0.113.10
    hostname: customer-a-edge
    state: present

- name: Reclaim it
  vergeio.vergeos.tenant_external_ip:
    tenant: customer-a
    ip: 203.0.113.10
    state: absent
'''

RETURN = r'''
external_ip:
  description: State of the assignment after the module ran.
  returned: when state is present
  type: dict
  sample:
    key: 9
    ip: "203.0.113.10"
    network: 3
    hostname: "customer-a-edge"
    description: ""
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
  sample: ["assigned IP 203.0.113.10 to tenant 'customer-a'"]
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


def find_ip(tenant, ip):
    matches = tenant.external_ips.list(ip=ip)
    return matches[0] if matches else None


def ip_result(row):
    data = dict(row)
    return {
        'key': data.get('$key'),
        'ip': data.get('ip'),
        'network': data.get('vnet'),
        'hostname': data.get('hostname') or '',
        'description': data.get('description') or '',
    }


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        tenant=dict(type='str', required=True),
        ip=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        network=dict(type='str'),
        hostname=dict(type='str'),
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
        row = find_ip(tenant, params['ip'])

        if params['state'] == 'absent':
            if row is None:
                module.exit_json(changed=False, actions=actions)
            actions.append("removed IP %s from tenant '%s'"
                           % (params['ip'], params['tenant']))
            if not module.check_mode:
                tenant.external_ips.delete(dict(row)['$key'])
            module.exit_json(changed=True, actions=actions)

        # state: present
        net_key = resolve_network_key(module, client, params['network'])

        if row is not None:
            current_net = dict(row).get('vnet')
            if current_net != net_key:
                module.fail_json(
                    msg="IP %s on tenant '%s' is assigned from network key "
                        "%s, not '%s'; an assignment cannot be moved - "
                        "remove it (state: absent) and re-add it from the "
                        "right network." % (params['ip'], params['tenant'],
                                            current_net, params['network']))
            module.exit_json(changed=False, actions=actions,
                             external_ip=ip_result(row))

        actions.append("assigned IP %s to tenant '%s'"
                       % (params['ip'], params['tenant']))
        if module.check_mode:
            module.exit_json(changed=True, actions=actions)

        created = tenant.external_ips.create(
            ip=params['ip'], network=net_key,
            hostname=params.get('hostname'),
            description=params.get('description') or '')
        module.exit_json(changed=True, actions=actions,
                         external_ip=ip_result(created))

    except (AuthenticationError, ValidationError, APIError,
            VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except NotFoundError as e:
        module.fail_json(msg="Resource not found: %s" % e)
    except Exception as e:
        module.fail_json(msg="Unexpected error: %s" % e)


if __name__ == '__main__':
    main()
