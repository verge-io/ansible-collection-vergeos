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
  - A period may limit which VMs a cloud snapshot captures.
    O(periods[].include_tags) keeps only those tags, O(periods[].exclude_tags)
    drops them, and O(periods[].quiesce_tags) freezes disks on a partial
    period. Leave all three off and the period stays an all-or-nothing
    schedule. Those parameters need pyVergeOS 1.8.0 or later (PR 200).
    On an older SDK the task fails instead of writing a field VergeOS
    would drop. The collection floor stays pyvergeos>=1.2.8.
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
      include_tags:
        description:
          - Capture only VMs carrying these tags. Each entry is a tag name
            or a numeric tag key.
          - Mutually exclusive with C(exclude_tags).
          - Needs pyVergeOS 1.8.0 or later (PR 200). The collection floor stays pyvergeos>=1.2.8.
        type: list
        elements: str
        version_added: "2.3.0"
      exclude_tags:
        description:
          - Capture every VM except those carrying these tags.
          - Mutually exclusive with C(include_tags).
          - Needs pyVergeOS 1.8.0 or later (PR 200). The collection floor stays pyvergeos>=1.2.8.
        type: list
        elements: str
        version_added: "2.3.0"
      quiesce_tags:
        description:
          - Freeze disks on VMs carrying these tags during the snapshot.
          - Only valid together with C(include_tags) or C(exclude_tags).
          - Needs pyVergeOS 1.8.0 or later (PR 200). The collection floor stays pyvergeos>=1.2.8.
        type: list
        elements: str
        version_added: "2.3.0"
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

- name: Daily snapshots of the database tier only
  vergeio.vergeos.snapshot_profile:
    name: db-backups
    periods:
      - name: Daily
        frequency: daily
        retention: 604800
        hour: 2
        include_tags:
          - database
        quiesce_tags:
          - database
    state: present

- name: Delete a profile
  vergeio.vergeos.snapshot_profile:
    name: old-profile
    state: absent
'''

RETURN = r'''
profile:
  description:
    - State of the profile after the module ran.
    - When a period sets include_tags, exclude_tags, or quiesce_tags, that
      period also reports snapshot_type, partial_tag_keys, and
      quiesce_tag_keys. A period with none of those parameters does not.
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
        snapshot_type: partial_include
        partial_tag_keys:
          - 3
        quiesce_tag_keys:
          - 3
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
  sample: ["created profile 'vm-backups'", "created period 'Daily'"]
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

# How a tag-scoped period is read back. partial_tags and quiesce_tags are
# read-only rows; the tag keys sit on partial_tags[refs[tag]] and
# quiesce_tags[refs[tag]]. Those are the names pyVergeOS #200 puts on the
# period projection, and the names snapshot_type / partial_tag_keys /
# quiesce_tag_keys read. The write side is include_tags, exclude_tags, and
# quiesce_tags, not these columns.
TAG_READ_FIELDS = [
    'type',
    'partial_tags[refs[tag]]',
    'quiesce_tags[refs[tag]]',
]


def _tag_names(value):
    """Tag names or numeric keys, blanks and duplicates dropped."""
    if not value:
        return []
    names = []
    for item in value:
        text = str(item).strip()
        if text and text not in names:
            names.append(text)
    return names


def _managing_scope(want):
    """True when this period declares a tag scope.

    An empty or omitted list is not a scope. That is what keeps a playbook
    that never mentioned tags from rewriting periods, on this SDK and on
    the one that grew the parameters.
    """
    return bool(_tag_names(want.get('include_tags'))
                or _tag_names(want.get('exclude_tags'))
                or _tag_names(want.get('quiesce_tags')))


def _reject_bad_scope(module, want):
    include = _tag_names(want.get('include_tags'))
    exclude = _tag_names(want.get('exclude_tags'))
    quiesce = _tag_names(want.get('quiesce_tags'))
    if include and exclude:
        module.fail_json(msg=(
            "period '%s' sets both include_tags and exclude_tags. "
            "A partial snapshot is include-scoped or exclude-scoped."
            % want['name']))
    if quiesce and not include and not exclude:
        module.fail_json(msg=(
            "period '%s' sets quiesce_tags without include_tags or "
            "exclude_tags. Quiesce tags apply only to a partial snapshot."
            % want['name']))


def period_tags_supported(create):
    """Whether ``periods.create()`` accepts ``include_tags``.

    A MagicMock's signature is ``*args, **kwargs``, which counts as yes so
    a unit test can walk the tag path. A released pyVergeOS lists its
    parameters and does not have ``include_tags`` until 1.8.0 (PR #200).
    Sending the keyword to an older method raises
    TypeError. Sending a column the method does not know about is worse:
    VergeOS drops unknown fields and still returns success.
    """
    if create is None:
        return False
    try:
        signature = inspect.signature(create)
    except (TypeError, ValueError):
        return False
    if 'include_tags' in signature.parameters:
        return True
    return any(param.kind is inspect.Parameter.VAR_KEYWORD
               for param in signature.parameters.values())


def _scope_create_fn(client, profile):
    """The ``create`` that will actually write the period.

    When the profile already exists, that is its period manager. When it
    does not, ``periods(0)`` only builds the manager so we can read the
    signature. It does not request anything. A test double returns whatever
    ``create`` the test planted, which is how an older SDK is simulated
    without uninstalling pyvergeos.
    """
    if profile is not None:
        manager = client.snapshot_profiles.periods(dict(profile)['$key'])
    else:
        manager = client.snapshot_profiles.periods(0)
    return getattr(manager, 'create', None)


def _require_period_tags(module, create):
    if period_tags_supported(create):
        return
    module.fail_json(msg=(
        "include_tags, exclude_tags, and quiesce_tags need pyVergeOS 1.8.0 "
        "or later (PR #200, tag-scoped snapshot profile periods). "
        "This SDK's periods.create() does not accept include_tags. The "
        "collection floor stays pyvergeos>=1.2.8. Leave the tag parameters "
        "off for an all-or-nothing period, which this SDK still writes."
    ))


def _guard_scopes(module, client, profile, desired):
    managing = False
    for want in desired or []:
        _reject_bad_scope(module, want)
        if _managing_scope(want):
            managing = True
    if managing:
        _require_period_tags(module, _scope_create_fn(client, profile))
    return managing


def _resolve_tag_keys(module, client, names, cache):
    """Names to ``$key`` values, matching the SDK's rule.

    A numeric string is already a key. A name has to match one tag. Names
    are unique only inside a category, so two hits is a refusal. The keys
    are what ``partial_tag_keys`` is compared with, so the same names on
    the next run are not drift.
    """
    if not names:
        return []
    if any(not name.isdigit() for name in names) and 'rows' not in cache:
        cache['rows'] = [dict(tag) for tag in client.tags.list()]
    rows = cache.get('rows') or []
    keys = []
    for name in names:
        if name.isdigit():
            keys.append(int(name))
            continue
        matches = [row for row in rows if row.get('name') == name]
        if not matches:
            module.fail_json(msg="no tag named '%s'" % name)
        if len(matches) > 1:
            module.fail_json(msg=(
                "tag name '%s' matches %d tags. Pass the tag key."
                % (name, len(matches))))
        keys.append(int(matches[0]['$key']))
    return keys


def _read_scope(period):
    """``snapshot_type`` and the two key lists, when the SDK has them.

    A build older than 1.8.0 (PR #200) has neither property. Callers only get here
    after ``_require_period_tags`` has accepted the SDK, so a missing
    property is read as a full period with no keys and the next compare
    sends the scope.
    """
    kind = getattr(period, 'snapshot_type', None)
    if not isinstance(kind, str):
        raw = dict(period).get('type')
        kind = str(raw) if raw else 'full'
    partial = getattr(period, 'partial_tag_keys', None)
    quiesce = getattr(period, 'quiesce_tag_keys', None)
    if not isinstance(partial, list):
        partial = []
    if not isinstance(quiesce, list):
        quiesce = []
    return kind, [int(key) for key in partial], [int(key) for key in quiesce]


def _scope_kwargs(want, clear_quiesce):
    """SDK keyword arguments for one period's tag scope.

    Empty quiesce is omitted on create. On update, an empty list is how
    the SDK clears ``quiesce_tag_args`` (``clear_empty_quiesce``).
    """
    include = _tag_names(want.get('include_tags'))
    exclude = _tag_names(want.get('exclude_tags'))
    quiesce = _tag_names(want.get('quiesce_tags'))
    kwargs = {}
    if include:
        kwargs['include_tags'] = include
    elif exclude:
        kwargs['exclude_tags'] = exclude
    if quiesce:
        kwargs['quiesce_tags'] = quiesce
    elif clear_quiesce:
        kwargs['quiesce_tags'] = []
    return kwargs


def _scope_changes(module, client, period, want, cache):
    """Keyword arguments that bring a stored period in line, or ``{}``."""
    if not _managing_scope(want):
        return {}
    include = _tag_names(want.get('include_tags'))
    exclude = _tag_names(want.get('exclude_tags'))
    quiesce = _tag_names(want.get('quiesce_tags'))
    desired_kind = 'partial_include' if include else 'partial_exclude'
    desired_partial = _resolve_tag_keys(
        module, client, include or exclude, cache)
    desired_quiesce = _resolve_tag_keys(module, client, quiesce, cache)
    kind, partial, current_quiesce = _read_scope(period)
    if (kind == desired_kind
            and set(partial) == set(desired_partial)
            and set(current_quiesce) == set(desired_quiesce)):
        return {}
    return _scope_kwargs(
        want, clear_quiesce=bool(current_quiesce) and not quiesce)


def _period_fields(include_scope):
    if not include_scope:
        return list(PERIOD_READ_FIELDS)
    fields = list(PERIOD_READ_FIELDS)
    for name in TAG_READ_FIELDS:
        if name not in fields:
            fields.append(name)
    return fields


def get_profile(module, client, name):
    """One profile by name, refusing to guess between duplicates (#72/#85)."""
    try:
        return resolve_one(module, client.snapshot_profiles, name,
                           'snapshot profile', fields=PROFILE_FIELDS)
    except NotFoundError:
        return None


def profile_result(client, profile, include_scope=False):
    data = dict(profile)
    key = data.get('$key')
    result = {
        'key': key,
        'name': data.get('name'),
        'description': data.get('description'),
        'periods': [],
    }
    for period in client.snapshot_profiles.periods(key).list(
            fields=_period_fields(include_scope)):
        pdata = dict(period)
        row = {
            'name': pdata.get('name'),
            'frequency': pdata.get('frequency'),
            'retention': int(pdata.get('retention') or 0),
            'minute': int(pdata.get('minute') or 0),
            'hour': int(pdata.get('hour') or 0),
            'day_of_week': pdata.get('day_of_week'),
            'max_tier': int(pdata.get('max_tier') or 1),
            'quiesce': bool(pdata.get('quiesce', False)),
            'min_snapshots': int(pdata.get('min_snapshots') or 1),
        }
        if include_scope:
            kind, partial, quiesce_keys = _read_scope(period)
            row['snapshot_type'] = kind
            row['partial_tag_keys'] = partial
            row['quiesce_tag_keys'] = quiesce_keys
        result['periods'].append(row)
    return result


def reconcile_periods(module, client, profile_key, actions, include_scope=False):
    desired = module.params.get('periods')
    if desired is None:
        return False

    changed = False
    manager = client.snapshot_profiles.periods(profile_key)
    fields = _period_fields(include_scope)
    existing = {dict(p).get('name'): p
                for p in manager.list(fields=fields)}
    tag_cache = {}

    for want in desired:
        have = existing.get(want['name'])
        if have is None:
            actions.append("created period '%s'" % want['name'])
            changed = True
            if not module.check_mode:
                create_kwargs = dict(
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
                # Only when the period actually names tags. Passing
                # include_tags=None is a different call, and an older SDK
                # rejects the keyword.
                create_kwargs.update(_scope_kwargs(want, clear_quiesce=False))
                manager.create(**create_kwargs)
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
        scope_changes = {}
        if include_scope and _managing_scope(want):
            scope_changes = _scope_changes(
                module, client, have, want, tag_cache)
        if changes or scope_changes:
            detail = sorted(changes)
            if scope_changes:
                detail.append('tag scope')
            actions.append("updated period '%s': %s"
                           % (want['name'], ', '.join(detail)))
            changed = True
            if not module.check_mode:
                payload = dict(changes)
                payload.update(scope_changes)
                manager.update(have_data['$key'], **payload)

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
                include_tags=dict(type='list', elements='str'),
                exclude_tags=dict(type='list', elements='str'),
                quiesce_tags=dict(type='list', elements='str'),
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
        include_scope = False
        if module.params['state'] != 'absent':
            include_scope = _guard_scopes(
                module, client, profile, module.params.get('periods'))

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
        changed = reconcile_periods(
            module, client, profile_key, actions, include_scope) or changed

        module.exit_json(
            changed=changed, actions=actions,
            profile=profile_result(client, profile, include_scope))

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except ValueError as e:
        module.fail_json(msg=str(e))
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
