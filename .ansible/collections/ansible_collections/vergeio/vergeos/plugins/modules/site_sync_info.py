#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: site_sync_info
short_description: Gather information about VergeOS site syncs
version_added: "2.1.0"
description:
  - Gather facts about site-to-site replication, in either or both
    directions, with the age of each sync's last run computed.
  - RV(stale) and RV(unhealthy) are the two lists a replication watchdog
    actually acts on.
options:
  name:
    description:
      - Report only the sync of this name. Matched exactly.
    type: str
  direction:
    description:
      - Which side of replication to report.
      - C(outgoing) pushes snapshots to a remote system; C(incoming) accepts
        them from one.
    type: str
    choices: [ outgoing, incoming, both ]
    default: both
  max_age:
    description:
      - Seconds since a sync's last run beyond which it is reported in
        RV(stale).
      - A sync that has never run is always stale. That is the point - a
        replication target configured once and never exercised is the failure
        this surfaces.
      - Omit to skip the staleness check entirely.
    type: int
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Report every site sync
  vergeio.vergeos.site_sync_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
  register: syncs

- name: Show replication lag per outgoing sync
  ansible.builtin.debug:
    msg: >-
      {{ dict(syncs.outgoing | map(attribute='name')
              | zip(syncs.outgoing | map(attribute='seconds_since_last_run'))) }}

- name: Fail if any sync has not run in 24 hours
  vergeio.vergeos.site_sync_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    direction: outgoing
    max_age: 86400
  register: rpo

- name: Enforce the recovery point objective
  ansible.builtin.assert:
    that: rpo.stale | length == 0
    fail_msg: >-
      replication behind RPO: {{ rpo.stale | map(attribute='name') | list }}
'''

RETURN = r'''
outgoing:
  description:
    - Outgoing syncs, each with the derived fields below.
  returned: when O(direction) is C(outgoing) or C(both)
  type: list
  elements: dict
  contains:
    name:
      description: Sync name.
      type: str
      returned: always
    seconds_since_last_run:
      description:
        - Age of the last run, or V(none) if it has never run.
      type: int
      returned: always
    healthy:
      description:
        - Whether the platform positively reports the sync online and not in
          error. An unknown state is not healthy.
      type: bool
      returned: always
  sample:
    - name: "to-dr-site"
      enabled: true
      online: true
      healthy: true
      seconds_since_last_run: 1820
incoming:
  description: Incoming syncs, with the same derived fields.
  returned: when O(direction) is C(incoming) or C(both)
  type: list
  elements: dict
stale:
  description:
    - Syncs whose last run is older than O(max_age), plus any that have never
      run. Drawn from whichever directions were reported.
  returned: when O(max_age) is set
  type: list
  elements: dict
unhealthy:
  description:
    - Syncs the platform does not positively report as online and error-free.
  returned: always
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
from ansible_collections.vergeio.vergeos.plugins.module_utils.site_sync import (
    stale,
    summarize,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


def collect(manager, name):
    rows = [summarize(dict(r)) for r in manager.list()]
    if name:
        rows = [r for r in rows if r.get('name') == name]
    return rows


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
        direction=dict(type='str', default='both',
                       choices=['outgoing', 'incoming', 'both']),
        max_age=dict(type='int'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    params = module.params
    client = get_vergeos_client(module)
    want = params['direction']

    try:
        result = dict(changed=False)
        reported = []

        if want in ('outgoing', 'both'):
            result['outgoing'] = collect(client.site_syncs, params.get('name'))
            reported += result['outgoing']

        if want in ('incoming', 'both'):
            result['incoming'] = collect(client.site_syncs_incoming,
                                         params.get('name'))
            reported += result['incoming']

        result['unhealthy'] = [r for r in reported if not r['healthy']]

        if params.get('max_age') is not None:
            result['stale'] = stale(reported, params['max_age'])

        module.exit_json(**result)

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
