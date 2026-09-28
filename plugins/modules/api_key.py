#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: api_key
short_description: Manage user API keys in VergeOS
version_added: "2.2.0"
description:
  - Create, update, and delete per-user API keys (bearer tokens) so
    automation credentials can be managed - and rotated - as code.
  - Keys are matched by O(user) and O(name).
  - The key's secret is generated server-side and returned B(exactly
    once), on creation, as RV(secret). It cannot be retrieved later;
    store it from the creation task (see the examples).
  - Requires a VergeOS version with the C(user_api_keys) API table
    (26.x and later).
options:
  user:
    description:
      - Username the key belongs to.
    type: str
    required: true
  name:
    description:
      - Key name, the match key for idempotence within the user.
    type: str
    required: true
  state:
    description:
      - Whether the key should exist.
      - C(absent) removes B(every) key of O(user) with O(name), so a
        crashed run's leftovers are cleaned up too.
    type: str
    choices: [ present, absent ]
    default: present
  description:
    description:
      - Free-form description of the key.
    type: str
  ip_allow_list:
    description:
      - IPs/CIDRs allowed to use the key. Order is preserved and
        compared exactly.
    type: list
    elements: str
  ip_deny_list:
    description:
      - IPs/CIDRs denied from using the key.
    type: list
    elements: str
notes:
  - Key expiry is deliberately not exposed - on VergeOS 26.1.8 an
    C(expires_in) sent at creation was observed to leave C(expires) at 0.
    Until that platform behavior is understood, rotate keys with this
    module instead of relying on server-side expiry.
  - Supports C(check_mode).
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Create an automation key (secret is only available this once)
  vergeio.vergeos.api_key:
    user: automation
    name: ansible-runner
    description: "key used by the Ansible control node"
    ip_allow_list:
      - 192.0.2.0/24
    state: present
  register: runner_key
  no_log: true

- name: Persist the secret with owner-only permissions
  ansible.builtin.copy:
    content: "{{ runner_key.secret }}"
    dest: /root/.vergeos_token
    mode: "0600"
  when: runner_key.secret is defined
  no_log: true

- name: Tighten the allow list (idempotent update)
  vergeio.vergeos.api_key:
    user: automation
    name: ansible-runner
    ip_allow_list:
      - 192.0.2.10/32
    state: present

- name: Revoke a key
  vergeio.vergeos.api_key:
    user: automation
    name: ansible-runner
    state: absent
'''

RETURN = r'''
api_key:
  description: State of the key after the module ran (never includes the
    secret).
  returned: when state is present
  type: dict
  sample:
    key: 7
    name: "ansible-runner"
    user_name: "automation"
    description: "key used by the Ansible control node"
    created: 1787715355
    ip_allow_list: ["192.0.2.0/24"]
    ip_deny_list: []
    last_login: 0
    last_login_ip: ""
secret:
  description: The bearer secret, generated server-side. Returned only
    when the module created the key - it cannot be retrieved afterwards.
    Register the task with C(no_log) and store it immediately.
  returned: on creation
  type: str
  sample: "0123abcd..."
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
  sample: ["created key 'ansible-runner' for user 'automation'"]
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.api_keys import (
    LIST_FIELDS,
    join_ip_list,
    key_result,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


# Module parameter -> API column. Both maps were checked against a live
# user_api_keys row on 26.1.8 (11 columns); every name below is real. Writing
# them out opts this module into the guard in
# tests/unit/plugins/modules/test_field_contracts.py (#75) -- the guard that
# found #87 on vm, where three parameters were not columns at all.
CREATE_PARAM_MAP = {
    'user': 'user',
    'name': 'name',
    'description': 'description',
    'ip_allow_list': 'ip_allow_list',
    'ip_deny_list': 'ip_deny_list',
}

# user and name are the key's identity, not settings: changing either means a
# different key, which is a create plus a revoke and not something this module
# does behind your back.
UPDATE_FIELD_MAP = {
    'description': 'description',
    'ip_allow_list': 'ip_allow_list',
    'ip_deny_list': 'ip_deny_list',
}

IDENTITY_PARAMS = ('user', 'name')

# Fetched before diffing. #18: a field diffed but never fetched reads as None,
# so the module reports changed forever.
COMPARISON_FIELDS = tuple(sorted(set(UPDATE_FIELD_MAP.values())))


def find_keys(client, user, name):
    """All keys matching (user, name), matched client-side.

    Two keys can share a name across users, so the match is on the PAIR. The
    columns are requested explicitly (LIST_FIELDS) rather than left to the
    SDK's default projection: user_name is a join rather than a stored column,
    and if a future projection dropped it every row would compare unequal,
    every run would create another key, and nothing would fail.
    """
    matches = []
    for key_obj in client.api_keys.list(fields=LIST_FIELDS):
        data = dict(key_obj)
        if data.get('name') == name and data.get('user_name') == user:
            matches.append(key_obj)
    return matches


def create_key(module, client, actions):
    params = module.params
    actions.append("created key '%s' for user '%s'"
                   % (params['name'], params['user']))
    if module.check_mode:
        return None, None

    create_args = {
        CREATE_PARAM_MAP['user']: params['user'],
        CREATE_PARAM_MAP['name']: params['name'],
    }
    for param, api_field in sorted(CREATE_PARAM_MAP.items()):
        if param in IDENTITY_PARAMS:
            continue
        if params.get(param):
            create_args[api_field] = params[param]

    created = client.api_keys.create(**create_args)
    # create() returns only the id and the once-only secret, so the row is
    # read back for the return value.
    key_obj = client.api_keys.get(created.key, fields=LIST_FIELDS)
    return key_obj, created.secret


def update_key(module, client, key_obj, actions):
    params = module.params
    current = dict(key_obj)
    changes = {}

    for param, api_field in sorted(UPDATE_FIELD_MAP.items()):
        if params.get(param) is None:
            continue
        if param in ('ip_allow_list', 'ip_deny_list'):
            wanted = join_ip_list(params[param])
        else:
            wanted = params[param]
        if (current.get(api_field) or '') != wanted:
            changes[api_field] = wanted

    if not changes:
        return False, key_obj

    actions.append("updated key '%s': %s"
                   % (params['name'], ', '.join(sorted(changes))))
    if module.check_mode:
        return True, key_obj
    client.api_keys.update(current['$key'], **changes)
    # Read back rather than trusting the PUT response: the SDK builds its
    # model from whatever the PUT returned, which is not a full row, so
    # key_result() would report Nones for everything the caller just set.
    key_obj = client.api_keys.get(current['$key'], fields=LIST_FIELDS)
    return True, key_obj


def delete_keys(module, client, matches, actions):
    for key_obj in matches:
        data = dict(key_obj)
        actions.append("deleted key '%s' (id %s)"
                       % (data.get('name'), data.get('$key')))
        if not module.check_mode:
            client.api_keys.delete(data['$key'])
    return bool(matches)


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        user=dict(type='str', required=True),
        name=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        description=dict(type='str'),
        ip_allow_list=dict(type='list', elements='str'),
        ip_deny_list=dict(type='list', elements='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    actions = []

    try:
        matches = find_keys(client, module.params['user'],
                            module.params['name'])

        if module.params['state'] == 'absent':
            changed = delete_keys(module, client, matches, actions)
            module.exit_json(changed=changed, actions=actions)

        # state: present
        if len(matches) > 1:
            module.fail_json(
                msg="%d keys named '%s' exist for user '%s'; refusing to "
                    "guess. Run state: absent first to clear them, then "
                    "recreate." % (len(matches), module.params['name'],
                                   module.params['user']))

        if not matches:
            key_obj, secret = create_key(module, client, actions)
            result = {'changed': True, 'actions': actions}
            if key_obj is not None:
                result['api_key'] = key_result(key_obj)
                result['secret'] = secret
            module.exit_json(**result)

        changed, key_obj = update_key(module, client, matches[0], actions)
        module.exit_json(changed=changed, actions=actions,
                         api_key=key_result(key_obj))

    except (AuthenticationError, ValidationError, APIError,
            VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except NotFoundError as e:
        module.fail_json(msg="Resource not found: %s" % e)
    except Exception as e:
        module.fail_json(msg="Unexpected error: %s" % e)


if __name__ == '__main__':
    main()
