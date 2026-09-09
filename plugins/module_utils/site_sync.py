# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared helpers for the site-sync modules.

Site syncs are VergeOS's native site-to-site replication (ioReplicate). An
OUTGOING sync pushes cloud snapshots to a remote system; the matching
INCOMING sync on that remote system accepts them and issues the registration
code the outgoing side authenticates with.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import time

# Fields whose value the caller can reconcile on an existing outgoing sync.
# registration_code is deliberately absent: it is a create-time credential the
# remote system issues, the API does not return it, and "reconciling" it would
# mean rewriting an established trust relationship on every run.
OUTGOING_MUTABLE = (
    'description', 'url', 'encryption', 'compression', 'netinteg',
    'threads', 'file_threads', 'destination_tier', 'queue_retry_count',
    'queue_retry_interval_seconds', 'queue_retry_interval_multiplier',
    'note', 'enabled',
)

# The row fields those parameters actually land in. The SDK's create() takes
# friendlier names than the table uses, and an update has to compare against
# the table's own spelling or every run reports drift that is not there.
ROW_FIELD = {
    'netinteg': 'netinteg',
    'threads': 'threads',
    'file_threads': 'file_threads',
    'queue_retry_interval_seconds': 'queue_retry_interval',
    'queue_retry_interval_multiplier': 'queue_retry_multiplier',
}


def row_field(param):
    """The table field a create() parameter corresponds to."""
    return ROW_FIELD.get(param, param)


def seconds_since(epoch_value, now=None):
    """Age in seconds of an epoch timestamp, or None when there is none.

    Returned as an age rather than a timestamp because every caller wants the
    age: "how far behind is this sync" is the question, and making each role
    subtract from its own clock invites each one to get the units wrong.
    """
    if not epoch_value:
        return None
    try:
        value = int(epoch_value)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return max(0, int(now if now is not None else time.time()) - value)


def summarize(row, now=None):
    """Add the derived fields every consumer of a sync row needs.

    ``healthy`` is deliberately conservative: a sync is healthy only when the
    platform positively says it is online and not in error. An unknown state
    is NOT healthy -- a replication watchdog that reports green on data it
    could not read is worse than no watchdog.

    Also the single choke point where the registration code is stripped. Every
    sync row returned by any module in this collection passes through here, so
    redacting once here cannot be forgotten at a call site -- and it holds
    whether or not a given VergeOS build echoes the code back on create.
    """
    out = dict(row)
    for secret in ('registration_code', 'registration'):
        out.pop(secret, None)
    out['seconds_since_last_run'] = seconds_since(
        row.get('last_run') or row.get('last_sync'), now=now)
    out['healthy'] = bool(row.get('online')) and not row.get('error')
    return out


def stale(rows, max_age_seconds):
    """Syncs whose last successful run is older than the budget.

    A sync that has NEVER run counts as stale. That is the point: a
    replication target configured months ago and never exercised is the
    failure this check exists to surface, and treating "no timestamp" as
    "nothing to report" would hide exactly that case.
    """
    if not max_age_seconds:
        return []
    out = []
    for row in rows:
        age = row.get('seconds_since_last_run')
        if age is None or age > int(max_age_seconds):
            out.append(row)
    return out


def find_by_name(manager, name):
    """One sync row by name, or None.

    Matched client-side. A refused server-side filter returns ``{"err": ...}``
    -- a single-key mapping that reads as one result row -- and there are tens
    of syncs at most, so the unfiltered list costs nothing.
    """
    for row in manager.list():
        row = dict(row)
        if row.get('name') == name:
            return row
    return None
