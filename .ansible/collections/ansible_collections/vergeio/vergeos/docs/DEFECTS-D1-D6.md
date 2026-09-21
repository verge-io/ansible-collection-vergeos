# D1–D6 — defect reports

All six measured on **conundrum-lab, VergeOS 26.1.8, 2 nodes**, collection
branch `platform-port` (tree pristine — no fixes applied), pyvergeos
**1.2.5**, ansible-core **2.20.9**, Python **3.14.4**, on **2026-09-12**.

Every finding below was produced twice: once while characterising it and
once again as a clean confirmation run against an unmodified tree. Where a
fix is described, it was applied temporarily, verified, and reverted —
`git status` was confirmed clean afterwards, and the lab was left with zero
`zz-*` objects in any table.

> **Revision 2 (2026-09-12).** A fact-check pass against the lab found that
> several statements in revision 1 had been reasoned from source rather than
> measured. Four were wrong and are corrected here: D1's wire signature is not
> uniform across the five modules (`user` issues no PUT at all); D2 is broken
> only on the **update** path — `tier` works on create; D6 is the single
> standing lint failure **on this branch**, not on `main`, which has seven;
> and the closing claim that no live ladder exercises the five affected
> modules was false. Every correction is marked **[corrected in rev 2]**.
>
> **Revision 3 (2026-09-12).** D5 was challenged and re-checked, and it was
> the worst of the lot: rev 1 and rev 2 both claimed the collection "cannot
> use an API token". It can — the **inventory plugin already does**, and it
> works against the lab with no username or password present. Only the
> modules lack it. D5 is now an unfinished feature rather than a missing
> one. Marked **[corrected in rev 3]**.
>
> **Revision 4 (2026-09-12).** D5 challenged again, on whether it is a defect
> at all. It is not. Password auth reaches everything: all 48 modules share
> one auth path, 34 of them are exercised live under username/password with
> zero auth failures, and the inventory plugin returns identical results on
> either credential. **D5 is reclassified as an enhancement** and moved out of
> the defect table. D1-D4 and D6 are unaffected. Marked
> **[reclassified in rev 4]**.

## Status after pyvergeos 1.2.7 (2026-09-21)

| | State | Fixed by |
|---|---|---|
| D1 | **CLOSED** | pyvergeos 1.2.7 (`save()` sends dirty fields). No collection change needed. All five modules converge and persist. |
| D2 | **CLOSED** | collection `e8356cf` — 1.2.7 added the `tier`→`preferred_tier` translation, but only on the typed `update()`; the diff now goes as `save(**kwargs)` to reach it |
| D3 | **CLOSED** | collection `e8356cf` — 1.2.7 normalises the empty datasource on the model path; `cloud_init` used raw `_request` and now goes through the model, sending `'none'` outright |
| D4 | **CLOSED** | collection `e8356cf` — **the 1.2.7 upgrade activated this one.** On 1.2.5 the re-enable was swallowed; on 1.2.7 it lands. Dropping the default is what stops an SDK bump from powering VMs back on. |
| D5 | open (enhancement) | — token auth for the modules; nothing is broken without it |
| D6 | open (owner call) | — `requires_ansible` floor |
| `refresh()` | open | 1.2.7 did not change it; `state: running`/`stopped` still burn 60s each |

`requirements.txt` now pins **`pyvergeos>=1.2.7`**. That floor is load-bearing
for D1 and for the `save(**kwargs)` shape D2 depends on, not merely
aspirational.

The detail below is kept as written, because how these were found and what
they looked like in the wild is the part worth keeping.

Reproductions live in [`docs/repro/d1-d6/`](repro/d1-d6/). Run them with:

```bash
source ~/.config/vergeos/verify.env
export ANSIBLE_COLLECTIONS_PATH=<where the collection is installed>
cd docs/repro/d1-d6
ansible-playbook d1_all_modules.yml
```

`readback.py` deliberately uses raw HTTP and the Python standard library —
no pyvergeos, no collection code — so a read-back cannot inherit the bug it
is checking for. `wire.py` hooks `requests.Session.request` to record what
actually left the machine.

---

## D1 — `vm`, `nic`, `drive`, `network` and `user` never persist an update

### Summary

All five modules end their update path with a bare `obj.save()`. pyvergeos's
`ResourceObject.save(**kwargs)` forwards **only its keyword arguments** to
`manager.update(key, **kwargs)`, which PUTs them as the request body — so a
bare `save()` issues `PUT <resource>/<key>` with an empty JSON object. The
attributes the modules set with `setattr` beforehand are never transmitted.
**[corrected in rev 2]** The wire signature is not uniform: `vm`, `network`,
`nic` and `drive` each emit an empty `PUT`, but `user` emits **no PUT at
all** — `UserManager.update()` overrides the generic method with keyword-only
parameters and ends `if not body: return self.get(key)`, so an empty body
short-circuits into a bare `GET`. The operator-visible outcome is identical
for all five; only the packet capture differs.
VergeOS accepts the empty PUT with **HTTP 200**, so nothing raises, and the
module then returns `dict(obj)` — the *locally mutated* object — so the task
result reports the values the operator asked for while the server still
holds the old ones. The net effect is that every `state: present` update
through these five modules is a silent no-op that reports `changed: true`,
never converges (it reports `changed` on every subsequent run forever), and
returns a result that actively contradicts the server. Creation is
unaffected — measured, not assumed: a VM created with
`description: CREATED-VALUES, cpu_cores: 2, ram: 1024` reads back with
exactly those values. Power actions do fire (`state: running` genuinely
starts the VM; `state: stopped` uses `power_off(force=True)`), which is why
the collection appears to work in demos. This is pre-existing on `main` —
all five bare `save()` calls are present there at the same line numbers —
and is the highest-severity item of the six.

**[corrected in rev 2] An adjacent defect found while checking the power
claim.** The power paths change state correctly but their wait loops never
observe it: `ResourceObject.refresh()` **returns** a refreshed object and
does not mutate `self`, so `vm.refresh()` followed by `dict(vm)` re-reads
the stale copy. Both loops therefore run all 30 iterations every time.
Measured: `state=running took 61s`, `state=stopped took 61s`, against a
30 × 2s loop. Verified separately that `refresh()` does not mutate — after
an out-of-band change, `dict(obj)` still read `ORIG` while the object
`refresh()` returned read `CHANGED-OUT-OF-BAND`. This is the same family of
mistake as D1 (a pyvergeos return-value convention the collection ignores)
but it is **not** part of D1-D6 and is recorded here only because it
qualifies the scope sentence above.

### Steps to reproduce

1. Create a VM with known values:
   ```yaml
   - vergeio.vergeos.vm:
       name: zz-d1-vm
       cpu_cores: 1
       ram: 512
       description: BEFORE
       state: present
   ```
2. Update it through the same module:
   ```yaml
   - vergeio.vergeos.vm:
       name: zz-d1-vm
       cpu_cores: 4
       ram: 2048
       description: AFTER
       state: present
     register: r
   ```
3. Read the VM back **without** the SDK or the collection:
   ```bash
   python3 readback.py "vms?fields=name,description,cpu_cores,ram&filter=name eq 'zz-d1-vm'"
   ```
4. Repeat step 2 twice more and watch `changed` never go false.

Full playbook: `d1_all_modules.yml` (all five modules),
`d1_nic_drive.yml` (nic/drive scoped to one machine),
`d1_convergence.yml` (convergence + return-value fidelity).

### Signatures

**The wire — this is the definitive signature.** An update that should carry
three fields carries none:

```
PUT    vms/36                       -> HTTP 200
       request body : {}
       response     : []
```

Compare the same call with the fix applied:

```
PUT    vms/36                       -> HTTP 200
       request body : {"description": "AFTER", "cpu_cores": 4, "ram": 2048}
       response     : []
```

Note that **HTTP 200 is returned either way** — the status code is not a
signature; the empty request body is.

**[corrected in rev 2] Per-module wire signature**, captured by driving each
module's own SDK manager exactly as the module does:

```
vm       PUT vms/36              HTTP 200  body={}
network  PUT vnets/14            HTTP 200  body={}
nic      PUT machine_nics/38     HTTP 200  body={}
drive    PUT machine_drives/12   HTTP 200  body={}
user     NO PUT ISSUED. requests made: ['GET']
```

`user` is the odd one out for the reason given in the summary. If you are
hunting this in a packet capture, look for the empty PUT on four of the five
and for an update that produced only a GET on `user`.

**Operator-visible, all five modules** (module said `changed`, server
disagrees):

```
vm      module changed=True   read-back: [{"name": "zz-d1-vm", "description": "BEFORE", "cpu_cores": 1, "ram": 512}]
user    module changed=True   read-back: [{"name": "zz-d1-user", "displayname": "BEFORE", "email": "before@example.com"}]
network module changed=True   read-back: [{"name": "zz-d1-net", "description": "BEFORE"}]
nic     module changed=True   read-back: [{"name": "nic_0", "interface": "virtio", ...}]   (expected e1000)
drive   module changed=True   read-back: [{"name": "zz-d1b-drive", "interface": "virtio-scsi"}] (expected ahci)
```

**Never converges, and the result misreports.** Three identical runs of
*each* of the five (rev 1 measured only `vm`):

```
vm      changed per run: [True, True, True]
user    changed per run: [True, True, True]
network changed per run: [True, True, True]
nic     changed per run: [True, True, True]
drive   changed per run: [True, True, True]
```

and what each returned on run 3, against a server that never moved:

```
vm.description      returned: AFTER
user.displayname    returned: AFTER
network.description returned: AFTER
nic.interface       returned: e1000
drive.interface     returned: ahci
```

This is why it went unnoticed: `r.vm.description` in a registered result
says `AFTER`. Any assertion written against the module's own return value
passes.

**In code** — `plugins/modules/vm.py:238-242`, and the identical shape in
`nic.py:238`, `drive.py:223`, `network.py:242`, `user.py:203`:

```python
for key, value in update_data.items():
    setattr(vm, key, value)
vm.save()                      # <- update_data never leaves the process
```

**Grep signature** for auditing: `grep -rn '\.save()' plugins/` returns
exactly the five affected call sites; `grep -rn '\.save(.\+)' plugins/`
returns nothing.

### Verification of cause

Changing the single line to `vm.save(**update_data)` and re-running
`d1_convergence.yml` produced:

```
run1 changed=True  run2 changed=False  run3 changed=False
server ACTUALLY has: [{"name": "zz-d1c-vm", "description": "AFTER", "cpu_cores": 4, "ram": 2048}]
```

Persisted and converged. Reverted afterwards.

---

## D2 — `drive`'s `tier` never applies on update (create is fine)

### Summary

The `drive` module's tier handling reads `drive_dict.get('tier')`, but a live
`machine_drives` row has no `tier` key at all — the field is
`preferred_tier`, and it holds a **string**. The comparison is therefore
always `None != <int>`, so the module reports `changed: true` on every run
forever, and the value it writes is `update_data['tier']`, a field name the
API does not recognise. This is genuinely independent of D1: I confirmed at
the wire that VergeOS accepts `PUT {"tier": 1}` with **HTTP 200 and silently
ignores it**, while `PUT {"preferred_tier": 1}` and
`PUT {"preferred_tier": "1"}` both apply correctly. So fixing D1 alone would
change nothing here — the payload would finally be transmitted, and the
platform would still discard it without complaint. **[corrected in rev 2]**
Rev 1 said tier placement "cannot be expressed through this collection at
all". That is wrong. `tier` works correctly on **create**, because the create
path goes through `DriveManager.create()`, which accepts `tier` and
translates it — captured on the wire as
`POST machine_drives {... 'preferred_tier': '1'}`. Only the **update** path
is broken, because it writes the raw field name into `update_data` itself and
never reaches that translation. So a drive can be *placed* on a tier but not
*re-tiered*, which is precisely the half the `tier_policy` role's enforce
mode needs (it retiers existing drives); that role lives in
`vergeos_platform` and is not yet ported here.

### Steps to reproduce

1. Create a VM and add a drive without specifying a tier — it lands on
   `preferred_tier` `"4"`.
2. Ask for tier 1 three times:
   ```yaml
   - vergeio.vergeos.drive:
       vm_name: zz-d2-vm
       name: zz-d2-drive
       tier: 1
       state: present
   ```
3. Read back `machine_drives?fields=name,preferred_tier`.

Full playbook: `d2_drive_tier.yml`.

### Signatures

```
as created          : [{"name": "zz-d2-drive", "preferred_tier": "4"}]
asked for tier 1 x3 : changed=True,True,True
after three runs    : [{"name": "zz-d2-drive", "preferred_tier": "4"}]
```

**The field really is absent** — measured keys of a live drive row:

```
['$key', 'advanced', 'asset', 'description', 'discard', 'enabled', 'fsync',
 'interface', 'machine', 'media', 'media_source', 'ms_2023_kek_applied',
 'name', 'optimize', 'orderid', 'origin_uuid', 'physical_status',
 'preferred_tier', 'preserve_drive_format', 'readonly', 'serial', 'stats',
 'status']

tier            -> absent
preferred_tier  -> '4'      (a string)
```

**The platform silently ignores the wrong field name** — this is the part
that makes D2 independent of D1:

```
PUT {'tier': 1}                -> accepted, preferred_tier now '4'    <- ignored
PUT {'preferred_tier': 1}      -> accepted, preferred_tier now '1'
PUT {'preferred_tier': '1'}    -> accepted, preferred_tier now '1'
```

All three returned HTTP 200.

**[corrected in rev 2] Create works, update does not** — same module, same
parameter, measured back to back:

```
created with tier:1  -> [{"name": "zz-fc-d2c-drive", "preferred_tier": "1"}]
update to tier:4     -> changed=True, [{"name": "zz-fc-d2c-drive", "preferred_tier": "1"}]
```

The SDK's create path does the translation the module's update path omits:

```
POST machine_drives  body={'machine': 41, 'interface': 'virtio-scsi',
                           'media': 'disk', 'enabled': True, 'name': 'd0',
                           'disksize': 1073741824, 'preferred_tier': '1'}
```

**In code** — `plugins/modules/drive.py:202-204`:

```python
if module.params.get('tier') is not None:
    if drive_dict.get('tier') != module.params['tier']:   # None != 1, always
        update_data['tier'] = module.params['tier']       # field does not exist
```

### Verification of cause

With `preferred_tier` compared as a string and `save(**update_data)`:

```
asked for tier 1 x3 : changed=True,False,False
after three runs    : [{"name": "zz-d2-drive", "preferred_tier": "1"}]
```

Reverted afterwards.

---

## D3 — `cloud_init` cannot disable cloud-init, and blocks a valid combination

### Summary

Two independent problems in one module. First, `state: absent` disables
cloud-init by sending `cloudinit_datasource=''`, which VergeOS rejects
outright — the accepted disable value is `'none'`. Because the datasource
write happens *before* the file deletion in `remove_cloudinit()`, the task
fails at the first step, leaving the datasource on `nocloud` and the
cloud-init files still in place; `state: absent` is completely
non-functional. Unlike D1 and D2 this one fails **loudly**, which is the
better failure mode, but it means there is no supported way to turn
cloud-init off through the collection. Second, the argument spec declares
`hostname` mutually exclusive with `user_data` and `meta_data`, even though
the generation logic already treats `hostname` purely as a gap-filler — it
generates content only for whichever document the operator did not supply.
The constraint therefore blocks the ordinary VM-import case of a custom
`#cloud-config` plus an auto-generated meta-data, and blocks nothing that
would actually misbehave.

### Steps to reproduce

Part A — the disable path:

1. Enable cloud-init on a scratch VM with `hostname: zz-d3-host`.
2. Confirm `cloudinit_datasource` reads `nocloud`.
3. Run the module with `state: absent`.
4. Re-read `cloudinit_datasource`.

Part B — the constraint:

5. Call the module with `hostname` **and** an explicit `user_data` block.

Full playbook: `d3_cloudinit.yml`.

### Signatures

```
A. enable            -> changed=True, datasource=[{"name": "zz-d3-vm", "cloudinit_datasource": "nocloud"}]
A. state=absent      -> failed=True
   message           : Validation error: value '' is not in list for field 'cloudinit_datasource'
   datasource after  : [{"name": "zz-d3-vm", "cloudinit_datasource": "nocloud"}]
B. hostname+user_data-> failed=True
   message           : parameters are mutually exclusive: hostname|user_data
```

**[verified in rev 2] The files really do survive the failure**, so the VM is
left in a partial state rather than untouched:

```
absent failed?       True
files before disable: [{'name': '/user-data', 'owner': 'vms/36'},
                       {'name': '/meta-data', 'owner': 'vms/36'}]
files after  disable: [{'name': '/user-data', 'owner': 'vms/36'},
                       {'name': '/meta-data', 'owner': 'vms/36'}]
VERDICT: files REMAIN (partial state)
```

Note for anyone reproducing: `cloudinit_files` rows key their VM as
`owner: "vms/<key>"`, not `vm`. Filtering on `vm` returns `[]` and looks like
a clean teardown — that mistake cost me a run.

**The accepted value set, measured directly:**

```
PUT cloudinit_datasource='nocloud' -> OK,   reads back 'nocloud'
PUT cloudinit_datasource=''        -> ValidationError: value '' is not in list
                                      for field 'cloudinit_datasource'
PUT cloudinit_datasource='none'    -> OK,   reads back 'none'
```

**In code** — `plugins/modules/cloud_init.py:381` sends the rejected value,
and line 398 does not even offer `none` as a choice:

```python
enable_cloudinit_datasource(client, module, vm_key, '')     # line 381
datasource=dict(type='str', choices=['nocloud', '']),       # line 398
```

and the constraint at the argument spec:

```python
mutually_exclusive=[
    ('vm_name', 'vm_id'),
    ('hostname', 'user_data'),
    ('hostname', 'meta_data'),
    ('network', 'network_config'),
],
```

### Verification of cause

Sending `'none'` instead of `''` and dropping the two `hostname` pairs:

```
A. state=absent      -> failed=False
   message           : Cloud-init disabled and files removed
   datasource after  : [{"name": "zz-d3-vm", "cloudinit_datasource": "none"}]
B. hostname+user_data-> failed=False
```

The relaxed constraint produces correct documents, not merely an absence of
error — the operator's user-data is kept byte-for-byte and `hostname` fills
only the missing meta-data:

```
user-data (operator's, must be verbatim): '#cloud-config\npackages: [htop]\n'
meta-data (generated from hostname)     : 'instance-id: zz-d3d-host-001\nlocal-hostname: zz-d3d-host'

operator's user_data preserved verbatim?  True
meta-data carries the hostname?           True
```

Reverted afterwards.

---

## D4 — `vm`'s `enabled` default will re-enable disabled VMs once D1 is fixed

### Summary

`enabled` is declared `default=True`, so `module.params['enabled']` is `True`
on every run unless the caller explicitly says otherwise, and `update_vm()`
compares it against the live row like any other field. Any partial update to
an existing VM — a reconciler setting only `description`, or only
`snapshot_profile` — therefore also asserts `enabled=True` against a VM the
operator deliberately disabled. **On the collection as it stands today this
is not observable**, and I want to be precise about that: I ran it against a
disabled VM and the VM stayed disabled, because D1 swallows the write. The
defect is real but latent. Fixing D1 alone activates it, and I confirmed
that by applying only the D1 one-liner and re-running the same test: the VM
flipped from `enabled: false` to `enabled: true`. That makes D4 a
**prerequisite of D1 rather than an independent improvement** — shipping D1
without it converts a silent no-op into a silent power-state change, which
is strictly worse.

### Steps to reproduce

1. Create a VM with `enabled: false`.
2. Confirm `enabled` reads `false`.
3. Run the module again changing **only** `description`, never mentioning
   `enabled`.
4. Re-read `enabled`.
5. Repeat the whole sequence with `vm.save(**update_data)` applied (D1's fix
   only, D4 untouched).

Full playbook: `d4_enabled.yml`; the no-regression check is
`d4_fix_check.yml`.

### Signatures

Stock collection — D1 masks it:

```
before : [{"name": "zz-d4-vm", "enabled": false}]
after  : [{"name": "zz-d4-vm", "enabled": false, "description": ""}]
VERDICT: disabled state preserved
```

Note `description` is also still `""` — the same run proves D1.

With **only** the D1 fix applied:

```
before : [{"name": "zz-d4-vm", "enabled": false}]
after  : [{"name": "zz-d4-vm", "enabled": true, "description": "touched by a reconciler that only cares about description"}]
VERDICT: RE-ENABLED (data-affecting)
```

**In code** — `plugins/modules/vm.py:306` and the generic comparison loop at
`226-231`:

```python
enabled=dict(type='bool', default=True),   # params['enabled'] is never None
...
for field in fields_to_check:                       # 'enabled' is in this list
    if module.params.get(field) is not None:        # always true for enabled
        if vm_dict.get(field) != module.params[field]:
            update_data[field] = module.params[field]
```

### Verification of cause

Dropping the default (`enabled=dict(type='bool')`) with D1 fixed:

```
before : [{"name": "zz-d4-vm", "enabled": false}]
after  : [{"name": "zz-d4-vm", "enabled": false, "description": "touched by ..."}]
VERDICT: disabled state preserved
```

and D1 still converges on the same tree:

```
run1 changed=True  run2 changed=False  run3 changed=False
server ACTUALLY has: [{"name": "zz-d1c-vm", "description": "AFTER", "cpu_cores": 4, "ram": 2048}]
```

No regression to the three create/enable paths:

```
expected: zz-d4a=true (platform default), zz-d4b=false, zz-d4c=true (explicit)
actual  : [{'name': 'zz-d4a', 'enabled': True},
           {'name': 'zz-d4b', 'enabled': False},
           {'name': 'zz-d4c', 'enabled': True}]
explicit enable reported changed=True
```

Reverted afterwards.

---

## D5 — ENHANCEMENT: finish token auth (inventory has it, modules do not)

### Summary

**[corrected in rev 3]** Rev 1 and rev 2 both said "the collection cannot use
an API token", and framed it as a defect. Both parts were wrong. Token auth is
already implemented in **half** the collection, and its absence from the other
half costs no functionality. The **inventory plugin
supports it** through a per-site `api_key` option that it maps to the SDK's
`token` parameter (`plugins/inventory/vergeos_vms.py:255-257`), and it works
— verified live, enumerating 6 hosts and 4 groups from the lab with a key
minted by this collection's own `api_key` module and with
`VERGEOS_USERNAME`/`VERGEOS_PASSWORD` explicitly unset in the environment.
What is missing is the other half: `vergeos_argument_spec()` offers only
`host`, `username`, `password` and `insecure`, so **none of the 48 modules**
accepts a token, and there is no workaround — passing the secret as
`password` fails with `Login required`. So the collection can *discover* an
estate with a token but cannot *act* on it with one, and the `api_key`
module's own documented example persists a freshly minted secret to
`/root/.vergeos_token`, a file nothing in the collection can subsequently
consume for a write. This is an inconsistency to finish, not a feature to
invent — which also makes it materially cheaper than rev 1 implied.

**[reclassified in rev 4] D5 is an enhancement, not a defect.** Challenged on
whether anything is actually broken without a token, and the answer is no.
Measured: all **48** modules resolve their credentials through the same
`vergeos_argument_spec()` and `get_vergeos_client()` — there is no per-module
auth code and therefore no module that could behave differently — and **34 of
the 48 have been exercised live on this lab under username/password auth with
zero auth failures**, spanning every write path in the eleven `tests/live`
ladders plus fourteen read modules in one pass. The inventory plugin takes
either credential and returns byte-identical results: 6 hosts and the same
four groups with a token, and with a username and password. **Nothing in the
collection is unreachable without a token.** D5 therefore does not belong in
a defect list next to D1-D4 — it is a credential-hygiene improvement
(revocable, rotatable, `ip_allow_list`-scoped credentials instead of a real
user's password in a playbook), and it should be prioritised as such.

### Steps to reproduce

1. Mint a key with the collection's own module:
   ```yaml
   - vergeio.vergeos.api_key:
       user: "{{ lookup('env','VERGEOS_USERNAME') }}"
       name: zz-d5-key
       state: present
     register: key
   ```
2. Prove the key works with a raw Bearer request to `/api/v4/vms`.
3. Try to hand the same key to any stock module as `token:`.
4. Try to run a module with empty `username`/`password`.
5. **[added in rev 3]** Write an inventory config whose only credential is
   that key, and run it with the username/password environment variables
   removed:
   ```yaml
   # zz_tok.vergeos_vms.yml  (the .vergeos_vms.yml suffix is required)
   plugin: vergeio.vergeos.vergeos_vms
   sites:
     - name: lab
       host: "<host>"
       api_key: "<the minted secret>"
       insecure: true
   ```
   ```bash
   env -u VERGEOS_USERNAME -u VERGEOS_PASSWORD \
     ansible-inventory -i zz_tok.vergeos_vms.yml --list
   ```
6. **[added in rev 3]** Try the obvious workaround — the token as `password`.

Full playbooks: `d5_token.yml`, `d5_inventory_token.yml`, `d5_workaround.yml`.

### Signatures

```
raw Bearer request  : HTTP 200
module with token:  : failed=True -> Unsupported parameters for (vergeio.vergeos.vm_info)
                      module: token. Supported parameters include: host, insecure,
                      name, password, username.
module with empty pw: failed=True -> Task failed: Module failed:
                      Either token or username/password required
```

**[added in rev 3] The inventory plugin, same key, no username or password
anywhere** — this is the half that already works:

```
$ env -u VERGEOS_USERNAME -u VERGEOS_PASSWORD \
    ansible-inventory -i zz_tok.vergeos_vms.yml --list
  hosts : 6
  groups: ['all', 'site_lab', 'status_running', 'status_stopped']
```

```python
# plugins/inventory/vergeos_vms.py:255-257
# SDK uses 'token' parameter, inventory config uses 'api_key'
if site_config.get('api_key'):
    conn_kwargs['token'] = site_config['api_key']
else:
    conn_kwargs['username'] = site_config.get('username')
    conn_kwargs['password'] = site_config.get('password')
```

**[added in rev 3] There is no module-side workaround.** The token is not
accepted in the password field:

```
token-as-password (real user): failed=True  Login required
token-as-both                : failed=True  Login required
```

**A naming decision comes with this.** The same secret is already called
three things: `token` in pyvergeos, `api_key` in the inventory plugin's
config, and `VERGEOS_TOKEN` in the ladder helpers' environment. Whichever
name the module parameter takes, the inventory plugin's existing `api_key`
option is public surface and cannot simply be renamed.

The last line is the sharpest signature: that wording comes from
**pyvergeos**, not the collection —

```
.venv/lib/python3.14/site-packages/pyvergeos/client.py:410:
    raise ValueError("Either token or username/password required")
```

— so the SDK is telling the operator about an authentication mode the
collection gives them no way to select.

```
VergeClient params: ['self', 'host', 'username', 'password', 'token',
                     'verify_ssl', 'timeout', 'auto_connect', ...]

grep -c token plugins/module_utils/vergeos.py  ->  0
```

---

## D6 — `requires_ansible: ">=2.14.0"` claims support that cannot be exercised

### Summary

`meta/runtime.yml` declares a floor of ansible-core 2.14, which is
end-of-life and is the single standing ansible-lint failure **on this
branch**. **[corrected in rev 2]** Rev 1 said "on both `main` and
`platform-port`", which is wrong: `main` has **seven** failures — this one
plus `role-name`, `no-handler` in `examples/snapshot_by_tag.yml`, and four
`yaml[key-duplicates]` in `plugins/inventory/vergeos_vms.py`. The other six
were fixed on the way to `integration-all`; D6 is what is left. Beyond the lint rule, the declared floor is
not merely stale but untestable in practice: ansible-core 2.14 accepts only
Python 3.9 through 3.11, so it cannot be installed on a current controller
(this one runs Python 3.14.4) and therefore no CI on a modern image can ever
validate the claim. Everything in this repository was developed and verified
on 2.20.9. This is a supported-version statement rather than a code change,
so it is the owner's call, but the present value promises compatibility that
nobody is testing and that a public consumer could reasonably rely on.

### Steps to reproduce

```bash
ansible-lint --offline meta/runtime.yml
```

and, for the Python-compatibility half:

```bash
python -c "import json,urllib.request; d=json.load(urllib.request.urlopen(
  'https://pypi.org/pypi/ansible-core/2.14.18/json'));
  print(d['info']['requires_python'])"
```

### Signatures

```
# Rule Violation Summary
  1 meta-runtime profile:shared tags:metadata

meta-runtime[unsupported-version]: 'requires_ansible' key must refer to a
currently supported version such as: >=2.15.0, >=2.16.0, >=2.17.0,
>=2.18.0, >=2.19.0
meta/runtime.yml:1
```

```
ansible-core 2.14.18 requires_python: >=3.9
classifiers: Python 3.9, 3.10, 3.11          <- no 3.12+
this controller:                    python 3.14.4
```

The declared floor and the only environment available to test it do not
intersect.

---

## Cross-cutting observation

D1 and D2 share a root cause that is worth stating on its own, because it
will produce more defects of this shape: **VergeOS accepts a PUT containing
unknown fields, or no fields at all, and answers HTTP 200 either way.**

```
PUT vms/36            {}               -> HTTP 200, nothing changes
PUT machine_drives/10 {"tier": 1}      -> HTTP 200, nothing changes
```

Nothing in the transport, the SDK, or the module surfaces a problem. The
only reliable detector is a **read-back after write**, compared against what
was asked for — which is the doctrine the live ladders in `tests/live/`
already follow, and the reason they caught none of these: every one of them
tests a module added in the port, not the five pre-existing ones.

## Priority

**Defects** — something is broken or wrong:

| | Defect | Severity | Independent? | Notes |
|---|---|---|---|---|
| D1 | five modules never persist updates | **critical** | needs D4 shipped with it | silent; reports success |
| D2 | `drive.tier` wrong field on update | high | yes — survives a D1 fix | create works; retier does not |
| D4 | `enabled` default | high | **prerequisite of D1** | latent until D1 lands |
| D3 | `cloud_init` absent/constraint | medium | yes | fails loudly; leaves files |
| D6 | EOL `requires_ansible` | low | yes | policy statement, not code |

**Enhancement** — nothing is broken; this adds capability:

| | Enhancement | Value | Cost |
|---|---|---|---|
| D5 | token auth for the modules | credential hygiene: revocable, rotatable, `ip_allow_list`-scoped, no user password in a playbook | low — SDK plumbing and a working reference implementation already in-tree |

**[reclassified in rev 4]** D5 sat in the defect table for three revisions on
the strength of a claim nobody had tested: that the gap cost functionality. It
does not. Everything the collection can do, it can do on a username and
password — so D5 competes with the feature backlog, not with D1.

D1, D2 and D4 should land together. Shipping D1 alone would activate D4;
shipping D1 without D2 would leave `drive.tier` looking fixed while the
platform silently discards the payload.

Not in scope but found during the rev 2 fact-check, and worth a decision of
its own: `ResourceObject.refresh()` returns rather than mutates, so both
power-state wait loops in `vm.py` run their full 60 seconds on every call
(measured 61s for `state: running` and 61s for `state: stopped`). Same family
as D1.
