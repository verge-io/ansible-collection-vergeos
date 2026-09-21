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
on the wrong VLAN. The ladder asserts the imported template has zero NICs.

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

**Live ladder** — `tests/live/verify-image-pipeline.yml`, passed on
**VergeOS 26.1.8, two nodes** (`ok=52 changed=13 failed=0`):

a missing source image refused → qcow2 packed, uploaded, imported as a
**stopped** template with the declared cores and RAM → golden snapshot taken
→ **template carries no NICs** → a re-run is a zero-change no-op with no
duplicate import → zero leftovers.

The ladder builds a 1 GB sparse qcow2 rather than pulling a distro image: the
pipeline is qcow2 → OVA → upload → import → snapshot, and none of that cares
whether the disk is bootable. It exercises every step in seconds.

## The fact it sets

`image_pipeline_result` — `template`, `media`, and `created` (false when the
template already existed).
