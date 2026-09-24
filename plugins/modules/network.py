#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: network
short_description: Manage networks in VergeOS
version_added: "1.0.0"
description:
  - Create, update, and delete networks in VergeOS.
  - Manage network configuration including IP ranges, VLANs, and network settings.
options:
  name:
    description:
      - The name of the network.
    type: str
    required: true
  state:
    description:
      - The desired state of the network.
      - C(present) ensures the network exists with the specified configuration.
      - C(absent) ensures the network is deleted.
    type: str
    choices: [ present, absent ]
    default: present
  description:
    description:
      - Description of the network.
    type: str
  network_type:
    description:
      - Type of network to create.
      - >-
        C(vlan) and C(overlay) were accepted before 2.1.0 but are not valid
        VergeOS network types; the API rejected both. A VLAN network is an
        C(external) (or C(internal)) network with C(layer2_type=vlan) and a
        C(vlan_id).
    type: str
    choices: [ internal, external, dmz ]
  ip_address:
    description:
      - Router IP address within the network (API field C(ipaddress)).
    type: str
  network:
    description:
      - CIDR-notation network address (e.g. C(10.10.10.0/24)).
      # Folded scalar: C(Validation error: Gateway is outside of network)
      # contains ": ", which YAML reads as a mapping key inside a plain
      # scalar and rejects -- taking the WHOLE DOCUMENTATION block with it.
      # See ansible-collection-vergeos#17.
      - >-
        Required by the VergeOS API for vnet creation; supplying
        C(ip_address) and C(gateway) without C(network) is rejected by the
        server with C(Validation error: Gateway is outside of network).
    type: str
    version_added: "2.1.0"
  gateway:
    description:
      - Default gateway for the network.
    type: str
  dhcp_enabled:
    description:
      - Whether DHCP is enabled on this network.
    type: bool
  dhcp_start:
    description:
      - Start of the DHCP range.
    type: str
  dhcp_end:
    description:
      - End of the DHCP range (API field C(dhcp_stop)).
      - >-
        Before 2.1.0 this was sent as C(dhcp_end), which the API silently
        discarded, producing a DHCP scope with a start and no end.
    type: str
  layer2_type:
    description:
      - Layer 2 encapsulation for the network.
      - Use C(vlan) together with C(vlan_id) to create a VLAN-tagged network.
    type: str
    choices: [ vlan, vxlan, none ]
    version_added: "2.1.0"
  vlan_id:
    description:
      - VLAN or VXLAN ID (API field C(layer2_id)).
      - Pair with C(layer2_type=vlan) for a VLAN-tagged network.
    type: int
  dns_servers:
    description:
      - List of DNS servers for the network (API field C(dnslist)).
      - >-
        Before 2.1.0 this was silently discarded on update; only the create
        path applied it.
    type: list
    elements: str
  domain:
    description:
      - DNS domain name handed to DHCP clients on this network.
    type: str
    version_added: "2.1.0"
  interface_network:
    description:
      - >-
        Name of the physical vnet (a switch) this network reaches the fabric
        through. Maps to the API's C(interface_vnet), which stores the
        target's key; give the name and the module resolves it.
      - >-
        Without an uplink a vnet is attached to nothing. A VLAN-tagged network
        with no C(interface_network) is an isolated layer 2 that carries the
        tag onto nothing, and the module will report success creating one.
        C(layer2_type) and C(vlan_id) describe the tag; this describes what
        the tag is carried on. Neither is useful without the other.
      - Set to an empty string to detach the network from its uplink.
    type: str
    version_added: "2.1.0"
  rate_limit:
    description:
      - Bandwidth cap for the network, in B(mbytes per second) - not bits, not bytes.
      - >-
        C(0) means explicitly uncapped and is applied like any other value.
        Omitting the parameter leaves whatever is already configured alone.
        The two are not the same thing.
    type: int
    version_added: "2.1.0"
  mtu:
    description:
      - MTU for the network.
    type: int
    version_added: "2.1.0"
  on_power_loss:
    description:
      - Behaviour when power is restored after a loss.
    type: str
    choices: [ power_on, last_state, leave_off ]
    version_added: "2.1.0"
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Create an internal network
  vergeio.vergeos.network:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "internal-network"
    description: "Internal production network"
    state: present
    network_type: internal
    network: "10.0.0.0/24"
    ip_address: "10.0.0.1"
    dhcp_enabled: true
    dhcp_start: "10.0.0.100"
    dhcp_end: "10.0.0.200"
    dns_servers:
      - "8.8.8.8"
      - "8.8.4.4"

# layer2_type and vlan_id describe the TAG; interface_network describes what
# the tag is carried on. Without an uplink this network is an isolated layer 2
# that carries VLAN 100 onto nothing -- and the module still reports success.
- name: Create a VLAN-tagged external network on the physical fabric
  vergeio.vergeos.network:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "vlan-100"
    state: present
    network_type: external
    layer2_type: vlan
    vlan_id: 100
    interface_network: "ext1 Switch"
    network: "192.168.100.0/24"
    ip_address: "192.168.100.1"

- name: Cap a network at 100 MB/s and move it to a different uplink
  vergeio.vergeos.network:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "vlan-100"
    state: present
    rate_limit: 100
    interface_network: "ext2 Switch"

- name: Remove the bandwidth cap and detach from the fabric
  vergeio.vergeos.network:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "vlan-100"
    state: present
    rate_limit: 0
    interface_network: ""

- name: Delete a network
  vergeio.vergeos.network:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "old-network"
    state: absent
'''

RETURN = r'''
network:
  description: Information about the network
  returned: when state is present
  type: dict
  sample:
    name: "internal-network"
    description: "Internal production network"
    type: "internal"
    network: "10.0.0.0/24"
    ipaddress: "10.0.0.1"
    dhcp_enabled: true
    dhcp_start: "10.0.0.100"
    dhcp_stop: "10.0.0.200"
    dnslist: "8.8.8.8,8.8.4.4"
changed:
  description: Whether the module made any changes
  returned: always
  type: bool
  sample: true
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


def get_network(client, name):
    """Get network by name, including every field the module compares.

    The SDK's default field set is a subset of the vnet schema and omits
    several fields this module manages -- notably 'dnslist'. Comparing
    against a field the fetch never returned makes it read as None, so the
    module reports 'changed' on every run and never converges (issue #18).
    """
    try:
        return client.networks.get(name=name, fields=COMPARISON_FIELDS)
    except NotFoundError:
        return None


# Module parameter -> pyvergeos NetworkManager.create() *parameter* name.
#
# The create path calls client.networks.create(**data), so these must be the
# SDK's named arguments -- the SDK is what translates them into API fields
# (ip_address -> ipaddress, dns_servers -> dnslist, network_address ->
# network). Anything that is not a named SDK argument falls through **kwargs
# and is sent to the API verbatim, where an unknown field is accepted and
# discarded: HTTP 200, no error. That silent discard is how #8, #10 and #18
# all shipped unnoticed.
CREATE_PARAM_MAP = {
    'description': 'description',
    'network_type': 'network_type',
    'ip_address': 'ip_address',
    'network': 'network_address',
    'gateway': 'gateway',
    'dhcp_enabled': 'dhcp_enabled',
    'dhcp_start': 'dhcp_start',
    'dhcp_end': 'dhcp_stop',
    'layer2_type': 'layer2_type',
    'vlan_id': 'layer2_id',
    'dns_servers': 'dns_servers',
    'domain': 'domain',
    'mtu': 'mtu',
    'on_power_loss': 'on_power_loss',
    'rate_limit': 'rate_limit',
}

# Module parameter -> raw VergeOS API field.
#
# The update path calls network.save(**data), which PUTs these keys verbatim,
# so every value here must be a real API field as returned by the vnet
# endpoint. tests/live/verify-network-contract.yml asserts exactly that
# against a live system; keep the two in sync.
UPDATE_FIELD_MAP = {
    'description': 'description',
    'network_type': 'type',
    'ip_address': 'ipaddress',
    'network': 'network',
    'gateway': 'gateway',
    'dhcp_enabled': 'dhcp_enabled',
    'dhcp_start': 'dhcp_start',
    'dhcp_end': 'dhcp_stop',
    'layer2_type': 'layer2_type',
    'vlan_id': 'layer2_id',
    'dns_servers': 'dnslist',
    'domain': 'domain',
    'mtu': 'mtu',
    'on_power_loss': 'on_power_loss',
    'rate_limit': 'rate_limit',
}

# interface_network is handled outside the maps above because resolving it
# needs the client: operators give a vnet NAME, the API stores that vnet's
# key. See resolve_interface_vnet().
UPLINK_PARAM = 'interface_network'
UPLINK_API_FIELD = 'interface_vnet'


# Fields the module must fetch in order to diff correctly. The SDK's default
# field set omits some of these (dnslist in particular), and a field that was
# never fetched reads as None, so the comparison never matches.
COMPARISON_FIELDS = (['$key', 'name', UPLINK_API_FIELD]
                     + sorted(set(UPDATE_FIELD_MAP.values())))


def normalize_value(param, value):
    """Coerce a module value into the representation the API stores."""
    if param == 'dns_servers' and isinstance(value, list):
        # The API stores the DNS server list as a comma-separated string.
        return ','.join(value)
    return value


def resolve_interface_vnet(module, client):
    """Resolve the uplink parameter to what the API stores.

    Returns one of:
      None -- the parameter was not supplied; leave the uplink alone
      ''   -- an empty string was supplied; detach from any uplink
      int  -- the $key of the named vnet

    Operators think in vnet names; the API stores the target's key. A vnet
    reaches the physical fabric through this field, so a network created
    without one is attached to nothing regardless of how correct every other
    field looks (issue #60).
    """
    name = module.params.get(UPLINK_PARAM)
    if name is None:
        return None
    if name == '':
        return ''

    try:
        uplink = client.networks.get(name=name)
    except NotFoundError:
        module.fail_json(
            msg="Uplink network '%s' not found. %s must name an existing "
                "vnet -- typically a 'physical' one, which is what carries a "
                "network onto the fabric." % (name, UPLINK_PARAM)
        )
    return dict(uplink).get('$key')


def build_network_data(module, uplink_key=None):
    """Build SDK create() kwargs from module params"""
    network_data = {
        'name': module.params['name'],
    }

    for param, sdk_arg in CREATE_PARAM_MAP.items():
        if module.params.get(param) is not None:
            network_data[sdk_arg] = module.params[param]

    if uplink_key is not None:
        network_data[UPLINK_API_FIELD] = uplink_key

    return network_data


def create_network(module, client):
    """Create a new network using SDK"""
    network_data = build_network_data(module, resolve_interface_vnet(module, client))

    if module.check_mode:
        return True, network_data

    network = client.networks.create(**network_data)
    return True, dict(network)


def update_network(module, client, network):
    """Update an existing network using SDK"""
    changed = False
    update_data = {}

    network_dict = dict(network)
    for param, api_field in UPDATE_FIELD_MAP.items():
        if module.params.get(param) is None:
            continue
        desired = normalize_value(param, module.params[param])
        if network_dict.get(api_field) != desired:
            update_data[api_field] = desired
            changed = True

    uplink_key = resolve_interface_vnet(module, client)
    if uplink_key is not None:
        # The stored value may come back as a string key; compare as strings
        # so a converged uplink does not read as a change on every run.
        current = network_dict.get(UPLINK_API_FIELD)
        if str(current or '') != str(uplink_key or ''):
            update_data[UPLINK_API_FIELD] = uplink_key
            changed = True

    if not changed:
        return False, network_dict

    if module.check_mode:
        network_dict.update(update_data)
        return True, network_dict

    network = network.save(**update_data)
    return True, dict(network)


def delete_network(module, client, network):
    """Delete a network using SDK"""
    if module.check_mode:
        return True

    network.delete()
    return True


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present', choices=['present', 'absent']),
        description=dict(type='str'),
        network_type=dict(type='str', choices=['internal', 'external', 'dmz']),
        ip_address=dict(type='str'),
        network=dict(type='str'),
        gateway=dict(type='str'),
        dhcp_enabled=dict(type='bool'),
        dhcp_start=dict(type='str'),
        dhcp_end=dict(type='str'),
        layer2_type=dict(type='str', choices=['vlan', 'vxlan', 'none']),
        vlan_id=dict(type='int'),
        dns_servers=dict(type='list', elements='str'),
        domain=dict(type='str'),
        mtu=dict(type='int'),
        interface_network=dict(type='str'),
        rate_limit=dict(type='int'),
        on_power_loss=dict(type='str',
                           choices=['power_on', 'last_state', 'leave_off']),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    client = get_vergeos_client(module)
    name = module.params['name']
    state = module.params['state']

    try:
        network = get_network(client, name)

        if state == 'absent':
            if network:
                delete_network(module, client, network)
                module.exit_json(changed=True, msg=f"Network '{name}' deleted")
            else:
                module.exit_json(changed=False, msg=f"Network '{name}' does not exist")

        elif state == 'present':
            if network:
                changed, updated_network = update_network(module, client, network)
                module.exit_json(changed=changed, network=updated_network)
            else:
                changed, new_network = create_network(module, client)
                module.exit_json(changed=changed, network=new_network)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
