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

## Results — recipe suite, VergeOS 26.1.8, pyvergeos 1.2.7, 2026-09-21

`vm_recipe_deploy` and `vm_recipe_info` get five ladders rather than one,
because the interesting behaviour is spread across four axes that do not
combine into a single run: one recipe end to end, every recipe at once,
the module's own parameters, two deploys at once, and an answer validator
attacked on purpose.

| Ladder | Covers | Result |
|---|---|---|
| `verify-recipe-deploy.yml` | one recipe, refusals → deploy → convergence → teardown | PASS `ok=25 changed=2 failed=0 rescued=4` |
| `verify-recipe-matrix.yml` | all 32 recipes simulated in check mode | PASS `ok=10` — 29 clean, 3 blocked-as-declared |
| `verify-recipe-scenarios.yml` | `prune_unknown`, `fail_on_hints`, `catalog`, answer types | PASS `ok=28 failed=0` |
| `verify-recipe-concurrency.yml` | two deploys racing for one VM name | PASS `ok=19 changed=3 failed=0` |
| `verify-recipe-fuzz.yml` | 34 hostile answer sets against a hand-authored recipe | PASS `ok=64 changed=4 failed=0` |
| `verify-recipe-real.yml` | all 30 deployable recipes DEPLOYED, powered on, boot-proved | PASS 30/30 |
| `verify-recipe-custom.yml` | a recipe authored from scratch, then deployed from | PASS `ok=190 changed=16 failed=0` |
| `verify-recipe-edges.yml` | the parameters and branches the other seven never reach | PASS `ok=106 changed=20 failed=0` |
| `verify-recipe-bulk.yml` | a fleet of one OS, serially and then all at once | PASS `ok=299 changed=21 failed=0` |

Only `verify-recipe-deploy.yml`, `-concurrency`, `-fuzz` and `-real` create
anything. The fuzz ladder builds its own catalog, source VM and recipe
because no stock recipe carries the constraints it needs to attack; its
34 deploy cases then all run in check mode, and rung 8 asserts nothing was
built. Each has been run three times with identical results and zero
leftovers.

The fuzz table is falsifiable, which was checked rather than assumed: with
one case's `matching` string changed to the wrong reason, rung 3e fails;
with one valid case declared `expect: refuse`, rung 3c fails.

## The real sweep — 28 of 28, VergeOS 26.1.8, 2026-09-21

`verify-recipe-real.yml` deploys every recipe not in its skip list for real,
powers it on, and proves the guest booted by watching for disk **writes** —
read counters and NIC transmit counters both move on a VM sitting at "no
bootable device", so neither is usable as a signal. Every one of the 28 also
took a DHCP lease on DMZ, which is a second, independent sign the guest got
all the way up.

Two recipes are skipped with reasons (`Services`, because this appliance must
never get a second NAS, and `Tenant Crash Cart`, which is tenant-scoped).
`New VM` is deployed but not boot-checked: it builds a blank unformatted
drive by design, and is the negative control the boot check must *not* pass.

**Both Windows evaluations deploy, install and boot** — 2022 wrote 5.78 GB
and 2025 wrote 5.64 GB before taking DHCP leases. They had previously been
declared blocked here on the grounds that `WINDOWS_ISO` is a list over the
`files` table whose filter matches nothing on this appliance. The filter
really does resolve to `[]`, and the 2025 recipe's filter really does name
the *2022* ISO — but none of that matters, because the answer the recipe
wants is the **download URL** from the question's own default, not a row
from that table. The blocker was inferred from a plausible API message
(`Missing required answer to 'Windows 2025 ISO'`) and never tested. Two
working recipes went untested for as long as the entry stood.

Write volumes at first observation ranged from 11 MB (AlmaLinux 9) to 466 MB
(Ubuntu 22.04). That is a floor caught the moment the guest started writing,
not a total.

## Checking the lab is clean — `assert_lab_clean.yml`

Each ladder asserts its own prefix is gone. That is the right check for that
ladder and a blind one for the suite: nothing looked across all of them at
once, and nothing looked at `vm_recipe_instances` at all.

The gap was not hypothetical. An instance named `zz-from-custom`, pointing at
a recipe key that had already been deleted, survived several ladder runs — the
teardown selected instances **by recipe key**, so an orphan matched nothing,
and the leftover assertion never read that table. It surfaced only because the
system reported 33 recipes where it had previously had 32.

Two fixes: the shared teardown now matches instances by name as well as by
recipe key, and `assert_lab_clean.yml` sweeps `vms`, `vm_recipes`, `catalogs`,
`vm_recipe_instances` and `vnets` for anything `zz-`. A VM list looking empty
does not mean the lab is clean.

## Fleets — the thing a recipe is actually for

Every other ladder deploys ONE VM per play. Nobody keeps a recipe to build one
machine. `verify-recipe-bulk.yml` builds `bulk_count` (default 4) VMs of the
same OS from one recipe, twice: serially through `vm_from_recipe` in a loop,
then — after tearing the first fleet down — the same names again from
`bulk_count` **concurrent `ansible-playbook` processes**. Both passed, with
distinct VM keys, distinct drives, every NIC on DMZ, and a full re-run
converging without rebuilding anything.

Two things it checks that only exist at fleet scale:

- **Shared evidence.** Two members reporting the same VM key or the same
  drive would mean one was judged on another's results. The drive check is
  aimed at a defect that was real once: IO counters are addressed by a filter
  on `parent_drive` rather than by path key, because
  `machine_drive_stats/<n>` resolves to the row whose *own* `$key` is `n` —
  which belongs to a different drive. With one VM per play that is invisible.
- **Simultaneous load.** Four deploys of the same cached image at once,
  competing for the same recipe and the same tier.

### Two notes on the fact-reset in `recipe_bulk_one.yml`

It is there because `include_role` leaves its facts set and a loop runs in one
variable scope. Measured by deleting it, the risk is narrower than that
sounds: **a skipped task still registers**, so Ansible writes
`{'skipped': true}` over the variable and a gated task that does not run does
*not* leave the previous value behind. Only a task never *reached* leaves
stale data, and in this ladder an abort ends the loop anyway. The reset stays
as belt and braces, and the seeded run confirmed rung 2c fires — on missing
evidence rather than shared evidence.

## The edges — parameters and branches nothing else reached

`verify-recipe-edges.yml` was written by walking `vm_recipe_deploy`'s
parameters and `module_utils` branches and asking which had never executed
against a real system. Four had not: `simulate: false`, `auto_update`, the
`is_snapshot` skip in `find_vm_by_name()`, and `power_on` against a disabled
VM. It runs in about a minute, because its recipe is built from a blank 1 GB
VM — none of these rungs care what the guest is.

What it established:

- **`simulate: false` removes the only preflight a deploy has.** The stock
  `New VM` recipe attaches a CD-ROM with no media unless told otherwise, and
  that step fails. With the simulate on it is refused — *"the simulated
  deploy reported failed steps, so a real deploy would build a broken VM"*.
  With it off, the same configuration passes. Local answer validation still
  runs either way.
- **A snapshot sharing the target's name is not mistaken for a VM.** The skip
  in `find_vm_by_name()` had only ever been verified by reading it.
- **VM names are case-sensitive and case-preserving.** `ZZ-FOO` and `zz-foo`
  are two different VMs and the platform will hold both.
- **`auto_update` reaches the instance row.** A parameter that is accepted
  and dropped is worse than one that is rejected.
- **A re-run with changed answers converges without rewriting a hand edit.**
  The VM's RAM was altered outside Ansible first, so this is a real check
  rather than a restatement of idempotence.
- **The recipe modules work under API-key auth**, not just the username and
  password every other ladder happens to use. `username` and `password` are
  passed as empty strings in that rung on purpose — the argument spec falls
  back to the environment, so omitting them would quietly re-test the
  password path and pass for the wrong reason.

### A warning that was not true

`vm_from_recipe`'s defaults warned that `power_on` would re-enable a VM
someone had deliberately disabled, because *"the vm module's `enabled`
parameter defaults to true"*. Both halves were wrong. `enabled` has no
default in the argument spec — it defaults to true only on the **create**
path — `update_vm` skips any field left unset, and the role never passes it.
Measured:

```
state: running, enabled not passed  -> Error starting machine: Machine is disabled
state: running, enabled: true       -> re-enabled and started
```

So a disabled VM is refused loudly rather than quietly re-enabled, which is
the behaviour worth having. The role's comments now describe that, and
`power_on` stays opt-in for the honest reason: on the convergence path it
starts a VM the role merely *found*.

### Not covered, and why

`resolve_answers()` refuses an ambiguous **network** name. That branch cannot
be reached here — `POST /v4/vnets` answers `{"err": "This name is already in
use"}`, so two vnets cannot share a name in the first place. The unit suite
is its only possible coverage.

## Recipe authoring — what the platform does for you, and what it does not

`verify-recipe-custom.yml` builds a recipe from scratch and deploys from it,
in two halves. The first publishes from a **blank** VM: setting `vm` on a
`vm_recipes` row fires the platform's `set_vm` hook, which snapshots the VM
and auto-publishes a question set derived from its real shape — one
`YB_DRIVE_<n>_*` group per drive — so a custom recipe is deployable with no
question authoring at all. The second publishes from a **real, booted**
Debian and proves the clone boots. That is the golden-image workflow, and it
is the one people actually do.

Three things that ladder had to learn the hard way:

- **You cannot publish a recipe from a running VM.** `POST /v4/vm_recipes`
  with `vm: <key>` answers HTTP 405 `{"err": "VM is currently running"}`.
  Stop it first.
- **The auto-published NIC question is not called what you would guess.** A
  VM with one NIC publishes `YB_NIC_1`, `YB_NIC_1_IP_ADDR` and
  `YB_NIC_1_INTERNAL_GATEWAY`, and only the bare `YB_NIC_<n>` selects the
  network. Its default is `__new_internal__`, which **the platform does not
  apply** — leave it unanswered and the clone boots with its NIC attached to
  nothing, with every other check passing. The role's `verify_nics`
  post-condition is what caught it.
- **A recipe cannot be deleted while instances exist**, and its snapshot
  cannot be deleted while a deployed clone references the snapshot's drive.
  Teardown order is therefore instances → deployed clones → recipes →
  source VMs → catalogs, and `recipe_scratch_teardown.yml` documents each
  step with the refusal that taught it.

Seen once and **not reproduced**: on the first run of the golden half, the
clone drive never finished — `media=clone`, `status=importing`,
`status_info` empty, `used_bytes` frozen at the source's value, and
`machine_drive_status.modified` unchanged for 25 minutes, still incomplete
at 1800s. Every subsequent run completed within five minutes. It is recorded
here rather than filed as a defect because one observation is not a defect,
but the signature is worth recognising: a stalled clone is otherwise
indistinguishable from a slow one, since the drive reports no progress at
all.

## Measured platform behaviours worth knowing

- **The two Windows recipes disagree about `HOSTNAME`.** The 2025 recipe
  declares `max: 15`, which is the real Windows computer-name limit, and the
  module refuses a longer answer before deploying anything. The 2022 recipe
  declares `max: 0` — unbounded — and will happily accept a 31-character
  name that Windows itself cannot use. Same vendor, same catalogue, adjacent
  recipes.

- **`state: absent` will not delete a running VM, and that is correct.** The
  API answers `Virtual Machine must be stopped to delete`. The `vm` module
  surfaces it honestly and deliberately does not stop-then-delete, because an
  `absent` that powered off a running workload in order to remove it would be
  a far worse default than a refusal. Tear down with `state: stopped` first —
  `recipe_real_teardown.yml` is the worked example, and it does **not**
  suppress the failure.
- **`state: stopped` does not create a missing VM, but `state: running`
  does.** The asymmetry is deliberate and now documented on the module;
  creating a machine in order to report it as stopped is rarely what was
  meant.
- **The cluster's real VM ceiling is well under its physical RAM.** Each node
  reports `ram: 94208` MB but `vm_ram: ~68352` MB, with `target_ram_pct: 80`
  and `ram_overcommit_pct: 0`. With the nested `vlab-node-*` lab standing at
  16 GB each plus `nas1` at 8 GB, roughly 56 GB is already committed before
  any sweep starts, and a VM has to fit on a single node.

- **A one-character answer used to mask digits in the module's own error
  messages** (B20, now fixed). `answers` is `no_log`, which is right —
  recipe answers routinely carry passwords — and ansible-core masks a
  `no_log` value by replacing it *anywhere* in the module's output as a
  plain substring, integers included. `YB_CPU_CORES: 1` turned
  *"requires at least 512"* into *"requires at least 5\*\*\*\*\*\*\*\*2"*.
  `narrow_no_log()` now stops masking the answers the recipe's question
  types say are not credentials, once those types are known — the
  invocation log, which is the path that matters for leakage, has already
  happened fully masked by then. Rungs 5–5g pin **both** directions: the
  same marker string is masked as a `password`-typed answer and survives
  as a `string`-typed one. The `matching` strings in the case table stay
  digit-free anyway, so the table does not depend on the fix holding.

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
