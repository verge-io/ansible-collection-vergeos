# dr_replication

Site-to-site replication as code, plus an RPO watchdog that proves replication
is actually keeping up.

Two jobs that belong together. Declaring a sync is easy and tells you nothing:
the failure this role is built around is a replication target that was
configured once, still looks correct in the UI, and has not moved data in
weeks.

## What it does

1. Reconciles the outgoing syncs you declare (create, converge drift, enable,
   disable). Skipped entirely when nothing is declared.
2. Optionally removes syncs you did **not** declare (`dr_replication_exact`).
3. Optionally triggers named syncs and waits for them.
4. Reports lag and health, and can fail the play on either.

## Usage

```yaml
- hosts: localhost
  gather_facts: false
  roles:
    - role: vergeio.vergeos.dr_replication
      vars:
        dr_replication_syncs:
          - name: "to-dr-site"
            site: 1
            registration_code: "{{ vault_dr_registration_code }}"
            url: "https://dr.example.com"
            destination_tier: "4"
        dr_replication_max_age: 14400
        dr_replication_fail_on_lag: true
```

As a **scheduled watchdog**, leave `dr_replication_syncs` empty and turn the
fail gates on. Reconciliation is skipped, so a monitoring run cannot converge
anything by accident.

## Things worth knowing

**A sync that has never run is always counted as behind RPO.** That is the
point — "no timestamp" and "recently replicated" must not look alike, and a
target configured months ago and never exercised is precisely the failure this
surfaces. The report leaves it as `null` rather than `0` for the same reason.

**Health is conservative.** A sync counts as healthy only when the platform
positively reports it online and error-free. An unreadable state is unhealthy,
never assumed green.

**`dr_replication_exact` deletes replication relationships.** Adopt an existing
estate with it `false`, read the report, then flip it. The snapshots already on
the remote system survive a deletion; the pipe keeping them current does not.

**The registration code is create-time only.** It comes from the matching
incoming sync on the *remote* system, is never reconciled, and is never
returned — reconciling it would rewrite an established trust relationship on
every run.

**Incoming syncs are reported but never managed.** An incoming sync belongs to
the system that receives; this role only ever holds credentials for one side.
