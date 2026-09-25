# Live-verify ladders

Each ladder drives one module (or a tightly-coupled group) against a **real**
VergeOS system and asserts its way up from refusals, through convergence, to
teardown.

They are **not** run by `pytest` or `ansible-test` and are **not** part of CI.
They need a cluster, and they create and destroy real objects.

Every ladder:

- touches only objects named `zz-*`, and refuses to act on anything else;
- proves **idempotence** — a converged re-run reports `changed=false`;
- proves **check mode** reports the pending change without making it, where
  the module supports it;
- tears down in `post_tasks:` or `always:`, so a mid-ladder failure still
  cleans up, and then asserts **zero leftovers**.

## Running them

```bash
set -a; . ~/.config/vergeos/env; set +a     # VERGEOS_HOST / _USERNAME / _PASSWORD / _INSECURE
export ANSIBLE_COLLECTIONS_PATH=<where the collection is installed>

cd tests/live
ansible-playbook verify-field-contract.yml
ansible-playbook verify-recipe-deploy.yml
```

Auth comes from the environment through the collection's `env_fallback`, so no
ladder hard-codes a host or a credential.

### Two traps that cost real time

**Pin the interpreter.** `localhost.ini` sets `ansible_python_interpreter` for
a reason. Naming an inventory host makes Ansible discover a system Python that
has no `pyvergeos`, and every module then fails with *"The pyvergeos SDK is
required for this module"*. Playbooks run against **implicit** localhost are
unaffected, which is why it looks intermittent. The value must be quoted —
unquoted Jinja makes the whole INI file unparsable (#29). `ansible-inventory`
still exits 0 in that case and falls back to implicit localhost. What you
see is an empty host list and a warning.
`tests/unit/test_live_ladders.py` parses the file.

**A teardown that prints `ok` has not necessarily deleted anything.** A bare
`state: absent` with `failed_when: false` on a running VM reports success and
removes nothing; the platform refuses to delete a running VM. That leaked
seventeen VMs until the cluster ran out of RAM, and reported six working
recipes as broken. Stop the VM first, and assert the lab is clean afterwards.

## The lab-cleanliness sweep

`assert_lab_clean.yml` is the one to run when you think you are done:

```bash
ansible-playbook assert_lab_clean.yml
```

It reads **every** table a ladder can leave something in and fails if anything
named `zz-*` survives. That breadth is the point — an empty VM list does not
mean the lab is clean. An orphaned `vm_recipe_instance`, whose recipe had
already been deleted, matched no cleanup selector and survived several runs
precisely because nothing read that table.

## What is here

| Ladder | Covers |
|---|---|
| `verify-field-contract.yml` | the compare-and-map contract for `network`, `nic`, `drive`, `user`, and `vm` (#75, #87) |
| `verify-health-report.yml` | the `health_report` role — capacity, snapshot age and NAS verdicts (#45) |
| `verify-billing-export.yml` | the `billing_export` role — and that its numbers agree with `tenant_info` (#46, #27) |
| `verify-k3s-node.yml` | the `k3s_node` role — clone, render, cloud-init, boot, adopt (#50) |
| `verify-lb-stack.yml` | the `lb_stack` role — haproxy/keepalived rendered as code (#51) |
| `verify-image-pipeline.yml` | the `image_pipeline` role — qcow2 to OVA to a versioned golden template (#49) |
| `verify-vm-backup.yml` | the `vm_backup` role — snapshot schedule, export volume, NFS exposure, export retention (#52) |
| `verify-restore-drill.yml` | the `restore_drill` role — and that a drill which should fail does (#53) |
| `verify-rebalance-advisor.yml` | the `rebalance_advisor` role — and that the live collector produces every field the planner reads (#47) |
| `verify-tier-policy.yml` | the `tier_policy` role — drift, enforcement, and the tiers the platform accepts but does not have (#48) |
| `verify-node-drain.yml` | `node_info` / `node_maintenance` and the `node_drain` role — a real drain and restore, on a node picked for being empty (#36) |
| `verify-rolling-update.yml` | `update` / `update_info` and the `rolling_update` role — staging, the capacity gate and both consent gates. The reboot itself is not exercised; see the header (#41) |
| `verify-drive-health.yml` | `physical_drive_info` and the `drive_health` role — SMART triage, and that every flag it reads is a real column (#37) |
| `verify-auth-source.yml` | `auth_source` / `auth_source_info` — SSO, and that a settings update replaces rather than merges (#31) |
| `verify-file.yml` | `file` / `file_info` — upload, the `filesize` settle window, metadata drift (#33) |
| `verify-tenant.yml` | `tenant` / `tenant_info` — nodes, storage, power, and the placement field issue #24 turns on (#40) |
| `verify-tenant-network.yml` | `tenant_external_ip` / `tenant_network_block` (#40) |
| `verify-nas-modules.yml` | `nas_volume` / `nas_nfs_share` — volumes, NFS exports, and the two convergence bugs they shipped with (#35) |
| `verify-vm-export.yml` | `vm_export` — configuration, a real export run, and the platform's own statistics row (#43) |
| `verify-vm-clone.yml` | `vm_clone` — clone from a snapshot, idempotent on the clone name (#42) |
| `verify-snapshot-profile.yml` | `snapshot_profile` — profiles and their periods (#39) |
| `verify-protect.yml` | the `protect` role: tag a VM, get it enrolled (#39) |
| `verify-vnet-rule.yml` | `vnet_rule` / `vnet_apply` — firewall rules and the explicit apply (#44, #19) |
| `verify-vm-power.yml` | `vm` power waits — a budget that cannot be met fails instead of reporting success after sixty seconds (#114), and `state: absent` on a running VM is refused in check mode too (#127) |
| `verify-vm-snapshot.yml` | `vm_snapshot` — never-expire and in-place restore, the two defects that made a golden snapshot vanish overnight and made restore always fail (#119, #128) |
| `verify-network-power.yml` | `network` powering a vnet on and off — a rule staged on a stopped router, started, and read back live (#97) |
| `verify-network-policy.yml` | the `network_policy` role, enforcing a rule set with one apply (#44) |
| `verify-rbac.yml` | `group` / `group_info` / `permission` and the `rbac` role (#34) |
| `verify-member.yml` | `member` add/converge/remove, and that the right membership row is taken (#92) |
| `verify-user-groups.yml` | `user` `groups` add/converge, and that `role` is rejected instead of discarded (#120) |
| `verify-catalog.yml` | `catalog` create/converge/scope/delete, plus the apostrophe and `{brace}` name guards (#32) |
| `verify-cloudinit.yml` | `cloud_init` and `windows_unattend`: applying the same configuration again reports `changed=false`, and check mode reports a write only when one would happen (#125) |
| `verify-api-key.yml` | `api_key` / `api_key_info`, including that a revoked secret really is dead (#30) |
| `verify-key-rotation.yml` | the `api_key_rotation` role: verify-before-revoke (#30) |
| `verify-recipe-deploy.yml` | `vm_recipe_deploy` end to end |
| `verify-recipe-matrix.yml` | every recipe on the system, simulated |
| `verify-recipe-real.yml` | real deployments — boot proved, DHCP confirmed, torn down |
| `verify-recipe-scenarios.yml` | answer-file scenarios |
| `verify-recipe-custom.yml` | custom recipes and catalogs |
| `verify-recipe-edges.yml` | edge cases and refusal paths |
| `verify-recipe-fuzz.yml` | adversarial answers |
| `verify-recipe-bulk.yml` | bulk deployment |
| `verify-recipe-concurrency.yml` | concurrent deploys |

Helpers, not run directly:

| File | Purpose |
|---|---|
| `assert_lab_clean.yml` | the cleanliness sweep described above |
| `recipe_scratch_teardown.yml` | tear down scratch recipe objects |
| `recipe_real_teardown.yml` | tear down real deployments |
| `recipe_real_cleanup.yml` | reconcile anything a failed run left |
| `scratch_net_tasks.yml` | create/delete the scratch network the firewall ladders run against |
| `recipe_real_one.yml`, `recipe_bulk_one.yml`, `recipe_concurrency_worker.yml` | worker playbooks invoked by the ladders above |
| `ansible.cfg`, `localhost.ini` | shared config and the interpreter pin |

### Not here yet

Nothing. `verify-recipe-custom.yml`, `verify-recipe-fuzz.yml` and
`verify-recipe-edges.yml` were excluded from `ansible-lint` until `catalog`
(#32) and `api_key` (#30) existed; both landed, all three exclusions are gone,
and `.ansible-lint` now has no `exclude_paths` at all.
`tests/unit/test_module_references.py` still audits every
`vergeio.vergeos.*` reference in the repository, against an allowlist that is
now empty.

The remaining ladders from the port land with their modules — see the
tracking issue #57. `verify-nas-modules.yml` and `verify-vm-export.yml` landed
together, because the export ladder needs a scratch NAS volume and the ported
version made one by shelling out to a `scratch_vol.py` that is not in this
repository. With `nas_volume` in the same change, the fixture is a module call.

Both need a NAS service named with `-e nas_service=<name>`. It cannot be
discovered: NAS services are rows in `vm_services`, and so is the cluster's own
Services VM, with no column separating them. Both ladders print the candidates
rather than guessing.

## The check-mode sweep

Nothing in CI runs a playbook against a live system, so nothing runs the
shipped examples the way an operator does. Issue #28 was six roles aborting
under `--check` with a `from_json` stack trace — all six read-only reporters,
which is exactly when someone reaches for `--check`.

Two things guard it now, and they cover different halves:

- `tests/unit/test_role_check_mode.py` runs in CI, with no cluster. It insists
  that any variable parsed with `from_json` is produced by a task carrying
  `check_mode: false` (or that the parse itself tolerates an empty stdout,
  for a command that writes). It reads role task files and every playbook
  under `tests/live/`. That is #28's exact shape.
- The `examples parse` CI job loads every example as a playbook. That catches a
  broken example, not a broken role.

Neither replaces actually running them. When you have a cluster:

```bash
set -a; . ~/.config/vergeos/env; set +a
for f in examples/*.yml; do
  ansible-playbook --check "$f" || echo "FAILED: $f"
done
```

Read the failures rather than treating the list as a pass/fail. Several are
**correct**: `delete_snapshot.yml` and `vm_snapshots.yml` refuse without `-e`,
which is the behaviour they are supposed to have. Others fail because check
mode does not actually create the object a later task then looks up — inherent
to a create-then-configure playbook, not a defect. What you are looking for is
a *stack trace*.

## Adding one

1. Name every object `zz-<ladder>-<thing>`.
2. Clean up at the **start** as well as the end; a previous run may have died.
3. Assert `changed=false` on a re-apply. That single property would have
   caught #8, #10, #18, #59 and #87 — every recurrence of the compare-and-map
   defect class.
4. A module with a create/update path declares `UPDATE_FIELD_MAP` (parameter
   name to API column) and is listed in `MAPPED_MODULES` in
   `tests/unit/plugins/modules/test_field_contracts.py`. A declared map that
   is not in that list fails CI. Add the same column list to
   `verify-field-contract.yml`. The unit test keeps the ladder's list equal
   to the map, and the ladder checks that every sent field exists, every
   value round-trips, and a second apply reports `changed=false`.
5. Make the failure message say what was measured, not just that an assert
   failed.
6. Finish with `assert_lab_clean.yml`, or inline the same sweep.
