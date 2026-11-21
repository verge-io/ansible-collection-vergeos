# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

"""Parse Proxmox host network topology into segment facts.

Pure transformation. Input is a JSON bundle of already-read file contents
(so the caller owns all remote I/O)::

    {"interfaces": "<text of /etc/network/interfaces>",
     "sdn": {"zones.cfg": "...", "vnets.cfg": "...", "subnets.cfg": "..."}}

Output is a JSON object whose most important key is `segments`: a map from
the SAME segment identity pve_net_parse.py produces for a NIC, to
everything the host knows about that layer-2 domain.

Why this exists
---------------
A bridge NAME is not a network DEFINITION. `vmbr0` says nothing about the
uplink, whether the bridge is VLAN-aware, or what subnet lives on it.
Without that, "synthesise an equivalent network on the destination" is
guesswork. This module is what turns a name into facts.

Two sources are merged:

  * /etc/network/interfaces -- ifupdown2 stanzas. Gives bridges, their
    ports, bonds, VLAN-awareness, MTU, and any L3 the HOST holds on the
    segment. Note the host's own address is not necessarily the guest
    subnet; it is reported as `host_addresses`, never as the segment's
    subnet, because on a VLAN-aware bridge they are routinely different.

  * /etc/pve/sdn/*.cfg -- when Proxmox SDN is in use this is far richer:
    zones carry the underlying bridge, vnets carry the VLAN tag, and
    subnets carry CIDR, gateway and DHCP ranges. That is exactly the
    information synthesis needs, and it is authoritative rather than
    inferred.

Everything is reported with a `confidence` and a `source`, so a caller can
refuse to synthesise from a guess while still accepting SDN facts.
"""

import ipaddress
import re


def _norm_key(key):
    """ifupdown2 accepts dashes and underscores interchangeably."""
    return key.strip().lower().replace("_", "-")


def parse_interfaces(text):
    """Parse /etc/network/interfaces into {name: {stanza options}}.

    Only `iface` stanzas carry options. `auto`/`allow-*` lines are noted
    as flags. Continuation of a stanza is by indentation, per ifupdown.
    """
    ifaces = {}
    autos = set()
    current = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indented = line[0] in " \t"
        fields = line.split()
        if not indented:
            current = None
            head = fields[0].lower()
            if head == "auto" or head.startswith("allow-"):
                autos.update(fields[1:])
                continue
            if head == "iface" and len(fields) >= 2:
                name = fields[1]
                entry = ifaces.setdefault(name, {"name": name, "options": {},
                                                 "families": []})
                # `inet` / `inet6` plus a method (static/manual/dhcp/...)
                if len(fields) >= 4:
                    entry["families"].append((fields[2], fields[3]))
                elif len(fields) == 3:
                    entry["families"].append((fields[2], ""))
                current = entry
                continue
            if head == "source" or head == "source-directory":
                # Included files are the caller's problem to concatenate;
                # silently ignoring them would hide bridges.
                ifaces.setdefault("__sourced__", {"name": "__sourced__",
                                                  "options": {},
                                                  "families": []})
                ifaces["__sourced__"]["options"].setdefault(
                    "source", []).append(" ".join(fields[1:]))
                continue
            continue
        if current is None:
            continue
        key = _norm_key(fields[0])
        value = " ".join(fields[1:])
        current["options"].setdefault(key, []).append(value)
    for name, iface in ifaces.items():
        iface["auto"] = name in autos
    return ifaces


def _opt(entry, key, default=None):
    vals = entry["options"].get(key)
    return vals[0] if vals else default


def _opt_all(entry, key):
    out = []
    for v in entry["options"].get(key, []):
        out.extend(v.split())
    return out


def _truthy(value):
    return value is not None and value.strip().lower() in (
        "yes", "on", "1", "true")


VLAN_IFACE_RE = re.compile(r"^(?P<parent>.+)\.(?P<tag>\d+)$")


def classify_interfaces(ifaces):
    """Split parsed stanzas into bridges, bonds and VLAN sub-interfaces."""
    bridges, bonds, vlans = {}, {}, {}
    for name, entry in ifaces.items():
        if name == "__sourced__":
            continue
        opts = entry["options"]
        ovs_type = (_opt(entry, "ovs-type") or "").lower()

        if "bridge-ports" in opts or "bridge-vlan-aware" in opts \
                or ovs_type == "ovsbridge":
            ports = [p for p in _opt_all(entry, "bridge-ports")
                     if p.lower() != "none"]
            ports += [p for p in _opt_all(entry, "ovs-ports")
                      if p.lower() != "none"]
            bridges[name] = {
                "name": name,
                "kind": "ovs-bridge" if ovs_type == "ovsbridge"
                        else "linux-bridge",
                "ports": ports,
                "vlan_aware": _truthy(_opt(entry, "bridge-vlan-aware")),
                "vids": _opt(entry, "bridge-vids"),
                "mtu": _int(_opt(entry, "mtu")),
                "host_addresses": _opt_all(entry, "address"),
                "host_gateway": _opt(entry, "gateway"),
                "auto": entry["auto"],
            }
            continue

        if "bond-slaves" in opts or "ovs-bonds" in opts:
            bonds[name] = {
                "name": name,
                "slaves": (_opt_all(entry, "bond-slaves")
                           or _opt_all(entry, "ovs-bonds")),
                "mode": _opt(entry, "bond-mode"),
                "mtu": _int(_opt(entry, "mtu")),
            }
            continue

        m = VLAN_IFACE_RE.match(name)
        if m and _int(m.group("tag")) is not None:
            vlans[name] = {
                "name": name,
                "parent": m.group("parent"),
                "tag": int(m.group("tag")),
                "mtu": _int(_opt(entry, "mtu")),
                "host_addresses": _opt_all(entry, "address"),
                "host_gateway": _opt(entry, "gateway"),
            }
            continue

        # OVS internal port carrying an access VLAN: ovs_options tag=20
        ovs_opts = _opt(entry, "ovs-options") or ""
        tagm = re.search(r"\btag=(\d+)\b", ovs_opts)
        if tagm and _opt(entry, "ovs-bridge"):
            parent = _opt(entry, "ovs-bridge")
            vlans["%s.%s" % (parent, tagm.group(1))] = {
                "name": name,
                "parent": parent,
                "tag": int(tagm.group(1)),
                "mtu": _int(_opt(entry, "mtu")),
                "host_addresses": _opt_all(entry, "address"),
                "host_gateway": _opt(entry, "gateway"),
            }
    return bridges, bonds, vlans


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_uplinks(bridge, bonds, depth=0):
    """Flatten bridge ports through bonds and VLAN sub-interfaces to the
    physical NICs actually carrying the segment."""
    out = []
    for port in bridge.get("ports", []):
        base = port.split(".", 1)[0]
        if base in bonds and depth < 4:
            out.extend(bonds[base]["slaves"])
        else:
            out.append(port)
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def parse_sdn_cfg(text):
    """Parse a PVE section-config file (zones/vnets/subnets .cfg).

    Shape is::

        <type>: <id>
            <key> <value>
            <key> <value>
    """
    out = {}
    current = None
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line[0] not in " \t":
            head, sep, ident = line.partition(":")
            if not sep:
                continue
            current = {"type": head.strip(), "id": ident.strip()}
            out[ident.strip()] = current
            continue
        if current is None:
            continue
        fields = line.split(None, 1)
        current[_norm_key(fields[0])] = fields[1].strip() if len(fields) > 1 \
            else ""
    return out


SUBNET_ID_RE = re.compile(r"^(?P<zone>.+)-(?P<net>[0-9a-fA-F:.]+)-(?P<pfx>\d+)$")


def subnet_cidr(entry):
    """PVE encodes a subnet's CIDR in its section id: zone-10.0.20.0-24."""
    m = SUBNET_ID_RE.match(entry["id"])
    if not m:
        return None
    cidr = "%s/%s" % (m.group("net"), m.group("pfx"))
    try:
        return str(ipaddress.ip_network(cidr, strict=False))
    except ValueError:
        return None


def parse_dhcp_range(entry):
    raw = entry.get("dhcp-range") or ""
    start = re.search(r"start-address=([^,\s]+)", raw)
    end = re.search(r"end-address=([^,\s]+)", raw)
    if start and end:
        return {"start": start.group(1), "end": end.group(1)}
    return None


def build_sdn(zones_text, vnets_text, subnets_text):
    zones = parse_sdn_cfg(zones_text)
    vnets = parse_sdn_cfg(vnets_text)
    subnets = parse_sdn_cfg(subnets_text)

    by_vnet = {}
    for entry in subnets.values():
        vnet = entry.get("vnet")
        if not vnet:
            continue
        by_vnet.setdefault(vnet, []).append({
            "cidr": subnet_cidr(entry),
            "gateway": entry.get("gateway"),
            "snat": entry.get("snat") in ("1", "true", "yes"),
            "dhcp": parse_dhcp_range(entry),
        })
    return {"zones": zones, "vnets": vnets, "subnets": subnets,
            "subnets_by_vnet": by_vnet}


def build_segments(bridges, bonds, vlans, sdn):
    """Join everything into segment-keyed facts.

    Keys match pve_net_parse.segment_id(): 'vmbr0', 'vmbr0.20',
    'vmbr0:trunk', or an SDN vnet name (optionally '.tag').
    """
    segments = {}

    def put(seg_id, **facts):
        segments.setdefault(seg_id, {"segment": seg_id})
        segments[seg_id].update({k: v for k, v in facts.items()
                                 if v is not None})

    for name, br in bridges.items():
        put(name,
            kind=br["kind"],
            bridge=name,
            vlan_aware=br["vlan_aware"],
            vids=br["vids"],
            mtu=br["mtu"],
            uplinks=resolve_uplinks(br, bonds),
            host_addresses=br["host_addresses"] or None,
            host_gateway=br["host_gateway"],
            source="interfaces",
            confidence="host" if br["host_addresses"] else "name-only")

    for name, vl in vlans.items():
        parent = bridges.get(vl["parent"])
        put(name,
            kind="vlan",
            bridge=vl["parent"],
            tag=vl["tag"],
            mtu=vl["mtu"] or (parent or {}).get("mtu"),
            uplinks=resolve_uplinks(parent, bonds) if parent else None,
            host_addresses=vl["host_addresses"] or None,
            host_gateway=vl["host_gateway"],
            source="interfaces",
            confidence="host" if vl["host_addresses"] else "name-only")

    # SDN is authoritative where it exists, so it is applied last.
    for vnet_id, vnet in sdn["vnets"].items():
        zone = sdn["zones"].get(vnet.get("zone") or "", {})
        tag = _int(vnet.get("tag"))
        nets = sdn["subnets_by_vnet"].get(vnet_id, [])
        primary = nets[0] if nets else {}
        facts = dict(
            kind="sdn-vnet",
            sdn_vnet=vnet_id,
            sdn_zone=vnet.get("zone"),
            sdn_zone_type=zone.get("type"),
            alias=vnet.get("alias"),
            bridge=zone.get("bridge"),
            tag=tag,
            vlan_aware=vnet.get("vlanaware") in ("1", "true", "yes"),
            cidr=primary.get("cidr"),
            gateway=primary.get("gateway"),
            snat=primary.get("snat"),
            dhcp=primary.get("dhcp"),
            subnets=nets or None,
            mtu=_int(zone.get("mtu")),
            source="sdn",
            # SDN carries CIDR/gateway/DHCP outright -- nothing is inferred.
            confidence="authoritative" if primary.get("cidr") else "sdn",
        )
        put(vnet_id, **facts)
        if tag is not None and zone.get("bridge"):
            put("%s.%d" % (zone["bridge"], tag), **facts)

    return segments


def analyse(bundle):
    ifaces = parse_interfaces(bundle.get("interfaces") or "")
    bridges, bonds, vlans = classify_interfaces(ifaces)
    sdn_files = bundle.get("sdn") or {}
    sdn = build_sdn(sdn_files.get("zones.cfg"),
                    sdn_files.get("vnets.cfg"),
                    sdn_files.get("subnets.cfg"))
    result = {
        "bridges": bridges,
        "bonds": bonds,
        "vlan_interfaces": vlans,
        "sdn": sdn,
        "segments": build_segments(bridges, bonds, vlans, sdn),
    }
    sourced = ifaces.get("__sourced__")
    if sourced:
        # A `source` line means stanzas live in files we were not handed.
        # Reported, because a missing bridge would otherwise look like an
        # unmapped segment later.
        result["warnings"] = [
            "interfaces includes external files not provided: %s"
            % ", ".join(sourced["options"].get("source", []))
        ]
    return result
