#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vm_clone
short_description: Clone a VM from one of its snapshots
version_added: "2.2.0"
description:
  - Create a new VM from a snapshot of an existing VM (restore-to-name),
    leaving the source untouched — the primitive behind restore drills
    and test-from-backup workflows.
  - Idempotent by clone name, if a VM named O(name) already exists,
    nothing is done.
options:
  name:
    description:
      - Name for the new (cloned) VM.
    type: str
    required: true
  source:
    description:
      - Name of the source VM whose snapshot is cloned.
    type: str
    required: true
  snapshot:
    description:
      - Name of the source snapshot to clone from.
      - When omitted, the most recent snapshot is used.
    type: str
  preserve_macs:
    description:
      - Keep the source MAC addresses on the clone.
      - The default (false) generates new MACs, which is what you want
        when the source is still running.
    type: bool
    default: false
  wait:
    description:
      - Wait for the cloned VM to become visible.
    type: bool
    default: true
  wait_timeout:
    description:
      - Seconds to wait for the clone to appear.
    type: int
    default: 300
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Clone the latest snapshot for a restore drill
  vergeio.vergeos.vm_clone:
    name: app-server-drill
    source: app-server

- name: Clone a specific snapshot
  vergeio.vergeos.vm_clone:
    name: app-server-tuesday
    source: app-server
    snapshot: nightly-20260824
'''

RETURN = r'''
vm:
  description: The cloned VM (name and key), or the existing VM when the
    name was already taken.
  returned: success
  type: dict
  sample:
    key: 51
    name: "app-server-drill"
    source: "app-server"
    snapshot: "nightly-20260824"
actions:
  description: Human-readable list of what the module did.
  returned: always
  type: list
  elements: str
'''

import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    resolve_one,
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

POLL_INTERVAL = 5


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        source=dict(type='str', required=True),
        snapshot=dict(type='str'),
        preserve_macs=dict(type='bool', default=False),
        wait=dict(type='bool', default=True),
        wait_timeout=dict(type='int', default=300),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    actions = []
    name = module.params['name']
    source_name = module.params['source']

    try:
        # Idempotence: clone name already taken -> nothing to do
        try:
            existing = resolve_one(module, client.vms, name, 'VM')
            module.exit_json(changed=False, actions=actions,
                             vm={'key': dict(existing).get('$key'),
                                 'name': name},
                             msg="VM '%s' already exists" % name)
        except NotFoundError:
            pass

        try:
            source = resolve_one(module, client.vms, source_name, 'VM')
        except NotFoundError:
            module.fail_json(msg="Source VM '%s' not found" % source_name)

        snapshots = source.snapshots.list()
        if not snapshots:
            module.fail_json(msg="Source VM '%s' has no snapshots"
                                 % source_name)

        wanted = module.params.get('snapshot')
        if wanted:
            match = [s for s in snapshots if dict(s).get('name') == wanted]
            if not match:
                module.fail_json(msg="Snapshot '%s' not found on '%s'"
                                     % (wanted, source_name))
            snap = match[0]
        else:
            snap = max(snapshots,
                       key=lambda s: int(dict(s).get('created') or 0))
        snap_data = dict(snap)

        actions.append("cloned '%s' from snapshot '%s' of '%s'"
                       % (name, snap_data.get('name'), source_name))
        if module.check_mode:
            module.exit_json(changed=True, actions=actions,
                             vm={'name': name, 'source': source_name,
                                 'snapshot': snap_data.get('name')})

        # The restore action with a destination name fails on current
        # builds ("Error getting destination VM during clone"). The
        # working path: a snapshot has its own vms row (is_snapshot),
        # reachable via the machine_snapshots row's snap_machine field —
        # clone THAT row.
        snap_full = client._request(
            'GET', 'machine_snapshots/%s' % snap_data['$key'],
            params={'fields': '$key,name,snap_machine'})
        snap_machine = (snap_full or {}).get('snap_machine')
        if not snap_machine:
            module.fail_json(msg="Snapshot '%s' has no snap_machine "
                                 'reference' % snap_data.get('name'))
        rows = client._request(
            'GET', 'vms',
            params={'filter': 'machine eq %s' % snap_machine,
                    'fields': '$key,name'})
        if not rows:
            module.fail_json(msg="No VM row found for snapshot '%s'"
                                 % snap_data.get('name'))
        snap_vm = client.vms.get(int(rows[0]['$key']))
        snap_vm.clone(
            name=name,
            preserve_macs=module.params['preserve_macs'],
        )

        clone = None
        if module.params['wait']:
            deadline = time.time() + module.params['wait_timeout']
            while True:
                try:
                    clone = resolve_one(module, client.vms, name, 'VM')
                    break
                except NotFoundError:
                    if time.time() >= deadline:
                        module.fail_json(
                            msg="Timed out after %ds waiting for clone "
                                "'%s' to appear"
                                % (module.params['wait_timeout'], name))
                    time.sleep(POLL_INTERVAL)

        module.exit_json(changed=True, actions=actions, vm={
            'key': dict(clone).get('$key') if clone is not None else None,
            'name': name,
            'source': source_name,
            'snapshot': snap_data.get('name'),
        })

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
