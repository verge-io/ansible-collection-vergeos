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
# A second, stricter spelling for the self-check. The substitution regex
# above is deliberately greedy so it rewrites whatever it finds; scanning
# with it matches the surrounding JSON punctuation too, so every scrubbed
# address read as a leak and the real ones would have been lost in them.
EMAIL_STRICT = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')

# Private address space and home paths, for the self-check below. A capture is
# a public artefact; these are the shapes that leak a topology into one.
PRIVATE = re.compile(r'\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.'
                     r'\d{1,3}\.\d{1,3}\b')
HOME_PATH = re.compile(r'/home/[A-Za-z0-9_.-]+')

# Local identifiers -> generic stand-ins, filled in at capture time.
#
# Redacting by FIELD NAME catches only the values whose field you thought of.
# It cannot catch a site's own name, which arrives in cluster_name, in
# clusters.name, in creator, in member_display, and in any free-text field
# somebody typed it into. The captures made before this existed carried the
# lab's cluster name in two files and the operator's username in a third,
# straight into a public repository.
#
# So identifiers are substituted by VALUE as well as by field. Longest first,
# so a name that contains another name is replaced whole.
ALIASES = {}


def _alias(text):
    for real in sorted(ALIASES, key=len, reverse=True):
        if real:
            text = re.sub(re.escape(real), ALIASES[real], text,
                          flags=re.IGNORECASE)
    return text


def scrub(value, field=None):
    """Redact identifying values, preserving type and shape."""
    if field in REDACT_FIELDS:
        return REDACT_FIELDS[field]
    if isinstance(value, str):
        # Bound to a NEW name rather than reassigning `value`. pylint's
        # astroid on the collection's floor version then tries to infer
        # `value` in the dict comprehension below as the result of a compiled
        # pattern's .sub(), reaches its typing-alias brain for `re`, and dies
        # with an unhandled AstroidError -- the whole sanity run fails on a
        # file that has nothing wrong with it.
        text = _alias(value)
        if EMAIL.search(text):
            text = EMAIL.sub('user@example.com', text)
        return text
    if isinstance(value, dict):
        return {k: scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def build_aliases(client, username, host):
    """The identifiers this site would otherwise publish.

    Cluster names come from the platform rather than a list kept here, so a
    site with differently named clusters is covered without editing anything.
    """
    aliases = {}
    try:
        for index, cluster in enumerate(client.clusters.list(), start=1):
            name = dict(cluster).get('name')
            if name:
                aliases[str(name)] = 'cluster%d' % index
    except Exception as exc:                          # noqa: BLE001
        print('could not read cluster names to alias them (%s)' % exc)
    # Every account, not just the one connecting. A capture of group
    # membership is exactly a list of other people's usernames, and the
    # connecting user is rarely the one in it.
    try:
        for index, user in enumerate(client.users.list(), start=1):
            name = dict(user).get('name')
            if name:
                aliases[str(name)] = 'user%d' % index
    except Exception as exc:                          # noqa: BLE001
        print('could not read user names to alias them (%s)' % exc)
    if username:
        aliases[str(username)] = 'operator'
    # The bare host, and its first label if it is a name rather than an
    # address -- 'lab.example.com' leaks as much as 'lab'.
    if host:
        aliases[str(host)] = 'vergeos.example.com'
        label = str(host).split('.', maxsplit=1)[0]
        if label and not label.replace('.', '').isdigit() and len(label) > 3:
            aliases[label] = 'vergeos'
    return aliases


def leaks(text):
    """Anything in a finished capture that must not be published."""
    found = sorted(set(PRIVATE.findall(text)) | set(HOME_PATH.findall(text)))

    addresses = EMAIL_STRICT.findall(text)
    found += sorted({m for m in addresses if not m.endswith('@example.com')})
    for real in ALIASES:
        if real and re.search(re.escape(real), text, flags=re.IGNORECASE):
            found.append('<site identifier>')
    return sorted(set(found))


FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'fixtures', 'api')


def build(client):
    """name -> (call description, callable returning rows)."""
    def listing(manager, limit=None):
        return lambda: [dict(r) for r in manager.list()][:limit]

    # Only the tables a unit test reads. Running this script should leave the
    # working tree either unchanged or changed for a reason, so it does not
    # capture things nothing consumes.
    #
    # Deliberately absent: networks, which is a list of the capture system's
    # subnets and gateways and is refused by the leak check below anyway; and
    # users and groups, which are a list of who works there. Neither is
    # needed to pin a field name.
    return {
        'nodes': ('client.nodes.list()', listing(client.nodes)),
        'clusters': ('client.clusters.list()', listing(client.clusters)),
        'physical_drives': ('client.physical_drives.list()',
                            listing(client.physical_drives)),
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

    ALIASES.update(build_aliases(client, os.environ['VERGEOS_USERNAME'],
                                 os.environ['VERGEOS_HOST']))
    print('aliasing %d site identifier(s) -> %s'
          % (len(ALIASES), ', '.join(sorted(set(ALIASES.values())))))

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
        # The placeholders, not the values they replaced. Recorded so a
        # capture made by an older script -- one that aliased nothing -- is
        # distinguishable from one that had nothing to alias.
        'identities_substituted': sorted(set(ALIASES.values())),
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
        text = json.dumps(doc, indent=1, sort_keys=True, default=str) + '\n'

        # Checked after scrubbing rather than trusted to it. Field-name
        # redaction covers the fields somebody thought of; this covers the
        # ones nobody did, and refuses to write the file rather than leaving
        # the discovery to a reviewer's grep.
        found = leaks(text)
        if found:
            print('%-18s NOT WRITTEN -- would publish %s'
                  % (name, ', '.join(found)))
            continue

        with open(os.path.join(FIXTURE_DIR, '%s.json' % name), 'w') as handle:
            handle.write(text)
        keys = sorted({k for r in data for k in r})
        print('%-18s %2d rows %3d fields  <- %s' % (name, len(data), len(keys), call))
        if not data:
            print('%-18s    ^ nothing of this kind exists on the capture system;'
                  ' row() will refuse to build on it' % '')


if __name__ == '__main__':
    main()
