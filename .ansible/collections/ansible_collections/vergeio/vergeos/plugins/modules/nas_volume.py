#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: nas_volume
short_description: Manage NAS volumes in VergeOS
version_added: "2.1.0"
description:
  - Create, update, and delete local NAS volumes on a VergeOS NAS
    service.
  - Deleting a volume destroys its data. Online volumes cannot be
    deleted; the module disables the volume first, which is the
    required sequence.
options:
  name:
    description:
      - Volume name; the lookup key.
    type: str
    required: true
  state:
    description:
      - Whether the volume should exist.
    type: str
    choices: [ present, absent ]
    default: present
  service:
    description:
      - NAS service (name) to create the volume on.
      - Required when creating; ignored for existing volumes.
    type: str
  size_gb:
    description:
      - Maximum volume size in GB.
      - Required when creating. Drift on an existing volume is
        corrected (grow or shrink — shrinking below used space is
        refused by the API).
    type: int
  tier:
    description:
      - Preferred storage tier (1-5). Applied at creation only.
    type: int
  description:
    description:
      - Volume description.
    type: str
  read_only:
    description:
      - Volume is read-only.
    type: bool
    default: false
  enabled:
    description:
      - Volume is enabled (online).
    type: bool
    default: true
  snapshot_profile:
    description:
      - Name of a snapshot profile to attach. Applied at creation only.
    type: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Backups volume on nas1
  vergeio.vergeos.nas_volume:
    name: backups
    service: nas1
    size_gb: 500
    tier: 3
    description: "VM export target"
    state: present

- name: Grow it
  vergeio.vergeos.nas_volume:
    name: backups
    size_gb: 1000
    state: present

- name: Delete (disables first — online volumes refuse deletion)
  vergeio.vergeos.nas_volume:
    name: backups
    state: absent
'''

RETURN = r'''
volume:
  description: State of the volume after the module ran.
  returned: when state is present
  type: dict
  sample:
    key: 7
    name: "backups"
    size_gb: 500.0
    enabled: true
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
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

GB = 1073741824


def get_volume(client, name):
    try:
        return client.nas_volumes.get(name=name)
    except NotFoundError:
        return None


def volume_result(volume):
    data = dict(volume)
    return {
        'key': data.get('$key'),
        'name': data.get('name'),
        'description': data.get('description'),
        'size_gb': round(int(data.get('maxsize') or 0) / float(GB), 2),
        'read_only': bool(data.get('read_only', False)),
        'enabled': bool(data.get('enabled', True)),
    }


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        service=dict(type='str'),
        size_gb=dict(type='int'),
        tier=dict(type='int'),
        description=dict(type='str'),
        read_only=dict(type='bool', default=False),
        enabled=dict(type='bool', default=True),
        snapshot_profile=dict(type='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    actions = []
    name = module.params['name']

    try:
        volume = get_volume(client, name)

        if module.params['state'] == 'absent':
            if volume is None:
                module.exit_json(changed=False, actions=actions,
                                 msg="Volume '%s' does not exist" % name)
            actions.append("deleted volume '%s' (disabled first)" % name)
            if not module.check_mode:
                key = dict(volume)['$key']
                # Online volumes refuse deletion ("Unable to delete
                # online drive") — disable, settle, then delete.
                if bool(dict(volume).get('enabled', True)):
                    client.nas_volumes.update(key, enabled=False)
                    time.sleep(3)
                client.nas_volumes.delete(key)
            module.exit_json(changed=True, actions=actions)

        changed = False
        if volume is None:
            if not module.params.get('service') or not module.params.get('size_gb'):
                module.fail_json(msg='service and size_gb are required to '
                                     'create a volume')
            actions.append("created volume '%s'" % name)
            changed = True
            if module.check_mode:
                module.exit_json(changed=True, actions=actions,
                                 volume={'name': name})
            create_args = {
                'name': name,
                'service': module.params['service'],
                'size_gb': module.params['size_gb'],
                'read_only': module.params['read_only'],
                'enabled': module.params['enabled'],
            }
            if module.params.get('tier') is not None:
                create_args['tier'] = module.params['tier']
            if module.params.get('description') is not None:
                create_args['description'] = module.params['description']
            if module.params.get('snapshot_profile'):
                profile = client.snapshot_profiles.get(
                    name=module.params['snapshot_profile'])
                create_args['snapshot_profile'] = dict(profile)['$key']
            volume = client.nas_volumes.create(**create_args)
        else:
            data = dict(volume)
            updates = {}
            if module.params.get('size_gb') is not None:
                want_bytes = module.params['size_gb'] * GB
                if int(data.get('maxsize') or 0) != want_bytes:
                    updates['maxsize'] = want_bytes
            if module.params.get('description') is not None and \
                    data.get('description') != module.params['description']:
                updates['description'] = module.params['description']
            for field in ('read_only', 'enabled'):
                if bool(data.get(field, field == 'enabled')) != module.params[field]:
                    updates[field] = module.params[field]
            if updates:
                actions.append("updated volume '%s': %s"
                               % (name, ', '.join(sorted(updates))))
                changed = True
                if not module.check_mode:
                    client.nas_volumes.update(data['$key'], **updates)
                    volume = get_volume(client, name)

        module.exit_json(changed=changed, actions=actions,
                         volume=volume_result(volume))

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
