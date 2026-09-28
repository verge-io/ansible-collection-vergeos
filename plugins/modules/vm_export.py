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
version_added: "2.2.0"
description:
  - Manage a volume's VM-export configuration and optionally run an
    export -- the agentless backup path, VM images land as files on a
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
      - C(absent) removes the configuration. Exports already written to
        the volume are files and are left alone.
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
        Older generations are pruned -- this is the retention knob.
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
      - When waiting, the run's own statistics row is read back and the
        module fails if it recorded any errors.
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
    vms: [app-server, db-server]

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
    volume: "c3437883534918dcf2abbb3e9b9622b865226c68"
    quiesced: true
    create_current: true
    max_exports: 5
    status: "idle"
last_export:
  description: Statistics for the run this task started.
  returned: when O(start) and O(wait) are both true
  type: dict
  sample:
    file_name: "nightly"
    virtual_machines: 2
    export_success: 2
    errors: 0
    size_bytes: 2147487086
    duration: 20
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

POLL_INTERVAL = 10

# Module parameter -> raw API column on `volume_vm_exports`. All three are
# real columns on a live export row (VergeOS 26.1.8, 10 columns), checked
# before anything else here was written -- see issue #75.
UPDATE_FIELD_MAP = {
    'quiesced': 'quiesced',
    'create_current': 'create_current',
    'max_exports': 'max_exports',
}

# Module parameter -> the keyword the SDK's update() takes. Identical to the
# columns here, unlike nas_volume and nas_nfs_share -- declared anyway so
# tests/unit/test_sdk_call_signatures.py can check it, because "identical
# today" is exactly the assumption that broke those two.
UPDATE_KWARG_MAP = {
    'quiesced': 'quiesced',
    'create_current': 'create_current',
    'max_exports': 'max_exports',
}

# There is one export configuration per volume, so the volume IS the identity.
CREATE_PARAM_MAP = dict(UPDATE_FIELD_MAP, volume='volume')
IDENTITY_PARAMS = ('volume',)

COMPARISON_FIELDS = tuple(sorted(set(UPDATE_FIELD_MAP.values())))
EXPORT_FIELDS = ['$key', 'volume', 'status', 'status_info'] \
    + list(COMPARISON_FIELDS)
STAT_FIELDS = ['$key', 'file_name', 'virtual_machines', 'export_success',
               'errors', 'size_bytes', 'duration', 'timestamp']


def export_result(export):
    data = dict(export)
    return {
        'key': data.get('$key'),
        'volume': data.get('volume'),
        'quiesced': bool(data.get('quiesced', True)),
        'create_current': bool(data.get('create_current', True)),
        'max_exports': int(data.get('max_exports') or 0),
        'status': data.get('status'),
        'status_info': data.get('status_info'),
    }


def find_export(client, volume_key):
    """The export configuration on this volume, or None.

    Matched client-side rather than through the SDK's ``get(volume=...)``,
    which builds ``filter="volume eq <key>"`` with the key unquoted. Volume
    keys are 40-character hex strings, so that is a filter literal made of
    unescaped user-adjacent data -- the same shape as pyVergeOS#100. There is
    one export row per volume, so the table this walks is tiny.
    """
    for export in client.volume_vm_exports.list(fields=EXPORT_FIELDS):
        if dict(export).get('volume') == volume_key:
            return export
    return None


def resolve_vm_keys(module, client, names):
    keys = []
    for name in names:
        try:
            vm = resolve_one(module, client.vms, name, 'VM',
                             fields=['$key', 'name'])
        except NotFoundError:
            module.fail_json(msg="VM '%s' not found" % name)
        keys.append(int(dict(vm)['$key']))
    return keys


def latest_stat(client, export_key):
    """The newest statistics row for this export, or None.

    Rows are ordered by the API's own ordering, which is not documented as
    newest-first, so the newest is picked by timestamp rather than position.
    """
    rows = [dict(row) for row in
            client.volume_vm_exports.stats(export_key).list(fields=STAT_FIELDS)]
    if not rows:
        return None
    return max(rows, key=lambda row: int(row.get('timestamp') or 0))


def wait_for_idle(module, client, export_key):
    """Poll until the export leaves the building state.

    `status` flips to 'building' synchronously -- measured at under 10ms
    after start_export returns on 26.1.8 -- so there is no window in which
    this loop could see a stale 'idle' and declare an export finished before
    it began.
    """
    deadline = time.time() + module.params['wait_timeout']
    while True:
        export = client.volume_vm_exports.get(export_key,
                                              fields=EXPORT_FIELDS)
        data = dict(export)
        status = data.get('status')
        if status == 'error':
            module.fail_json(msg='Export finished with error status: %s'
                                 % (data.get('status_info') or 'no detail'))
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
    last_export = None

    try:
        try:
            volume = resolve_one(module, client.nas_volumes, volume_name,
                                 'NAS volume', fields=['$key', 'name'])
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
            changes = {}
            for param, column in UPDATE_FIELD_MAP.items():
                want = module.params[param]
                current = data.get(column)
                if isinstance(want, bool):
                    if bool(current) == want:
                        continue
                elif int(current or 0) == want:
                    continue
                changes[UPDATE_KWARG_MAP[param]] = want
            if changes:
                actions.append("updated export config on '%s': %s"
                               % (volume_name, ', '.join(sorted(changes))))
                changed = True
                if not module.check_mode:
                    export = client.volume_vm_exports.update(
                        data['$key'], **changes)

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
                    # An export can end 'idle' having failed some of its VMs;
                    # the per-run statistics row is the only place that count
                    # appears. Reporting success for a backup that did not
                    # happen is the worst failure mode this module has.
                    #
                    # NOT VERIFIED on a live system: no way was found to make
                    # a VM fail to export on demand. The clean path (errors=0)
                    # is verified by the ladder; this branch is not.
                    last_export = latest_stat(client, export_key)
                    if last_export and int(last_export.get('errors') or 0):
                        module.fail_json(
                            msg='Export finished but recorded %s error(s) '
                                'across %s VM(s)'
                                % (last_export.get('errors'),
                                   last_export.get('virtual_machines')),
                            last_export=last_export)

        module.exit_json(changed=changed, actions=actions,
                         export=export_result(export),
                         last_export=last_export)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
