# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared helpers for the node modules."""

from __future__ import absolute_import, division, print_function
__metaclass__ = type


def _flag(row, *names):
    """First present value among ``names``, as a bool.

    Node rows spell these differently depending on the field projection --
    ``maintenance`` on the raw row, ``is_maintenance`` on the model -- and
    reading only one of them silently reports every node as not in
    maintenance, which is the answer that makes a drain look unnecessary.
    """
    for name in names:
        if name in row:
            return bool(row[name])
    return False


def summarize_node(row):
    """Normalise the three node states every caller branches on.

    ``online`` is read positively: a node whose state could not be determined
    is NOT online. An upgrade loop that treats unknown as online will drain
    the next node while the previous one is still down.
    """
    out = dict(row)
    out['online'] = _flag(row, 'online', 'is_online')
    out['maintenance'] = _flag(row, 'maintenance', 'is_maintenance')
    out['needs_restart'] = _flag(row, 'needs_restart', 'restart_needed')
    return out


def find_node(client, name):
    """One node row by name, or None. Matched client-side."""
    for row in client.nodes.list():
        row = dict(row)
        if row.get('name') == name:
            return summarize_node(row)
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
