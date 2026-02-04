# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build and Install Commands

```bash
# Build the collection
ansible-galaxy collection build --force

# Install locally
ansible-galaxy collection install vergeio-vergeos-*.tar.gz --force
```

## Running Playbooks

```bash
# Set required environment variables
export VERGEOS_HOST="https://your-vergeos-host"
export VERGEOS_USERNAME="your-username"
export VERGEOS_PASSWORD="your-password"
export VERGEOS_INSECURE="true"  # Optional: skip SSL verification

# Run example playbooks
ansible-playbook examples/gather_info.yml
ansible-playbook examples/create_vm.yml

# Test inventory plugin
ansible-playbook -i inventory/vergeos.yml examples/test_inventory.yml
```

## Architecture

This is an Ansible collection (`vergeio.vergeos`) for managing VergeOS infrastructure via the pyvergeos Python SDK.

### Module Utilities (`plugins/module_utils/vergeos.py`)

- `get_vergeos_client(module)`: Factory function that creates a pyvergeos `VergeClient` from module params
- `sdk_error_handler(module, e)`: Maps SDK exceptions to `module.fail_json()` calls
- `vergeos_argument_spec()`: Shared argument spec for authentication (host, username, password, insecure) with environment variable fallbacks

### Module Pattern

All modules follow this structure:
1. Merge `vergeos_argument_spec()` with module-specific arguments
2. Create SDK client via `get_vergeos_client(module)`
3. Use SDK resource managers (e.g., `client.vms`, `client.networks`) for API operations
4. Handle state-based operations (present/absent, running/stopped)
5. Support `check_mode` for dry-run operations
6. Wrap SDK calls in try/except with `sdk_error_handler()` for consistent error messages

### Modules

- **VM**: `vm`, `vm_info`, `vm_import`, `vm_snapshot`
- **Network**: `network`, `network_info`, `nic`
- **Storage**: `drive`
- **Config**: `cloud_init`, `windows_unattend`
- **System**: `user`, `member`, `cluster_info`, `file_info`
- **Tags**: `tag`, `tag_category`

### Inventory Plugin (`plugins/inventory/vergeos_vms.py`)

Multi-site dynamic inventory with:
- Concurrent site queries (ThreadPoolExecutor)
- Batch API fetching for tags/NICs (O(1) API calls per site)
- Group by: site, status, tags, tenant, os_family, cluster
- JSON file caching (recommended: 1 hour timeout)
- Hostname templating

### Documentation Fragment (`plugins/doc_fragments/vergeos.py`)

Shared documentation for authentication options, included in all modules via `extends_documentation_fragment: vergeio.vergeos.vergeos`.
