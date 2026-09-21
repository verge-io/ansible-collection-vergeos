#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: api_key_info
short_description: Gather information about user API keys in VergeOS
version_added: "2.1.0"
description:
  - List per-user API keys, optionally filtered by user and/or name.
  - Secrets are generated server-side and shown exactly once at creation;
    they are never available here or anywhere else after that, so this
    module can be used freely in reports.
  - Requires a VergeOS version with the C(user_api_keys) API table
    (26.x and later).
options:
  user:
    description:
      - Only return keys belonging to this username.
    type: str
  name:
    description:
      - Only return keys with this name.
    type: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: All API keys on the system
  vergeio.vergeos.api_key_info:
  register: all_keys

- name: Keys for one user
  vergeio.vergeos.api_key_info:
    user: automation
  register: automation_keys

- name: A specific key
  vergeio.vergeos.api_key_info:
    user: automation
    name: ansible-runner
  register: runner_key
'''

RETURN = r'''
api_keys:
  description: Matching API keys. Never includes secrets.
  returned: always
  type: list
  elements: dict
  sample:
    - key: 7
      name: "ansible-runner"
      user_name: "automation"
      description: "key used by the Ansible control node"
      created: 1787715355
      ip_allow_list: ["192.0.2.0/24"]
      ip_deny_list: []
      last_login: 1787716001
      last_login_ip: "192.0.2.10"
'''

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


def split_ip_list(value):
    if not value:
        return []
    return [item for item in str(value).split(',') if item]


def key_result(key_obj):
    data = dict(key_obj)
    return {
        'key': data.get('$key'),
        'name': data.get('name'),
        'user_name': data.get('user_name'),
        'description': data.get('description') or '',
        'created': data.get('created'),
        'ip_allow_list': split_ip_list(data.get('ip_allow_list')),
        'ip_deny_list': split_ip_list(data.get('ip_deny_list')),
        'last_login': data.get('lastlogin_stamp'),
        'last_login_ip': data.get('lastlogin_ip') or '',
    }


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        user=dict(type='str'),
        name=dict(type='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    user = module.params.get('user')
    name = module.params.get('name')

    try:
        keys = []
        for key_obj in client.api_keys.list():
            data = dict(key_obj)
            if user is not None and data.get('user_name') != user:
                continue
            if name is not None and data.get('name') != name:
                continue
            keys.append(key_result(key_obj))
        module.exit_json(changed=False, api_keys=keys)

    except (NotFoundError, AuthenticationError, ValidationError, APIError,
            VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg="Unexpected error: %s" % e)


if __name__ == '__main__':
    main()
