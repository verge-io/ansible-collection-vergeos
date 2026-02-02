# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-02-02

### Breaking Changes

- **Python 3.9+ Required**: The collection now requires Python >= 3.9
- **Ansible 2.14+ Required**: Minimum Ansible version is now 2.14.0
- **pyvergeos SDK Required**: All modules now use the pyvergeos SDK instead of direct HTTP API calls
  - Install with: `pip install pyvergeos`

### Changed

- Migrated all modules to use the pyvergeos Python SDK for API interactions
- Migrated inventory plugin to use pyvergeos SDK
- Improved error handling with specific exception types from the SDK
- Simplified module code by leveraging SDK resource objects

### Deprecated

- `VergeOSAPI` class in `module_utils/vergeos.py` is deprecated and will be removed in v3.0.0
- `VergeOSAPIError` exception is deprecated and will be removed in v3.0.0
- Use `get_vergeos_client()` and pyvergeos SDK instead

### Added

- `get_vergeos_client()` factory function for creating SDK client instances
- `sdk_error_handler()` helper for consistent error handling across modules
- Support for additional SDK features (typed resources, convenience methods)
- Unit tests for modules
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

4. **Existing playbooks**: No changes required to playbooks - the module interface remains the same

5. **Custom modules using VergeOSAPI**: Migrate to using `get_vergeos_client()`:
   ```python
   # Before (deprecated)
   from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import VergeOSAPI
   api = VergeOSAPI(module)
   vms = api.get('vms')

   # After (recommended)
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
