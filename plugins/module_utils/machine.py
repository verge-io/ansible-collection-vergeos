# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""pyvergeos glue for reading a VM's machine-level hardware.

A VM's drives and NICs come from public SDK managers scoped to the VM. Their
per-drive IO counters do too, as of pyvergeos 1.5.0: ``machine_drive_stats``
and the scoped ``drive.drive_stats`` accessor (pyVergeOS#128).
drive_write_stats() still reads the table with ``client._request()``.
The manager exists from pyvergeos 1.5.0; the floor (published as 1.6.1)
includes it. This path was not switched onto the manager.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

# status_info is not in the SDK's default drive projection, and it is the field
# that carries an import's live progress -- e.g. "Downloading '...qcow2'
# (73% - 257MB / 348MB)". Without it an operator cannot tell 2% from 99%, or a
# slow mirror from a dead one.
DRIVE_FIELDS = [
    '$key', 'name', 'orderid', 'interface', 'media', 'enabled', 'disksize',
    'used_bytes', 'preferred_tier', 'machine',
    'status#status as status',
    'status#status_info as status_info',
    'media_source#name as media_file',
]

NIC_FIELDS = [
    '$key', 'name', 'orderid', 'interface', 'enabled', 'macaddress',
    'ipaddress', 'vnet', 'machine',
    'status#status as status',
    'vnet#name as vnet_name',
]

STATS_FIELDS = 'parent_drive,read_bytes,write_bytes,reads,writes'


def vm_drives(vm, media=None):
    """A VM's drives, ordered as the UI orders them."""
    return [dict(d) for d in vm.drives.list(fields=DRIVE_FIELDS, media=media)]


def vm_nics(vm):
    """A VM's NICs."""
    return [dict(n) for n in vm.nics.list(fields=NIC_FIELDS)]


def drive_write_stats(client, drive_keys):
    """IO counters for the given drives, keyed by drive key (as a string).

    The stats row is addressed by a FILTER on parent_drive, never by path key.
    ``machine_drive_stats/<n>`` resolves to the row whose OWN $key is n, and
    that row belongs to a different drive -- measured on a 26.1.8 system, $key
    34 carried parent_drive 39. A counter read the obvious way therefore
    reports another VM's IO, and reports it as success: the defect surfaced as
    809 MB of writes on a VM that had never been powered on. The same aliasing
    applies to machine_nic_stats/parent_nic.

    pyvergeos 1.5.0 added a public manager for this table
    (``client.machine_drive_stats``, and ``drive.drive_stats`` on a drive;
    pyVergeOS#128). The read stays on ``_request`` because the collection
    floor is pyvergeos>=1.2.7, where that manager does not exist. A later
    switch must keep the parent_drive filter: path-key access addresses the
    stats row's own $key, not the drive.
    """
    keys = [k for k in (drive_keys or []) if k not in (None, '')]
    if not keys:
        return {}

    rows = client._request('GET', 'machine_drive_stats', params={
        'fields': STATS_FIELDS,
        'filter': " or ".join("parent_drive eq %s" % k for k in keys),
    })
    # A filter matching nothing returns {"$count": 0}, not [].
    if not isinstance(rows, list):
        return {}

    return {str(row.get('parent_drive')): row for row in rows
            if row.get('parent_drive') is not None}
