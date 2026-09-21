#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: catalog
short_description: Manage recipe catalogs and their publication in VergeOS
version_added: "2.1.0"
description:
  - Create, update, and delete recipe catalogs, including their
    B(publication scope) - which is how recipes are granted to tenants
    in VergeOS (there is no per-tenant grant; a catalog with
    O(publishing_scope=tenant) is what tenants see).
  - A catalog's identity is O(name) within O(repository). With no
    repository given the module matches by name alone - and refuses to
    guess if several repositories hold that name.
options:
  name:
    description:
      - Catalog name, the match key for idempotence.
    type: str
    required: true
  state:
    description:
      - Whether the catalog should exist.
    type: str
    choices: [ present, absent ]
    default: present
  repository:
    description:
      - Name of the repository the catalog belongs to. Required to
        create; optional otherwise (used to disambiguate the match).
    type: str
  description:
    description:
      - Free-form description.
    type: str
  publishing_scope:
    description:
      - Who can see the catalog's recipes. C(tenant) is the tenant
        recipe grant; C(global) publishes to everyone; C(private) keeps
        it to this system's users; C(none) unpublishes.
    type: str
    choices: [ private, global, tenant, none ]
  enabled:
    description:
      - Whether the catalog is enabled. Unset preserves the current
        value on update.
    type: bool
notes:
  - Supports C(check_mode).
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: A catalog of golden images, visible to tenants
  vergeio.vergeos.catalog:
    name: Golden Images
    repository: Local
    description: hardened base images, published to all tenants
    publishing_scope: tenant
    state: present

- name: Unpublish it without deleting anything
  vergeio.vergeos.catalog:
    name: Golden Images
    publishing_scope: none
    state: present

- name: Remove it
  vergeio.vergeos.catalog:
    name: Golden Images
    state: absent
'''

RETURN = r'''
catalog:
  description: State of the catalog after the module ran.
  returned: when state is present
  type: dict
  sample:
    key: "b170cf3baba9f588d38b4145096edfdc34ce7e8b"
    name: "Golden Images"
    repository: 1
    repository_name: "Local"
    description: "hardened base images"
    publishing_scope: "tenant"
    enabled: true
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
  sample: ["updated catalog 'Golden Images': publishing_scope"]
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

UPDATE_FIELDS = ('description', 'publishing_scope', 'enabled')


def resolve_repository_key(module, client, name):
    try:
        return dict(client.catalog_repositories.get(name=name))['$key']
    except NotFoundError:
        module.fail_json(msg="Repository '%s' not found" % name)


def find_catalogs(client, name, repo_key=None):
    # Let the SDK build the filter. Hand-rolling it here used the SQL-style
    # doubling ("'" -> "''"), which VergeOS 26.1.8 rejects outright --
    # measured: filter=name eq 'o''brien' returns ValidationError "Invalid
    # argument", while the SDK's quote_value form returns HTTP 200 with no
    # match. So any catalog whose name contained an apostrophe made this
    # module fail rather than report "not found".
    matches = client.catalogs.list(name=name)
    if repo_key is not None:
        matches = [c for c in matches
                   if dict(c).get('repository') == repo_key]
    return matches


def catalog_result(catalog):
    data = dict(catalog)
    return {
        'key': data.get('$key'),
        'name': data.get('name'),
        'repository': data.get('repository'),
        'repository_name': data.get('repository_display') or '',
        'description': data.get('description') or '',
        'publishing_scope': data.get('publishing_scope'),
        'enabled': bool(data.get('enabled')),
    }


def update_catalog(module, client, catalog, actions):
    params = module.params
    current = dict(catalog)
    changes = {}

    for field in UPDATE_FIELDS:
        if params.get(field) is None:
            continue
        live = current.get(field)
        if field == 'enabled':
            live = bool(live)
        elif live is None:
            live = ''
        if live != params[field]:
            changes[field] = params[field]

    if not changes:
        return False, catalog

    actions.append("updated catalog '%s': %s"
                   % (params['name'], ', '.join(sorted(changes))))
    if module.check_mode:
        return True, catalog

    catalog = client.catalogs.update(current['$key'], **changes)
    return True, catalog


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        repository=dict(type='str'),
        description=dict(type='str'),
        publishing_scope=dict(type='str',
                              choices=['private', 'global', 'tenant',
                                       'none']),
        enabled=dict(type='bool'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    actions = []
    params = module.params

    try:
        repo_key = None
        if params.get('repository'):
            repo_key = resolve_repository_key(module, client,
                                              params['repository'])

        matches = find_catalogs(client, params['name'], repo_key)

        if len(matches) > 1:
            module.fail_json(
                msg="%d catalogs named '%s' exist%s; add the repository "
                    "option to disambiguate."
                    % (len(matches), params['name'],
                       '' if repo_key is None
                       else " in repository '%s'" % params['repository']))

        catalog = matches[0] if matches else None

        if params['state'] == 'absent':
            if catalog is None:
                module.exit_json(changed=False, actions=actions)
            actions.append("deleted catalog '%s'" % params['name'])
            if not module.check_mode:
                client.catalogs.delete(dict(catalog)['$key'])
            module.exit_json(changed=True, actions=actions)

        # state: present
        if catalog is None:
            if repo_key is None:
                module.fail_json(
                    msg="catalog '%s' does not exist and no repository "
                        "was given; repository is required to create."
                        % params['name'])
            actions.append("created catalog '%s' in repository '%s'"
                           % (params['name'], params['repository']))
            if module.check_mode:
                module.exit_json(changed=True, actions=actions)
            create_args = {'name': params['name'], 'repository': repo_key}
            if params.get('description') is not None:
                create_args['description'] = params['description']
            if params.get('publishing_scope') is not None:
                create_args['publishing_scope'] = params['publishing_scope']
            if params.get('enabled') is not None:
                create_args['enabled'] = params['enabled']
            created = client.catalogs.create(**create_args)
            module.exit_json(changed=True, actions=actions,
                             catalog=catalog_result(created))

        changed, catalog = update_catalog(module, client, catalog, actions)
        module.exit_json(changed=changed, actions=actions,
                         catalog=catalog_result(catalog))

    except (AuthenticationError, ValidationError, APIError,
            VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except NotFoundError as e:
        module.fail_json(msg="Resource not found: %s" % e)
    except Exception as e:
        module.fail_json(msg="Unexpected error: %s" % e)


if __name__ == '__main__':
    main()
