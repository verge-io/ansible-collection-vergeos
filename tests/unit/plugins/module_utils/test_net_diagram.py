# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Diagrams are pictures of the plan. They do not apply it."""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os

from ansible_collections.vergeio.vergeos.plugins.module_utils import net_diagram
from ansible_collections.vergeio.vergeos.plugins.module_utils.netmap import plan


TOPOLOGY = {
    "segments": {
        "vmbr0": {
            "segment": "vmbr0", "bridge": "vmbr0", "vlan_aware": True,
            "uplinks": ["bond0"], "confidence": "name-only",
        },
        "vmbr0.20": {
            "segment": "vmbr0.20", "bridge": "vmbr0", "tag": 20,
            "cidr": "192.0.2.0/24", "confidence": "authoritative",
        },
    },
    "bonds": {"bond0": {"name": "bond0", "slaves": ["eno1", "eno2"]}},
}


def test_topology_mermaid_names_the_bridge_and_the_vlan():
    text = net_diagram.topology_mermaid(TOPOLOGY)
    assert text.startswith("flowchart LR\n")
    assert "vmbr0" in text
    assert "vlan 20" in text
    assert "192.0.2.0/24" in text


def test_topology_dot_is_a_digraph():
    text = net_diagram.topology_dot(TOPOLOGY)
    assert text.startswith("digraph topology {")
    assert "bond0" in text
    assert "->" in text


def test_topology_draws_a_nic_onto_its_segment():
    text = net_diagram.topology_mermaid(TOPOLOGY, [
        {"vm": "web01", "key": "net0", "segment": "vmbr0.20"},
    ])
    assert "web01" in text
    assert "net0" in text


def test_trunk_nic_is_labelled_refused():
    text = net_diagram.topology_mermaid(TOPOLOGY, [
        {"key": "net1", "segment": "vmbr0:trunk", "trunks": [10, 20]},
    ])
    assert "trunk refused" in text


def test_empty_topology_is_a_diagram():
    text = net_diagram.topology_mermaid({})
    assert "no topology" in text
    assert net_diagram.topology_dot(None).strip().endswith("}")


def _plan():
    nic = {
        "key": "net0", "mac": "AA:BB:CC:DD:EE:01", "model": "virtio",
        "bridge": "vmbr0", "tag": 20, "trunks": [], "segment": "vmbr0.20",
        "connected": True, "rate_mbyte_s": 12,
    }
    segments = {
        "vmbr0.20": {
            "segment": "vmbr0.20", "cidr": "192.0.2.0/24",
            "gateway": "192.0.2.1", "source": "sdn",
            "confidence": "authoritative",
        },
    }
    return plan({"web01": [nic]}, segments, [], {
        "order": ["synthesize"],
        "synthesize": {"interface_network": "ext1 Switch"},
    })


def test_mapping_diagram_shows_the_proposal_uplink_and_rate():
    mapped = _plan()
    text = net_diagram.mapping_mermaid(mapped)
    assert "proposal" in text
    assert "ext1 Switch" in text
    assert "rate_limit 12 MB/s" in text
    assert mapped["applied"] is False
    dot = net_diagram.mapping_dot(mapped)
    assert dot.startswith("digraph mapping {")
    assert "ext1 Switch" in dot


def test_mapping_diagram_shows_a_refusal():
    mapped = _plan()
    mapped["decisions"].append({
        "vm": "web01", "nic": "net1", "action": "refuse",
        "segment": "vmbr0:trunk", "target": None,
    })
    assert "refused" in net_diagram.mapping_mermaid(mapped)


def test_guard_diagram_names_the_mode():
    text = net_diagram.guard_mermaid({
        "attach_mode": "detached",
        "results": [{
            "vm": "web01", "nic": "net0", "verdict": "approve",
            "attach_to": None,
        }],
    })
    assert "attach_mode detached" in text
    assert "approve detached" in text
    dot = net_diagram.guard_dot({"results": []})
    assert "digraph guard" in dot


def test_diagram_paths_pair_mermaid_and_dot():
    mermaid, dot = net_diagram.diagram_paths("out/map.mmd")
    assert mermaid.endswith("map.mmd")
    assert dot.endswith("map.dot")
    mermaid, dot = net_diagram.diagram_paths("out/map")
    assert mermaid.endswith("map.mmd")
    assert dot.endswith("map.dot")


def test_write_diagrams_writes_both_files(tmp_path):
    target = str(tmp_path / "picture.mmd")
    mermaid_path, dot_path = net_diagram.write_diagrams(
        target, "flowchart LR\n", "digraph g {\n}\n")
    assert os.path.isfile(mermaid_path)
    assert os.path.isfile(dot_path)
    with open(dot_path) as handle:
        assert "digraph" in handle.read()


class _Module:
    def __init__(self, params, check_mode):
        self.params = params
        self.check_mode = check_mode
        self.result = None
        self.warnings = []

    def warn(self, warning):
        self.warnings.append(warning)

    def exit_json(self, **kwargs):
        self.result = kwargs
        raise SystemExit(0)

    def fail_json(self, **kwargs):
        self.result = kwargs
        raise SystemExit(1)


def test_deliver_check_mode_does_not_write_and_reports_unchanged(tmp_path):
    module = _Module({"diagram_path": str(tmp_path / "x.mmd"), "strict": False},
                     check_mode=True)
    try:
        net_diagram.deliver(module, {"ok": True, "refusals": []},
                            "flowchart LR\n", "digraph g {\n}\n")
    except SystemExit:
        pass
    assert module.result["changed"] is False
    assert module.result["applied"] is False
    assert module.result["diagram_written"] is False
    assert not os.path.exists(str(tmp_path / "x.mmd"))


def test_deliver_writes_when_asked_and_still_reports_unchanged(tmp_path):
    module = _Module({"diagram_path": str(tmp_path / "x.mmd"), "strict": False},
                     check_mode=False)
    try:
        net_diagram.deliver(module, {"ok": True}, "flowchart LR\n",
                            "digraph g {\n}\n")
    except SystemExit:
        pass
    assert module.result["changed"] is False
    assert module.result["diagram_written"] is True
    assert os.path.isfile(module.result["diagram_dot_path"])


def test_deliver_strict_fails_without_claiming_a_change():
    module = _Module({"diagram_path": None, "strict": True}, check_mode=False)
    try:
        net_diagram.deliver(
            module,
            {"ok": False, "refusals": [{"vm": "web01"}]},
            "flowchart LR\n", "digraph g {\n}\n", strict=True)
    except SystemExit:
        pass
    assert module.result["changed"] is False
    assert "refused" in module.result["msg"]


def _deliver(module, result, strict=False):
    try:
        net_diagram.deliver(module, result, "flowchart LR\n", "digraph g {\n}\n",
                            strict=strict)
    except SystemExit:
        return module
    raise AssertionError("deliver returned")


def test_deliver_emits_warnings_and_keeps_plan_warnings():
    module = _Module({"diagram_path": None}, check_mode=False)
    _deliver(module, {
        "ok": True,
        "warnings": ["proposed vnet names no uplink"],
        "plan": {"warnings": ["proposed vnet names no uplink"]},
    })
    assert "warnings" not in module.result
    assert module.result["plan"]["warnings"] == ["proposed vnet names no uplink"]
    assert module.warnings == ["proposed vnet names no uplink"]
    assert module.result["changed"] is False


def test_deliver_omits_an_empty_warnings_list():
    module = _Module({"diagram_path": None}, check_mode=False)
    _deliver(module, {"ok": True, "warnings": [], "plan": {"warnings": []}})
    assert "warnings" not in module.result
    assert module.warnings == []
    assert module.result["plan"]["warnings"] == []


def test_deliver_strict_failure_does_not_pass_warnings():
    module = _Module({"diagram_path": None}, check_mode=False)
    _deliver(
        module,
        {"ok": False, "refusals": [{"vm": "web01"}],
         "warnings": ["proposed vnet names no uplink"]},
        strict=True,
    )
    assert "warnings" not in module.result
    assert module.warnings == ["proposed vnet names no uplink"]
    assert "refused" in module.result["msg"]
