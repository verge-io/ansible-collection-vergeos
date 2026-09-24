# VergeOS Ansible Collection

Ansible collection for managing VergeOS virtualization infrastructure via the VergeOS API.

## Requirements

- **Python**: >= 3.9
- **Ansible**: >= 2.14.0
- **pyvergeos**: >= 1.0.1 (VergeOS Python SDK)

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
pyvergeos>=1.0.1
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

## Deploying VMs from recipes

A recipe is what you pick in the VergeOS UI when you create a new VM. It
carries its own set of questions, and deploying one from Ansible means
answering them. There is a full walkthrough in
[docs/DEPLOYING-VMS-FROM-RECIPES.md](docs/DEPLOYING-VMS-FROM-RECIPES.md), and
runnable examples in [`examples/`](examples).

| Module | What it does |
|---|---|
| `vm_recipe_info` | Lists recipes, and the questions a recipe accepts. Start here, because question names are not guessable and some valid values depend on the system you are pointed at |
| `vm_recipe_deploy` | Deploys a VM from a recipe. Answers are checked against the recipe's own published questions before anything is sent, so a bad answer is a refusal rather than a half built VM |
| `vm_drive_info` | A VM's drives, with optional IO counters. Those counters are how you tell a booted guest from one sitting at "no bootable device" |
| `vm_nic_info` | A VM's NICs, and separately the ones attached to no network at all. A recipe whose network question was left unanswered produces exactly that, and it otherwise passes every check |

| Role | What it does | Example |
|---|---|---|
| `vm_from_recipe` | Deploys a VM from a recipe and then waits until it is genuinely usable, meaning drives imported, NICs attached, and optionally the guest proven to have booted | `examples/vm_from_recipe_role.yml` |

The modules return as soon as the platform accepts the deploy, which happens
while the OS image is still downloading. Use them when you just want to fire
a deploy off. Use the role when the next thing in your playbook needs the VM
to actually work.

### Examples

| Example | What it shows |
|---|---|
| `recipe_discover.yml` | Read only. What recipes exist, what each one asks, and which answers are only valid on this particular system |
| `recipe_preflight.yml` | Validates an answer set and runs the platform's own simulation without creating anything. Exits non zero when the answers would not deploy, so it works as a CI gate |
| `deploy_from_recipe.yml` | A single VM, driving the modules directly |
| `vm_from_recipe_role.yml` | A single VM via the role, waited on and boot proved |
| `recipe_fleet.yml` | Several VMs from one recipe, each with its own size |

### Setting connection details once

The recipe modules share an action group, so `module_defaults` can carry the
connection for all of them:

```yaml
- hosts: localhost
  module_defaults:
    group/vergeio.vergeos.recipe:
      host: "{{ lookup('env', 'VERGEOS_HOST') }}"
      username: "{{ lookup('env', 'VERGEOS_USERNAME') }}"
      password: "{{ lookup('env', 'VERGEOS_PASSWORD') }}"
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
  - node      # node_node1, node_node2 (running VMs only)

# Optional: Filter VMs
filters:
  status: running
  name_pattern: ".*web.*"

# Caching (recommended for production use)
cache: true
cache_plugin: jsonfile
cache_connection: ~/.cache/vergeos_inventory
cache_timeout: 3600  # 1 hour

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
| `vergeos_description` | VM description/notes |
| `vergeos_status` | VM status (running, stopped, etc.) |
| `vergeos_created` | Creation timestamp (Unix epoch) |
| `vergeos_modified` | Last modified timestamp (Unix epoch) |
| `vergeos_machine_type` | QEMU machine type (e.g., "pc-q35-10.0") |
| `vergeos_tags` | List of tag names |
| `vergeos_ip` | First IP address (for reference) |
| `vergeos_mac_addresses` | List of MAC addresses |
| `vergeos_nics` | List of NIC details |
| `vergeos_drives` | List of drive details |
| `vergeos_ram` | RAM in MB |
| `vergeos_cpu_cores` | Number of CPU cores |
| `vergeos_tenant` | Tenant name |
| `vergeos_cluster` | Cluster name |
| `vergeos_node_name` | Node running VM (None if stopped) |
| `vergeos_node_key` | Node resource key (None if stopped) |
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
ansible-inventory -i inventory.vergeos_vms.yml --list --flush-cache
```

## Tag Management

The collection includes modules for managing tags, which integrate with the inventory plugin's `group_by: tags` feature.

### Create Tag Infrastructure

```yaml
# Create a tag category
- vergeio.vergeos.tag_category:
    host: "{{ vergeos_host }}"
    username: "{{ vergeos_username }}"
    password: "{{ vergeos_password }}"
    name: Environment
    description: "Environment classification"
    taggable_vms: true
    state: present

# Create tags in the category
- vergeio.vergeos.tag:
    host: "{{ vergeos_host }}"
    username: "{{ vergeos_username }}"
    password: "{{ vergeos_password }}"
    name: Production
    category: Environment
    description: "Production servers"
    state: present
```

### Apply Tags to VMs

```yaml
# Apply a tag to a VM
- vergeio.vergeos.tag:
    host: "{{ vergeos_host }}"
    username: "{{ vergeos_username }}"
    password: "{{ vergeos_password }}"
    name: Production
    category: Environment
    vm_name: my-web-server
    state: present
```

### Tag Examples

```bash
# Setup tag infrastructure (categories and tags)
ansible-playbook examples/setup_tags.yml

# Apply tags to VMs based on name patterns
ansible-playbook -i inventory.vergeos_vms.yml examples/apply_tags.yml

# Snapshot VMs by tag
ansible-playbook -i inventory.vergeos_vms.yml examples/snapshot_by_tag.yml --limit tag_production
```
