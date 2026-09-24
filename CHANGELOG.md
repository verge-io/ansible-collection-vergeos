# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **`network`: three API field mappings the platform silently discarded**
  (#18). `dhcp_end` was sent as `dhcp_end` where the API field is `dhcp_stop`,
  so a DHCP scope was created with a start and no end; `dns_servers` was sent
  verbatim on the update path where the field is `dnslist`, so DNS never
  applied to an existing network; `subnet_mask` does not exist in the vnet
  schema at all. The API accepts unknown fields with HTTP 200 and discards
  them, so all three reported success. Third recurrence of the class behind
  #8 and #10.
- **`network`: never converged.** Setting any of the three parameters above
  made every run report `changed`, because the update path compared against
  API fields that do not exist. `get_network()` now also fetches the fields it
  diffs -- the SDK's default field set omits `dnslist`.
- **`network`: `network_type` choices were wrong in both directions** (#18).
  `vlan` and `overlay` were offered and rejected by the platform; `dmz` is
  valid and was blocked. VLAN networks were unreachable through this module
  and can now be created with `layer2_type: vlan` plus `vlan_id`.
- **`network`: `DOCUMENTATION` was unparsable** (#17). An unquoted
  `C(Validation error: Gateway is outside of network)` contains `": "`, which
  YAML reads as a mapping key inside a plain scalar, taking the whole block
  with it: `ansible-doc` reported the module as undocumented and
  `validate-modules` raised 28 errors. Now a folded scalar.
- **`network_info` returned 25 of 98 fields**, hiding `dnslist`, port
  mirroring, rate limits and more. Now returns the whole resource plus the two
  aliased status joins (#25): `all` expands to the vnet's own columns and
  never includes a traversal, so requesting it alone would have dropped
  `running` and `status`. Note the field list is passed as a list: on
  pyvergeos 1.2.7 the string `"all"` made the SDK send a per-character field
  list and the API returned a single field (pyvergeos#101, fixed on pyvergeos
  dev; the list form is correct on every version this collection supports).
- **`vm`: `machine_type` never converged.** The platform expands the alias
  `q35` to `pc-q35-10.0`, so comparing the alias literally never matched and
  every run reported `changed`. Aliases now match any version of their family,
  and an exact machine type can be pinned -- the `choices` list previously
  allowed only aliases, which made remediating a deprecated machine type
  impossible.
- **`user`: supplying `user_password` made every run report `changed`.**
  VergeOS never returns a stored password, so there is nothing to compare;
  the password was rewritten unconditionally. New `update_password` option,
  defaulting to `on_create`. Use `always` for rotation.
- **`vm_snapshot`: creating an existing snapshot name failed** instead of
  converging, so any play taking a named snapshot could not be re-run.
- **"The pyvergeos SDK is required" misdiagnosed the cause.** When Ansible
  runs a module under a discovered interpreter that lacks pyvergeos, the
  message told the operator to install a package that was already installed.
  It now names the interpreter it actually ran under and points at the two
  real fixes (`delegate_to: localhost`, or pinning
  `ansible_python_interpreter`). The inventory example carries the same note:
  hosts from the inventory plugin are VM records, not reachable machines, so
  module tasks in those plays must delegate.
- **`examples/snapshot_by_tag.yml`**: the summary play referenced
  `snapshot_prefix`, a var set in a *different* play, so it failed with
  "'snapshot_prefix' is undefined".
- **`examples/vm_snapshots.yml`** was hardcoded to `web-server-01`, so it
  either failed outright or -- worse -- operated on whatever real VM carried
  that name. It now requires `-e vm_name=`, and the destructive restore step
  is opt-in behind `-e allow_restore=true`.
- **`examples/setup_tags.yml`** defaulted to `sitea.example.com` /
  `siteb.example.com`, which do not resolve, so it could not run as shipped.
  It now defaults to the single system in `VERGEOS_HOST`.
- **`examples/create_vm.yml`** used the legacy
  `ip_address` + `subnet_mask` + `gateway` form the API now rejects with
  "Validation error: Gateway is outside of network".

### Removed

- **`network`: the `subnet_mask` parameter.** No such field exists in the vnet
  schema, so it was never applied -- the value went into the request body and
  was discarded with HTTP 200. Supplying it now fails argument validation
  rather than being silently ignored. Use `network` (CIDR) instead, which the
  API requires for vnet creation.

### Changed

- **`network`: `version_added` for the `network` (CIDR) option corrected from
  `2.0.1` to `2.1.0`.** `validate-modules` rejects a patch release, and the
  collection's next release carrying a documented option is a minor.

## [2.0.1] - 2026-09-21

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
