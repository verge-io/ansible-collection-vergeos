# Unattended install + post-install (nested lab)

Working example of the workflow documented in
[docs/UNATTENDED-INSTALL.md](../../docs/UNATTENDED-INSTALL.md):
seed the VergeOS installer through the platform's own cloud-init
(nocloud/CIDATA), let it install with zero prompts, discover the new
system, and converge it with this collection.

```
deploy_nested_lab.yml            create fabrics + node VMs + seed, power on node-1
templates/user-data-node1.sh.j2  the installer answer file (sourced as bash)
find_new_system.yml              discover the new system's DHCP address + login check
post_install.yml                 baseline config on the NEW system (tags, ...)
```

## Run it

```bash
export ANSIBLE_COLLECTIONS_PATH=<collections dir>
source <host-cloud env>                      # VERGEOS_HOST/USERNAME/PASSWORD/INSECURE
export VLAB_ADMIN_PASSWORD='...'             # seeded admin for the NEW system

# 1. Deploy the nested lab on the host cloud (node-1 powers on and installs)
ansible-playbook deploy_nested_lab.yml

# 2. Wait for the new system to appear (10-20 min) and verify login
ansible-playbook find_new_system.yml -e exclude_ips='["<host-cloud-ui-ip>"]'

# 3. Converge the new system
export VERGEOS_HOST=<new_system_ip> VERGEOS_USERNAME=admin \
       VERGEOS_PASSWORD="$VLAB_ADMIN_PASSWORD" VERGEOS_INSECURE=true
ansible-playbook post_install.yml            # run twice: 2nd run must be changed=0
```

## Prerequisites

- Host cluster: `kvm_nested: true` (needs a rolling node reboot to apply).
- `verge-io-install-<ver>.iso` in the host cloud's media catalog.
- pyvergeos in the ansible python (`ansible_python_interpreter` may need
  pinning — see the interpreter note in the workflow doc).
- Secrets only via environment; the seed task runs `no_log: true`.
