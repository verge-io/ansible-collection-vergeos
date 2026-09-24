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
  - Drives a platform update through its lifecycle - check, download, install,
    and optionally apply.
  - Check, download and install are system-wide and leave the cluster running.
    Applying is a reboot, so it is a state of its own that no lower stage
    implies - installing never reboots as a side effect.
  - Applying is delegated to the platform's own rolling apply, which reboots
    nodes one at a time and migrates workloads first. This module does not
    reimplement that sequence.
options:
  state:
    description:
      - How far along the lifecycle to go. Each stage implies the ones before
        it.
      - C(checked) refreshes what the source offers.
      - C(downloaded) also pulls the packages.
      - C(installed) also installs them, after which a reboot is outstanding.
      - C(applied) reboots the nodes to bring an installed update into
        service, using the platform's own rolling apply - one node at a time,
        migrating workloads first. It is never reached by implication from a
        lower stage, because there is no state in which rebooting again is a
        no-op.
    type: str
    choices: [ checked, downloaded, installed, applied ]
    default: checked
  force:
    description:
      - With O(state=applied), permit nodes carrying workloads that cannot be
        migrated - GPU passthrough, for example - to reboot those workloads
        rather than stalling the roll.
      - This decides availability for those workloads, so it is never implied.
    type: bool
    default: false
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Supports C(check_mode).
  - Idempotence is by consequence, not bookkeeping. The platform records that
    updates are installed, not that a check was performed - so once updates
    are installed this module reports no change for any state, and until then
    C(checked) and C(downloaded) run each time. Re-checking is cheap; the
    alternative would be inventing a state the platform does not keep.
  - Every state except C(applied) leaves the cluster running. C(applied)
    reboots nodes.
  - C(applied) does not hand-roll the roll. It calls the platform action that
    already does it, which also drains and migrates - the same operation
    C(nodes/{key}/maintenance_reboot) performs per node. It does NOT check
    whether the cluster can survive losing a node; use M(vergeio.vergeos.cluster_info)
    for that, or the C(rolling_update) role, which does it for you.
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
    apply_installed,
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
                   choices=['checked', 'downloaded', 'installed', 'applied']),
        force=dict(type='bool', default=False),
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

        # ── applied ─────────────────────────────────────────────────────────
        # Handled before the stage machinery, because applying is a reboot
        # rather than a stage: there is no "already applied" flag, and the
        # thing that says whether it is still outstanding is reboot_required.
        if state == 'applied':
            if not summary['installed']:
                module.fail_json(
                    msg="no update is installed, so there is nothing to "
                        "apply. Run state=installed first.", **summary)
            if not summary['reboot_required']:
                module.exit_json(
                    changed=False, stages_run=[],
                    msg="the installed update needs no reboot; nothing to "
                        "apply.", **summary)
            if module.check_mode:
                module.exit_json(
                    changed=True, stages_run=['applied'],
                    msg="check mode: would apply the installed update by "
                        "rolling the nodes%s."
                        % (" (force)" if module.params['force'] else ""),
                    **summary)

            apply_installed(settings, force=module.params['force'])
            summary = settings_summary(client.update_settings.get())
            module.exit_json(
                changed=True, stages_run=['applied'],
                msg="rolling apply started; the platform reboots nodes one at "
                    "a time, migrating workloads first. This returns as soon "
                    "as the roll is accepted, not when it finishes.",
                **summary)

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
