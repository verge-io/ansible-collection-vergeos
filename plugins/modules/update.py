#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: update
short_description: Drive the VergeOS platform update lifecycle
version_added: "2.1.0"
description:
  - Runs the system-wide part of a platform update - check, download, install.
  - Applying an installed update is per node and is deliberately NOT done here.
    Use M(vergeio.vergeos.node_maintenance) with O(state=restarted), or the
    C(rolling_update) role, so that rebooting nodes stays an explicit,
    reviewable step rather than a side effect of installing.
options:
  state:
    description:
      - How far along the lifecycle to go. Each stage implies the ones before
        it.
      - C(checked) refreshes what the source offers.
      - C(downloaded) also pulls the packages.
      - C(installed) also installs them, after which a reboot is outstanding.
    type: str
    choices: [ checked, downloaded, installed ]
    default: checked
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Supports C(check_mode).
  - Idempotence is by consequence, not bookkeeping. The platform records that
    updates are installed, not that a check was performed - so once updates
    are installed this module reports no change for any state, and until then
    C(checked) and C(downloaded) run each time. Re-checking is cheap; the
    alternative would be inventing a state the platform does not keep.
  - This module never reboots anything.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: See what the update source offers
  vergeio.vergeos.update:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    state: checked
  register: available

- name: Stage updates without installing them
  vergeio.vergeos.update:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    state: downloaded

- name: Install, leaving the reboot for the rolling upgrade
  vergeio.vergeos.update:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    state: installed
  register: installed

- name: Nodes now need restarting
  ansible.builtin.debug:
    msg: "reboot outstanding: {{ installed.reboot_required }}"
'''

RETURN = r'''
stages_run:
  description:
    - Lifecycle stages this run actually executed. Empty when there was
      nothing to do.
  returned: always
  type: list
  elements: str
  sample: ["checked", "downloaded"]
installed:
  description: Whether updates are installed and waiting to be applied.
  returned: always
  type: bool
reboot_required:
  description: Whether a reboot is outstanding.
  returned: always
  type: bool
branch:
  description: Name of the selected update branch.
  returned: always
  type: str
source:
  description: Name of the active update source.
  returned: always
  type: str
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
    stages_to_run,
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
        state=dict(type='str', default='checked',
                   choices=['checked', 'downloaded', 'installed']),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    state = module.params['state']
    client = get_vergeos_client(module)

    try:
        settings = client.update_settings.get()
        summary = settings_summary(settings)

        # Two update runs interleaving is not something to find out about
        # afterwards.
        if summary['applying']:
            module.fail_json(
                msg="the platform is applying updates right now; refusing to "
                    "start another update run.", **summary)

        stages = stages_to_run(state, summary)

        if not stages or module.check_mode:
            module.exit_json(changed=bool(stages), stages_run=stages, **summary)

        run = []
        if 'checked' in stages:
            settings.check()
            run.append('checked')
        if 'downloaded' in stages:
            settings.download()
            run.append('downloaded')
        if 'installed' in stages:
            settings.install()
            run.append('installed')

        summary = settings_summary(client.update_settings.get())
        module.exit_json(changed=True, stages_run=run, **summary)

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
