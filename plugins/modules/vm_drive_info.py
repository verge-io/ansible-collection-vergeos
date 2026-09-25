#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vm_drive_info
short_description: Gather information about a VM's drives in VergeOS
version_added: "2.1.0"
description:
  - Gather facts about the drives attached to a virtual machine, including
    each drive's media type and import status.
  - Optionally include per-drive IO counters, which is how you tell a guest
    that is actually running from a VM that merely reports C(running).
options:
  vm:
    description:
      - Name of the VM whose drives to report.
    type: str
    required: true
  media:
    description:
      - Only report drives of this media type.
      - C(import) is a drive the platform has not finished building; it
        becomes C(disk) when the import completes.
    type: str
    choices: [ disk, cdrom, efidisk, import ]
  stats:
    description:
      - Also report each drive's IO counters.
      - Counters are read through a filter on the drive, never by row
        position; see the note below.
    type: bool
    default: false
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Every counter on a freshly deployed VM is exactly C(0) before its first
    power-on, so an absolute value is meaningful and no baseline has to be
    captured first.
  - When judging whether a guest booted, use C(write_bytes). On a VM that
    never boots, the firmware still reads the boot sector and sends a few
    DHCP packets, so C(read_bytes) and the NIC's transmit counters both move
    off zero on a guest that is sitting at "no bootable device".
      Measured on a 26.1.8 system, a booted Ubuntu guest against an
    identically configured VM with a blank drive, four minutes after power-on
    - C(write_bytes) 419 MB versus C(0), C(read_bytes) 410 MB versus C(512).
      Only C(write_bytes) separates the two, and it separates by three orders
    of magnitude.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Report a VM's drives
  vergeio.vergeos.vm_drive_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    vm: "web-01"
  register: drives

- name: Fail if the recipe built a VM with no drives
  ansible.builtin.assert:
    that: drives.drives | length > 0
    fail_msg: "web-01 has no drives, so a recipe step failed. It cannot boot."

- name: Wait for a cloud image to finish importing
  vergeio.vergeos.vm_drive_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    vm: "web-01"
  register: importing
  until:
    - importing.drives | length > 0
    - importing.importing | length == 0 or importing.failed_drives | length > 0
  retries: 180
  delay: 10

- name: A failed import is not a slow download
  ansible.builtin.assert:
    that: importing.failed_drives | length == 0
    fail_msg: >-
      {{ importing.failed_drives | map(attribute='name') | list }} failed.
      The platform reports
      {{ importing.failed_drives | map(attribute='status_info') | list }}.

- name: Prove the guest is writing to disk
  vergeio.vergeos.vm_drive_info:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    vm: "web-01"
    media: disk
    stats: true
  register: booted
  until: booted.drives | selectattr('write_bytes', 'gt', 0) | list | length > 0
  retries: 60
  delay: 10
'''

RETURN = r'''
drives:
  description:
    - The VM's drives, ordered as the UI orders them.
    - When O(stats) is true each drive also carries RV(drives[].write_bytes),
      RV(drives[].read_bytes), C(writes) and C(reads); a drive with no stats
      row reports zeroes rather than being omitted.
  returned: always
  type: list
  elements: dict
  contains:
    name:
      description: Drive name.
      type: str
      returned: always
    media:
      description:
        - Media type. C(import) means the platform is still building the
          drive.
      type: str
      returned: always
    status:
      description: Drive status.
      type: str
      returned: always
    status_info:
      description:
        - Free text from the platform. During an import this carries the
          source URL and a live percentage.
      type: str
      returned: always
    write_bytes:
      description: Bytes written to this drive.
      type: int
      returned: when O(stats) is true
    read_bytes:
      description: Bytes read from this drive.
      type: int
      returned: when O(stats) is true
  sample:
    - name: "OS"
      media: "disk"
      status: "online"
      disksize: 53687091200
importing:
  description:
    - The subset of RV(drives) the platform has not finished building.
    - A drive can carry C(media=import) before its status becomes
      C(importing), so both signals are folded in here.
    - A drive in a terminal failure state is excluded. It is finished, and
      it failed; see RV(failed_drives).
  returned: always
  type: list
  elements: dict
failed_drives:
  description:
    - Drives the platform has given up on.
    - C(status=errors) is the terminal state of a failed import, measured
      on 26.1.8. The row keeps C(media=import) and is never retried, so
      waiting on it cannot succeed. RV(drives[].status_info) carries the
      platform's reason.
    - Named C(failed_drives) rather than C(failed). Ansible treats a truthy
      C(failed) result as a module failure, and C(failed_when) overwrites
      that key, which would hide the list from a wait that needs it.
  returned: always
  type: list
  elements: dict
  version_added: "2.2.0"
machine:
  description: Key of the VM's machine.
  returned: always
  type: str
  sample: "18"
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    resolve_one,
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.machine import (
    drive_write_stats,
    vm_drives,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )

STAT_KEYS = ('read_bytes', 'write_bytes', 'reads', 'writes')

# Terminal drive statuses. Confirmed on VergeOS 26.1.8: a cloud-image import
# the platform cannot download is marked 'errors' within about two seconds,
# keeps media=import, and is never retried. The row read every 30s for twenty
# minutes did not change. No other terminal VM-drive status was confirmed, so
# none is listed. 'offline' in particular is not a failure.
TERMINAL_DRIVE_STATUSES = frozenset(('errors',))


def failed_drives(drives):
    """Drives the platform has given up on.

    These are finished. Leaving them in `importing` makes a caller wait out a
    timeout that cannot help, then advise raising it.
    """
    return [d for d in drives if d.get('status') in TERMINAL_DRIVE_STATUSES]


def unfinished(drives):
    """Drives the platform has not finished building.

    media=import is the drive that is not yet usable; status=importing is the
    moment it is actively pulling. A drive can be the first without yet being
    the second, and that window is exactly what makes "wait for the absence of
    importing" exit before the download has started.

    A terminal failure is neither. status=errors keeps media=import, so folding
    media in without looking at status counts a drive the platform has already
    abandoned as still in progress.
    """
    out = []
    for drive in drives:
        if drive.get('status') in TERMINAL_DRIVE_STATUSES:
            continue
        if drive.get('media') == 'import' or drive.get('status') == 'importing':
            out.append(drive)
    return out


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        vm=dict(type='str', required=True),
        media=dict(type='str', choices=['disk', 'cdrom', 'efidisk', 'import']),
        stats=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    params = module.params
    client = get_vergeos_client(module)

    try:
        vm = resolve_one(module, client.vms, params['vm'], 'VM')
        drives = vm_drives(vm, media=params.get('media'))

        if params['stats']:
            by_drive = drive_write_stats(
                client, [d.get('$key') for d in drives])
            for drive in drives:
                row = by_drive.get(str(drive.get('$key'))) or {}
                for key in STAT_KEYS:
                    drive[key] = int(row.get(key) or 0)

        module.exit_json(
            changed=False,
            drives=drives,
            importing=unfinished(drives),
            failed_drives=failed_drives(drives),
            machine=str(dict(vm).get('machine') or ''),
        )

    except NotFoundError as e:
        module.fail_json(msg=f"VM '{params['vm']}' not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
