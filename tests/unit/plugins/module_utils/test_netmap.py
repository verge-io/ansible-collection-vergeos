# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Gate 3 -- mapping policy. Plans are proposals. Nothing is created."""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils import netmap
from ansible_collections.vergeio.vergeos.plugins.module_utils.netmap import PolicyError
from ansible_collections.vergeio.vergeos.plugins.module_utils.netmap_resolve import (
    VNET_TYPES,
)


def nic(segment="vmbr0.20", tag=20, bridge="vmbr0", mac="AA:BB:CC:DD:EE:01",
        key="net0", connected=True, trunks=None, model="virtio",
        rate_mbyte_s=None):
    row = {"key": key, "mac": mac, "model": model, "bridge": bridge,
           "tag": tag, "trunks": trunks or [], "segment": segment,
           "connected": connected}
    if rate_mbyte_s is not None:
        row["rate_mbyte_s"] = rate_mbyte_s
    return row


AUTHORITATIVE = {
    "vmbr0.20": {"segment": "vmbr0.20", "bridge": "vmbr0", "tag": 20,
                 "alias": "Production Web", "sdn_vnet": "webvnet",
                 "cidr": "10.0.20.0/24", "gateway": "10.0.20.1",
                 "dhcp": {"start": "10.0.20.100", "end": "10.0.20.200"},
                 "source": "sdn", "confidence": "authoritative"},
}

NAME_ONLY = {
    "vmbr1": {"segment": "vmbr1", "bridge": "vmbr1", "uplinks": ["eno3"],
              "source": "interfaces", "confidence": "name-only"},
}

INVENTORY = [
    {"name": "prod-web", "vlan_id": 20, "type": "vlan"},
    {"name": "dmz", "vlan_id": 30, "type": "vlan"},
    {"name": "vmbr1", "type": "internal"},
]


def run(nics, segments=None, inventory=None, policy=None, vm="web01"):
    return netmap.plan({vm: nics}, segments or {}, inventory or [], policy)


def only(result):
    assert len(result["decisions"]) == 1
    return result["decisions"][0]


# ------------------------------------------------------------ the choice
def test_default_order_does_not_create_anything():
    """Creating networks must be opt-in: the default chain omits it."""
    assert "synthesize" not in netmap.DEFAULT_POLICY["order"]


def test_strict_table_only_policy():
    p = {"order": ["explicit"], "explicit": {"vmbr0.20": "prod-web"}}
    d = only(run([nic()], AUTHORITATIVE, INVENTORY, p))
    assert (d["action"], d["target"], d["resolver"]) == \
        ("map", "prod-web", "explicit")


def test_strict_table_refuses_anything_not_in_it():
    p = {"order": ["explicit"], "explicit": {"vmbr9": "x"}}
    d = only(run([nic()], AUTHORITATIVE, INVENTORY, p))
    assert d["action"] == "refuse"


def test_vlan_match_reads_the_api_layer2_id():
    """network_info returns layer2_type and layer2_id, not vlan_id."""
    inventory = [{"name": "prod-web", "layer2_type": "vlan", "layer2_id": 20,
                  "type": "external"}]
    d = only(run([nic()], AUTHORITATIVE, inventory, {"order": ["vlan"]}))
    assert (d["action"], d["target"]) == ("map", "prod-web")


def test_vlan_match_accepts_a_string_layer2_id():
    inventory = [{"name": "prod-web", "layer2_type": "VLAN", "layer2_id": "20"}]
    d = only(run([nic()], {}, inventory, {"order": ["vlan"]}))
    assert (d["action"], d["target"]) == ("map", "prod-web")


def test_vlan_layer2_type_prefers_an_explicit_vlan_id():
    inventory = [{"name": "prod-web", "layer2_type": "vlan",
                  "vlan_id": 20, "layer2_id": 99}]
    d = only(run([nic()], {}, inventory, {"order": ["vlan"]}))
    assert d["target"] == "prod-web"
    other = only(run([nic(segment="vmbr0.99", tag=99)], {}, inventory,
                     {"order": ["vlan"]}))
    assert other["action"] == "refuse"


@pytest.mark.parametrize("key", ["vlan_id", "vlan", "vlanid"])
def test_explicit_vlan_field_matches_when_layer2_type_is_missing(key):
    """A missing layer2_type leaves layer2_id unused. vlan_id still matches."""
    inventory = [{"name": "prod-web", key: 20, "layer2_id": 99}]
    d = only(run([nic()], {}, inventory, {"order": ["vlan"]}))
    assert (d["action"], d["target"]) == ("map", "prod-web")


def test_bare_layer2_id_without_layer2_type_is_not_a_vlan_match():
    """layer2_id alone may be a VXLAN id. It is not treated as a VLAN."""
    inventory = [{"name": "prod-web", "layer2_id": 20, "type": "external"}]
    d = only(run([nic()], AUTHORITATIVE, inventory, {"order": ["vlan"]}))
    assert d["action"] == "refuse"
    assert d["target"] is None
    assert "matches an existing vnet" not in d["why"]


def test_a_boolean_vlan_id_is_not_vlan_one():
    inventory = [{"name": "DMZ", "vlan_id": True}]
    d = only(run([nic(segment="vmbr0.1", tag=1, bridge="vmbr0")],
                 {}, inventory, {"order": ["vlan"]}))
    assert d["action"] == "refuse"


def test_non_vlan_layer2_type_stays_unmatched_even_with_vlan_id():
    inventory = [{"name": "flat", "layer2_type": "none",
                  "layer2_id": 20, "vlan_id": 20}]
    d = only(run([nic()], {}, inventory, {"order": ["vlan"]}))
    assert d["action"] == "refuse"
    assert d["target"] is None


def test_low_vlan_tags_do_not_map_onto_vxlan_core_and_dmz():
    """Core and DMZ style networks carry VXLAN ids 1 and 2.

    A guest on VLAN 1, 2, or 13 must stay off those networks. A real
    VLAN 20 still matches, and a VXLAN network that happens to use id
    20 does not make the match ambiguous.
    """
    inventory = [
        {"name": "DMZ", "type": "external", "layer2_type": "vxlan",
         "layer2_id": 1},
        {"name": "Core", "type": "internal", "layer2_type": "vxlan",
         "layer2_id": 2},
        {"name": "internal", "type": "internal", "layer2_type": "vxlan",
         "layer2_id": 13},
        {"name": "also-twenty", "type": "internal", "layer2_type": "VXLAN",
         "layer2_id": 20},
        {"name": "prod-web", "type": "external", "layer2_type": "vlan",
         "layer2_id": 20},
    ]
    for tag in (1, 2, 13, 3000):
        d = only(run([nic(segment="vmbr0.%d" % tag, tag=tag, bridge="vmbr0")],
                     {}, inventory))
        assert d["action"] == "refuse", (tag, d)
        assert d["target"] is None
        assert "matches an existing vnet" not in d["why"]
    mapped = only(run([nic()], {}, inventory))
    assert (mapped["action"], mapped["target"], mapped["resolver"]) == \
        ("map", "prod-web", "vlan")


def test_map_to_existing_by_vlan():
    p = {"order": ["vlan"]}
    d = only(run([nic()], AUTHORITATIVE, INVENTORY, p))
    assert (d["action"], d["target"]) == ("map", "prod-web")


def test_map_to_existing_by_alias():
    inv = INVENTORY + [{"name": "ProductionWeb", "type": "vlan"}]
    p = {"order": ["alias"]}
    d = only(run([nic()], AUTHORITATIVE, inv, p))
    assert d["target"] == "ProductionWeb"


def test_map_to_existing_by_name():
    p = {"order": ["name"]}
    d = only(run([nic(segment="vmbr1", tag=None, bridge="vmbr1")],
                 NAME_ONLY, INVENTORY, p))
    assert (d["action"], d["target"]) == ("map", "vmbr1")


def test_synthesis_only_policy_creates():
    p = {"order": ["synthesize"]}
    r = run([nic()], AUTHORITATIVE, [], p)
    d = only(r)
    assert d["action"] == "create"
    assert r["creates"][0]["name"] == "vmbr0.20"


def test_full_chain_prefers_existing_over_creating():
    """With both available, mapping wins -- creation is the last resort."""
    p = {"order": ["explicit", "alias", "vlan", "name", "synthesize"]}
    r = run([nic()], AUTHORITATIVE, INVENTORY, p)
    assert only(r)["action"] == "map"
    assert r["creates"] == []


def test_chain_falls_through_to_synthesis_when_nothing_exists():
    p = {"order": ["explicit", "alias", "vlan", "name", "synthesize"]}
    r = run([nic()], AUTHORITATIVE, [], p)
    assert only(r)["action"] == "create"


def test_explicit_overrides_a_perfectly_good_vlan_match():
    """The operator's table is authoritative even when inference agrees
    on something else."""
    p = {"order": ["explicit", "vlan"], "explicit": {"vmbr0.20": "dmz"}}
    assert only(run([nic()], AUTHORITATIVE, INVENTORY, p))["target"] == "dmz"


def test_reordering_the_chain_changes_the_winner():
    inv = [{"name": "webvnet", "vlan_id": 99}, {"name": "prod-web",
                                                "vlan_id": 20}]
    vlan_first = only(run([nic()], AUTHORITATIVE, inv,
                          {"order": ["vlan", "name"]}))
    name_first = only(run([nic()], AUTHORITATIVE, inv,
                          {"order": ["name", "vlan"]}))
    assert vlan_first["target"] == "prod-web"
    assert name_first["target"] == "webvnet"


def test_explicit_table_may_key_on_the_bridge():
    p = {"order": ["explicit"], "explicit": {"vmbr0": "flat-net"}}
    assert only(run([nic()], AUTHORITATIVE, INVENTORY, p))["target"] == \
        "flat-net"


def test_segment_key_beats_bridge_key_in_the_table():
    p = {"order": ["explicit"],
         "explicit": {"vmbr0": "flat-net", "vmbr0.20": "prod-web"}}
    assert only(run([nic()], AUTHORITATIVE, INVENTORY, p))["target"] == \
        "prod-web"


# ------------------------------------------------------ never guess
def test_two_vnets_on_one_vlan_is_refused_not_guessed():
    inv = [{"name": "a", "vlan_id": 20}, {"name": "b", "vlan_id": 20}]
    d = only(run([nic()], AUTHORITATIVE, inv, {"order": ["vlan"]}))
    assert d["action"] == "refuse"
    assert "a" in d["why"] and "b" in d["why"]


def test_ambiguity_stops_the_chain_rather_than_falling_through():
    """Falling through to synthesis after an ambiguous match would create
    a third network next to the two that already collided."""
    inv = [{"name": "a", "vlan_id": 20}, {"name": "b", "vlan_id": 20}]
    r = run([nic()], AUTHORITATIVE, inv, {"order": ["vlan", "synthesize"]})
    assert only(r)["action"] == "refuse"
    assert r["creates"] == []


def test_ambiguous_names_are_refused():
    inv = [{"name": "vmbr1", "type": "internal"},
           {"name": "VMBR1", "type": "internal"}]
    d = only(run([nic(segment="vmbr1", tag=None, bridge="vmbr1")],
                 NAME_ONLY, inv, {"order": ["name"]}))
    assert d["action"] == "refuse"


def test_refusal_makes_the_whole_plan_not_ok():
    r = run([nic()], AUTHORITATIVE, [], {"order": ["explicit"]})
    assert r["ok"] is False
    assert len(r["refusals"]) == 1


def test_clean_plan_is_ok():
    r = run([nic()], AUTHORITATIVE, INVENTORY, {"order": ["vlan"]})
    assert r["ok"] is True


# --------------------------------------------- synthesis is gated
def test_synthesis_refuses_a_name_only_segment():
    """A bridge known only by name has no subnet; inventing one would put
    guests on a network that does not route."""
    p = {"order": ["synthesize"]}
    d = only(run([nic(segment="vmbr1", tag=None, bridge="vmbr1")],
                 NAME_ONLY, [], p))
    assert d["action"] == "refuse"
    blocked = [t for t in d["tried"] if t["result"] == "blocked"]
    assert blocked and "confidence" in blocked[0]["why"]


def test_lowering_the_confidence_floor_permits_it():
    p = {"order": ["synthesize"],
         "synthesize": {"require_confidence": "any"}}
    r = run([nic(segment="vmbr1", tag=None, bridge="vmbr1")], NAME_ONLY, [], p)
    assert only(r)["action"] == "create"
    assert r["creates"][0]["network_type"] == "internal"
    assert "layer2_type" not in r["creates"][0], "untagged means no VLAN"


def test_synthesis_with_no_facts_at_all_is_blocked():
    p = {"order": ["synthesize"], "synthesize": {"require_confidence": "any"}}
    d = only(run([nic(segment="vmbr7", bridge="vmbr7", tag=None)], {}, [], p))
    assert d["action"] == "refuse"


def test_synthesised_spec_carries_subnet_address_and_dhcp():
    r = run([nic()], AUTHORITATIVE, [], {"order": ["synthesize"]})
    spec = r["creates"][0]
    assert spec["network"] == "10.0.20.0/24"
    # The source subnet's gateway is the ROUTER'S address on that segment,
    # and in VergeOS the vnet is that router -- so it maps to ip_address.
    assert spec["ip_address"] == "10.0.20.1"
    assert spec["vlan_id"] == 20
    assert spec["layer2_type"] == "vlan"
    assert spec["network_type"] == "internal"
    assert spec["dhcp_enabled"] is True
    assert spec["dhcp_start"] == "10.0.20.100"


def test_upstream_gateway_is_never_inferred():
    """A VergeOS vnet's `gateway` is its UPSTREAM router. A Proxmox subnet
    definition does not describe one, and the API rejects a gateway sent
    without the vnet's own address: 'Gateway is outside of network'."""
    r = run([nic()], AUTHORITATIVE, [], {"order": ["synthesize"]})
    assert "gateway" not in r["creates"][0]


def test_upstream_gateway_is_emitted_when_the_operator_supplies_one():
    p = {"order": ["synthesize"],
         "synthesize": {"upstream_gateway": "10.0.20.254"}}
    assert run([nic()], AUTHORITATIVE, [], p)["creates"][0]["gateway"] == \
        "10.0.20.254"


def test_address_is_never_emitted_without_its_network():
    segs = {"vmbr0.20": dict(AUTHORITATIVE["vmbr0.20"], cidr=None)}
    r = run([nic()], segs, [], {"order": ["synthesize"]})
    assert "ip_address" not in r["creates"][0]
    assert "gateway" not in r["creates"][0]


def test_address_outside_the_subnet_is_blocked():
    segs = {"vmbr0.20": dict(AUTHORITATIVE["vmbr0.20"],
                             gateway="198.51.100.1")}
    d = only(run([nic()], segs, [], {"order": ["synthesize"]}))
    assert d["action"] == "refuse"


def test_upstream_gateway_outside_the_subnet_is_blocked():
    p = {"order": ["synthesize"],
         "synthesize": {"upstream_gateway": "198.51.100.1"}}
    assert only(run([nic()], AUTHORITATIVE, [], p))["action"] == "refuse"


def test_dhcp_range_outside_the_subnet_is_blocked():
    segs = {"vmbr0.20": dict(AUTHORITATIVE["vmbr0.20"],
                             dhcp={"start": "10.9.9.10", "end": "10.9.9.20"})}
    assert only(run([nic()], segs, [], {"order": ["synthesize"]}))["action"] \
        == "refuse"


def test_dhcp_can_be_forced_off():
    p = {"order": ["synthesize"], "synthesize": {"dhcp": "off"}}
    r = run([nic()], AUTHORITATIVE, [], p)
    assert r["creates"][0]["dhcp_enabled"] is False


def test_name_template_is_honoured():
    p = {"order": ["synthesize"],
         "synthesize": {"name_template": "dr-{alias}-{vlan}"}}
    r = run([nic()], AUTHORITATIVE, [], p)
    assert r["creates"][0]["name"] == "dr-Production Web-20"


def test_duplicate_creates_are_collapsed_once():
    nics = [nic(key="net0"), nic(key="net1", mac="AA:BB:CC:DD:EE:02")]
    r = run(nics, AUTHORITATIVE, [], {"order": ["synthesize"]})
    assert len(r["creates"]) == 1
    assert r["summary"]["vnets_to_create"] == 1


def test_conflicting_creates_for_one_name_are_refused():
    segs = dict(AUTHORITATIVE)
    segs["vmbr2.20"] = dict(AUTHORITATIVE["vmbr0.20"], segment="vmbr2.20",
                            cidr="10.9.9.0/24", gateway="10.9.9.1")
    p = {"order": ["synthesize"], "synthesize": {"name_template": "net{vlan}"}}
    r = netmap.plan({"a": [nic()], "b": [nic(segment="vmbr2.20",
                                             bridge="vmbr2")]},
                    segs, [], p)
    assert any(d["action"] == "refuse" for d in r["decisions"])


# ------------------------------------------------------------- trunks
def test_trunk_is_not_matched_by_vlan():
    n = nic(segment="vmbr0:trunk", tag=None, trunks=[10, 20, 30])
    segs = {"vmbr0:trunk": {"confidence": "authoritative", "source": "sdn"}}
    d = only(run([n], segs, INVENTORY, {"order": ["vlan"]}))
    assert d["action"] == "refuse"


def test_trunk_synthesis_is_blocked_by_default():
    n = nic(segment="vmbr0:trunk", tag=None, trunks=[10, 20])
    segs = {"vmbr0:trunk": dict(AUTHORITATIVE["vmbr0.20"],
                                segment="vmbr0:trunk")}
    d = only(run([n], segs, [], {"order": ["synthesize"]}))
    assert d["action"] == "refuse"
    assert d["resolver"] == "trunk"
    assert "trunk" in d["why"]
    assert "out of scope" in d["why"]


def test_trunk_is_not_mapped_onto_the_bridge_name():
    """A name match on the bridge would collapse the trunk into one vnet."""
    n = nic(segment="vmbr0:trunk", bridge="vmbr0", tag=None, trunks=[10, 20])
    inventory = INVENTORY + [{"name": "vmbr0", "type": "internal"}]
    d = only(run([n], {}, inventory, {"order": ["name", "synthesize"]}))
    assert d["action"] == "refuse"
    assert d["target"] is None


def test_tagged_access_ports_are_not_mapped_onto_the_bridge_name():
    """vmbr7.3001, vmbr7.3002 and untagged vmbr7 are three layer-2 domains.

    Default policy, one vnet named after the bridge. The tagged ports
    must refuse. The untagged segment may still match by name.
    """
    inventory = [{"name": "vmbr7", "type": "internal"}]
    nics = [
        nic(key="net0", mac="AA:BB:CC:DD:EE:01",
            segment="vmbr7.3001", tag=3001, bridge="vmbr7"),
        nic(key="net1", mac="AA:BB:CC:DD:EE:02",
            segment="vmbr7.3002", tag=3002, bridge="vmbr7"),
        nic(key="net2", mac="AA:BB:CC:DD:EE:03",
            segment="vmbr7", tag=None, bridge="vmbr7"),
    ]
    by_segment = {d["segment"]: d for d in run(nics, {}, inventory)["decisions"]}
    for segment in ("vmbr7.3001", "vmbr7.3002"):
        decided = by_segment[segment]
        assert decided["action"] == "refuse", decided
        assert decided["target"] is None
        assert all(step["result"] == "abstained" for step in decided["tried"])
        assert "bridge name" not in decided["why"]
    untagged = by_segment["vmbr7"]
    assert (untagged["action"], untagged["target"], untagged["resolver"]) == \
        ("map", "vmbr7", "name")
    assert "segment name" in untagged["why"]


def test_tagged_access_port_matches_its_own_segment_name():
    inventory = [
        {"name": "vmbr7", "type": "internal"},
        {"name": "vmbr7.3001", "type": "internal"},
    ]
    d = only(run([nic(segment="vmbr7.3001", tag=3001, bridge="vmbr7")],
                 {}, inventory, {"order": ["name"]}))
    assert (d["action"], d["target"]) == ("map", "vmbr7.3001")
    assert d["why"].startswith("segment name")


def test_tagged_segment_form_does_not_fall_back_to_the_bridge():
    inventory = [{"name": "vmbr7", "type": "internal"}]
    d = only(run([nic(segment="vmbr7.3001", tag=None, bridge="vmbr7")],
                 {}, inventory, {"order": ["name"]}))
    assert d["action"] == "refuse"
    assert d["target"] is None


def test_segment_facts_tag_blocks_the_bridge_name_fallback():
    segs = {"webvnet": {"tag": 3001}}
    inventory = [{"name": "vmbr7", "type": "internal"}]
    d = only(run([nic(segment="webvnet", tag=None, bridge="vmbr7")],
                 segs, inventory, {"order": ["name"]}))
    assert d["action"] == "refuse"
    assert d["target"] is None


def test_tagged_access_port_can_match_an_sdn_vnet_name():
    segs = {"vmbr7.3001": {"tag": 3001, "sdn_vnet": "webvnet"}}
    inventory = [{"name": "vmbr7"}, {"name": "webvnet"}]
    d = only(run([nic(segment="vmbr7.3001", tag=3001, bridge="vmbr7")],
                 segs, inventory, {"order": ["name"]}))
    assert (d["action"], d["target"]) == ("map", "webvnet")
    assert "SDN vnet" in d["why"]


def test_untagged_nic_still_falls_back_to_the_bridge_name():
    inventory = [{"name": "vmbr1", "type": "internal"}]
    d = only(run([nic(segment="other", tag=None, bridge="vmbr1")],
                 {}, inventory, {"order": ["name"]}))
    assert (d["action"], d["target"]) == ("map", "vmbr1")
    assert "bridge name" in d["why"]


def test_allow_trunk_does_not_translate_a_trunk():
    n = nic(segment="vmbr0:trunk", tag=None, trunks=[10, 20])
    p = {"order": ["synthesize"], "synthesize": {"allow_trunk": True}}
    d = only(run([n], AUTHORITATIVE, [], p))
    assert d["action"] == "refuse"
    assert "trunk" in d["why"]


# ------------------------------------------------- unconnected NICs
def test_unconnected_nic_is_detached_under_every_policy():
    n = nic(segment=None, bridge=None, tag=None, connected=False)
    for order in (["explicit"], ["vlan", "name"],
                  ["explicit", "alias", "vlan", "name", "synthesize"]):
        d = only(run([n], AUTHORITATIVE, INVENTORY, {"order": order}))
        assert d["action"] == "detach", order


def test_unconnected_nic_does_not_make_the_plan_fail():
    n = nic(segment=None, bridge=None, tag=None, connected=False)
    assert run([n], {}, [], {"order": ["explicit"]})["ok"] is True


# --------------------------------------------------------- fallbacks
def test_on_unmapped_detach():
    p = {"order": ["explicit"], "on_unmapped": "detach"}
    d = only(run([nic()], AUTHORITATIVE, INVENTORY, p))
    assert d["action"] == "detach"


def test_on_unmapped_bubble():
    p = {"order": ["explicit"], "on_unmapped": "bubble",
         "bubble_network": "dr-bubble"}
    d = only(run([nic()], AUTHORITATIVE, INVENTORY, p))
    assert (d["action"], d["target"]) == ("bubble", "dr-bubble")


def test_bubble_without_a_bubble_network_is_a_policy_error():
    with pytest.raises(PolicyError):
        run([nic()], {}, [], {"on_unmapped": "bubble"})


# ----------------------------------------------------- policy validation
def test_unknown_resolver_is_refused():
    with pytest.raises(PolicyError) as exc:
        run([nic()], {}, [], {"order": ["explicit", "telepathy"]})
    assert "telepathy" in str(exc.value)


def test_empty_order_is_refused():
    with pytest.raises(PolicyError):
        run([nic()], {}, [], {"order": []})


def test_bad_confidence_floor_is_refused():
    with pytest.raises(PolicyError):
        run([nic()], {}, [], {"synthesize": {"require_confidence": "vibes"}})


def test_bad_on_unmapped_is_refused():
    with pytest.raises(PolicyError):
        run([nic()], {}, [], {"on_unmapped": "improvise"})


def test_policy_merge_is_deep_not_replacing():
    p = netmap.merge_policy({"synthesize": {"dhcp": "off"}})
    assert p["synthesize"]["dhcp"] == "off"
    assert p["synthesize"]["require_confidence"] == "authoritative"


def test_defaults_are_not_mutated_between_calls():
    netmap.merge_policy({"synthesize": {"dhcp": "off"}})
    assert netmap.DEFAULT_POLICY["synthesize"]["dhcp"] == "inherit"


# ------------------------------------------------------------ audit
def test_every_decision_records_what_was_tried():
    p = {"order": ["explicit", "alias", "vlan"]}
    d = only(run([nic()], AUTHORITATIVE, INVENTORY, p))
    assert [t["resolver"] for t in d["tried"]] == ["explicit", "alias", "vlan"]
    assert d["tried"][-1]["result"] == "matched"


def test_decision_explains_itself():
    d = only(run([nic()], AUTHORITATIVE, INVENTORY, {"order": ["vlan"]}))
    assert "VLAN 20" in d["why"]


def test_mac_and_model_are_carried_for_the_apply_step():
    d = only(run([nic()], AUTHORITATIVE, INVENTORY, {"order": ["vlan"]}))
    assert d["mac"] == "AA:BB:CC:DD:EE:01"
    assert d["model"] == "virtio"


def test_summary_counts_actions():
    nics = [nic(key="net0"),
            nic(key="net1", segment=None, bridge=None, tag=None,
                connected=False)]
    r = run(nics, AUTHORITATIVE, INVENTORY, {"order": ["vlan"]})
    assert r["summary"]["nics"] == 2
    assert r["summary"]["by_action"] == {"map": 1, "detach": 1}


def test_multiple_vms_are_planned_together():
    r = netmap.plan({"a": [nic()], "b": [nic()]}, AUTHORITATIVE, INVENTORY,
                    {"order": ["vlan"]})
    assert {d["vm"] for d in r["decisions"]} == {"a", "b"}


# ------------------------------------------------------------ cli shape


# ------------------------------------------- shapes the server accepts
def test_vlan_is_layer2_not_a_network_type():
    """VergeOS vnet types are internal|external|dmz. The collection
    dropped 'vlan' as a type in 2.1.0 because the server never had one;
    tagging is layer2_type + layer2_id, orthogonal to the vnet's role."""
    r = run([nic()], AUTHORITATIVE, [], {"order": ["synthesize"]})
    spec = r["creates"][0]
    assert spec["network_type"] in VNET_TYPES
    assert spec["network_type"] != "vlan"


def test_invalid_network_type_is_blocked_not_sent():
    p = {"order": ["synthesize"], "synthesize": {"network_type": "vlan"}}
    d = only(run([nic()], AUTHORITATIVE, [], p))
    assert d["action"] == "refuse"


@pytest.mark.parametrize("ntype", ["internal", "external", "dmz"])
def test_every_real_vnet_type_is_accepted(ntype):
    p = {"order": ["synthesize"], "synthesize": {"network_type": ntype}}
    r = run([nic()], AUTHORITATIVE, [], p)
    assert r["creates"][0]["network_type"] == ntype


def test_dhcp_range_uses_the_modules_spelling():
    """The module takes dhcp_end and translates to the API's dhcp_stop.
    Sending dhcp_end at the API directly is silently ignored, leaving a
    DHCP range with no upper bound."""
    r = run([nic()], AUTHORITATIVE, [], {"order": ["synthesize"]})
    assert "dhcp_end" in r["creates"][0]
    assert "dhcp_stop" not in r["creates"][0]


def test_mtu_is_carried_when_known():
    segs = {"vmbr0.20": dict(AUTHORITATIVE["vmbr0.20"], mtu=9000)}
    r = run([nic()], segs, [], {"order": ["synthesize"]})
    assert r["creates"][0]["mtu"] == 9000


def test_spec_only_contains_keys_the_module_accepts():
    """Proposal keys are network-module parameters. A stray key would be
    dropped by VergeOS if a later, out-of-scope step ever sent it."""
    accepted = {"name", "state", "description", "network_type", "ip_address",
                "network", "gateway", "dhcp_enabled", "dhcp_start",
                "dhcp_end", "vlan_id", "layer2_type", "dns_servers", "mtu",
                "interface_network", "rate_limit"}
    segs = {"vmbr0.20": dict(AUTHORITATIVE["vmbr0.20"], mtu=9000)}
    r = run([nic(rate_mbyte_s=12)], segs, [], {
        "order": ["synthesize"],
        "synthesize": {"interface_network": "ext1 Switch"},
    })
    assert set(r["creates"][0]) <= accepted, set(r["creates"][0]) - accepted
    assert "interface_vnet" not in r["creates"][0]
    assert "dhcp_stop" not in r["creates"][0]


# ------------------------------------ proposed uplink and rate limit
def test_synthesised_vnet_is_flagged_as_having_no_uplink():
    """With no interface_network in the policy the proposal names no
    uplink. The network module can set one; the plan does not."""
    r = run([nic()], AUTHORITATIVE, [], {"order": ["synthesize"]})
    d = only(r)
    assert d["isolated"] is True
    assert "interface_network" in d["note"]
    assert "cannot" not in d["note"].lower()
    assert r["applied"] is False


def test_plan_warns_about_isolated_vnets():
    r = run([nic()], AUTHORITATIVE, [], {"order": ["synthesize"]})
    assert r["warnings"]
    assert "interface_network" in r["warnings"][0]
    assert "cannot set" not in r["warnings"][0]
    assert r["ok"] is True, "a warning is not a refusal"


def test_proposed_uplink_is_carried_and_not_marked_isolated():
    p = {"order": ["synthesize"],
         "synthesize": {"interface_network": "ext1 Switch"}}
    r = run([nic()], AUTHORITATIVE, [], p)
    spec = r["creates"][0]
    assert spec["interface_network"] == "ext1 Switch"
    assert "isolated" not in only(r)
    assert r["warnings"] == []
    assert r["proposals"] == r["creates"]
    assert r["applied"] is False


def test_rate_limit_is_proposed_on_the_vnet_and_the_decision():
    r = run([nic(rate_mbyte_s=12)], AUTHORITATIVE, [],
            {"order": ["synthesize"]})
    assert r["creates"][0]["rate_limit"] == 12
    assert only(r)["proposed_rate_limit"] == 12


def test_rate_limit_is_carried_when_mapping_an_existing_vnet():
    d = only(run([nic(rate_mbyte_s=8)], AUTHORITATIVE, INVENTORY,
                 {"order": ["vlan"]}))
    assert d["action"] == "map"
    assert d["proposed_rate_limit"] == 8


def test_fractional_rate_becomes_an_integer_cap():
    d = only(run([nic(rate_mbyte_s=0.4)], AUTHORITATIVE, INVENTORY,
                 {"order": ["vlan"]}))
    assert d["proposed_rate_limit"] == 1
    assert "integer" in d["rate_limit_note"]


def test_zero_rate_is_an_explicit_uncap():
    r = run([nic(rate_mbyte_s=0)], AUTHORITATIVE, [],
            {"order": ["synthesize"]})
    assert r["creates"][0]["rate_limit"] == 0


def test_conflicting_proposed_rates_are_refused():
    nics = [nic(key="net0", rate_mbyte_s=10),
            nic(key="net1", mac="AA:BB:CC:DD:EE:02", rate_mbyte_s=20)]
    r = run(nics, AUTHORITATIVE, [], {"order": ["synthesize"]})
    assert any(d["action"] == "refuse" for d in r["decisions"])


def test_mapping_to_an_existing_vnet_carries_no_such_warning():
    """Mapping onto an operator-built vnet says nothing about its uplink,
    because we did not build it."""
    r = run([nic()], AUTHORITATIVE, INVENTORY, {"order": ["vlan"]})
    assert r["warnings"] == []
    assert "isolated" not in only(r)
