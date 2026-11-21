# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

"""Parse Proxmox VE NIC configuration into the shared network schema.

Pure transformation: `qm config` text or a dict of net lines in, a list
of NIC records out. No I/O.

Why this exists
---------------
The migration previously derived a NIC's network with a single regex,
`bridge=([^,]+)`, defaulting to 'vmbr0'. That is lossy in two ways that
both corrupt the destination topology:

  1. It drops `tag=`. Two NICs on `vmbr0,tag=20` and `vmbr0,tag=30` are
     in DIFFERENT layer-2 domains, but both reduced to 'vmbr0' and were
     then collapsed into a single OVF <Network>. Migrating them would
     merge two isolated VLANs into one.

  2. It invented a bridge for NICs that have none. A Proxmox NIC with no
     `bridge=` is deliberately unconnected; defaulting it to 'vmbr0'
     silently attaches an isolated interface to the production bridge.

So the unit of identity here is the SEGMENT -- the (bridge, vlan) pair --
not the bridge name. `segment` is what downstream mapping keys on.

Proxmox NIC syntax (qm.conf / `qm config`)::

    netN: <model>=<MAC>[,bridge=<br>][,tag=<vlan>][,trunks=<v;v;v>]
          [,firewall=<0|1>][,mtu=<n>][,rate=<MB/s>][,queues=<n>]
          [,link_down=<0|1>][,macaddr=<MAC>]

Fields not represented in the output are deliberately dropped, not
silently mapped: see UNSUPPORTED_KEYS.
"""

import re

# Every NIC model Proxmox accepts. The model is part of the guest's
# hardware identity: switching a Linux guest from virtio to e1000 renames
# its interface (ens18 -> eth0), which can strand a statically-addressed
# host. Captured so the destination can preserve or consciously override.
PVE_MODELS = (
    "e1000", "e1000-82540em", "e1000-82544gc", "e1000-82545em", "e1000e",
    "i82551", "i82557b", "i82559er", "ne2k_isa", "ne2k_pci", "pcnet",
    "rtl8139", "virtio", "vmxnet3",
)

MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")

# Recognised but intentionally not carried into the manifest, because the
# destination has no equivalent or the value is host-local. Listed so that
# "we did not translate this" is explicit rather than an omission.
UNSUPPORTED_KEYS = frozenset({"macaddr", "bridge_disable_mac_learning"})


class NetParseError(ValueError):
    """A netN line that cannot be trusted. Never guessed around."""


def _split_options(value):
    """Split a netN value into (model, mac, options dict).

    The first field may be `<model>=<MAC>` (the usual shape) or a bare
    `key=value`. Anything else is an error rather than a default.
    """
    parts = [p for p in value.split(",") if p.strip()]
    if not parts:
        raise NetParseError("empty NIC definition")

    model = None
    mac = ""
    opts = {}
    for i, part in enumerate(parts):
        key, eq, val = part.partition("=")
        key = key.strip()
        val = val.strip()
        if not eq:
            raise NetParseError("malformed field %r" % part)
        if i == 0 and key in PVE_MODELS:
            model = key
            if not MAC_RE.match(val):
                raise NetParseError("model %s has a malformed MAC %r"
                                    % (key, val))
            mac = val.upper()
            continue
        opts[key.lower()] = val
    if model is None:
        raise NetParseError("no NIC model in %r (expected one of %s)"
                            % (value, ", ".join(PVE_MODELS)))
    # `macaddr=` is an alternative spelling Proxmox accepts.
    if not mac and "macaddr" in opts and MAC_RE.match(opts["macaddr"]):
        mac = opts["macaddr"].upper()
    return model, mac, opts


def _int_or_none(opts, key):
    raw = opts.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise NetParseError("%s=%r is not an integer" % (key, raw)) from exc


def _bool_flag(opts, key, default=False):
    raw = opts.get(key)
    if raw is None or raw == "":
        return default
    return raw not in ("0", "false", "no")


def parse_trunks(raw):
    """Proxmox writes trunk VLAN lists as `10;20;30`."""
    if not raw:
        return []
    out = []
    for token in raw.split(";"):
        token = token.strip()
        if not token:
            continue
        try:
            vlan = int(token)
        except ValueError as exc:
            raise NetParseError("trunks contains non-integer %r" % token) from exc
        if not 1 <= vlan <= 4094:
            raise NetParseError("trunk VLAN %d out of range 1-4094" % vlan)
        out.append(vlan)
    return sorted(set(out))


def segment_id(bridge, tag, trunks):
    """Stable identity for the layer-2 domain a NIC lands in.

    Unconnected NICs get None -- they have no segment, and inventing one
    is exactly the bug this module exists to fix. Trunk ports get a
    distinct ':trunk' form because they carry several VLANs and must not
    be folded into any single one.
    """
    if not bridge:
        return None
    if trunks:
        return "%s:trunk" % bridge
    if tag is not None:
        return "%s.%d" % (bridge, tag)
    return bridge


def parse_nic(key, value):
    model, mac, opts = _split_options(value)

    for unsupported in UNSUPPORTED_KEYS & set(opts):
        if unsupported != "macaddr":
            opts.pop(unsupported, None)

    bridge = opts.get("bridge") or None
    tag = _int_or_none(opts, "tag")
    if tag is not None and not 1 <= tag <= 4094:
        raise NetParseError("tag=%d out of range 1-4094" % tag)
    trunks = parse_trunks(opts.get("trunks"))
    # Proxmox permits an access VLAN together with a trunk. That has no
    # single destination equivalent, so it is recorded and the planner
    # refuses the NIC.
    access_plus_trunk = tag is not None and bool(trunks)

    mtu = _int_or_none(opts, "mtu")
    # virtio's `mtu=1` is the sentinel for "inherit the bridge MTU", not a
    # 1-byte MTU. Normalised to None so a mapper never programs MTU 1.
    inherit_mtu = mtu == 1
    if inherit_mtu:
        mtu = None

    rate = opts.get("rate")
    if rate not in (None, ""):
        try:
            rate = float(rate)
        except ValueError as exc:
            raise NetParseError("rate=%r is not a number" % rate) from exc
        if rate < 0:
            raise NetParseError("rate=%r is negative" % rate)
    else:
        rate = None

    return {
        "key": key,
        "index": int(key[3:]),
        "mac": mac,
        "model": model,
        "bridge": bridge,
        "tag": tag,
        "trunks": trunks,
        "segment": segment_id(bridge, tag, trunks),
        "connected": bridge is not None,
        "mtu": mtu,
        "mtu_inherit": inherit_mtu,
        "rate_mbyte_s": rate,
        "queues": _int_or_none(opts, "queues"),
        "firewall": _bool_flag(opts, "firewall"),
        "link_down": _bool_flag(opts, "link_down"),
        "access_plus_trunk": access_plus_trunk,
        # Compatibility with the existing manifest/OVF schema. Now carries
        # the segment, so tagged NICs no longer collapse together.
        "net": segment_id(bridge, tag, trunks) or "",
    }


NET_KEY_RE = re.compile(r"^net(\d+)$")


def extract_net_lines(text):
    """Pull `netN: ...` pairs out of raw `qm config` output.

    Deliberately not YAML: a Proxmox config can contain description and
    args values that are hostile to a YAML parser, and only netN lines
    matter here.
    """
    nets = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        if NET_KEY_RE.match(key):
            nets[key] = value.strip()
    return nets


def parse_config(text_or_obj):
    if isinstance(text_or_obj, dict):
        nets = {k: v for k, v in text_or_obj.items() if NET_KEY_RE.match(k)}
    else:
        nets = extract_net_lines(text_or_obj)
    out = []
    for key in sorted(nets, key=lambda k: int(NET_KEY_RE.match(k).group(1))):
        try:
            out.append(parse_nic(key, nets[key]))
        except NetParseError as exc:
            raise NetParseError("%s: %s" % (key, exc))
    return out


def group_segments(nics):
    """Group parsed NICs by segment id.

    Unconnected NICs have a None segment and stay in that group. The
    planner refuses trunk segments; this function only groups.
    """
    groups = {}
    order = []
    for nic in nics or []:
        seg = nic.get("segment")
        if seg not in groups:
            groups[seg] = []
            order.append(seg)
        groups[seg].append(nic.get("key"))
    return [{"segment": seg, "nic_keys": groups[seg]} for seg in order]
