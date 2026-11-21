# Network translation

Planning and visualization for a captured Proxmox network. The modules
read captured config and, when the playbook passes it, the existing
VergeOS vnet list. They return structured data and diagrams.

They do not migrate data. They do not change VergeOS. They do not change
the source hypervisor. They do not create, modify, or attach vnets, and
they do not call `network` to apply an uplink or a rate limit. A
synthesized network in the plan is a proposal.

Cutover, applying a guarded plan during a migration, is out of scope.

## Modules

| Gate | Module | What it returns |
|---|---|---|
| 1 capture | `proxmox_net_config` | NIC records and segment ids from captured `qm config` text |
| 2 host topology | `proxmox_host_network` | Segment facts from captured `/etc/network/interfaces` and SDN cfg files |
| 3 mapping | `network_map` | A plan against existing vnets, including proposals |
| 4 blast radius | `network_guard` | A verdict per NIC |

Every module reports `changed=false` and supports check mode. In check
mode the diagram text is returned and the diagram file is not written.

`network_map` does not query the API. Pass `network_info`'s `networks`
list in `networks`. That read is the only VergeOS call the live probe
makes.

## Segments

The identity of a NIC's layer 2 is the segment, the `(bridge, vlan)`
pair: `vmbr0`, `vmbr0.20`, or `vmbr0:trunk`. A NIC with no `bridge` is
unconnected and stays that way. The NIC model is captured so a later
step can keep it.

## Mapping

Resolvers run in the order the policy names. The first answer wins.
The default order is `explicit`, `alias`, `vlan`, `name`. `synthesize`
is not in the default, so proposing a new vnet is opt-in.

Ambiguity is a refusal that names both candidates and stops the chain.
Two vnets on one VLAN are not a guess, and they are not a reason to
synthesize a third.

`vlan` matching reads `vlan_id` or the API column `layer2_id`, which is
what `network_info` returns.

A proposal uses the `network` module's parameter names. `interface_network`
is the proposed uplink (the module stores it as `interface_vnet`).
`rate_limit` is the captured Proxmox `rate`, in MB/s, as an integer.
`0` means explicitly uncapped. A positive fraction that would round to
0 is proposed as 1. Neither field is sent to VergeOS. When the policy
names no `interface_network`, the proposal says so and the plan warns.
That warning is not a claim that the `network` module is unable to set
an uplink.

## Guard

| Mode | Verdict |
|---|---|
| `detached` (default) | Approve leaving the NIC unconnected. The intended target is recorded. |
| `bubble` | Approve the named bubble vnet instead of the mapped target. Isolation of that vnet is an operator assertion. |
| `live` | Approve the mapped target only when the source VM is confirmed down. |

Confirmed down means `stopped`, `shutdown`, `off`, `poweroff`, or
`down`. Anything else, including a VM with no supplied state, counts as
running. `live_attach_running_override` is a list of VM names and is
honoured only in `live` mode.

## Diagrams

`proxmox_host_network` returns a Mermaid flowchart and Graphviz DOT of
the source topology. `network_map` returns the same pair for the
source-to-VergeOS proposal, with proposed uplinks and rate limits on
the proposal nodes. `network_guard` returns the verdicts. Set
`diagram_path` to write the Mermaid file; the DOT file is written
beside it. Check mode does not write.

## Trunks

A trunk carries several VLANs. No single vnet represents it. The parser
records the trunk as its own segment (`vmbr0:trunk`). `network_map`
refuses the NIC and the error names the VLAN list. Trunk translation
is out of scope.

## What is not proven here

Host topology is fixture-tested. This collection has no Proxmox node
to run it against.

## Live probe

The probe is read-only. It reads existing vnets, plans against that
list, and creates nothing, so there is nothing to clean up. It does
not select a tenant.

```bash
set -a; . ~/.config/vergeos/env; set +a
cd tests/live
ansible-playbook verify-network-translation.yml
```

`NETMAP_PROBE_CIDR`, `NETMAP_PROBE_GATEWAY`, `NETMAP_PROBE_DHCP_START`
and `NETMAP_PROBE_DHCP_END` override the documentation-range addresses
used in the proposal. The defaults are `192.0.2.0/24`, `192.0.2.1`,
`192.0.2.10` and `192.0.2.40`. The gateway and the DHCP range have to
sit inside the CIDR. Those addresses are plan input. They are not
configured on VergeOS.
