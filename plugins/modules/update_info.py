#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: update_info
short_description: Gather information about VergeOS platform updates
version_added: "2.1.0"
description:
  - Report the update source and branch in use, whether updates are pending,
    downloaded or installed, and whether a reboot is outstanding.
  - Reading this does not contact the update source. Use
    M(vergeio.vergeos.update) with O(state=checked) to refresh first.
options:
  packages:
    description:
      - Also list the packages the current source offers, and which of them
        are already downloaded.
    type: bool
    default: false
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Report update state
  vergeio.vergeos.update_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
  register: updates

- name: Show what is pending
  ansible.builtin.debug:
    msg:
      branch: "{{ updates.branch }}"
      installed: "{{ updates.installed }}"
      reboot_required: "{{ updates.reboot_required }}"

- name: Skip the upgrade play when there is nothing to apply
  ansible.builtin.meta: end_play
  when: not updates.installed
'''

RETURN = r'''
settings:
  description: The raw update settings row.
  returned: always
  type: dict
source:
  description: Name of the active update source.
  returned: always
  type: str
  sample: "VergeIO"
branch:
  description: Name of the selected update branch.
  returned: always
  type: str
  sample: "26.1"
installed:
  description:
    - Whether updates have been installed and are waiting to be applied.
  returned: always
  type: bool
reboot_required:
  description:
    - Whether a reboot is outstanding. On a cluster this is applied node by
      node; see the C(rolling_update) role.
  returned: always
  type: bool
applying:
  description:
    - Whether the platform is applying updates right now. Starting a rolling
      upgrade while this is true would interleave two update runs.
  returned: always
  type: bool
auto_update:
  description: Whether the platform updates itself unattended.
  returned: always
  type: bool
packages:
  description: Packages the current source offers.
  returned: when O(packages) is true
  type: list
  elements: dict
pending_packages:
  description: Packages not yet downloaded.
  returned: when O(packages) is true
  type: list
  elements: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.updates import (
    settings_summary,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        packages=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)

    try:
        settings = client.update_settings.get()
        result = dict(changed=False, **settings_summary(settings))

        if module.params['packages']:
            packages = [dict(p) for p in client.update_source_packages.list()]
            result['packages'] = packages
            result['pending_packages'] = [
                p for p in packages if not p.get('downloaded')]

        module.exit_json(**result)

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
