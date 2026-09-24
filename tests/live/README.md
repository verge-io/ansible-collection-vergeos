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
unquoted Jinja makes the whole INI file unparsable (#29).

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
| `verify-field-contract.yml` | the compare-and-map contract for `network`, `nic`, `drive`, `user` (#75) |
| `verify-nas-modules.yml` | `nas_volume` / `nas_nfs_share` — volumes, NFS exports, and the two convergence bugs they shipped with (#35) |
| `verify-vm-export.yml` | `vm_export` — configuration, a real export run, and the platform's own statistics row (#43) |
| `verify-vm-clone.yml` | `vm_clone` — clone from a snapshot, idempotent on the clone name (#42) |
| `verify-snapshot-profile.yml` | `snapshot_profile` — profiles and their periods (#39) |
| `verify-protect.yml` | the `protect` role: tag a VM, get it enrolled (#39) |
| `verify-vnet-rule.yml` | `vnet_rule` / `vnet_apply` — firewall rules and the explicit apply (#44, #19) |
| `verify-network-policy.yml` | the `network_policy` role, enforcing a rule set with one apply (#44) |
| `verify-rbac.yml` | `group` / `group_info` / `permission` and the `rbac` role (#34) |
| `verify-member.yml` | `member` add/converge/remove, and that the right membership row is taken (#92) |
| `verify-catalog.yml` | `catalog` create/converge/scope/delete, plus the apostrophe and `{brace}` name guards (#32) |
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

## Adding one

1. Name every object `zz-<ladder>-<thing>`.
2. Clean up at the **start** as well as the end; a previous run may have died.
3. Assert `changed=false` on a re-apply. That single property would have
   caught #8, #10, #18 and #59 — every recurrence of the compare-and-map
   defect class.
4. Make the failure message say what was measured, not just that an assert
   failed.
5. Finish with `assert_lab_clean.yml`, or inline the same sweep.
