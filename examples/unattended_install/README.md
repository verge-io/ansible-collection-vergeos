# Unattended install and post-install (nested lab)

Working example of the workflow in
[docs/UNATTENDED-INSTALL.md](../../docs/UNATTENDED-INSTALL.md):
seed the VergeOS installer through the platform's own cloud-init
(nocloud/CIDATA), let it install with zero prompts, discover the new
system, and converge it with this collection.

```
deploy_nested_lab.yml            fabrics, node VMs, seeds; powers on node 1
templates/user-data-node1.sh.j2  new-system installer answers (sourced as bash)
templates/user-data-node2.sh.j2  controller join
templates/user-data-node3.sh.j2  scale-out join, static core address
find_new_system.yml              DHCP address plus a seeded-admin login check
post_install.yml                 tag baseline on the NEW system
```

## Run it

`VERGEOS_HOST` is a hostname or IP. A leading `https://` is accepted and
stripped, the same way the modules treat it.

```bash
export VERGEOS_HOST=<host-cloud> VERGEOS_USERNAME=<user> VERGEOS_PASSWORD=<password>
export VERGEOS_INSECURE=true
export VLAB_ADMIN_PASSWORD='...'             # seeded admin for the NEW system

# 1. Deploy the nested lab on the host cloud. Node 1 powers on and installs.
ansible-playbook examples/unattended_install/deploy_nested_lab.yml

# 2. Wait for the new system (10-20 min) and verify the seeded login.
#    exclude_ips is every VergeOS UI already on the subnet, including the
#    host cloud. The first match in address order wins.
ansible-playbook examples/unattended_install/find_new_system.yml \
  -e discover_subnet=192.168.1 \
  -e exclude_ips='["<host-cloud-ui>"]'

# 3. Converge the new system. Run twice: the second run must be changed=0.
export VERGEOS_HOST=<new-system-ip> VERGEOS_USERNAME=admin \
       VERGEOS_PASSWORD="$VLAB_ADMIN_PASSWORD" VERGEOS_INSECURE=true
ansible-playbook examples/unattended_install/post_install.yml
```

Node 2 and node 3 stay powered off. After node 1 is up:

```bash
ansible-playbook examples/unattended_install/deploy_nested_lab.yml \
  -e '{"vlab_power_on_nodes":[2]}'
# then, once node 2 is in the nested vSAN:
ansible-playbook examples/unattended_install/deploy_nested_lab.yml \
  -e '{"vlab_power_on_nodes":[3]}'
```

`deploy_nested_lab.yml` is not check-mode safe. Core-fabric create, the
nested-virt flags, and the installer CD go through the API, and `--check`
refuses to start.

## Prerequisites

- Host cluster: `kvm_nested: true` (needs a rolling node reboot to apply).
- `verge-io-install-26.1.8.iso` (or the ISO named by `vlab_installer_iso`)
  already in the host cloud's media catalog.
- pyvergeos importable from the interpreter Ansible runs the modules under.
  When a task says pyvergeos is missing and it is installed, pin
  `ansible_python_interpreter` to that interpreter. The module's error
  names the interpreter it actually used.
- `curl` on the controller for `find_new_system.yml`.
- Secrets only via the environment. The seed tasks run `no_log: true`.

## What still goes through the API

| Gap | Why |
|---|---|
| Core fabric create | `network` has no `ipaddress_type`, and a core fabric has none |
| Installer CD | `drive` has no `media_source`, so the ISO cannot be inserted through it |
| `nested_virtualization`, `disable_hypervisor`, `guest_agent` | not `vm` options. UEFI is `bios_type: uefi` |

Fabric NICs use `nic`. It matches a NIC by network, so each fabric gets its
own NIC. Create External first, then Core 1, then Core 2.
