#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: file
short_description: Upload and remove files in VergeOS
version_added: "2.2.0"
description:
  - Upload local files (OVA, ISO, raw disk images, and other content) to the
    VergeOS media catalog, and remove files from it.
  - Uploads are idempotent by name and size - a file that already exists with
    the same name and byte size is not re-uploaded.
  - O(description) and O(tier) are corrected in place on an existing file,
    without moving the bytes again.
  - Complements M(vergeio.vergeos.file_info), which queries the catalog.
options:
  name:
    description:
      - Name of the file in VergeOS.
      - Defaults to the basename of I(src) when uploading.
      - Required when I(state=absent).
    type: str
  src:
    description:
      - Local path of the file to upload.
      - Required when I(state=present).
    type: path
  description:
    description:
      - Optional description stored with the file.
      - Drift on an existing file is corrected in place; the bytes are not
        re-uploaded for a text change.
    type: str
  tier:
    description:
      - Preferred storage tier for the file (1-5).
      - Drift on an existing file is corrected in place. This is a
        preference, and VergeOS does not check that the tier exists.
    type: int
    choices: [ 1, 2, 3, 4, 5 ]
  force:
    description:
      - Re-upload even if a file with the same name and size already exists.
      - Size comparison cannot detect same-size content changes; use
        I(force=true) to guarantee the catalog copy matches I(src).
    type: bool
    default: false
  state:
    description:
      - C(present) ensures the file exists in the catalog (uploading or
        replacing as needed).
      - C(absent) ensures no file with I(name) exists.
    type: str
    choices: [ present, absent ]
    default: present
notes:
  - B(Replacing a file is not atomic.) File names carry a unique constraint,
    so the catalog cannot hold the old and new copy at once - the module has
    to delete the existing entry before uploading the replacement. If the
    upload then fails, the old copy is already gone. Replacement only happens
    when the size differs or O(force=true); an unchanged file is never at
    risk.
  - Supports C(check_mode).
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Upload an OVA to the media catalog
  vergeio.vergeos.file:
    src: /var/images/appliance.ova

- name: Upload an ISO under a different name, pinned to tier 1
  vergeio.vergeos.file:
    src: /var/images/ubuntu-24.04.iso
    name: ubuntu-lts.iso
    tier: 1
    description: "Ubuntu LTS installer"

- name: Remove a file from the catalog
  vergeio.vergeos.file:
    name: ubuntu-lts.iso
    state: absent
'''

RETURN = r'''
file:
  description: The catalog entry after the operation
  returned: when state is present
  type: dict
  sample:
    $key: 41
    name: "appliance.ova"
    size_bytes: 1073741824
msg:
  description: Human-readable summary of what happened
  returned: always
  type: str
  sample: "uploaded 'appliance.ova' (1073741824 bytes)"
'''

import os
import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


# Module parameter -> raw API column on `files`, checked against a live row on
# VergeOS 26.1.8 (19 columns) before anything else here was touched. One
# rename, and it is issue #8's for the third time on a third table:
#
#   tier -> preferred_tier, and the column holds the STRING '1', not 1.
UPDATE_FIELD_MAP = {
    'description': 'description',
    'tier': 'preferred_tier',
}

# A file's name is its identity -- the catalog carries a unique constraint on
# it -- and the bytes arrive by upload rather than as a field.
CREATE_PARAM_MAP = dict(UPDATE_FIELD_MAP, name='name')
IDENTITY_PARAMS = ('name',)

COMPARISON_FIELDS = tuple(sorted(set(UPDATE_FIELD_MAP.values())))

# Named rather than inherited. `filesize` is what idempotence turns on, so a
# projection that dropped it would re-upload the whole catalogue on every run.
# allocated_bytes is here for the settle window described at size_matches().
FILE_FIELDS = (['$key', 'name', 'filesize', 'allocated_bytes']
               + list(COMPARISON_FIELDS))


def find_file(client, name):
    """The catalog entry with this exact name, or None.

    Matched client-side, and NOT through ``resolve_one``: the usual reason for
    that helper is that VergeOS does not enforce unique names (#72/#85), and
    here it does -- a second upload under an existing name is refused with
    ``Validation error: unique constraint``. There is no ambiguity to refuse.
    Client-side matching is what this path uses. pyVergeOS#100
    (``quote_value()`` did not escape ``{``) is fixed in the 1.2.8 floor;
    the lookup was not switched to a server-side filter.
    """
    for existing in client.files.list(fields=FILE_FIELDS):
        if dict(existing).get('name') == name:
            return existing
    return None


def read_file_row(client, key):
    """Re-read a file by key.

    ``upload()`` returns what the create call answered with, which carries
    almost nothing -- name, description, filesize and tier all read as None.
    Returning that to the user as ``file`` reports an empty catalog entry for
    a successful upload.
    """
    return client._request('GET', 'files/%s' % key,
                           params={'fields': ','.join(FILE_FIELDS)})


# `filesize` settles asynchronously after an upload. Measured on 26.1.8: an
# 8388608-byte file reads 8126464 for the first four seconds and only then
# reaches its true size. `allocated_bytes` is correct immediately.
#
# That four-second window is not cosmetic. Idempotence here turns entirely on
# the size comparison, so a second run inside the window concludes the catalog
# copy is the wrong size, DELETES it, and uploads the whole thing again -- for
# an ISO that is gigabytes of transfer and a gap in which the file does not
# exist. Re-reading before deciding costs nothing on the path that matters,
# because it only runs when the sizes disagree.
SIZE_SETTLE_SECONDS = 20
SIZE_POLL_INTERVAL = 2


def size_matches(client, row, src_size):
    """Whether the catalog copy is src_size bytes, allowing for the settle.

    Returns the possibly re-read row alongside the verdict, so a caller that
    waited does not then report the stale number it started with.
    """
    if int(row.get('filesize') or 0) == src_size:
        return True, row

    # allocated_bytes is right immediately, so an exact match there settles it
    # without waiting at all. It is not enough on its own -- allocation rounds
    # up -- which is why a mismatch falls through to the poll rather than to a
    # verdict.
    if int(row.get('allocated_bytes') or 0) == src_size:
        return True, row

    deadline = time.time() + SIZE_SETTLE_SECONDS
    while time.time() < deadline:
        time.sleep(SIZE_POLL_INTERVAL)
        row = read_file_row(client, row['$key'])
        if int(row.get('filesize') or 0) == src_size:
            return True, row
    return False, row


def metadata_changes(module, row):
    """Description and tier drift, as raw columns.

    These are not worth moving gigabytes for, and they do not have to be:
    ``files`` accepts a PUT on the row even though pyvergeos models no
    ``update()`` for it. Without this the two options were accepted, stored at
    upload time, and then silently ignored forever -- #18's shape.
    """
    changes = {}
    for param, column in UPDATE_FIELD_MAP.items():
        want = module.params.get(param)
        if want is None:
            continue
        current = row.get(column)
        if param == 'tier':
            # preferred_tier is stored as a string; compared as an int and
            # written back as a string, exactly like drive and nas_volume.
            if current not in (None, '') and int(current) == want:
                continue
            changes[column] = str(want)
            continue
        if (current or '') == want:
            continue
        changes[column] = want
    return changes


def ensure_present(module, client):
    src = module.params['src']
    name = module.params.get('name') or os.path.basename(src)

    if not os.path.isfile(src):
        module.fail_json(msg=f"src '{src}' does not exist or is not a file")

    src_size = os.path.getsize(src)
    existing = find_file(client, name)

    if existing is not None:
        row = dict(existing)
        key = row['$key']
        if module.params['force']:
            same_size = False
        else:
            same_size, row = size_matches(client, row, src_size)

        if same_size:
            # The bytes are right. Metadata may still have drifted, and
            # correcting it does not require moving them again.
            changes = metadata_changes(module, row)
            if not changes:
                module.exit_json(
                    changed=False,
                    file=row,
                    msg=f"'{name}' already exists with the same size "
                        f"({src_size} bytes)")
            if module.check_mode:
                module.exit_json(
                    changed=True,
                    file=row,
                    msg=f"would update {', '.join(sorted(changes))} "
                        f"on '{name}'")
            client._request('PUT', 'files/%s' % key, json_data=changes)
            module.exit_json(
                changed=True,
                file=read_file_row(client, key),
                msg=f"updated {', '.join(sorted(changes))} on '{name}'")

        if module.check_mode:
            module.exit_json(
                changed=True,
                file=row,
                msg=f"would replace '{name}' "
                    f"({row.get('filesize')} -> {src_size} bytes)")
        # File names carry a unique constraint, so the catalog cannot hold
        # both copies at once and the stale entry has to go first. If the
        # upload below fails, the old copy is already gone -- said plainly in
        # the module's notes rather than left for someone to discover.
        existing.delete()
    elif module.check_mode:
        module.exit_json(
            changed=True,
            msg=f"would upload '{name}' ({src_size} bytes)"
        )

    uploaded = client.files.upload(
        src,
        name=name,
        description=module.params.get('description'),
        tier=module.params.get('tier'),
    )
    module.exit_json(
        changed=True,
        file=read_file_row(client, dict(uploaded)['$key']),
        msg=f"uploaded '{name}' ({src_size} bytes)"
    )


def ensure_absent(module, client):
    name = module.params.get('name')
    if not name:
        module.fail_json(msg="name is required when state=absent")

    existing = find_file(client, name)
    if existing is None:
        module.exit_json(changed=False, msg=f"'{name}' does not exist")

    if module.check_mode:
        module.exit_json(changed=True, msg=f"would delete '{name}'")

    existing.delete()
    module.exit_json(changed=True, msg=f"deleted '{name}'")


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
        src=dict(type='path'),
        description=dict(type='str'),
        tier=dict(type='int', choices=[1, 2, 3, 4, 5]),
        force=dict(type='bool', default=False),
        state=dict(type='str', choices=['present', 'absent'], default='present'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        required_if=[
            ('state', 'present', ('src',)),
            ('state', 'absent', ('name',)),
        ],
    )

    client = get_vergeos_client(module)

    try:
        if module.params['state'] == 'present':
            ensure_present(module, client)
        else:
            ensure_absent(module, client)
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
