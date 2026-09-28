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
version_added: "2.2.0"
description:
  - Create, update, and delete local NAS volumes on a VergeOS NAS
    service.
  - Deleting a volume destroys its data. A mounted volume refuses
    deletion, so the module disables it first and retries until the
    underlying drive is no longer online.
options:
  name:
    description:
      - Volume name; the lookup key. Renaming is not supported.
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
      - Required when creating. A volume cannot be moved between
        services, so this is ignored for volumes that already exist.
    type: str
  size_gb:
    description:
      - Maximum volume size in GB.
      - Required when creating. Drift on an existing volume is
        corrected; shrinking below the space already used is refused by
        the API.
    type: int
  tier:
    description:
      - Preferred storage tier (1-5). Drift is corrected.
      - This is a preference, not a guarantee, and VergeOS does not
        check that the tier exists.
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
      - Name of a snapshot profile to attach. Drift is corrected.
      - Omitting this leaves whatever profile is attached alone.
        Detaching a profile is not expressible here, because the SDK's
        update reads "no profile given" as "leave it alone"; detach it
        in the UI or with a direct API call.
    type: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Backups volume on nas-01
  vergeio.vergeos.nas_volume:
    name: backups
    service: nas-01
    size_gb: 500
    tier: 3
    description: "VM export target"
    state: present

- name: Grow it
  vergeio.vergeos.nas_volume:
    name: backups
    size_gb: 1000
    state: present

- name: Delete it (disables first -- a mounted volume refuses deletion)
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
    key: "c3437883534918dcf2abbb3e9b9622b865226c68"
    name: "backups"
    size_gb: 500.0
    tier: 3
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

GB = 1073741824

# Module parameter -> raw API column on `volumes`. Every name here was checked
# against a live volume row on VergeOS 26.1.8 (38 columns) before anything
# else in this module was written -- see issue #75 and the guard in
# tests/unit/plugins/modules/test_field_contracts.py.
#
# Two of the six are renames, and both are the #8 shape:
#
#   size_gb -> maxsize         the column is a BYTE count, not GB
#   tier    -> preferred_tier  exactly #8's drive defect, on a different table
#
# preferred_tier is also stored as a STRING ('1', not 1), so it is compared as
# an int. And when no tier is given at create, VergeOS defaults the column to
# '4' regardless of which tiers exist.
UPDATE_FIELD_MAP = {
    'description': 'description',
    'size_gb': 'maxsize',
    'tier': 'preferred_tier',
    'read_only': 'read_only',
    'enabled': 'enabled',
    'snapshot_profile': 'snapshot_profile',
}

# Module parameter -> the keyword the SDK's update() takes.
#
# This map exists because the previous version of this module did not have it,
# and sent the COLUMN names to the SDK:
#
#     client.nas_volumes.update(key, maxsize=21474836480)
#     TypeError: NASVolumeManager.update() got an unexpected keyword argument
#
# NASVolumeManager.update() is keyword-only and translates on the way out --
# it takes size_gb and multiplies, takes tier and stringifies. So every resize
# raised, was swallowed by the module's catch-all, and surfaced as "Unexpected
# error". Unit tests could not see it: the manager was a MagicMock, which
# accepts any keyword at all (#92).
#
# tests/unit/test_sdk_call_signatures.py now checks every value here against
# the real signature, which is the only thing that can.
UPDATE_KWARG_MAP = {
    'description': 'description',
    'size_gb': 'size_gb',
    'tier': 'tier',
    'read_only': 'read_only',
    'enabled': 'enabled',
    'snapshot_profile': 'snapshot_profile',
}

# name and service are the volume's identity: a volume cannot be renamed and
# cannot be moved to another service, so neither is a setting.
CREATE_PARAM_MAP = dict(UPDATE_FIELD_MAP, name='name', service='service')
IDENTITY_PARAMS = ('name', 'service')

COMPARISON_FIELDS = tuple(sorted(set(UPDATE_FIELD_MAP.values())))

# `fields=all` on the volumes table does NOT include $key -- it returns `id`
# instead -- so $key is asked for by name. A key that reads as None makes every
# update and delete target nothing.
VOLUME_FIELDS = ['$key', 'name'] + list(COMPARISON_FIELDS)

# A mounted volume refuses deletion with "Unable to delete online drive".
# Disabling clears it, but not instantly and not in step with the `mounted`
# flag: measured on 26.1.8, `mounted` went false at +1.0s while the delete was
# still refused, then succeeded on the next attempt. Polling `mounted` would
# be a race dressed as a check, so the module retries the delete itself --
# the only predicate that is the thing we actually want to know.
DELETE_SETTLE_SECONDS = 60
DELETE_RETRY_INTERVAL = 2
ONLINE_DRIVE_MARKER = 'online drive'


def get_volume(module, client, name):
    """One volume by name, refusing to guess between duplicates (#72/#85)."""
    try:
        return resolve_one(module, client.nas_volumes, name, 'NAS volume',
                           fields=VOLUME_FIELDS)
    except NotFoundError:
        return None


def resolve_service_key(module, client, name):
    try:
        service = resolve_one(module, client.nas_services, name,
                              'NAS service', fields=['$key', 'name'])
    except NotFoundError:
        module.fail_json(msg="NAS service '%s' not found" % name)
    return int(dict(service)['$key'])


def resolve_profile_key(module, client, name):
    try:
        profile = resolve_one(module, client.snapshot_profiles, name,
                              'snapshot profile', fields=['$key', 'name'])
    except NotFoundError:
        module.fail_json(msg="Snapshot profile '%s' not found" % name)
    return int(dict(profile)['$key'])


def volume_result(volume):
    data = dict(volume)
    tier = data.get('preferred_tier')
    return {
        'key': data.get('$key'),
        'name': data.get('name'),
        'description': data.get('description'),
        'size_gb': round(int(data.get('maxsize') or 0) / float(GB), 2),
        'tier': int(tier) if tier not in (None, '') else None,
        'read_only': bool(data.get('read_only', False)),
        'enabled': bool(data.get('enabled', True)),
        'snapshot_profile': data.get('snapshot_profile'),
    }


def delete_volume(module, client, volume):
    """Disable, then retry the delete until the drive is no longer online."""
    data = dict(volume)
    key = data['$key']

    if bool(data.get('enabled', True)):
        client.nas_volumes.update(key, enabled=False)

    deadline = time.time() + DELETE_SETTLE_SECONDS
    while True:
        try:
            client.nas_volumes.delete(key)
            return
        except APIError as exc:
            if ONLINE_DRIVE_MARKER not in str(exc):
                raise
            if time.time() >= deadline:
                module.fail_json(
                    msg="Volume '%s' was still online %ds after being "
                        'disabled, so it could not be deleted: %s'
                        % (data.get('name'), DELETE_SETTLE_SECONDS, exc))
            time.sleep(DELETE_RETRY_INTERVAL)


def desired_changes(module, client, data):
    """Parameters that differ from the live row, as SDK update keywords."""
    changes = {}

    for param, column in UPDATE_FIELD_MAP.items():
        want = module.params.get(param)
        if want is None:
            continue
        current = data.get(column)

        if param == 'size_gb':
            if int(current or 0) == want * GB:
                continue
        elif param == 'tier':
            if current not in (None, '') and int(current) == want:
                continue
        elif param == 'snapshot_profile':
            want = resolve_profile_key(module, client, want)
            if current is not None and int(current) == want:
                continue
        elif isinstance(want, bool):
            if bool(current) == want:
                continue
        elif (current or '') == want:
            continue

        changes[UPDATE_KWARG_MAP[param]] = want

    return changes


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
        volume = get_volume(module, client, name)

        if module.params['state'] == 'absent':
            if volume is None:
                module.exit_json(changed=False, actions=actions,
                                 msg="Volume '%s' does not exist" % name)
            actions.append("deleted volume '%s' (disabled first)" % name)
            if not module.check_mode:
                delete_volume(module, client, volume)
            module.exit_json(changed=True, actions=actions)

        changed = False
        if volume is None:
            if not module.params.get('service') \
                    or module.params.get('size_gb') is None:
                module.fail_json(msg='service and size_gb are required to '
                                     'create a volume')
            actions.append("created volume '%s'" % name)
            changed = True
            if module.check_mode:
                module.exit_json(changed=True, actions=actions,
                                 volume={'name': name})
            create_args = {
                'name': name,
                'service': resolve_service_key(module, client,
                                               module.params['service']),
                'size_gb': module.params['size_gb'],
                'read_only': module.params['read_only'],
                'enabled': module.params['enabled'],
            }
            if module.params.get('tier') is not None:
                create_args['tier'] = module.params['tier']
            if module.params.get('description') is not None:
                create_args['description'] = module.params['description']
            if module.params.get('snapshot_profile'):
                create_args['snapshot_profile'] = resolve_profile_key(
                    module, client, module.params['snapshot_profile'])
            volume = client.nas_volumes.create(**create_args)
        else:
            data = dict(volume)
            changes = desired_changes(module, client, data)
            if changes:
                actions.append("updated volume '%s': %s"
                               % (name, ', '.join(sorted(changes))))
                changed = True
                if not module.check_mode:
                    client.nas_volumes.update(data['$key'], **changes)
                    volume = get_volume(module, client, name)

        module.exit_json(changed=changed, actions=actions,
                         volume=volume_result(volume))

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
