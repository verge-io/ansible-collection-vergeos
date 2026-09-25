# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""A read-only picture of one VergeOS site, so a playbook can adapt to it.

``cluster_info`` answers cluster capacity and whether a node can be drained.
This answers the question that comes first: what does this system have.

Storage tiers are the ``tier`` column on ``client.storage_tiers``. That table
has a row for a tier whether or not any drive is assigned to it.
``physical_drive_info`` reports ``vsan_tier`` on each drive, which only sees
tiers that already have a drive and needs drive-level access.

Field names were measured on VergeOS 26.1.8 with pyvergeos 1.6.1 (the release
``pyvergeos>=1.2.8`` installs):

  client.os_version          OS version, e.g. 26.1.8
  client.version             yb_version
  client.cloud_name          cloud / site name
  storage_tiers.tier         the tier number a drive or tenant asks for
  storage_tiers.$key         the row key; on a one-tier system it matches
                             ``tier``, and it is still a different column
  storage_tiers.capacity     bytes
  storage_tiers.used         bytes
  nodes.list()               ram, vm_ram, ram_used, cores, running, physical
  cluster_status             online_ram, used_ram

``ram`` is physical RAM. ``vm_ram`` is what the platform makes available to
VMs. ``ram_used`` is physical RAM in use (``machine#stats#ram_used``). Those
three are different numbers. ``ram_headroom_mb`` is ``online_ram - used_ram``
summed over ``cluster_status``, the same arithmetic ``cluster_info`` returns.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

from ansible_collections.vergeio.vergeos.plugins.module_utils.clusters import (
    fetch_cluster_status,
)


def _as_dict(row):
    if row is None:
        return {}
    return dict(row)


def _optional_int(value):
    """An int, or None when the row did not report the field.

    A missing figure is left as None. Turning it into 0 would let a playbook
    size a VM against a capacity the system never stated.
    """
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_attr(obj, name):
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if isinstance(value, str) and value:
        return value
    return None


def read_identity(client):
    """OS version, yb version, and cloud name.

    The public properties exist on pyvergeos 1.6.1. When one of them is empty,
    the same value is read from the connection under the name the property
    itself uses (``version`` is ``vergeos_version`` on the connection).
    """
    connection = getattr(client, '_connection', None)
    return {
        'os_version': (_str_attr(client, 'os_version')
                       or _str_attr(connection, 'os_version')),
        'version': (_str_attr(client, 'version')
                    or _str_attr(connection, 'vergeos_version')),
        'cloud_name': (_str_attr(client, 'cloud_name')
                       or _str_attr(connection, 'cloud_name')),
    }


def storage_tier_report(rows):
    """Tier numbers, plus the capacity columns from the same rows.

    ``storage_tiers`` is the sorted unique ``tier`` column, which is what a
    drive, a recipe, or a tenant storage allocation asks for. ``$key`` is
    preserved on the detail rows and is never substituted for ``tier``.
    """
    details = []
    for row in rows or []:
        row = _as_dict(row)
        number = _optional_int(row.get('tier'))
        if number is None:
            continue
        details.append({
            'tier': number,
            '$key': row.get('$key'),
            'description': row.get('description') or '',
            'capacity': _optional_int(row.get('capacity')),
            'used': _optional_int(row.get('used')),
        })
    details.sort(key=lambda item: (item['tier'], str(item.get('$key'))))
    numbers = sorted({item['tier'] for item in details})
    return {
        'storage_tiers': numbers,
        'storage_tier_details': details,
    }


def shape_node(row):
    """The capacity fields a sizing decision reads, and nothing else.

    The default ``nodes.list()`` row also carries hardware identity and an
    out-of-band address. Those stay out of this report.
    """
    row = _as_dict(row)
    running = row.get('running')
    if running is None and row.get('is_online') is not None:
        online = bool(row.get('is_online'))
    elif running is None:
        online = None
    else:
        online = bool(running)

    if 'physical' not in row or row.get('physical') is None:
        physical = None
    else:
        physical = bool(row.get('physical'))

    return {
        'name': row.get('name'),
        '$key': row.get('$key'),
        'cluster': row.get('cluster'),
        'cluster_name': row.get('cluster_name') or None,
        'ram': _optional_int(row.get('ram')),
        'vm_ram': _optional_int(row.get('vm_ram')),
        'ram_used': _optional_int(row.get('ram_used')),
        'cores': _optional_int(row.get('cores')),
        'physical': physical,
        'online': online,
        'maintenance': bool(row.get('maintenance')),
    }


def shape_nodes(rows):
    nodes = [shape_node(row) for row in rows or []]
    nodes.sort(key=lambda node: (node.get('name') is None,
                                 node.get('name') or ''))
    return nodes


def _prefer_int(primary, fallback, name):
    """``primary`` when it reported ``name``, otherwise ``fallback``."""
    if name in primary and primary.get(name) is not None:
        return _optional_int(primary.get(name))
    return _optional_int(fallback.get(name))


def shape_clusters(cluster_rows, status_rows):
    """One row per cluster, with live capacity from ``cluster_status``.

    ``cluster_status.cluster`` is the cluster ``$key``. Live figures win when
    that row is present. The clusters table on 26.1.8 carries the same
    columns, and those are used when status has no row for the cluster.
    """
    by_key = {}
    for row in status_rows or []:
        row = _as_dict(row)
        key = row.get('cluster')
        if key is not None:
            by_key[key] = row

    shaped = []
    for row in cluster_rows or []:
        row = _as_dict(row)
        key = row.get('$key')
        status = by_key.get(key, {})
        online_ram = _prefer_int(status, row, 'online_ram')
        used_ram = _prefer_int(status, row, 'used_ram')
        if online_ram is None and used_ram is None:
            headroom = None
        else:
            headroom = (online_ram or 0) - (used_ram or 0)
        shaped.append({
            'name': row.get('name'),
            '$key': key,
            'online_nodes': _prefer_int(status, row, 'online_nodes'),
            'total_nodes': _prefer_int(status, row, 'total_nodes'),
            'online_ram': online_ram,
            'used_ram': used_ram,
            'ram_headroom_mb': headroom,
            'online_cores': _prefer_int(status, row, 'online_cores'),
            'used_cores': _prefer_int(status, row, 'used_cores'),
        })
    shaped.sort(key=lambda item: (item.get('name') is None,
                                  item.get('name') or ''))
    return shaped


def ram_headroom_mb(status_rows):
    """Uncommitted VM RAM across online nodes, summed over every cluster.

    Identical to the figure ``cluster_info`` returns as ``ram_headroom_mb``:
    ``online_ram - used_ram`` on each ``cluster_status`` row. A missing
    figure contributes 0, matching that module.
    """
    total = 0
    for row in status_rows or []:
        row = _as_dict(row)
        total += int(row.get('online_ram') or 0) - int(row.get('used_ram') or 0)
    return total


def _vm_capacity(node):
    """VM RAM when the node reported it, otherwise physical RAM."""
    if node.get('vm_ram') is not None:
        return node.get('vm_ram')
    return node.get('ram')


def largest_node_vm_ram_mb(nodes):
    """``vm_ram`` of the largest online physical node.

    A new machine has to fit on one node. This is that ceiling, in MB.
    Maintenance nodes are excluded. When no online node is marked physical,
    the largest online node is used. None when no online node reported RAM.
    """
    online = [node for node in nodes or []
              if node.get('online') is True and not node.get('maintenance')]
    physical = [node for node in online if node.get('physical') is True]
    pool = physical or online
    values = [value for value in (_vm_capacity(node) for node in pool)
              if value is not None]
    if not values:
        return None
    return max(values)


def census(rows):
    """Row count, and the names those rows carry, sorted.

    The count is the number of rows. A row with no name is counted and
    omitted from ``names``. Duplicate names are kept, because VergeOS does
    not enforce unique names on these tables.
    """
    items = [_as_dict(row) for row in rows or []]
    names = sorted(str(row['name']) for row in items
                   if row.get('name') not in (None, ''))
    return {'count': len(items), 'names': names}


def gather_site(client):
    """The site report. Read-only. Every list is the SDK's own manager."""
    tiers = storage_tier_report(client.storage_tiers.list())
    status = fetch_cluster_status(client)
    nodes = shape_nodes(client.nodes.list())
    networks = census(client.networks.list())
    recipes = census(client.vm_recipes.list())
    services = census(client.nas_services.list())
    report = read_identity(client)
    report.update(tiers)
    report.update(
        clusters=shape_clusters(client.clusters.list(), status),
        nodes=nodes,
        ram_headroom_mb=ram_headroom_mb(status),
        largest_node_vm_ram_mb=largest_node_vm_ram_mb(nodes),
        counts={
            'networks': networks['count'],
            'vm_recipes': recipes['count'],
            'nas_services': services['count'],
        },
        names={
            'networks': networks['names'],
            'vm_recipes': recipes['names'],
            'nas_services': services['names'],
        },
    )
    return report
