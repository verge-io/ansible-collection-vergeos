# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Cluster capacity figures, for explaining a placement that did not happen.

This exists because of issue #24: a tenant node the platform cannot place is
not reported as a failure. The tenant goes Power On -> Initializing ->
Starting -> Stopped, the node ends with no host, and there is no alarm, no
task failure, no ``status_info``, and no log line saying why. The module that
asked for it then fails with::

    Timed out after 180s waiting for tenant to be running

which tells the operator nothing they did not already know.

**This module reports facts and derives no verdict, deliberately.**

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
  nodes          : name ram vm_ram failover_ram cores maintenance

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
    'ram',
    'vm_ram',
    'failover_ram',
    'cores',
    'maintenance',
    'machine#status#running as running',
]


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
