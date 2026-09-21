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

SYSTEM_FIELDS = [
    'period_from', 'period_to', 'total_nodes', 'online_nodes',
    'total_cores', 'used_cores', 'total_ram', 'used_ram',
    'running_machines', 'gpus', 'vgpus',
] + ['tier_%d_%s' % (t, k) for t in range(6) for k in ('total', 'used')]

TENANT_FIELDS = [
    'tenant', 'running', 'nodes', 'total_cores', 'total_ram_mb',
    'storage_tiers', 'provisioned_gb', 'used_gb',
]


def iso(ts):
    if not ts:
        return ''
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def shape_system(billing_rows, periods):
    rows = sorted(billing_rows, key=lambda r: r.get('from') or 0,
                  reverse=True)[:periods]
    out = []
    for r in rows:
        row = {'period_from': iso(r.get('from')),
               'period_to': iso(r.get('to')),
               'total_nodes': r.get('total_nodes'),
               'online_nodes': r.get('online_nodes'),
               'total_cores': r.get('total_cores'),
               'used_cores': r.get('used_cores'),
               'total_ram': r.get('total_ram'),
               'used_ram': r.get('used_ram'),
               'running_machines': r.get('running_machines'),
               'gpus': r.get('gpus'), 'vgpus': r.get('vgpus')}
        for t in range(6):
            row['tier_%d_total' % t] = r.get('tier_%d_total' % t)
            row['tier_%d_used' % t] = r.get('tier_%d_used' % t)
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
            'storage_tiers': ','.join(str(s.get('tier'))
                                      for s in storage if s.get('tier')
                                      is not None),
            'provisioned_gb': sum(float(s.get('provisioned') or 0)
                                  for s in storage),
            'used_gb': sum(float(s.get('used') or 0) for s in storage),
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
    p.add_argument('--fixture', help='JSON file {billing, tenants} — '
                   'offline run for tests')
    args = p.parse_args()

    if args.fixture:
        with open(args.fixture) as fh:
            state = json.load(fh)
        billing_rows, tenants = state['billing'], state['tenants']
    else:
        billing_rows, tenants = collect()

    os.makedirs(args.dest, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime('%Y%m%d')
    sys_rows = shape_system(billing_rows, args.periods)
    ten_rows = shape_tenants(tenants)

    sys_path = os.path.join(args.dest, 'vergeos-billing-system-%s.csv' % stamp)
    ten_path = os.path.join(args.dest, 'vergeos-billing-tenants-%s.csv' % stamp)
    write_csv(sys_path, SYSTEM_FIELDS, sys_rows)
    write_csv(ten_path, TENANT_FIELDS, ten_rows)

    json.dump({'system_rows': len(sys_rows), 'tenant_rows': len(ten_rows),
               'files': [sys_path, ten_path]}, sys.stdout, indent=2)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
