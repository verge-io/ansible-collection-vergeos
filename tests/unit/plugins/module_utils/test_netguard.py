# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Gate 4 -- blast radius. Verdicts only. Nothing is attached."""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils import netguard
from ansible_collections.vergeio.vergeos.plugins.module_utils import netmap
from ansible_collections.vergeio.vergeos.plugins.module_utils.netguard import GuardError


def decision(vm="web01", nic="net0", mac="AA:BB:CC:DD:EE:01", action="map",
             target="prod-web", segment="vmbr0.20"):
    return {"vm": vm, "nic": nic, "mac": mac, "model": "virtio",
            "segment": segment, "action": action, "target": target,
            "resolver": "vlan", "why": "VLAN 20 matches"}


def make_plan(decisions, creates=None, policy=None):
    return {"policy": policy or {}, "decisions": decisions,
            "creates": creates or [], "refusals": [], "ok": True}


def one(verdict):
    assert len(verdict["results"]) == 1
    return verdict["results"][0]


# ------------------------------------------------------- the default
def test_default_mode_is_detached():
    v = netguard.assess(make_plan([decision()]))
    assert v["attach_mode"] == "detached"
    r = one(v)
    assert r["verdict"] == "approve"
    assert r["attach_to"] is None


def test_detached_still_records_the_intended_target():
    """Detaching must not lose the answer -- a later run needs it."""
    r = one(netguard.assess(make_plan([decision()])))
    assert r["intended_target"] == "prod-web"


def test_detached_is_safe_even_with_the_source_running():
    v = netguard.assess(make_plan([decision()]), {"web01": "running"})
    assert v["ok"] is True
    assert one(v)["attach_to"] is None


# ------------------------------------------- live attach vs running source
def test_live_attach_is_refused_while_the_source_runs():
    """THE hazard. Preserved MAC + running source + production vnet."""
    v = netguard.assess(make_plan([decision()]), {"web01": "running"},
                        options={"attach_mode": "live"})
    r = one(v)
    assert r["verdict"] == "refuse"
    assert "duplicate MAC" in r["why"]
    assert v["ok"] is False


def test_live_attach_is_allowed_once_the_source_is_stopped():
    v = netguard.assess(make_plan([decision()]), {"web01": "stopped"},
                        options={"attach_mode": "live"})
    r = one(v)
    assert r["verdict"] == "approve"
    assert r["attach_to"] == "prod-web"


@pytest.mark.parametrize("state", ["stopped", "shutdown", "off", "poweroff",
                                   "down", "STOPPED", " Stopped "])
def test_all_down_states_are_accepted(state):
    v = netguard.assess(make_plan([decision()]), {"web01": state},
                        options={"attach_mode": "live"})
    assert one(v)["verdict"] == "approve"


@pytest.mark.parametrize("state", ["running", "paused", "suspended",
                                   "migrating", "unknown", ""])
def test_anything_not_clearly_down_is_treated_as_up(state):
    v = netguard.assess(make_plan([decision()]), {"web01": state},
                        options={"attach_mode": "live"})
    assert one(v)["verdict"] == "refuse"


def test_missing_power_state_is_treated_as_running():
    """Absence of evidence is not evidence of absence when the failure
    mode is a production outage."""
    v = netguard.assess(make_plan([decision()]), {},
                        options={"attach_mode": "live"})
    r = one(v)
    assert r["verdict"] == "refuse"
    assert "not supplied" in r["why"]


# ------------------------------------------------------------ overrides
def test_named_override_permits_a_running_source():
    v = netguard.assess(
        make_plan([decision()]), {"web01": "running"},
        options={"attach_mode": "live",
                 "live_attach_running_override": ["web01"]})
    r = one(v)
    assert r["verdict"] == "approve"
    assert "warning" in r


def test_override_is_per_vm_not_a_blanket_yes():
    """A global switch gets ticked once and then applies forever, to VMs
    nobody was thinking about."""
    plan = make_plan([decision(vm="web01"),
                      decision(vm="db01", mac="AA:BB:CC:DD:EE:02")])
    v = netguard.assess(plan, {"web01": "running", "db01": "running"},
                        options={"attach_mode": "live",
                                 "live_attach_running_override": ["web01"]})
    by_vm = {r["vm"]: r["verdict"] for r in v["results"]}
    assert by_vm == {"web01": "approve", "db01": "refuse"}


def test_override_outside_live_mode_is_an_error():
    with pytest.raises(GuardError):
        netguard.assess(make_plan([decision()]),
                        options={"attach_mode": "detached",
                                 "live_attach_running_override": ["web01"]})


# --------------------------------------------------------------- bubble
def test_bubble_forces_everything_onto_the_bubble_vnet():
    v = netguard.assess(make_plan([decision()]), {"web01": "running"},
                        options={"attach_mode": "bubble",
                                 "bubble_network": "dr-bubble"})
    r = one(v)
    assert (r["verdict"], r["attach_to"]) == ("approve", "dr-bubble")


def test_bubble_ignores_the_mapped_target_but_remembers_it():
    v = netguard.assess(make_plan([decision()]),
                        options={"attach_mode": "bubble",
                                 "bubble_network": "dr-bubble"})
    r = one(v)
    assert r["attach_to"] == "dr-bubble"
    assert r["intended_target"] == "prod-web"


def test_bubble_isolation_is_stated_as_an_assertion_not_a_check():
    """The tool cannot see the bubble's uplink. Saying so is the honest
    thing; claiming verification would be worse than saying nothing."""
    r = one(netguard.assess(make_plan([decision()]),
                            options={"attach_mode": "bubble",
                                     "bubble_network": "dr-bubble"}))
    assert "cannot verify" in r["why"]


def test_bubble_without_a_network_is_an_error():
    with pytest.raises(GuardError):
        netguard.assess(make_plan([decision()]),
                        options={"attach_mode": "bubble"})


def test_bubble_network_can_come_from_the_plan_policy():
    plan = make_plan([decision()],
                     policy={"attach_mode": "bubble",
                             "bubble_network": "from-policy"})
    assert one(netguard.assess(plan))["attach_to"] == "from-policy"


# ---------------------------------------------------------- MAC conflicts
def test_duplicate_mac_within_the_plan_is_refused():
    plan = make_plan([decision(vm="a"), decision(vm="b")])
    v = netguard.assess(plan, {"a": "stopped", "b": "stopped"},
                        options={"attach_mode": "live"})
    verdicts = [r["verdict"] for r in v["results"]]
    assert verdicts == ["approve", "refuse"]
    assert "same plan" in v["results"][1]["why"]


def test_mac_already_on_a_different_vergeos_vm_is_refused():
    existing = [{"mac_address": "aa:bb:cc:dd:ee:01", "vm_name": "someone-else",
                 "network": "prod-web"}]
    v = netguard.assess(make_plan([decision()]), {"web01": "stopped"},
                        [], options={"attach_mode": "live"})
    assert one(v)["verdict"] == "approve"
    v2 = netguard.assess(make_plan([decision()]), {"web01": "stopped"},
                         existing, options={"attach_mode": "live"})
    r = one(v2)
    assert r["verdict"] == "refuse"
    assert "someone-else" in r["why"]


def test_rerunning_against_the_same_vm_is_idempotent_not_a_conflict():
    existing = [{"mac_address": "AA:BB:CC:DD:EE:01", "vm_name": "web01",
                 "network": "prod-web"}]
    v = netguard.assess(make_plan([decision()]), {"web01": "stopped"},
                        existing, options={"attach_mode": "live"})
    assert one(v)["verdict"] == "approve"


def test_mac_comparison_is_case_and_separator_insensitive():
    existing = [{"mac": "aa-bb-cc-dd-ee-01", "vm": "other"}]
    v = netguard.assess(make_plan([decision()]), {"web01": "stopped"},
                        existing, options={"attach_mode": "live"})
    assert one(v)["verdict"] == "refuse"


def test_mac_conflict_is_caught_even_in_detached_mode():
    """Detached NICs still carry their MAC; the collision arrives the
    moment someone attaches them."""
    existing = [{"mac": "AA:BB:CC:DD:EE:01", "vm": "other"}]
    v = netguard.assess(make_plan([decision()]), {}, existing)
    assert one(v)["verdict"] == "refuse"


def test_blank_macs_do_not_collide_with_each_other():
    plan = make_plan([decision(vm="a", mac=""), decision(vm="b", mac="")])
    v = netguard.assess(plan)
    assert all(r["verdict"] == "approve" for r in v["results"])


# ------------------------------------------------- upstream interaction
def test_upstream_refusal_is_not_rehabilitated():
    d = decision(action="refuse", target=None)
    d["why"] = "VLAN 20 matches 2 vnets"
    r = one(netguard.assess(make_plan([d]), {"web01": "stopped"},
                            options={"attach_mode": "live"}))
    assert r["verdict"] == "refuse"
    assert "upstream" in r["why"]


def test_unconnected_nics_pass_through_untouched():
    d = decision(action="detach", target=None, segment=None)
    d["why"] = "source NIC has no bridge"
    r = one(netguard.assess(make_plan([d]), options={"attach_mode": "live"}))
    assert r["verdict"] == "approve"
    assert r["attach_to"] is None


def test_end_to_end_from_netmap_to_guard():
    nics = [{"key": "net0", "mac": "AA:BB:CC:DD:EE:01", "model": "virtio",
             "bridge": "vmbr0", "tag": 20, "trunks": [],
             "segment": "vmbr0.20", "connected": True}]
    plan = netmap.plan({"web01": nics}, {}, [{"name": "prod-web",
                                              "vlan_id": 20}],
                       {"order": ["vlan"]})
    v = netguard.assess(plan, {"web01": "running"},
                        options={"attach_mode": "live"})
    assert plan["ok"] is True, "mapping is correct even while source runs"
    assert v["ok"] is False, "attaching it right now is not"


# ---------------------------------------------------------------- creates
def test_creates_are_flagged_unused_in_detached_mode():
    plan = make_plan([decision(action="create", target="vmbr0.20")],
                     creates=[{"name": "vmbr0.20"}])
    v = netguard.assess(plan)
    assert [c["name"] for c in v["creates_unused"]] == ["vmbr0.20"]


def test_creates_are_not_unused_once_attached():
    plan = make_plan([decision(action="create", target="vmbr0.20")],
                     creates=[{"name": "vmbr0.20"}])
    v = netguard.assess(plan, {"web01": "stopped"},
                        options={"attach_mode": "live"})
    assert v["creates_unused"] == []


def test_summary_counts_verdicts():
    plan = make_plan([decision(vm="a"),
                      decision(vm="b", mac="AA:BB:CC:DD:EE:02")])
    v = netguard.assess(plan, {"a": "stopped"},
                        options={"attach_mode": "live"})
    assert v["summary"]["nics"] == 2
    assert v["summary"]["by_verdict"] == {"approve": 1, "refuse": 1}


def test_bad_attach_mode_is_an_error():
    with pytest.raises(GuardError):
        netguard.assess(make_plan([decision()]),
                        options={"attach_mode": "yolo"})


# ------------------------------------------------------------ cli shape


# --------------------------------- the address a synthesised vnet takes
def make_create_plan(vm="web01", addr="10.0.20.1"):
    d = decision(vm=vm, action="create", target="vmbr0.20")
    return {"policy": {}, "decisions": [d],
            "creates": [{"name": "vmbr0.20", "ip_address": addr}],
            "refusals": [], "warnings": [], "ok": True}


def test_warns_when_a_vnet_takes_a_live_sources_gateway_address():
    """The vnet's own address IS the source subnet's gateway, still
    answered by the live source router."""
    v = netguard.assess(make_create_plan(), {"web01": "running"},
                        options={"attach_mode": "live",
                                 "live_attach_running_override": ["web01"]})
    assert any("duplicate gateway" in w for w in v["warnings"])


def test_no_address_warning_once_the_source_is_down():
    v = netguard.assess(make_create_plan(), {"web01": "stopped"},
                        options={"attach_mode": "live"})
    assert v["warnings"] == []


def test_address_collision_is_a_warning_not_a_refusal():
    """The guard reports the collision. It does not create the vnet."""
    v = netguard.assess(make_create_plan(), {"web01": "running"},
                        options={"attach_mode": "live",
                                 "live_attach_running_override": ["web01"]})
    assert v["ok"] is True
    assert v["applied"] is False


def test_detached_mode_raises_no_address_warning():
    """Nothing is attached, so nothing can answer for the address."""
    v = netguard.assess(make_create_plan(), {"web01": "running"})
    assert v["warnings"] == []


def test_plan_warnings_are_carried_through_to_the_guard():
    plan = make_create_plan()
    plan["warnings"] = ["synthesised vnet has no uplink"]
    v = netguard.assess(plan, {"web01": "stopped"},
                        options={"attach_mode": "live"})
    assert "synthesised vnet has no uplink" in v["warnings"]


def _uplink_plan():
    plan = make_create_plan()
    plan["creates"][0]["interface_network"] = "ext1 Switch"
    return plan


def test_named_uplink_warns_while_the_source_is_up():
    v = netguard.assess(_uplink_plan(), {"web01": "running"},
                        options={"attach_mode": "detached"})
    assert any("ext1 Switch" in w and "duplicate gateway" in w
               for w in v["warnings"])
    assert v["ok"] is True
    assert v["applied"] is False


def test_named_uplink_is_quiet_once_the_source_is_down():
    v = netguard.assess(_uplink_plan(), {"web01": "stopped"},
                        options={"attach_mode": "live"})
    assert v["warnings"] == []
    assert one(v)["verdict"] == "approve"
