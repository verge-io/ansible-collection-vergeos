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
version_added: "2.2.0"
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

# Module parameter -> API field name. Declared as maps, not a bare tuple, so
# tests/unit/plugins/modules/test_field_contracts.py can audit them (#75).
# Every name below was checked against a live catalog row on VergeOS 26.1.8:
# the table has 13 columns and description, publishing_scope, enabled,
# repository and name are all among them. That check is what found #87 on the
# vm module -- three parameters that were not fields at all.
#
# The mapping happens to be the identity here. It is still written out, because
# "no rename needed" is a fact worth stating once and testing, rather than an
# assumption re-made by every future editor.
CREATE_PARAM_MAP = {
    'name': 'name',
    'repository': 'repository',
    'description': 'description',
    'publishing_scope': 'publishing_scope',
    'enabled': 'enabled',
}

# name and repository are the identity, not updatable settings: changing them
# would mean "a different catalog", which is a create plus a delete and not
# something this module does behind your back.
UPDATE_FIELD_MAP = {
    'description': 'description',
    'publishing_scope': 'publishing_scope',
    'enabled': 'enabled',
}

IDENTITY_PARAMS = ('name', 'repository')

# Fields read back for comparison. A field diffed but never fetched reads as
# None and the module never converges -- that was #18.
COMPARISON_FIELDS = tuple(sorted(set(UPDATE_FIELD_MAP.values())))


def resolve_repository_key(module, client, name):
    """Repository name -> $key, refusing to guess if the name is ambiguous.

    Uses ``resolve_one`` rather than the SDK's ``get(name=...)`` for the same
    two reasons ``find_catalogs`` matches client-side -- see its docstring.
    Repositories are few and admin-created, so a duplicate is unlikely; that
    is an argument for the check being cheap, not for skipping it.
    """
    try:
        return dict(resolve_one(module, client.catalog_repositories, name,
                                'repository'))['$key']
    except NotFoundError:
        module.fail_json(msg="Repository '%s' not found" % name)


def find_catalogs(client, name, repo_key=None):
    """Catalogs of this name, matched client-side. There are tens at most.

    Deliberately NOT a server-side ``name eq '...'`` filter, in either its
    hand-rolled or its SDK-built form. Both have bitten us:

    1. Hand-rolled with SQL-style doubling ("'" -> "''") is rejected by
       VergeOS 26.1.8 outright -- ``name eq 'o''brien'`` returns
       ValidationError "Invalid argument" -- so a catalog with an
       apostrophe in its name could not be looked up, created, converged
       or deleted.
    2. Letting the SDK build it fixes the apostrophe but, on the floor
       version, hits pyVergeOS#100: ``quote_value()`` did not escape
       ``{``, so VergeOS read the brace as the start of a substitution
       token and the query matched a DIFFERENT catalog. Measured through
       this module: creating ``zz-jw-c{x}at`` alongside an existing
       ``zz-jw-cat`` reported ``changed: false`` (it matched the other one
       and created nothing), and ``state: absent`` on ``zz-jw-c{x}at``
       reported ``changed: true`` having deleted ``zz-jw-cat``.

       Worth being precise about, because the first diagnosis was wrong:
       this was filed as a VergeOS filter-parser defect and closed as a
       client-side escaping bug (verge-io/engineering#20). The platform
       behaves as documented; the SDK was under-quoting.

    Matching in Python is immune to both, and is now the collection's
    general rule -- ``module_utils/vergeos.py::resolve_one`` does exactly
    this for every single-result name lookup (#72/#85). This function is
    not ``resolve_one`` because it is deliberately a MULTI-match: the
    caller needs the full match list to narrow by repository, and to tell
    "ambiguous by name alone" apart from "ambiguous within one repository"
    in its error message.

    Restore a server-side filter only once the floor requires pyvergeos
    >= 1.2.8, which carries the fix for pyVergeOS#100. The floor is 1.2.7
    today -- one patch short.
    """
    matches = [c for c in client.catalogs.list()
               if dict(c).get('name') == name]
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

    for param, api_field in sorted(UPDATE_FIELD_MAP.items()):
        if params.get(param) is None:
            continue
        live = current.get(api_field)
        if param == 'enabled':
            live = bool(live)
        elif live is None:
            live = ''
        if live != params[param]:
            changes[api_field] = params[param]

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
            create_args = {
                CREATE_PARAM_MAP['name']: params['name'],
                CREATE_PARAM_MAP['repository']: repo_key,
            }
            for param, api_field in sorted(CREATE_PARAM_MAP.items()):
                if param in IDENTITY_PARAMS:
                    continue
                if params.get(param) is not None:
                    create_args[api_field] = params[param]
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
