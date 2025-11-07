#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

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
      - Snapshot creation: 2-5 minutes typical.
      - Snapshot restore: 5-15 minutes typical.
    type: int
    default: 600
  state:
    description:
      - C(present) creates a snapshot (same as operation=create).
      - C(absent) deletes a snapshot (same as operation=delete).
      - C(list) lists snapshots (same as operation=list).
    type: str
    choices: [ present, absent, list ]
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO
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
from ansible.module_utils.six.moves.urllib.parse import quote
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    VergeOSAPI,
    VergeOSAPIError,
    vergeos_argument_spec
)


def get_vm_id(api, module, vm_name=None, vm_id=None):
    """Get VM ID from name or validate provided ID."""
    if vm_id:
        return vm_id

    if vm_name:
        try:
            # Get all VMs and filter by name
            vms = api.get('vms')
            matching_vms = [vm for vm in vms if vm.get('name') == vm_name]
            if not matching_vms:
                module.fail_json(msg=f"VM '{vm_name}' not found")
            vm = matching_vms[0]
            return str(vm.get('$key') or vm.get('id'))
        except VergeOSAPIError as e:
            module.fail_json(msg=f"Failed to find VM: {str(e)}")

    return None


def create_snapshot(api, module):
    """Create a VM snapshot."""
    vm_name = module.params['vm_name']
    vm_id = module.params['vm_id']
    snapshot_name = module.params['snapshot_name']
    description = module.params.get('description', '')
    expiration = module.params.get('expiration')

    if not snapshot_name:
        module.fail_json(msg="snapshot_name is required when creating a snapshot")

    # Resolve VM ID
    resolved_vm_id = get_vm_id(api, module, vm_name, vm_id)
    if not resolved_vm_id:
        module.fail_json(msg="Either vm_name or vm_id must be provided")

    # Build snapshot payload
    payload = {
        'name': snapshot_name,
    }
    if description:
        payload['description'] = description
    if expiration:
        payload['expiration'] = expiration

    if module.check_mode:
        module.exit_json(
            changed=True,
            msg="Would create snapshot (check mode)",
            vm_id=resolved_vm_id,
            snapshot_name=snapshot_name
        )

    # Create the snapshot using POST /vms/{id}/snapshot endpoint
    try:
        response = api.post(f'vms/{resolved_vm_id}/snapshot', payload)
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to create snapshot: {str(e)}")

    # Extract snapshot ID from response
    snapshot_id = response.get('$key') or response.get('id')

    # TODO: Add polling logic if needed to wait for snapshot completion
    # For now, return immediately as snapshot creation is async

    module.exit_json(
        changed=True,
        operation='create',
        snapshot_id=str(snapshot_id) if snapshot_id else None,
        snapshot_name=snapshot_name,
        vm_id=resolved_vm_id,
        response=response
    )


def list_snapshots(api, module):
    """List VM snapshots."""
    vm_name = module.params.get('vm_name')
    vm_id = module.params.get('vm_id')

    try:
        # Query for snapshots
        if vm_name or vm_id:
            # List snapshots for a specific VM
            # First, get the VM to find its machine ID
            resolved_vm_id = get_vm_id(api, module, vm_name, vm_id)
            vm_data = api.get(f"vms/{resolved_vm_id}")
            machine_id = vm_data.get('machine')

            if not machine_id:
                module.fail_json(msg=f"VM {resolved_vm_id} does not have a machine ID")

            # Query machine_snapshots for this machine
            filter_param = quote(f"machine eq {machine_id}")
            snapshots = api.get(f"machine_snapshots?filter={filter_param}")
        else:
            # List all snapshots
            snapshots = api.get("machine_snapshots")

        if not snapshots:
            snapshots = []
        elif not isinstance(snapshots, list):
            snapshots = [snapshots]

        module.exit_json(
            changed=False,
            operation='list',
            snapshots=snapshots,
            count=len(snapshots)
        )

    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to list snapshots: {str(e)}")


def restore_snapshot(api, module):
    """Restore a VM from a snapshot."""
    vm_name = module.params.get('vm_name')
    vm_id = module.params.get('vm_id')
    snapshot_id = module.params.get('snapshot_id')

    if not snapshot_id:
        module.fail_json(msg="snapshot_id is required for restore operation")

    # Resolve VM ID if provided
    resolved_vm_id = get_vm_id(api, module, vm_name, vm_id)
    if not resolved_vm_id:
        module.fail_json(msg="Either vm_name or vm_id must be provided for restore")

    payload = {
        'vm': resolved_vm_id,
        'action': 'restore',
        'params': {
            'snapshot_id': snapshot_id
        }
    }

    if module.check_mode:
        module.exit_json(
            changed=True,
            msg="Would restore from snapshot (check mode)",
            vm_id=resolved_vm_id,
            snapshot_id=snapshot_id
        )

    # Restore from snapshot
    try:
        response = api.post('vm_actions', payload)
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to restore from snapshot: {str(e)}")

    # TODO: Add polling logic to wait for restore completion
    # Restore typically takes 5-15 minutes

    module.exit_json(
        changed=True,
        operation='restore',
        vm_id=resolved_vm_id,
        snapshot_id=snapshot_id,
        response=response
    )


def delete_snapshot(api, module):
    """Delete a snapshot."""
    snapshot_id = module.params.get('snapshot_id')

    if not snapshot_id:
        module.fail_json(msg="snapshot_id is required for delete operation")

    if module.check_mode:
        module.exit_json(
            changed=True,
            msg="Would delete snapshot (check mode)",
            snapshot_id=snapshot_id
        )

    # Delete the snapshot from machine_snapshots endpoint
    try:
        api.delete(f"machine_snapshots/{snapshot_id}")
    except VergeOSAPIError as e:
        module.fail_json(msg=f"Failed to delete snapshot: {str(e)}")

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
        state=dict(type='str', choices=['present', 'absent', 'list']),
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
            'list': 'list'
        }
        operation = state_to_operation[module.params['state']]
    else:
        operation = module.params['operation']

    api = VergeOSAPI(module)

    try:
        if operation == 'create':
            create_snapshot(api, module)
        elif operation == 'list':
            list_snapshots(api, module)
        elif operation == 'restore':
            restore_snapshot(api, module)
        elif operation == 'delete':
            delete_snapshot(api, module)
        else:
            module.fail_json(msg=f"Invalid operation: {operation}")
    except VergeOSAPIError as e:
        module.fail_json(msg=str(e))


if __name__ == '__main__':
    main()
