# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
name: vergeos_credential_concealed
short_description: Check that a recipe answer stayed no_log and was not echoed
version_added: "2.3.0"
description:
  - Return C(true) when a C(vm_recipe_deploy) result lists the question in
    RV(no_log_answers) and does not echo the answer value.
  - The recipe name is not evidence. A refusal that only quotes the recipe
    is C(false) unless that question is in RV(no_log_answers).
  - The recipe row is not searched. The recipe name is removed from C(msg)
    before the value is sought, so a name that happens to contain the same
    letters does not count as an echo and does not count as masking.
options:
  _input:
    description: A C(vm_recipe_deploy) result.
    type: dict
    required: true
  name:
    description: Question variable name, such as C(ZZ_SECRET).
    type: str
    required: true
  value:
    description:
      - The answer value that must not appear outside the recipe name.
      - An empty value is never treated as concealed.
    type: raw
    required: true
'''

EXAMPLES = r'''
- name: A password answer has to stay no_log
  ansible.builtin.assert:
    that:
      - >-
        deploy | vergeio.vergeos.vergeos_credential_concealed(
        'ZZ_SECRET', 'fuzz')
'''

RETURN = r'''
_value:
  description:
    - C(true) when C(name) is in RV(no_log_answers) and C(value) is not echoed.
  type: bool
'''

from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    credential_answer_concealed,
)


class FilterModule:
    """Whether a deploy result kept one answer no_log."""

    def filters(self):
        return {
            'vergeos_credential_concealed': credential_answer_concealed,
        }
