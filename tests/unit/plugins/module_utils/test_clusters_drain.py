# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Whether a node can be drained, and what the answer is allowed to be.

The rows here come from the captured node and cluster_status fixtures, so a
field name nobody checked cannot creep back in: api_fixtures.row() refuses an
override the platform did not send.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible_collections.vergeio.vergeos.tests.unit import api_fixtures as F
from ansible_collections.vergeio.vergeos.plugins.module_utils.clusters import (
    DRAIN_FIELDS,
    NODE_FIELDS,
    drain_capacity,
    failover_reserved,
    usable_ram,
)


def node(name, vm_ram, running=True, maintenance=False, **over):
    return F.row('nodes', name=name, vm_ram=vm_ram, running=running,
                 maintenance=maintenance, **over)


def status(used_ram, online_ram=137472):
    return F.row('cluster_status', used_ram=used_ram, online_ram=online_ram)


TWO = [node('node1', 68352), node('node2', 69120)]


# ── the projection the answer depends on ─────────────────────────────────────

class TestTheProjectionSuppliesWhatTheGateReads:
    """The defect this collection keeps finding, in its newest place.

    `running` is not a column on the nodes table. It arrives through the
    projection `machine#status#running as running` and is absent from BOTH
    fields=most and fields=all -- the same shape as #18, #92 and the `vm`
    power-state bug, where a module compared on a figure it never fetched.
    """

    @pytest.mark.parametrize('field', DRAIN_FIELDS)
    def test_every_field_the_drain_gate_reads_is_named_in_the_projection(self, field):
        named = {f.rsplit(' as ', maxsplit=1)[-1] for f in NODE_FIELDS}
        assert field in named, (
            "drain_capacity reads %r but NODE_FIELDS does not fetch it, so it "
            "arrives as None and the gate compares against nothing" % field)

    @pytest.mark.parametrize('field', DRAIN_FIELDS)
    def test_and_the_platform_really_sends_it(self, field):
        assert field in F.fields('nodes'), (
            "%r is not in the captured %s response" % (field, F.call_for('nodes')))

    def test_a_row_without_running_is_refused_rather_than_read_as_stopped(self):
        """This is the whole point of the guard.

        Without it, rows from fields=all make every peer look stopped, the
        survivor total comes out 0, and the gate reports a confident shortfall
        for a cluster with 59 GB free. A gate that always refuses is a gate
        somebody turns off.
        """
        blind = [{k: v for k, v in row.items() if k != 'running'}
                 for row in TWO]
        result = drain_capacity(status(9216), blind, 'node2')
        assert result['fits'] is None
        assert 'running' in result['reason']
        assert 'machine#status#running as running' in result['reason']

    def test_one_row_missing_it_is_not_enough_to_abstain(self):
        """Abstaining because a single row is odd would make the gate useless
        on any cluster with one unusual node. It abstains when NO row said."""
        rows = [dict(TWO[0]), {k: v for k, v in TWO[1].items() if k != 'running'}]
        assert drain_capacity(status(9216), rows, 'node2')['fits'] is True


# ── the arithmetic ───────────────────────────────────────────────────────────

class TestTheArithmetic:
    def test_the_whole_of_used_ram_has_to_fit_on_the_survivors(self):
        """Draining moves workloads, it does not stop them. Subtracting the
        drained node's share from the requirement is what makes an impossible
        drain look possible."""
        result = drain_capacity(status(9216), TWO, 'node2')
        assert result['fits'] is True
        assert result['survivor_ram_mb'] == 68352
        assert result['used_ram_mb'] == 9216

    def test_a_shortfall_is_reported_with_its_size(self):
        result = drain_capacity(status(87040), TWO, 'node2')
        assert result['fits'] is False
        assert result['deficit_mb'] == 87040 - 68352
        assert 'shortfall' in result['reason']
        assert 'stall' in result['reason']

    def test_exactly_enough_fits(self):
        assert drain_capacity(status(68352), TWO, 'node2')['fits'] is True

    def test_one_mb_over_does_not(self):
        assert drain_capacity(status(68353), TWO, 'node2')['fits'] is False

    def test_a_node_already_in_maintenance_is_not_somewhere_to_go(self):
        """It is on its way out itself; anything placed there would have to
        move again. Subtracting from online_ram instead of summing the node
        rows would have counted it."""
        rows = [node('node1', 68352, maintenance=True), node('node2', 69120),
                node('node3', 8192)]
        assert drain_capacity(status(9216), rows, 'node2')['survivor_ram_mb'] == 8192

    def test_an_offline_node_is_not_somewhere_to_go_either(self):
        rows = [node('node1', 68352, running=False), node('node2', 69120),
                node('node3', 8192)]
        assert drain_capacity(status(9216), rows, 'node2')['survivor_ram_mb'] == 8192

    def test_the_target_never_counts_as_its_own_survivor(self):
        assert drain_capacity(status(0), TWO, 'node1')['survivor_ram_mb'] == 69120


# ── what the answer is allowed to be ─────────────────────────────────────────

class TestNoneIsNotYes:
    def test_an_unknown_node_cannot_be_checked(self):
        result = drain_capacity(status(9216), TWO, 'zz-no-such-node')
        assert result['fits'] is None
        assert 'not in the cluster' in result['reason']

    def test_no_rows_at_all_is_also_none(self):
        assert drain_capacity(status(9216), [], 'node1')['fits'] is None

    def test_none_survives_the_idiom_roles_gate_on(self):
        """Roles reject anything that is not explicitly true, rather than
        testing for truth -- `rejectattr('fits', 'equalto', true)`. A None
        that slipped through a truth test would pass a gate whose whole job is
        to be certain."""
        checks = [drain_capacity(status(9216), TWO, 'zz-no-such-node')]
        rejected = [c for c in checks if c['fits'] is not True]
        assert rejected, 'a None verdict passed the gate'

    def test_a_fits_true_says_out_loud_that_it_is_not_a_promise(self):
        """Issue #24 measured a tenant node that never placed with 59 GB free
        on one host. Enough RAM is necessary and demonstrably not
        sufficient, and the reason string has to say so or somebody will read
        it as a guarantee."""
        reason = drain_capacity(status(9216), TWO, 'node2')['reason']
        assert 'not a guarantee' in reason


class TestMissingFieldsDoNotTraceback:
    """A capacity check that tracebacks is worse than one that says it could
    not tell."""

    def test_an_empty_status_row_is_survivable(self):
        assert drain_capacity({}, TWO, 'node1')['used_ram_mb'] == 0

    def test_a_none_status_is_survivable(self):
        assert drain_capacity(None, TWO, 'node1')['fits'] is True

    def test_a_node_with_no_vm_ram_falls_back_and_says_so(self):
        rows = [dict(TWO[0]), dict(TWO[1])]
        rows[1].pop('vm_ram')
        result = drain_capacity(status(9216), rows, 'node2')
        assert result['estimated'] is True
        assert 'overstates' in result['reason']

    def test_usable_ram_prefers_vm_ram_over_physical(self):
        """On a measured node those are 68352 and 94208. Using the physical
        figure overstates capacity by about a third."""
        assert usable_ram({'vm_ram': 68352, 'ram': 94208}) == (68352, False)
        assert usable_ram({'ram': 94208}) == (94208, True)

    def test_a_zero_vm_ram_is_a_number_not_a_missing_field(self):
        assert usable_ram({'vm_ram': 0, 'ram': 94208}) == (0, False)


class TestFailoverReservation:
    def test_nodes_reserving_nothing_are_listed(self):
        rows = [node('node1', 68352, failover_ram=0),
                node('node2', 69120, failover_ram=8192)]
        assert failover_reserved(rows) == ['node1']

    def test_the_captured_lab_reserves_nothing_anywhere(self):
        """Recorded because it is why nothing stopped the drain that stalled:
        no reservation means the cluster is sized for all nodes being up."""
        assert sorted(failover_reserved(F.rows('nodes'))) == ['node1', 'node2']
