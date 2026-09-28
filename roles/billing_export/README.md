# billing_export

Read-only usage export for invoicing and chargeback pipelines. Two
date-stamped CSVs per run; point a schedule at it and ingest them downstream.

## What it exports

| File | Contents |
|---|---|
| `vergeos-billing-system-YYYYMMDD.csv` | the platform's own billing period records — capacity and usage per period, per storage tier |
| `vergeos-billing-tenants-YYYYMMDD.csv` | a per-tenant allocation snapshot, summed from each tenant's node and storage sub-resources |

The system half is the platform's own accounting, not a reconstruction. The
tenant half is a **snapshot at run time**, not a period aggregate — if you
need per-period tenant history, schedule this and keep the files.

## Read-only

Nothing on the platform is modified. The only thing this role writes to is the
controller's filesystem, which is why the export task is `changed_when: true`
while everything else in the role is not.

## Usage

```yaml
- hosts: localhost
  roles:
    - role: vergeio.vergeos.billing_export
      vars:
        billing_export_dest: /var/lib/chargeback
        billing_export_periods: 24
```

Re-running on the same day overwrites that day's files rather than
accumulating duplicates — verified in the ladder.

## Results

- **7 shaping unit tests** — `tests/unit/roles/test_billing_export_shaping.py`
- **Live ladder** — `tests/live/verify-billing-export.yml`, passed on
  **VergeOS 26.1.8, two nodes** (`ok=25 changed=3 failed=0`): live export →
  both CSVs exist, are non-empty, and carry a header row → a second run on the
  same day leaves exactly two files → scratch directory removed.

## The fact it sets

`billing_export_result`:

```yaml
system_rows: 12
tenant_rows: 3
files:
  - /var/lib/chargeback/vergeos-billing-system-20260921.csv
  - /var/lib/chargeback/vergeos-billing-tenants-20260921.csv
```
