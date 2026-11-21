# protect

Tag a VM `protect/<class>`, and it gets that class's snapshot profile. New VM
plus one tag equals protected on the next run.

This is the Nutanix-categories equivalent: protection stops being something
somebody has to remember to configure, and becomes a property of the VM.

## Classes

```yaml
protect_classes:
  gold:
    periods:
      - {name: Hourly, frequency: hourly, retention: 86400, quiesce: true}
      - {name: Daily,  frequency: daily,  retention: 604800, hour: 2}
  bronze:
    periods:
      - {name: Daily, frequency: daily, retention: 259200, hour: 3}
```

The role ensures, per class: the `protect` tag category exists and is
VM-taggable, a tag named for the class exists, and a snapshot profile
(`protect-<class>` unless you override `profile:`) exists with those periods.
Then it reconciles every tagged VM into the matching profile.

## A period can snapshot a tagged subset

Leave the tag keys off a period and it stays the all-or-nothing schedule
this role has always written. Set them when one cloud should keep a dev
tier and a production tier on different retention.

```yaml
protect_classes:
  gold:
    periods:
      - name: Daily
        frequency: daily
        retention: 604800
        hour: 2
        include_tags:
          - gold
        quiesce_tags:
          - gold
```

`include_tags` captures only VMs carrying those tags. `exclude_tags`
captures everything else. A period cannot set both. `quiesce_tags` freezes
disks on the VMs carrying those tags, and only alongside include or exclude.

Each entry is a tag name or a numeric tag key. A name that exists in two
categories is refused. The class tag this role creates is a natural include
list: VMs tagged `protect/gold` are the ones the gold schedule captures.
Any other tag name works the same way.

Taking the keys back out of the playbook does not clear a scope that is
already stored. Omission means the role does not manage that scope, which
is what keeps an older playbook from rewriting periods. To change the set,
write the new set.

This needs a pyVergeOS build that contains PR #200. That change is on
`dev` and is not in a release yet, so the collection floor stays
`pyvergeos>=1.2.8`. On an older SDK the snapshot profile task fails and
names the gap, instead of writing a field VergeOS would drop and then
reporting success.

`tests/live/verify-protect-partial.yml` is the ladder. It tags one `zz-`
VM, leaves another untagged, and checks the cloud snapshot. The untagged
VM must not be in it. Membership stays empty until `find_vms` runs
(pyVergeOS #201), and `create(wait=True)` can return while a `fields=all`
read still says the snapshot is building (pyVergeOS #202), so the ladder
polls that read before `find_vms`. The same `still building` refusal can
come back from `find_vms` after that read has settled, and
`cloud_snapshot_vms` can stay empty for several seconds after the POST.
The helper retries the refusal and polls until the read is non-empty, and
the ladder polls again before the assert. Released pyVergeOS is enough.
pyVergeOS #210 does the same wait inside `list()` and `get_vms()` and is
not in a release. The ladder does not call those methods.

## Orphans are always report-only

A VM that carries a managed profile but is **no longer tagged** is reported as
an orphan and **never unenrolled** — not even under `enforce`.

This asymmetry is deliberate. Adding protection is safe to automate: the worst
case is a VM gets snapshotted more than it needed. Removing protection is not:
the worst case is you silently stop protecting something, and find out during
a restore. So untagging tells you what changed and leaves the decision to a
person.

The ladder asserts this directly — it untags a VM, runs `enforce`, and checks
the profile is *still there*.

## Modes

**`report`** (default) shows what would change. With
`protect_fail_on_drift: true` it is a CI gate: a VM tagged but not enrolled
fails the play.

**`enforce`** performs the enrolment.

## Usage

```yaml
- role: vergeio.vergeos.protect
  vars:
    protect_classes:
      gold:
        periods:
          - {name: Daily, frequency: daily, retention: 604800, hour: 2}
    protect_mode: enforce
```

Then tag VMs — in the UI, through `vergeio.vergeos.tag`, or as part of
provisioning — and schedule this role.

## Relationship to `vm_backup`

`vm_backup` builds the backup *infrastructure*: the schedule, the export
volume, the NFS exposure. `protect` decides *which VMs* are on which schedule.
They compose: define the profile once in either, then let tags drive
membership.

## Results

**Live ladder** — `tests/live/verify-protect.yml`, passed on **VergeOS 26.1.8,
two nodes** (`ok=103 changed=9 failed=0`):

an empty class set refused → an untagged VM is invisible to the reconciler →
tagging produces drift → the CI gate fires → `enforce` enrols and the profile
reads back over raw HTTP → a converged `enforce` is a no-op → untagging
produces an orphan → **`enforce` does not strip the orphan's protection** →
zero leftovers.

The class is named `zz-gold`, so the ladder cannot collide with a real
protection class.

## The facts it sets

- `protect_result` — `mode`, `enrolled`, `pending`, `orphans`.
- `protect_to_enroll` / `protect_orphans` — the raw sets, if you want to
  branch on them.
