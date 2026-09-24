# network_policy

A YAML policy document in, enforced per-network firewall rules out.

## Honest scope

This is **per-network** firewalling, because that is what VergeOS has
(`vnet_rules`). It is not per-VM distributed firewalling — there is no NSX-DFW
equivalent here to drive. If you need microsegmentation between two VMs on the
same network, this role cannot give it to you, and no amount of policy YAML
will change that.

## Zero-change when converged

Every rule is staged with `apply: false`, then `vnet_apply` refreshes the
network **once, and only if something is actually pending**. That matters more
than it sounds: applying on every run means every play reloads the firewall,
which is both noisy and briefly disruptive. A converged policy here reports no
change at all, verified in the ladder.

## Additive by default, exact on request

**Additive** (the default) leaves rules the policy does not name alone. This
is the right mode for adopting a network that already has rules somebody else
made.

**Exact** (`exact: true` per network) deletes non-system rules the policy does
not name — "nothing exists on this network but this policy". **System rules
are never touched**; deleting one is not a decision the operator made, and an
exact policy that quietly removed them would break the network it was meant to
secure.

Adopt additively first, read what it reports, then flip to exact once the
policy names everything you intend to keep.

## Usage

```yaml
- role: vergeio.vergeos.network_policy
  vars:
    network_policy_networks:
      - network: DMZ
        exact: true
        rules:
          - name: allow-ssh-from-jump
            protocol: tcp
            source_ip: 192.0.2.10
            destination_ports: "22"
          - name: allow-https
            protocol: tcp
            destination_ports: "443"
          - name: old-rule-we-retired
            state: absent
```

`name` is the idempotence key. Everything else falls through to the
`vnet_rule` module's own defaults.

## A limit worth knowing

Rules are created in list order on a fresh network. When retrofitting a
network that already has rules, set an explicit `order:` per rule rather than
relying on position — otherwise where your rule lands relative to the existing
ones is not something the policy controls.

## Results

**Live ladder** — `tests/live/verify-network-policy.yml`, passed on
**VergeOS 26.1.8, two nodes** (`ok=67 changed=13 failed=0`):

two rules enforced with a single apply → converged re-run is zero-change →
a drifted port corrected with exactly one apply → additive mode keeps an
unmanaged rule → exact mode purges it while keeping both declared rules and
every system rule → converged exact run is still zero-change →
`state: absent` in the policy removes a rule → scratch network deleted.

The ladder only ever touches `zz-policy-net`.
