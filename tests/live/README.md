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
| `verify-catalog.yml` | `catalog` create/converge/scope/delete, plus the apostrophe and `{brace}` name guards (#32) |
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
| `recipe_real_one.yml`, `recipe_bulk_one.yml`, `recipe_concurrency_worker.yml` | worker playbooks invoked by the ladders above |
| `ansible.cfg`, `localhost.ini` | shared config and the interpreter pin |

### Not here yet

`verify-recipe-edges.yml` calls `vergeio.vergeos.api_key`, which arrives with
#30, so it is still excluded from `ansible-lint` — see `.ansible-lint`, and
`tests/unit/test_module_references.py`, which audits every
`vergeio.vergeos.*` reference against a self-expiring allowlist.

`verify-recipe-custom.yml` and `verify-recipe-fuzz.yml` came off that list
when `catalog` landed with #32.

The remaining ladders from the port (`verify-vm-clone`, `verify-vm-export`,
`verify-nas-modules`, `verify-snapshot-profile` and others)
land with their modules — see the tracking issue #57.

## Adding one

1. Name every object `zz-<ladder>-<thing>`.
2. Clean up at the **start** as well as the end; a previous run may have died.
3. Assert `changed=false` on a re-apply. That single property would have
   caught #8, #10, #18 and #59 — every recurrence of the compare-and-map
   defect class.
4. Make the failure message say what was measured, not just that an assert
   failed.
5. Finish with `assert_lab_clean.yml`, or inline the same sweep.
