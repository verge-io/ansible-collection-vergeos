#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: tenant_info
short_description: Gather information about VergeOS tenants
version_added: "2.1.0"
description:
  - Retrieve tenants with their settings, power state, and (per tenant)
    node and storage allocations.
options:
  name:
    description:
      - Name of a single tenant to query.
      - When omitted, all tenants are returned.
    type: str
  include_allocations:
    description:
      - Include each tenant's node and storage allocations.
      - Costs two extra API calls per tenant; disable for large fleets
        when only the tenant list is needed.
    type: bool
    default: true
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: All tenants with allocations
  vergeio.vergeos.tenant_info:
  register: tenants

- name: One tenant
  vergeio.vergeos.tenant_info:
    name: acme
  register: acme

- name: Fast fleet listing without allocations
  vergeio.vergeos.tenant_info:
    include_allocations: false
  register: fleet
'''

RETURN = r'''
tenants:
  description: List of tenants (one element when O(name) is given).
  returned: always
  type: list
  elements: dict
  sample:
    - key: 7
      name: "acme"
      description: "ACME Corp production tenant"
      running: true
      status: "online"
      is_snapshot: false
      nodes:
        - name: "acme-node1"
          cpu_cores: 8
          ram_gb: 32.0
          running: true
      storage:
        - tier: 1
          provisioned_gb: 500.0
          used_gb: 120.5
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
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

GB = 1073741824


def describe_tenant(tenant, include_allocations):
    data = dict(tenant)
    result = {
        'key': data.get('$key'),
        'name': data.get('name'),
        'description': data.get('description'),
        'url': data.get('url'),
        'note': data.get('note'),
        'expose_cloud_snapshots': bool(data.get('expose_cloud_snapshots', False)),
        'allow_branding': bool(data.get('allow_branding', False)),
        'running': bool(data.get('running', False)),
        'status': data.get('status'),
        'state': data.get('state'),
        'is_snapshot': bool(data.get('is_snapshot', False)),
    }
    if include_allocations:
        result['nodes'] = [
            {
                'name': dict(n).get('name'),
                'cpu_cores': dict(n).get('cpu_cores'),
                'ram_gb': round(int(dict(n).get('ram') or 0) / 1024.0, 2),
                'running': bool(dict(n).get('running', False)),
            }
            for n in tenant.nodes.list()
        ]
        result['storage'] = [
            {
                'tier': dict(s).get('tier_number'),
                'provisioned_gb': round(int(dict(s).get('provisioned') or 0) / float(GB), 2),
                'used_gb': round(int(dict(s).get('used') or 0) / float(GB), 2),
            }
            for s in tenant.storage.list()
        ]
    return result


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
        include_allocations=dict(type='bool', default=True),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    include_allocations = module.params['include_allocations']

    try:
        if module.params.get('name'):
            try:
                tenants = [client.tenants.get(name=module.params['name'])]
            except NotFoundError:
                tenants = []
        else:
            tenants = client.tenants.list()

        module.exit_json(changed=False, tenants=[
            describe_tenant(t, include_allocations) for t in tenants
        ])

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
