# D1–D6 reproductions

The playbooks that produced the transcripts quoted in
[`../../DEFECTS-D1-D6.md`](../../DEFECTS-D1-D6.md). They are kept exactly as
run, not tidied — see the note in `.ansible-lint`.

```bash
source ~/.config/vergeos/verify.env
export ANSIBLE_COLLECTIONS_PATH=<where the collection is installed>
cd docs/repro/d1-d6
ansible-playbook d1_all_modules.yml
```

| File | Shows |
|---|---|
| `d1_all_modules.yml` | D1 across `vm`, `user`, `network`, `nic`, `drive` |
| `d1_nic_drive.yml` | D1 for `nic`/`drive`, read-back scoped to one machine |
| `d1_convergence.yml` | D1 never converges, and the return value misreports |
| `d2_drive_tier.yml` | D2 `drive.tier` never applies |
| `d3_cloudinit.yml` | D3 both halves (disable path, mutual exclusion) |
| `d4_enabled.yml` | D4 (run once stock, once with D1's fix applied) |
| `d4_fix_check.yml` | D4's fix does not regress the create/enable paths |
| `d5_token.yml` | D5 token auth unavailable |

## Helpers

- **`readback.py`** — raw HTTP with the standard library only. No pyvergeos,
  no collection code, so a read-back cannot inherit the bug it is checking
  for. Usage: `python3 readback.py "vms?fields=name,enabled"`.
- **`wire.py`** — hooks `requests.Session.request` and records method, URL,
  request body, status and response. This is what produced the
  `PUT vms/36 {}` capture.

Every playbook creates only `zz-*` objects and deletes them at the end.

D6 needs no playbook:

```bash
ansible-lint --offline meta/runtime.yml
```

## Fact-check playbooks

Added after a review pass found several claims in the first draft of the
report were reasoned from source rather than measured. Each of these turned
one of those into a measurement, and three of them changed the report.

| File | Settles |
|---|---|
| `factcheck_d1.yml` | convergence + return-value fidelity for **all five** modules, not just `vm` |
| `factcheck_power.yml` | does the create path persist? do power actions fire? |
| `factcheck_power2.yml` | times the power paths (found the broken `refresh()` wait loop) |
| `factcheck_d2_create.yml` | `tier` on **create** vs on **update** — they differ |
| `factcheck_d3_files.yml` | do the cloud-init files really survive a failed `state: absent`? |

## Rev 3

`d5_inventory_token.yml` mints a key and writes an inventory config that uses
it as the only credential; `d5_workaround.yml` checks whether the token can be
smuggled through the `password` field (it cannot). Between them they showed
that D5 as first written was wrong — the inventory plugin has supported token
auth all along.

Note the filename rule: the inventory plugin's `verify_file()` only accepts
sources ending `.vergeos_vms.yml` or `.vergeos_vms.yaml`. Any other name is
rejected before the plugin ever reads the config, which looks like an auth
failure and is not.

## Rev 4

`pw_smoke.yml` answers the question that reclassified D5: is anything
unreachable without a token? No. Fourteen read modules in one pass, zero auth
failures, on username/password. Combined with the eleven ladders (all write
paths, all password auth) that is 34 of the 48 modules exercised live on
password auth — and since all 48 share one `vergeos_argument_spec()` and one
`get_vergeos_client()`, there is no mechanism by which the other 14 could
differ.
