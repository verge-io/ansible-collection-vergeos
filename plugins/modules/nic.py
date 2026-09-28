#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: nic
short_description: Manage network interfaces for VMs in VergeOS
version_added: "1.0.0"
description:
  - Create, update, and delete network interface cards (NICs) for virtual machines in VergeOS.
  - NICs connect VMs to VergeOS virtual networks (vnets).
  - Each VM has a machine key that is used for hardware operations like NIC management.
notes:
  - >-
    A NIC is identified by the network it is attached to. C(state=present)
    adds a NIC when the VM has none on I(network). C(state=absent) removes
    only a NIC on that network, and changes nothing when there isn't one.
  - >-
    An OVA import often arrives with a default NIC on the wrong network.
    Set I(nic_index) to move that NIC onto I(network) instead of adding a
    second one. Without I(nic_index) the imported NIC is left where it is.
options:
  vm_name:
    description:
      - The name of the virtual machine to attach the NIC to.
    type: str
    required: true
  network:
    description:
      - The name of the network to attach the NIC to.
    type: str
    required: true
  state:
    description:
      - The desired state of the NIC.
      - >-
        C(present) ensures the VM has a NIC on I(network), adding one when
        it does not. C(absent) removes the NIC on I(network) and is a no-op
        when the VM has no NIC there.
    type: str
    choices: [ present, absent ]
    default: present
  nic_index:
    description:
      - >-
        Move an existing NIC onto I(network) instead of adding one, when
        the VM has no NIC on I(network) yet.
      - >-
        The index is 0-based, in the order the API returns the VM's NICs.
        C(0) is the first NIC, which is the default NIC an OVA import
        brings.
      - >-
        Consulted only when no NIC is already attached to I(network). A
        NIC on that network is updated in place, so a second run converges
        whether or not I(nic_index) is set.
      - >-
        Not valid with C(state=absent). Removal always targets the NIC on
        I(network) and never a NIC chosen by position.
    type: int
    version_added: "2.2.0"
  mac_address:
    description:
      - MAC address for the NIC. If not specified, one will be auto-generated.
      - >-
        Case-insensitive and separator-insensitive. The VergeOS API stores
        MACs lowercase with colons, so C(AA:BB:CC:00:5E:0B),
        C(aa:bb:cc:00:5e:0b) and C(AA-BB-CC-00-5E-0B) are the same address and
        all converge. The value sent to the API is normalised to the stored
        form.
    type: str
  enabled:
    description:
      - Whether the NIC is enabled.
      - Defaults to C(true) when creating. Omit to leave unchanged on update.
    type: bool
  nic_type:
    description:
      - Type of network interface.
      - Defaults to C(virtio) when creating. Omit to leave unchanged on update.
    type: str
    choices: [ virtio, e1000, rtl8139 ]
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Add a NIC to a VM
  vergeio.vergeos.nic:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    vm_name: "web-server-01"
    network: "internal-network"
    state: present
    nic_type: virtio

- name: Add a NIC with specific MAC address
  vergeio.vergeos.nic:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    vm_name: "web-server-01"
    network: "dmz-network"
    mac_address: "52:54:00:12:34:56"
    state: present

- name: Remove a NIC from a VM
  vergeio.vergeos.nic:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    vm_name: "web-server-01"
    network: "old-network"
    state: absent

- name: Move the NIC an OVA import created onto the right network
  vergeio.vergeos.nic:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    vm_name: "imported-vm-01"
    network: "external"
    nic_index: 0
    state: present
'''

RETURN = r'''
nic:
  description: Information about the network interface
  returned: when state is present
  type: dict
  sample:
    vm_name: "web-server-01"
    network: "internal-network"
    mac_address: "52:54:00:12:34:56"
    enabled: true
    nic_type: "virtio"
    id: "12345"
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    resolve_one,
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


def get_vm(module, client, vm_name):
    """Get VM by name using SDK"""
    try:
        return resolve_one(module, client.vms, vm_name, 'VM')
    except NotFoundError:
        return None


def get_network(module, client, network_name):
    """Get network by name using SDK"""
    try:
        return resolve_one(module, client.networks, network_name, 'network')
    except NotFoundError:
        return None


def list_nics(vm):
    """Return the VM's NICs, or an empty list when the machine has none."""
    try:
        return list(vm.nics.list())
    except (NotFoundError, AttributeError):
        return []


def nic_on_network(nics, target_network):
    """Return the NIC attached to target_network, or None.

    Matching is by vnet key only (the SDK may also expose it as ``network``).
    A NIC on any other network is not a match. Returning the VM's first NIC
    here is what made ``state=absent`` delete a NIC on a different network,
    and what made declaring two NICs re-point the same one on every run.
    See issue #118.
    """
    target_network_key = dict(target_network).get('$key')
    for nic in nics:
        nic_dict = dict(nic)
        # The SDK may return either 'vnet' or 'network' as the field name.
        nic_network = nic_dict.get('vnet') or nic_dict.get('network')
        if nic_network == target_network_key:
            return nic
    return None


def get_nic(client, vm, target_network):
    """Return the NIC attached to target_network, or None.

    ``client`` is unused; kept so existing callers do not change shape.
    """
    return nic_on_network(list_nics(vm), target_network)


def nic_at_index(module, vm, nic_index):
    """Return the NIC at nic_index so the caller can move it.

    Used only for ``state=present`` when nothing is on the target network.
    An index past the end fails rather than falling back to another NIC.
    """
    nics = list_nics(vm)
    count = len(nics)
    if nic_index < 0 or nic_index >= count:
        vm_name = module.params['vm_name']
        module.fail_json(
            msg=f"nic_index {nic_index} is out of range: VM '{vm_name}' has "
                f"{count} NIC(s). Indexes start at 0, in the order the API "
                "returns them. Omit nic_index to add a NIC instead of "
                "moving one."
        )
    return nics[nic_index]


# Module parameter -> raw VergeOS API field, for the update path.
#
# update_nic() PUTs these keys verbatim, so every value must be a real field
# on the machine_nics resource. The API accepts unknown fields with HTTP 200
# and discards them, which is how #8, #10 and #18 all shipped unnoticed.
# tests/live/verify-field-contract.yml asserts this against a live system and
# tests/unit/plugins/modules/test_field_contracts.py asserts the shape of it
# offline. See issue #75.
UPDATE_FIELD_MAP = {
    'network': 'vnet',
    'enabled': 'enabled',
    'nic_type': 'interface',
    'mac_address': 'macaddress',
}


def normalize_mac(mac):
    """Fold a MAC to the form the VergeOS API stores: lowercase, colons.

    A MAC address is case-insensitive and separator-insensitive, but a string
    comparison is neither. The API normalises on write and returns
    'aa:bb:cc:00:5e:01' whatever you sent it, so comparing a user-supplied
    'AA:BB:CC:00:5E:01' against the stored value literally never matches and
    every run issues a PUT and reports changed. See issue #59.
    """
    return (mac or '').strip().lower().replace('-', ':')


def create_nic(module, client, vm, network):
    """Create a new NIC using SDK"""
    # Map our friendly nic_type names to SDK interface names
    interface_mapping = {
        'virtio': 'virtio',
        'e1000': 'e1000',
        'rtl8139': 'rtl8139',
    }

    # SDK accepts network name directly
    network_name = module.params['network']
    nic_data = {
        'network': network_name,
        'enabled': module.params['enabled'] if module.params['enabled'] is not None else True,
        'interface': interface_mapping.get(module.params['nic_type'] or 'virtio', 'virtio'),
    }

    if module.params.get('mac_address'):
        nic_data['mac_address'] = normalize_mac(module.params['mac_address'])

    if module.check_mode:
        return True, nic_data

    nic = vm.nics.create(**nic_data)
    return True, dict(nic)


def update_nic(module, client, nic, target_network):
    """Update an existing NIC using SDK"""
    changed = False
    update_data = {}

    nic_dict = dict(nic)
    target_network_key = dict(target_network).get('$key')

    # vnet differs only when nic_index selected a NIC that is not already
    # on the target network. A NIC found by network is already equal here.
    # The SDK may use 'vnet' or 'network' as the field name.
    current_network = nic_dict.get('vnet') or nic_dict.get('network')
    if current_network != target_network_key:
        update_data[UPDATE_FIELD_MAP['network']] = target_network_key
        changed = True

    # Check enabled
    if module.params.get('enabled') is not None:
        if nic_dict.get('enabled') != module.params['enabled']:
            update_data[UPDATE_FIELD_MAP['enabled']] = module.params['enabled']
            changed = True

    # Check interface type
    if module.params.get('nic_type') is not None:
        if nic_dict.get('interface') != module.params['nic_type']:
            update_data[UPDATE_FIELD_MAP['nic_type']] = module.params['nic_type']
            changed = True

    # Check MAC address (SDK uses 'mac_address' or 'macaddress').
    # Both sides are normalised: the API stores lowercase with colons, so a
    # literal comparison against a user-supplied uppercase MAC never matches.
    if module.params.get('mac_address') is not None:
        current_mac = normalize_mac(nic_dict.get('mac_address')
                                    or nic_dict.get('macaddress'))
        desired_mac = normalize_mac(module.params['mac_address'])
        if current_mac != desired_mac:
            update_data[UPDATE_FIELD_MAP['mac_address']] = desired_mac
            changed = True

    if not changed:
        return False, nic_dict

    if module.check_mode:
        nic_dict.update(update_data)
        return True, nic_dict

    nic = nic.save(**update_data)
    return True, dict(nic)


def delete_nic(module, client, nic):
    """Delete a NIC using SDK"""
    if module.check_mode:
        return True

    nic.delete()
    return True


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        vm_name=dict(type='str', required=True),
        network=dict(type='str', required=True),
        state=dict(type='str', default='present', choices=['present', 'absent']),
        mac_address=dict(type='str'),
        enabled=dict(type='bool'),
        nic_type=dict(type='str', choices=['virtio', 'e1000', 'rtl8139']),
        nic_index=dict(type='int'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    client = get_vergeos_client(module)
    vm_name = module.params['vm_name']
    network_name = module.params['network']
    state = module.params['state']

    try:
        # Get VM
        vm = get_vm(module, client, vm_name)
        if not vm:
            module.fail_json(msg=f"VM '{vm_name}' not found")

        vm_dict = dict(vm)
        if not vm_dict.get('machine'):
            module.fail_json(msg=f"VM '{vm_name}' has no machine key (may not be fully created yet)")

        # Get network
        network = get_network(module, client, network_name)
        if not network:
            module.fail_json(msg=f"Network '{network_name}' not found")

        # Get existing NIC
        nic = get_nic(client, vm, network)

        if state == 'absent':
            # Removal is by network only. An index would select a NIC on a
            # different network, which is the #118 bug.
            if module.params.get('nic_index') is not None:
                module.fail_json(
                    msg="nic_index cannot be used with state=absent. "
                        f"absent removes only the NIC on network '{network_name}', "
                        "and does nothing when the VM has no NIC there."
                )
            if nic:
                delete_nic(module, client, nic)
                module.exit_json(changed=True, msg=f"NIC removed from VM '{vm_name}'")
            else:
                module.exit_json(changed=False, msg="NIC does not exist")

        elif state == 'present':
            # No NIC on this network. nic_index opts in to moving one that
            # is attached somewhere else (the OVA-import case). Otherwise
            # add a NIC and leave every other NIC where it is.
            if not nic and module.params.get('nic_index') is not None:
                nic = nic_at_index(module, vm, module.params['nic_index'])
            if nic:
                changed, updated_nic = update_nic(module, client, nic, network)
                module.exit_json(changed=changed, nic=updated_nic)
            else:
                changed, new_nic = create_nic(module, client, vm, network)
                module.exit_json(changed=changed, nic=new_nic)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
