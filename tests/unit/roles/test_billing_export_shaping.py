#!/usr/bin/env python3
"""Shaping tests for the billing export (pure functions, no API)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'billing_export', 'files'))
from billing_export import shape_system, shape_tenants, iso  # noqa: E402


def billing_row(frm=1782878400, to=1785556799, **over):
    row = {'from': frm, 'to': to, 'total_nodes': 1, 'online_nodes': 1,
           'total_cores': 32, 'used_cores': 4, 'total_ram': 94208,
           'used_ram': 11000, 'running_machines': 3, 'gpus': 0, 'vgpus': 0}
    for t in range(6):
        row['tier_%d_total' % t] = 0
        row['tier_%d_used' % t] = 0
    row.update(over)
    return row


class TestSystemShaping:
    def test_periods_are_iso_and_newest_first(self):
        rows = shape_system([billing_row(frm=100, to=200),
                             billing_row(frm=300, to=400)], periods=12)
        assert rows[0]['period_from'] == iso(300)
        assert rows[1]['period_from'] == iso(100)

    def test_period_limit_applies(self):
        rows = shape_system([billing_row(frm=i) for i in range(20)],
                            periods=5)
        assert len(rows) == 5

    def test_tier_columns_present(self):
        row = shape_system([billing_row(tier_1_total=500, tier_1_used=120)],
                           periods=1)[0]
        assert row['tier_1_total'] == 500
        assert row['tier_1_used'] == 120


class TestTenantShaping:
    def test_allocations_are_summed(self):
        rows = shape_tenants([{
            'row': {'name': 'acme', 'running': True},
            'nodes': [{'cpu_cores': 4, 'ram': 8192},
                      {'cpu_cores': 2, 'ram': 4096}],
            'storage': [{'tier': 1, 'provisioned': 100, 'used': 40.5},
                        {'tier': 3, 'provisioned': 500, 'used': 10}],
        }])
        r = rows[0]
        assert (r['tenant'], r['nodes']) == ('acme', 2)
        assert r['total_cores'] == 6
        assert r['total_ram_mb'] == 12288
        assert r['storage_tiers'] == '1,3'
        assert r['provisioned_gb'] == 600
        assert r['used_gb'] == 50.5

    def test_empty_subresources_are_zero(self):
        r = shape_tenants([{'row': {'name': 'bare', 'running': False}}])[0]
        assert r['nodes'] == 0 and r['provisioned_gb'] == 0
        assert r['storage_tiers'] == ''

    def test_sorted_by_name(self):
        rows = shape_tenants([{'row': {'name': 'zeta'}},
                              {'row': {'name': 'alpha'}}])
        assert [r['tenant'] for r in rows] == ['alpha', 'zeta']

    def test_alt_core_field_name(self):
        r = shape_tenants([{'row': {'name': 't'},
                            'nodes': [{'cores': 8, 'ram': 1024}]}])[0]
        assert r['total_cores'] == 8
