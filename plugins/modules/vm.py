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
  - A VM reaches a network through its NICs. Use M(vergeio.vergeos.nic).
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
        far worse default than a refusal. Check mode makes that same refusal
        instead of reporting the VM deleted. A VM that is already stopped is
        reported as C(would delete) in check mode.
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
  power_timeout:
    description:
      - Seconds to wait for O(state=running) or O(state=stopped) to be
        reached.
      - On expiry the module fails and says what status it was still reading.
        It used to wait the same 60 seconds and then report success whichever
        way the wait ended, so a VM that never started produced
        C(changed=true) and a green play.
      - Raise it for a system that is legitimately slower than the default.
        A VM that never arrives is not a VM that started.
      - The state is read before the first wait, so C(0) means "check once and
        fail if it is not already there". Measured on 26.1.8, a VM reports
        C(initializing) the instant a power-on call returns and C(running)
        about half a second later, so C(0) is a way to exercise the expiry
        path deliberately rather than a useful production setting.
    type: int
    default: 60
    version_added: "2.2.0"
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
      - >-
        Rounded up to the next multiple of 256 MB before the VM is created
        or updated, the same way the SDK rounds on create. The platform
        stores RAM in 256 MB increments and floors a value that is not one,
        so comparing or writing the raw number made an unchanged playbook
        lower the VM's RAM and report a change on every run.
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
  description:
    - The VM row after the requested state was applied.
    - Keys are the API's, plus C(key). The platform identifier is C($key).
      C(key) is that same value under a name Jinja can read with dot
      notation. C($key) stays on the dict so existing plays can keep reading
      it with brackets; it is not a documented return key. Power is
      C(status) (a string such as C(running) or C(stopped)) and C(running)
      (a bool). There is no C(power_state) key and no C(id) key.
    - This is the projection C(VM_FIELDS) fetches, not every column on the
      VM. C(vm_info) returns a wider row.
  returned: when state is present, running, or stopped
  type: dict
  contains:
    key:
      description:
        - VM identifier. The same value as the platform row's C($key).
        - Plays that already read C(['$key']) keep working. C(key) is what
          Jinja can read with dot notation.
      type: int
      returned: always
    name:
      description: VM name.
      type: str
      returned: always
    description:
      description: VM description.
      type: str
      returned: always
    enabled:
      description: Whether the VM is enabled.
      type: bool
      returned: always
    os_family:
      description: OS family (C(linux), C(windows), or C(other)).
      type: str
      returned: always
    cpu_cores:
      description: Number of CPU cores.
      type: int
      returned: always
    ram:
      description: RAM in MB.
      type: int
      returned: always
    machine_type:
      description:
        - QEMU machine type.
        - An alias such as C(q35) is stored expanded (C(pc-q35-10.0)).
      type: str
      returned: always
    uefi:
      description: Whether the firmware is UEFI. This is the column C(bios_type) writes.
      type: bool
      returned: always
    boot_order:
      description:
        - Boot order string.
        - Absent from the SDK's default projection, so this module asks for it by name.
      type: str
      returned: always
    status:
      description: Power status string, for example C(running) or C(stopped).
      type: str
      returned: always
    running:
      description: Whether the VM is powered on.
      type: bool
      returned: always
  sample:
    key: 1
    name: web-server-01
    description: Web server for production
    enabled: true
    os_family: linux
    cpu_cores: 4
    ram: 8192
    machine_type: pc-q35-10.0
    uefi: false
    boot_order: cdn
    status: running
    running: true
changed:
  description: Whether the module made any changes
  returned: always
  type: bool
  sample: true
'''

import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    alias_platform_key,
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


# VergeOS stores VM RAM in 256 MB increments. VMManager.create rounds UP
# before the API call:
#
#     normalized_ram = ((ram + 255) // 256) * 256
#
# save() does not. A raw update is floored by the platform, so the value
# create stored and the value the next run compared were never the same
# number. ram: 2000 created a VM at 2048 MB; the identical playbook then
# wrote 1792 and reported changed on every run after that (#123).
RAM_INCREMENT_MB = 256


def normalize_ram(ram):
    """Round RAM up to a multiple of 256 MB, matching VMManager.create."""
    ram = int(ram)
    step = RAM_INCREMENT_MB
    return ((ram + step - 1) // step) * step


def ram_matches(stored, requested):
    """True when the VM already has the RAM this request rounds up to.

    The stored value is whatever the platform kept, which is an integer
    number of MB. Coerce it before comparing so a numeric string still
    counts as converged.
    """
    wanted = normalize_ram(requested)
    try:
        return int(stored) == wanted
    except (TypeError, ValueError):
        return False


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
        if param == 'bios_type':
            value = bios_to_uefi(value)
        elif param == 'ram':
            value = normalize_ram(value)
        vm_data[api_field] = value

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
        elif param == 'ram':
            if ram_matches(current, desired):
                continue
            desired = normalize_ram(desired)
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


def vm_is_running(vm):
    """True when the fetched row says this VM is powered on.

    Same reading ``power_on_vm()`` already uses. ``get_vm()`` asks for both
    spellings through ``POWER_FIELDS``. A row that carries neither is not
    treated as running: "no opinion" is not "on".
    """
    row = dict(vm)
    return row.get('status') == 'running' or bool(row.get('running'))


def delete_vm(module, client, vm):
    """Delete a VM using SDK.

    The platform refuses to delete a running VM ("Virtual Machine must be
    stopped to delete") and this module will not power it off to get around
    that. Check mode used to return success before looking at the row, so a
    dry run reported the VM deleted and the real run failed (#127). Both
    modes now refuse up front, with the same message.
    """
    if vm_is_running(vm):
        module.fail_json(
            msg="Virtual Machine must be stopped to delete. "
                "Set state=stopped on VM '%s' first."
                % module.params['name'])

    if module.check_mode:
        return True

    vm.delete()
    return True


# Both power waits used to poll thirty times, two seconds apart, and then
# `return True` whichever way the loop ended -- so a VM that never reached the
# requested state produced changed=true and a green play (#114). The platform
# refuses an over-provisioned start synchronously, which is why this was
# survivable for so long; what it does not refuse is a start that is accepted
# and then does not finish.
# Measured on 26.1.8, from the instant the power call returned:
#
#   power_on   +0.01s initializing   +0.52s running
#   power_off  +0.01s running        +0.51s running     +1.03s stopped
#
# So the state is never already correct when the call returns, and it arrives
# in about half a second. The old loop slept two seconds BEFORE looking, which
# made every power task cost at least that whether or not it needed to.
POWER_POLL_SECONDS = 1


def wait_for_power(module, vm, wanted, timeout):
    """Poll until the VM reports ``wanted``, or fail saying what it read.

    Failing on expiry rather than returning is the whole point (#114).
    `network` was written this way for the same reason (#97); this brings the
    two into line.

    The state is read BEFORE the first sleep, so a VM that arrives quickly is
    not made to wait for a poll interval it did not need -- and so that
    ``power_timeout: 0`` means "check once", which is what makes the expiry
    path testable against a real system without contriving a broken VM.
    """
    deadline = time.time() + max(0, int(timeout))
    while True:
        vm.refresh()
        status = dict(vm).get('status')
        if status == wanted:
            return dict(vm)
        if time.time() >= deadline:
            module.fail_json(
                msg="VM '%s' did not reach '%s' within %ss; it still reads "
                    "status '%s'. Power is asynchronous and this module waits "
                    "for it rather than assuming it -- raise power_timeout if "
                    "this system is legitimately slower than that, but a VM "
                    "that never arrives is not a VM that started."
                    % (module.params['name'], wanted, timeout, status))
        time.sleep(POWER_POLL_SECONDS)


def power_on_vm(module, client, vm):
    """Power on a VM using SDK"""
    vm_dict = dict(vm)
    if vm_dict.get('status') == 'running' or vm_dict.get('running'):
        return False, vm_dict

    if module.check_mode:
        vm_dict['status'] = 'running'
        return True, vm_dict

    vm.power_on()
    return True, wait_for_power(module, vm, 'running',
                                module.params['power_timeout'])


def power_off_vm(module, client, vm):
    """Power off a VM using SDK"""
    vm_dict = dict(vm)
    if vm_dict.get('status') == 'stopped' and not vm_dict.get('running'):
        return False, vm_dict

    if module.check_mode:
        vm_dict['status'] = 'stopped'
        return True, vm_dict

    vm.power_off(force=True)
    return True, wait_for_power(module, vm, 'stopped',
                                module.params['power_timeout'])


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(
            type='str', default='present',
            choices=['present', 'absent', 'running', 'stopped']
        ),
        power_timeout=dict(type='int', default=60),
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
                if module.check_mode:
                    module.exit_json(
                        changed=True, msg=f"Would delete VM '{name}'")
                else:
                    module.exit_json(changed=True, msg=f"VM '{name}' deleted")
            else:
                module.exit_json(changed=False, msg=f"VM '{name}' does not exist")

        elif state == 'present':
            if vm:
                # Update existing VM
                changed, updated_vm = update_vm(module, client, vm)
                module.exit_json(changed=changed, vm=alias_platform_key(updated_vm))
            else:
                # Create new VM
                changed, new_vm = create_vm(module, client)
                module.exit_json(changed=changed, vm=alias_platform_key(new_vm))

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
                module.exit_json(changed=changed or power_changed,
                                 vm=alias_platform_key(vm_dict))
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
            module.exit_json(changed=update_changed or power_changed,
                             vm=alias_platform_key(vm_dict))

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
