#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: physical_drive_info
short_description: Gather SMART and vSAN health for VergeOS physical drives
version_added: "2.1.0"
description:
  - Report every physical drive's SMART attributes and the platform's own vSAN
    IO error counters, triaged into a severity so that a drive with
    uncorrectable sectors is not reported the same way as one that is merely
    warm.
  - Read-only. This module never touches a drive.
options:
  node:
    description:
      - Report only drives on this node. Matched exactly by node name.
    type: str
  severity:
    description:
      - Report only drives at or above this severity.
      - C(all) reports everything, which is what you want for an inventory
        rather than an alert.
    type: str
    choices: [ all, info, warning, critical ]
    default: all
  critical_flags:
    description:
      - SMART warning flags to treat as critical.
      - Defaults to the sector-failure attributes -
        C(realloc_sectors_warn), C(current_pending_sector_warn) and
        C(offline_uncorrectable_warn) - because those are the drive reporting
        that it could not read or write something.
    type: list
    elements: str
  warning_flags:
    description:
      - SMART warning flags to treat as warnings rather than critical.
      - Defaults to C(wear_level_warn) and C(temp_warn). Both are real and
        actionable, but they are plan-a-replacement rather than pull-it-now,
        and a hot drive is often the rack's problem rather than the drive's.
    type: list
    elements: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - vSAN IO errors outrank every SMART flag. A SMART warning is the drive's own
    prediction; a vSAN read or write error is the platform reporting that an
    operation against the drive actually failed. The measurement wins.
  - A drive with SMART disabled is reported as C(info), not C(ok). Its health
    flags are silent, so it reads as healthy whether it is or not.
  - RV(repairing) is not a severity. A drive can be rebuilding while perfectly
    healthy - it may have just been replaced - but pulling a second drive
    mid-repair is how a rebuild becomes a data-loss event, so it is surfaced
    separately.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Inventory every drive
  vergeio.vergeos.physical_drive_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
  register: drives

- name: Show anything that needs attention
  ansible.builtin.debug:
    msg: >-
      {{ drives.warning + drives.critical
         | map(attribute='reasons') | list }}

- name: Alert only on drives that are actually failing
  vergeio.vergeos.physical_drive_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    severity: critical
  register: failing

- name: Fail the check when a drive needs replacing
  ansible.builtin.assert:
    that: failing.drives | length == 0
    fail_msg: >-
      replace: {{ failing.drives
                  | map(attribute='serial') | list }}

- name: Treat wear-out as urgent on an all-flash tier
  vergeio.vergeos.physical_drive_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    critical_flags:
      - realloc_sectors_warn
      - current_pending_sector_warn
      - offline_uncorrectable_warn
      - wear_level_warn
  register: flash
'''

RETURN = r'''
drives:
  description:
    - Matching drives, each triaged.
  returned: always
  type: list
  elements: dict
  contains:
    serial:
      description: Drive serial number, which is what you take to the rack.
      type: str
      returned: always
    location:
      description: Physical location of the drive.
      type: str
      returned: always
    severity:
      description: One of C(ok), C(info), C(warning), C(critical).
      type: str
      returned: always
    reasons:
      description: Why the drive got that severity. Empty when C(ok).
      type: list
      elements: str
      returned: always
    repairing:
      description: Whether vSAN is currently rebuilding onto this drive.
      type: bool
      returned: always
  sample:
    - serial: "S3Z1NB0K"
      location: "node1:/dev/sdc"
      severity: "critical"
      reasons: ["realloc_sectors_warn is set (realloc_sectors=184)"]
      repairing: false
critical:
  description: Drives whose media is failing, or that vSAN has had IO errors on.
  returned: always
  type: list
  elements: dict
warning:
  description: Drives wearing out or running hot.
  returned: always
  type: list
  elements: dict
repairing:
  description:
    - Drives vSAN is rebuilding onto right now. Do not pull another drive
      until these are done.
  returned: always
  type: list
  elements: dict
counts:
  description: Number of drives at each severity.
  returned: always
  type: dict
  sample:
    ok: 22
    info: 1
    warning: 2
    critical: 1
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.physical_drives import (
    by_severity,
    classify,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )

# Ordered least to most urgent, so `severity:` can mean "this and above".
LADDER = ('ok', 'info', 'warning', 'critical')


def at_or_above(rows, floor):
    if floor == 'all':
        return list(rows)
    wanted = LADDER[LADDER.index(floor):]
    return by_severity(rows, *wanted)


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        node=dict(type='str'),
        severity=dict(type='str', default='all',
                      choices=['all', 'info', 'warning', 'critical']),
        critical_flags=dict(type='list', elements='str'),
        warning_flags=dict(type='list', elements='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    params = module.params
    client = get_vergeos_client(module)

    try:
        rows = [classify(dict(d),
                         critical_flags=params.get('critical_flags'),
                         warning_flags=params.get('warning_flags'))
                for d in client.physical_drives.list()]

        if params.get('node'):
            # The drive's location carries the node it sits in; match on it
            # rather than resolving node keys, so a caller can name a node
            # the same way the report does.
            wanted = params['node']
            rows = [r for r in rows
                    if wanted in str(r.get('location') or '')
                    or wanted == str(r.get('node_name') or '')]

        module.exit_json(
            changed=False,
            drives=at_or_above(rows, params['severity']),
            critical=by_severity(rows, 'critical'),
            warning=by_severity(rows, 'warning'),
            repairing=[r for r in rows if r.get('repairing')],
            counts={level: len(by_severity(rows, level)) for level in LADDER},
        )

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
