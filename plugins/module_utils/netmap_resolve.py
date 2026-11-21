# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Resolver chain for a network map plan. Proposals only."""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import ipaddress
import re

RESOLVERS = ("explicit", "alias", "vlan", "name", "synthesize")
CONFIDENCE_RANK = {"name-only": 0, "host": 1, "sdn": 2, "authoritative": 3}
VNET_TYPES = ("internal", "external", "dmz")


class PolicyError(ValueError):
    """Policy that cannot be honoured."""


def normalise(name):
    """Fold names for comparison only."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _hit(resolver, target, why, **extra):
    result = {"resolver": resolver, "target": target, "why": why}
    result.update(extra)
    return result


def proposed_rate_limit(rate):
    """Integer MB/s for the network module's rate_limit parameter.

    Proxmox `rate` is MB/s and may be fractional. The network module's
    rate_limit is an integer number of MB/s, and 0 means explicitly
    uncapped. A positive fraction that would round to 0 becomes 1 so a
    cap is not proposed as uncapped. None means the source set no rate.
    """
    if rate is None or rate == "":
        return None
    value = float(rate)
    if value < 0:
        raise PolicyError("rate_mbyte_s %r is negative" % rate)
    rounded = round(value)
    if value > 0 and rounded == 0:
        rounded = 1
    return rounded


def _ambiguous(resolver, candidates, why):
    return {"resolver": resolver, "ambiguous": [c["name"] for c in candidates],
            "why": why}


def r_explicit(nic, facts, inv, policy):
    table = policy.get("explicit") or {}
    for key in (nic.get("segment"), nic.get("bridge")):
        if key and key in table:
            return _hit("explicit", table[key],
                        "operator table maps %s" % key)
    return None


def r_alias(nic, facts, inv, policy):
    alias = (facts or {}).get("alias")
    if not alias:
        return None
    cands = inv["by_name"].get(normalise(alias), [])
    if len(cands) == 1:
        return _hit("alias", cands[0]["name"],
                    "source SDN alias %r matches an existing vnet" % alias)
    if len(cands) > 1:
        return _ambiguous("alias", cands, "alias %r matches %d vnets"
                          % (alias, len(cands)))
    return None


def r_vlan(nic, facts, inv, policy):
    vlan = nic.get("tag")
    if vlan is None:
        vlan = (facts or {}).get("tag")
    if vlan is None:
        return None
    if nic.get("trunks"):
        return None
    cands = inv["by_vlan"].get(vlan, [])
    if len(cands) == 1:
        return _hit("vlan", cands[0]["name"],
                    "VLAN %d matches an existing vnet" % vlan)
    if len(cands) > 1:
        return _ambiguous("vlan", cands,
                          "VLAN %d matches %d vnets" % (vlan, len(cands)))
    return None


def r_name(nic, facts, inv, policy):
    for key, label in ((nic.get("segment"), "segment"),
                       ((facts or {}).get("sdn_vnet"), "SDN vnet"),
                       (nic.get("bridge"), "bridge")):
        if not key:
            continue
        cands = inv["by_name"].get(normalise(key), [])
        if len(cands) == 1:
            return _hit("name", cands[0]["name"],
                        "%s name %r matches an existing vnet" % (label, key))
        if len(cands) > 1:
            return _ambiguous("name", cands, "%s %r matches %d vnets"
                              % (label, key, len(cands)))
    return None


def _blocked(why):
    return {"resolver": "synthesize", "blocked": True, "why": why}


def _synthesis_refused(nic, facts, floor):
    have = facts.get("confidence") if facts else None
    if nic.get("trunks"):
        return _blocked(
            "segment is a VLAN trunk carrying %s; a single vnet cannot "
            "represent it, and trunk translation is out of scope"
            % nic["trunks"])
    if not facts:
        return _blocked(
            "no host topology facts for segment %r -- nothing to build from"
            % nic.get("segment"))
    if floor != "any" and (
            have is None or CONFIDENCE_RANK.get(have, -1) < CONFIDENCE_RANK[floor]):
        return _blocked(
            "topology confidence %r is below the required %r; synthesising "
            "would invent a subnet" % (have, floor))
    return None


def _synthesis_name(nic, facts, cfg):
    vlan = nic.get("tag") if nic.get("tag") is not None else (facts.get("tag") or "")
    name = cfg.get("name_template", "{segment}").format(
        segment=nic.get("segment") or "",
        bridge=nic.get("bridge") or "",
        vlan=vlan,
        alias=facts.get("alias") or "",
        vnet=facts.get("sdn_vnet") or "")
    return name.strip("-. ")


def _fill_layer3(spec, facts, cfg):
    """ip_address is the vnet's own address. gateway is only the upstream."""
    cidr = facts.get("cidr")
    if cidr:
        spec["network"] = cidr
        if facts.get("gateway"):
            spec["ip_address"] = facts["gateway"]
    upstream = cfg.get("upstream_gateway")
    if upstream and cidr:
        spec["gateway"] = upstream
    dhcp_mode = cfg.get("dhcp", "inherit")
    dhcp = facts.get("dhcp")
    if dhcp_mode == "on" or (dhcp_mode == "inherit" and dhcp):
        spec["dhcp_enabled"] = True
        if dhcp:
            spec["dhcp_start"] = dhcp.get("start")
            spec["dhcp_end"] = dhcp.get("end")
    elif dhcp_mode == "off":
        spec["dhcp_enabled"] = False
    return cidr


def _addresses_outside(spec, cidr):
    if not cidr:
        return None
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        return _blocked("unusable address in topology: %s" % exc)
    for field in ("ip_address", "gateway", "dhcp_start", "dhcp_end"):
        value = spec.get(field)
        if value and ipaddress.ip_address(value) not in net:
            return _blocked(
                "%s %s is outside %s; the source topology is inconsistent"
                % (field, value, cidr))
    return None


def _proposal_note(spec, cfg, nic):
    uplink = (cfg.get("interface_network") or "").strip()
    if uplink:
        spec["interface_network"] = uplink
    rate = proposed_rate_limit(nic.get("rate_mbyte_s"))
    if rate is not None:
        spec["rate_limit"] = rate
    if uplink:
        return {"note": "proposal includes uplink %s. Nothing is created "
                        "or attached." % uplink}
    return {
        "isolated": True,
        "note": "proposal names no interface_network, so this vnet would "
                "have no uplink. Name one in synthesize.interface_network; "
                "the network module can set it. This plan does not create "
                "the vnet.",
    }


def r_synthesize(nic, facts, inv, policy):
    cfg = policy["synthesize"]
    facts = facts or {}
    refused = _synthesis_refused(
        nic, facts, cfg.get("require_confidence", "authoritative"))
    if refused:
        return refused
    name = _synthesis_name(nic, facts, cfg)
    if not name:
        return _blocked("name_template produced an empty name")
    ntype = cfg.get("network_type", "internal")
    if ntype not in VNET_TYPES:
        return _blocked(
            "network_type %r is not a VergeOS vnet type (%s); VLANs are "
            "expressed with layer2_type, not a type"
            % (ntype, ", ".join(VNET_TYPES)))
    vlan = nic.get("tag") if nic.get("tag") is not None else facts.get("tag")
    spec = {"name": name, "state": "present", "network_type": ntype}
    if vlan is not None:
        spec["layer2_type"] = "vlan"
        spec["vlan_id"] = vlan
    if facts.get("mtu"):
        spec["mtu"] = facts["mtu"]
    cidr = _fill_layer3(spec, facts, cfg)
    outside = _addresses_outside(spec, cidr)
    if outside:
        return outside
    extra = _proposal_note(spec, cfg, nic)
    return _hit(
        "synthesize", name,
        "proposal built from %s facts (confidence %s); not applied"
        % (facts.get("source"), facts.get("confidence")),
        create=spec, **extra)


RESOLVER_FUNCS = {"explicit": r_explicit, "alias": r_alias, "vlan": r_vlan,
                  "name": r_name, "synthesize": r_synthesize}
