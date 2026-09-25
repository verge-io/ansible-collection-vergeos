# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Recipe deployment.** `vm_recipe_deploy`, `vm_recipe_info`, `vm_drive_info` and `vm_nic_info`, plus the `vm_from_recipe` role. A recipe deploy is asynchronous, in that the platform returns a VM key long before the VM can boot, so the role waits for drives to finish importing and asserts post-conditions on the VM itself rather than on a deploy log. Answers are validated against the recipe's own published questions before anything is sent, so a bad answer is a refusal rather than a half-built VM.
- **`meta/runtime.yml` action group `recipe`**, so connection details can be set once with `module_defaults`. Without an action group the idiomatic `group/vergeio.vergeos.recipe` syntax fails outright rather than being ignored.
- **`docs/SDK-COMPATIBILITY.md`**, recording the collection verified against released `pyvergeos==1.2.7` and `origin/dev` (22 commits ahead): 15 live ladders, identical task counts, `failed=0` on both.

### Fixed

- **`cloud_init` and `windows_unattend`: applying the same configuration again reported `changed` on every run** (#125). Neither module compared what was stored with what was asked for. `cloud_init` PUT the datasource and rewrote every file; `windows_unattend` PUT `cloudinit_datasource: nocloud` and rewrote `/unattend.xml`, including in check mode. A GET of the file row does not return contents (`fields=all`, `fields=contents` and `fields=most` all omit them). The comparison uses `cloudinit_files.get_content()`, which returns the stored contents, and the `render` value on the list row (`no` and `No` are the same setting). The datasource is written only when it differs. `changed` is true only when a write happens, and in check mode only when a write would happen.

- **`vm`: `state: absent` in check mode claimed a running VM was deleted** (#127). The platform refuses with "Virtual Machine must be stopped to delete", and the module will not power the VM off to get around that. Check mode returned before reading the row, so a dry run reported `changed=true` and "deleted" and the real run failed. Both modes now fail with that refusal and point at `state=stopped`. A stopped VM in check mode reports "would delete".

- **`vm_import`: a second `state: present` failed with "This name is already in use"** (#129). The module posted a new import on every run, and check mode said it would create a VM that already existed. It now resolves `name` first and, when that VM exists, returns `changed=false` with its id, in check mode too. It follows the same shape as `vm_snapshot`, converging when the name is already taken.

- **`member`: every call failed, and the membership check could never succeed** (#92). The module called `users.get(username=)`, a parameter no pyvergeos release has. Membership rows identify the member by reference (`/v4/users/N` from the members table, `users/N` from a group's nested projection), and the module compared that reference to the bare username, so a present task would have re-added forever and an absent task would have removed nothing. The create posted the username as `member` instead of calling `add_user` / `remove_user`. Lookup now goes through `resolve_one`, the reference is parsed for both shapes, and writes use the SDK helpers.

- **`user`: `role` and `groups` were accepted and discarded** (#120). There is no role column on a VergeOS user, so `role: admin` created an ordinary user and reported success — including the task named "Create a new admin user". Passing `role` now fails and names `permission` and `group`. `groups` adds the user to each named group through the same membership path as `member` (reference match, `add_user`) and is additive: groups not listed are left alone. The admin and read-only examples grant that access with `permission`.

- **`vm`: `machine_subtype`, `bios_type`, and `network` are not VM fields** (#87, #75). The API accepts unknown field names with HTTP 200 and discards them, so the module reported success, the setting never applied, and every later run reported `changed`. `machine_subtype` is removed, because `machine_type` already stores the expanded form. `network` is removed; a VM reaches a network through the `nic` module. `bios_type` stays, as the readable spelling of the boolean `uefi`. The field-contract harness covers `vm`: every sent column exists, `bios_type` round-trips through `uefi`, the three option names are not columns, and a second apply reports `changed=false`.

- **`tag_category`: omitting `taggable_*` or `single_tag_selection` on update turned those flags off** (#121). Every flag defaulted to `false` in the argument spec, so a task that only changed a description sent `false` for every flag it left out. After that, tagging a new VM failed with "API error: Operation not permitted". The flags still default to `false` when a category is created. Omit them on update to leave the current values unchanged.

- **`vm`: RAM that is not a multiple of 256 never converged, and a second run lowered it** (#123). `VMManager.create` rounds RAM up to a multiple of 256 MB. The update path sent the raw value through `save()`, and the platform floors that, so `ram: 2000` stored 2048 on create and 1792 on the next identical run, then reported changed forever. The module now rounds up the same way before it compares and before it writes, so create and update agree and a converged VM stays put.

- **`nic`: removing a NIC only removes a NIC on that network** (#118). `get_nic()` returned the VM's first NIC whenever none was attached to the requested network, so `state: absent` deleted a NIC on a different network, and declaring NICs on two networks re-pointed the same one on every run and never converged. A NIC is now matched only by the network it is on. `state: absent` with no match changes nothing and deletes nothing. `state: present` adds a NIC. Moving an existing NIC, which an OVA import needs for the default NIC it arrives with, is opt-in via `nic_index`.

- **Every shipped recipe example hardcoded a storage tier that most systems do not have.** `examples/deploy_from_recipe.yml`, `examples/vm_from_recipe_role.yml`, the `vm_from_recipe` role README and three of the four `vm_recipe_deploy` EXAMPLES all carried `SELECT_OS_TIER: 4`. Tier numbering is per system, and a single tier system is perfectly normal, so as shipped these failed on such a system with `answer 'SELECT_OS_TIER' is not a valid choice`. They now read the valid tiers at run time from `vm_recipe_info` with `resolve_options: true`, so the same playbook runs anywhere. Every live test in the repository already used tier 1, which is why no test caught it.
- **`examples/vm_from_recipe_role.yml` could not succeed even with the tier corrected.** It set `vm_from_recipe_fail_on_hints: true` without answering any of the six questions that produce hints on a stock recipe, so the example always failed. The option is now off in the example, with a note on what satisfying it actually requires.
- **Two `vm_recipe_deploy` EXAMPLES omitted required answers.** They answered only `HOSTNAME` and the tier, so they failed validation on `USER` and `PASSWORD` before the simulation was reached. All five examples in the module now run clean in check mode.
- **`tests/live/localhost.ini` did not parse.** The Jinja value for `ansible_python_interpreter` was unquoted, so the INI inventory plugin rejected the whole file with `Expected key=value host variable assignment`. Ansible then fell back to implicit localhost with only a warning, which is the silent kind of failure the file was added to prevent. The value is now quoted.
- **`vm_recipe_deploy`: a one-character answer masked the digits in the module's own error messages.** `answers` is `no_log`, and ansible-core masks a `no_log` value by plain substring replacement anywhere in the return data, integers included, so the ordinary answer `YB_CPU_CORES: 1` turned *"the recipe requires at least 512"* into *"requires at least 5\*\*\*\*\*\*\*\*2"*. The module now stops masking the answers the recipe's question *types* say are not credentials, once those types are known. The invocation log, which is the path that matters for credential leakage, has already happened fully masked by then.

### Documentation

- **`docs/DEPLOYING-VMS-FROM-RECIPES.md`**, a walkthrough of the whole path from discovering what a recipe asks to tearing a VM down, including the traps that are not visible from outside: per system tier numbering, network questions defaulting to building a brand new internal network, the asynchronous deploy, why a VM name can appear as asterisks, and what `fail_on_hints` really costs.
- **Two new examples.** `examples/recipe_discover.yml` explores a system read only and sorts a recipe's questions into the groups worth thinking about. `examples/recipe_preflight.yml` validates an answer set and runs the platform's simulation without creating anything, exiting non zero when the answers would not deploy, so it works as a CI gate.
- **`examples/recipe_fleet.yml`**, building several VMs from one recipe with per VM sizing, looking the storage tier up once and preflighting the whole fleet before building any of it.
- **`vm_recipe_deploy` error messages** no longer use em dashes, so `valid: ...` now reads as an ordinary sentence.

- **`vm`: `state` semantics corrected.** `absent` will not delete a *running* VM, because the platform refuses with *"Virtual Machine must be stopped to delete"*, and the module deliberately does not stop it for you, because an `absent` that powered off a running workload in order to remove it would be a far worse default than a refusal. `stopped` does **not** create a missing VM while `running` does. Both verified against VergeOS 26.1.8; neither was previously documented.
## [2.1.0] - 2026-09-24

### Added

- **`network`: `interface_network`** (#60), the uplink a vnet reaches the
  physical fabric through. Maps to the API's `interface_vnet`; give the name
  of a physical vnet and the module resolves it to the key the API stores. An
  empty string detaches; a name that does not resolve fails by name rather
  than silently doing nothing. Without this there was **no way to build a vnet
  attached to anything** — the module accepted what looked like a VLAN-tagged
  network on the fabric, reported success, and produced a network whose tag
  was carried onto nothing.
- **`network`: `rate_limit`** (#61), in **mbytes per second** — not bits, not
  bytes. `0` is an explicit "uncapped" and is applied like any other value;
  omitting the parameter leaves the existing value alone. The two are not the
  same thing.
- **Continuous integration** (#66). The first workflow this repository has
  ever had: unit tests on ansible-core 2.15 and 2.20, `ansible-test sanity` on
  both, `ansible-lint` at the production profile, and a `ansible-galaxy
  collection build`. `sanity` and the rest are required status checks on
  `dev` and `main`, with `strict` so a branch must be up to date before it
  merges.
- **A contract harness for the compare-and-map defect class** (#75).
  `tests/live/verify-field-contract.yml` asserts, against a live system, that
  every API field a module sends exists on the resource, that every value
  round-trips, and that a second apply reports `changed=false`.
  `tests/unit/plugins/modules/test_field_contracts.py` asserts the structural
  half in CI, including that the live ladder cannot drift from the code it
  checks. Covers `network` and `nic`.

### Fixed

- **`nic`: a MAC address supplied in uppercase never converged** (#59). The
  API stores MACs lowercase with colons and returns them that way whatever was
  sent, so comparing an operator's `AA:BB:CC:00:5E:0B` against the stored
  value literally never matched and every run issued a PUT. Both sides are now
  folded to the stored form, and the create path sends it too. Case and
  separator are both insensitive, which is what `qm config` and OVF
  `rasd:Address` emit. Fourth recurrence of the class behind #8, #10 and #18 —
  and the first where the field *name* was correct and the *value comparison*
  was not.
- **The unit suite did not pass on `main`** (#66): 10 failed, 16 passed, 47
  errors. Four separate defects, none of them the environment incompatibility
  originally diagnosed — `patch.dict('sys.modules')` deleting ansible-core on
  exit; every module test patching the definition site rather than the module
  under test, so nothing was mocked and each test made live HTTPS calls; a
  `pyvergeos.exceptions` stub whose attributes were not exception classes, so
  `side_effect` returned instead of raising; and `dict(mock)` silently
  returning `{}` because `dict()` prefers the `keys` mapping protocol. Now 140
  passing tests in 0.2s.
- **`ansible-test sanity` never passed on ansible-core 2.15** (#77), the
  version `requires_ansible` advertises as the floor. 2.15 runs 46 sanity
  tests to 2.20's 34, and the run was crashing at test #2 on an unparsable
  DOCUMENTATION block, skipping the other 44. Fixing that exposed four further
  defects: six sanity-ignore files whose every entry named a file that does not
  exist, missing `__future__`/`__metaclass__` boilerplate in the unit tests,
  and an `EXAMPLES` block that was a multi-document YAML stream.
- **`build_ignore` shipped the entire test tree** (#15). `tests/` with a
  trailing slash matches nothing — patterns are fnmatch against the path
  relative to the collection root. 57 test entries in the 2.0.1 artifact, 0
  now. `.probe`, `.pytest_cache` and `.github` excluded too, since
  `ansible-galaxy` does not read `.gitignore`.
- **Two documented commands that silently did nothing** (#16).
  `examples/snapshot_workflow.yml` told the operator to delete a snapshot with
  `examples/vm_snapshots.yml --tags delete`; that playbook defines no tags, so
  the command matched zero tasks and **exited 0** with the snapshot still in
  place. It now points at `examples/delete_snapshot.yml`.
  `examples/snapshot_by_tag.yml` advertised `vm_snapshot` with `state: info`,
  which fails argument validation; listing is `operation: list`.
- **The shipped VLAN example taught a broken pattern** (#60). "Create a
  VLAN-tagged external network" carried a tag and no uplink — the exact
  combination that produces an isolated network while reporting success.
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
