# image_pipeline

A local qcow2 in, a versioned golden template VM out.

## The pipeline

```
local qcow2
  -> minimal OVA (no NICs)
  -> media-catalog upload   (file, idempotent by size)
  -> vm_import              (waits; the VM lands stopped)
  -> spec enforcement       (vm)
  -> golden snapshot        (vm_snapshot)
```

Consumers clone from the snapshot and attach their own NICs and cloud-init.
`lb_stack` and `k3s_node` both work this way.

## An existing template is never rebuilt

If a VM with the template name already exists, the whole build block is
skipped and the run is a zero-change no-op.

That is deliberate. Somebody's VMs were cloned from that template; silently
replacing it underneath them would be the worst kind of helpful. **Version by
name** — `web-golden-v2`, `web-golden-v3` — so a rebuild is an explicit new
artefact and the old one stays available to roll back to.

## No NICs in the template

The OVF carries no network devices. A golden template with a network binding
drags that binding into every clone, and you find out when the first VM lands
on the wrong VLAN.

Two things check it now. The live ladder asserts the *imported* template has
zero NICs, which needs a cluster, an upload and an import to find out.
`tests/unit/roles/test_image_pipeline_ovf.py` renders the OVF and reads it
back as XML in milliseconds: no `NetworkSection`, no `ResourceType` 10 item,
the disk capacity is the image's **virtual** size rather than its file size,
and memory carries `byte * 2^20` so a consumer cannot read 2048 MB as 2048
bytes.

It also found this: the template did not escape its interpolations, so a
template named `R&D-golden` produced an OVA that packed and uploaded fine and
failed at import with an XML parse error about a file nobody wrote. Same
family as issue #32, apostrophes and braces in catalog names. Escaped now,
and the packed filename still matches — a consumer unescapes `ovf:href` back
to the real name.

## Check mode

`--check` reports what it would pack, upload and import, and builds nothing.

It did not, before: `tempfile` does not support check mode, so the working
directory was skipped and the next task templated its `.path` anyway, and the
run died inside Ansible's own templating with

```
Error while resolving value for 'argv' ...
object of type 'dict' has no attribute 'path'
```

A pipeline that uploads things and imports VMs is exactly what somebody
dry-runs first. Being skipped is now how the role *learns* it must not build
— gating on that rather than on `ansible_check_mode`, which reports the
`--check` flag and would read false for a caller wrapping this role in a
`check_mode: true` block.

## Usage

```yaml
- role: vergeio.vergeos.image_pipeline
  vars:
    image_pipeline_src: /var/cache/images/debian-13-genericcloud.qcow2
    image_pipeline_template_name: debian13-golden
    image_pipeline_cores: 2
    image_pipeline_ram_mb: 2048
```

Requires `qemu-img` and `tar` on the controller.

A Packer build can feed `image_pipeline_src`, but is not required — anything
that produces a qcow2 will do.

## Known limit

**Catalog and recipe publication is not covered.** Publishing a template as a
recipe is a catalog-plus-repository object graph rather than a single call,
and needs its own module. This role gets you a template and a snapshot to
clone from, which is what the other roles here consume.

## Results

- **20 OVF unit tests** — `tests/unit/roles/test_image_pipeline_ovf.py`
- **Live ladder** — `tests/live/verify-image-pipeline.yml`, passed on
  **VergeOS 26.1.8, two nodes** (`ok=72 changed=13 failed=0`):

  a missing source image refused → qcow2 packed, uploaded, imported as a
  **stopped** template with the declared cores and RAM → golden snapshot
  taken → **template carries no NICs** → a re-run is a zero-change no-op
  with no duplicate import, and says so both ways round → **check mode
  builds nothing and does not claim it did** → zero leftovers.

The ladder builds a 1 GB sparse qcow2 rather than pulling a distro image: the
pipeline is qcow2 → OVA → upload → import → snapshot, and none of that cares
whether the disk is bootable. It exercises every step in seconds.

## The fact it sets

`image_pipeline_result`:

| key | meaning |
|---|---|
| `template` | the template VM's name |
| `media` | the OVA's name in the media catalog |
| `created` | this run built it |
| `already_present` | it was there before this run started |

`created` describes what the **run** did, not what the cluster contains —
which matters because a caller keys off it to decide whether to publish. A
check-mode run reports both as false: it built nothing, and it found nothing.

One consequence worth knowing: `set_fact` facts outlive the role, so the
role resets its own build flag on entry. Without that, a second
`include_role` in the same play inherits the first run's `created: true` and
reports a rebuild that never happened — which is the one claim this role
exists never to make. Measured.
