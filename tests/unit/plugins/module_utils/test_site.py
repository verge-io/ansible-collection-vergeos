# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the site preflight shapers.

The node and cluster numbers are the captured 26.1.8 rows in
tests/fixtures/api, so a renamed column fails here instead of in a playbook.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import json
import os
from types import SimpleNamespace

from ansible_collections.vergeio.vergeos.plugins.module_utils.site import (
    census,
    largest_node_vm_ram_mb,
    ram_headroom_mb,
    read_identity,
    shape_clusters,
    shape_node,
    shape_nodes,
    storage_tier_report,
)


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..', '..', '..'))


def _fixture(name):
    path = os.path.join(_root(), 'tests', 'fixtures', 'api', name + '.json')
    with open(path) as handle:
        return json.load(handle)['rows']


def test_tier_number_is_the_tier_column():
    """The row key and the tier number are different columns.

    On a one-tier system they happen to match. A playbook that stored the
    key would ask for the wrong tier the moment they diverged.
    """
    report = storage_tier_report([
        {'$key': 9, 'tier': 4, 'capacity': 0, 'used': 0, 'description': ''},
    ])
    assert report['storage_tiers'] == [4]
    assert report['storage_tier_details'][0]['$key'] == 9
    assert report['storage_tier_details'][0]['tier'] == 4


def test_a_tier_with_no_capacity_is_still_present():
    """A tier row with nothing stored on it is still a tier the system has.

    Inferring tiers from drives would drop this row.
    """
    report = storage_tier_report([
        {'$key': 1, 'tier': 1, 'capacity': 100, 'used': 10},
        {'$key': 2, 'tier': 3, 'capacity': 0, 'used': 0},
    ])
    assert report['storage_tiers'] == [1, 3]
    empty = report['storage_tier_details'][1]
    assert empty['tier'] == 3
    assert empty['capacity'] == 0


def test_missing_tier_column_is_skipped_and_duplicates_collapse():
    report = storage_tier_report([
        {'$key': 1, 'tier': '1'},
        {'$key': 2, 'tier': 1},
        {'$key': 3, 'description': 'no number'},
        {'tier': None},
    ])
    assert report['storage_tiers'] == [1]
    assert len(report['storage_tier_details']) == 2


def test_tier_numbers_come_back_sorted():
    report = storage_tier_report([
        {'tier': 4},
        {'tier': 1},
        {'tier': 2},
    ])
    assert report['storage_tiers'] == [1, 2, 4]


def test_captured_nodes_keep_the_sizing_fields():
    nodes = shape_nodes(_fixture('nodes'))
    assert [node['name'] for node in nodes] == ['node1', 'node2']
    node1 = nodes[0]
    assert node1['ram'] == 94208
    assert node1['vm_ram'] == 68352
    assert node1['ram_used'] == 14601
    assert node1['cores'] == 32
    assert node1['physical'] is True
    assert node1['online'] is True
    assert node1['maintenance'] is False
    assert node1['cluster_name'] == 'cluster1'


def test_node_without_running_does_not_become_offline():
    node = shape_node({'name': 'mystery', 'vm_ram': 1024})
    assert node['online'] is None
    assert node['ram'] is None
    assert node['physical'] is None


def test_captured_cluster_headroom_matches_cluster_info():
    """online_ram 137472 - used_ram 9216, the captured cluster_status row."""
    status = _fixture('cluster_status')
    clusters = shape_clusters(_fixture('clusters'), status)
    assert len(clusters) == 1
    assert clusters[0]['name'] == 'cluster1'
    assert clusters[0]['ram_headroom_mb'] == 128256
    assert ram_headroom_mb(status) == 128256
    assert clusters[0]['online_ram'] == 137472
    assert clusters[0]['used_ram'] == 9216


def test_live_status_wins_over_the_cluster_row():
    clusters = shape_clusters(
        [{'$key': 1, 'name': 'cluster1', 'online_ram': 1, 'used_ram': 1}],
        [{'cluster': 1, 'online_ram': 100, 'used_ram': 40,
          'online_nodes': 2, 'total_nodes': 2,
          'online_cores': 8, 'used_cores': 1}],
    )
    assert clusters[0]['online_ram'] == 100
    assert clusters[0]['ram_headroom_mb'] == 60
    assert ram_headroom_mb([
        {'online_ram': 100, 'used_ram': 40},
    ]) == 60


def test_headroom_is_zero_when_status_is_empty():
    assert ram_headroom_mb([]) == 0
    assert ram_headroom_mb([{}]) == 0


def test_largest_node_is_the_online_physical_vm_ram():
    nodes = shape_nodes([
        {'name': 'small', 'vm_ram': 1000, 'ram': 9000, 'running': True,
         'physical': True, 'maintenance': False},
        {'name': 'big', 'vm_ram': 4000, 'ram': 9000, 'running': True,
         'physical': True, 'maintenance': False},
        {'name': 'bigger-but-virtual', 'vm_ram': 8000, 'running': True,
         'physical': False},
        {'name': 'down', 'vm_ram': 9000, 'running': False, 'physical': True},
        {'name': 'maint', 'vm_ram': 9000, 'running': True, 'physical': True,
         'maintenance': True},
    ])
    assert largest_node_vm_ram_mb(nodes) == 4000


def test_largest_node_prefers_vm_ram_when_nothing_is_marked_physical():
    """vm_ram wins on a node that has both. A node with only physical ram
    still counts, and the larger of those figures is the ceiling."""
    nodes = shape_nodes([
        {'name': 'a', 'vm_ram': 1024, 'ram': 4096, 'running': True},
        {'name': 'b', 'ram': 2048, 'running': True},
    ])
    assert largest_node_vm_ram_mb(nodes) == 2048


def test_largest_node_is_none_when_ram_was_not_reported():
    nodes = shape_nodes([{'name': 'a', 'running': True, 'physical': True}])
    assert largest_node_vm_ram_mb(nodes) is None


def test_census_counts_rows_and_keeps_duplicate_names():
    found = census([
        {'name': 'DMZ'},
        {'name': 'Core'},
        {'name': 'Core'},
        {'$key': 4},
    ])
    assert found['count'] == 4
    assert found['names'] == ['Core', 'Core', 'DMZ']


def test_identity_reads_the_client_properties():
    client = SimpleNamespace(
        os_version='26.1.8', version='26.1.8', cloud_name='example-lab',
        _connection=None)
    assert read_identity(client) == {
        'os_version': '26.1.8',
        'version': '26.1.8',
        'cloud_name': 'example-lab',
    }


def test_identity_falls_back_to_the_connection():
    client = SimpleNamespace(
        os_version=None, version='', cloud_name=None,
        _connection=SimpleNamespace(
            os_version='26.1.8', vergeos_version='26.1.8',
            cloud_name='example-lab'))
    assert read_identity(client) == {
        'os_version': '26.1.8',
        'version': '26.1.8',
        'cloud_name': 'example-lab',
    }
