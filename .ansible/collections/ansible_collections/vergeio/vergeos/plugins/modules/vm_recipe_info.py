#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vm_recipe_info
short_description: Gather information about VM recipes in VergeOS
version_added: "2.1.0"
description:
  - Gather facts about VM recipes, and optionally about the questions a recipe
    accepts as deploy-time answers.
  - Use this to discover what M(vergeio.vergeos.vm_recipe_deploy) can be asked
    for, rather than reading the recipe in the UI and transcribing it.
options:
  name:
    description:
      - Name of a specific recipe to query.
      - Matched exactly, including case and punctuation.
      - If not specified, returns every recipe and O(questions) is ignored.
    type: str
  catalog:
    description:
      - Catalog name, used to disambiguate when two catalogs carry a recipe of
        the same name.
      - Without it, an ambiguous name is refused rather than guessed.
    type: str
  questions:
    description:
      - Also return the recipe's question set.
      - Requires O(name).
      - Questions that are recipe plumbing rather than operator input
        (hidden, C(database_*), and anything in the C($database) section) are
        reported separately in RV(internal_questions) so they do not bury the
        answerable ones.
    type: bool
    default: false
  resolve_options:
    description:
      - For table-backed questions (C(row), C(list), C(cluster)), look up the
        valid values from the table the question points at.
      - Requires O(questions).
      - This is what turns an unanswerable question into a listed choice. A
        question whose table has no rows is reported with an empty option list,
        which is the signal that something has to be created first.
    type: bool
    default: false
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: List every recipe
  vergeio.vergeos.vm_recipe_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
  register: recipes

- name: Show the recipe names
  ansible.builtin.debug:
    msg: "{{ recipes.recipes | map(attribute='name') | list }}"

- name: Discover what one recipe accepts, with valid values resolved
  vergeio.vergeos.vm_recipe_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    questions: true
    resolve_options: true
  register: ubuntu

- name: Show which questions must be answered
  ansible.builtin.debug:
    msg: >-
      {{ ubuntu.questions
         | selectattr('required')
         | map(attribute='name') | list }}

- name: Show the valid storage tiers for a table-backed question
  ansible.builtin.debug:
    var: ubuntu.options.SELECT_OS_TIER
'''

RETURN = r'''
recipes:
  description:
    - Matching recipes. A single-element list when O(name) is given.
  returned: always
  type: list
  elements: dict
  sample:
    - name: "Ubuntu Server 22.04 (Jammy Jellyfish)"
      version: 3
      catalog_display: "Local"
      downloaded: true
questions:
  description:
    - The recipe's answerable questions, in the order the UI presents them.
    - The C(default) of a question carrying a credential is redacted, because
      the raw row returns it in clear text.
  returned: when O(questions) is true and O(name) matched
  type: list
  elements: dict
  sample:
    - name: "HOSTNAME"
      type: "string"
      display: "Hostname"
      required: true
      default: ""
internal_questions:
  description:
    - Names of questions that are recipe plumbing rather than operator input.
  returned: when O(questions) is true and O(name) matched
  type: list
  elements: str
  sample: ["YB_CHECK_WINDOWS_ISO", "YB_VM_KEY"]
options:
  description:
    - Valid values for each table-backed question, keyed by question name.
    - An empty list means the question's table has no rows, so no answer to it
      can be valid until one is created.
  returned: when O(resolve_options) is true
  type: dict
  sample:
    SELECT_OS_TIER:
      - $key: 4
        $display: "4"
secret_questions:
  description:
    - Names of questions whose answers are credentials. Pass these with
      C(no_log) when you build an answer set.
  returned: when O(questions) is true and O(name) matched
  type: list
  elements: str
  sample: ["PASSWORD"]
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    SECRET_TYPES,
    TABLE_BACKED_TYPES,
    is_internal,
    looks_secret,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.vm_recipes import (
    fetch_options,
    fetch_questions,
    resolve_recipe,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


def _is_secret(question):
    return (question.get('type') in SECRET_TYPES
            or looks_secret(question.get('name')))


def _needs_options(questions):
    """The option-lookup specs, derived the same way the resolver derives them.

    Kept in terms of the resolver's own vocabulary so info and deploy cannot
    disagree about which questions are table-backed.
    """
    specs = []
    for q in questions:
        if q.get('type') in TABLE_BACKED_TYPES and q.get('table'):
            specs.append({
                'name': q.get('name'),
                'table': q.get('table'),
                'filter': q.get('filter') or '',
                'fields': q.get('fields') or '$key,$display',
            })
    return specs


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
        catalog=dict(type='str'),
        questions=dict(type='bool', default=False),
        resolve_options=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    params = module.params
    name = params.get('name')

    if params['questions'] and not name:
        module.fail_json(msg="questions requires name: a question set belongs "
                             "to one recipe.")
    if params['resolve_options'] and not params['questions']:
        module.fail_json(msg="resolve_options requires questions: options are "
                             "resolved per table-backed question.")

    client = get_vergeos_client(module)

    try:
        if not name:
            recipes = [dict(r) for r in client.vm_recipes.list()]
            module.exit_json(changed=False, recipes=recipes)

        recipe, error = resolve_recipe(client, name, params.get('catalog'))
        if error:
            module.fail_json(msg=error)

        result = dict(changed=False, recipes=[recipe])

        if not params['questions']:
            module.exit_json(**result)

        rows = fetch_questions(client, recipe['$key'])

        answerable, internal, secrets = [], [], []
        for q in rows:
            if _is_secret(q):
                secrets.append(q.get('name'))
                # The raw row returns a credential default in clear text.
                if q.get('default'):
                    q = dict(q, default='VALUE_SPECIFIED_IN_NO_LOG_PARAMETER')
            if is_internal(q):
                internal.append(q.get('name'))
            else:
                answerable.append(q)

        result.update(
            questions=answerable,
            internal_questions=sorted(n for n in internal if n),
            secret_questions=sorted(n for n in secrets if n),
        )

        if params['resolve_options']:
            result['options'] = fetch_options(client, _needs_options(answerable))

        module.exit_json(**result)

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
