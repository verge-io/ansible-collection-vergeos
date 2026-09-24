"""VergeOS billing export — system billing periods + per-tenant usage.

Reads the system `billing` records (period-scoped capacity/usage the
platform already generates) and a per-tenant allocation/usage snapshot
(tenant rows + their node and storage sub-resources), and writes two
CSVs for invoicing/chargeback pipelines. Read-only against the API.

Usage:
  billing_export.py --dest DIR [--periods N]      # VERGEOS_* env auth
  billing_export.py --dest DIR --fixture f.json   # offline (tests)

Prints a JSON summary on stdout: {system_rows, tenant_rows, files}.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

GB = 1073741824

# Every column name carries its unit, because the artifact this writes is
# consumed by an invoicing pipeline that cannot ask what `tier_1_total` meant.
#
# Issue #27: the tenant CSV published raw BYTE counts under headers ending
# _gb, overstating storage by 1,073,741,824. Two modules in this collection
# read the same field and disagreed by 1024**3 -- tenant_info converts, this
# did not. The system CSV had no units at all: tier_N_total was bytes and
# total_ram was MB, under names that said neither.
#
# Converting (rather than renaming the columns to _bytes) is the choice that
# makes this file agree with tenant_info, which already had the constant and
# the rounding. Disagreeing with the rest of the collection is what made the
# bug possible.
SYSTEM_FIELDS = [
    'period_key', 'period_from', 'period_to', 'total_nodes', 'online_nodes',
    'total_cores', 'used_cores', 'total_ram_mb', 'used_ram_mb',
    'running_machines', 'gpus', 'vgpus',
] + ['tier_%d_%s_gb' % (t, k) for t in range(6) for k in ('total', 'used')]

TENANT_FIELDS = [
    'tenant', 'running', 'nodes', 'total_cores', 'total_ram_mb',
    'storage_tiers', 'provisioned_gb', 'used_gb',
]


def to_gb(value):
    """Bytes to GB, two decimals -- the same conversion as tenant_info."""
    return round(int(value or 0) / float(GB), 2)


def iso(ts):
    if not ts:
        return ''
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def dedupe_periods(billing_rows):
    """One row per billing period.

    Issue #27: the platform returns the same period twice. Measured on 26.1.8,
    two rows share a `from` and differ only in `to` -- 04:00:00 against
    03:59:59 -- and in one extra datapoint:

        $key 2  from 1785556800  to 1788235199  storage_datapoints 2080
        $key 3  from 1785556800  to 1788235200  storage_datapoints 2081

    There is no state column to tell them apart, so `from` is the period's
    identity and the later `created` is the more complete snapshot. Billing
    off the undeduplicated CSV double-counts a month.

    Deduplicating by default rather than adding a flag column: this file feeds
    invoicing, and a duplicate that a consumer has to know to filter is a
    silent overcharge waiting for someone not to read the docs. The superseded
    rows are still reachable with keep_duplicates, and `period_key` makes
    every row traceable back to the platform.
    """
    best = {}
    for row in billing_rows:
        start = row.get('from') or 0
        current = best.get(start)
        if current is None or (row.get('created') or 0) > (current.get('created') or 0):
            best[start] = row
    return list(best.values())


def shape_system(billing_rows, periods, keep_duplicates=False):
    if not keep_duplicates:
        billing_rows = dedupe_periods(billing_rows)
    rows = sorted(billing_rows, key=lambda r: r.get('from') or 0,
                  reverse=True)[:periods]
    out = []
    for r in rows:
        row = {'period_key': r.get('$key'),
               'period_from': iso(r.get('from')),
               'period_to': iso(r.get('to')),
               'total_nodes': r.get('total_nodes'),
               'online_nodes': r.get('online_nodes'),
               'total_cores': r.get('total_cores'),
               'used_cores': r.get('used_cores'),
               # Already MB on the platform side -- named so here, where it
               # used to say only `total_ram`.
               'total_ram_mb': r.get('total_ram'),
               'used_ram_mb': r.get('used_ram'),
               'running_machines': r.get('running_machines'),
               'gpus': r.get('gpus'), 'vgpus': r.get('vgpus')}
        for t in range(6):
            row['tier_%d_total_gb' % t] = to_gb(r.get('tier_%d_total' % t))
            row['tier_%d_used_gb' % t] = to_gb(r.get('tier_%d_used' % t))
        out.append(row)
    return out


def shape_tenants(tenants):
    """tenants: [{row, nodes: [...], storage: [...]}] — plain dicts."""
    out = []
    for t in tenants:
        row = t['row']
        nodes = t.get('nodes') or []
        storage = t.get('storage') or []
        out.append({
            'tenant': row.get('name'),
            'running': bool(row.get('running')),
            'nodes': len(nodes),
            'total_cores': sum(int(n.get('cpu_cores') or n.get('cores') or 0)
                               for n in nodes),
            'total_ram_mb': sum(int(n.get('ram') or 0) for n in nodes),
            # `tier_number` is the join; the `tier` COLUMN holds the storage
            # tier's KEY. They happen to coincide on a single-tier system,
            # which is what makes this the kind of thing that ships. Fall back
            # to the column only when the projection did not carry the join.
            'storage_tiers': ','.join(
                str(s.get('tier_number', s.get('tier')))
                for s in storage
                if s.get('tier_number', s.get('tier')) is not None),
            # Issue #27. These were raw byte sums under _gb headers.
            'provisioned_gb': to_gb(sum(int(s.get('provisioned') or 0)
                                        for s in storage)),
            'used_gb': to_gb(sum(int(s.get('used') or 0) for s in storage)),
        })
    return sorted(out, key=lambda r: r['tenant'] or '')


def write_csv(path, fields, rows):
    with open(path, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in fields})


def collect():
    import urllib3
    urllib3.disable_warnings()
    from pyvergeos import VergeClient

    kwargs = {'host': os.environ['VERGEOS_HOST'],
              'verify_ssl': os.environ.get('VERGEOS_INSECURE', '') not in
              ('1', 'true', 'True')}
    if os.environ.get('VERGEOS_TOKEN'):
        kwargs['token'] = os.environ['VERGEOS_TOKEN']
    else:
        kwargs['username'] = os.environ['VERGEOS_USERNAME']
        kwargs['password'] = os.environ['VERGEOS_PASSWORD']
    client = VergeClient(**kwargs)

    billing_rows = [dict(r) for r in client.billing.list()]
    tenants = []
    for row in (dict(t) for t in client.tenants.list()):
        key = row.get('$key')
        entry = {'row': row, 'nodes': [], 'storage': []}
        try:
            entry['nodes'] = [dict(n) for n in client.tenants.nodes(key)]
        except Exception:
            pass
        try:
            entry['storage'] = [dict(s) for s in client.tenants.storage(key)]
        except Exception:
            pass
        tenants.append(entry)
    return billing_rows, tenants


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dest', required=True, help='output directory')
    p.add_argument('--periods', type=int, default=12,
                   help='most-recent system billing periods to export')
    p.add_argument('--fixture', help='JSON file {billing, tenants} - '
                   'offline run for tests')
    p.add_argument('--keep-duplicate-periods', action='store_true',
                   help='emit every billing row the platform returns, '
                        'including the superseded copy of a period. Off by '
                        'default: billing off a duplicated period '
                        'double-counts the month (#27).')
    args = p.parse_args()

    if args.fixture:
        with open(args.fixture) as fh:
            state = json.load(fh)
        billing_rows, tenants = state['billing'], state['tenants']
    else:
        billing_rows, tenants = collect()

    os.makedirs(args.dest, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime('%Y%m%d')
    sys_rows = shape_system(billing_rows, args.periods,
                            keep_duplicates=args.keep_duplicate_periods)
    ten_rows = shape_tenants(tenants)

    sys_path = os.path.join(args.dest, 'vergeos-billing-system-%s.csv' % stamp)
    ten_path = os.path.join(args.dest, 'vergeos-billing-tenants-%s.csv' % stamp)
    write_csv(sys_path, SYSTEM_FIELDS, sys_rows)
    write_csv(ten_path, TENANT_FIELDS, ten_rows)

    json.dump({'system_rows': len(sys_rows), 'tenant_rows': len(ten_rows),
               'periods_deduplicated':
                   len(billing_rows) - len(dedupe_periods(billing_rows)),
               'files': [sys_path, ten_path]}, sys.stdout, indent=2)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
