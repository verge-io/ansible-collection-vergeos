"""Unit tests for cluster capacity and drain feasibility.

The numbers in these fixtures are the ones measured on the test cluster
(VergeOS 26.1.8) at the moment a drain actually stalled, so the tests assert
against a failure that happened rather than one imagined:

  cluster_status : online_ram=137472  used_ram=87040  online_nodes=2
  node1          : ram=94208  vm_ram=68352  failover_ram=0
  node2          : ram=94208  vm_ram=69120  failover_ram=0
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from ansible_collections.vergeio.vergeos.plugins.module_utils.clusters import (
    drain_capacity,
    failover_reserved,
    n1_headroom,
    usable_ram,
)

STATUS = {'cluster': 1, 'online_ram': 137472, 'used_ram': 87040,
          'online_nodes': 2, 'total_nodes': 2, 'state': 'online'}

NODES = [
    {'name': 'node1', 'cluster': 1, 'ram': 94208, 'vm_ram': 68352,
     'failover_ram': 0, 'running': True, 'maintenance': False},
    {'name': 'node2', 'cluster': 1, 'ram': 94208, 'vm_ram': 69120,
     'failover_ram': 0, 'running': True, 'maintenance': False},
]


def test_usable_ram_prefers_vm_ram_over_physical():
    """vm_ram is what the platform lets VMs have; ram is the stick in the box.

    On the measured node those are 68352 and 94208 -- using the physical
    figure overstates capacity by about a third, which is the difference
    between "will not fit" and "plenty of room".
    """
    assert usable_ram(NODES[0]) == (68352, False)


def test_usable_ram_falls_back_to_physical_and_says_so():
    value, estimated = usable_ram({'name': 'x', 'ram': 1000})
    assert value == 1000
    assert estimated is True


def test_the_measured_stall_is_predicted():
    """This is bug P1. Draining node2 was accepted and then stalled for
    fourteen minutes with migration_destination=None."""
    result = drain_capacity(STATUS, NODES, 'node2')
    assert result['fits'] is False
    assert result['survivor_ram_mb'] == 68352
    assert result['used_ram_mb'] == 87040
    assert result['deficit_mb'] == 18688


def test_the_other_node_is_also_short():
    result = drain_capacity(STATUS, NODES, 'node1')
    assert result['fits'] is False
    assert result['deficit_mb'] == 17920


def test_a_drain_that_fits_reports_headroom():
    light = [dict(n) for n in NODES]
    result = drain_capacity({'online_ram': 137472, 'used_ram': 40000},
                            light, 'node2')
    assert result['fits'] is True
    assert result['deficit_mb'] == 0
    assert 'headroom' in result['reason']


def test_used_ram_is_not_reduced_by_the_drained_node():
    """The whole of used_ram must fit on the survivors.

    Draining moves workloads, it does not stop them, so subtracting the
    drained node's share from the requirement is the mistake that makes an
    impossible drain look possible. 87040 must be compared against 68352, not
    against 68352 minus anything.
    """
    result = drain_capacity(STATUS, NODES, 'node2')
    assert result['used_ram_mb'] == STATUS['used_ram']


def test_a_node_already_in_maintenance_is_not_a_survivor():
    """It is on its way out itself; anything placed there moves again."""
    nodes = [dict(n) for n in NODES]
    nodes[1]['maintenance'] = True
    result = drain_capacity(STATUS, nodes, 'node1')
    assert result['survivor_ram_mb'] == 0
    assert result['fits'] is False


def test_an_offline_node_is_not_a_survivor():
    nodes = [dict(n) for n in NODES]
    nodes[1]['running'] = False
    assert drain_capacity(STATUS, nodes, 'node1')['survivor_ram_mb'] == 0


def test_is_online_is_accepted_as_well_as_running():
    """Raw rows carry 'running'; SDK models expose 'is_online'. Bug B4 was
    reading only the name that did not exist."""
    nodes = [{'name': 'a', 'vm_ram': 100, 'is_online': True},
             {'name': 'b', 'vm_ram': 100, 'is_online': True}]
    assert drain_capacity({'used_ram': 50}, nodes, 'a')['survivor_ram_mb'] == 100


def test_an_unknown_node_is_undetermined_not_fine():
    """None, not False and not True. A gate must be able to tell "could not
    determine" from "yes"."""
    result = drain_capacity(STATUS, NODES, 'nodeX')
    assert result['fits'] is None
    assert 'not in the cluster' in result['reason']


def test_missing_fields_do_not_raise():
    """A capacity check that tracebacks is worse than one that says it could
    not tell -- the traceback reads as "the module is broken"."""
    assert drain_capacity({}, [], 'node1')['fits'] is None
    assert drain_capacity(None, None, 'node1')['fits'] is None


def test_estimated_capacity_is_flagged_in_the_reason():
    nodes = [{'name': 'a', 'ram': 1000, 'running': True},
             {'name': 'b', 'ram': 1000, 'running': True}]
    result = drain_capacity({'online_ram': 2000, 'used_ram': 500}, nodes, 'a')
    assert result['estimated'] is True
    assert 'overstates' in result['reason']


def test_issue_24_headroom_is_survivor_minus_committed():
    """The figure the original report measured, from the drain arithmetic.

    committed 54585 MB, nodes 68352 / 69120 MB. Losing the larger node leaves
    68352 MB, so N-1 headroom is 13767 MB. That is drain_capacity asked about
    node2, not online_ram - used_ram (which is the free-RAM number the issue
    showed does not predict placement).
    """
    status = [{'cluster': 1, 'online_ram': 137472, 'used_ram': 54585}]
    head = n1_headroom(status, NODES)
    assert head['largest_node'] == 'node2'
    assert head['largest_node_ram_mb'] == 69120
    assert head['survivor_ram_mb'] == 68352
    assert head['headroom_mb'] == 13767
    assert head['used_ram_mb'] == 54585


def test_headroom_matches_drain_of_the_largest_node():
    """Same inputs, same answer. Two copies of this arithmetic would drift."""
    head = n1_headroom([STATUS], NODES)
    drained = drain_capacity(STATUS, NODES, 'node2')
    assert head['survivor_ram_mb'] == drained['survivor_ram_mb']
    assert head['headroom_mb'] == drained['survivor_ram_mb'] - drained['used_ram_mb']
    assert head['headroom_mb'] == 68352 - 87040


def test_the_refuted_cases_are_figures_not_gates():
    """Both directions from the re-measurement, as numbers only.

    A. committed 9216, headroom 59136. A 65536 MB node started anyway.
    B. committed 74752, headroom -6400. An 8192 MB node never placed.
    The function returns the figure in both cases and has no fits flag.
    """
    above = n1_headroom([dict(STATUS, used_ram=9216)], NODES)
    below = n1_headroom([dict(STATUS, used_ram=74752)], NODES)
    assert above['headroom_mb'] == 68352 - 9216
    assert below['headroom_mb'] == 68352 - 74752
    assert 'fits' not in above
    assert 'fits' not in below


def test_n1_headroom_is_none_when_it_cannot_be_computed():
    assert n1_headroom([], []) is None
    assert n1_headroom(None, None) is None
    blind = [{'name': 'node1', 'vm_ram': 68352},
             {'name': 'node2', 'vm_ram': 69120}]
    assert n1_headroom([STATUS], blind) is None


def test_n1_headroom_reports_the_tighter_cluster():
    """A tenant node lands in one cluster. The roomier cluster's headroom
    would hide the constraint."""
    status = [
        {'cluster': 1, 'used_ram': 1000, 'online_ram': 3000},
        {'cluster': 2, 'used_ram': 9000, 'online_ram': 10000},
    ]
    nodes = [
        {'name': 'a', 'cluster': 1, 'vm_ram': 1000, 'running': True},
        {'name': 'b', 'cluster': 1, 'vm_ram': 2000, 'running': True},
        {'name': 'c', 'cluster': 2, 'vm_ram': 4000, 'running': True},
        {'name': 'd', 'cluster': 2, 'vm_ram': 6000, 'running': True},
    ]
    head = n1_headroom(status, nodes)
    # Cluster 1: lose b, survivor 1000, headroom 0.
    # Cluster 2: lose d, survivor 4000, headroom 4000 - 9000 = -5000.
    assert head['largest_node'] == 'd'
    assert head['headroom_mb'] == -5000


def test_failover_reservation_gap_is_reported():
    """Every node on the measured lab reserves nothing, which is why nothing
    stopped the drain that stalled."""
    assert failover_reserved(NODES) == ['node1', 'node2']


def test_a_node_with_a_reservation_is_not_reported():
    nodes = [dict(NODES[0], failover_ram=8192), NODES[1]]
    assert failover_reserved(nodes) == ['node2']
