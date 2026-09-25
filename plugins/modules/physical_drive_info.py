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
version_added: "2.2.0"
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
      - The name is resolved first. An unknown name fails rather than
        returning an empty list that reads as a healthy node.
      - When the installed pyvergeos has the pyVergeOS#143 parent_drive
        walk, the list is scoped with C(PhysicalDriveManager(node_key=...)).
        Released pyvergeos through 1.6.1 still filters on a C(node) column
        that C(machine_drive_phys) does not have, so those versions list
        the fleet and match C(node_name) client-side.
      - A node that exists but has no drives produces a warning. An empty
        report is not a silent success.
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
  register: drives

- name: Show anything that needs attention
  ansible.builtin.debug:
    msg: >-
      {{ drives.warning + drives.critical
         | map(attribute='reasons') | list }}

- name: Alert only on drives that are actually failing
  vergeio.vergeos.physical_drive_info:
    severity: critical
  register: failing

- name: Fail the check when a drive needs replacing
  ansible.builtin.assert:
    that: failing.drives | length == 0
    fail_msg: >-
      replace: {{ failing.drives
                  | map(attribute='node_name') | zip(
                      failing.drives | map(attribute='serial')) | list }}

- name: Treat wear-out as urgent on an all-flash tier
  vergeio.vergeos.physical_drive_info:
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
    node_name:
      description:
        - The node the drive is installed in.
        - Reached through a join; there is no node column on the drive row.
          Without it a report cannot say which machine to walk to, and on a
          cluster of identical hardware the other fields do not distinguish
          one drive from another.
      type: str
      returned: always
    location:
      description:
        - The drive's slot on its node, for example C(nvme0). This is not
          node-qualified, and it repeats across nodes.
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
      node_name: "node1"
      location: "nvme0"
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

import inspect

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    resolve_one,
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
    from pyvergeos.resources.physical_drives import PhysicalDriveManager

# Ordered least to most urgent, so `severity:` can mean "this and above".
LADDER = ('ok', 'info', 'warning', 'critical')

# The projection this module reads, named rather than inherited from the SDK.
#
# `node_name` is the reason it has to be named at all. There is NO node column
# on machine_drive_phys -- checked against a live row on 26.1.8, 48 columns,
# none of them `node`. The node arrives only through the join below, and
# without it this module cannot say which machine to walk to. On a two-node
# system both drives came back with location "nvme0" and path "/dev/nvme0n1";
# the serial and the node are the only things that tell them apart.
#
# Node scoping. PhysicalDriveManager(client, node_key=...) on every released
# pyvergeos through 1.6.1 builds filter="node eq <key>". That column does not
# exist, so the list is empty and a per-node health scan looks healthy
# (pyVergeOS#143). The fix walks nodes.machine -> machine_drives ->
# parent_drive and lives on the SDK's dev branch only; it adds
# _parent_drive_filter_for_node. Use the scoped manager only when this
# install has that method. Otherwise list the fleet and match node_name
# client-side, which is what worked on the 1.2.7 floor before the scoped
# call replaced it.
NODE_FIELD = 'parent_drive#machine#name as node_name'

DRIVE_FIELDS = [
    '$key', 'model', 'serial', 'fw', 'path', 'location', 'size',
    'temp', 'temp_warn',
    'realloc_sectors', 'realloc_sectors_warn',
    'wear_level', 'wear_level_warn',
    'current_pending_sector', 'current_pending_sector_warn',
    'offline_uncorrectable', 'offline_uncorrectable_warn',
    'hours', 'hours_warn', 'smart',
    'vsan_read_errors', 'vsan_write_errors', 'vsan_last_error',
    'vsan_throttle', 'vsan_tier', 'vsan_repairing', 'vsan_repair_estimate',
    'vsan_online_since',
    'boot', 'swap', 'spare', 'encrypted', 'parent_drive', 'modified',
    NODE_FIELD,
]


def at_or_above(rows, floor):
    if floor == 'all':
        return list(rows)
    wanted = LADDER[LADDER.index(floor):]
    return by_severity(rows, *wanted)


def sdk_node_scope_uses_parent_drive(manager_cls=None):
    """Whether ``PhysicalDriveManager(node_key=...)`` scopes via parent_drive.

    True only when the installed SDK has the pyVergeOS#143 fix. Released
    builds from 1.2.7 through 1.6.1 do not: their ``list()`` appends
    ``node eq <key>`` and the API returns an empty list.

    The marker is a real function named ``_parent_drive_filter_for_node``.
    A MagicMock grows that attribute on demand and is callable, which is
    how a mocked manager hid this bug, so a mock does not count.
    """
    if manager_cls is None:
        manager_cls = globals().get('PhysicalDriveManager')
    if manager_cls is None:
        return False
    return inspect.isfunction(
        getattr(manager_cls, '_parent_drive_filter_for_node', None))


def _node_name_of(drive):
    value = dict(drive).get('node_name')
    if value is None:
        return ''
    return str(value)


def _drives_named(drives, node_name):
    wanted = str(node_name)
    return [drive for drive in drives if _node_name_of(drive) == wanted]


def _known_node_names(drives):
    return sorted({name for name in (_node_name_of(d) for d in drives) if name})


def _no_drives_warning(node_name, drives):
    return ("no drives matched node '%s'. Known nodes: %s"
            % (node_name, _known_node_names(drives)))


def list_drives_for_node(client, node_name, node_key):
    """Drives on ``node_name``, which has already been resolved to a key.

    Returns ``(drives, warning)``. ``warning`` is set when that has to be
    said out loud: the node has no drives, or the SDK scope returned
    nothing and the fleet match was used instead. It is ``None`` when the
    first list already returned this node's drives.

    The SDK scoped manager is used only when this install has the #143
    parent_drive walk. A scoped list that comes back empty is checked
    against the fleet: if the fleet has this node, those rows are what
    gets reported, because an empty scoped list is how the broken
    ``node eq`` filter presents. If the fleet does not have it either,
    the node simply has no drives and the caller warns.
    """
    if sdk_node_scope_uses_parent_drive():
        scoped = list(PhysicalDriveManager(
            client, node_key=int(node_key),
        ).list(fields=DRIVE_FIELDS))
        if scoped:
            return scoped, None
        fleet = list(client.physical_drives.list(fields=DRIVE_FIELDS))
        matched = _drives_named(fleet, node_name)
        if matched:
            return matched, (
                "no drives matched node '%s' via the SDK node scope; "
                "reporting %d drive(s) matched by node_name instead. "
                "Known nodes: %s"
                % (node_name, len(matched), _known_node_names(fleet)))
        return [], _no_drives_warning(node_name, fleet)

    fleet = list(client.physical_drives.list(fields=DRIVE_FIELDS))
    matched = _drives_named(fleet, node_name)
    if not matched:
        return [], _no_drives_warning(node_name, fleet)
    return matched, None


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
        if params.get('node'):
            # resolve_one fails loudly on an unknown name -- "0 drives, none
            # failing" must not be how a typo reads. A name that resolves but
            # matches nothing warns; see list_drives_for_node.
            node = resolve_one(
                module, client.nodes, params['node'], 'node',
                fields=['$key', 'name'],
            )
            drives, warning = list_drives_for_node(
                client, params['node'], node.key)
            if warning:
                module.warn(warning)
        else:
            drives = client.physical_drives.list(fields=DRIVE_FIELDS)

        rows = [classify(dict(d),
                         critical_flags=params.get('critical_flags'),
                         warning_flags=params.get('warning_flags'))
                for d in drives]

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
