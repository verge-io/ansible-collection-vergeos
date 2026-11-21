# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Gate 1 -- Proxmox NIC capture."""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils import pve_net_parse as pnp
from ansible_collections.vergeio.vergeos.plugins.module_utils.pve_net_parse import NetParseError


def one(value, key="net0"):
    return pnp.parse_nic(key, value)


# --------------------------------------------------------------- basics
def test_plain_untagged_nic():
    n = one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0")
    assert n["mac"] == "AA:BB:CC:DD:EE:01"
    assert n["model"] == "virtio"
    assert n["bridge"] == "vmbr0"
    assert n["tag"] is None
    assert n["segment"] == "vmbr0"
    assert n["connected"] is True


def test_mac_is_normalised_to_upper():
    assert one("virtio=aa:bb:cc:dd:ee:01,bridge=vmbr0")["mac"] == \
        "AA:BB:CC:DD:EE:01"


def test_every_documented_model_is_accepted():
    for model in pnp.PVE_MODELS:
        n = one("%s=AA:BB:CC:DD:EE:01,bridge=vmbr0" % model)
        assert n["model"] == model


def test_model_is_preserved_not_flattened():
    """Changing a Linux guest's NIC model renames its interface
    (ens18 -> eth0) and can strand a statically-addressed host."""
    assert one("e1000=AA:BB:CC:DD:EE:01,bridge=vmbr0")["model"] == "e1000"
    assert one("vmxnet3=AA:BB:CC:DD:EE:01,bridge=vmbr0")["model"] == "vmxnet3"


# ------------------------------------------------- the VLAN-collapse bug
def test_vlan_tag_is_captured():
    assert one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=20")["tag"] == 20


def test_different_tags_on_one_bridge_are_different_segments():
    """THE regression. Two isolated VLANs must not merge into one L2."""
    a = one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=20")
    b = one("virtio=AA:BB:CC:DD:EE:02,bridge=vmbr0,tag=30")
    assert a["segment"] != b["segment"]
    assert {a["segment"], b["segment"]} == {"vmbr0.20", "vmbr0.30"}


def test_same_bridge_and_tag_share_a_segment():
    a = one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=20")
    b = one("virtio=AA:BB:CC:DD:EE:02,bridge=vmbr0,tag=20", key="net1")
    assert a["segment"] == b["segment"] == "vmbr0.20"


def test_untagged_and_tagged_are_different_segments():
    a = one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0")
    b = one("virtio=AA:BB:CC:DD:EE:02,bridge=vmbr0,tag=20")
    assert a["segment"] == "vmbr0"
    assert b["segment"] == "vmbr0.20"


@pytest.mark.parametrize("tag", [0, 4095, 9999, -1])
def test_out_of_range_tag_is_refused(tag):
    with pytest.raises(NetParseError):
        one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=%d" % tag)


# -------------------------------------------------- the invented-bridge bug
def test_unconnected_nic_is_not_given_a_bridge():
    """A NIC with no bridge= is deliberately isolated. The old code
    defaulted it to vmbr0, attaching it to production."""
    n = one("virtio=AA:BB:CC:DD:EE:01")
    assert n["bridge"] is None
    assert n["segment"] is None
    assert n["connected"] is False
    assert n["net"] == ""


# -------------------------------------------------------------- trunks
def test_trunk_list_is_parsed_and_sorted():
    n = one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,trunks=30;10;20")
    assert n["trunks"] == [10, 20, 30]


def test_trunk_port_is_its_own_segment():
    """A trunk carries several VLANs; folding it into one would silently
    drop the rest."""
    t = one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,trunks=10;20")
    plain = one("virtio=AA:BB:CC:DD:EE:02,bridge=vmbr0")
    assert t["segment"] == "vmbr0:trunk"
    assert t["segment"] != plain["segment"]


def test_trunk_with_bad_vlan_is_refused():
    with pytest.raises(NetParseError):
        one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,trunks=10;abc")
    with pytest.raises(NetParseError):
        one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,trunks=10;4095")


def test_empty_trunks_is_not_a_trunk_port():
    assert one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,trunks=")["trunks"] == []


# ------------------------------------------------------- other options
def test_mtu_is_captured():
    assert one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,mtu=9000")["mtu"] == 9000


def test_mtu_1_means_inherit_not_one_byte():
    """virtio uses mtu=1 as the 'inherit from bridge' sentinel."""
    n = one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,mtu=1")
    assert n["mtu"] is None
    assert n["mtu_inherit"] is True


def test_rate_queues_firewall_link_down():
    n = one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,"
            "rate=12.5,queues=8,firewall=1,link_down=1")
    assert n["rate_mbyte_s"] == 12.5
    assert n["queues"] == 8
    assert n["firewall"] is True
    assert n["link_down"] is True


def test_flags_default_off():
    n = one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0")
    assert n["firewall"] is False
    assert n["link_down"] is False
    assert n["rate_mbyte_s"] is None
    assert n["queues"] is None


def test_firewall_zero_is_off():
    assert one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,firewall=0")["firewall"] \
        is False


def test_option_order_does_not_matter():
    a = one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=20,mtu=9000")
    b = one("virtio=AA:BB:CC:DD:EE:01,mtu=9000,tag=20,bridge=vmbr0")
    assert a == b


# ------------------------------------------------------ sdn / ovs names
def test_sdn_vnet_name_is_accepted_as_a_bridge():
    """SDN vnets are referenced by name and do not start with 'vmbr'."""
    n = one("virtio=AA:BB:CC:DD:EE:01,bridge=prodvnet,tag=42")
    assert n["bridge"] == "prodvnet"
    assert n["segment"] == "prodvnet.42"


# ------------------------------------------------------------ refusals
@pytest.mark.parametrize("bad", [
    "",
    "bridge=vmbr0",                                   # no model
    "virtio=NOTAMAC,bridge=vmbr0",                    # malformed MAC
    "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=abc",  # non-int tag
    "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,rate=fast",
    "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,queues=many",
    "notamodel=AA:BB:CC:DD:EE:01,bridge=vmbr0",
])
def test_malformed_definitions_are_refused(bad):
    with pytest.raises(NetParseError):
        one(bad)


def test_error_names_the_offending_nic():
    with pytest.raises(NetParseError) as exc:
        pnp.parse_config({"net0": "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0",
                          "net3": "garbage"})
    assert "net3" in str(exc.value)


# ------------------------------------------------------- whole-config
QM_CONFIG = """\
boot: order=scsi0;net0
cores: 4
description: multi-line\\nnotes with: colons and [brackets]
memory: 8192
name: web01
net0: virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=20,firewall=1
net1: virtio=AA:BB:CC:DD:EE:02,bridge=vmbr0,tag=30,mtu=9000
net10: e1000=AA:BB:CC:DD:EE:0A,bridge=vmbr1
net2: virtio=AA:BB:CC:DD:EE:03,bridge=vmbr0,trunks=10;20;30
scsi0: local-lvm:vm-101-disk-0,size=32G
smbios1: uuid=deadbeef-0000-0000-0000-000000000000
"""


def test_extracts_only_net_lines():
    nets = pnp.extract_net_lines(QM_CONFIG)
    assert set(nets) == {"net0", "net1", "net2", "net10"}


def test_nics_are_ordered_numerically_not_lexically():
    keys = [n["key"] for n in pnp.parse_config(QM_CONFIG)]
    assert keys == ["net0", "net1", "net2", "net10"]


def test_full_config_yields_four_distinct_segments():
    segs = [n["segment"] for n in pnp.parse_config(QM_CONFIG)]
    assert segs == ["vmbr0.20", "vmbr0.30", "vmbr0:trunk", "vmbr1"]
    assert len(set(segs)) == 4, "the old parser collapsed all of these"


def test_hostile_description_does_not_break_parsing():
    """Proxmox descriptions can contain colons and brackets; the parser
    must not route config text through a YAML loader."""
    assert len(pnp.parse_config(QM_CONFIG)) == 4


def test_config_with_no_nics_is_empty_not_an_error():
    assert pnp.parse_config("name: x\nmemory: 512\n") == []


def test_negative_rate_is_refused():
    with pytest.raises(NetParseError):
        one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,rate=-1")


def test_access_vlan_plus_trunk_is_recorded():
    n = one("virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=20,trunks=30;40")
    assert n["access_plus_trunk"] is True
    assert n["segment"] == "vmbr0:trunk"
