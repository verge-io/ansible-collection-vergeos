# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

"""Decide which VergeOS vnet each migrating NIC belongs on.

Pure planning. Consumes the NIC facts from a source connector, the host
topology facts, an inventory of existing VergeOS vnets, and a policy;
emits a PLAN. The plan is data: networks it would synthesize are
proposals, not creates. It never talks to VergeOS or the source
hypervisor, and nothing here is migrated or attached.

Mapping policy is a CHOICE, not a fixed rule
--------------------------------------------
Sites differ too much for one answer. A lift-and-shift into a prepared
VergeOS estate wants "attach to the vnets I already built". A greenfield
DR target wants "build me what the source had". Most real waves want some
of each, with the operator pinning the handful that matter.

So resolution is an ORDERED CHAIN of named resolvers. Each one either
answers or abstains, and the first answer wins:

  explicit     operator's own segment -> vnet table. Always authoritative.
  alias        match an existing vnet by the source's SDN alias.
  vlan         match an existing vnet carrying the same VLAN id.
  name         match an existing vnet by (normalised) segment or bridge name.
  synthesize   emit a create-spec for a vnet the source implies.

`order` selects and sequences them, so `[explicit]` is a strict table,
`[explicit, vlan, name]` is map-to-existing with a manual override, and
`[explicit, alias, vlan, name, synthesize]` is best-effort with creation
as the fallback. Disabling is by omission.

Two rules hold regardless of policy:

  * Ambiguity is never resolved by guessing. If a resolver finds more than
    one candidate, the NIC is REFUSED and the candidates are reported.
  * Synthesis is gated on the CONFIDENCE of the topology facts. A bridge
    known only by name cannot produce a subnet, and inventing one would
    put guests on a network that silently does not route.
"""

import json

from ansible_collections.vergeio.vergeos.plugins.module_utils.netmap_resolve import (
    CONFIDENCE_RANK,
    RESOLVER_FUNCS,
    RESOLVERS,
    PolicyError,
    normalise,
    proposed_rate_limit,
)

DEFAULT_POLICY = {
    "order": ["explicit", "alias", "vlan", "name"],
    "explicit": {},
    # refuse | detach | bubble -- what to do with a NIC nothing resolved.
    # 'refuse' is the default because a wrongly-attached NIC is worse than
    # a stopped migration.
    "on_unmapped": "refuse",
    "bubble_network": "",
    "synthesize": {
        "require_confidence": "authoritative",
        "name_template": "{segment}",
        # VergeOS vnet types are internal | external | dmz. There is no
        # 'vlan' type: VLAN tagging is layer2_type=vlan + layer2_id, which
        # is orthogonal to the vnet's role. Verified against the lab API,
        # where a vnet row carries type/layer2_type/layer2_id separately.
        "network_type": "internal",
        "dhcp": "inherit",
        # Name of an existing VergeOS vnet (usually a physical switch)
        # the proposal would use as its uplink. This is the network
        # module's interface_network parameter, which that module stores
        # as interface_vnet. Empty means the proposal names no uplink.
        # Nothing here creates the vnet or sets the uplink.
        "interface_network": "",
        # A VergeOS vnet's `gateway` is its upstream router. A source
        # subnet says nothing about that, so it is never inferred.
        "upstream_gateway": "",
    },
    # Gate 4. detached | bubble | live
    "attach_mode": "detached",
    "allow_live_attach": False,
}


def merge_policy(user):
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    for key, value in (user or {}).items():
        if isinstance(value, dict) and isinstance(policy.get(key), dict):
            policy[key].update(value)
        else:
            policy[key] = value

    unknown = [r for r in policy["order"] if r not in RESOLVERS]
    if unknown:
        raise PolicyError(
            "unknown resolver(s) %s; valid: %s"
            % (", ".join(unknown), ", ".join(RESOLVERS)))
    if not policy["order"]:
        raise PolicyError("policy 'order' is empty -- nothing would resolve")
    if policy["on_unmapped"] not in ("refuse", "detach", "bubble"):
        raise PolicyError("on_unmapped must be refuse, detach or bubble")
    if policy["on_unmapped"] == "bubble" and not policy["bubble_network"]:
        raise PolicyError("on_unmapped=bubble requires bubble_network")
    if policy["attach_mode"] not in ("detached", "bubble", "live"):
        raise PolicyError("attach_mode must be detached, bubble or live")
    if policy["attach_mode"] == "bubble" and not policy["bubble_network"]:
        raise PolicyError("attach_mode=bubble requires bubble_network")
    floor = policy["synthesize"].get("require_confidence")
    if floor not in CONFIDENCE_RANK and floor != "any":
        raise PolicyError("require_confidence must be 'any' or one of %s"
                          % ", ".join(CONFIDENCE_RANK))
    return policy


def index_inventory(networks):
    """Index existing VergeOS vnets for lookup, tolerating key spellings."""
    by_name, by_vlan = {}, {}
    entries = []
    for net in networks or []:
        name = net.get("name") or ""
        # network_info returns the API row, where the VLAN id is layer2_id.
        # vlan_id is the network module's parameter name for the same column.
        vlan = net.get("vlan_id")
        if vlan is None:
            vlan = net.get("vlan")
        if vlan is None:
            vlan = net.get("vlanid")
        if vlan is None:
            vlan = net.get("layer2_id")
        try:
            vlan = int(vlan)
        except (TypeError, ValueError):
            vlan = None
        entry = {"name": name, "vlan": vlan,
                 "type": net.get("network_type") or net.get("type"),
                 "network": net.get("network"),
                 "raw": net}
        entries.append(entry)
        by_name.setdefault(normalise(name), []).append(entry)
        if vlan is not None:
            by_vlan.setdefault(vlan, []).append(entry)
    return {"entries": entries, "by_name": by_name, "by_vlan": by_vlan}


def plan(vms, segments, inventory, policy=None):
    """Produce the mapping plan. `vms` is {vm_name: [nic, ...]}."""
    policy = merge_policy(policy)
    inv = index_inventory(inventory)
    segments = segments or {}

    decisions = []
    creates = {}
    for vm_name in sorted(vms):
        for nic in vms[vm_name]:
            decisions.append(
                _decide(vm_name, nic, segments, inv, policy, creates))

    refusals = [d for d in decisions if d["action"] == "refuse"]
    warnings = []
    isolated = sorted({d["target"] for d in decisions if d.get("isolated")})
    if isolated:
        warnings.append(
            "%d proposed vnet(s) name no interface_network and would have "
            "no uplink: %s. Set synthesize.interface_network to propose "
            "one. Nothing was created."
            % (len(isolated), ", ".join(isolated)))
    proposed = [creates[k] for k in sorted(creates)]
    return {
        "policy": policy,
        "decisions": decisions,
        "creates": proposed,
        "proposals": proposed,
        "refusals": refusals,
        "warnings": warnings,
        "ok": not refusals,
        "applied": False,
        "summary": _summarise(decisions, creates),
    }


def _decide(vm_name, nic, segments, inv, policy, creates):
    base = {"vm": vm_name, "nic": nic.get("key"), "mac": nic.get("mac"),
            "model": nic.get("model"), "segment": nic.get("segment"),
            "tried": []}

    if nic.get("trunks"):
        # A trunk carries several VLANs. No single vnet represents it.
        # Translation would need several vnets and a guest-side decision,
        # which is out of scope. Refuse before any resolver can map the
        # bridge name and collapse the trunk into one segment.
        return _with_rate(dict(
            base, action="refuse", target=None, resolver="trunk",
            why="segment %r is a VLAN trunk carrying %s. Trunk translation "
                "is out of scope: a trunk has no single VergeOS vnet."
                % (nic.get("segment"), nic.get("trunks"))), nic)

    if not nic.get("connected"):
        # Deliberately isolated on the source. Honouring that is the whole
        # reason the parser stopped defaulting to vmbr0.
        return _with_rate(dict(
            base, action="detach", target=None, resolver="none",
            why="source NIC has no bridge; it is intentionally "
                "unconnected and stays that way"), nic)

    facts = segments.get(nic.get("segment")) or {}
    for name in policy["order"]:
        result = RESOLVER_FUNCS[name](nic, facts, inv, policy)
        if result is None:
            base["tried"].append({"resolver": name, "result": "abstained"})
            continue
        if result.get("ambiguous"):
            base["tried"].append({"resolver": name, "result": "ambiguous",
                                  "candidates": result["ambiguous"]})
            return _with_rate(dict(
                base, action="refuse", target=None, resolver=name,
                why="%s -- refusing rather than guessing between %s"
                    % (result["why"], ", ".join(result["ambiguous"]))), nic)
        if result.get("blocked"):
            base["tried"].append({"resolver": name, "result": "blocked",
                                  "why": result["why"]})
            continue
        spec = result.get("create")
        if spec:
            existing = creates.get(spec["name"])
            if existing and existing != spec:
                return _with_rate(dict(
                    base, action="refuse", target=None, resolver=name,
                    why="two segments would both propose vnet %r with "
                        "different settings" % spec["name"]), nic)
            creates[spec["name"]] = spec
        base["tried"].append({"resolver": name, "result": "matched"})
        decided = dict(base, action="create" if spec else "map",
                       target=result["target"], resolver=name,
                       why=result["why"],
                       confidence=facts.get("confidence"))
        if result.get("isolated"):
            decided["isolated"] = True
        if result.get("note"):
            decided["note"] = result["note"]
        return _with_rate(decided, nic)

    fallback = policy["on_unmapped"]
    if fallback == "detach":
        return _with_rate(dict(
            base, action="detach", target=None, resolver="fallback",
            why="no resolver matched; policy on_unmapped=detach"), nic)
    if fallback == "bubble":
        return _with_rate(dict(
            base, action="bubble", target=policy["bubble_network"],
            resolver="fallback",
            why="no resolver matched; policy on_unmapped=bubble"), nic)
    return _with_rate(dict(
        base, action="refuse", target=None, resolver="fallback",
        why="no resolver matched segment %r and on_unmapped=refuse"
            % nic.get("segment")), nic)


def _with_rate(decision, nic):
    """Carry a captured rate onto the decision. It is a proposal only."""
    raw = nic.get("rate_mbyte_s")
    if raw is None or raw == "":
        return decision
    limit = proposed_rate_limit(raw)
    decision["proposed_rate_limit"] = limit
    if float(raw) != limit:
        decision["rate_limit_note"] = (
            "Proxmox rate %s MB/s is proposed as integer rate_limit %s"
            % (raw, limit))
    return decision


def _summarise(decisions, creates):
    counts = {}
    for d in decisions:
        counts[d["action"]] = counts.get(d["action"], 0) + 1
    return {"nics": len(decisions), "by_action": counts,
            "vnets_to_create": len(creates)}
