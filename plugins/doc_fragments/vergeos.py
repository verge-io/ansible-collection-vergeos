# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type


class ModuleDocFragment(object):
    # Standard VergeOS documentation fragment
    DOCUMENTATION = r'''
options:
  host:
    description:
      - The hostname or IP address of the VergeOS system or tenant.
      - Can also be set via the C(VERGEOS_HOST) environment variable.
    type: str
    required: true
  username:
    description:
      - The username for authenticating with VergeOS.
      - Can also be set via the C(VERGEOS_USERNAME) environment variable.
    type: str
    required: true
  password:
    description:
      - The password for authenticating with VergeOS.
      - Can also be set via the C(VERGEOS_PASSWORD) environment variable.
    type: str
    required: true
  insecure:
    description:
      - If set to C(true), SSL certificates will not be validated.
      - This should only be used on personally controlled sites using self-signed certificates.
      - Can also be set via the C(VERGEOS_INSECURE) environment variable.
    type: bool
    default: false
notes:
  - Supports C(check_mode).
'''
