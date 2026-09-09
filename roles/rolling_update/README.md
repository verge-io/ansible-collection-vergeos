# rolling_update

Install a VergeOS platform update, then apply it **node by node** with a
health gate between nodes.

The shape follows the platform's own split: check, download and install are
system-wide and happen once; only *applying* an update is per node, and it is
applied by restarting. So this role runs the system-wide part once and then
walks the nodes.

Rolling rather than all-at-once is the entire point. A bad update is found on
the first node while the rest of the cluster still runs the previous version.

## Usage

```bash
# Dry run: stages the update, prints the node order, restarts nothing
ansible-playbook examples/rolling_update.yml

# For real
ansible-playbook examples/rolling_update.yml -e rolling_update_confirm=yes
```

## Per-node cycle

1. **Drain** — enter maintenance, evacuating running workloads.
2. **Wait** for evacuation to actually finish.
3. **Restart**.
4. **Wait for the node to go down, then come back.**
5. **Return to service** — leave maintenance.
6. **Health gate** — the cluster must look right before the next node.

## Things worth knowing

**Consent is a variable, not `--check`.** `rolling_update_confirm` must be the
literal string `yes` before any node restarts. A scheduler will not remember
to pass `--check`, and rebooting every node in a cluster is not something to
start by accident. Run it without consent and you get an honest dry run — the
update staged, and the exact node order printed — as the *default* path rather
than a mode you have to know to ask for.

**The health gate compares against a baseline captured before the run.** Not
against the previous iteration. Comparing to the previous iteration lets the
expected node count drift downwards one node at a time, so each lost node
quietly becomes the new normal and the gate never fires.

**It waits for the node to go DOWN before waiting for it to come back.** A
node does not vanish the instant a restart is requested, so polling straight
away sees it still online and calls the restart finished before it has begun.

**A failure stops the roll.** If a node does not come back, the remaining
nodes are untouched and still running the previous version, and the failed
node is left drained. That is deliberate — fix it before re-running rather
than continuing.

**Ordering matters on an uneven cluster.** Name nodes explicitly in
`rolling_update_nodes` to prove the update on the node you choose, rather than
the one the API happened to list first.

**Replication can gate the roll.** Set
`rolling_update_max_replication_age` and the run stops if site syncs fall
behind while nodes cycle.

**Turning off `rolling_update_return_to_service` will stop the run after one
node** — the next node has nowhere to evacuate to. That is useful when you
want to inspect a node before it takes workloads again, but it is a one-node
operation by construction.
