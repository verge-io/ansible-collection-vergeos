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

`vlan` matching compares a Proxmox VLAN tag with a VergeOS network
whose `layer2_type` is `vlan`. The id is `vlan_id` when that field is
set, otherwise the API column `layer2_id`. `network_info` returns
`layer2_type` and `layer2_id`.

A VXLAN network stays out of that match. Its `layer2_id` is a VXLAN id,
so tags 1 and 2 stay off Core and DMZ when those networks use VXLAN ids
1 and 2. `layer2_type: none` and any other layer 2 type are treated the
same way. A stated type other than `vlan` stays unmatched even when the
row also carries `vlan_id`.

When `layer2_type` is missing, `layer2_id` is left unused. The number
may be a VXLAN id on a row that omitted the type. An explicit `vlan_id`
still matches in that case, as do the `vlan` and `vlanid` spellings.
Those fields name a VLAN id.

The `name` resolver tries the segment, then the SDN vnet. It tries the
bare bridge only for an untagged segment. A tagged access port such as
`vmbr7.3001` stays off a vnet named `vmbr7`. VLAN 3001, VLAN 3002, and
the untagged `vmbr7` segment are three layer 2 domains. The untagged
segment can still match by its own name. A trunk is refused before this
resolver runs.

A proposal uses the `network` module's parameter names. `interface_network`
is the proposed uplink (the module stores it as `interface_vnet`).
`rate_limit` is the captured Proxmox `rate`, in MB/s, as an integer.
`0` means explicitly uncapped. A positive fraction that would round to
0 is proposed as 1. Neither field is sent to VergeOS. When the policy
names no `interface_network`, the proposal says so and the note is
recorded on `plan.warnings`. The same note is emitted with Ansible's
module warning facility. That note is not a claim that the `network`
module is unable to set an uplink.

`network_map`, `network_guard`, and `proxmox_host_network` do not pass
a top-level `warnings` key to `exit_json`. ansible-core 2.21 and later
deprecates that key, and 2.23 removes it. `network_guard` emits its own
safety notes, such as a proposal that would hold a live source gateway,
the same way. Notes already on the plan stay on `plan.warnings`. When a
captured interfaces file sources other files, `proxmox_host_network`
emits that with the module warning facility.

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
