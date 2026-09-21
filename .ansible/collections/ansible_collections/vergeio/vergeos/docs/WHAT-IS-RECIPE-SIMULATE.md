# What `simulate: true` is

A short answer to a fair question: this flag keeps coming up in the D2 defect
report, but what actually *is* it?

---

## In one paragraph

A **VM recipe** is VergeOS's template for building a VM — it asks a set of
questions ("hostname?", "how much RAM?", "which network?") and then runs a
sequence of steps to build the machine. `simulate: true` tells VergeOS to
**walk every one of those steps and report what it would do, without doing
it.** It is the API form of the **Simulate Recipe** button in the VergeOS UI.

VergeOS's own documentation describes the UI equivalent as letting you *"view
the user input form, test field validation and create sample answer files to
verify your configuration"*, and confirms simulation does not create a VM.

---

## How you use it

It is not a separate endpoint. It is a flag on the ordinary "create a recipe
instance" call:

```jsonc
POST /api/v4/vm_recipe_instances
{
  "recipe": "4d10e412...",        // the recipe's $key -- a hash, so QUOTE it
  "name": "web-01",
  "simulate": true,               // <-- the only difference from a real deploy
  "answers": {
    "HOSTNAME": "web-01",
    "USER": "ops",
    "PASSWORD": "...",
    "YB_CPU_CORES": 1,
    "YB_RAM": 1024,
    "YB_NIC_ETH0": 3,             // a vnet KEY, not a name
    "SELECT_OS_TIER": 1
  }
}
```

Remove `"simulate": true` and the identical call builds the VM for real.

---

## What it gives back

Measured on VergeOS 26.1.8 — three things worth having:

**1. A step-by-step log of the build (28 entries for a stock Ubuntu recipe).**
Not a summary; the actual database calls with their parameters and responses:

```
Executing recipe API commands
Database create CREATE_OS_DRIVE: machine_drives
Parameters: {"machine":"62","name":"OS","media":"import",
             "importurl":"https://cloud-images.ubuntu.com/.../jammy...img",
             "interface":"virtio-scsi","minsize":"53687091200",
             "preferred_tier":"1"}
Response: {"location":"/v4/machine_drives/12","$row":12,"$key":"12","code":0}
Generated variable 'CREATE_OS_DRIVE:location'
```

**2. The answers as resolved**, including keys the recipe worked out for
itself — `YB_VM_KEY`, `YB_MACHINE_KEY`.

**3. The fully rendered cloud-init**, with real content, not a template:
`/meta-data`, `/network-config`, `/user-data` — hostname substituted, user
created, password hashed, packages listed.

---

## Is it genuinely safe? Yes — measured, not assumed

The log above is unsettling on first read. It says it *created*
`machine_drives/12` and gives a real-looking database response with a row
number. So it was worth checking properly rather than trusting the label.

Row counts before and after two consecutive simulates:

```
BEFORE      : vm_recipe_instances=1 vm_recipe_logs=368 machine_drives=49 machines=63 vms=48
after run 1 : vm_recipe_instances=1 vm_recipe_logs=368 machine_drives=49 machines=63 vms=48
after run 2 : vm_recipe_instances=1 vm_recipe_logs=368 machine_drives=49 machines=63 vms=48
```

**Nothing changed** — not even `vm_recipe_logs`, which a real deploy writes
to. And both runs reported *identical* keys:

```
run 1: YB_VM_KEY=36  YB_MACHINE_KEY=32  machine_drives/12
run 2: YB_VM_KEY=36  YB_MACHINE_KEY=32  machine_drives/12
```

`GET /machine_drives/12` returns *not found*.

### The detail that makes it useful

Those keys are not placeholders. `12` and `36` are precisely the **gaps** in
each table's key sequence — the next free slots:

```
machine_drives keys present: 1..11, 13, 14, ...   missing: [12]
vms            missing: [36]
```

And when a **real** deploy was run later, the VM was created as **key 36** —
exactly what the simulate predicted.

So the simulate runs the operations against the real database inside a
transaction and rolls back, which is why the log is so faithful. The result
is a dry run that tells you the truth rather than an approximation.

---

## Why it matters to the collection

It is **the only genuine preflight a recipe deploy has.** A recipe can fail
halfway — a wrong tier, an unresolvable network, a required answer whose
default is empty — and half a VM is worse than none.

So `vm_recipe_deploy` uses it two ways:

- **`check_mode`** maps onto it. `--check` runs the simulate and reports what
  would be built, which is a real answer rather than a guess.
- **Before every real deploy**, the simulate runs first and its log is
  *scanned for failures* rather than trusted. VergeOS reports
  `"Simulation complete"` even when steps inside the log failed, so the
  module reads the log itself and refuses to deploy if it finds errors.

---

## The oddity, and its consequence

A **successful** simulate answers with **HTTP 405 Method Not Allowed**:

```
HTTP STATUS: 405
err : 'Simulation complete'
response.logs : 28 entries
```

405 normally means "you cannot do that here". Here it means "done, and here
is your report". That is the whole of defect D2: pyvergeos treats any
non-2xx status as a failure and discards the body, so the report is
unreachable through the SDK. See `docs/D2-AFFECTED-CALLS.md`.

Worth raising with the platform team separately — a successful simulation
would be better as a 200.

---

## Sources

- Measured against VergeOS 26.1.8 (`conundrum-lab`), reproducible with
  `bash docs/repro/d2_discarded_body.sh`
- [VM Recipes — VergeOS Docs](https://docs.verge.io/automate-protect-and-extend/automation/vm-recipes)
