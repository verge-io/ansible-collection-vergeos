"""Unit tests for cluster capacity and drain feasibility.

The numbers in these fixtures are the ones measured on conundrum-lab
(VergeOS 26.1.8) at the moment a drain actually stalled, so the tests assert
against a failure that happened rather than one imagined:

  cluster_status : online_ram=137472  used_ram=87040  online_nodes=2
  node1          : ram=94208  vm_ram=68352  failover_ram=0
  node2          : ram=94208  vm_ram=69120  failover_ram=0
"""

from ansible_collections.vergeio.vergeos.plugins.module_utils.clusters import (
    drain_capacity,
    failover_reserved,
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


def test_failover_reservation_gap_is_reported():
    """Every node on the measured lab reserves nothing, which is why nothing
    stopped the drain that stalled."""
    assert failover_reserved(NODES) == ['node1', 'node2']


def test_a_node_with_a_reservation_is_not_reported():
    nodes = [dict(NODES[0], failover_ram=8192), NODES[1]]
    assert failover_reserved(nodes) == ['node2']
