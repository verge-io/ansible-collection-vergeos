#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: nas_nfs_share
short_description: Manage NFS shares on VergeOS NAS volumes
version_added: "2.2.0"
description:
  - Create, update, and delete NFS shares on a NAS volume, so exported
    data (for example VM exports) is reachable by external tooling.
  - Shares are matched by O(name) on the given volume.
options:
  name:
    description:
      - Share name; the match key on the volume.
    type: str
    required: true
  volume:
    description:
      - Name of the NAS volume the share exposes.
    type: str
    required: true
  state:
    description:
      - Whether the share should exist.
    type: str
    choices: [ present, absent ]
    default: present
  share_path:
    description:
      - Path within the volume to share (empty shares the whole
        volume).
      - Applied at creation only. There is no way to change the path of
        an existing share, so a different path is a different share.
    type: str
  description:
    description:
      - Share description.
    type: str
  allowed_hosts:
    description:
      - Hosts allowed to mount (IPs, CIDRs, FQDNs).
      - Required unless O(allow_all) is true.
    type: list
    elements: str
  allow_all:
    description:
      - Allow any host to mount.
    type: bool
    default: false
  data_access:
    description:
      - C(ro) read-only or C(rw) read-write.
    type: str
    choices: [ ro, rw ]
    default: ro
  squash:
    description:
      - User/group squashing mode.
    type: str
    choices: [ root_squash, all_squash, no_root_squash ]
    default: root_squash
  async_mode:
    description:
      - Async NFS (faster, risks data loss on service crash).
    type: bool
    default: false
  insecure_ports:
    description:
      - Allow client connections from non-privileged ports (the NFS
        C(insecure) export option; named to avoid the connection
        option C(insecure)).
    type: bool
    default: false
  no_acl:
    description:
      - Disable ACL support on the share.
    type: bool
    default: false
  enabled:
    description:
      - Share is enabled.
    type: bool
    default: true
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Read-only export share for the backup tooling host
  vergeio.vergeos.nas_nfs_share:
    name: backups
    volume: backups
    allowed_hosts: [192.0.2.10]
    data_access: ro
    state: present

- name: Open share on the whole volume (lab only)
  vergeio.vergeos.nas_nfs_share:
    name: scratch
    volume: scratch
    allow_all: true
    data_access: rw

- name: Remove the share
  vergeio.vergeos.nas_nfs_share:
    name: backups
    volume: backups
    state: absent
'''

RETURN = r'''
share:
  description: State of the share after the module ran.
  returned: when state is present
  type: dict
  sample:
    name: "backups"
    volume: "backups"
    data_access: "ro"
    allowed_hosts: [ "192.0.2.10" ]
    enabled: true
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
'''

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

# Module parameter -> raw API column on `volume_nfs_shares`, checked against a
# live share row on VergeOS 26.1.8 (20 columns) before the rest of this module
# was touched. Two parameters are NOT their column:
#
#   async_mode     -> async       `async` is a Python keyword, so neither the
#                                 SDK nor an Ansible option can be called it
#   insecure_ports -> insecure    renamed to avoid the connection option
#
# The first of those was a live bug (issue #75's class, ninth instance). The
# module kept one dict and used it for BOTH the column it read back and the
# keyword it wrote, so the read side asked a share row for `async_mode`, got
# None, and compared bool(None) against the parameter. Anyone setting
# async_mode=true got changed=true on every run, forever, and a PUT each time.
# Anyone leaving it false converged by accident, which is why the ladder that
# "live-verified" this module went green.
UPDATE_FIELD_MAP = {
    'description': 'description',
    'allowed_hosts': 'allowed_hosts',
    'allow_all': 'allow_all',
    'data_access': 'data_access',
    'squash': 'squash',
    'async_mode': 'async',
    'insecure_ports': 'insecure',
    'no_acl': 'no_acl',
    'enabled': 'enabled',
}

# Module parameter -> the keyword the SDK's update() takes. Columns and
# keywords are two different namespaces and they disagree for exactly one
# parameter -- which is the whole trap above, kept visible rather than
# implied. Checked against the real signature in
# tests/unit/test_sdk_call_signatures.py.
UPDATE_KWARG_MAP = {
    'description': 'description',
    'allowed_hosts': 'allowed_hosts',
    'allow_all': 'allow_all',
    'data_access': 'data_access',
    'squash': 'squash',
    'async_mode': 'async_mode',
    'insecure_ports': 'insecure',
    'no_acl': 'no_acl',
    'enabled': 'enabled',
}

# The share's identity. name and volume are what it is; share_path is here
# because the SDK's update() has no parameter for it -- a share of a different
# path is a different share, not a changed one.
CREATE_PARAM_MAP = dict(UPDATE_FIELD_MAP, name='name', volume='volume',
                        share_path='share_path')
IDENTITY_PARAMS = ('name', 'volume', 'share_path')

COMPARISON_FIELDS = tuple(sorted(set(UPDATE_FIELD_MAP.values())))
SHARE_FIELDS = ['$key', 'name', 'volume'] + list(COMPARISON_FIELDS)


def hosts_to_set(value):
    if value is None:
        return set()
    if isinstance(value, str):
        return {h.strip() for h in value.split(',') if h.strip()}
    return {str(h).strip() for h in value if str(h).strip()}


def find_share(client, volume_key, name):
    """The named share on this volume, matched by volume KEY.

    Not by the volume's display name: `volume` is a real column holding the
    key, while volume_name is a join. Two volumes may share a name (#72), and
    a join that is not asked for reads as None.
    """
    for share in client.nfs_shares.list(volume=volume_key,
                                        fields=SHARE_FIELDS):
        data = dict(share)
        if data.get('name') == name:
            return share
    return None


def share_result(share, volume_name):
    data = dict(share)
    return {
        'key': data.get('$key'),
        'name': data.get('name'),
        'volume': volume_name,
        'description': data.get('description'),
        'data_access': data.get('data_access'),
        'allow_all': bool(data.get('allow_all', False)),
        'allowed_hosts': sorted(hosts_to_set(data.get('allowed_hosts'))),
        'squash': data.get('squash'),
        'async_mode': bool(data.get('async', False)),
        'insecure_ports': bool(data.get('insecure', False)),
        'no_acl': bool(data.get('no_acl', False)),
        'enabled': bool(data.get('enabled', True)),
    }


def desired_changes(module, data):
    """Parameters that differ from the live row, as SDK update keywords."""
    changes = {}
    for param, column in UPDATE_FIELD_MAP.items():
        want = module.params.get(param)
        if want is None:
            continue
        current = data.get(column)

        if param == 'allowed_hosts':
            if hosts_to_set(current) == hosts_to_set(want):
                continue
            want = ','.join(want)
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
        volume=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        share_path=dict(type='str'),
        description=dict(type='str'),
        allowed_hosts=dict(type='list', elements='str'),
        allow_all=dict(type='bool', default=False),
        data_access=dict(type='str', default='ro', choices=['ro', 'rw']),
        squash=dict(type='str', default='root_squash',
                    choices=['root_squash', 'all_squash', 'no_root_squash']),
        async_mode=dict(type='bool', default=False),
        insecure_ports=dict(type='bool', default=False),
        no_acl=dict(type='bool', default=False),
        enabled=dict(type='bool', default=True),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    actions = []
    name = module.params['name']
    volume_name = module.params['volume']

    try:
        try:
            volume = resolve_one(module, client.nas_volumes, volume_name,
                                 'NAS volume', fields=['$key', 'name'])
        except NotFoundError:
            module.fail_json(msg="NAS volume '%s' not found" % volume_name)
        volume_key = dict(volume)['$key']

        share = find_share(client, volume_key, name)

        if module.params['state'] == 'absent':
            if share is None:
                module.exit_json(changed=False, actions=actions,
                                 msg="Share '%s' does not exist" % name)
            actions.append("deleted share '%s'" % name)
            if not module.check_mode:
                client.nfs_shares.delete(dict(share)['$key'])
            module.exit_json(changed=True, actions=actions)

        if not module.params['allow_all'] and not module.params.get('allowed_hosts'):
            module.fail_json(msg='allowed_hosts is required unless '
                                 'allow_all is true')

        changed = False
        if share is None:
            actions.append("created share '%s' on '%s'" % (name, volume_name))
            changed = True
            if module.check_mode:
                module.exit_json(changed=True, actions=actions,
                                 share={'name': name, 'volume': volume_name})
            create_args = {
                'name': name,
                'volume': volume_key,
                'allow_all': module.params['allow_all'],
                'data_access': module.params['data_access'],
                'squash': module.params['squash'],
                'async_mode': module.params['async_mode'],
                'insecure': module.params['insecure_ports'],
                'no_acl': module.params['no_acl'],
                'enabled': module.params['enabled'],
            }
            if module.params.get('allowed_hosts'):
                create_args['allowed_hosts'] = ','.join(
                    module.params['allowed_hosts'])
            if module.params.get('share_path'):
                create_args['share_path'] = module.params['share_path']
            if module.params.get('description') is not None:
                create_args['description'] = module.params['description']
            share = client.nfs_shares.create(**create_args)
        else:
            data = dict(share)
            changes = desired_changes(module, data)
            if changes:
                actions.append("updated share '%s': %s"
                               % (name, ', '.join(sorted(changes))))
                changed = True
                if not module.check_mode:
                    share = client.nfs_shares.update(data['$key'], **changes)

        module.exit_json(changed=changed, actions=actions,
                         share=share_result(share, volume_name))

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
