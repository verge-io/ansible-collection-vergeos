# vm_backup

One policy document, four enforced layers of a backup configuration.

| Layer | Module | What it sets |
|---|---|---|
| snapshot schedule | `snapshot_profile` | when snapshots are taken, how long they live |
| export target | `nas_volume` | where exports land |
| exposure | `nas_nfs_share` | how external backup tooling reaches them |
| export config | `vm_export` | what gets exported, how many generations |

Every layer except the volume is optional. Omitting a section leaves that
layer **unmanaged**, rather than resetting it to a default — so this role can
be adopted one layer at a time.

## Honest scope

VergeOS has no third-party backup certification. This role is the mitigation,
and it is a real one: snapshot on a schedule → export to a NAS volume →
expose read-only over NFS → let Veeam, Commvault, or anything else that can
read an NFS share ingest from there. That gets you 3-2-1 without vendor
support. It does not get you a certified integration, and nothing here
pretends otherwise.

Two things this role deliberately does **not** do:

- **Attach the snapshot profile to individual VMs.** That belongs with the VM
  definition, or with the `protect` role, which enrols VMs by tag.
- **Verify that a backup restores.** That is `restore_drill`. A backup you
  have never restored is a hypothesis, and the two halves are separate roles
  on purpose so you cannot accidentally believe you have the second one.

## The empty-allowed_hosts rule

`vm_backup_nfs_share.allowed_hosts` empty means **no share is created**. An
export volume reachable by everything on the network is not a backup target,
it is a liability, so the role will not produce that by omission. Name the
hosts, or get no share. The ladder asserts this.

## Usage

```yaml
- role: vergeio.vergeos.vm_backup
  vars:
    vm_backup_nas_service: nas1
    vm_backup_volume: backups
    vm_backup_volume_size_gb: 500
    vm_backup_nfs_share:
      name: backups
      allowed_hosts: ["10.0.0.20"]     # the backup server
      data_access: ro
    vm_backup_export:
      max_exports: 7
      run_now: false
```

`run_now: true` runs an export during the play. Useful for a first
seed; not something you want on a schedule that already has one.

## Results

**Live ladder** — `tests/live/verify-vm-backup.yml`, passed on **VergeOS
26.1.8, two nodes** (`ok=47 changed=10 failed=0`):

all four layers created → idempotent re-run with nothing changed → retention
drift corrected while the volume was left alone → an empty `allowed_hosts`
skipped the share rather than widening it → full teardown, and deleting the
volume a second time was a no-op.

Run it with `-e nas_service=<a NAS service>`. Everything it creates is `zz-*`.
