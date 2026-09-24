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

## The empty-allowed_hosts rule, and what it does not do

`vm_backup_nfs_share.allowed_hosts` empty means **no share is created**. An
export volume reachable by everything on the network is not a backup target,
it is a liability, so the role will not produce that by omission.

It does **not** mean "there is no share". Skipping a task does not revoke
anything, and the role used to say otherwise. Measured on 26.1.8 — apply the
policy with a host listed, re-apply with the list emptied, and the platform
still reports

```
zz-probe-share   allowed_hosts "192.0.2.10"   data_access ro
```

while the run's own summary read `NFS (no share)`. The volume was still
exported to that host and the role said it was not. The shipped ladder
asserted the share task had been *skipped* and called that correct, which it
is, and which is not the same question.

`state` settles it, because the two readings of an empty list are opposites:

| `state` | empty `allowed_hosts` means |
|---|---|
| `present` | this run does not manage the share; an existing one is left exactly as it is |
| `absent` | revoke it — idempotent, so it is safe to leave declared |

With an empty list and **no** `state`, the role refuses rather than guess.
The summary now reports which of these happened instead of describing the
declaration.

## A tier has to exist

`vm_backup_volume_tier` names a storage tier. The platform range-checks the
number (1–5) and does not check that the tier is *present*, so naming a
missing one is accepted, reads back, and is never honoured. The `tier_policy`
role reports exactly this class of thing; check `storage_tiers` before
changing it.

## Usage

```yaml
- role: vergeio.vergeos.vm_backup
  vars:
    vm_backup_nas_service: nas1
    vm_backup_volume: backups
    vm_backup_volume_size_gb: 500
    vm_backup_nfs_share:
      name: backups
      allowed_hosts: ["192.0.2.20"]    # the backup server
      data_access: ro
      state: present
    vm_backup_export:
      max_exports: 7
      run_now: false
```

`run_now: true` runs an export during the play. Useful for a first
seed; not something you want on a schedule that already has one.

## Results

- **Live ladder** — `tests/live/verify-vm-backup.yml`, passed on **VergeOS
  26.1.8, two nodes** (`ok=84 changed=10 failed=0`):

  all four layers created → idempotent re-run with nothing changed →
  retention drift corrected while the volume was left alone → an empty
  `allowed_hosts` skipped the share rather than widening it → **and, read
  back from the platform, revoked nothing either** → an ambiguous
  declaration is refused → `state: absent` really revokes → revoking twice
  is a no-op → full teardown, and deleting the volume a second time was a
  no-op.

- **Role → module argument contract** — `tests/unit/test_role_module_arguments.py`
  resolves every parameter these tasks pass against the module's real
  argument spec. Ansible catches a bad one at runtime, but only when the
  task runs, and a revocation path is not a task that runs often.

Run it with `-e nas_service=<a NAS service>`. Everything it creates is `zz-*`.
