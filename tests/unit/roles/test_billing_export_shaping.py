"""Shaping tests for the billing export (pure functions, no API).

Note what the previous version of this file asserted:

    'storage': [{'tier': 1, 'provisioned': 100, 'used': 40.5}, ...]
    assert r['provisioned_gb'] == 600

600 raw bytes reported as 600 GB. The test agreed with the code and both were
wrong, which is issue #27 -- the tenant CSV overstated storage by 1024**3 in
the artifact an invoicing pipeline consumes. A test that encodes the bug is
worse than no test: it defends it.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'billing_export', 'files'))
from billing_export import (  # noqa: E402
    GB,
    dedupe_periods,
    iso,
    shape_system,
    shape_tenants,
    to_gb,
)


def billing_row(frm=1782878400, to=1785556799, **over):
    row = {'$key': 1, 'from': frm, 'to': to, 'created': frm + 10,
           'total_nodes': 1, 'online_nodes': 1,
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

    def test_tier_columns_carry_their_unit_and_the_conversion(self):
        """#27: these were raw bytes under a name that said nothing.

        tier_1_total was 1998341734400 on a live system -- 1.82 TiB reported
        as a bare number in a column called `tier_1_total`.
        """
        row = shape_system([billing_row(tier_1_total=500 * GB,
                                        tier_1_used=120 * GB)], periods=1)[0]
        assert row['tier_1_total_gb'] == 500.0
        assert row['tier_1_used_gb'] == 120.0
        assert 'tier_1_total' not in row

    def test_ram_columns_say_they_are_mb(self):
        """Already MB on the platform side; the header did not say so."""
        row = shape_system([billing_row()], periods=1)[0]
        assert row['total_ram_mb'] == 94208
        assert row['used_ram_mb'] == 11000
        assert 'total_ram' not in row

    def test_every_row_is_traceable_to_a_platform_row(self):
        row = shape_system([billing_row(**{'$key': 7})], periods=1)[0]
        assert row['period_key'] == 7


class TestTheDuplicatedPeriod:
    """#27: the platform returns the same period twice.

    Measured on 26.1.8 -- the two rows share a `from` and differ only in the
    last second of `to`, plus one extra datapoint:

        $key 2  from 1785556800  to 1788235199  created 1788238471
        $key 3  from 1785556800  to 1788235200  created 1789036725

    Billing off both double-counts the month.
    """

    OPEN = dict(frm=1785556800, to=1788235200, created=1789036725,
                **{'$key': 3})
    CLOSED = dict(frm=1785556800, to=1788235199, created=1788238471,
                  **{'$key': 2})

    def test_one_row_per_period_by_default(self):
        rows = shape_system([billing_row(**self.OPEN),
                             billing_row(**self.CLOSED)], periods=12)
        assert len(rows) == 1

    def test_the_later_snapshot_wins(self):
        """`created` orders them; the later row carried one more datapoint,
        so it is the more complete account of the period."""
        rows = shape_system([billing_row(**self.CLOSED),
                             billing_row(**self.OPEN)], periods=12)
        assert rows[0]['period_key'] == 3

    def test_order_of_arrival_does_not_decide_it(self):
        forwards = shape_system([billing_row(**self.OPEN),
                                 billing_row(**self.CLOSED)], periods=12)
        backwards = shape_system([billing_row(**self.CLOSED),
                                  billing_row(**self.OPEN)], periods=12)
        assert forwards == backwards

    def test_distinct_periods_are_not_collapsed(self):
        """The dedupe must not turn into "one row, ever"."""
        rows = shape_system([billing_row(frm=100), billing_row(frm=200)],
                            periods=12)
        assert len(rows) == 2

    def test_duplicates_can_be_kept_deliberately(self):
        """For reconciling against the platform's own UI."""
        rows = shape_system([billing_row(**self.OPEN),
                             billing_row(**self.CLOSED)], periods=12,
                            keep_duplicates=True)
        assert len(rows) == 2

    def test_dedupe_counts_what_it_removed(self):
        removed = len([1, 2]) - len(dedupe_periods(
            [billing_row(**self.OPEN), billing_row(**self.CLOSED)]))
        assert removed == 1


class TestTenantShaping:
    def test_allocations_are_summed_and_converted(self):
        """The assertion that used to read `== 600` for 600 bytes."""
        rows = shape_tenants([{
            'row': {'name': 'acme', 'running': True},
            'nodes': [{'cpu_cores': 4, 'ram': 8192},
                      {'cpu_cores': 2, 'ram': 4096}],
            'storage': [{'tier_number': 1, 'provisioned': 100 * GB,
                         'used': 40 * GB},
                        {'tier_number': 3, 'provisioned': 500 * GB,
                         'used': 10 * GB}],
        }])
        r = rows[0]
        assert (r['tenant'], r['nodes']) == ('acme', 2)
        assert r['total_cores'] == 6
        assert r['total_ram_mb'] == 12288
        assert r['storage_tiers'] == '1,3'
        assert r['provisioned_gb'] == 600.0
        assert r['used_gb'] == 50.0

    def test_the_live_numbers_from_the_issue(self):
        """#27 quoted a real tenant. The CSV said 107374182400.0 GB; the same
        field through tenant_info said 100.0 GB. Two modules in this
        collection, one field, a factor of 1024**3."""
        r = shape_tenants([{
            'row': {'name': 'lab'},
            'storage': [{'tier_number': 1, 'provisioned': 107374182400,
                         'used': 294780928}],
        }])[0]
        assert r['provisioned_gb'] == 100.0
        assert r['used_gb'] == 0.27

    def test_the_conversion_is_the_one_tenant_info_uses(self):
        assert GB == 1073741824
        assert to_gb(107374182400) == 100.0

    def test_storage_tiers_reports_the_tier_number_not_its_key(self):
        """The `tier` COLUMN on tenant_storage holds the storage tier's KEY;
        the number lives in the `tier#tier as tier_number` join. They coincide
        on a single-tier system, which is exactly what lets this ship."""
        r = shape_tenants([{
            'row': {'name': 't'},
            'storage': [{'tier': 2, 'tier_number': 4, 'provisioned': GB}],
        }])[0]
        assert r['storage_tiers'] == '4'

    def test_it_falls_back_to_the_column_without_the_join(self):
        """An older projection carries no tier_number. Reporting nothing would
        be worse than reporting the key."""
        r = shape_tenants([{
            'row': {'name': 't'},
            'storage': [{'tier': 2, 'provisioned': GB}],
        }])[0]
        assert r['storage_tiers'] == '2'

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


class TestHeadersAndValuesAgree:
    """The defect in one sentence: a column called _gb holding bytes.

    Nothing structural stopped that, so this does -- every header that claims
    a unit must be produced by a function that applies it.
    """

    def test_no_gb_column_holds_a_byte_count(self):
        from billing_export import SYSTEM_FIELDS, TENANT_FIELDS
        one_gb_of_bytes = GB
        sys_row = shape_system([billing_row(tier_1_total=one_gb_of_bytes)],
                               periods=1)[0]
        ten_row = shape_tenants([{
            'row': {'name': 't'},
            'storage': [{'tier_number': 1, 'provisioned': one_gb_of_bytes,
                         'used': one_gb_of_bytes}],
        }])[0]
        for field in SYSTEM_FIELDS:
            if field.endswith('_gb'):
                assert sys_row[field] < 1024, (
                    '%s looks like a byte count: %s' % (field, sys_row[field]))
        for field in TENANT_FIELDS:
            if field.endswith('_gb'):
                assert ten_row[field] == 1.0, (
                    '%s did not convert: %s' % (field, ten_row[field]))

    def test_every_declared_column_is_actually_produced(self):
        from billing_export import SYSTEM_FIELDS, TENANT_FIELDS
        sys_row = shape_system([billing_row()], periods=1)[0]
        ten_row = shape_tenants([{'row': {'name': 't'}}])[0]
        assert set(SYSTEM_FIELDS) == set(sys_row)
        assert set(TENANT_FIELDS) == set(ten_row)
