#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: auth_source_info
short_description: Gather information about VergeOS authentication sources
version_added: "2.1.0"
description:
  - List authentication sources (SSO/OIDC providers), optionally with
    their settings documents.
  - The client secret is B(always stripped) from returned settings. The
    raw API returns it in cleartext to any reader; this module never
    passes it on.
options:
  name:
    description:
      - Return only the source with this name. An unknown name returns
        an empty list, not an error.
    type: str
  include_settings:
    description:
      - Include each source's settings document (minus
        C(client_secret)) in the result.
    type: bool
    default: false
notes:
  - This module does not modify the system and always returns
    C(changed: false).
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: List every auth source
  vergeio.vergeos.auth_source_info:
  register: sso

- name: Inspect one provider's configuration (secret is stripped)
  vergeio.vergeos.auth_source_info:
    name: Corporate Azure
    include_settings: true
  register: azure
'''

RETURN = r'''
auth_sources:
  description: The matching auth sources.
  returned: always
  type: list
  elements: dict
  sample:
    - key: 3
      name: "Corporate Azure"
      driver: "azure"
      menu: false
      debug: false
      button_background_color: ""
      button_color: ""
      button_fa_icon: "bi-microsoft"
      icon_color: ""
      settings:
        client_id: "app-registration-id"
        scope: "openid profile email"
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

SECRET_KEYS = ('client_secret',)


def source_result(source, include_settings):
    data = dict(source)
    result = {
        'key': data.get('$key'),
        'name': data.get('name'),
        'driver': data.get('driver'),
        'menu': bool(data.get('menu')),
        'debug': bool(data.get('debug')),
        'button_background_color': data.get('button_background_color') or '',
        'button_color': data.get('button_color') or '',
        'button_fa_icon': data.get('button_fa_icon') or '',
        'icon_color': data.get('icon_color') or '',
    }
    if include_settings:
        settings = dict(data.get('settings') or {})
        for key in SECRET_KEYS:
            settings.pop(key, None)
        result['settings'] = settings
    return result


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
        include_settings=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    include_settings = module.params['include_settings']

    try:
        if module.params.get('name'):
            try:
                sources = [client.auth_sources.get(
                    name=module.params['name'],
                    include_settings=include_settings)]
            except NotFoundError:
                sources = []
        elif include_settings:
            # list() does not carry settings; fetch each source fully
            sources = [client.auth_sources.get(dict(s)['$key'],
                                               include_settings=True)
                       for s in client.auth_sources.list()]
        else:
            sources = list(client.auth_sources.list())

        module.exit_json(changed=False, auth_sources=[
            source_result(s, include_settings) for s in sources])

    except (AuthenticationError, ValidationError, APIError,
            VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg="Unexpected error: %s" % e)


if __name__ == '__main__':
    main()
