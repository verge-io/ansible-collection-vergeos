#!/usr/bin/env python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Re-capture tests/fixtures/api/ from a real VergeOS system.

    export VERGEOS_HOST=... VERGEOS_USERNAME=... VERGEOS_PASSWORD=...
    python tests/capture_api_fixtures.py

Each capture records the CALL that produced it, because a row's shape depends
on the projection rather than the table -- 'running' is present in
client.nodes.list() and absent from GET /nodes?fields=all, and the same
membership row is 'users/1' through the SDK and '/v4/users/2' through
GET /members?fields=all. A capture that did not say where it came from could
not be checked against anything.

Read-only. Nothing here creates, modifies or deletes.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import json
import os
import re
import sys


# Values that identify a person or a network, redacted on capture.
#
# These fixtures exist for field NAMES and SHAPES. A real email address or
# BMC address adds nothing to that and does not belong in a public
# repository. Redaction preserves the type and the general shape, because a
# test that reads len() or splits on '@' should still behave.
#
# Deliberately NOT redacted: reference values such as member='users/1' and
# member='vms/36'. Their exact form is the thing three bugs turned on, so
# blanking them would remove the only evidence the fixture carries.
REDACT_FIELDS = {
    'email': 'user@example.com',
    'ipmi_address': '198.51.100.10',
    'ipmi_user': 'ipmiuser',
    'asset_tag': 'ASSET-0000',
    'note': '',
    'lldp': '',
}

EMAIL = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')


def scrub(value, field=None):
    """Redact identifying values, preserving type and shape."""
    if field in REDACT_FIELDS:
        return REDACT_FIELDS[field]
    if isinstance(value, str) and EMAIL.search(value):
        return EMAIL.sub('user@example.com', value)
    if isinstance(value, dict):
        return {k: scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'fixtures', 'api')


def build(client):
    """name -> (call description, callable returning rows)."""
    def listing(manager, limit=None):
        return lambda: [dict(r) for r in manager.list()][:limit]

    return {
        'nodes': ('client.nodes.list()', listing(client.nodes)),
        'groups': ('client.groups.list()', listing(client.groups)),
        'users': ('client.users.list()', listing(client.users, 3)),
        'clusters': ('client.clusters.list()', listing(client.clusters)),
        'physical_drives': ('client.physical_drives.list()',
                            listing(client.physical_drives)),
        'vm_recipes': ('client.vm_recipes.list()', listing(client.vm_recipes, 3)),
        'vms': ('client.vms.list()', listing(client.vms, 3)),
        'networks': ('client.networks.list()', listing(client.networks, 3)),
        'group_members': ('client.groups.members(1).list()',
                          lambda: [dict(r) for r in client.groups.members(1).list()]),
        'update_settings': ('client.update_settings.get()',
                            lambda: [dict(client.update_settings.get())]),
        'cluster_status': ("client._request('GET','cluster_status',{'fields':'all'})",
                           lambda: client._request('GET', 'cluster_status',
                                                   params={'fields': 'all'})),
        'tag_members': ("client._request('GET','tag_members',{'fields':'all'})",
                        lambda: client._request('GET', 'tag_members',
                                                params={'fields': 'all'})),
    }


def main():
    try:
        from pyvergeos import VergeClient
    except ImportError:
        sys.exit('pyvergeos is not installed')

    missing = [v for v in ('VERGEOS_HOST', 'VERGEOS_USERNAME', 'VERGEOS_PASSWORD')
               if not os.environ.get(v)]
    if missing:
        sys.exit('set %s' % ', '.join(missing))

    client = VergeClient(
        host=os.environ['VERGEOS_HOST'],
        username=os.environ['VERGEOS_USERNAME'],
        password=os.environ['VERGEOS_PASSWORD'],
        verify_ssl=os.environ.get('VERGEOS_INSECURE', '').lower() not in
        ('1', 'true', 'yes'))
    client.connect()

    version = ''
    try:
        version = str(getattr(client._connection, 'vergeos_version', '') or '')
    except Exception:
        pass

    meta = {
        'captured': datetime.datetime.now().date().isoformat(),
        'platform': version or 'unknown',
        'note': 'Regenerate with tests/capture_api_fixtures.py. Do not '
                'hand-edit: the point of these files is that no human chose '
                'the field names.',
    }

    if not os.path.isdir(FIXTURE_DIR):
        os.makedirs(FIXTURE_DIR)

    for name, (call, fetch) in sorted(build(client).items()):
        try:
            data = fetch() or []
        except Exception as exc:                      # noqa: BLE001
            print('%-18s SKIPPED (%s)' % (name, exc))
            continue
        if not isinstance(data, list):
            data = [data]
        data = [scrub(d) for d in data if isinstance(d, dict)]
        doc = {'_call': call, '_meta': meta, 'rows': data}
        with open(os.path.join(FIXTURE_DIR, '%s.json' % name), 'w') as handle:
            json.dump(doc, handle, indent=1, sort_keys=True, default=str)
            handle.write('\n')
        keys = sorted({k for r in data for k in r})
        print('%-18s %2d rows %3d fields  <- %s' % (name, len(data), len(keys), call))
        if not data:
            print('%-18s    ^ nothing of this kind exists on the capture system;'
                  ' row() will refuse to build on it' % '')


if __name__ == '__main__':
    main()
