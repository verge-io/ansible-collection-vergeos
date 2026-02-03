# VergeOS Ansible Collection

Ansible collection for managing VergeOS virtualization infrastructure via the VergeOS API.

## Requirements

- **Python**: >= 3.9
- **Ansible**: >= 2.14.0
- **pyvergeos**: >= 1.0.0 (VergeOS Python SDK)

## Installation

### 1. Install the pyvergeos SDK

```bash
pip install pyvergeos
```

### 2. Install the Collection

**From Galaxy (when published):**
```bash
ansible-galaxy collection install vergeio.vergeos
```

**From Source:**
```bash
ansible-galaxy collection build --force
ansible-galaxy collection install vergeio-vergeos-*.tar.gz --force
```

### 3. For Ansible Execution Environments

Add to your `requirements.txt`:
```
pyvergeos>=1.0.0
```

## Basic Setup

### 1. Set Environment Variables

The easiest way to authenticate is using environment variables:

```bash
export VERGEOS_HOST="your-vergeos-host.example.com"
export VERGEOS_USERNAME="your-username"
export VERGEOS_PASSWORD="your-password"
export VERGEOS_INSECURE="false"  # Set to "true" for self-signed certificates
```
### 2. Examples Windows and RHEL VM from OVA with hostname and static ip set

Upload ova files to VergeOS

Configure static var examples in 
RHEL) examples/import_and_configure_vm.yml (ova cloud-init installed and enabled)
WINDOWS) examples/import_and_configure_winvm.yml (ova sysprep /generalize /oobe /shutdown)

Configure and Run Playbooks
```bash
ansible-playbook examples/import_and_configure_winvm.yml
ansible-playbook examples/import_and_configure_vm.yml
```

### 3. Example VM snapshot workflow

Configure and Run Playbook
```bash
ansible-playbook examples/snapshot_workflow.yml
```

## Dynamic Inventory

The collection includes a dynamic inventory plugin (`vergeos_vms`) that queries one or more VergeOS sites for VMs.

> **Note:** This is an **API-only** inventory plugin. It does NOT set `ansible_host` and does not support SSH connections to VMs. Use it with VergeOS modules that operate via the API.

### Single-Site Configuration

Create a file named `inventory.vergeos_vms.yml`:

```yaml
plugin: vergeio.vergeos.vergeos_vms

sites:
  - name: production
    host: vergeos.example.com
    username: admin
    password: "{{ lookup('env', 'VERGEOS_PASSWORD') }}"
    # Or use API key:
    # api_key: "{{ lookup('env', 'VERGEOS_API_KEY') }}"
    insecure: false  # Set to true for self-signed certificates
```

### Multi-Site Configuration

Query multiple VergeOS sites concurrently:

```yaml
plugin: vergeio.vergeos.vergeos_vms

sites:
  - name: denver
    host: denver.vergeos.local
    username: admin
    password: "{{ lookup('env', 'DENVER_PASS') }}"
  - name: chicago
    host: chicago.vergeos.local
    api_key: "{{ lookup('env', 'CHICAGO_API_KEY') }}"
    insecure: true

# Optional: Group hosts by dimensions
group_by:
  - site      # site_denver, site_chicago
  - status    # status_running, status_stopped
  - tags      # tag_production, tag_web
  - tenant
  - os_family
  - cluster

# Optional: Filter VMs
filters:
  status: running
  name_pattern: ".*web.*"

# Optional: Caching (recommended for large deployments)
cache: true
cache_plugin: jsonfile
cache_connection: ~/.cache/vergeos_inventory
cache_timeout: 900

# Optional: Concurrency settings
max_workers: 10
site_timeout: 60
```

### Available Host Variables

Each host in the inventory has these variables (with `vergeos_` prefix by default):

| Variable | Description |
|----------|-------------|
| `vergeos_site` | Site name (for grouping/filtering) |
| `vergeos_site_url` | Site API URL (use with modules) |
| `vergeos_vm_id` | VM ID (use with modules) |
| `vergeos_name` | VM name |
| `vergeos_status` | VM status (running, stopped, etc.) |
| `vergeos_tags` | List of tag names |
| `vergeos_ip` | First IP address (for reference) |
| `vergeos_nics` | List of NIC details |
| `vergeos_ram` | RAM in MB |
| `vergeos_cpu_cores` | Number of CPU cores |
| `vergeos_tenant` | Tenant name |
| `vergeos_cluster` | Cluster name |
| `vergeos_vm_data` | Full VM data dictionary |

### Example Playbook with Inventory

```yaml
# snapshot_all_production.yml
# Create snapshots of all VMs tagged "production" across all sites
- hosts: tag_production
  connection: local
  gather_facts: false

  tasks:
    - name: Create VM snapshot
      vergeio.vergeos.vm_snapshot:
        host: "{{ vergeos_site_url }}"
        username: "{{ lookup('env', 'VERGEOS_USERNAME') }}"
        password: "{{ lookup('env', 'VERGEOS_PASSWORD') }}"
        name: "{{ vergeos_name }}"
        snapshot_name: "automated-{{ ansible_date_time.date }}"
        state: present
      delegate_to: localhost
```

### CLI Usage

```bash
# List all hosts
ansible-inventory -i inventory.vergeos_vms.yml --list

# Show inventory graph
ansible-inventory -i inventory.vergeos_vms.yml --graph

# Run playbook with inventory
ansible-playbook -i inventory.vergeos_vms.yml playbook.yml

# Target specific groups
ansible-playbook -i inventory.vergeos_vms.yml playbook.yml --limit tag_production
ansible-playbook -i inventory.vergeos_vms.yml playbook.yml --limit site_denver

# Refresh cache
ansible-inventory -i inventory.vergeos_vms.yml --list --refresh-cache
```
