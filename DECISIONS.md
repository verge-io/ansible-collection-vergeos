# Architecture Decision Records

This document captures key design decisions made during the development of the VergeOS Ansible Collection (vergeio.vergeos).

---

## ADR-001: Use pyvergeos SDK Instead of Direct HTTP API Calls

**Date:** 2026-02-02

**Status:** Accepted

**Context:** The Ansible collection currently uses direct HTTP API calls via a custom `VergeOSAPI` class in `module_utils/vergeos.py`. The pyvergeos Python SDK exists at `/Users/dzarzycki/verge/pyvergeos` and provides comprehensive coverage of the VergeOS API (167+ endpoints vs ~15 in the collection). Maintaining two separate HTTP implementations creates duplication and the SDK offers more features.

**Decision:** Refactor all Ansible modules and the inventory plugin to use the pyvergeos SDK instead of direct HTTP calls.

**Rationale:**
- SDK covers 10x more functionality (71 resource modules vs 14 in Ansible)
- Single point of maintenance for API interactions
- SDK provides typed resource objects with convenience methods (`.save()`, `.delete()`)
- Better error handling with specific exception types
- Enables rapid addition of new modules by leveraging SDK capabilities

**Consequences:**
- Breaking change requiring major version bump (2.0.0)
- External dependency on pyvergeos package
- Users must install pyvergeos via pip before using the collection
- Existing playbooks continue to work (API unchanged at module level)
- Legacy `VergeOSAPI` class removed (see ADR-004)

---

## ADR-002: Require Python 3.9+ and Ansible 2.14+

**Date:** 2026-02-02

**Status:** Accepted

**Context:** The pyvergeos SDK requires Python 3.9+. Modern Ansible versions have moved to requiring newer Python versions. We need to establish minimum version requirements.

**Decision:** Require Python >= 3.9 and Ansible >= 2.14.0 for the collection.

**Rationale:**
- pyvergeos SDK requires Python 3.9+
- Ansible 2.14+ has stable collection support and modern features
- Python 3.9 is widely available (released October 2020)
- Aligns with Ansible community collection standards

**Consequences:**
- Users on older Python/Ansible versions cannot use v2.0.0+
- Clear documentation of requirements needed
- `meta/runtime.yml` enforces Ansible version requirement

---

## ADR-003: External Dependency Model for pyvergeos

**Date:** 2026-02-02

**Status:** Accepted

**Context:** The pyvergeos SDK could be vendored (copied into the collection) or required as an external dependency. Need to decide distribution model.

**Decision:** Use external dependency model - users install pyvergeos separately via pip.

**Rationale:**
- Avoids code duplication and version drift
- SDK can be updated independently of the collection
- Standard practice for Ansible collections with Python dependencies
- `meta/ee-requirements.txt` supports Ansible Execution Environments
- Simpler maintenance - SDK bugs fixed in one place

**Consequences:**
- Additional installation step for users (`pip install pyvergeos`)
- Must document installation requirements clearly
- Collection depends on SDK versioning/stability
- Execution Environment builds automatically include the dependency

---

## ADR-004: Retain Deprecated Legacy HTTP Client

**Date:** 2026-02-02

**Status:** Rejected

**Context:** The existing `VergeOSAPI` and `VergeOSAPIError` classes in `module_utils/vergeos.py` may be used by external code or custom modules. Removing them immediately would be a harder breaking change.

**Decision:** ~~Retain the legacy classes with deprecation warnings, scheduled for removal in a future version.~~

**Rejected:** Remove the legacy HTTP client classes entirely. Go all-in on the pyvergeos SDK with no backward compatibility shim.

**Rationale:**
- Clean break simplifies the codebase
- No known external dependencies on the legacy classes
- v2.0.0 is already a breaking change, so bundle all breaking changes together
- Reduces maintenance burden and potential confusion
- Users upgrading to v2.0.0 must adapt to SDK regardless

**Consequences:**
- Cleaner, smaller module_utils file
- No deprecated code to maintain or eventually remove
- Any external code using legacy classes must migrate immediately to SDK

---

## ADR-005: SDK Client Factory Pattern

**Date:** 2026-02-02

**Status:** Accepted

**Context:** Each Ansible module needs to create a VergeClient instance from the module parameters. Need a consistent, reusable approach.

**Decision:** Implement `get_vergeos_client(module)` factory function in `module_utils/vergeos.py` that creates SDK client from Ansible module params.

**Rationale:**
- Single place to handle client creation logic
- Consistent parameter mapping (e.g., `insecure` → `verify_ssl=False`)
- Centralized error handling for missing SDK
- Matches existing `vergeos_argument_spec()` pattern

**Consequences:**
- All modules use identical client initialization
- Parameter mapping logic in one place
- Easy to extend with additional options (timeout, etc.) later

---

## ADR-006: Centralized SDK Exception Handler

**Date:** 2026-02-02

**Status:** Accepted

**Context:** The pyvergeos SDK raises specific exceptions (NotFoundError, AuthenticationError, etc.). Ansible modules need to convert these to `module.fail_json()` calls consistently.

**Decision:** Implement `sdk_error_handler(module, e)` function that maps SDK exceptions to appropriate fail_json calls.

**Rationale:**
- Consistent error messages across all modules
- Single place to handle exception-to-message mapping
- Reduces boilerplate in each module
- Easy to add new exception types later

**Consequences:**
- All modules use same error handling pattern
- Error messages are consistent and informative
- Must keep handler updated if SDK adds new exception types

---

## ADR-007: Module Migration Order

**Date:** 2026-02-02

**Status:** Proposed

**Context:** There are 14 modules to migrate plus the inventory plugin. Need to decide migration order for Phase 2.

**Decision:** Migrate in order of complexity, starting with simple read-only modules:
1. Info modules (vm_info, network_info, cluster_info, file_info) - read-only, simple
2. Core resource modules (vm, network, user) - complex state handling
3. Sub-resource modules (drive, nic, cloud_init, windows_unattend) - depend on parent resources
4. Complex modules (vm_import, vm_snapshot, member) - special handling needed
5. Inventory plugin - last, after all modules working

**Rationale:**
- Start simple to validate patterns
- Build confidence before tackling complex modules
- Sub-resource modules can reference working parent modules
- Inventory plugin tests all underlying functionality

**Consequences:**
- Incremental, testable progress
- Early validation of SDK integration patterns
- Can commit working modules as completed

---

## ADR-008: Use GPL-3.0-or-later License

**Date:** 2026-02-03

**Status:** Accepted

**Context:** The collection was initially using MIT license headers. During sanity testing, Ansible's `validate-modules` test flagged all modules with `missing-gplv3-license` errors. While this check can be ignored, we needed to decide on the appropriate license for an Ansible collection.

**Decision:** Use GPL-3.0-or-later license for all modules, plugins, and module utilities.

**Rationale:**
- Ansible's `validate-modules` sanity test requires GPL-3.0+ headers by default
- Aligns with Ansible ecosystem standards and community collections
- Enables potential future inclusion in Ansible Galaxy certified content
- Passes all sanity checks without requiring ignore entries
- GPL-3.0 is compatible with the pyvergeos SDK dependency

**Consequences:**
- All module files include GPL-3.0-or-later SPDX header
- `galaxy.yml` specifies GPL-3.0-or-later license
- Users and contributors must comply with GPL-3.0 terms
- Derivative works must also be GPL-3.0 licensed

---

## ADR-009: Use ansible-test for Unit Tests Instead of Plain pytest

**Date:** 2026-02-04

**Status:** Accepted

**Context:** Unit tests for the inventory plugin failed when run with plain `pytest` due to YAML parsing errors. Importing Ansible modules triggers loading of `ansible/config/base.yml`, which uses custom YAML tags that require Ansible's special loader. Plain pytest doesn't set up this environment correctly.

**Decision:** Use `ansible-test units` to run unit tests instead of invoking pytest directly.

**Rationale:**
- `ansible-test units` properly initializes the Ansible environment before running tests
- It handles the namespace package structure (`ansible_collections/vergeio/vergeos/`)
- It installs required test dependencies automatically with `--requirements`
- It's the standard testing approach for Ansible collections
- Plain pytest fails with `yaml.constructor.ConstructorError: could not determine a constructor for the tag None`

**Test Command:**
```bash
# From collection root, sync to ansible_collections structure
rsync -a . /tmp/ansible_collections/vergeio/vergeos/

# Run unit tests
cd /tmp/ansible_collections/vergeio/vergeos
ansible-test units tests/unit/plugins/inventory/ --local --python 3.13 --requirements
```

**Consequences:**
- Tests must be run from an `ansible_collections/{namespace}/{collection}/` directory structure
- Python version limited to ansible-test supported versions (3.8-3.13 as of ansible-core 2.20)
- Cannot use plain `pytest` command directly from project root
- CI/CD pipelines must use `ansible-test units` for unit test execution
- Local development requires syncing to `/tmp/ansible_collections/` or similar

---

## ADR-010: Batch API Fetching for Inventory Plugin Performance

**Date:** 2026-02-04

**Status:** Accepted

**Context:** The inventory plugin needs to fetch VMs, their tags, and their NICs from each VergeOS site. The initial implementation used per-VM API calls: after fetching the VM list, it called `vm.get_tags()` and `vm.nics.list()` for each VM. This resulted in `1 + 2N` API calls per site (where N = number of VMs). For a site with 100 VMs, this meant 201 API calls, causing significant latency especially over high-latency networks.

**Alternatives Considered:**
1. **Per-VM sequential** - Simple but O(n) API calls, poor performance
2. **Per-VM parallel** - ThreadPoolExecutor for concurrent per-VM calls, still O(n) API calls but better latency
3. **Batch API fetching** - Use bulk endpoints to fetch all data in constant API calls

**Decision:** Use batch API fetching via the SDK's internal `_request()` method to access bulk endpoints (`tag_members`, `machine_nics`) that return all data in single calls, then join data client-side.

**Implementation:**
```python
# 4 API calls total per site (constant, regardless of VM count):
vms = client.vms.list()                                    # 1. All VMs
tags = client.tags.list()                                  # 2. Tag definitions
tag_members = client._request('GET', 'tag_members', ...)   # 3. All tag->VM mappings
all_nics = client._request('GET', 'machine_nics', ...)     # 4. All NICs
# Then join data client-side by machine ID
```

**Rationale:**
- Reduces API calls from O(n) to O(1) per site
- For 100 VMs: 201 calls → 4 calls (50x reduction)
- For 500 VMs: 1001 calls → 4 calls (250x reduction)
- Network latency impact reduced dramatically for geographically distributed sites
- Client-side joins are fast (in-memory dictionary lookups)

**Consequences:**
- Uses SDK internal method `_request()` which could change in future SDK versions
- Requires understanding of VergeOS API structure (`tag_members` uses `member: "vms/34"` format)
- Graceful degradation: if batch endpoints fail, tags/NICs are simply empty (not fatal)
- Site-level parallelism (ThreadPoolExecutor) still used for multi-site queries
- Per-VM parallelism no longer needed and was removed

**Performance Results (46 VMs across 2 sites):**
| Approach | API Calls | Time |
|----------|-----------|------|
| Sequential | 98 | ~0.93s |
| Per-VM parallel | 98 | ~0.5-1.0s |
| Batch fetching | 8 | ~0.35-0.45s |

---
