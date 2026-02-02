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
- Legacy `VergeOSAPI` class retained but deprecated for backward compatibility

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

**Status:** Accepted

**Context:** The existing `VergeOSAPI` and `VergeOSAPIError` classes in `module_utils/vergeos.py` may be used by external code or custom modules. Removing them immediately would be a harder breaking change.

**Decision:** Retain the legacy classes with deprecation warnings, scheduled for removal in a future version.

**Rationale:**
- Provides migration path for any external code depending on these classes
- Follows deprecation best practices (warn before remove)
- Low maintenance burden to keep unused code temporarily
- Clear documentation that new code should use SDK

**Consequences:**
- Slightly larger module_utils file
- Must remember to remove in future version (e.g., 3.0.0)
- Deprecation notices in class docstrings

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
