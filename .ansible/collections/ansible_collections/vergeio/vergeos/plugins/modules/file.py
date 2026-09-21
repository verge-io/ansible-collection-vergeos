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
version_added: "2.1.0"
description:
  - Upload local files (OVA, ISO, raw disk images, and other content) to the
    VergeOS media catalog, and remove files from it.
  - Uploads are idempotent by name and size - a file that already exists with
    the same name and byte size is not re-uploaded.
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
      - Only applied at upload time; an existing file's description is not
        updated.
    type: str
  tier:
    description:
      - Preferred storage tier for the file (1-5).
      - Only applied at upload time.
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
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Upload an OVA to the media catalog
  vergeio.vergeos.file:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    src: /var/images/appliance.ova

- name: Upload an ISO under a different name, pinned to tier 1
  vergeio.vergeos.file:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    src: /var/images/ubuntu-24.04.iso
    name: ubuntu-lts.iso
    tier: 1
    description: "Ubuntu LTS installer"

- name: Remove a file from the catalog
  vergeio.vergeos.file:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
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


def find_file(client, name):
    """Find a catalog file by exact name, or None."""
    for existing in client.files.list():
        if existing.name == name:
            return existing
    return None


def ensure_present(module, client):
    src = module.params['src']
    name = module.params.get('name') or os.path.basename(src)

    if not os.path.isfile(src):
        module.fail_json(msg=f"src '{src}' does not exist or is not a file")

    src_size = os.path.getsize(src)
    existing = find_file(client, name)

    if existing is not None:
        # size_bytes is the File model property over the raw filesize field
        if existing.size_bytes == src_size and not module.params['force']:
            module.exit_json(
                changed=False,
                file=dict(existing),
                msg=f"'{name}' already exists with the same size ({src_size} bytes)"
            )

        if module.check_mode:
            module.exit_json(
                changed=True,
                file=dict(existing),
                msg=f"would replace '{name}' ({existing.size_bytes} -> {src_size} bytes)"
            )
        # The catalog has no in-place replace; remove the stale entry first
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
        file=dict(uploaded),
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
