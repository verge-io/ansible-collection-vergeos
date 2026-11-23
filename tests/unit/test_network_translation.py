# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The network translation modules plan and draw. They do not apply."""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import ast
import os

import yaml


MODULES = (
    "proxmox_net_config",
    "proxmox_host_network",
    "network_map",
    "network_guard",
)


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def _module_path(name):
    return os.path.join(_root(), "plugins", "modules", name + ".py")


def _read(path):
    with open(path) as handle:
        return handle.read()


def _assign(path, name):
    tree = ast.parse(_read(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError("%s has no %s" % (path, name))


def test_modules_are_read_only_and_support_check_mode():
    for name in MODULES:
        source = _read(_module_path(name))
        assert "supports_check_mode=True" in source, name
        assert "get_vergeos_client" not in source, name
        assert "networks.create" not in source, name
        assert "vergeio.vergeos.network:" not in source, name
        doc = yaml.safe_load(_assign(_module_path(name), "DOCUMENTATION"))
        notes = " ".join(doc.get("notes") or [])
        assert "changed=false" in notes, name
        description = " ".join(doc["description"])
        assert "does not migrate" in description.lower() or "do not migrate" in description.lower(), name


def test_docs_say_this_does_not_migrate_or_apply():
    text = _read(os.path.join(_root(), "docs", "NETWORK-TRANSLATION.md")).lower()
    assert "do not migrate" in text
    assert "do not change vergeos" in text
    assert "trunk" in text
    assert "out of scope" in text
    assert "fixture-tested" in text
    assert "verify-network-translation.yml" in text
    assert "when `layer2_type` is missing" in text
    assert "vxlan" in text
    assert "vmbr7.3001" in text
    assert "plan.warnings" in text


def test_return_docs_keep_warnings_off_the_top_level():
    for name in ("network_map", "network_guard", "proxmox_host_network"):
        block = yaml.safe_load(_assign(_module_path(name), "RETURN"))
        assert "warnings" not in block, name
    plan = yaml.safe_load(_assign(_module_path("network_map"), "RETURN"))
    notes = yaml.safe_load(_assign(_module_path("network_map"), "DOCUMENTATION"))
    warnings = plan["plan"]["contains"]["warnings"]
    text = " ".join(warnings["description"])
    assert "uplink" in text.lower()
    assert "RV(plan.warnings)" in " ".join(notes["notes"])


def test_live_probe_is_read_only():
    path = os.path.join(_root(), "tests", "live", "verify-network-translation.yml")
    text = _read(path)
    assert "vergeio.vergeos.network_info:" in text
    assert "vergeio.vergeos.network:" not in text
    assert "vergeio.vergeos.vm:" not in text
    assert "vergeio.vergeos.nic:" not in text
    assert "state: present" not in text
    # The only absent is the local diagram directory.
    assert text.count("state: absent") == 1
    assert "ansible.builtin.file:" in text
    assert "conundrum" not in text.lower()
    assert "192.168.1." not in text
    assert "tenant:" not in text
    assert "zz-netmap-proposal-not-created" in text
    lowered = text.lower()
    assert "creates nothing" in lowered


def test_proposal_keys_match_the_network_module():
    """A proposal uses network-module parameters, not raw API field names."""
    doc = yaml.safe_load(_assign(
        os.path.join(_root(), "plugins", "modules", "network.py"),
        "DOCUMENTATION"))
    options = set(doc["options"])
    for key in ("interface_network", "rate_limit", "network", "ip_address",
                "gateway", "dhcp_enabled", "dhcp_start", "dhcp_end",
                "layer2_type", "vlan_id", "network_type", "mtu", "name",
                "state"):
        assert key in options, key
    assert "interface_vnet" not in options
