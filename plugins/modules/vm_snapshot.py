#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vm_snapshot
short_description: Manage VM snapshots in VergeOS
version_added: "1.0.0"
description:
  - Create, list, restore, and delete VM snapshots in VergeOS.
  - Supports expiring and non-expiring snapshots.
  - Can filter snapshots by VM name, ID, or other criteria.
options:
  vm_name:
    description:
      - The name of the VM to snapshot or query snapshots for.
      - Mutually exclusive with I(vm_id).
    type: str
  vm_id:
    description:
      - The ID of the VM to snapshot or query snapshots for.
      - Mutually exclusive with I(vm_name).
    type: str
  snapshot_name:
    description:
      - Name for the new snapshot.
      - Required when I(state=present).
    type: str
  snapshot_id:
    description:
      - ID of the snapshot to restore from or delete.
      - Required when I(state=absent) or when I(operation=restore).
      - Restore, and delete when I(vm_name) or I(vm_id) is set, require this
        snapshot's C(machine) to be the named VM's C(machine). A key from
        another VM is refused in check mode and on a real run. That other
        VM is not modified. The refusal names both VMs.
      - An id that does not exist is reported as not found, including when
        a VM is named. A missing key is not described as another VM's snapshot.
    type: str
  description:
    description:
      - Description for the snapshot.
    type: str
  expiration:
    description:
      - Expiration time for the snapshot as a Unix epoch (seconds).
      - Omit or set to C(0) for a snapshot that never expires.
      - A past epoch is rejected; it is not silently rewritten to one hour.
    type: int
  operation:
    description:
      - Operation to perform.
      - C(create) creates a new snapshot.
      - C(restore) reverts the VM in place to the snapshot state.
      - The VM must be powered off for restore. This is destructive; changes
        since the snapshot are lost.
      - Restore refuses a snapshot whose C(machine) is not the named VM's
        C(machine). A key from another VM does not revert that VM.
      - C(list) lists snapshots (returns all snapshots or filtered by VM).
      - C(delete) deletes a snapshot.
      - Delete with I(vm_name) or I(vm_id) refuses a snapshot that belongs
        to a different VM, and the message names both VMs. A missing key
        is reported as not found. Delete with only I(snapshot_id) is unchanged.
    type: str
    choices: [ create, restore, list, delete ]
    default: create
  poll_interval:
    description:
      - Accepted for compatibility. The module does not poll; create and
        restore return when the API accepts the request.
    type: int
    default: 5
  poll_timeout:
    description:
      - Accepted for compatibility. The module does not poll; create and
        restore return when the API accepts the request.
    type: int
    default: 600
  state:
    description:
      - C(present) creates a snapshot (same as operation=create).
      - C(absent) deletes a snapshot (same as operation=delete).
      - Use I(operation=list) to list snapshots.
    type: str
    choices: [ present, absent ]
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Create a snapshot of a VM
  vergeio.vergeos.vm_snapshot:
    vm_name: "web-server-01"
    snapshot_name: "pre-update-snapshot"
    description: "Before system update"
    operation: create

- name: Create an expiring snapshot
  vergeio.vergeos.vm_snapshot:
    vm_name: "web-server-01"
    snapshot_name: "temp-snapshot"
    description: "Temporary snapshot"
    expiration: 1735689600  # Unix timestamp
    state: present

- name: List all snapshots for a VM
  vergeio.vergeos.vm_snapshot:
    vm_name: "web-server-01"
    operation: list
  register: vm_snapshots

- name: List all snapshots in the system
  vergeio.vergeos.vm_snapshot:
    operation: list
  register: all_snapshots

- name: Restore VM from snapshot (VM must be powered off; destructive)
  vergeio.vergeos.vm_snapshot:
    vm_name: "web-server-01"
    snapshot_id: "45"
    operation: restore
  # snapshot_id must be a snapshot of web-server-01. A key from another
  # VM is refused, and that VM is not modified.

- name: Delete a snapshot of this VM
  vergeio.vergeos.vm_snapshot:
    vm_name: "web-server-01"
    snapshot_id: "45"
    operation: delete

- name: Delete a snapshot by id
  vergeio.vergeos.vm_snapshot:
    snapshot_id: "45"
    operation: delete

- name: Delete a snapshot using state
  vergeio.vergeos.vm_snapshot:
    snapshot_id: "45"
    state: absent
'''

RETURN = r'''
snapshots:
  description: List of snapshots (when operation=list)
  returned: when operation is list
  type: list
  elements: dict
  sample:
    - $key: 45
      name: "pre-update-snapshot"
      description: "Before system update"
      parent_vm: 42
      is_snapshot: true
      created: 1735689600
snapshot_id:
  description: ID of created snapshot
  returned: when operation is create
  type: str
  sample: "45"
snapshot_name:
  description: Name of created snapshot
  returned: when operation is create
  type: str
  sample: "pre-update-snapshot"
vm_id:
  description: ID of the VM that was snapshotted or restored
  returned: when operation is create or restore
  type: str
  sample: "42"
operation:
  description: Operation that was performed
  returned: always
  type: str
  sample: "create"
'''

import time
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


# Power-state joins that actually resolve on 26.1.8 (see vm.py POWER_FIELDS).
_VM_POWER_FIELDS = [
    '$key',
    'name',
    'machine',
    'machine#status#running as running',
    'machine#status#status as status',
]

# Identity columns for the snapshot-ownership check. name is here so a
# refusal can name the VM the task addressed, including by vm_id.
_VM_SCOPE_FIELDS = [
    '$key',
    'name',
    'machine',
]


def get_vm(client, module, vm_name=None, vm_id=None, fields=None):
    """Get VM from name or ID using SDK."""
    get_kwargs = {}
    list_kwargs = {}
    if fields is not None:
        get_kwargs['fields'] = fields
        list_kwargs['fields'] = fields

    if vm_id:
        try:
            return client.vms.get(key=vm_id, **get_kwargs)
        except NotFoundError:
            module.fail_json(msg=f"VM with ID '{vm_id}' not found")

    if vm_name:
        try:
            return resolve_one(module, client.vms, vm_name, 'VM', **list_kwargs)
        except NotFoundError:
            module.fail_json(msg=f"VM '{vm_name}' not found")

    return None


def vm_is_running(vm):
    """True when the VM is powered on.

    Prefer the SDK projected accessor when present; fall back to the raw
    join columns this module asks for.
    """
    if hasattr(vm, 'is_running'):
        try:
            value = vm.is_running
            if value is not None:
                return bool(value)
        except Exception:
            pass
    row = dict(vm)
    if row.get('running') is not None:
        return bool(row.get('running'))
    return row.get('status') == 'running'


def create_snapshot(client, module):
    """Create a VM snapshot using SDK."""
    vm_name = module.params['vm_name']
    vm_id = module.params['vm_id']
    snapshot_name = module.params['snapshot_name']
    description = module.params.get('description')
    expiration = module.params.get('expiration')

    if not snapshot_name:
        module.fail_json(msg="snapshot_name is required when creating a snapshot")

    # Get VM
    vm = get_vm(client, module, vm_name, vm_id)
    if not vm:
        module.fail_json(msg="Either vm_name or vm_id must be provided")

    resolved_vm_id = str(dict(vm).get('$key'))

    # Resolve expires. Docs say omit => never. expiration=0 is also never.
    # A past epoch used to be silently rewritten to +1h; refuse it instead.
    #
    # pyVergeOS#146/#148 fixed create() to always send expires (including 0)
    # on tip 1.7.1+. The floor is >=1.2.8, which PyPI resolves to 1.6.1,
    # still below that tip: retention=0 omits the field and the platform
    # applies +72h. POST expires ourselves so "never" is actually never.
    current_time = int(time.time())
    if expiration is None or expiration == 0:
        expires = 0
    elif expiration > current_time:
        expires = int(expiration)
    else:
        module.fail_json(
            msg=(
                "expiration must be a future Unix epoch, or 0 / omitted "
                "for a snapshot that never expires (got %s)" % expiration
            )
        )

    # Converge instead of colliding. The platform rejects a duplicate snapshot
    # name with "This name is already in use", so re-running a play that takes
    # a named snapshot used to fail outright rather than report no change.
    existing = next(
        (dict(s) for s in vm.snapshots.list()
         if dict(s).get('name') == snapshot_name),
        None,
    )
    if existing is not None:
        module.exit_json(
            changed=False,
            operation='create',
            snapshot_id=str(existing.get('$key', '')),
            snapshot_name=snapshot_name,
            vm_id=resolved_vm_id,
            response=existing,
            msg="Snapshot '%s' already exists on this VM" % snapshot_name,
        )

    if module.check_mode:
        module.exit_json(
            changed=True,
            msg="Would create snapshot (check mode)",
            vm_id=resolved_vm_id,
            snapshot_name=snapshot_name,
            expires=expires,
        )

    try:
        machine_key = int(vm.snapshots.machine_key)
    except (TypeError, ValueError) as exc:
        module.fail_json(msg="VM has no machine key for snapshot create: %s" % exc)

    body = {
        'machine': machine_key,
        'name': snapshot_name,
        'created_manually': True,
        'quiesce': False,
        'expires': expires,
    }
    if description:
        body['description'] = description

    # Direct POST: keeps expires:0 in the body on pre-#148 SDKs.
    result = client._request('POST', 'machine_snapshots', json_data=body)
    result_dict = dict(result) if result and hasattr(result, '__iter__') else {}

    created_snapshot_id = str(result_dict.get('$key', '')) if result_dict else ''

    module.exit_json(
        changed=True,
        operation='create',
        snapshot_id=created_snapshot_id,
        snapshot_name=snapshot_name,
        vm_id=resolved_vm_id,
        expires=expires,
        response=result_dict
    )


def list_snapshots(client, module):
    """List VM snapshots using SDK."""
    vm_name = module.params.get('vm_name')
    vm_id = module.params.get('vm_id')

    try:
        # Query for snapshots
        if vm_name or vm_id:
            # List snapshots for a specific VM
            vm = get_vm(client, module, vm_name, vm_id)
            if not vm:
                module.fail_json(msg="Either vm_name or vm_id must be provided")

            snapshots = [dict(s) for s in vm.snapshots.list()]
        else:
            # List all snapshots using direct API call (no per-VM manager needed)
            result = client._request('GET', 'machine_snapshots', params={'fields': 'all'})
            snapshots = [dict(s) for s in result] if result else []

        module.exit_json(
            changed=False,
            operation='list',
            snapshots=snapshots,
            count=len(snapshots)
        )

    except NotFoundError as e:
        module.fail_json(msg=f"Failed to list snapshots: {str(e)}")


def _machine_key(value):
    """Integer machine key, or None when value is not one.

    ``bool`` is an ``int`` subclass and is never a machine key. A numeric
    string still matches, because a projection may return ``"60"``.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _vm_label(data, fallback_name=None):
    """Human label for a VM row: name, id, and machine."""
    if not isinstance(data, dict):
        try:
            data = dict(data)
        except (TypeError, ValueError):
            data = {}
    name = data.get('name') or fallback_name
    key = data.get('$key')
    machine = data.get('machine')
    if name:
        return "VM '%s' (vm_id=%s, machine=%s)" % (name, key, machine)
    return "VM vm_id=%s (machine=%s)" % (key, machine)


def _vm_by_machine(client, machine_key):
    """Return the VM row that owns ``machine_key``, or None.

    Used only on the refusal path, so the message can name the VM the
    snapshot actually belongs to. A lookup failure still refuses; it just
    names the machine key instead of the VM.
    """
    try:
        rows = client._request(
            'GET', 'vms',
            params={
                'filter': 'machine eq %s' % machine_key,
                'fields': '$key,name,machine',
            },
        )
    except Exception:                                       # noqa: BLE001
        return None
    if not rows:
        return None
    if isinstance(rows, dict):
        rows = [rows]
    for row in rows:
        try:
            data = row if isinstance(row, dict) else dict(row)
        except (TypeError, ValueError):
            continue
        if _machine_key(data.get('machine')) == machine_key:
            return data
    return None


def _unscoped_snapshot(client, snapshot_key):
    """Return the machine_snapshots row, or None when the key does not exist.

    ``vm.snapshots.get(key)`` raises NotFoundError for a missing key and,
    since pyVergeOS #174, for a key whose ``machine`` is not this VM.
    Those two are the same exception. This GET is not VM-scoped, so a
    foreign row comes back and a missing key does not.
    """
    try:
        row = client._request(
            'GET',
            'machine_snapshots/%s' % snapshot_key,
            params={'fields': '$key,name,machine'},
        )
    except NotFoundError:
        return None
    if not isinstance(row, dict) or not row:
        return None
    return row


def _require_snapshot_of_vm(module, client, vm, snapshot_key, snapshot_id):
    """Confirm ``snapshot_key`` belongs to ``vm`` before check mode or a write.

    Before pyVergeOS #174, ``vm.snapshots.get(key)`` returned another
    machine's row. After #174 it raises NotFoundError for that row, which
    is also what a missing key raises. A NotFound from the scoped get is
    therefore read again without the VM scope. A row on another machine
    is refused and the message names both VMs. A key that does not exist
    is reported as not found.
    """
    try:
        snapshot = vm.snapshots.get(key=snapshot_key)
    except NotFoundError:
        snapshot = _unscoped_snapshot(client, snapshot_key)
        if snapshot is None:
            module.fail_json(msg="Snapshot '%s' not found" % snapshot_id)
            return
    _refuse_foreign_snapshot(module, client, vm, snapshot, snapshot_id)


def _refuse_foreign_snapshot(module, client, vm, snapshot, snapshot_id):
    """Refuse when the snapshot's machine is not the named VM's machine.

    A match returns. Anything else fails before a write and names both
    VMs when the owner can be resolved.
    """
    vm_data = dict(vm)
    vm_machine = _machine_key(vm_data.get('machine'))
    snap_data = dict(snapshot) if snapshot is not None else {}
    snap_machine = _machine_key(snap_data.get('machine'))
    named = _vm_label(vm_data, module.params.get('vm_name'))

    if vm_machine is None:
        module.fail_json(
            msg=(
                "VM %s has no machine key; cannot confirm snapshot '%s' "
                "belongs to it" % (named, snapshot_id)
            )
        )

    if snap_machine is not None and snap_machine == vm_machine:
        return

    if snap_machine is None:
        detail = (
            "has no machine key, so it cannot be confirmed to belong to this VM"
        )
    else:
        owner = _vm_by_machine(client, snap_machine)
        if owner is not None:
            detail = "belongs to %s" % _vm_label(owner)
        else:
            detail = "belongs to machine %s" % snap_machine
    module.fail_json(
        msg="Snapshot '%s' %s, not %s" % (snapshot_id, detail, named)
    )


def restore_snapshot(client, module):
    """Revert a VM in place to a snapshot (destructive).

    Docs and examples describe an in-place restore. The object method
    ``snapshot.restore()`` is a *clone to a new VM* (and before
    pyVergeOS#148 posted the snap_machine as a VM key -- #147). The
    manager method with ``replace_original=True`` is the in-place revert
    the docs describe; it is the correct SDK path on every supported
    release, including tip after #148.
    """
    vm_name = module.params.get('vm_name')
    vm_id = module.params.get('vm_id')
    snapshot_id = module.params.get('snapshot_id')

    if not snapshot_id:
        module.fail_json(msg="snapshot_id is required for restore operation")

    try:
        snapshot_key = int(snapshot_id)
    except (TypeError, ValueError):
        module.fail_json(
            msg="snapshot_id must be an integer key, got %r" % (snapshot_id,)
        )

    # Power state is required: the platform refuses an in-place revert of a
    # running VM, and check mode must say so rather than claim changed=true.
    # Ownership is checked first. A snapshot key from another VM must not be
    # reported as "this VM is running", and must not reach restore.
    vm = get_vm(client, module, vm_name, vm_id, fields=_VM_POWER_FIELDS)
    if not vm:
        module.fail_json(msg="Either vm_name or vm_id must be provided for restore")

    resolved_vm_id = str(dict(vm).get('$key'))

    # Before check mode and before the restore posts. A foreign key is
    # refused by name; a key that does not exist says not found.
    _require_snapshot_of_vm(module, client, vm, snapshot_key, snapshot_id)

    if vm_is_running(vm):
        module.fail_json(
            msg=(
                "VM must be powered off for in-place restore "
                "(vm_id=%s, snapshot_id=%s)" % (resolved_vm_id, snapshot_id)
            )
        )

    if module.check_mode:
        module.exit_json(
            changed=True,
            msg="Would restore from snapshot (check mode)",
            vm_id=resolved_vm_id,
            snapshot_id=str(snapshot_key),
        )

    # In-place revert. Do NOT catch NotFoundError here: a failure from the
    # restore action is not "snapshot not found" (that was the old lie).
    try:
        result = vm.snapshots.restore(snapshot_key, replace_original=True)
    except ValueError as exc:
        module.fail_json(msg=str(exc))

    result_dict = dict(result) if result and hasattr(result, '__iter__') else {}

    module.exit_json(
        changed=True,
        operation='restore',
        vm_id=resolved_vm_id,
        snapshot_id=str(snapshot_key),
        response=result_dict,
    )


def delete_snapshot(client, module):
    """Delete a snapshot using SDK.

    With vm_name or vm_id, the snapshot must belong to that VM. The same
    machine comparison as restore runs before check mode and before the
    DELETE, so a key from another VM is refused and that snapshot stays.
    A delete that names no VM still deletes machine_snapshots/<id> directly.
    """
    snapshot_id = module.params.get('snapshot_id')
    vm_name = module.params.get('vm_name')
    vm_id = module.params.get('vm_id')

    if not snapshot_id:
        module.fail_json(msg="snapshot_id is required for delete operation")

    if vm_name or vm_id:
        try:
            snapshot_key = int(snapshot_id)
        except (TypeError, ValueError):
            module.fail_json(
                msg="snapshot_id must be an integer key, got %r" % (snapshot_id,)
            )

        vm = get_vm(
            client, module, vm_name, vm_id, fields=_VM_SCOPE_FIELDS,
        )
        if not vm:
            module.fail_json(
                msg="Either vm_name or vm_id must be provided for delete"
            )

        # Before check mode and before the DELETE. A foreign key is
        # refused by name; a key that does not exist says not found.
        _require_snapshot_of_vm(
            module, client, vm, snapshot_key, snapshot_id,
        )

    if module.check_mode:
        module.exit_json(
            changed=True,
            msg="Would delete snapshot (check mode)",
            snapshot_id=snapshot_id
        )

    # Ownership was checked above when a VM was named. The DELETE is the
    # same request either way; the key is the one just verified, or the
    # id the task gave when it named no VM.
    try:
        client._request('DELETE', f'machine_snapshots/{snapshot_id}')
    except NotFoundError:
        module.fail_json(msg=f"Snapshot '{snapshot_id}' not found")

    module.exit_json(
        changed=True,
        operation='delete',
        snapshot_id=snapshot_id,
        msg=f"Snapshot {snapshot_id} deleted"
    )


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        vm_name=dict(type='str'),
        vm_id=dict(type='str'),
        snapshot_name=dict(type='str'),
        snapshot_id=dict(type='str'),
        description=dict(type='str'),
        expiration=dict(type='int'),
        operation=dict(
            type='str',
            choices=['create', 'restore', 'list', 'delete'],
            default='create'
        ),
        poll_interval=dict(type='int', default=5),
        poll_timeout=dict(type='int', default=600),
        state=dict(type='str', choices=['present', 'absent']),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        mutually_exclusive=[
            ('vm_name', 'vm_id'),
            ('operation', 'state'),
        ],
    )

    # Map state to operation if state is provided
    if module.params['state']:
        state_to_operation = {
            'present': 'create',
            'absent': 'delete',
        }
        operation = state_to_operation[module.params['state']]
    else:
        operation = module.params['operation']

    client = get_vergeos_client(module)

    try:
        if operation == 'create':
            create_snapshot(client, module)
        elif operation == 'list':
            list_snapshots(client, module)
        elif operation == 'restore':
            restore_snapshot(client, module)
        elif operation == 'delete':
            delete_snapshot(client, module)
        else:
            module.fail_json(msg=f"Invalid operation: {operation}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
