#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: site_sync
short_description: Manage VergeOS outgoing site syncs
version_added: "2.1.0"
description:
  - Create, reconcile, enable, disable and remove outgoing site syncs -
    VergeOS's native site-to-site replication.
  - Also runs a sync on demand with O(state=started), and stops one in
    progress with O(state=stopped).
options:
  name:
    description:
      - Sync name, and the identity this module reconciles on.
    type: str
    required: true
  state:
    description:
      - C(present) creates the sync if absent and reconciles it if present.
      - C(absent) removes it. The snapshots already replicated to the remote
        system are not removed.
      - C(started) runs the sync now, creating it first if needed. A sync
        already running is left alone rather than restarted.
      - C(stopped) stops a run in progress. It does not disable the sync -
        use O(enabled=false) for that.
    type: str
    choices: [ present, absent, started, stopped ]
    default: present
  site:
    description:
      - Key of the site this sync belongs to. Required to create.
    type: int
  registration_code:
    description:
      - Registration code issued by the matching incoming sync on the remote
        system. Required to create.
      - Create-time only, and never reconciled - changing it here on an
        existing sync does nothing, because rewriting an established trust
        relationship on every run is not something this module should do
        silently.
      - Never returned in RV(sync), whether or not the platform echoes it
        back.
    type: str
  url:
    description:
      - URL of the destination system.
    type: str
  description:
    description:
      - Free-text description.
    type: str
  enabled:
    description:
      - Whether the sync is enabled. Left as-is when not specified.
    type: bool
  encryption:
    description:
      - Encrypt replication traffic.
    type: bool
  compression:
    description:
      - Compress replication traffic.
    type: bool
  netinteg:
    description:
      - Enable network integrity checking.
    type: bool
  threads:
    description:
      - Data transfer threads.
    type: int
  file_threads:
    description:
      - File transfer threads.
    type: int
  destination_tier:
    description:
      - Storage tier to land replicated data on at the destination.
    type: str
  queue_retry_count:
    description:
      - How many times a failed queue entry is retried.
    type: int
  queue_retry_interval:
    description:
      - Seconds between queue retries.
    type: int
  queue_retry_multiplier:
    description:
      - Back off between retries instead of using a fixed interval.
    type: bool
  note:
    description:
      - Operator note stored on the sync.
    type: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Supports C(check_mode).
  - Incoming syncs are deliberately out of scope. An incoming sync is the
    remote system's object, created there to issue the registration code this
    module consumes; managing both ends from one playbook run would need
    credentials for both systems and would make the direction of trust
    ambiguous.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Ensure replication to the DR site exists
  vergeio.vergeos.site_sync:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "to-dr-site"
    site: 1
    registration_code: "{{ vault_dr_registration_code }}"
    url: "https://dr.example.com"
    encryption: true
    compression: true
    destination_tier: "4"
    enabled: true

- name: Throttle replication during business hours
  vergeio.vergeos.site_sync:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "to-dr-site"
    threads: 2
    file_threads: 2

- name: Replicate now, ahead of a risky change
  vergeio.vergeos.site_sync:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "to-dr-site"
    state: started

- name: Pause replication without losing its configuration
  vergeio.vergeos.site_sync:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "to-dr-site"
    enabled: false
'''

RETURN = r'''
sync:
  description: The sync as it stands after the module ran.
  returned: unless O(state=absent) removed it
  type: dict
  sample:
    name: "to-dr-site"
    enabled: true
    online: true
    destination_tier: "4"
changed_fields:
  description:
    - Names of the fields this run reconciled. Empty on a no-op.
  returned: always
  type: list
  elements: str
  sample: ["threads", "file_threads"]
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.site_sync import (
    OUTGOING_MUTABLE,
    find_by_name,
    row_field,
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

# Module parameter -> SDK create()/update() keyword. The SDK spells two of the
# retry knobs more verbosely than the module does.
SDK_KEYWORD = {
    'queue_retry_interval': 'queue_retry_interval_seconds',
    'queue_retry_multiplier': 'queue_retry_interval_multiplier',
}


def desired(params):
    """The fields the caller actually specified, keyed by SDK keyword."""
    out = {}
    for name in ('description', 'url', 'enabled', 'encryption', 'compression',
                 'netinteg', 'threads', 'file_threads', 'destination_tier',
                 'queue_retry_count', 'queue_retry_interval',
                 'queue_retry_multiplier', 'note'):
        if params.get(name) is not None:
            out[SDK_KEYWORD.get(name, name)] = params[name]
    return out


def drift(current, wanted):
    """Fields whose live value differs from what was asked for.

    Compared against the ROW's spelling, not the SDK keyword's: an update that
    compares 'queue_retry_interval_seconds' against a row carrying
    'queue_retry_interval' finds a difference on every run and reports drift
    that is not there.
    """
    changes = {}
    for keyword, want in wanted.items():
        if keyword not in OUTGOING_MUTABLE:
            continue
        have = current.get(row_field(keyword))
        if isinstance(want, bool):
            if bool(have) != want:
                changes[keyword] = want
        elif isinstance(want, int):
            try:
                if int(have) != want:
                    changes[keyword] = want
            except (TypeError, ValueError):
                changes[keyword] = want
        elif str(have or '') != str(want):
            changes[keyword] = want
    return changes


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent', 'started', 'stopped']),
        site=dict(type='int'),
        registration_code=dict(type='str', no_log=True),
        url=dict(type='str'),
        description=dict(type='str'),
        enabled=dict(type='bool'),
        encryption=dict(type='bool'),
        compression=dict(type='bool'),
        netinteg=dict(type='bool'),
        threads=dict(type='int'),
        file_threads=dict(type='int'),
        destination_tier=dict(type='str'),
        queue_retry_count=dict(type='int'),
        queue_retry_interval=dict(type='int'),
        queue_retry_multiplier=dict(type='bool'),
        note=dict(type='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    params = module.params
    name = params['name']
    state = params['state']
    client = get_vergeos_client(module)

    try:
        manager = client.site_syncs
        current = find_by_name(manager, name)
        wanted = desired(params)

        # ── absent ───────────────────────────────────────────────────────────
        if state == 'absent':
            if not current:
                module.exit_json(changed=False, changed_fields=[])
            if not module.check_mode:
                manager.delete(current['$key'])
            module.exit_json(
                changed=True, changed_fields=['removed'],
                msg="removed sync '%s'. Snapshots already replicated to the "
                    "remote system were not removed." % name)

        # ── stopped ──────────────────────────────────────────────────────────
        if state == 'stopped':
            if not current:
                module.fail_json(msg="no sync named '%s' to stop." % name)
            if not current.get('syncing'):
                module.exit_json(changed=False, changed_fields=[],
                                 sync=summarize(current),
                                 msg="sync '%s' is not running." % name)
            if not module.check_mode:
                manager.get(key=current['$key']).stop()
                current = find_by_name(manager, name) or current
            module.exit_json(changed=True, changed_fields=['stopped'],
                             sync=summarize(current))

        # ── present / started ────────────────────────────────────────────────
        changed_fields = []

        if not current:
            missing = [p for p in ('site', 'registration_code')
                       if params.get(p) in (None, '')]
            if missing:
                module.fail_json(
                    msg="creating sync '%s' needs %s. The registration code "
                        "comes from the matching incoming sync on the remote "
                        "system." % (name, " and ".join(missing)))
            if module.check_mode:
                module.exit_json(changed=True, changed_fields=['created'],
                                 msg="would create sync '%s'." % name)
            created = manager.create(
                site=params['site'], name=name,
                registration_code=params['registration_code'], **wanted)
            current = dict(created)
            changed_fields.append('created')
        else:
            changes = drift(current, wanted)
            if changes:
                changed_fields = sorted(changes)
                if not module.check_mode:
                    manager.update(current['$key'], **changes)
                    current = find_by_name(manager, name) or current

        if state == 'started':
            # A sync already running is left alone. Restarting one mid-flight
            # would discard the progress it has made, and "make sure
            # replication is happening" is satisfied either way.
            if current.get('syncing'):
                module.exit_json(
                    changed=bool(changed_fields), changed_fields=changed_fields,
                    sync=summarize(current),
                    msg="sync '%s' was already running." % name)
            if not module.check_mode:
                manager.get(key=current['$key']).start()
                current = find_by_name(manager, name) or current
            changed_fields.append('started')

        module.exit_json(changed=bool(changed_fields),
                         changed_fields=changed_fields,
                         sync=summarize(current))

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
