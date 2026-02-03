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
    type: str
  description:
    description:
      - Description for the snapshot.
    type: str
  expiration:
    description:
      - Expiration time for the snapshot in Unix epoch seconds.
      - If not specified, snapshot will not expire.
    type: int
  operation:
    description:
      - Operation to perform.
      - C(create) creates a new snapshot.
      - C(restore) restores a VM from a snapshot.
      - C(list) lists snapshots (returns all snapshots or filtered by VM).
      - C(delete) deletes a snapshot.
    type: str
    choices: [ create, restore, list, delete ]
    default: create
  poll_interval:
    description:
      - Number of seconds to wait between status polls for async operations.
    type: int
    default: 5
  poll_timeout:
    description:
      - Maximum time in seconds to wait for operation to complete.
      - Snapshot creation is typically 2-5 minutes.
      - Snapshot restore is typically 5-15 minutes.
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

- name: Restore VM from snapshot
  vergeio.vergeos.vm_snapshot:
    vm_name: "web-server-01"
    snapshot_id: "45"
    operation: restore

- name: Delete a snapshot
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


def get_vm(client, module, vm_name=None, vm_id=None):
    """Get VM from name or ID using SDK."""
    if vm_id:
        try:
            return client.vms.get(key=vm_id)
        except NotFoundError:
            module.fail_json(msg=f"VM with ID '{vm_id}' not found")

    if vm_name:
        try:
            return client.vms.get(name=vm_name)
        except NotFoundError:
            module.fail_json(msg=f"VM '{vm_name}' not found")

    return None


def create_snapshot(client, module):
    """Create a VM snapshot using SDK."""
    vm_name = module.params['vm_name']
    vm_id = module.params['vm_id']
    snapshot_name = module.params['snapshot_name']
    expiration = module.params.get('expiration')

    if not snapshot_name:
        module.fail_json(msg="snapshot_name is required when creating a snapshot")

    # Get VM
    vm = get_vm(client, module, vm_name, vm_id)
    if not vm:
        module.fail_json(msg="Either vm_name or vm_id must be provided")

    resolved_vm_id = str(dict(vm).get('$key'))

    # Build snapshot payload - SDK uses name and retention (in seconds)
    snapshot_data = {
        'name': snapshot_name,
    }
    # Convert expiration timestamp to retention duration if provided
    if expiration:
        current_time = int(time.time())
        if expiration > current_time:
            snapshot_data['retention'] = expiration - current_time
        else:
            # If expiration is in the past, use a minimal retention
            snapshot_data['retention'] = 3600  # 1 hour default

    if module.check_mode:
        module.exit_json(
            changed=True,
            msg="Would create snapshot (check mode)",
            vm_id=resolved_vm_id,
            snapshot_name=snapshot_name
        )

    # Create the snapshot using VM's snapshot method
    result = vm.snapshot(**snapshot_data)
    result_dict = dict(result) if result and hasattr(result, '__iter__') else {}

    module.exit_json(
        changed=True,
        operation='create',
        snapshot_name=snapshot_name,
        vm_id=resolved_vm_id,
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
            # List all snapshots
            snapshots = [dict(s) for s in client.machine_snapshots.list()]

        module.exit_json(
            changed=False,
            operation='list',
            snapshots=snapshots,
            count=len(snapshots)
        )

    except NotFoundError as e:
        module.fail_json(msg=f"Failed to list snapshots: {str(e)}")


def restore_snapshot(client, module):
    """Restore a VM from a snapshot using SDK."""
    vm_name = module.params.get('vm_name')
    vm_id = module.params.get('vm_id')
    snapshot_id = module.params.get('snapshot_id')

    if not snapshot_id:
        module.fail_json(msg="snapshot_id is required for restore operation")

    # Get VM
    vm = get_vm(client, module, vm_name, vm_id)
    if not vm:
        module.fail_json(msg="Either vm_name or vm_id must be provided for restore")

    resolved_vm_id = str(dict(vm).get('$key'))

    if module.check_mode:
        module.exit_json(
            changed=True,
            msg="Would restore from snapshot (check mode)",
            vm_id=resolved_vm_id,
            snapshot_id=snapshot_id
        )

    # Restore from snapshot
    try:
        snapshot = client.machine_snapshots.get(key=snapshot_id)
        snapshot.restore()
    except NotFoundError:
        module.fail_json(msg=f"Snapshot '{snapshot_id}' not found")

    module.exit_json(
        changed=True,
        operation='restore',
        vm_id=resolved_vm_id,
        snapshot_id=snapshot_id
    )


def delete_snapshot(client, module):
    """Delete a snapshot using SDK."""
    snapshot_id = module.params.get('snapshot_id')

    if not snapshot_id:
        module.fail_json(msg="snapshot_id is required for delete operation")

    if module.check_mode:
        module.exit_json(
            changed=True,
            msg="Would delete snapshot (check mode)",
            snapshot_id=snapshot_id
        )

    # Delete the snapshot
    try:
        snapshot = client.machine_snapshots.get(key=snapshot_id)
        snapshot.delete()
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
