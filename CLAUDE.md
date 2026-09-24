# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build and Install Commands

```bash
# Build the collection
ansible-galaxy collection build --force

# Install locally
ansible-galaxy collection install vergeio-vergeos-*.tar.gz --force
```

## Running the Local Gates

```bash
# Unit tests
PYTHONPATH=<workdir> python -m pytest tests/unit -q

# Lint
ansible-lint --profile production

# Sanity -- run it from a CHECKOUT-SHAPED directory, not from the built
# artifact. galaxy.yml's build_ignore excludes `tests`, so the tarball
# contains no test files at all and `ansible-test sanity` against an
# installed collection silently skips the entire test tree. CI checks out
# the repo and runs in place, so it checks files a build-then-install run
# never sees -- boilerplate, pylint and yamllint findings in tests/ arrive
# as a red PR after a green local run.
#
# Exclude `.ansible/` when copying: ansible-lint creates a full copy of the
# collection inside itself there, and sanity then lints every file twice.
git archive --format=tar HEAD | tar -x -C <workdir>/ansible_collections/vergeio/vergeos
cd <workdir>/ansible_collections/vergeio/vergeos
ansible-test sanity --local
```

Run sanity on **both** supported cores. The two pylint versions disagree:
the older one does not know `kwarg-superseded-by-positional-arg` and reports
`redundant-keyword-arg` for the same code, so a `# pylint: disable=` that
satisfies one fails the other with `unknown-option-value`. Fix the
construct rather than suppressing the message.

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
- Batch API fetching for tags/NICs/drives (O(1) API calls per site - 5 calls total)
- Group by: site, status, tags, tenant, os_family, cluster, node
- Host variables: site info, VM identification, timestamps (created/modified), machine_type, status, resources, OS, organization (tenant/cluster/node), tags, NICs, MAC addresses, drives, IP
- JSON file caching (recommended: 1 hour timeout)
- Hostname templating

### Documentation Fragment (`plugins/doc_fragments/vergeos.py`)

Shared documentation for authentication options, included in all modules via `extends_documentation_fragment: vergeio.vergeos.vergeos`.

## Commit Message Guidelines

- **Never include IP addresses, hostnames, or other infrastructure details** in commit messages
- Use generic terms like "test environment", "live API", or "development system" instead
- This prevents leaking internal network topology in public repository history
