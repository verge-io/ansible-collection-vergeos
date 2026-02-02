#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

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
  - VergeIO
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
      network_type: "internal"
      ip_address: "10.0.0.0"
      subnet_mask: "255.255.255.0"
      id: "12345"
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

    try:
        if name:
            # Get specific network by name
            try:
                network = client.networks.get(name=name)
                networks = [dict(network)]
            except NotFoundError:
                networks = []
        else:
            # Get all networks
            networks = [dict(net) for net in client.networks.list()]

        module.exit_json(changed=False, networks=networks)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
