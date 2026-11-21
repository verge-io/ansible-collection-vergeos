# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

"""Gate the network plan before anything is attached.

Pure evaluation: takes a netmap plan, the source VMs' power states, and an
inventory of NICs that already exist in VergeOS, and returns the plan
again with every attachment either APPROVED or REFUSED, each with a
reason. The verdict is data. Nothing is attached.

Why this is a separate stage
---------------------------
netmap answers "which vnet does this NIC belong on". That is a topology
question and it has a correct answer even while the source VM is running.
Whether it is SAFE to attach right now is a different question with a
different input -- the state of the world -- and conflating the two is how
a DR test takes down the production it was testing.

The specific hazard: replication and migration deliberately PRESERVE MAC
addresses, because changing them renames interfaces inside the guest and
strands statically-addressed hosts. During a DR rehearsal the source VM is
still running. Attach its twin to a vnet that bridges the same layer 2 and
you have put two hosts with one MAC and one IP on the same wire. The
outage lands on exactly the workloads the rehearsal existed to protect.

Hence three attach modes, defaulting to the safe one:

  detached  The verdict approves leaving the NIC unconnected. The plan's
            intended target is recorded. Safe at any time. Nothing is created.
  bubble    Everything lands on one isolated vnet. Safe if -- and only if
            -- that vnet really is isolated, which this module cannot
            verify and therefore states as an operator assertion.
  live      Real attachment to real production vnets. Permitted only when
            the source VM is confirmed down, or when the operator has
            named that specific VM in an override list.

The override is per-VM by design. A global "yes" would be ticked once and
then apply forever, to VMs nobody was thinking about at the time.
"""


ATTACH_MODES = ("detached", "bubble", "live")

# Actions netmap can emit that mean "this NIC wants a real vnet".
ATTACHING = ("map", "create", "bubble")

# States that count as "the source cannot be on the wire". Anything else,
# including unknown, is treated as running -- absence of evidence is not
# evidence of absence when the failure mode is a production outage.
SAFE_SOURCE_STATES = ("stopped", "shutdown", "off", "poweroff", "down")


class GuardError(ValueError):
    """Guard configuration that cannot be honoured."""


def normalise_mac(mac):
    return (mac or "").strip().upper().replace("-", ":")


def index_existing_nics(nics):
    """Map MAC -> list of {vm, network} already present in VergeOS."""
    out = {}
    for entry in nics or []:
        mac = normalise_mac(entry.get("mac_address") or entry.get("mac"))
        if not mac:
            continue
        out.setdefault(mac, []).append({
            "vm": entry.get("vm_name") or entry.get("vm") or "",
            "network": entry.get("network") or entry.get("vnet") or "",
        })
    return out


def source_is_down(vm, states):
    state = (states or {}).get(vm)
    if state is None:
        return False, "source power state for %r was not supplied" % vm
    if str(state).strip().lower() in SAFE_SOURCE_STATES:
        return True, "source %r is %s" % (vm, state)
    return False, "source %r is %s" % (vm, state)


def assess(plan, source_states=None, existing_nics=None, options=None):
    options = dict(options or {})
    mode = options.get("attach_mode")
    if mode is None:
        mode = (plan.get("policy") or {}).get("attach_mode", "detached")
    if mode not in ATTACH_MODES:
        raise GuardError("attach_mode must be one of %s"
                         % ", ".join(ATTACH_MODES))

    bubble = options.get("bubble_network") or \
        (plan.get("policy") or {}).get("bubble_network") or ""
    if mode == "bubble" and not bubble:
        raise GuardError("attach_mode=bubble requires bubble_network")

    overrides = set(options.get("live_attach_running_override") or [])
    if overrides and mode != "live":
        raise GuardError("live_attach_running_override is meaningless unless "
                         "attach_mode=live")

    existing = index_existing_nics(existing_nics)
    seen_macs = {}
    results = []

    context = {
        "mode": mode,
        "bubble": bubble,
        "overrides": overrides,
        "source_states": source_states,
        "existing": existing,
        "seen_macs": seen_macs,
    }
    for decision in plan.get("decisions", []):
        results.append(_gate(decision, context))

    # A proposal may take the source subnet's gateway as its own address,
    # because in VergeOS the vnet is the router for that subnet. That
    # address is still held by the live source router. This guard does
    # not create the vnet. The warning says what applying the proposal
    # would do.
    address_warnings = []
    for spec in plan.get("creates", []):
        addr = spec.get("ip_address")
        if not addr:
            continue
        holders = sorted({r["vm"] for r in results
                          if r.get("verdict") == "approve"
                          and r.get("attach_to") == spec.get("name")})
        uplink = (spec.get("interface_network") or "").strip()
        if uplink:
            # The proposal names an uplink, so applying the vnet (not
            # just attaching a NIC) would put this address on the fabric.
            interested = sorted({r["vm"] for r in results
                                 if r.get("intended_target") == spec.get("name")
                                 or r.get("attach_to") == spec.get("name")})
        else:
            interested = holders
        running = [vm for vm in interested if not source_is_down(
            vm, source_states)[0]]
        if not running:
            continue
        if uplink:
            address_warnings.append(
                "proposal %r would hold %s and names uplink %r. Applying "
                "it while the source for %s is still up would be a "
                "duplicate gateway on that uplink. This plan does not "
                "create or attach anything."
                % (spec["name"], addr, uplink, ", ".join(running)))
        elif holders:
            address_warnings.append(
                "proposal %r would hold %s, which is the source subnet's "
                "gateway and is still answered by the live source for %s. "
                "The proposal names no uplink, so the address would stay "
                "off the fabric until one is added. A duplicate gateway "
                "the moment an uplink is attached. This plan does not "
                "create the vnet."
                % (spec["name"], addr, ", ".join(running)))

    refused = [r for r in results if r["verdict"] == "refuse"]
    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    # A vnet only needs creating if something it holds is actually being
    # attached. In detached mode creation is still useful -- it is where a
    # later run will attach to -- so it is kept, but flagged as unused.
    attaching_targets = {r["attach_to"] for r in results
                         if r["verdict"] == "approve" and r["attach_to"]}

    return {
        "applied": False,
        "attach_mode": mode,
        "bubble_network": bubble,
        "results": results,
        "refusals": refused,
        "warnings": list(plan.get("warnings", [])) + address_warnings,
        "ok": not refused,
        "creates": plan.get("creates", []),
        "creates_unused": [c for c in plan.get("creates", [])
                           if c["name"] not in attaching_targets],
        "summary": {"nics": len(results), "by_verdict": counts,
                    "attaching": len(attaching_targets)},
    }


def _gate(decision, context):
    mode = context["mode"]
    bubble = context["bubble"]
    overrides = context["overrides"]
    source_states = context["source_states"]
    existing = context["existing"]
    seen_macs = context["seen_macs"]
    base = {k: decision.get(k) for k in
            ("vm", "nic", "mac", "model", "segment", "action", "target",
             "resolver")}
    base["intended_target"] = decision.get("target")

    def out(verdict, attach_to, why, **extra):
        return dict(base, verdict=verdict, attach_to=attach_to, why=why,
                    **extra)

    # netmap already refused this NIC; the guard does not rehabilitate it.
    if decision.get("action") == "refuse":
        return out("refuse", None,
                   "mapping was refused upstream: %s" % decision.get("why"))

    if decision.get("action") == "detach":
        return out("approve", None,
                   decision.get("why") or "NIC is left unconnected")

    mac = normalise_mac(decision.get("mac"))
    vm = decision.get("vm")

    # Duplicate MAC inside the wave itself. Two migrated VMs on one MAC is
    # a self-inflicted collision that no source-side check would catch.
    if mac:
        prior = seen_macs.get(mac)
        if prior and prior != (vm, decision.get("nic")):
            return out("refuse", None,
                       "MAC %s is already used by %s/%s in this same plan"
                       % (mac, prior[0], prior[1]))
        seen_macs[mac] = (vm, decision.get("nic"))

    # Same MAC already live in VergeOS on a different VM. Re-running
    # against the same VM is idempotent and allowed.
    for holder in existing.get(mac, []):
        if holder["vm"] and holder["vm"] != vm:
            return out("refuse", None,
                       "MAC %s already exists in VergeOS on VM %r "
                       "(network %r); attaching would duplicate it"
                       % (mac, holder["vm"], holder["network"]))

    if mode == "detached":
        return out("approve", None,
                   "attach_mode=detached: NIC created but connected to "
                   "nothing; intended target %r recorded for a later run"
                   % decision.get("target"))

    if mode == "bubble":
        return out("approve", bubble,
                   "attach_mode=bubble: forced onto %r instead of %r. "
                   "Isolation of the bubble is an operator assertion this "
                   "tool cannot verify." % (bubble, decision.get("target")))

    # mode == live
    down, why = source_is_down(vm, source_states)
    if down:
        return out("approve", decision.get("target"),
                   "attach_mode=live and %s" % why)
    if vm in overrides:
        return out("approve", decision.get("target"),
                   "attach_mode=live; %s, but %r is named in "
                   "live_attach_running_override" % (why, vm),
                   warning="MAC %s will be live on the wire while the source "
                           "is still up" % mac)
    return out("refuse", None,
               "attach_mode=live but %s. MACs are preserved by design, so "
               "attaching now would put a duplicate MAC/IP on the wire "
               "alongside the running source. Stop the source, or name %r "
               "in live_attach_running_override." % (why, vm))
