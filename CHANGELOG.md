# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`vm_recipe_info` module**: list VM recipes, and return a recipe's question
  set with the valid values of table-backed questions resolved from the tables
  they point at. Recipe plumbing (hidden, `database_*`, `$database` section) is
  reported separately from answerable questions, and questions carrying
  credentials are named so callers can `no_log` them.
- **`vm_recipe_deploy` module**: deploy a VM from a native VergeOS recipe.
  Answers are validated against the recipe's own questions before anything is
  created (unknown names, types, `min`/`max`/`regex`, table-backed choices,
  network answers by name), then the deploy runs server-side as a simulation,
  and only then for real. `check_mode` runs the validation and the simulation,
  making it a real preflight rather than a guess. An existing VM of the target
  name is the idempotence key.
- **`module_utils/recipe_answers.py`**: answer resolution and simulate-log
  scanning as pure functions, free of Ansible, pyvergeos and the network.
- **`module_utils/vm_recipes.py`**: pyvergeos glue for the recipe modules.
- **`vm_drive_info` module**: a VM's drives with media type and import status,
  optionally with per-drive IO counters. Separates out the drives the platform
  has not finished building, folding together `media=import` and
  `status=importing` - a drive can be the first without yet being the second,
  and that window is what makes "wait for the absence of importing" return
  before a download has started.
- **`vm_nic_info` module**: a VM's NICs, separating out those attached to no
  network at all - a state a VM reaches when a recipe's network question is
  left unanswered, and one every other check passes on.
- **`module_utils/machine.py`**: reads a VM's drives, NICs and drive IO
  counters.
- **`vm_from_recipe` role**: deploys a VM from a recipe and waits for it to be
  usable rather than merely present. Two-phase drive-import waiting with
  separate budgets, post-conditions on drives and NICs asserted on both the
  deploy and convergence paths, and optional power-on and guest-boot proof.
  This is the collection's first role, so `roles/` is new.
- **`examples/deploy_from_recipe.yml`**: discovery, preflight and deploy.
- **`examples/vm_from_recipe_role.yml`**: the same via the role, waiting for a
  bootable VM.

### Notes

- The simulated deploy is scanned rather than trusted. The API reports success
  as `{"err": "Simulation complete"}` even when its own log contains failed
  steps, so a deploy that would build a VM with no OS drive looks clean at the
  top level.
- A guest-boot proof must watch disk WRITES. On a VM that never boots the
  firmware still reads the boot sector and sends a few DHCP packets, so read
  counters and NIC transmit counters both move off zero on a guest sitting at
  "no bootable device". Measured on a 26.1.8 system four minutes after
  power-on, a booted guest against an identically configured VM with a blank
  drive - `write_bytes` 419 MB versus 0, `read_bytes` 410 MB versus 512.
- `machine_drive_stats` rows are addressed by a filter on `parent_drive`,
  never by row position: `machine_drive_stats/<n>` resolves to the row whose
  own `$key` is n, which belongs to a different drive. Measured on a 26.1.8
  system, `$key` 34 carried `parent_drive` 39, and a positional read reported
  809 MB of writes on a VM that had never been powered on.
- Recipes and VMs are matched by name client-side rather than with a
  server-side OData filter. The correct escaping for a name embedded in an
  OData string literal is not settled: pyvergeos doubles `'` SQL-style, while
  measurements against VergeOS 26.1.8 recorded backslash-escaping as the form
  the platform accepts, with the doubled form returning
  `{"err": "Invalid argument"}`. Getting it wrong does not raise - the query
  silently matches nothing, or returns an error document a caller counts as a
  result row. Matching client-side avoids the question entirely.

## [2.0.0] - 2026-02-02

### Breaking Changes

- **Python 3.9+ Required**: The collection now requires Python >= 3.9
- **Ansible 2.14+ Required**: Minimum Ansible version is now 2.14.0
- **pyvergeos SDK Required**: All modules now use the pyvergeos SDK instead of direct HTTP API calls
  - Install with: `pip install pyvergeos`
- **Inventory Plugin Replaced**: The `vergeos` inventory plugin has been replaced with `vergeos_vms`
  - New file extension: `.vergeos_vms.yml` (was `.vergeos.yml`)
  - Sites must now be configured as a list (single site = list of one)
  - Host naming changed to `{site}_{vm_name}` to prevent collisions across sites
  - `ansible_host` is **no longer set** - this is now an API-only plugin (see migration guide)

### Changed

- Migrated all modules to use the pyvergeos Python SDK for API interactions
- **Inventory plugin completely rewritten** with multi-site support:
  - Concurrent site queries using ThreadPoolExecutor
  - Site failures warn and continue (don't fail entire inventory)
  - Full tag support via `vm.get_tags()` SDK method
  - Enhanced caching with full state serialization
  - Configurable grouping: site, status, tags, tenant, os_family, cluster
- Improved error handling with specific exception types from the SDK
- Simplified module code by leveraging SDK resource objects

### Removed

- `VergeOSAPI` class removed from `module_utils/vergeos.py` (use pyvergeos SDK instead)
- `VergeOSAPIError` exception removed (SDK provides specific exception types)

### Added

- `get_vergeos_client()` factory function for creating SDK client instances
- `sdk_error_handler()` helper for consistent error handling across modules
- Support for additional SDK features (typed resources, convenience methods)
- **Multi-site inventory plugin** (`vergeos_vms`) with:
  - Concurrent queries to 100+ VergeOS sites
  - Per-site error handling (warn and continue)
  - Tag-based grouping via `vm.get_tags()` SDK method
  - API key authentication (maps to SDK `token` parameter)
  - Configurable hostname template: `{site}_{name}`
  - Full caching support with state serialization
- Unit tests for modules and inventory plugin
- Integration tests for core functionality

### Migration Guide

1. **Install the pyvergeos SDK**:
   ```bash
   pip install pyvergeos
   ```

2. **Update Python version** (if needed):
   - Ensure Python >= 3.9 is installed

3. **Update Ansible version** (if needed):
   - Ensure Ansible >= 2.14.0 is installed

4. **Existing playbooks using modules**: No changes required - the module interface remains the same

5. **Inventory plugin migration**: The `vergeos` plugin has been replaced with `vergeos_vms`:
   ```yaml
   # v1.x (removed)
   plugin: vergeio.vergeos.vergeos
   host: vergeos.example.com
   username: admin
   password: "{{ lookup('env', 'VERGEOS_PASSWORD') }}"

   # v2.x (required) - note: sites is now a list
   plugin: vergeio.vergeos.vergeos_vms
   sites:
     - name: production
       host: vergeos.example.com
       username: admin
       password: "{{ lookup('env', 'VERGEOS_PASSWORD') }}"
   ```

   **Important changes:**
   - Rename inventory files from `*.vergeos.yml` to `*.vergeos_vms.yml`
   - Host naming is now `{site}_{vm_name}` (e.g., `production_webserver01`)
   - `ansible_host` is **no longer set** - use VergeOS modules with `vergeos_site_url` and `vergeos_vm_id` hostvars
   - New grouping options: `group_by: [site, status, tags, tenant, os_family, cluster]`
   - Multi-site support: add multiple sites to the `sites` list

6. **Custom modules using legacy VergeOSAPI**: The legacy HTTP client has been removed. Migrate to the SDK:
   ```python
   # v1.x (removed)
   from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import VergeOSAPI
   api = VergeOSAPI(module)
   vms = api.get('vms')

   # v2.x (required)
   from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import get_vergeos_client
   client = get_vergeos_client(module)
   vms = [dict(vm) for vm in client.vms.list()]
   ```

## [1.0.0] - 2025-01-15

### Added

- Initial release
- Modules: vm, vm_info, vm_import, vm_snapshot, network, network_info, nic, drive, cloud_init, windows_unattend, user, member, cluster_info, file_info
- Inventory plugin with tenant/cluster filtering, caching, and constructed groups
- Documentation fragments for shared auth options
- Example playbooks for common workflows
