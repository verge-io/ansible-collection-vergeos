# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test rows that came from the platform, not from the author.

Why this exists
---------------
Three separate bugs in this collection had one shape: the code read a field
name the author believed in, and the unit fixture asserted the same belief.
The test agreed with the bug and both were wrong together.

  B4  code read 'online' / 'needs_restart'
      fixture:  {'online': True, 'needs_restart': False}
      platform: {'running': True, 'need_restart': False}
      -> every node reported offline; drains refused; nothing to test caught it

  B2  code read 'member_name' / 'member_type'
      fixture:  {'member_name': 'alice', 'member_type': 'users'}
      platform: {'member': 'users/1', 'member_display': 'welchums'}
      -> every group reported zero members

  B6  code read 'is_installed' / 'is_reboot_required'
      platform: 'installed' / 'reboot_required'

A hand-written dict is an unverifiable claim about the API. This module makes
that claim checkable by keeping real captured responses in
tests/fixtures/api/ and refusing to hand back a row containing a field the
platform did not send.

The shape depends on the CALL, not the table
--------------------------------------------
This is the part that makes the problem subtle, and it is why each capture
records the exact call that produced it:

  nodes: 'running' is present in client.nodes.list(), because the SDK asks
  for an explicit field list. It is absent from BOTH GET /nodes?fields=most
  and GET /nodes?fields=all.

  group members: the SDK's projection returns member='users/1'.
  GET /members?fields=all returns member='/v4/users/2' for the same row.

So "what fields does a node have" has no single answer, and a fixture that
does not say which call it came from cannot be checked against anything.

Refreshing
----------
    python tests/capture_api_fixtures.py      # needs VERGEOS_* env vars

Captures are committed so the suite stays offline and deterministic.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import copy
import json
import os

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), '..', 'fixtures', 'api')


def _load(name):
    path = os.path.join(FIXTURE_DIR, '%s.json' % name)
    if not os.path.exists(path):
        raise AssertionError(
            "no captured API fixture named %r. Available: %s. "
            "Add one by extending tests/capture_api_fixtures.py and running it "
            "against a real system -- do not hand-write it."
            % (name, ', '.join(sorted(available())) or 'none'))
    with open(path) as handle:
        return json.load(handle)


def available():
    if not os.path.isdir(FIXTURE_DIR):
        return []
    return [f[:-5] for f in os.listdir(FIXTURE_DIR) if f.endswith('.json')]


def call_for(name):
    """The SDK call this capture came from."""
    return _load(name)['_call']


def rows(name):
    """Every captured row, as plain dicts."""
    return copy.deepcopy(_load(name)['rows'])


def fields(name):
    """Every field name the platform sent for this call."""
    return {key for row in _load(name)['rows'] for key in row}


def row(name, index=0, /, **overrides):
    """One captured row, with ``overrides`` applied.

    Raises if an override names a field the platform did not send. That is the
    entire point: it turns "I think the field is called this" from a silent
    assumption into a failing test at the moment it is written.

    Use it for the values a test cares about::

        row('nodes', running=False)          # ok, 'running' was captured
        row('nodes', online=False)           # AssertionError -- B4's mistake

    To add a field that genuinely is not in the capture -- a projection this
    fixture does not cover -- capture that projection instead.

    ``name`` and ``index`` are positional-only, because rows have a ``name``
    field of their own and ``row('nodes', name='node1')`` must set the field
    rather than collide with the parameter.
    """
    captured = _load(name)
    all_rows = captured['rows']
    if not all_rows:
        raise AssertionError(
            "fixture %r captured zero rows (call: %s), so there is nothing to "
            "build on. Configure one on the lab and re-capture."
            % (name, captured['_call']))

    known = fields(name)
    invented = sorted(set(overrides) - known)
    if invented:
        raise AssertionError(
            "%s is not a field the platform sent for %r.\n"
            "  call captured : %s\n"
            "  fields sent   : %s\n"
            "If you believe the field exists, capture the call that returns it "
            "rather than asserting it here -- a row's shape depends on the "
            "projection, not the table."
            % (', '.join(repr(f) for f in invented), name,
               captured['_call'], ', '.join(sorted(known))))

    out = copy.deepcopy(all_rows[index])
    out.update(overrides)
    return out
