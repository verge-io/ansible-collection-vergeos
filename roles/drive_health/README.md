# drive_health

SMART and vSAN triage for the physical drives in a VergeOS cluster, producing a
replacement list and a do-not-pull list.

**Read-only.** Every other decision this collection makes is reversible;
pulling a drive is not, so this role reports and stops.

## Usage

```yaml
- hosts: localhost
  gather_facts: false
  roles:
    - role: vergeio.vergeos.drive_health
```

As a scheduled check that should page someone:

```yaml
    - role: vergeio.vergeos.drive_health
      vars:
        drive_health_fail_on_critical: true
```

## What it reports

```
counts:                 {ok: 22, info: 1, warning: 2, critical: 1}
replace_now:            [[node3, S3Z1NB0K]]
watch:                  [[node1, S3Z1NB2X], [node2, S3Z1NB44]]
rebuilding_do_not_pull: [S3Z1NBQ7]
```

`rebuilding_do_not_pull` is separate from the severities on purpose. It does
not say a drive is *wrong*; it says what is **unsafe right now**. A drive that
vSAN is rebuilding onto is usually perfectly healthy — it may have just been
replaced — but pulling a second drive mid-rebuild is how a rebuild becomes a
data-loss event.

Every entry names the **node** as well as the serial. There is no node column
on a drive row, and a drive's `location` is its slot on its own node (`nvme0`),
which repeats across nodes. On a cluster of identical hardware the serial and
the node are the only things that tell two drives apart.

## Triage policy

The groupings are the conventional reading of these SMART attributes, **not**
platform policy, which is why all of them are overridable.

| Severity | Flags | Why |
|---|---|---|
| `critical` | `realloc_sectors_warn`, `current_pending_sector_warn`, `offline_uncorrectable_warn` | The drive could not read or write something and had to do something about it. These do not improve on their own. |
| `warning` | `wear_level_warn`, `temp_warn` | Real and actionable, but plan-a-replacement and check-the-airflow respectively. A hot drive is often the rack's problem rather than the drive's. |
| `info` | `hours_warn` | Age alone is not a fault. Treating power-on hours as a warning makes every long-lived healthy drive shout. |

Two things outrank the table:

- **vSAN IO errors are always critical**, ahead of every SMART flag. A SMART
  warning is the drive's own *prediction*; a vSAN read or write error is the
  platform reporting that an operation actually *failed*. The measurement wins.
- **SMART being disabled reports as `info`, not `ok`.** The flags above are all
  silent on such a drive, so it reads as healthy whether it is or not. That is
  worth saying out loud rather than counting as a pass.

On an all-flash tier where wear-out *is* the failure mode, promote it:

```yaml
    - role: vergeio.vergeos.drive_health
      vars:
        drive_health_critical_flags:
          - realloc_sectors_warn
          - current_pending_sector_warn
          - offline_uncorrectable_warn
          - wear_level_warn
```

## Variables

See `defaults/main.yml`; every option is documented there and in
`meta/argument_specs.yml`. Connection details fall back to `VERGEOS_HOST`,
`VERGEOS_USERNAME`, `VERGEOS_PASSWORD` and `VERGEOS_INSECURE`.

| Variable | Default | |
|---|---|---|
| `drive_health_node` | `""` | Limit to one node, by name. Empty scans the cluster. |
| `drive_health_critical_flags` | sector failures | Flags that mean *replace this*. |
| `drive_health_warning_flags` | wear, temperature | Flags that mean *watch this*. |
| `drive_health_fail_on_critical` | `false` | Fail the play when a drive needs replacing. |
| `drive_health_fail_on_warning` | `false` | Fail on warnings too. Noisier — a warm drive trips it. |

## See also

`vergeio.vergeos.physical_drive_info` is the module underneath, if you want the
triaged rows without the report and the gates.
