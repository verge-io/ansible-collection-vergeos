#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vm_export
short_description: Manage VM exports to a VergeOS NAS volume
version_added: "2.1.0"
description:
  - Manage a volume's VM-export configuration and optionally run an
    export — the agentless backup path, VM images land as files on a
    NAS volume that external tooling (or an NFS/CIFS share) can reach.
  - One export configuration exists per NAS volume; it is matched by
    O(volume).
options:
  volume:
    description:
      - Name of the NAS volume exports land on.
    type: str
    required: true
  state:
    description:
      - Whether the export configuration should exist.
    type: str
    choices: [ present, absent ]
    default: present
  quiesced:
    description:
      - Quiesce VMs (guest-agent filesystem freeze) during export.
    type: bool
    default: true
  create_current:
    description:
      - Maintain a C(current) folder holding the latest export.
    type: bool
    default: true
  max_exports:
    description:
      - Number of export generations to keep on the volume (1-100).
        Older generations are pruned — this is the retention knob.
    type: int
    default: 3
  start:
    description:
      - Run an export now (after ensuring the configuration).
      - An export is always reported as changed.
    type: bool
    default: false
  export_name:
    description:
      - Optional name for the export run/folder.
    type: str
  vms:
    description:
      - VM names to export. When omitted, all VMs are exported.
    type: list
    elements: str
  wait:
    description:
      - Wait for a started export to finish.
    type: bool
    default: true
  wait_timeout:
    description:
      - Seconds to wait for the export to finish.
    type: int
    default: 3600
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Export configuration on the backups volume, keep 5 generations
  vergeio.vergeos.vm_export:
    volume: backups
    max_exports: 5
    state: present

- name: Run a quiesced export of two VMs and wait for it
  vergeio.vergeos.vm_export:
    volume: backups
    start: true
    export_name: nightly
    vms: [ app-server, db-server ]

- name: Remove the export configuration
  vergeio.vergeos.vm_export:
    volume: backups
    state: absent
'''

RETURN = r'''
export:
  description: State of the export configuration after the module ran.
  returned: when state is present
  type: dict
  sample:
    key: 2
    volume: 7
    quiesced: true
    create_current: true
    max_exports: 5
    status: "idle"
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
  sample: ["created export config on 'backups'", "export finished"]
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

POLL_INTERVAL = 10


def export_result(export):
    data = dict(export)
    return {
        'key': data.get('$key'),
        'volume': data.get('volume'),
        'quiesced': bool(data.get('quiesced', True)),
        'create_current': bool(data.get('create_current', True)),
        'max_exports': int(data.get('max_exports') or 0),
        'status': data.get('status'),
    }


def find_export(client, volume_key):
    try:
        return client.volume_vm_exports.get(volume=volume_key)
    except NotFoundError:
        return None


def resolve_vm_keys(module, client, names):
    keys = []
    for name in names:
        try:
            vm = client.vms.get(name=name)
        except NotFoundError:
            module.fail_json(msg="VM '%s' not found" % name)
        keys.append(dict(vm)['$key'])
    return keys


def wait_for_idle(module, client, export_key):
    deadline = time.time() + module.params['wait_timeout']
    while True:
        export = client.volume_vm_exports.get(export_key)
        status = dict(export).get('status')
        if status == 'error':
            module.fail_json(msg='Export finished with error status')
        if status != 'building':
            return export
        if time.time() >= deadline:
            module.fail_json(msg='Timed out after %ds waiting for the export '
                                 'to finish' % module.params['wait_timeout'])
        time.sleep(POLL_INTERVAL)


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        volume=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        quiesced=dict(type='bool', default=True),
        create_current=dict(type='bool', default=True),
        max_exports=dict(type='int', default=3),
        start=dict(type='bool', default=False),
        export_name=dict(type='str'),
        vms=dict(type='list', elements='str'),
        wait=dict(type='bool', default=True),
        wait_timeout=dict(type='int', default=3600),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    actions = []
    volume_name = module.params['volume']

    try:
        try:
            volume = client.nas_volumes.get(name=volume_name)
        except NotFoundError:
            module.fail_json(msg="NAS volume '%s' not found" % volume_name)
        volume_key = dict(volume)['$key']

        export = find_export(client, volume_key)

        if module.params['state'] == 'absent':
            if export is None:
                module.exit_json(changed=False, actions=actions,
                                 msg="No export config on '%s'" % volume_name)
            actions.append("deleted export config on '%s'" % volume_name)
            if not module.check_mode:
                client.volume_vm_exports.delete(dict(export)['$key'])
            module.exit_json(changed=True, actions=actions)

        changed = False
        if export is None:
            actions.append("created export config on '%s'" % volume_name)
            changed = True
            if module.check_mode:
                if module.params['start']:
                    actions.append('started export')
                module.exit_json(changed=True, actions=actions,
                                 export={'volume': volume_name})
            export = client.volume_vm_exports.create(
                volume=volume_key,
                quiesced=module.params['quiesced'],
                create_current=module.params['create_current'],
                max_exports=module.params['max_exports'],
            )
        else:
            data = dict(export)
            updates = {}
            for field in ('quiesced', 'create_current'):
                if bool(data.get(field, True)) != module.params[field]:
                    updates[field] = module.params[field]
            if int(data.get('max_exports') or 0) != module.params['max_exports']:
                updates['max_exports'] = module.params['max_exports']
            if updates:
                actions.append("updated export config on '%s': %s"
                               % (volume_name, ', '.join(sorted(updates))))
                changed = True
                if not module.check_mode:
                    export = client.volume_vm_exports.update(
                        data['$key'], **updates)

        if module.params['start']:
            export_key = dict(export)['$key']
            vm_keys = None
            if module.params.get('vms'):
                vm_keys = resolve_vm_keys(module, client, module.params['vms'])
            actions.append('started export')
            changed = True
            if not module.check_mode:
                client.volume_vm_exports.start_export(
                    export_key,
                    name=module.params.get('export_name'),
                    vms=vm_keys,
                )
                if module.params['wait']:
                    export = wait_for_idle(module, client, export_key)
                    actions.append('export finished')

        module.exit_json(changed=changed, actions=actions,
                         export=export_result(export))

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
