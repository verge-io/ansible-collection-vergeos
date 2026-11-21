#!/usr/bin/env python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Live helper for a tag-scoped cloud snapshot.

Verbs, all of them JSON on stdout:

  show-period --profile NAME --period NAME
      Read one snapshot profile period through the pyVergeOS #200
      properties (snapshot_type, partial_tag_keys, quiesce_tag_keys).
      An SDK that does not have those fails here, by name.

  create --name NAME --include-tag TAG [--timeout SECONDS]
      cloud_snapshots.create(include_tags=[TAG], wait=True). If the
      platform answers ValidationError 'System snapshot is still building',
      retry until --timeout (default 300 seconds). A profile period can
      start a system snapshot, and listed rows can already read normal
      while create is still refused, so this does not poll those rows.
      Any other error is not retried. Then
      GET cloud_snapshots/N?fields=all until status is set and is not
      ``building``. wait=True polls cloud_snapshots.get() and can return
      while that fields=all read still says building (pyVergeOS #202),
      so get() is not the check. Then
      POST cloud_snapshot_actions {action: find_vms, cloud_snapshot: N}.
      Membership stays empty until that runs (pyVergeOS #201), and the
      action has nothing to fill in while the snapshot is still building.
      The same ValidationError is retried, for the same --timeout.
      The POST returns before cloud_snapshot_vms rows exist (on the lab,
      6 to 8 seconds later), so the VM names are read from
      GET cloud_snapshot_vms?filter=cloud_snapshot eq N and is_snapshot eq false
      until that list is non-empty or --timeout. This does not call
      CloudSnapshotVMManager.list() or get_vms(), so a released pyVergeOS
      and one that contains the unreleased #210 poll behave the same.

  delete --name NAME
      Delete every cloud snapshot of that name. Missing is success.

Refuses to create without an include tag. That call would snapshot the
whole cloud. Auth is VERGEOS_* in the environment, same as protect_ctl.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import argparse
import inspect
import json
import os
import sys
import time


# Same projection pyVergeOS #200 puts on a period so partial_tag_keys has
# something to read. partial_tags / quiesce_tags alone are the row, not the tags.
PERIOD_FIELDS = [
    '$key',
    'name',
    'type',
    'partial_tags[refs[tag]]',
    'quiesce_tags[refs[tag]]',
]

MEMBER_FILTER = 'cloud_snapshot eq %d and is_snapshot eq false'
MEMBER_FIELDS = 'name,original_key,is_snapshot,cloud_snapshot'


def client():
    from pyvergeos import VergeClient
    kwargs = {
        'host': os.environ['VERGEOS_HOST'],
        'verify_ssl': os.environ.get('VERGEOS_INSECURE', '') not in (
            '1', 'true', 'True'),
    }
    if os.environ.get('VERGEOS_TOKEN'):
        kwargs['token'] = os.environ['VERGEOS_TOKEN']
    else:
        kwargs['username'] = os.environ['VERGEOS_USERNAME']
        kwargs['password'] = os.environ['VERGEOS_PASSWORD']
    return VergeClient(**kwargs)


def _as_rows(payload):
    if payload is None:
        return []
    if isinstance(payload, dict):
        return [payload]
    return list(payload)


def settled(status):
    """A snapshot read is done when status is present and not building.

    A missing status is not done. The accessor on the SDK defaults that
    to ``normal``, which is how wait=True can return early (#202). Read
    the field.
    """
    return bool(status) and status != 'building'


def _cloud_tags_supported(create):
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


def _snapshot_key(sdk, name):
    rows = _as_rows(sdk._request(
        'GET', 'cloud_snapshots', params={'fields': '$key,name'}))
    keys = [int(row['$key']) for row in rows if row.get('name') == name]
    if not keys:
        return None
    return keys[-1]


def snapshot_status(sdk, key):
    """Status from ``GET cloud_snapshots/N?fields=all``.

    ``create(wait=True)`` decides it is done from ``cloud_snapshots.get()``.
    That get can return while a ``fields=all`` read of the same row still
    says ``building`` (pyVergeOS #202). ``status`` is the column the SDK
    already waits on, and ``fields=all`` is the read #202 compared with
    the early return. ``find_vms`` runs only after this leaves
    ``building``, because a snapshot that is still building has no VM
    list to fill in (#201).
    """
    payload = sdk._request(
        'GET', 'cloud_snapshots/%d' % int(key),
        params={'fields': 'all'},
    )
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if isinstance(payload, dict):
        return payload.get('status')
    return None


def wait_until_settled(sdk, key, timeout):
    """Poll fields=all until status is present and not ``building``.

    A slow GET is ``VergeTimeoutError``, not the builtin ``TimeoutError``,
    so it would otherwise escape this loop and fail the command while the
    wait budget is still open. One slow read of a snapshot that is still
    building is not a settled snapshot. It is retried until the budget
    runs out.
    """
    start = time.time()
    last = None
    while True:
        try:
            last = snapshot_status(sdk, key)
        except _wait_timeout_type():
            last = None
        if settled(last):
            return last
        if timeout > 0 and (time.time() - start) > timeout:
            raise TimeoutError(
                'cloud snapshot %s still building after %s seconds '
                '(last status: %r)' % (key, timeout, last))
        time.sleep(2)


def _retry_while_building(operation, timeout):
    """Retry ``operation`` on ValidationError 'System snapshot is still building'.

    Listed rows can already read normal while the platform still refuses
    the POST, so this does not poll them. Any other error is re-raised.
    ``timeout`` of 0 has no deadline.
    """
    start = time.time()
    delay = 2
    while True:
        try:
            return operation()
        except _validation_error_type() as exc:
            if not _system_snapshot_busy(exc):
                raise
            elapsed = time.time() - start
            if timeout > 0 and elapsed >= timeout:
                raise TimeoutError(
                    'system snapshot still building after %s seconds (%s)'
                    % (timeout, exc))
            pause = delay
            if timeout > 0:
                pause = min(delay, max(timeout - elapsed, 0))
            time.sleep(pause)
            if delay < 30:
                delay = min(delay * 2, 30)


def find_vms(sdk, key, timeout):
    """POST find_vms. cloud_snapshot_vms stays empty until this runs (#201).

    The POST raises ValidationError 'System snapshot is still building'
    after create() was accepted and the fields=all read has left
    building. That error is retried the same way as create(). The POST
    goes through ``_request``, not ``cloud_snapshots.find_vms()``, so
    an SDK that retries inside that method is not required and is not
    asked twice.
    """
    def post():
        sdk._request(
            'POST', 'cloud_snapshot_actions',
            json_data={'action': 'find_vms', 'cloud_snapshot': int(key)},
        )

    _retry_while_building(post, timeout)


def member_names(sdk, key):
    rows = _as_rows(sdk._request(
        'GET', 'cloud_snapshot_vms',
        params={
            'filter': MEMBER_FILTER % int(key),
            'fields': MEMBER_FIELDS,
        },
    ))
    return [str(row.get('name') or '') for row in rows]


def wait_for_members(sdk, key, timeout):
    """Poll cloud_snapshot_vms until a row exists.

    find_vms returns before the platform fills those rows. On the lab
    they appeared 6 to 8 seconds later. An empty read is not a finished
    membership. ``timeout`` of 0 has no deadline.
    """
    start = time.time()
    while True:
        names = member_names(sdk, key)
        if names:
            return names
        if timeout > 0 and (time.time() - start) > timeout:
            raise TimeoutError(
                'cloud snapshot %s membership still empty after %s seconds'
                % (key, timeout))
        time.sleep(2)


def _wait_timeout_type():
    from pyvergeos.exceptions import VergeTimeoutError
    return VergeTimeoutError


def _validation_error_type():
    from pyvergeos.exceptions import ValidationError
    return ValidationError


def _system_snapshot_busy(exc):
    """The refusal measured on the lab: another system snapshot is building."""
    return 'still building' in str(exc).lower()


def _create_snapshot(sdk, name, include_tag, timeout):
    """Create one include-scoped snapshot once the platform will accept it.

    Saving the temporary profile period starts a system snapshot. create()
    then raises ValidationError 'System snapshot is still building' until
    that finishes. Rows already on the system can read status normal from
    fields=all while the platform still refuses, so waiting on those rows
    does not see it.
    """
    return _retry_while_building(
        lambda: sdk.cloud_snapshots.create(
            name=name,
            include_tags=[include_tag],
            retention_seconds=3600,
            wait=True,
            wait_timeout=timeout,
        ),
        timeout,
    )


def create_partial(sdk, name, include_tag, timeout=300):
    """One include-scoped cloud snapshot, settled, with VM names filled in."""
    include_tag = str(include_tag or '').strip()
    if not include_tag:
        raise ValueError(
            'refusing to create a cloud snapshot without an include tag. '
            'That would capture the whole cloud.')
    create = getattr(getattr(sdk, 'cloud_snapshots', None), 'create', None)
    if not _cloud_tags_supported(create):
        raise ValueError(
            'this pyVergeOS cloud_snapshots.create() does not accept '
            'include_tags. Tag-scoped cloud snapshots need the parameter '
            'added with pyVergeOS #129.')
    try:
        snap = _create_snapshot(sdk, name, include_tag, timeout)
        key = dict(snap).get('$key')
    except _wait_timeout_type():
        # create(wait=True) gave up, but the row may already exist (#202).
        key = _snapshot_key(sdk, name)
        if key is None:
            raise
    if key is None:
        raise ValueError('cloud snapshot create returned no $key')
    key = int(key)
    status = wait_until_settled(sdk, key, timeout)
    find_vms(sdk, key, timeout)
    return {
        'key': key,
        'status': status,
        'members': wait_for_members(sdk, key, timeout),
    }


def delete_named(sdk, name):
    rows = _as_rows(sdk._request(
        'GET', 'cloud_snapshots', params={'fields': '$key,name'}))
    deleted = []
    for row in rows:
        if row.get('name') == name and row.get('$key') is not None:
            sdk.cloud_snapshots.delete(int(row['$key']))
            deleted.append(int(row['$key']))
    return {'deleted': deleted}


def show_period(sdk, profile_name, period_name):
    """One period's tag scope, via the properties #200 added."""
    profiles = [dict(row) for row in sdk.snapshot_profiles.list()]
    matches = [row for row in profiles if row.get('name') == profile_name]
    if len(matches) != 1:
        raise ValueError(
            'expected one snapshot profile named %r, found %d'
            % (profile_name, len(matches)))
    profile_key = int(matches[0]['$key'])
    periods = sdk.snapshot_profiles.periods(profile_key).list(
        fields=list(PERIOD_FIELDS))
    found = None
    for period in periods:
        if dict(period).get('name') == period_name:
            found = period
            break
    if found is None:
        raise ValueError(
            'no period %r on profile %r' % (period_name, profile_name))
    kind = getattr(found, 'snapshot_type', None)
    partial = getattr(found, 'partial_tag_keys', None)
    quiesce = getattr(found, 'quiesce_tag_keys', None)
    if (not isinstance(kind, str) or not isinstance(partial, list)
            or not isinstance(quiesce, list)):
        raise ValueError(
            'this pyVergeOS cannot read a period tag scope '
            '(snapshot_type, partial_tag_keys, quiesce_tag_keys). '
            'Those landed in PR #200, which is on dev and not in a '
            'release yet.')
    return {
        'profile_key': profile_key,
        'period_key': int(dict(found)['$key']),
        'snapshot_type': kind,
        'partial_tag_keys': [int(key) for key in partial],
        'quiesce_tag_keys': [int(key) for key in quiesce],
    }


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'verb', choices=['show-period', 'create', 'delete'])
    parser.add_argument('--profile')
    parser.add_argument('--period')
    parser.add_argument('--name')
    parser.add_argument('--include-tag')
    parser.add_argument('--timeout', type=int, default=300)
    return parser


def scrub(text):
    """One line a playbook can print.

    The ladder runs this with no_log, because the password sits in the
    environment, and no_log hides the whole task result. The playbook
    prints stderr when the command fails, so a credential must not be
    in that line. The API error's response body is not included, because
    pyVergeOS says that body may carry secrets.
    """
    out = str(text)
    for name in ('VERGEOS_PASSWORD', 'VERGEOS_TOKEN'):
        secret = os.environ.get(name)
        if secret:
            out = out.replace(secret, '<%s>' % name)
    return out


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        sdk = client()
        if args.verb == 'show-period':
            if not args.profile or not args.period:
                parser.error('--profile and --period are required')
            payload = show_period(sdk, args.profile, args.period)
        elif args.verb == 'create':
            if not args.name:
                parser.error('--name is required')
            payload = create_partial(
                sdk, args.name, args.include_tag, timeout=args.timeout)
        else:
            if not args.name:
                parser.error('--name is required')
            payload = delete_named(sdk, args.name)
    except Exception as exc:  # noqa: BLE001
        # Any failure, not only ValueError. A requests timeout arrives as
        # VergeTimeoutError, which is not the builtin TimeoutError, and a
        # conflict or an API error used to be a traceback under no_log.
        print('%s: %s' % (type(exc).__name__, scrub(exc)), file=sys.stderr)
        return 1
    json.dump(payload, sys.stdout)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
