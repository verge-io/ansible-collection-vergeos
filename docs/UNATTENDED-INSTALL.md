# Fully unattended VergeOS install + post-install with Ansible

End-to-end workflow: seed the VergeOS installer so it asks **zero questions**,
then converge the freshly installed system with this collection — tags,
users, RBAC — with no human at a console at any point.

Verified live on **VergeOS 26.1.8** (installer internals confirmed against
the extracted `verge-io-install-26.1.8.iso`: `usr/bin/yb-install`, the
`usr/lib/ybos/install/????-*` components, and the initrd's `9900-Live` boot
script). Nested acceptance run 2026-09-21 on a 2-node 26.1.8 cloud: seeded install
completed with **zero prompts**, new system came up on DHCP, seeded admin
login verified, `post_install.yml` converged (run 1 `changed=2`, runs 2-3
`changed=0`), objects confirmed by API read-back on the new system.

---

## 1. How the installer consumes a seed

`yb-install` collects its answers from four places, in order (later plain
assignments override earlier ones):

| # | Vector | Mechanics |
|---|--------|-----------|
| 1 | **Kernel cmdline** | any `YC_*=value` parameter is exported; `yb.install` / `yb.install=advanced` selects the mode |
| 2 | **`install-seed` on the boot media root** | the initrd copies it to `/etc/yottabyte/install-seed`; `yb-install` sources it as bash. `init-hook`, `post-init-hook`, `first-boot`, `install-settings` on the media root are honored the same way |
| 3 | **`yb.installseedurl=<URL>`** | the initrd downloads the seed over HTTP (`wget -T 15`) — ideal for PXE/netinstall fleets; a downloaded seed wins over one on the media |
| 4 | **Volume labeled `CIDATA` or `CONFIG-2`** | `yb-install` mounts it and sources `user-data` (also `openstack/latest/user_data`, `cloud-init/data`) **as bash** — but only if the first line is *not* `#cloud-config` |

Every installer dialog checks its `YC_*` variable first and only prompts
when it is unset — a complete seed is a fully silent install; a partial
seed prompts for the remainder.

Because the seed is *sourced bash*, it can also stage work: write extra
install hooks into `/usr/lib/ybos/install/` (they run in lexical order),
or drop a script at `/etc/yottabyte/run-with-appserver` to run API calls
against the brand-new system at the end of the install.

**Vector 4 is the workhorse.** VergeOS's own cloud-init injection
(`cloudinit_datasource: nocloud` + a `user-data` file) presents exactly such
a CIDATA volume to a VM — so for a *nested* install, the platform is the
seed-delivery mechanism and no ISO remastering is ever needed. For physical
installs, put `install-seed` on the USB/ISO root (vector 2), serve it over
HTTP (vector 3), or attach a small CIDATA-labeled ISO next to the installer.

## 2. The YC_* answer set (26.1.8)

Minimum for a silent single-node ("controller", new system) install:

```bash
YC_INSTALL=basic
YC_INSTALL_TYPE=controller        # controller|scale-out|compute|pxe|replace
YC_VSAN_NEW=1                     # 1 = new system; 0 = join (see 'Joining additional nodes')
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

# Drives: explicit lists skip both the drive checklist AND the
# "manually set the tier?" prompt (which fires whenever the tier list is empty)
YC_DRIVE_LIST='/dev/sda /dev/sdb /dev/sdc /dev/sdd'
YC_VSAN_TIER_LIST='0 0 1 1'       # or 'auto auto auto auto'
YC_VSAN_ENCRYPTION=0
YC_CLUSTER_SWAP_TIER=-1           # "none" — skips the swap-tier menu
```

Network seeding is **switch-based** on 26.x (`YC_NET_SWITCH<n>_*` with
`_DONE=1` marking each switch complete, then `YC_NET_CORE/DMZ/EXTERNAL1_*`
group assignments). See the working template in
`examples/unattended_install/templates/user-data-node1.sh.j2` — three
switches (External / Core 1 / Core 2), DHCP on External, no bonding.

Gotchas measured on 26.1.8:

- **Never hardcode NIC names.** They depend on the machine type (q35 →
  `enp1s0/enp2s0/...`; i440fx → `enp0sN`; older platforms → `enp1s1..`).
  A hardcoded name that matches nothing parks the installer at the NIC
  picker forever, with no error anywhere. The template detects NICs in
  PCI-bus order at seed runtime instead — creation order == PCI order, so
  NIC1/2/3 keep their intended roles on any machine type.
- **Timezone:** `0020-timezone` re-reads `/etc/timezone`; have the seed
  `echo "$TZ" > /etc/timezone` rather than trusting `YC_TIMEZONE` alone.
- **DHCP external** needs `YC_NET_EXTERNAL1_HOSTNAME` *and*
  `YC_NET_EXTERNAL1_DOMAINNAME` non-empty or those two dialogs appear.
- **No `802.3ad`** in nested seeds — LACP won't negotiate on a vSwitch.
- The 4.x-era `YC_NET_AUTODETECT` path **does not exist** in 26.x.

## 3. Nested lab deployment (the platform delivers the seed)

The nested pattern (from `examples/unattended_install/deploy_nested_lab.yml`):

1. Two **core-fabric networks**: internal, MTU 9142, no router IP, no DHCP.
2. Node VM(s): ≥8 cores / 16 GB, `nested_virtualization`,
   `disable_hypervisor`, `uefi`, tier0/tier1 virtio-scsi disks, the
   installer ISO in an `ahci` CD-ROM, NICs ordered
   **External → Core 1 → Core 2** (the seed detects their guest names in
   PCI-bus order at runtime; only the creation order matters).
3. `cloud_init` module injects the rendered seed as nocloud `user-data`.
4. Power on. The installer answers itself, installs, reboots; the new
   system's UI comes up on its External DHCP address.

**Host prerequisite:** the *host* cluster must have
`kvm_nested: true` (Cluster → Edit → Nested Virtualization) — and the change
requires a **rolling node reboot** (nodes flag
`restart_reason: "Cluster configuration changes"`). Without it a
nested-virt VM silently returns to `stopped`; the reason only appears in the
machine's log, not the power-on API response.


### Joining additional nodes (node 2+)

A join seed (`templates/user-data-node2.sh.j2`) differs from the new-system
seed in a handful of lines:

- `YC_VSAN_NEW=0`, no `YC_CLOUD_NAME`/`YC_HOSTNAME` (the platform assigns
  `nodeN`), `YC_CLUSTER=<key or name>` selects the target cluster.
- `YC_USER_NAME`/`YC_USER_PASSWORD` are the **existing system's admin
  credentials** — the installer uses them for `yb-api` calls against node 1.
- `YC_CONTROLLER_NODE` is not needed in basic mode: it defaults to the
  core-network DNS name `yb-api` (0050-network:874), which node 1 serves.
  The joining node's `YC_NET_CORE_NODE_ADDR` (e.g. `100.96.0.3/24`) plus
  `YC_NET_CORE_DNS='100.96.0.1'` make that resolvable.
- Three proven join-specific tweaks ride in the seed: skip the
  quick-install preflight (`truncate -s 0 .../0040-quick-install`), skip the
  installer's ntpd sync (time comes from the cluster), and shorten the final
  reboot pause.

Deploy: the example playbook injects the join seed into node-2's cloud-init;
power it on with `-e '{"vlab_power_on_nodes":[2]}'` once node 1 is up.
Acceptance: the *nested* system's `/api/v4/nodes` reports two nodes with
`vsan_connected: true`. Verified live 2026-09-21: node 2 registered ~13 min
after power-on and was fully vSAN-connected at ~16 min, zero prompts; tier
capacities doubled (both nodes' drives in the nested vSAN).


### Scale-out nodes (node 3+)

A scale-out seed (`templates/user-data-node3.sh.j2`) is the join seed with
`YC_INSTALL_TYPE=scale-out` and the next static core address
(`YC_NET_CORE_NODE_ADDR=100.96.0.4/24`, NIC addresses `172.16.x.3`). The
External/hostname questions never fire for non-new installs (that whole
section is controller+new only), so no hostname/domain answers are needed.
Power on with `-e '{"vlab_power_on_nodes":[3]}'`.

**Use static core addressing for every joining node in a nested lab.**
Reference seeds from physical deployments let scale-out nodes take
`YC_NET_CORE_NODE_ADDR` via DHCP from node 1's core network — measured here,
that DHCP never serves a lease across the nested fabric (broadcast/DHCP over
nested vxlan; unicast works fine, which is why static joins succeed). Two
attempts with DHCP core addressing stalled without error; the static
attempt registered in ~2 min and was fully vSAN-connected at ~5 min.
Verified live 2026-09-21: three nodes, all `vsan_connected: true`, tier
capacities tripled (tier 0: 147 GiB, tier 1: 447 GiB).

> Operational note from the live run: on a 2-node cloud whose UI address is
> owned by the External vnet, draining the controller node takes the
> management plane offline until the vnet lands on the peer — budget for it
> and keep console/IPMI access at hand.

## 4. Post-install convergence

Point the collection at the *new* system and run the baseline
(`examples/unattended_install/post_install.yml`): tag categories, tags,
users/RBAC as needed. Auth comes from `VERGEOS_HOST` /
`VERGEOS_USERNAME` / `VERGEOS_PASSWORD` env — the admin user and password
are the ones the seed created (`YC_USER_NAME` / `YC_USER_PASSWORD`).

Discovery: with `YC_NET_EXTERNAL1_ADDR=dhcp` the new UI address isn't known
in advance. Either use a static `YC_NET_EXTERNAL1_ADDR=<ip>/<prefix>` (+
`_GATEWAY`, `_DNS`) for a deterministic endpoint, or scan for the VergeOS
signature (`GET /api/v4/version` → `{"err":"Login required"}`) as
`deploy_nested_lab.yml`'s companion `find_new_system.yml` does. Use a probe
timeout of several seconds — a freshly booted nested node answers slowly,
and a 1-second timeout reports it absent.

Module status for post-install work (see `docs/DEFECTS-D1-D6.md` and the
lab-test report): `tag` / `tag_category` are converged and idempotent
(verified 3×); `user`, `group`, `permission` (rbac role), `api_key` create
paths verified live; **updates** through `vm`/`nic`/`drive`/`network`/`user`
are D1-affected — treat those as create-only until D1 lands.

## 5. Secrets

Nothing secret belongs in the repo:

- Seed templates carry `@SEED_*@` tokens or Jinja vars resolved from the
  environment at render time (`VLAB_ADMIN_PASSWORD`, etc.).
- Installer constraints: password ≥ 8 chars, no `'`, `"` or `+`.
- The rendered user-data contains the admin password — the task that
  injects it must run under `no_log: true`, and rendered artifacts stay in
  `/tmp` or a vault, never committed.

## 6. Verification checklist (what "done" looks like)

1. VM/host powers on with the seed attached; installer completes with no
   console interaction and reboots into the installed system.
2. `https://<new-ip>/api/v4/version` answers `{"err":"Login required"}`.
3. `admin` + seed password authenticates.
4. `post_install.yml` first run: `changed` for each created object; second
   run: `changed=0` (idempotent); read the objects back via
   `/api/v4/tags?fields=name,category#name`.
