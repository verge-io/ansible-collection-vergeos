# Fully unattended VergeOS install and post-install with Ansible

End-to-end workflow: seed the VergeOS installer so it asks zero questions,
then converge the freshly installed system with this collection. Tags are
the baseline this tree ships. Users, networks, and the rest of a first-boot
click-through are a site decision and stay out of the example.

Verified live on **VergeOS 26.1.8** (installer internals confirmed against
the extracted `verge-io-install-26.1.8.iso`: `usr/bin/yb-install`, the
`usr/lib/ybos/install/????-*` components, and the initrd's `9900-Live` boot
script). Nested acceptance run 2026-09-21 on a 2-node 26.1.8 cloud: seeded
install completed with zero prompts, the new system came up on DHCP, the
seeded admin login verified, and `post_install.yml` converged (run 1
`changed=2`, runs 2 and 3 `changed=0`). Objects were confirmed by API
read-back on the new system.

The seeds target that 26.1.8 installer layout. A later installer can move
`/usr/lib/ybos/install/` components; re-check the `sed` and `truncate` lines
in the templates before pointing them at a different ISO.

---

## 1. How the installer consumes a seed

`yb-install` collects its answers from four places, in order. Later plain
assignments override earlier ones.

| # | Vector | Mechanics |
|---|--------|-----------|
| 1 | **Kernel cmdline** | any `YC_*=value` parameter is exported; `yb.install` / `yb.install=advanced` selects the mode |
| 2 | **`install-seed` on the boot media root** | the initrd copies it to `/etc/yottabyte/install-seed`; `yb-install` sources it as bash. `init-hook`, `post-init-hook`, `first-boot`, `install-settings` on the media root are honored the same way |
| 3 | **`yb.installseedurl=<URL>`** | the initrd downloads the seed over HTTP (`wget -T 15`). A downloaded seed wins over one on the media. This is the path for a PXE or netinstall fleet |
| 4 | **Volume labeled `CIDATA` or `CONFIG-2`** | `yb-install` mounts it and sources `user-data` (also `openstack/latest/user_data`, `cloud-init/data`) **as bash**, and only when the first line is not `#cloud-config` |

Every installer dialog checks its `YC_*` variable first and prompts only
when it is unset. A complete seed is a silent install. A partial seed
prompts for the remainder.

Because the seed is sourced bash, it can also stage work: write extra
install hooks into `/usr/lib/ybos/install/` (they run in lexical order),
or drop a script at `/etc/yottabyte/run-with-appserver` to run API calls
against the brand-new system at the end of the install.

**Vector 4 is the workhorse for a nested install.** VergeOS cloud-init
(`cloudinit_datasource: nocloud` plus a `user-data` file) presents a CIDATA
volume to a VM, so the platform delivers the seed and the ISO does not have
to be remastered. For a physical install, put `install-seed` on the USB or
ISO root (vector 2), serve it over HTTP (vector 3), or attach a small
CIDATA-labeled ISO next to the installer.

## 2. The YC_* answer set (26.1.8)

Minimum for a silent single-node (controller, new system) install:

```bash
YC_INSTALL=basic
YC_INSTALL_TYPE=controller        # controller|scale-out|compute|pxe|replace
YC_VSAN_NEW=1                     # 1 = new system; 0 = join (see below)
YC_CLOUD_NAME=vlab
YC_HOSTNAME=node1
YC_USER_NAME=admin
YC_USER_PASSWORD=...              # >= 8 chars; ' " + are rejected
YC_USER_EMAIL=you@example.com
YC_NTP_SERVERS='0.pool.ntp.org 1.pool.ntp.org'
YC_NO_TIME=1                      # skip the date/time dialogs
YC_SKIP_IPMI_CONFIG=1
YC_LICENSE_TYPE=trial             # basic mode defaults to trial anyway
YC_UPDATES_USER_SKIP=1            # skip license-server credential prompts
YC_UPDATES_PASSWORD_SKIP=1

# Explicit lists skip the drive checklist and the
# "manually set the tier?" prompt (that prompt fires when the tier list is empty)
YC_DRIVE_LIST='/dev/sda /dev/sdb /dev/sdc /dev/sdd'
YC_VSAN_TIER_LIST='0 0 1 1'       # or 'auto auto auto auto'
YC_VSAN_ENCRYPTION=0
YC_CLUSTER_SWAP_TIER=-1           # "none", skips the swap-tier menu
```

Network seeding on 26.x is switch-based (`YC_NET_SWITCH<n>_*`, with
`_DONE=1` marking each switch complete, then `YC_NET_CORE` / `YC_NET_DMZ` /
`YC_NET_EXTERNAL1_*` group assignments). The working template is
`examples/unattended_install/templates/user-data-node1.sh.j2`: three
switches (External, Core 1, Core 2), DHCP on External, no bonding.

Measured on 26.1.8:

- **NIC names follow the machine type.** q35 yields `enp1s0` and neighbors;
  i440fx yields `enp0sN`; older platforms yield `enp1s1` and up. A hardcoded
  name that matches nothing parks the installer at the NIC picker, with no
  error anywhere. The template detects NICs in PCI-bus order at seed
  runtime. Creation order is PCI order, so NIC 1, 2, and 3 keep their roles
  on any machine type.
- **Timezone:** `0020-timezone` re-reads `/etc/timezone`. The seed writes
  `echo "$TZ" > /etc/timezone` rather than relying on `YC_TIMEZONE` alone.
- **DHCP external** needs `YC_NET_EXTERNAL1_HOSTNAME` and
  `YC_NET_EXTERNAL1_DOMAINNAME` both non-empty, or those two dialogs appear.
- **No `802.3ad` in a nested seed.** LACP will not negotiate on a vSwitch.
- The 4.x `YC_NET_AUTODETECT` path is absent from 26.x.

## 3. Nested lab deployment (the platform delivers the seed)

`examples/unattended_install/deploy_nested_lab.yml` builds the lab on an
existing cluster:

1. Two **core-fabric networks**: internal, MTU 9142, no router IP, no DHCP.
   `network` can set MTU, DHCP, and power, and it has no `ipaddress_type`,
   so the create goes through the API with `ipaddress_type: none`. Power-on
   uses the `network` module (`state: running`).
2. Node VMs: 8 cores and 16 GB by default, UEFI (`bios_type: uefi`, which
   the `vm` module stores as `uefi`), tier 0 and tier 1 virtio-scsi disks,
   the installer ISO in a CD-ROM, NICs ordered **External, Core 1, Core 2**.
   The seed detects guest names in PCI order. Only the creation order
   matters. Additional NICs use the `nic` module, which matches a NIC by
   its network, so the second and third NIC are added rather than
   retargeting the first.
3. `nested_virtualization`, `disable_hypervisor`, and `guest_agent` are not
   `vm` module options. The playbook sets them with one PUT after create.
   The host cluster must have `kvm_nested: true` (Cluster, Edit, Nested
   Virtualization). That change needs a rolling node reboot (nodes flag
   `restart_reason: "Cluster configuration changes"`). Without it a
   nested-virt VM returns to `stopped`, and the reason is in the machine
   log, not the power-on response.
4. The installer ISO is attached as a CD-ROM. `drive` has no `media_source`,
   so the insert is an API call. The interface follows the machine type:
   **ide** on `pc` / i440fx (that chipset has no AHCI controller, and an
   ahci CD then leaves the VM with no boot device and no error), **ahci**
   on q35, including `pc-q35-*`, and on the platform default (empty
   `vlab_machine_type`, q35 on 26.x). Disks are virtio-scsi, so the guest
   sees `/dev/sdX` and the seed's `YC_DRIVE_LIST` matches.
5. `cloud_init` injects the rendered seed as nocloud `user-data`. Node 2's
   join seed is attached when `vlab_node_count` is at least 2. Node 3's
   scale-out seed is attached when it is at least 3.
6. Power on. The installer answers itself, installs, reboots, and the new
   system's UI comes up on its External DHCP address.

`vlab_seed_debug` (default false) renders an optional trace into the node 1
seed: NIC 1 is brought up inside the installer and the component log is
posted to `vlab_trace_url`. Set both when a stalled install has to be
visible without a console.

### Joining additional nodes (node 2)

A join seed (`templates/user-data-node2.sh.j2`) differs from the new-system
seed in a handful of lines:

- `YC_VSAN_NEW=0`. No `YC_CLOUD_NAME` or `YC_HOSTNAME` (the platform assigns
  `nodeN`). `YC_CLUSTER` selects the target cluster. The template sends
  cluster key `1`, which is the first cluster on a system that was just
  installed.
- `YC_USER_NAME` and `YC_USER_PASSWORD` are the **existing system's admin
  credentials**. The installer uses them for `yb-api` calls against node 1.
- `YC_CONTROLLER_NODE` is unnecessary in basic mode. It defaults to the
  core-network DNS name `yb-api` (`0050-network`), which node 1 serves.
  The joining node's `YC_NET_CORE_NODE_ADDR` (for node 2, `100.96.0.3/24`)
  plus `YC_NET_CORE_DNS='100.96.0.1'` make that name resolvable.
- Three join-specific tweaks ride in the seed: skip the quick-install
  preflight (`truncate -s 0 .../0040-quick-install`), skip the installer's
  ntpd sync (time comes from the cluster), and shorten the final reboot
  pause.

The example playbook injects the join seed into node 2's cloud-init. Power
it on with `-e '{"vlab_power_on_nodes":[2]}'` once node 1 is up. Acceptance
is the nested system's `/api/v4/nodes` reporting both nodes with
`vsan_connected: true`. Verified live 2026-09-21: node 2 registered about
13 minutes after power-on and was fully vSAN-connected at about 16 minutes,
with zero prompts. Tier capacities doubled (both nodes' drives in the
nested vSAN).

### Scale-out nodes (node 3)

A scale-out seed (`templates/user-data-node3.sh.j2`) is the join seed with
`YC_INSTALL_TYPE=scale-out` and the next static core address
(`YC_NET_CORE_NODE_ADDR=100.96.0.4/24`, NIC addresses `172.16.x.3`). The
External and hostname questions never fire for a join (that section is
controller-plus-new only), so no hostname or domain answers are required.
Power on with `-e '{"vlab_power_on_nodes":[3]}'`.

**Use static core addressing for every joining node in a nested lab.**
Reference seeds from physical deployments let a scale-out node take
`YC_NET_CORE_NODE_ADDR` via DHCP from node 1's core network. Measured here,
that DHCP never serves a lease across the nested fabric (broadcast DHCP
over nested vxlan; unicast works, which is why a static join succeeds).
Two DHCP attempts stalled with no error. The static attempt registered in
about 2 minutes and was fully vSAN-connected at about 5 minutes. Verified
live 2026-09-21: three nodes, all `vsan_connected: true`, tier capacities
tripled (tier 0: 147 GiB, tier 1: 447 GiB).

On a 2-node cloud whose UI address is owned by the External vnet, draining
the controller node takes the management plane offline until the vnet lands
on the peer. Budget for that, and keep console or IPMI access available.

## 4. Post-install convergence

Point the collection at the new system and run
`examples/unattended_install/post_install.yml`. Auth comes from
`VERGEOS_HOST`, `VERGEOS_USERNAME`, and `VERGEOS_PASSWORD`. The admin user
and password are the ones the seed created (`YC_USER_NAME`,
`YC_USER_PASSWORD`).

The baseline creates three tag categories (`environment`, `role`,
`backup-policy`) and their tags. Each category sets `taggable_vms: true`.
An omitted taggable flag is stored as false on create (issue #121), and a
category with every flag false cannot tag a VM.

`tag` and `tag_category` update through `manager.update(key, ...)`.
Re-checked against the SDK floor this collection installs
(`pyvergeos>=1.2.8`, which resolves to 1.6.1): pyVergeOS #97, where
`save()` skipped the typed manager's alias translation, is fixed in 1.6.1.
See `docs/SDK-COMPATIBILITY.md`. `vm`, `nic`, `drive`, `network`, and
`user` call `save()` with API field names. This baseline does not update
those objects. It creates categories and tags.

`user`, `group`, `permission`, `api_key`, `network`, `snapshot_profile`,
`auth_source`, and `nas_volume` are in the collection for whatever the site
adds next. The example does not invent that list.

### Discovery

With `YC_NET_EXTERNAL1_ADDR=dhcp` the new UI address is not known in
advance. Either set a static `YC_NET_EXTERNAL1_ADDR=<ip>/<prefix>` (plus
`_GATEWAY` and `_DNS`) for a deterministic endpoint, or scan for the
VergeOS signature. `GET /api/v4/version` returns `{"err":"Login required"}`
before authentication. `find_new_system.yml` does that scan.

The probe uses a 4 second curl cap. A freshly booted nested node answers
slowly, and a 1 second timeout reports it absent. Pass `exclude_ips` so
the scan does not stop on the host cloud's own UI, or on any other VergeOS
system already on the subnet. The first matching address in subnet order
wins. The play retries `discover_tries` times (default 20), waiting
`discover_wait` seconds (default 60) between failed scans, then fails. It
does not retry forever. Each scan still walks the subnet, so the wall clock
is the scans plus the waits.

## 5. Secrets

Nothing secret belongs in the repo.

- Seed templates carry Jinja variables resolved at render time
  (`VLAB_ADMIN_PASSWORD` and the optional username and email).
- Installer constraints: password at least 8 characters, and no `'`, `"`,
  or `+`.
- The rendered user-data contains the admin password. The task that injects
  it runs with `no_log: true`. Rendered artifacts stay in `/tmp` or a vault.

## 6. Verification checklist

1. The VM powers on with the seed attached. The installer completes with no
   console interaction and reboots into the installed system.
2. `https://<new-ip>/api/v4/version` answers `{"err":"Login required"}`.
3. The seeded admin authenticates.
4. `post_install.yml` first run reports `changed` for each created object.
   The second run reports `changed=0`. Read the objects back via
   `/api/v4/tags?fields=name,category#name`.
