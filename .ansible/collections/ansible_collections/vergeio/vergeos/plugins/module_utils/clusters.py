# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Cluster capacity, and whether a node can be drained.

This exists because of a measured failure. Draining node2 on a two-node lab
set ``maintenance=True`` in under a second while 32 GB was still resident,
then sat at ``status='migrating'`` with ``migration_destination: None`` for
fourteen minutes. The platform accepted the request and never reported that
there was nowhere for the workloads to go.

The numbers to predict it were available before the request was made, so this
module reads them and refuses first.

Field names below were measured on VergeOS 26.1.8, not inferred:

  cluster_status : total_nodes online_nodes running_machines
                   total_ram online_ram used_ram
                   total_cores online_cores used_cores
                   phys_ram_used state status status_info
  nodes          : ram vm_ram failover_ram cores maintenance running

``nodes.ram`` is physical RAM (94208 per node on the lab); ``nodes.vm_ram`` is
what is actually available to VMs (68352 / 69120). They are not the same
number and the difference is not rounding -- it is the platform's own
reservation. The sum of ``vm_ram`` is what ``cluster_status.online_ram``
reports (68352 + 69120 = 137472), so ``vm_ram`` is the one to do capacity
arithmetic with. Using ``ram`` overstates capacity by roughly a third and
turns "will not fit" into "plenty of room".
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type


def fetch_cluster_status(client):
    """Rows from the ``cluster_status`` table, one per cluster.

    Reached with ``_request`` because pyvergeos does not model this table at
    all: ``clusters.py`` exposes a ``status`` *string* property and stops
    there, so the capacity figures are unreachable through the SDK.
    """
    rows = client._request('GET', 'cluster_status', params={'fields': 'all'})
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def usable_ram(node):
    """RAM this node can host VMs in, in MB.

    ``vm_ram`` rather than ``ram`` -- see the module docstring. Falls back to
    ``ram`` only when ``vm_ram`` is absent, which overstates capacity, so the
    caller is told when that happened rather than being quietly optimistic.
    """
    row = dict(node)
    if row.get('vm_ram') is not None:
        return _int(row.get('vm_ram')), False
    return _int(row.get('ram')), True


def drain_capacity(status, nodes, target):
    """Can ``target`` be drained without running out of RAM.

    The arithmetic, and why it is this and not something simpler:

    ``used_ram`` is cluster-wide RAM committed to running machines. Draining a
    node does not reduce it -- the workloads move, they do not stop. So the
    surviving nodes must be able to hold the WHOLE of ``used_ram``, not just
    the part that migrates.

        survivor capacity = online_ram - usable_ram(target)
        fits              = survivor capacity >= used_ram

    A node already in maintenance is not counted as a survivor. It is on its
    way out itself, and anything placed there would have to move again.

    Returns a dict; never raises on missing fields, because a capacity check
    that tracebacks is worse than one that says it could not tell.
    """
    status = dict(status or {})
    rows = [dict(n) for n in nodes or []]
    target_row = next((n for n in rows if n.get('name') == target), None)

    result = {
        'node': target,
        'fits': None,
        'deficit_mb': 0,
        'survivor_ram_mb': 0,
        'used_ram_mb': _int(status.get('used_ram')),
        'target_ram_mb': 0,
        'online_ram_mb': _int(status.get('online_ram')),
        'estimated': False,
        'reason': '',
    }

    if target_row is None:
        result['reason'] = ("node '%s' is not in the cluster's node list, so "
                            "its capacity could not be checked" % target)
        return result

    target_ram, estimated = usable_ram(target_row)
    result['target_ram_mb'] = target_ram
    result['estimated'] = estimated

    # Survivors computed from the node rows rather than by subtracting from
    # online_ram, so a node that is already in maintenance is excluded. The
    # subtraction alone would count it as available.
    survivor = 0
    for row in rows:
        if row.get('name') == target:
            continue
        if not row.get('running', row.get('is_online')):
            continue
        if row.get('maintenance'):
            continue
        survivor += usable_ram(row)[0]

    result['survivor_ram_mb'] = survivor
    need = result['used_ram_mb']
    result['fits'] = survivor >= need
    if not result['fits']:
        result['deficit_mb'] = need - survivor
        result['reason'] = (
            "draining '%s' leaves %d MB of VM RAM across the surviving nodes, "
            "but %d MB is committed to running machines -- a %d MB shortfall. "
            "The platform will accept the request, flip the node to "
            "maintenance immediately, and then stall mid-migration with "
            "nowhere to put the workloads."
            % (target, survivor, need, result['deficit_mb']))
    else:
        result['reason'] = (
            "draining '%s' leaves %d MB for %d MB of committed VM RAM (%d MB "
            "headroom)" % (target, survivor, need, survivor - need))

    if estimated:
        result['reason'] += (
            " NOTE: this node reported no vm_ram, so physical ram was used "
            "instead, which overstates available capacity.")
    return result


def failover_reserved(nodes):
    """Nodes reserving nothing for a peer failure.

    ``failover_ram`` is the platform's own N-1 reservation. Every node on the
    lab reports 0, which is why nothing stopped the drain that stalled: no
    reservation means the cluster is sized for all nodes up. Worth reporting
    on its own -- it is a standing configuration gap, not a transient one.
    """
    return [dict(n).get('name') for n in nodes or []
            if not _int(dict(n).get('failover_ram'))]
