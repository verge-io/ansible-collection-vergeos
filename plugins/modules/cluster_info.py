#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: cluster_info
short_description: Gather information about clusters in VergeOS
version_added: "1.0.0"
description:
  - Gather facts about clusters in VergeOS.
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO
'''

EXAMPLES = r'''
- name: Get information about all clusters
  vergeio.vergeos.cluster_info:
    host: "192.168.1.100"
    username: "admin"
    password: "password"
  register: cluster_info
'''

RETURN = r'''
clusters:
  description: List of clusters
  returned: always
  type: list
  elements: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    VergeOSAPI,
    VergeOSAPIError,
    vergeos_argument_spec
)


def main():
    module = AnsibleModule(
        argument_spec=vergeos_argument_spec(),
        supports_check_mode=True
    )

    api = VergeOSAPI(module)

    try:
        clusters = api.get('clusters')
        module.exit_json(changed=False, clusters=clusters)
    except VergeOSAPIError as e:
        module.fail_json(msg=str(e))
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
