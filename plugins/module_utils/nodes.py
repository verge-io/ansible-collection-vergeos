# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared helpers for the node modules.

The field names below were read off a live VergeOS 26.1.8 node row, not
inferred. The first version of this file guessed them and guessed wrong, and
because the unit-test fixtures encoded the same guess, the tests passed while
every node reported as offline. Recorded here so the next change starts from
the measurement:

    client.nodes.list()  : running, maintenance, need_restart, status,
                           restart_reason
    model properties     : is_online, is_maintenance, needs_restart, status

Note ``running`` (there is no ``online`` key) and ``need_restart`` (singular
``need``). The plural ``needs_restart`` exists only as a model property, and
``is_online`` is simply ``bool(row['running'])``.

The projection matters, and this is easy to get wrong twice: ``running`` is
present because the SDK's list() asks for an explicit field set that includes
it. It is absent from BOTH ``GET /nodes?fields=most`` AND
``GET /nodes?fields=all`` -- there, the only ``running`` keys are the ones
nested inside each row's ``running_machines`` entries, which are per-machine
and answer a different question. (That list is useful in its own right; see
MACHINE_FIELDS below. It is simply not the node's own state.)

So these helpers are correct for rows that came from ``client.nodes.list()``
and would silently report every node offline for rows fetched with
``_request('GET', 'nodes', {'fields': 'all'})``. If you ever change where the
rows come from, re-capture the fixture: see tests/capture_api_fixtures.py.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

# Model property first, then the raw row's field names. Order matters: the
# properties are computed and authoritative, the raw names are what survives
# dict() conversion.
ONLINE = ('is_online', 'running')
MAINTENANCE = ('is_maintenance', 'maintenance')
NEEDS_RESTART = ('needs_restart', 'need_restart')

# What is actually resident on a node, which is not the same question as
# "which VMs are on it". Measured on the lab: node1 carried NINE running
# machines while ``vms.list()`` attributed only TWO of them to it. The other
# seven are vnets -- Core, DMZ, External and the tenant fabrics -- and
# ``networks.list()`` reports no node at all, so a check written over VMs
# cannot see them.
#
# That gap has teeth. On a system whose UI and API ride a vnet, draining the
# node hosting it takes away the connection the drain is being driven over,
# and a caller that counted only VMs would have called the node empty.
#
# ``running_machines`` is absent from the SDK's default projection but does
# survive a named one, so it costs one extra call rather than a raw request.
# Each entry carries ``migratable``, which is precisely what decides whether
# a drain moves a workload or stops it.
MACHINE_FIELDS = ['$key', 'name', 'running_machines']


def _flag(node, names):
    """First present value among ``names``, as a bool.

    Tries attribute access before the mapping for each name. That order
    matters and the reason is easy to get wrong: pyvergeos ResourceObject is a
    dict SUBCLASS, so branching on isinstance(node, dict) sends every real SDK
    object down the plain-mapping path and the computed properties are never
    consulted. This version asks for the attribute unconditionally -- harmless
    on a plain dict, which simply has no such attribute.

    A name that is present but ``None`` counts as absent. The API returns null
    for a field it did not populate, and treating null as False is how "state
    unknown" silently became "definitely off".
    """
    for name in names:
        value = getattr(node, name, None)
        if value is None:
            try:
                value = node.get(name)
            except AttributeError:
                value = None
        if value is not None:
            return bool(value)
    return False


def summarize_node(node):
    """Normalise the three node states every caller branches on.

    Accepts an SDK node object or a plain row dict. Pass the object where you
    have one: its computed properties are authoritative.

    ``online`` is read positively -- a node whose state could not be
    determined is NOT online. An upgrade loop that treats unknown as online
    will drain the next node while the previous one is still down.
    """
    out = dict(node)
    out['online'] = _flag(node, ONLINE)
    out['maintenance'] = _flag(node, MAINTENANCE)
    out['needs_restart'] = _flag(node, NEEDS_RESTART)
    return out


def machine_census(client):
    """``name -> {"running_machines": n, "unmigratable_machines": n}``.

    A second, narrow call rather than a wider first one: node_info returns the
    whole default row and narrowing it would silently drop fields callers
    already read.
    """
    census = {}
    for node in client.nodes.list(fields=MACHINE_FIELDS):
        row = dict(node)
        machines = row.get('running_machines') or []
        if not isinstance(machines, list):
            machines = []
        machines = [m for m in machines if isinstance(m, dict)]
        census[row.get('name')] = {
            'running_machines': len(machines),
            'unmigratable_machines': len(
                [m for m in machines if m.get('migratable') is False]),
        }
    return census


def list_nodes(client):
    """Every node, summarised. Keeps the SDK objects long enough to read their
    properties, which is why callers should not pre-convert to dicts."""
    nodes = [summarize_node(n) for n in client.nodes.list()]
    census = machine_census(client)
    for node in nodes:
        node.update(census.get(node.get('name'),
                               {'running_machines': None,
                                'unmigratable_machines': None}))
    return nodes


def find_node(client, name):
    """One node by name, or None. Matched client-side."""
    for node in client.nodes.list():
        if dict(node).get('name') == name:
            return summarize_node(node)
    return None


def other_online_nodes(nodes, name):
    """Online nodes that are neither ``name`` nor already in maintenance.

    This is the "somewhere to evacuate to" count. A node already in
    maintenance does not count as somewhere to go -- it is on its way out
    itself, and workloads placed there would have to move again.
    """
    return [n for n in nodes
            if n.get('name') != name
            and n.get('online')
            and not n.get('maintenance')]
