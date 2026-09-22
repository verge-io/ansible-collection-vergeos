#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: network_info
short_description: Gather information about networks in VergeOS
version_added: "1.0.0"
description:
  - Gather facts about networks in VergeOS.
  - Can retrieve information about all networks or filter by name.
options:
  name:
    description:
      - Name of a specific network to query.
      - If not specified, returns information about all networks.
    type: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Get information about all networks
  vergeio.vergeos.network_info:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
  register: all_networks

- name: Get information about a specific network
  vergeio.vergeos.network_info:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "internal-network"
  register: network_info
'''

RETURN = r'''
networks:
  description: List of networks
  returned: always
  type: list
  elements: dict
  sample:
    - name: "internal-network"
      type: "internal"
      network: "10.0.0.0/24"
      ipaddress: "10.0.0.1"
      dnslist: "8.8.8.8,8.8.4.4"
      dhcp_enabled: true
      running: true
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


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    client = get_vergeos_client(module)
    name = module.params.get('name')

    # The SDK's default field set is roughly a quarter of the vnet schema and
    # omits fields an operator needs -- dnslist, port mirroring, rate limits.
    # An _info module should return the whole resource.
    #
    # Keep this a list. On pyvergeos 1.2.7 -- the newest release, and what
    # most users will have -- passing the string "all" made the SDK send a
    # per-character field list and the API returned a single field, with no
    # error (pyvergeos#101, confirmed on 26.1.8 for both get and list). That
    # is fixed on pyvergeos dev, where both forms return the full record, but
    # the list form is correct on every version the collection supports
    # (requirements.txt allows >= 1.0.1).
    all_fields = ['all']

    try:
        if name:
            # Get specific network by name
            try:
                network = client.networks.get(name=name, fields=all_fields)
                networks = [dict(network)]
            except NotFoundError:
                networks = []
        else:
            # Get all networks
            networks = [dict(net) for net in client.networks.list(fields=all_fields)]

        module.exit_json(changed=False, networks=networks)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
