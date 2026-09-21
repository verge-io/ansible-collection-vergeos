# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- All modules accept an `api_key` parameter (env fallback `VERGEOS_API_KEY`) for Bearer-token authentication, completing the token-auth story that the inventory plugin already had. `api_key` takes precedence over `username`/`password` and bypasses 2FA/TOTP enforcement. The shared documentation fragment now covers it. (verge-io/pyVergeOS#85)

### Changed

- `requires_ansible` floor raised from `>=2.14.0` (EOL, uninstallable on current controllers) to `>=2.15.0`, verified live on ansible-core 2.15.13/Python 3.11 and 2.21/Python 3.14. Repository now passes `ansible-lint` at the production profile with zero failures (was 111): fixed duplicate `plugin`/`sites` keys in the inventory plugin's EXAMPLES, auto-fixed task-name casing across tests and examples, and annotated intentional `ignore_errors`/`run_once` usages. (verge-io/pyVergeOS#84)

### Fixed

- `vm`, `nic`, `drive`, `network` and `user` modules never persisted updates. They set attributes on the SDK object and called a bare `save()`, which sends only keyword arguments, so the API received an empty `PUT` (or no request at all for users) while the module reported `changed: true`. Updates now call `save(**update_data)`. (verge-io/pyVergeOS#80)
- `vm`, `nic` and `user` no longer default `enabled` to `true`. The default applied on every update, so any partial change to a deliberately disabled resource would re-enable it. `enabled` still defaults to `true` on create; omit it on update to leave the current state unchanged. (verge-io/pyVergeOS#83)
- `cloud_init` with `state: absent` failed on the first step: it sent `cloudinit_datasource=''`, which VergeOS rejects (the supported disable value is `none`), leaving the datasource enabled and the files in place. The module now sends `none`, only writes when the datasource is not already disabled, and reports `changed` accurately. (verge-io/pyVergeOS#82)
- `cloud_init` no longer declares `hostname` mutually exclusive with `user_data`/`meta_data`. The generation logic already treats `hostname` as a gap-filler, so explicit content wins and `hostname` fills only whichever file was omitted — unblocking the common VM-import case of supplying `user_data` plus `hostname`. (verge-io/pyVergeOS#82)
- `network` module router-IP updates never converged: the update path compared and sent `ip_address`, but the API field is `ipaddress`, so the module reported `changed: true` forever while the server ignored the value. The update mapping also gains `network` (CIDR) so it takes effect once the parameter lands in the argument spec. (#10)
- `drive` module tier updates never converged: the update path compared and sent a nonexistent `tier` field instead of the API's `preferred_tier`, so the module reported `changed: true` forever while the server ignored the value. Updates now read and write `preferred_tier`. (#8)

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
