#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: auth_source
short_description: Manage authentication sources (SSO/OIDC) in VergeOS
version_added: "2.2.0"
description:
  - Create, update, and delete external authentication sources (Azure
    AD, Google, Okta, generic OpenID Connect, OAuth2, ...) so SSO
    configuration can be managed - and drift-checked - as code.
  - Sources are matched by O(name).
  - B(The settings document is authoritative.) On VergeOS 26.1.8 a
    settings update B(replaces) the stored settings wholesale - a
    partial document silently destroys the keys it omits (client_id,
    client_secret, endpoints). This module therefore always sends the
    complete O(settings) you declare and reports drift against it.
options:
  name:
    description:
      - Display name of the auth source (appears on the login screen),
        the match key for idempotence.
    type: str
    required: true
  state:
    description:
      - Whether the source should exist.
      - Deleting fails while users or OIDC applications still reference
        the source; the error says so.
    type: str
    choices: [ present, absent ]
    default: present
  driver:
    description:
      - Identity provider type. Required when O(state=present).
      - Cannot be changed after creation; the module fails loudly if an
        existing source's driver differs.
    type: str
    choices: [ azure, google, gitlab, okta, openid, openid-well-known, oauth2, verge.io ]
  settings:
    description:
      - Driver-specific configuration (client_id, client_secret,
        tenant_id, scope, endpoints, user-sync flags, ...) as one
        complete document.
      - Compared key-for-key against the live settings (the
        server-managed C(debug) mirror is ignored); any difference
        replaces the stored settings with exactly this document.
    type: dict
  menu:
    description:
      - Show the source in the login dropdown menu instead of as a
        button.
    type: bool
  debug:
    description:
      - Enable provider debug logging.
    type: bool
  button_background_color:
    description:
      - Login button background (CSS color).
    type: str
  button_color:
    description:
      - Login button text color (CSS color).
    type: str
  button_fa_icon:
    description:
      - Login button icon (Bootstrap Icon class, e.g. C(bi-google)).
    type: str
  icon_color:
    description:
      - Login button icon color (CSS color).
    type: str
notes:
  - The raw API returns C(client_secret) in cleartext to any reader of
    the settings; this module never includes settings in its return
    value, and O(settings) is C(no_log).
  - The SDK's own documentation for the underlying update says settings
    are "merged with existing". They are not. Measured on 26.1.8, a PUT
    naming only C(scope) left the stored document holding C(scope) and
    nothing else - C(client_id), C(client_secret) and the endpoints were
    gone. Following that advice destroys a working SSO configuration,
    which is why this module always sends the whole document.
  - Supports C(check_mode).
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Corporate Azure AD login
  vergeio.vergeos.auth_source:
    name: Corporate Azure
    driver: azure
    settings:
      tenant_id: "{{ azure_tenant_id }}"
      client_id: "{{ azure_client_id }}"
      client_secret: "{{ azure_client_secret }}"
      scope: openid profile email
      update_user_email: true
    button_fa_icon: bi-microsoft
    state: present

- name: Same source, drift-checked in CI (any settings change reports changed)
  vergeio.vergeos.auth_source:
    name: Corporate Azure
    driver: azure
    settings:
      tenant_id: "{{ azure_tenant_id }}"
      client_id: "{{ azure_client_id }}"
      client_secret: "{{ azure_client_secret }}"
      scope: openid profile email
      update_user_email: true
    state: present
  check_mode: true

- name: Remove a retired provider
  vergeio.vergeos.auth_source:
    name: Old Okta
    state: absent
'''

RETURN = r'''
auth_source:
  description: State of the source after the module ran. Never includes
    the settings document (it contains the client secret).
  returned: when state is present
  type: dict
  sample:
    key: 3
    name: "Corporate Azure"
    driver: "azure"
    menu: false
    debug: false
    button_background_color: ""
    button_color: ""
    button_fa_icon: "bi-microsoft"
    icon_color: ""
settings_changed:
  description: Whether the settings document was (or would be) replaced.
  returned: when state is present
  type: bool
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
  sample: ["replaced settings of 'Corporate Azure'"]
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

# Module parameter -> raw API column on `auth_sources`. Every name checked
# against a live row on VergeOS 26.1.8 (16 columns) before anything else here
# was touched -- see issue #75. No renames: all eight are real columns, which
# is worth recording as a fact rather than an absence.
UPDATE_FIELD_MAP = {
    'settings': 'settings',
    'menu': 'menu',
    'debug': 'debug',
    'button_background_color': 'button_background_color',
    'button_color': 'button_color',
    'button_fa_icon': 'button_fa_icon',
    'icon_color': 'icon_color',
}

# `driver` is create-only: the API accepts no change to it, and swapping the
# provider under an existing source would silently repoint everyone who logs
# in through it. The module fails loudly instead.
CREATE_PARAM_MAP = dict(UPDATE_FIELD_MAP, name='name', driver='driver')
IDENTITY_PARAMS = ('name', 'driver')

COMPARISON_FIELDS = tuple(sorted(set(UPDATE_FIELD_MAP.values())))

# Named rather than left to the SDK's default projection (#18, #92). `settings`
# is fetched separately -- see find_source.
SOURCE_FIELDS = ['$key', 'name', 'driver'] + [
    f for f in COMPARISON_FIELDS if f != 'settings']

# Row fields the module manages besides settings. driver is immutable
# and handled separately.
SIMPLE_FIELDS = ('menu', 'debug', 'button_background_color',
                 'button_color', 'button_fa_icon', 'icon_color')

# Keys the server injects into the stored settings document; comparing
# them against the declared document would report phantom drift. Measured:
# a source created with four settings keys reads back with a fifth, `debug`.
SERVER_SETTINGS_KEYS = ('debug',)


def find_source(module, client, name, include_settings):
    """The named source, or None.

    Two calls rather than one ``get(name=...)``, for a reason that is NOT the
    usual one. Auth source names are genuinely unique -- the API answers a
    second source of the same name with ``Validation error: unique
    constraint`` -- so #72/#85's "refuse to guess between duplicates" does not
    arise here. What does arise is pyVergeOS#100: ``get(name=)`` builds an
    OData filter from the name, and the brace-stripping fix for that landed in
    pyvergeos 1.2.8, one patch above this collection's floor. A display name is
    free text on the login screen. Matching client-side has no escaping
    surface at all.
    """
    try:
        found = resolve_one(module, client.auth_sources, name, 'auth source',
                            fields=SOURCE_FIELDS)
    except NotFoundError:
        return None
    if not include_settings:
        return found
    # settings comes back only when asked for by key.
    return client.auth_sources.get(int(dict(found)['$key']),
                                   include_settings=True)


def source_result(source):
    data = dict(source)
    return {
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


def _without_server_keys(document):
    return {k: v for k, v in (document or {}).items()
            if k not in SERVER_SETTINGS_KEYS}


def settings_differ(current, desired):
    """Full-document comparison, ignoring server-injected keys."""
    return _without_server_keys(current) != _without_server_keys(desired)


def create_source(module, client, actions):
    params = module.params
    actions.append("created auth source '%s' (driver %s)"
                   % (params['name'], params['driver']))
    if module.check_mode:
        return None

    create_args = {'name': params['name'], 'driver': params['driver']}
    if params.get('settings') is not None:
        create_args['settings'] = params['settings']
    if params.get('menu'):
        create_args['menu'] = True
    for field in ('button_background_color', 'button_color',
                  'button_fa_icon', 'icon_color'):
        if params.get(field) is not None:
            create_args[field] = params[field]

    source = client.auth_sources.create(**create_args)
    # create() has no debug parameter; apply it as a follow-up
    if params.get('debug'):
        source = client.auth_sources.update(source.key, debug=True)
    return source


def update_source(module, client, source, actions):
    params = module.params
    current = dict(source)

    if current.get('driver') != params['driver']:
        module.fail_json(
            msg="auth source '%s' has driver '%s' but the task says "
                "'%s'; the driver cannot be changed after creation. "
                "Delete and recreate the source to switch providers."
                % (params['name'], current.get('driver'), params['driver']))

    changes = {}
    for field in SIMPLE_FIELDS:
        if params.get(field) is None:
            continue
        live = current.get(field)
        live = bool(live) if field in ('menu', 'debug') else (live or '')
        if live != params[field]:
            changes[field] = params[field]

    settings_changed = False
    if params.get('settings') is not None:
        if settings_differ(current.get('settings'), params['settings']):
            # The API replaces settings wholesale; always send the full
            # declared document, never a partial one.
            changes['settings'] = params['settings']
            settings_changed = True

    if not changes:
        return False, False, source

    described = sorted(k for k in changes if k != 'settings')
    if settings_changed:
        actions.append("replaced settings of '%s'" % params['name'])
    if described:
        actions.append("updated '%s': %s"
                       % (params['name'], ', '.join(described)))
    if module.check_mode:
        return True, settings_changed, source

    source = client.auth_sources.update(current['$key'], **changes)
    return True, settings_changed, source


def delete_source(module, client, source, actions):
    data = dict(source)
    actions.append("deleted auth source '%s' (id %s)"
                   % (data.get('name'), data.get('$key')))
    if not module.check_mode:
        client.auth_sources.delete(data['$key'])
    return True


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        driver=dict(type='str',
                    choices=['azure', 'google', 'gitlab', 'okta', 'openid',
                             'openid-well-known', 'oauth2', 'verge.io']),
        settings=dict(type='dict', no_log=True),
        menu=dict(type='bool'),
        debug=dict(type='bool'),
        button_background_color=dict(type='str'),
        button_color=dict(type='str'),
        button_fa_icon=dict(type='str'),
        icon_color=dict(type='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        required_if=[('state', 'present', ['driver'])],
    )

    client = get_vergeos_client(module)
    actions = []

    try:
        want_settings = module.params.get('settings') is not None
        source = find_source(module, client, module.params['name'],
                             include_settings=want_settings)

        if module.params['state'] == 'absent':
            if source is None:
                module.exit_json(changed=False, actions=actions)
            changed = delete_source(module, client, source, actions)
            module.exit_json(changed=changed, actions=actions)

        # state: present
        if source is None:
            source = create_source(module, client, actions)
            result = {'changed': True, 'actions': actions,
                      'settings_changed': want_settings}
            if source is not None:
                result['auth_source'] = source_result(source)
            module.exit_json(**result)

        changed, settings_changed, source = update_source(
            module, client, source, actions)
        module.exit_json(changed=changed, actions=actions,
                         settings_changed=settings_changed,
                         auth_source=source_result(source))

    except (AuthenticationError, ValidationError, APIError,
            VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except NotFoundError as e:
        module.fail_json(msg="Resource not found: %s" % e)
    except Exception as e:
        module.fail_json(msg="Unexpected error: %s" % e)


if __name__ == '__main__':
    main()
