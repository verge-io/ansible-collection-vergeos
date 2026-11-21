# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Mermaid and Graphviz pictures of a captured topology and a map plan.

The pictures are strings. Nothing in this module contacts VergeOS or a
source hypervisor. A caller may write the strings to a path the operator
named; that write is a local file, not a migration.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os
import re


def diagram_paths(path):
    """Return ``(mermaid_path, dot_path)`` for a path the operator named."""
    path = os.path.abspath(os.path.expanduser(path))
    root, ext = os.path.splitext(path)
    lowered = ext.lower()
    if lowered in (".mmd", ".md", ".mermaid"):
        return path, root + ".dot"
    if lowered == ".dot":
        return root + ".mmd", path
    return path + ".mmd", path + ".dot"


def deliver(module, result, mermaid, dot, strict=False):
    """Return a read-only result, optionally writing the pictures.

    ``changed`` is always false. Check mode returns the pictures and does
    not write. ``strict`` fails the module when the result is not ok, and
    still includes the result so the refusal is visible.
    """
    payload = dict(result)
    payload["diagram_mermaid"] = mermaid
    payload["diagram_dot"] = dot
    payload["changed"] = False
    payload["applied"] = False
    diagram_path = module.params.get("diagram_path")
    if diagram_path:
        mermaid_path, dot_path = diagram_paths(diagram_path)
        payload["diagram_mermaid_path"] = mermaid_path
        payload["diagram_dot_path"] = dot_path
        if module.check_mode:
            payload["diagram_written"] = False
        else:
            write_diagrams(diagram_path, mermaid, dot)
            payload["diagram_written"] = True
    else:
        payload["diagram_written"] = False
    if strict and payload.get("ok") is False:
        module.fail_json(msg=_strict_message(payload), **payload)
    module.exit_json(**payload)


def _strict_message(payload):
    refusals = payload.get("refusals") or []
    return "refused %d item(s); nothing was changed" % len(refusals)


def write_diagrams(path, mermaid, dot):
    """Write both pictures. Returns the two paths. Raises OSError on failure."""
    mermaid_path, dot_path = diagram_paths(path)
    for dest, content in ((mermaid_path, mermaid), (dot_path, dot)):
        parent = os.path.dirname(dest)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        temporary = dest + ".tmp"
        with open(temporary, "w") as handle:
            handle.write(content if content.endswith("\n") else content + "\n")
        os.replace(temporary, dest)
    return mermaid_path, dot_path


def topology_mermaid(topology, nics=None):
    nodes, edges = _topology_graph(topology, nics)
    return _mermaid(nodes, edges)


def topology_dot(topology, nics=None):
    nodes, edges = _topology_graph(topology, nics)
    return _dot("topology", nodes, edges)


def mapping_mermaid(plan):
    nodes, edges = _mapping_graph(plan)
    return _mermaid(nodes, edges)


def mapping_dot(plan):
    nodes, edges = _mapping_graph(plan)
    return _dot("mapping", nodes, edges)


def guard_mermaid(assessment):
    nodes, edges = _guard_graph(assessment)
    return _mermaid(nodes, edges)


def guard_dot(assessment):
    nodes, edges = _guard_graph(assessment)
    return _dot("guard", nodes, edges)


def _topology_graph(topology, nics=None):
    topology = topology or {}
    segments = topology.get("segments") or {}
    bonds = topology.get("bonds") or {}
    nodes = []
    edges = []
    seen = set()

    def add(node_id, label):
        if node_id in seen:
            return
        seen.add(node_id)
        nodes.append((node_id, label))

    for name in sorted(bonds):
        add("bond_" + _safe(name), "bond %s" % name)
    for seg_id in sorted(segments):
        facts = segments[seg_id] or {}
        add("seg_" + _safe(seg_id), _segment_label(seg_id, facts))
    for seg_id in sorted(segments):
        facts = segments[seg_id] or {}
        bridge = facts.get("bridge")
        if bridge and bridge != seg_id and bridge in segments:
            edges.append(("seg_" + _safe(bridge), "seg_" + _safe(seg_id), "carries"))
        for uplink in facts.get("uplinks") or []:
            if uplink in bonds:
                src = "bond_" + _safe(uplink)
                add(src, "bond %s" % uplink)
            else:
                src = "phy_" + _safe(uplink)
                add(src, uplink)
            edges.append((src, "seg_" + _safe(seg_id), "uplink"))
    for index, nic in enumerate(nics or []):
        ident = "nic_%d_%s" % (index, _safe(nic.get("key") or index))
        bits = [nic.get("vm") or "nic", nic.get("key") or ""]
        if nic.get("trunks"):
            bits.append("trunk refused")
        elif nic.get("segment"):
            bits.append(nic["segment"])
        else:
            bits.append("unconnected")
        add(ident, " / ".join(str(bit) for bit in bits if bit != ""))
        seg = nic.get("segment")
        if seg and seg in segments and not nic.get("trunks"):
            edges.append((ident, "seg_" + _safe(seg), "nic"))
    if not nodes:
        add("empty", "no topology")
    return nodes, edges


def _segment_label(seg_id, facts):
    bits = [seg_id]
    if facts.get("tag") is not None:
        bits.append("vlan %s" % facts["tag"])
    if facts.get("cidr"):
        bits.append(facts["cidr"])
    if facts.get("alias"):
        bits.append(facts["alias"])
    if facts.get("confidence"):
        bits.append(facts["confidence"])
    return " / ".join(str(bit) for bit in bits)


def _mapping_graph(plan):
    plan = plan or {}
    nodes = []
    edges = []
    seen = set()

    def add(node_id, label):
        if node_id in seen:
            return
        seen.add(node_id)
        nodes.append((node_id, label))

    for spec in plan.get("creates") or []:
        name = spec.get("name") or "proposal"
        bits = [name, "proposal"]
        if spec.get("interface_network"):
            bits.append("uplink %s" % spec["interface_network"])
        else:
            bits.append("no uplink")
        if spec.get("rate_limit") is not None:
            bits.append("rate_limit %s MB/s" % spec["rate_limit"])
        if spec.get("vlan_id") is not None:
            bits.append("vlan %s" % spec["vlan_id"])
        add("prop_" + _safe(name), " / ".join(str(bit) for bit in bits))
    for index, decision in enumerate(plan.get("decisions") or []):
        key = "%s/%s" % (decision.get("vm"), decision.get("nic"))
        src = "src_%d_%s" % (index, _safe(key))
        bits = [key, decision.get("segment") or "unconnected"]
        if decision.get("proposed_rate_limit") is not None:
            bits.append("rate_limit %s MB/s" % decision["proposed_rate_limit"])
        add(src, " / ".join(str(bit) for bit in bits))
        action = decision.get("action") or "unknown"
        target = decision.get("target")
        if action == "refuse":
            dst = "ref_%d" % index
            add(dst, "refused")
            edges.append((src, dst, "refuse"))
        elif action == "detach" or not target:
            dst = "det_%d" % index
            add(dst, "detached")
            edges.append((src, dst, action))
        else:
            proposed = any(
                (spec.get("name") == target) for spec in (plan.get("creates") or []))
            if proposed:
                dst = "prop_" + _safe(target)
                add(dst, "%s / proposal" % target)
            else:
                dst = "vnet_" + _safe(target)
                add(dst, str(target))
            edges.append((src, dst, action))
    if not nodes:
        add("empty", "no decisions")
    return nodes, edges


def _guard_graph(assessment):
    assessment = assessment or {}
    nodes = []
    edges = []
    seen = set()

    def add(node_id, label):
        if node_id in seen:
            return
        seen.add(node_id)
        nodes.append((node_id, label))

    mode = assessment.get("attach_mode") or "detached"
    add("mode", "attach_mode %s" % mode)
    for index, row in enumerate(assessment.get("results") or []):
        key = "%s/%s" % (row.get("vm"), row.get("nic"))
        src = "g_%d_%s" % (index, _safe(key))
        add(src, key)
        verdict = row.get("verdict") or "unknown"
        dst = "v_%d" % index
        if verdict == "approve" and row.get("attach_to"):
            label = "approve %s" % row["attach_to"]
        elif verdict == "approve":
            label = "approve detached"
        else:
            label = "refuse"
        add(dst, label)
        edges.append((src, dst, verdict))
    if len(nodes) == 1:
        add("empty", "no verdicts")
    return nodes, edges


def _mermaid(nodes, edges):
    lines = ["flowchart LR"]
    for node_id, label in nodes:
        lines.append('  %s["%s"]' % (node_id, _mermaid_label(label)))
    for src, dst, label in edges:
        lines.append("  %s -->|%s| %s" % (src, _mermaid_label(label), dst))
    return "\n".join(lines) + "\n"


def _dot(name, nodes, edges):
    lines = ["digraph %s {" % _safe(name), "  rankdir=LR;"]
    for node_id, label in nodes:
        lines.append('  %s [label="%s"];' % (node_id, _dot_label(label)))
    for src, dst, label in edges:
        lines.append('  %s -> %s [label="%s"];' % (src, dst, _dot_label(label)))
    lines.append("}")
    return "\n".join(lines) + "\n"


def _safe(text):
    raw = re.sub(r"[^A-Za-z0-9_]", "_", str(text))
    if not raw or raw[0].isdigit():
        raw = "n_" + raw
    return raw


def _mermaid_label(text):
    return str(text).replace("\\", "/").replace('"', "'")


def _dot_label(text):
    return str(text).replace("\\", "\\\\").replace('"', "'")
