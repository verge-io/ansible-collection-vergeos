#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vm
short_description: Manage virtual machines in VergeOS
version_added: "1.0.0"
description:
  - Create, update, power on/off, and delete virtual machines in VergeOS.
  - This module allows you to manage the complete lifecycle of VMs.
options:
  name:
    description:
      - The name of the virtual machine.
    type: str
    required: true
  state:
    description:
      - The desired state of the virtual machine.
      - C(present) ensures the VM exists with the specified configuration.
      - C(absent) ensures the VM is deleted. The platform refuses to delete a
        RUNNING VM - the API answers
        C(Virtual Machine must be stopped to delete) - so set O(state=stopped)
        first. This module deliberately does not stop it for you, because an
        C(absent) that powered off a running workload to remove it would be a
        far worse default than a refusal.
      - C(running) ensures the VM exists and is powered on, creating it first
        if it is absent.
      - >-
        C(stopped) powers off a VM that already exists. Unlike O(state=running)
        it does NOT create a missing VM - it fails with
        C(VM '<name>' does not exist). The asymmetry is deliberate: creating a
        machine in order to report it as stopped is rarely what was meant.
    type: str
    choices: [ present, absent, running, stopped ]
    default: present
  description:
    description:
      - Description of the virtual machine.
    type: str
  enabled:
    description:
      - Whether the VM is enabled.
      - Defaults to C(true) when creating. Omit to leave unchanged on update.
    type: bool
  os_family:
    description:
      - The operating system family for the VM.
    type: str
    choices: [ linux, windows, other ]
  cpu_cores:
    description:
      - Number of CPU cores to assign to the VM.
    type: int
  ram:
    description:
      - Amount of RAM in MB to assign to the VM.
    type: int
  machine_type:
    description:
      - The machine type for the VM.
      - >-
        Accepts either a family alias (C(pc), C(q35), C(virt)), which the
        platform expands to its newest version, or an exact machine type such
        as C(pc-q35-10.0). Pin the exact name when a specific version matters,
        for example when remediating a deprecated machine type.
      - >-
        An alias is considered converged against any version of that family,
        so C(q35) will not silently move a VM between machine versions. The
        platform rejects unknown machine types.
    type: str
  bios_type:
    description:
      - Firmware the VM boots with.
      - Stored as the boolean C(uefi) on the VM; this option is the readable
        spelling of it, not a field of its own.
    type: str
    choices: [ seabios, uefi ]
  boot_order:
    description:
      - Boot device order, as a single string.
      - Observed valid on VergeOS 26.1.8 - C(c) disk, C(d) cdrom, C(n) network,
        and the orderings C(cd), C(dc), C(nc), C(cdn), plus C(strict).
      - Not constrained with I(choices) on purpose. The set is the platform's
        and may differ by version; VergeOS rejects an invalid value with a
        message that names the field and the value, which is more useful than
        a client-side list that could be wrong.
    type: str
  snapshot_profile:
    description:
      - Name of the snapshot profile the VM is enrolled in.
      - Pass an empty string to remove the VM from its profile.
      - The profile must already exist - see
        M(vergeio.vergeos.snapshot_profile).
    type: str
    version_added: "2.2.0"
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Create a new Linux VM
  vergeio.vergeos.vm:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "web-server-01"
    description: "Web server for production"
    state: present
    enabled: true
    os_family: linux
    cpu_cores: 4
    ram: 8192
    machine_type: q35

- name: Ensure VM is running
  vergeio.vergeos.vm:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "web-server-01"
    state: running

- name: Stop a VM
  vergeio.vergeos.vm:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "web-server-01"
    state: stopped

- name: Update VM configuration
  vergeio.vergeos.vm:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "web-server-01"
    cpu_cores: 8
    ram: 16384
    state: present

- name: Delete a VM
  vergeio.vergeos.vm:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "web-server-01"
    state: absent
'''

RETURN = r'''
vm:
  description: Information about the virtual machine
  returned: when state is present, running, or stopped
  type: dict
  sample:
    name: "web-server-01"
    description: "Web server for production"
    enabled: true
    os_family: "linux"
    cpu_cores: 4
    ram: 8192
    machine_type: "q35"
    power_state: "running"
    id: "12345"
changed:
  description: Whether the module made any changes
  returned: always
  type: bool
  sample: true
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


# Module parameter -> API column, every name checked against a live vm row on
# VergeOS 26.1.8. The row has 74 columns and THREE of the options this module
# used to accept were not among them (#87):
#
#   machine_subtype   no column, and nothing holds the value. Removed --
#                     machine_type already carries the expanded form
#                     ('q35' -> 'pc-q35-10.0'), so there was nothing for a
#                     subtype to mean.
#   bios_type         no column. The firmware choice is stored as the BOOLEAN
#                     `uefi`. Kept as an option because 'seabios'/'uefi' reads
#                     better than a flag, and TRANSLATED on the way out.
#   network           no column. A VM's networks are its NICs -- see the nic
#                     module. Removed.
#
# `boot_order` is a real column and was wrong in a different way: declared
# `type: list`, while the API stores a single enumerated STRING. Sending a
# list is a hard error, not a silent discard --
#
#     value '["c","d"]' is not in list for field 'boot_order'
#
# -- so the option could never have worked in any spelling. It is a str now.
#
# Measured, not inferred. Creating a VM with all three set:
#
#     machine_subtype='q35'  ->  no column holds 'q35'
#     bios_type='uefi'       ->  uefi stayed False
#     network='Core'         ->  no column holds 'Core'
#
# and the module reported changed=True every run, forever, because each
# compared against a column that does not exist. VMManager.create takes
# **kwargs, so nothing rejected them; the API accepts unknown fields with
# HTTP 200 and discards them, which is the whole reason this class keeps
# recurring.
UPDATE_FIELD_MAP = {
    'description': 'description',
    'enabled': 'enabled',
    'os_family': 'os_family',
    'cpu_cores': 'cpu_cores',
    'ram': 'ram',
    'machine_type': 'machine_type',
    'bios_type': 'uefi',
    'boot_order': 'boot_order',
    'snapshot_profile': 'snapshot_profile',
}

CREATE_PARAM_MAP = dict(UPDATE_FIELD_MAP, name='name')

IDENTITY_PARAMS = ('name',)

COMPARISON_FIELDS = tuple(sorted(set(UPDATE_FIELD_MAP.values())))


# Asked for explicitly. The SDK's default projection is a 23-column summary
# and it does NOT include boot_order -- which this module compares. A column
# compared but never fetched reads as None, so a VM with boot_order set
# reported changed on every run and re-sent it, forever. Same shape as #18,
# found while fixing #87 in the same file.
#
# snapshot_profile is deliberately absent: it is read separately, by key, in
# current_snapshot_profile(), because it is not in the default projection
# either and the comparison needs its own call.
#
# The power state, and the only spelling of it that works.
#
# `state: running` on a VM that was already running FAILED:
#
#     API error: Error starting machine: Machine is already running
#     with status 'running'
#
# power_on_vm() guards on dict(vm)['status'] / ['running'] -- and this
# module fetches an explicit field list, which does not carry either, so the
# guard was never true and the module powered on a running VM every time.
# The same hole made `state: stopped` report changed forever.
#
# Measured on 26.1.8, asking for the power state four ways:
#
#     fields=...,running,status                 both silently dropped
#     fields=most                               absent
#     fields=all                                absent
#     status#running as running                 silently dropped
#     machine#status#running as running         WORKS
#
# The SDK's DEFAULT projection does carry them, which is why vm_info reports
# power state correctly and this module could not -- the moment a module
# names its fields, it owns every one it reads.
POWER_FIELDS = ['machine#status#running as running',
                'machine#status#status as status']

VM_FIELDS = ['$key', 'name'] + sorted(
    set(COMPARISON_FIELDS) - {'snapshot_profile'}) + POWER_FIELDS


def bios_to_uefi(value):
    """'uefi'/'seabios' -> the boolean the API stores."""
    return value == 'uefi'


# A machine-type alias is stored expanded: 'q35' becomes 'pc-q35-10.0'.
# Comparing the alias against the stored value literally never matched, so a
# converged VM reported 'changed' on every run and re-sent the field.

MACHINE_TYPE_ALIASES = {
    'pc': 'pc-i440fx-',
    'q35': 'pc-q35-',
    'virt': 'virt-',
}


def machine_type_matches(desired, current):
    """True if the stored machine type already satisfies the request."""
    if desired == current:
        return True
    prefix = MACHINE_TYPE_ALIASES.get(desired)
    return bool(prefix and current and str(current).startswith(prefix))


def get_vm(module, client, name):
    """Get VM by name using SDK"""
    try:
        return resolve_one(module, client.vms, name, 'VM', fields=VM_FIELDS)
    except NotFoundError:
        return None


# The vm row stores a snapshot profile's $key, and the operator writes its
# NAME. Two separate traps here, both measured on 26.1.8:
#
#   1. `snapshot_profile` is NOT in the SDK's default projection for a vm, so
#      dict(vm).get('snapshot_profile') is None whatever the VM is enrolled
#      in. Comparing against that made enrolment report changed on every run
#      and made clearing ('') a silent no-op, since None and '' both look
#      empty. It has to be read with an explicit field list.
#   2. This module used to carry resolve_snapshot_profile() reading a
#      parameter it never declared -- dead code that advertised the feature
#      without providing it (#95). This is the other half, arriving with the
#      snapshot_profile module it needs (#39).
SNAPSHOT_PROFILE_FIELD = 'snapshot_profile'


def resolve_snapshot_profile(module, client):
    """Profile NAME -> the raw field value. '' clears the enrolment."""
    name = module.params['snapshot_profile']
    if name == '':
        return ''
    try:
        profile = resolve_one(module, client.snapshot_profiles, name,
                              'snapshot profile')
    except NotFoundError:
        module.fail_json(msg="Snapshot profile '%s' not found" % name)
    return dict(profile)['$key']


def current_snapshot_profile(client, vm_key):
    """What the VM is enrolled in now, asked for by name.

    Not read from the row the caller already has: the default projection does
    not include this column, so that row says None regardless.
    """
    row = dict(client.vms.get(vm_key,
                              fields=['$key', SNAPSHOT_PROFILE_FIELD]))
    return str(row.get(SNAPSHOT_PROFILE_FIELD) or '')


def build_vm_data(module):
    """Build VM data dict from module params"""
    vm_data = {
        'name': module.params['name'],
        'enabled': module.params['enabled'] if module.params['enabled'] is not None else True,
    }

    for param, api_field in sorted(CREATE_PARAM_MAP.items()):
        if param in IDENTITY_PARAMS or param == 'enabled':
            continue            # handled above
        if param == 'snapshot_profile':
            continue            # needs the client, done by the caller
        value = module.params.get(param)
        if value is None:
            continue
        vm_data[api_field] = bios_to_uefi(value) if param == 'bios_type' \
            else value

    return vm_data


def create_vm(module, client):
    """Create a new VM using SDK"""
    vm_data = build_vm_data(module)
    if module.params.get('snapshot_profile'):
        vm_data[SNAPSHOT_PROFILE_FIELD] = resolve_snapshot_profile(module,
                                                                   client)

    if module.check_mode:
        return True, vm_data

    vm = client.vms.create(**vm_data)
    return True, dict(vm)


def update_vm(module, client, vm):
    """Update an existing VM using SDK"""
    changed = False
    update_data = {}

    vm_dict = dict(vm)
    for param, api_field in sorted(UPDATE_FIELD_MAP.items()):
        if param == 'snapshot_profile':
            continue            # needs the client, handled below
        if module.params.get(param) is None:
            continue
        desired = module.params[param]
        current = vm_dict.get(api_field)
        if param == 'machine_type':
            if machine_type_matches(desired, current):
                continue
        elif param == 'bios_type':
            desired = bios_to_uefi(desired)
            if bool(current) == desired:
                continue
        elif current == desired:
            continue
        update_data[api_field] = desired
        changed = True

    if module.params.get('snapshot_profile') is not None:
        wanted = str(resolve_snapshot_profile(module, client) or '')
        if current_snapshot_profile(client, vm_dict['$key']) != wanted:
            update_data[SNAPSHOT_PROFILE_FIELD] = wanted
            changed = True

    if not changed:
        return False, vm_dict

    if module.check_mode:
        vm_dict.update(update_data)
        return True, vm_dict

    vm = vm.save(**update_data)
    return True, dict(vm)


def delete_vm(module, client, vm):
    """Delete a VM using SDK"""
    if module.check_mode:
        return True

    vm.delete()
    return True


def power_on_vm(module, client, vm):
    """Power on a VM using SDK"""
    vm_dict = dict(vm)
    if vm_dict.get('status') == 'running' or vm_dict.get('running'):
        return False, vm_dict

    if module.check_mode:
        vm_dict['status'] = 'running'
        return True, vm_dict

    vm.power_on()
    # Wait for VM to start (up to 60 seconds)
    import time
    for _attempt in range(30):
        time.sleep(2)
        vm.refresh()
        if dict(vm).get('status') == 'running':
            break
    return True, dict(vm)


def power_off_vm(module, client, vm):
    """Power off a VM using SDK"""
    vm_dict = dict(vm)
    if vm_dict.get('status') == 'stopped' and not vm_dict.get('running'):
        return False, vm_dict

    if module.check_mode:
        vm_dict['status'] = 'stopped'
        return True, vm_dict

    vm.power_off(force=True)
    # Wait for VM to stop (up to 60 seconds)
    import time
    for _attempt in range(30):
        time.sleep(2)
        vm.refresh()
        if dict(vm).get('status') == 'stopped':
            break
    return True, dict(vm)


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(
            type='str', default='present',
            choices=['present', 'absent', 'running', 'stopped']
        ),
        description=dict(type='str'),
        enabled=dict(type='bool'),
        os_family=dict(type='str', choices=['linux', 'windows', 'other']),
        cpu_cores=dict(type='int'),
        ram=dict(type='int'),
        machine_type=dict(type='str'),
        bios_type=dict(type='str', choices=['seabios', 'uefi']),
        boot_order=dict(type='str'),
        snapshot_profile=dict(type='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    client = get_vergeos_client(module)
    name = module.params['name']
    state = module.params['state']

    try:
        # Get existing VM
        vm = get_vm(module, client, name)

        if state == 'absent':
            if vm:
                delete_vm(module, client, vm)
                module.exit_json(changed=True, msg=f"VM '{name}' deleted")
            else:
                module.exit_json(changed=False, msg=f"VM '{name}' does not exist")

        elif state == 'present':
            if vm:
                # Update existing VM
                changed, updated_vm = update_vm(module, client, vm)
                module.exit_json(changed=changed, vm=updated_vm)
            else:
                # Create new VM
                changed, new_vm = create_vm(module, client)
                module.exit_json(changed=changed, vm=new_vm)

        elif state == 'running':
            if not vm:
                # Create VM first
                changed, vm_data = create_vm(module, client)
                # Re-fetch VM after creation (skip in check mode)
                if not module.check_mode:
                    vm = get_vm(module, client, name)
                else:
                    vm = None
            else:
                # Check if updates needed
                update_changed, updated_vm = update_vm(module, client, vm)
                changed = update_changed
                # Re-fetch VM after update to get fresh state (skip in check mode)
                if update_changed and not module.check_mode:
                    vm = get_vm(module, client, name)

            # Ensure VM is running (only if we have a VM object)
            if vm:
                power_changed, vm_dict = power_on_vm(module, client, vm)
                module.exit_json(changed=changed or power_changed, vm=vm_dict)
            else:
                # Check mode - no actual VM object
                module.exit_json(changed=True, msg=f"Would power on VM '{name}' (check mode)")

        elif state == 'stopped':
            if not vm:
                module.fail_json(msg=f"VM '{name}' does not exist")

            # Check if updates needed
            update_changed, updated_vm = update_vm(module, client, vm)
            # Re-fetch VM after update to get fresh state
            if update_changed and not module.check_mode:
                vm = get_vm(module, client, name)

            # Ensure VM is stopped
            power_changed, vm_dict = power_off_vm(module, client, vm)
            module.exit_json(changed=update_changed or power_changed, vm=vm_dict)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
