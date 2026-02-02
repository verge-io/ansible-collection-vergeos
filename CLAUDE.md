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

This is an Ansible collection (`vergeio.vergeos`) for managing VergeOS infrastructure via its v4 REST API.

### Module Utilities (`plugins/module_utils/vergeos.py`)

- `VergeOSAPI`: Base HTTP client with Basic Auth, provides `get()`, `post()`, `put()`, `delete()` methods
- `vergeos_argument_spec()`: Shared argument spec for authentication (host, username, password, insecure) with environment variable fallbacks
- `VergeOSAPIError`: Custom exception for API errors

### Module Pattern

All modules follow this structure:
1. Merge `vergeos_argument_spec()` with module-specific arguments
2. Implement `get_*()` lookup, `create_*()`, `update_*()`, `delete_*()` helper functions
3. Handle state-based operations (present/absent, running/stopped)
4. Support `check_mode` for dry-run operations
5. Long-running operations (imports, snapshots) use polling with configurable `timeout` parameter

### Modules

- **VM**: `vm`, `vm_info`, `vm_import`, `vm_snapshot`
- **Network**: `network`, `network_info`, `nic`
- **Storage**: `drive`
- **Config**: `cloud_init`, `windows_unattend`
- **System**: `user`, `member`, `cluster_info`, `file_info`

### Inventory Plugin (`plugins/inventory/vergeos.py`)

Dynamic inventory with tenant/cluster filtering, constructed groups, caching support, and hostname templating.

### Documentation Fragment (`plugins/doc_fragments/vergeos.py`)

Shared documentation for authentication options, included in all modules via `extends_documentation_fragment: vergeio.vergeos.vergeos`.
