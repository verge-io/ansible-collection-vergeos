#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: file_info
short_description: Gather information about files in VergeOS
version_added: "1.0.0"
description:
  - Retrieve information about files stored in VergeOS.
  - Files include OVA files, ISO images, raw disk files, and other uploaded content.
  - Can query all files or filter by name or type.
options:
  name:
    description:
      - Filter files by exact name match.
      - If specified, only files with this exact name will be returned.
    type: str
  file_type:
    description:
      - Filter files by type.
      - Common types include C(ova), C(iso), C(raw), C(dir).
    type: str
    choices: [ ova, iso, raw, dir, qcow2 ]
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO
'''

EXAMPLES = r'''
- name: List all files
  vergeio.vergeos.file_info:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
  register: all_files

- name: Find a specific OVA file by name
  vergeio.vergeos.file_info:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    name: "rhel8.ova"
  register: rhel_ova

- name: List all OVA files
  vergeio.vergeos.file_info:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
    file_type: ova
  register: ova_files

- name: Display file information
  ansible.builtin.debug:
    msg: "File {{ item.name }} has ID {{ item['$key'] }}"
  loop: "{{ all_files.files }}"
'''

RETURN = r'''
files:
  description: List of files matching the query
  returned: always
  type: list
  elements: dict
  sample:
    - $key: 41
      name: "rhel8.ova"
      type: "ova"
      creator: "admin"
    - $key: 42
      name: "ubuntu.iso"
      type: "iso"
      creator: "admin"
count:
  description: Number of files found
  returned: always
  type: int
  sample: 2
'''

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


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
        file_type=dict(type='str', choices=['ova', 'iso', 'raw', 'dir', 'qcow2']),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    client = get_vergeos_client(module)
    name_filter = module.params.get('name')
    type_filter = module.params.get('file_type')

    try:
        # Get all files from VergeOS
        files = [dict(f) for f in client.files.list()]

        # Apply filters
        if name_filter:
            files = [f for f in files if f.get('name') == name_filter]

        if type_filter:
            files = [f for f in files if f.get('type') == type_filter]

        module.exit_json(
            changed=False,
            files=files,
            count=len(files)
        )

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
