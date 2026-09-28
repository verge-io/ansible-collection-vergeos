#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: snapshot_profile
short_description: Manage snapshot profiles in VergeOS
version_added: "2.2.0"
description:
  - Create, update, and delete snapshot profiles and their schedule
    periods, so snapshot policy (schedules + retention) can be described
    declaratively.
  - Periods are matched by O(periods[].name) within the profile, so
    repeated runs converge.
options:
  name:
    description:
      - Profile name; the lookup key. Renaming is not supported.
    type: str
    required: true
  state:
    description:
      - Whether the profile should exist.
      - C(absent) deletes the profile and its periods. Attached objects
        (VMs, volumes, tenants) lose the schedule.
    type: str
    choices: [ present, absent ]
    default: present
  description:
    description:
      - Profile description.
    type: str
  periods:
    description:
      - Desired schedule periods, matched by C(name).
      - Missing periods are created and drifted fields corrected. Periods
        not listed are left alone unless O(purge_periods) is true.
    type: list
    elements: dict
    suboptions:
      name:
        description: Period name, the match key (e.g. C(Daily)).
        type: str
        required: true
      frequency:
        description: Schedule frequency.
        type: str
        choices: [ hourly, daily, weekly, monthly, yearly, custom ]
        required: true
      retention:
        description: Retention in seconds (e.g. 604800 for 7 days).
        type: int
        required: true
      minute:
        description: Minute of the hour (0-59).
        type: int
        default: 0
      hour:
        description: Hour of the day (0-23).
        type: int
        default: 0
      day_of_week:
        description: Day of week.
        type: str
        choices: [ sun, mon, tue, wed, thu, fri, sat, any ]
        default: any
      day_of_month:
        description: Day of month (0 = any).
        type: int
        default: 0
      month:
        description: Month (0 = any).
        type: int
        default: 0
      max_tier:
        description: Maximum storage tier for the snapshots (1-5).
        type: int
        default: 1
      quiesce:
        description: Quiesce disks before snapshotting.
        type: bool
        default: false
      min_snapshots:
        description: Minimum snapshots to retain regardless of age.
        type: int
        default: 1
      immutable:
        description: Make snapshots immutable (system snapshots only).
        type: bool
        default: false
  purge_periods:
    description:
      - Delete periods not listed in O(periods).
      - Ignored when O(periods) is omitted.
    type: bool
    default: false
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Backup profile with daily and weekly periods
  vergeio.vergeos.snapshot_profile:
    name: vm-backups
    description: "Daily 7d + weekly 30d, quiesced"
    periods:
      - name: Daily
        frequency: daily
        retention: 604800
        hour: 2
        quiesce: true
      - name: Weekly
        frequency: weekly
        retention: 2592000
        day_of_week: sun
        hour: 3
        quiesce: true
    state: present

- name: Exactly these periods, removing any others
  vergeio.vergeos.snapshot_profile:
    name: vm-backups
    periods:
      - name: Daily
        frequency: daily
        retention: 604800
    purge_periods: true

- name: Delete a profile
  vergeio.vergeos.snapshot_profile:
    name: old-profile
    state: absent
'''

RETURN = r'''
profile:
  description: State of the profile after the module ran.
  returned: when state is present
  type: dict
  sample:
    key: 8
    name: "vm-backups"
    description: "Daily 7d + weekly 30d, quiesced"
    periods:
      - name: "Daily"
        frequency: "daily"
        retention: 604800
        hour: 2
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
  sample: ["created profile 'vm-backups'", "created period 'Daily'"]
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

# Period fields compared for drift: module param -> raw API field, with
# a normalizer so API string/int representations compare correctly.
# Period parameter -> API column, every name checked against a live period row
# on VergeOS 26.1.8 (20 columns).
#
# One name was removed rather than mapped: `skip_missed`. It is not a column.
# The API accepts it with HTTP 200 and stores it nowhere -- measured by
# creating a period with skip_missed=True and reading every column back, none
# of which changed. The SDK's periods.create() still offers the keyword, so
# this is not obvious from the signature.
#
# It did both kinds of harm at once. Setting it was silently discarded, and
# the update path compared a missing column (None -> False) against True, so a
# profile with skip_missed set reported changed on every run, forever, while
# PUTting a field that does nothing. Same decision as subnet_mask in #18:
# removed, not deprecated -- an option that cannot work is not a compatibility
# surface worth keeping.
#
# `max_tier` is the other oddity and is real: the API stores it as the STRING
# '1'. It is compared as an int and written back as a string.
PERIOD_FIELDS = {
    'frequency': ('frequency', str),
    'retention': ('retention', int),
    'minute': ('minute', int),
    'hour': ('hour', int),
    'day_of_week': ('day_of_week', str),
    'day_of_month': ('day_of_month', int),
    'month': ('month', int),
    'max_tier': ('max_tier', int),
    'quiesce': ('quiesce', bool),
    'min_snapshots': ('min_snapshots', int),
    'immutable': ('immutable', bool),
}


# Columns read back for a profile and for its periods. Named rather than left
# to the SDK's default projection: a column compared but not fetched reads as
# None and the module never converges (#18, #92).
PROFILE_FIELDS = ['$key', 'name', 'description']
PERIOD_READ_FIELDS = ['$key', 'name'] + sorted(
    {pair[0] for pair in PERIOD_FIELDS.values()})


def get_profile(module, client, name):
    """One profile by name, refusing to guess between duplicates (#72/#85)."""
    try:
        return resolve_one(module, client.snapshot_profiles, name,
                           'snapshot profile', fields=PROFILE_FIELDS)
    except NotFoundError:
        return None


def profile_result(client, profile):
    data = dict(profile)
    key = data.get('$key')
    result = {
        'key': key,
        'name': data.get('name'),
        'description': data.get('description'),
        'periods': [],
    }
    for period in client.snapshot_profiles.periods(key).list(
            fields=PERIOD_READ_FIELDS):
        pdata = dict(period)
        result['periods'].append({
            'name': pdata.get('name'),
            'frequency': pdata.get('frequency'),
            'retention': int(pdata.get('retention') or 0),
            'minute': int(pdata.get('minute') or 0),
            'hour': int(pdata.get('hour') or 0),
            'day_of_week': pdata.get('day_of_week'),
            'max_tier': int(pdata.get('max_tier') or 1),
            'quiesce': bool(pdata.get('quiesce', False)),
            'min_snapshots': int(pdata.get('min_snapshots') or 1),
        })
    return result


def reconcile_periods(module, client, profile_key, actions):
    desired = module.params.get('periods')
    if desired is None:
        return False

    changed = False
    manager = client.snapshot_profiles.periods(profile_key)
    existing = {dict(p).get('name'): p
                for p in manager.list(fields=PERIOD_READ_FIELDS)}

    for want in desired:
        have = existing.get(want['name'])
        if have is None:
            actions.append("created period '%s'" % want['name'])
            changed = True
            if not module.check_mode:
                manager.create(
                    name=want['name'],
                    frequency=want['frequency'],
                    retention=want['retention'],
                    minute=want['minute'],
                    hour=want['hour'],
                    day_of_week=want['day_of_week'],
                    day_of_month=want['day_of_month'],
                    month=want['month'],
                    max_tier=want['max_tier'],
                    quiesce=want['quiesce'],
                    min_snapshots=want['min_snapshots'],
                    immutable=want['immutable'],
                )
            continue

        have_data = dict(have)
        changes = {}
        for param, (field, norm) in PERIOD_FIELDS.items():
            current = have_data.get(field)
            current = norm(current) if current is not None else norm(0)
            if current != norm(want[param]):
                # max_tier is stored as a string by the API
                changes[field] = str(want[param]) if field == 'max_tier' \
                    else want[param]
        if changes:
            actions.append("updated period '%s': %s"
                           % (want['name'], ', '.join(sorted(changes))))
            changed = True
            if not module.check_mode:
                manager.update(have_data['$key'], **changes)

    if module.params['purge_periods']:
        wanted = {want['name'] for want in desired}
        for name, have in existing.items():
            if name not in wanted:
                actions.append("deleted period '%s'" % name)
                changed = True
                if not module.check_mode:
                    manager.delete(dict(have)['$key'])

    return changed


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent']),
        description=dict(type='str'),
        periods=dict(
            type='list', elements='dict',
            options=dict(
                name=dict(type='str', required=True),
                frequency=dict(type='str', required=True,
                               choices=['hourly', 'daily', 'weekly',
                                        'monthly', 'yearly', 'custom']),
                retention=dict(type='int', required=True),
                minute=dict(type='int', default=0),
                hour=dict(type='int', default=0),
                day_of_week=dict(type='str', default='any',
                                 choices=['sun', 'mon', 'tue', 'wed',
                                          'thu', 'fri', 'sat', 'any']),
                day_of_month=dict(type='int', default=0),
                month=dict(type='int', default=0),
                max_tier=dict(type='int', default=1),
                quiesce=dict(type='bool', default=False),
                min_snapshots=dict(type='int', default=1),
                immutable=dict(type='bool', default=False),
            ),
        ),
        purge_periods=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    actions = []
    name = module.params['name']

    try:
        profile = get_profile(module, client, name)

        if module.params['state'] == 'absent':
            if profile is None:
                module.exit_json(changed=False, actions=actions,
                                 msg="Profile '%s' does not exist" % name)
            actions.append("deleted profile '%s'" % name)
            if not module.check_mode:
                client.snapshot_profiles.delete(dict(profile)['$key'])
            module.exit_json(changed=True, actions=actions)

        changed = False
        if profile is None:
            actions.append("created profile '%s'" % name)
            changed = True
            if module.check_mode:
                for want in module.params.get('periods') or []:
                    actions.append("created period '%s'" % want['name'])
                module.exit_json(changed=True, actions=actions,
                                 profile={'name': name})
            create_args = {'name': name}
            if module.params.get('description') is not None:
                create_args['description'] = module.params['description']
            profile = client.snapshot_profiles.create(**create_args)
        elif module.params.get('description') is not None:
            current = dict(profile).get('description')
            if current != module.params['description']:
                actions.append('updated description')
                changed = True
                if not module.check_mode:
                    profile = client.snapshot_profiles.update(
                        dict(profile)['$key'],
                        description=module.params['description'])

        profile_key = dict(profile)['$key']
        changed = reconcile_periods(module, client, profile_key, actions) \
            or changed

        module.exit_json(changed=changed, actions=actions,
                         profile=profile_result(client, profile))

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
