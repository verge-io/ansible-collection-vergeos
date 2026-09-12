# Live-verify ladders

Each ladder drives one module (or one tightly-coupled pair) against a real
VergeOS system and asserts its way up from refusals to convergence to
teardown. They are **not** run by `pytest` or `ansible-test`; they need a
cluster, and they create and destroy real objects.

Every ladder:

- touches only objects named `zz-*`, and refuses to act on anything else;
- proves the **refusal** paths are side-effect free before proving the
  happy path;
- proves **idempotence** (a converged re-run reports `changed=false`);
- proves **check mode** reports the pending change without making it;
- tears down in `always:`, so a mid-ladder failure still cleans up, and
  then asserts **zero leftovers**.

## Running them

```bash
source ~/.config/vergeos/verify.env      # VERGEOS_HOST/_USERNAME/_PASSWORD/_INSECURE
export ANSIBLE_COLLECTIONS_PATH=<where the collection is installed>

cd tests/live
ansible-playbook verify-vm-clone.yml
ansible-playbook verify-vm-export.yml   -e nas_service=<a NAS service>
ansible-playbook verify-nas-modules.yml -e nas_service=<a NAS service>
```

Auth comes from the environment via the collection's `env_fallback`, so no
ladder hard-codes a host or a credential. `VERGEOS_TOKEN` is used by the two
`scratch_*.py` helpers when it is set, and they fall back to
username/password when it is not.

## Results — VergeOS 26.1.8, 2-node system, pyvergeos 1.2.5, 2026-09-11

| Ladder | Modules covered | Result |
|---|---|---|
| `verify-snapshot-profile.yml` | `snapshot_profile` | PASS `ok=12 changed=4 failed=0` |
| `verify-api-key.yml` | `api_key`, `api_key_info` | PASS `ok=15 changed=3 failed=0` |
| `verify-vnet-rule.yml` | `vnet_rule`, `vnet_apply`, `vnet_rule_info` | PASS `ok=15 changed=6 failed=0` |
| `verify-catalog.yml` | `catalog` | PASS `ok=18 changed=5 failed=0` |
| `verify-tenant.yml` | `tenant`, `tenant_info` | PASS `ok=16 changed=5 failed=0` |
| `verify-tenant-network.yml` | `tenant_network_block`, `tenant_external_ip` | PASS `ok=17 changed=9 failed=0` |
| `verify-auth-source.yml` | `auth_source`, `auth_source_info` | PASS `ok=19 changed=5 failed=0` |
| `verify-vm-export.yml` | `vm_export` | PASS `ok=14 changed=8 failed=0` |
| `verify-nas-modules.yml` | `nas_volume`, `nas_nfs_share` | PASS `ok=23 changed=7 failed=0` |
| `verify-vm-clone.yml` | `vm_clone` | PASS `ok=23 changed=6 failed=0` |
| `verify-file.yml` | `file`, `file_info` | PASS `ok=19 changed=6 failed=0` |

All 18 modules added in this port have live coverage. The last three
ladders were written here; the first eight came with the modules and were
re-run unchanged except for the password-auth fallback in the helpers.

## Measured platform behaviours worth knowing

- **`file`: `filesize` settles asynchronously.** Immediately after an
  upload of a 1048576-byte file the row reads `filesize: 786432`, reaching
  the true value a few seconds later. `allocated_bytes` is correct
  straight away. `verify-file.yml` polls rather than asserting once —
  asserting immediately is a flake, not a module defect. The module's own
  idempotence is unaffected because a second run happens well after the
  settle (verified: `changed=false`, *"already exists with the same size
  (1048576 bytes)"*).
- **`vm_export` and `nas_*` need a NAS service.** The lab has `nas1`.
  There is no `nas_services` endpoint — NAS services are rows in
  `vm_services`, volumes are `volumes`, NFS shares are
  `volume_nfs_shares`.
