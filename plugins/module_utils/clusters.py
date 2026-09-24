# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Cluster capacity figures, and whether a node can be drained.

Two questions, answered with different confidence, which is why they are
separate functions rather than one report:

  * How much capacity is there, and where -- ``capacity_facts``. Reported
    with NO verdict attached, for the reason set out below.
  * Can this node be drained -- ``drain_capacity``. A narrower question with a
    sound negative answer; see the comment above that function.

The first half of this docstring is about the first question.

This exists because of issue #24: a tenant node the platform cannot place is
not reported as a failure. The tenant goes Power On -> Initializing ->
Starting -> Stopped, the node ends with no host, and there is no alarm, no
task failure, no ``status_info``, and no log line saying why. The module that
asked for it then fails with::

    Timed out after 180s waiting for tenant to be running

which tells the operator nothing they did not already know.

**capacity_facts reports figures and derives no verdict, deliberately.**

Issue #24 proposed a rule -- that placement needs N-1 failover headroom,
``(sum of node vm_ram - largest node vm_ram) - committed RAM``. Measured again
on 26.1.8 while bringing the tenant modules forward, that rule does not hold:

  A. committed 9216 MB, nodes 68352 / 69120 MB, a 65536 MB tenant node.
     N-1 headroom is 59136 MB, so the rule says no. It started in 30s.
  B. committed 74752 MB, same nodes, an 8192 MB tenant node.
     N-1 headroom is -6400 MB, so the rule says no. It never placed,
     through 180s -- even though one node had 59136 MB free.

B refutes "it fits in some node's free RAM". A refutes "it fits in N-1
headroom". The platform's real placement rule is stricter than the first and
looser than the second, and it is not visible from outside; pyvergeos says as
much about its own approximation -- "a helper, not a scheduler: placement
rules can make the real answer stricter still".

So nothing here decides whether a node WILL be placed. What it does is put
the numbers the platform used in front of the operator, next to the
observation that the node was never given a host, so the failure reads as a
placement refusal rather than as a mystery.

Field names were measured on VergeOS 26.1.8, not inferred:

  cluster_status : total_nodes online_nodes running_machines
                   total_ram online_ram used_ram
                   total_cores online_cores used_cores
  nodes          : name cluster ram vm_ram failover_ram cores maintenance
                   need_restart

``nodes.ram`` is physical RAM (94208 per node on the test system);
``nodes.vm_ram`` is what is available to VMs (68352 / 69120). They are not the
same number and the difference is not rounding -- it is the platform's own
reservation. The sum of ``vm_ram`` is exactly what ``cluster_status.online_ram``
reports (68352 + 69120 = 137472), so ``vm_ram`` is the figure to quote. Using
``ram`` overstates capacity by roughly a third.

``running`` is NOT a column on the nodes table. It arrives through the SDK's
projection ``machine#status#running as running``, which is why NODE_FIELDS
below names it in that form rather than trusting a default projection.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

# Named rather than left to the SDK's default projection: a figure that is
# compared or reported but not fetched reads as None (#18, #92).
NODE_FIELDS = [
    '$key',
    'name',
    'cluster',
    'ram',
    'vm_ram',
    'failover_ram',
    'cores',
    'maintenance',
    'need_restart',
    'machine#status#running as running',
]

# The subset the drain arithmetic reads. Named separately so a test can assert
# the projection supplies them, rather than discovering at runtime that a
# figure it compares on arrives as None -- the shape of #18, #92 and the vm
# power-state defect. Every one of these was confirmed present in a
# ``client.nodes.list(fields=NODE_FIELDS)`` row on 26.1.8.
DRAIN_FIELDS = ('name', 'vm_ram', 'maintenance', 'running')


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def fetch_cluster_status(client):
    """Rows from the ``cluster_status`` table, one per cluster.

    Reached with ``_request`` rather than a manager because this collection
    floors at pyvergeos 1.2.7, where the table is not modelled at all --
    ``clusters.py`` exposes a status *string* and stops, so the capacity
    figures are unreachable through the SDK. Newer pyvergeos does model it
    (``client.cluster_status``); switch to that when the floor moves.
    """
    rows = client._request('GET', 'cluster_status', params={'fields': 'all'})
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def fetch_nodes(client):
    """Physical node rows, with the projection named."""
    return [dict(node) for node in client.nodes.list(fields=NODE_FIELDS)]


def capacity_facts(status_rows, node_rows):
    """The capacity numbers, with no opinion attached.

    Returns a dict that is safe to put in a failure message: every value is a
    plain number or string, and a missing field reads as 0 rather than
    raising. A capacity report that tracebacks is worse than one that says it
    could not tell.
    """
    nodes = []
    for row in node_rows or []:
        row = dict(row)
        nodes.append({
            'name': row.get('name'),
            'vm_ram_mb': _int(row.get('vm_ram')),
            'physical_ram_mb': _int(row.get('ram')),
            'failover_ram_mb': _int(row.get('failover_ram')),
            'maintenance': bool(row.get('maintenance')),
            'running': bool(row.get('running')),
        })

    available = [n['vm_ram_mb'] for n in nodes
                 if n['running'] and not n['maintenance']]

    return {
        'nodes': nodes,
        'online_ram_mb': sum(_int(r.get('online_ram')) for r in status_rows or []),
        'used_ram_mb': sum(_int(r.get('used_ram')) for r in status_rows or []),
        'online_cores': sum(_int(r.get('online_cores')) for r in status_rows or []),
        'used_cores': sum(_int(r.get('used_cores')) for r in status_rows or []),
        'largest_node_vm_ram_mb': max(available) if available else 0,
        'smallest_node_vm_ram_mb': min(available) if available else 0,
    }


def describe_capacity(facts):
    """One human-readable line per fact worth reading in a failure message."""
    per_node = ', '.join(
        '%s %d MB%s%s' % (n['name'], n['vm_ram_mb'],
                          ' (maintenance)' if n['maintenance'] else '',
                          ' (not running)' if not n['running'] else '')
        for n in facts.get('nodes') or [])
    return (
        'cluster RAM available to VMs: %d MB across %d node(s) [%s]; '
        '%d MB is already committed to running machines'
        % (facts.get('online_ram_mb', 0), len(facts.get('nodes') or []),
           per_node or 'no node rows returned',
           facts.get('used_ram_mb', 0)))


# ── draining ─────────────────────────────────────────────────────────────────
#
# Separate from capacity_facts above, and the distinction is the point.
#
# capacity_facts deliberately offers no verdict, because issue #24's proposed
# placement rule was measured and did not hold in either direction: a tenant
# node started with far less than N-1 headroom, and another never placed with
# 59 GB free on one node. Whether the platform will PLACE something new is not
# visible from outside.
#
# Draining is a narrower question with a harder floor. The workloads already
# exist and are already running, so they do not stop when the node goes away --
# they have to fit somewhere else. If the surviving nodes cannot hold what is
# committed, no placement policy makes that work.
#
# So drain_capacity answers only in the direction the arithmetic is sound in:
#
#   fits=False  the RAM is not there. This is a fact, and it is worth
#               refusing on. Measured: draining a node with an 18 GB shortfall
#               was accepted by the platform, flipped the node to maintenance
#               in under a second, and then sat at status 'migrating' with
#               migration_destination None for fourteen minutes -- no error,
#               no timeout, no alarm.
#   fits=True   the RAM is there. It is NOT a promise that the migration will
#               succeed: per #24 the platform's placement rule is stricter
#               than free RAM, so a drain can still stall for reasons this
#               cannot see.
#   fits=None   could not be determined. Callers must not read this as yes.


def usable_ram(node):
    """``(mb, estimated)`` -- RAM this node can host VMs in.

    ``vm_ram`` rather than ``ram``, per the module docstring. Falls back to
    ``ram`` only when ``vm_ram`` is absent, which overstates capacity by about
    a third, so the caller is told it happened rather than being quietly
    optimistic.
    """
    row = dict(node)
    if row.get('vm_ram') is not None:
        return _int(row.get('vm_ram')), False
    return _int(row.get('ram')), True


def _is_running(row):
    """``True``/``False``/``None`` -- and None means the row did not say.

    ``running`` is not a column on the nodes table; it arrives through the
    projection ``machine#status#running as running`` and is absent from both
    ``fields=most`` and ``fields=all``. A row that lacks it would make every
    peer look stopped, which turns the capacity gate into a gate that always
    refuses -- and a gate that always refuses gets turned off.
    """
    row = dict(row)
    for name in ('running', 'is_online'):
        if row.get(name) is not None:
            return bool(row[name])
    return None


def drain_capacity(status, nodes, target):
    """Whether ``target`` can be drained without running out of RAM.

    The arithmetic, and why it is this and not something simpler:

    ``used_ram`` is cluster-wide RAM committed to running machines. Draining a
    node does not reduce it -- the workloads move, they do not stop. So the
    surviving nodes must be able to hold the WHOLE of ``used_ram``, not just
    the part that migrates.

        survivor capacity = sum of vm_ram over the nodes still in service
        fits              = survivor capacity >= used_ram

    Survivors are summed from the node rows rather than subtracted from
    ``online_ram``, so a node already in maintenance is excluded. It is on its
    way out itself, and anything placed there would have to move again; the
    subtraction alone would count it as available.

    Never raises on a missing field. A capacity check that tracebacks is worse
    than one that says it could not tell.
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

    # Refuse to answer on a projection that did not supply the field the
    # answer turns on, instead of computing a confident zero from it.
    if all(_is_running(r) is None for r in rows):
        result['reason'] = (
            "no node row reported a 'running' field, so which nodes would "
            "survive the drain could not be determined. That field is not a "
            "column on the nodes table -- it arrives through the projection "
            "'machine#status#running as running' and is absent from "
            "fields=most and fields=all. Fetch the nodes with NODE_FIELDS.")
        return result

    target_ram, estimated = usable_ram(target_row)
    result['target_ram_mb'] = target_ram
    result['estimated'] = estimated

    survivor = 0
    for row in rows:
        if row.get('name') == target:
            continue
        if _is_running(row) is not True:
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
            "headroom). That is enough RAM; it is not a guarantee the "
            "platform will place every workload, whose rule is stricter than "
            "free RAM and is not visible from here."
            % (target, survivor, need, survivor - need))

    if estimated:
        result['reason'] += (
            " NOTE: this node reported no vm_ram, so physical ram was used "
            "instead, which overstates available capacity.")
    return result


def failover_reserved(nodes):
    """Nodes reserving nothing for a peer failure.

    ``failover_ram`` is the platform's own N-1 reservation. Every node on the
    measured lab reports 0, which is part of why nothing stopped the drain
    that stalled: no reservation means the cluster is sized for all nodes
    being up. Worth reporting on its own -- it is a standing configuration
    gap, not a transient one.
    """
    return [dict(n).get('name') for n in nodes or []
            if not _int(dict(n).get('failover_ram'))]
