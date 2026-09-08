"""Unit tests for the recipe answer resolver (pure functions, no API)."""

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    resolve_answers,
    scan_simulate,
)


def q(name, qtype="string", **kw):
    row = {"name": name, "type": qtype, "required": False, "default": "x"}
    row.update(kw)
    return row


# ── resolve_answers ──────────────────────────────────────────────────────────

def test_unknown_answer_is_an_error_not_a_silent_noop():
    out = resolve_answers([q("HOSTNAME")], {"HOTSNAME": "typo"})
    assert any("unknown answer" in e for e in out["errors"])


def test_missing_required_answer_is_an_error():
    out = resolve_answers([q("USER", required=True, default="")], {})
    assert any("missing required answer" in e for e in out["errors"])


def test_required_with_a_default_may_be_omitted():
    out = resolve_answers([q("USER", required=True, default="ops")], {})
    assert out["errors"] == []


def test_required_supplied_empty_is_an_error():
    out = resolve_answers([q("USER", required=True, default="")], {"USER": ""})
    assert any("supplied empty" in e for e in out["errors"])


def test_defaults_are_not_echoed_back():
    """The platform applies defaults itself; sending them back is noise."""
    out = resolve_answers([q("YB_RAM", "ram", default="4096")], {})
    assert out["answers"] == {}


def test_empty_default_unanswered_is_a_hint_not_an_error():
    """The SELECT_OS_TIER shape: optional, empty default, breaks the deploy."""
    out = resolve_answers([q("SELECT_OS_TIER", "row", default="")], {})
    assert out["errors"] == []
    assert any("SELECT_OS_TIER" in h for h in out["hints"])


@pytest.mark.parametrize("qtype", ["num", "ram", "disksize", "seconds"])
def test_numeric_types_coerce_from_strings(qtype):
    out = resolve_answers([q("N", qtype)], {"N": "2048"})
    assert out["answers"]["N"] == 2048
    assert isinstance(out["answers"]["N"], int)


def test_non_numeric_value_for_numeric_type_passes_through():
    out = resolve_answers([q("N", "num")], {"N": "auto"})
    assert out["answers"]["N"] == "auto"


@pytest.mark.parametrize("given,want", [("true", True), ("no", False),
                                        ("1", True), ("off", False)])
def test_bool_strings_coerce(given, want):
    out = resolve_answers([q("B", "bool")], {"B": given})
    assert out["answers"]["B"] is want


def test_network_name_resolves_to_vnet_key():
    out = resolve_answers([q("YB_NIC_ETH0", "network")],
                          {"YB_NIC_ETH0": "External"},
                          vnets=[{"name": "External", "$key": 7}])
    assert out["answers"]["YB_NIC_ETH0"] == 7


def test_unknown_network_name_is_an_error():
    out = resolve_answers([q("YB_NIC_ETH0", "network")],
                          {"YB_NIC_ETH0": "Nope"}, vnets=[])
    assert any("not found" in e for e in out["errors"])
    assert "YB_NIC_ETH0" not in out["answers"]


def test_new_internal_sentinel_is_passed_through_verbatim():
    out = resolve_answers([q("YB_NIC_ETH0", "network")],
                          {"YB_NIC_ETH0": "__new_internal__"}, vnets=[])
    assert out["answers"]["YB_NIC_ETH0"] == "__new_internal__"
    assert out["errors"] == []


def test_integer_network_key_is_accepted_directly():
    out = resolve_answers([q("YB_NIC_ETH0", "network")],
                          {"YB_NIC_ETH0": 7}, vnets=[])
    assert out["answers"]["YB_NIC_ETH0"] == 7


def test_password_questions_are_reported_for_no_log():
    out = resolve_answers([q("PASSWORD", "password"), q("USER")], {})
    assert out["secret_vars"] == ["PASSWORD"]


# ── scan_simulate ────────────────────────────────────────────────────────────

def test_clean_simulate_is_ok():
    out = scan_simulate({"err": "Simulation complete",
                         "response": {"logs": ["Executing recipe API commands"],
                                      "answers": {"YB_VM_KEY": 37}}})
    assert out["ok"] is True
    assert out["vm_key"] == 37


def test_simulation_complete_with_a_failed_step_is_not_ok():
    """The whole reason the scan exists: the API calls this a success."""
    out = scan_simulate({
        "err": "Simulation complete",
        "response": {"logs": [
            "Database create CREATE_OS_DRIVE: machine_drives",
            "Error executing API command 'CREATE_OS_DRIVE': value '' is not "
            "in list for field 'preferred_tier'",
        ], "answers": {}},
    })
    assert out["ok"] is False
    assert any("preferred_tier" in e for e in out["errors"])


def test_validation_failure_surfaces_as_an_error():
    out = scan_simulate({"err": "Missing required answer to 'User Name'"})
    assert out["ok"] is False
    assert out["errors"][0] == "Missing required answer to 'User Name'"


def test_empty_document_is_not_silently_ok():
    out = scan_simulate({})
    assert out["ok"] is False


def test_cloudinit_files_are_reported():
    out = scan_simulate({"err": "Simulation complete",
                         "response": {"logs": [],
                                      "cloudinit_files": [{"name": "/user-data"},
                                                          {"name": "/meta-data"}],
                                      "answers": {}}})
    assert out["cloudinit_files"] == ["/user-data", "/meta-data"]


# ── generality across the whole recipe catalogue ─────────────────────────────

def test_recipe_with_no_published_questions_passes_answers_through():
    """The vendor 'Services' recipe publishes nothing to recipe_questions.
    Validating against an empty set would reject every answer as unknown."""
    out = resolve_answers([], {"HOSTNAME": "svc", "ANYTHING": 1})
    assert out["errors"] == []
    assert out["introspectable"] is False
    assert out["answers"] == {"HOSTNAME": "svc", "ANYTHING": 1}
    assert any("cannot be validated locally" in h for h in out["hints"])


@pytest.mark.parametrize("qtype", ["hidden", "database_create",
                                   "database_edit", "database_find", "field"])
def test_internal_question_types_never_produce_hints(qtype):
    out = resolve_answers([q("YB_INTERNAL", qtype, default="")], {})
    assert out["hints"] == []


def test_database_section_questions_never_produce_hints():
    out = resolve_answers(
        [dict(name="DB_STEP", type="string", default="", required=False,
              sect="$database")], {})
    assert out["hints"] == []


def test_windows_hostname_over_max_is_refused_locally():
    """Windows recipes cap HOSTNAME at 15; the API rejects it mid-deploy."""
    out = resolve_answers(
        [q("HOSTNAME", "string", required=True, default="", max=15)],
        {"HOSTNAME": "a-very-long-windows-hostname"})
    assert any("at most 15" in e for e in out["errors"])


def test_hostname_within_max_is_accepted():
    out = resolve_answers(
        [q("HOSTNAME", "string", required=True, default="", max=15)],
        {"HOSTNAME": "win-01"})
    assert out["errors"] == []


def test_regex_violation_is_refused_locally():
    out = resolve_answers(
        [q("HOSTNAME", "string", default="",
           regex="[a-zA-Z]([a-zA-Z0-9_-]+[a-zA-Z0-9])?")],
        {"HOSTNAME": "9starts-with-digit"})
    assert any("pattern" in e for e in out["errors"])


def test_broken_regex_in_the_recipe_does_not_crash():
    out = resolve_answers([q("H", "string", default="", regex="([unclosed")],
                          {"H": "whatever"})
    assert out["errors"] == []


def test_numeric_max_is_enforced():
    out = resolve_answers([q("N", "num", default="", max=4)], {"N": 99})
    assert any("at most 4" in e for e in out["errors"])


def test_table_backed_question_is_reported_for_option_lookup():
    out = resolve_answers(
        [q("SELECT_OS_TIER", "row", default="", table="storage_tiers",
           filter="tier ne 0")], {})
    entry = out["needs_options"][0]
    assert entry["name"] == "SELECT_OS_TIER"
    assert entry["table"] == "storage_tiers"
    assert entry["filter"] == "tier ne 0"
    assert entry["fields"]          # the UI's field spec, for rendering options


def test_missing_required_row_answer_lists_the_valid_options():
    out = resolve_answers(
        [q("SELECT_OS_TIER", "row", required=True, default="",
           table="storage_tiers")], {},
        options={"SELECT_OS_TIER": [{"$key": 1, "$display": "1"},
                                    {"$key": 4, "$display": "4"}]})
    assert any("valid: 1=1, 4=4" in e for e in out["errors"])


def test_empty_option_set_says_so_instead_of_listing_nothing():
    """Windows WINDOWS_ISO draws from files; with no ISO uploaded the API
    only says 'Missing required answer'."""
    out = resolve_answers(
        [q("WINDOWS_ISO", "list", required=True, default="", table="files",
           filter="name eq 'server.iso'")], {},
        options={"WINDOWS_ISO": []})
    assert any("NO valid options exist" in e for e in out["errors"])
    assert any("create one first" in e for e in out["errors"])


def test_answer_not_in_the_option_set_is_refused():
    out = resolve_answers(
        [q("SELECT_OS_TIER", "row", default="", table="storage_tiers")],
        {"SELECT_OS_TIER": 9},
        options={"SELECT_OS_TIER": [{"$key": 1, "$display": "1"}]})
    assert any("not a valid choice" in e for e in out["errors"])
    assert "SELECT_OS_TIER" not in out["answers"]


def test_answer_in_the_option_set_is_accepted():
    out = resolve_answers(
        [q("SELECT_OS_TIER", "row", default="", table="storage_tiers")],
        {"SELECT_OS_TIER": 1},
        options={"SELECT_OS_TIER": [{"$key": 1, "$display": "1"}]})
    assert out["errors"] == []
    assert out["answers"]["SELECT_OS_TIER"] == 1


def test_prune_unknown_moves_unknown_answers_out_of_errors():
    """One answer set across 32 recipes: a key this recipe lacks is expected."""
    out = resolve_answers([q("HOSTNAME")], {"HOSTNAME": "h", "VPNPASS": "x"},
                          prune_unknown=True)
    assert out["errors"] == []
    assert out["pruned"] == ["VPNPASS"]
    assert "VPNPASS" not in out["answers"]


def test_pruning_is_off_by_default_so_typos_still_fail():
    out = resolve_answers([q("HOSTNAME")], {"HOTSNAME": "typo"})
    assert any("unknown answer" in e for e in out["errors"])
    assert out["pruned"] == []
