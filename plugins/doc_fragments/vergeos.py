#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

from __future__ import absolute_import, division, print_function
__metaclass__ = type


class ModuleDocFragment(object):
    # Standard VergeOS documentation fragment
    DOCUMENTATION = r'''
options:
  host:
    description:
      - The hostname or IP address of the VergeOS system or tenant.
    type: str
    required: true
    env:
      - name: VERGEOS_HOST
  username:
    description:
      - The username for authenticating with VergeOS.
    type: str
    required: true
    env:
      - name: VERGEOS_USERNAME
  password:
    description:
      - The password for authenticating with VergeOS.
    type: str
    required: true
    no_log: true
    env:
      - name: VERGEOS_PASSWORD
  insecure:
    description:
      - If set to C(true), SSL certificates will not be validated.
      - This should only be used on personally controlled sites using self-signed certificates.
    type: bool
    default: false
    env:
      - name: VERGEOS_INSECURE
notes:
  - Supports C(check_mode).
'''
