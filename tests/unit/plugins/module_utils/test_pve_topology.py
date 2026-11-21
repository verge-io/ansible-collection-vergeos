# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Gate 2 -- host topology. Fixture-tested; no Proxmox node is available."""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils import pve_net_parse as pnp
from ansible_collections.vergeio.vergeos.plugins.module_utils import pve_topology as top

INTERFACES = """\
# managed by ifupdown2
auto lo
iface lo inet loopback

iface eno1 inet manual
iface eno2 inet manual

auto bond0
iface bond0 inet manual
        bond-slaves eno1 eno2
        bond-miimon 100
        bond_mode 802.3ad
        mtu 9000

auto vmbr0
iface vmbr0 inet static
        address 192.0.2.10/24
        gateway 192.0.2.1
        bridge-ports bond0
        bridge-stp off
        bridge-fd 0
        bridge-vlan-aware yes
        bridge-vids 2-4094
        mtu 9000

auto vmbr0.20
iface vmbr0.20 inet static
        address 10.0.20.10/24

auto vmbr1
iface vmbr1 inet manual
        bridge-ports eno3
        bridge-stp off
        bridge-fd 0
"""

ZONES = """\
vlan: prodzone
    bridge vmbr0
    ipam pve
    mtu 9000

simple: dmzzone
    ipam pve
"""

VNETS = """\
vnet: webvnet
    zone prodzone
    tag 20
    alias Production Web
    vlanaware 1

vnet: dmzvnet
    zone dmzzone
    alias DMZ
"""

SUBNETS = """\
subnet: prodzone-10.0.20.0-24
    vnet webvnet
    gateway 10.0.20.1
    snat 1
    dhcp-range start-address=10.0.20.100,end-address=10.0.20.200

subnet: dmzzone-172.16.5.0-24
    vnet dmzvnet
    gateway 172.16.5.1
"""

BUNDLE = {"interfaces": INTERFACES,
          "sdn": {"zones.cfg": ZONES, "vnets.cfg": VNETS,
                  "subnets.cfg": SUBNETS}}


@pytest.fixture
def result():
    return top.analyse(BUNDLE)


# ------------------------------------------------------------- ifupdown
def test_bridges_are_found(result):
    assert set(result["bridges"]) == {"vmbr0", "vmbr1"}


def test_loopback_and_physicals_are_not_bridges(result):
    assert "lo" not in result["bridges"]
    assert "eno1" not in result["bridges"]


def test_vlan_awareness_is_captured(result):
    assert result["bridges"]["vmbr0"]["vlan_aware"] is True
    assert result["bridges"]["vmbr1"]["vlan_aware"] is False


def test_underscore_and_dash_options_are_equivalent(result):
    """ifupdown2 accepts bond_mode and bond-mode interchangeably."""
    assert result["bonds"]["bond0"]["mode"] == "802.3ad"


def test_uplinks_flatten_through_the_bond(result):
    """vmbr0 -> bond0 -> eno1+eno2. A bridge whose 'uplink' is a bond
    tells you nothing about physical connectivity until flattened."""
    assert result["segments"]["vmbr0"]["uplinks"] == ["eno1", "eno2"]


def test_uplinks_of_a_plain_bridge(result):
    assert result["segments"]["vmbr1"]["uplinks"] == ["eno3"]


def test_mtu_is_captured(result):
    assert result["segments"]["vmbr0"]["mtu"] == 9000


def test_host_address_is_not_reported_as_the_segment_subnet(result):
    """On a VLAN-aware bridge the host's own address and the guest subnet
    are routinely different networks. Conflating them would synthesise
    the wrong subnet."""
    seg = result["segments"]["vmbr0"]
    assert seg["host_addresses"] == ["192.0.2.10/24"]
    assert "cidr" not in seg


def test_bridge_without_l3_is_name_only_confidence(result):
    assert result["segments"]["vmbr1"]["confidence"] == "name-only"


def test_comments_are_stripped():
    r = top.analyse({"interfaces": "auto vmbr9\niface vmbr9 inet manual\n"
                                   "    bridge-ports eno9 # the uplink\n"})
    assert r["bridges"]["vmbr9"]["ports"] == ["eno9"]


def test_bridge_ports_none_is_not_a_port():
    r = top.analyse({"interfaces": "auto vmbr9\niface vmbr9 inet manual\n"
                                   "    bridge-ports none\n"})
    assert r["bridges"]["vmbr9"]["ports"] == []


def test_sourced_files_are_warned_about():
    """A `source` line means bridges may live in files we were not given.
    Silence here would later look like an unmapped segment."""
    r = top.analyse({"interfaces": "source /etc/network/interfaces.d/*\n"})
    assert r["warnings"]
    assert "interfaces.d" in r["warnings"][0]


def test_no_warning_when_nothing_is_sourced(result):
    assert "warnings" not in result


# ------------------------------------------------------------------ ovs
OVS = """\
auto vmbr2
allow-ovs vmbr2
iface vmbr2 inet manual
        ovs_type OVSBridge
        ovs_ports bond1 vlan30

allow-vmbr2 vlan30
iface vlan30 inet static
        address 10.0.30.5/24
        ovs_type OVSIntPort
        ovs_bridge vmbr2
        ovs_options tag=30
"""


def test_ovs_bridge_is_recognised():
    r = top.analyse({"interfaces": OVS})
    assert r["bridges"]["vmbr2"]["kind"] == "ovs-bridge"
    assert "bond1" in r["bridges"]["vmbr2"]["ports"]


def test_ovs_access_port_becomes_a_tagged_segment():
    r = top.analyse({"interfaces": OVS})
    assert r["segments"]["vmbr2.30"]["tag"] == 30
    assert r["segments"]["vmbr2.30"]["host_addresses"] == ["10.0.30.5/24"]


# ------------------------------------------------------------------ SDN
def test_sdn_vnet_resolves_to_its_zone_bridge(result):
    seg = result["segments"]["webvnet"]
    assert seg["bridge"] == "vmbr0"
    assert seg["sdn_zone"] == "prodzone"
    assert seg["tag"] == 20


def test_sdn_subnet_gives_cidr_and_gateway(result):
    seg = result["segments"]["webvnet"]
    assert seg["cidr"] == "10.0.20.0/24"
    assert seg["gateway"] == "10.0.20.1"
    assert seg["snat"] is True


def test_sdn_dhcp_range_is_parsed(result):
    assert result["segments"]["webvnet"]["dhcp"] == {
        "start": "10.0.20.100", "end": "10.0.20.200"}


def test_sdn_is_authoritative_not_inferred(result):
    """SDN states CIDR/gateway/DHCP outright, so synthesis from it is not
    a guess -- that distinction is what `confidence` exists to carry."""
    assert result["segments"]["webvnet"]["confidence"] == "authoritative"
    assert result["segments"]["webvnet"]["source"] == "sdn"


def test_sdn_vnet_is_also_reachable_by_bridge_dot_tag(result):
    """A guest may reference the vnet by name OR sit on vmbr0 tag 20; both
    must resolve to the same facts."""
    assert result["segments"]["vmbr0.20"]["cidr"] == "10.0.20.0/24"
    assert result["segments"]["vmbr0.20"]["sdn_vnet"] == "webvnet"


def test_sdn_overrides_the_weaker_ifupdown_view(result):
    """vmbr0.20 exists in /etc/network/interfaces with only the host's
    own address; SDN knows the real guest subnet and must win."""
    assert result["segments"]["vmbr0.20"]["gateway"] == "10.0.20.1"


def test_simple_zone_vnet_has_no_bridge_or_tag(result):
    seg = result["segments"]["dmzvnet"]
    assert seg.get("bridge") is None
    assert seg.get("tag") is None
    assert seg["cidr"] == "172.16.5.0/24"


def test_alias_is_carried(result):
    assert result["segments"]["webvnet"]["alias"] == "Production Web"


def test_subnet_without_dhcp_reports_none(result):
    assert result["segments"]["dmzvnet"].get("dhcp") is None


def test_missing_sdn_is_not_an_error():
    r = top.analyse({"interfaces": INTERFACES})
    assert r["sdn"]["vnets"] == {}
    assert "vmbr0" in r["segments"]


def test_malformed_subnet_id_yields_no_cidr():
    r = top.analyse({"interfaces": "", "sdn": {
        "vnets.cfg": "vnet: v1\n\tzone z1\n",
        "subnets.cfg": "subnet: nonsense\n\tvnet v1\n\tgateway 1.2.3.4\n"}})
    assert r["segments"]["v1"].get("cidr") is None
    assert r["segments"]["v1"]["confidence"] == "sdn"


# ----------------------------------------------- the join with Gate 1
def test_segment_keys_match_the_nic_parser():
    """The whole point: a NIC's segment id indexes straight into topology
    facts. If these two ever disagree, every mapping silently misses."""
    nics = pnp.parse_config(
        "net0: virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,tag=20\n"
        "net1: virtio=AA:BB:CC:DD:EE:02,bridge=vmbr1\n"
        "net2: virtio=AA:BB:CC:DD:EE:03,bridge=webvnet\n")
    segs = top.analyse(BUNDLE)["segments"]
    for nic in nics:
        assert nic["segment"] in segs, nic["segment"]


def test_unknown_bridge_is_absent_rather_than_invented(result):
    assert "vmbr99" not in result["segments"]
