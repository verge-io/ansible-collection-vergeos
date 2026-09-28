"""Unit tests for the recipe answer resolver (pure functions, no API)."""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    maskable_strings,
    resolve_answers,
    scan_simulate,
    unmaskable_answer_strings,
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


@pytest.mark.parametrize("given,want", [
    (True, True), (False, False),
    ("true", True), ("TRUE", True), (" yes ", True), ("on", True), ("1", True),
    (1, True),
    ("false", False), ("NO", False), ("off", False), ("0", False), ("", False),
    ("   ", False),
    (0, False),
])
def test_bool_values_coerce(given, want):
    """Known bool forms, including integer 0 and 1, become real booleans."""
    out = resolve_answers([q("B", "bool")], {"B": given})
    assert out["errors"] == []
    assert out["answers"]["B"] is want


@pytest.mark.parametrize("given", ["enabled", "UEFI", "uefi", "y", "2"])
def test_unrecognised_bool_answer_is_refused(given):
    """A word _coerce does not know must not be sent on as a string.

    The platform reads that string as false, so SELECT_CREATE_UEFI: enabled
    built a BIOS VM and the deploy reported success.
    """
    out = resolve_answers([q("SELECT_CREATE_UEFI", "bool")],
                          {"SELECT_CREATE_UEFI": given})
    assert out["errors"], "%r was accepted for a bool question" % given
    msg = " ".join(out["errors"])
    assert "SELECT_CREATE_UEFI" in msg
    assert "recognised boolean" in msg
    for word in ("true", "yes", "on", "1", "false", "no", "off", "0"):
        assert word in msg
    assert out["answers"].get("SELECT_CREATE_UEFI") is not True


@pytest.mark.parametrize("given", [50, 1024, 1048575, "50", "1024", "1048575"])
def test_disksize_under_one_megabyte_is_refused(given):
    """A disksize answer is bytes. 50 was sent as fifty bytes.

    The recipe then built the OS drive at the image size and the deploy
    reported success. Anything above zero and under 1 MB cannot be a disk.
    """
    out = resolve_answers([q("YB_DRIVE_OS_SIZE", "disksize")],
                          {"YB_DRIVE_OS_SIZE": given})
    assert out["errors"], "%r was accepted for a disksize question" % given
    msg = " ".join(out["errors"])
    assert "YB_DRIVE_OS_SIZE" in msg
    assert "bytes" in msg
    assert "53687091200" in msg
    assert "1048576" in msg


@pytest.mark.parametrize("given,want", [
    (0, 0),
    ("0", 0),
    (1048576, 1048576),
    ("1048576", 1048576),
    (21474836480, 21474836480),
    ("21474836480", 21474836480),
    (53687091200, 53687091200),
])
def test_disksize_zero_and_real_byte_counts_are_accepted(given, want):
    """Zero is the recipe default. A count of at least 1 MB is a disk."""
    out = resolve_answers([q("YB_DRIVE_OS_SIZE", "disksize")],
                          {"YB_DRIVE_OS_SIZE": given})
    assert out["errors"] == [], " ".join(out["errors"])
    assert out["answers"]["YB_DRIVE_OS_SIZE"] == want


@pytest.mark.parametrize("qtype", ["num", "ram", "seconds"])
def test_other_numeric_types_ignore_the_disksize_floor(qtype):
    """RAM, counts and timeouts are not byte sizes. 50 stays valid for them."""
    out = resolve_answers([q("N", qtype)], {"N": 50})
    assert out["errors"] == []
    assert out["answers"]["N"] == 50


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


# Published by stock Linux recipes. The inner + is what refuses "db".
STOCK_HOSTNAME_RE = "[a-zA-Z]([a-zA-Z0-9_-]+[a-zA-Z0-9])?"


def test_regex_violation_is_refused_locally():
    out = resolve_answers(
        [q("HOSTNAME", "string", default="", regex=STOCK_HOSTNAME_RE)],
        {"HOSTNAME": "9starts-with-digit"})
    assert any("pattern" in e for e in out["errors"])
    assert STOCK_HOSTNAME_RE in " ".join(out["errors"])


@pytest.mark.parametrize("qtype", ["string", "hostname"])
@pytest.mark.parametrize("value", ["db", "DB", "d1", "a", "ab", "abc", "a-b",
                                   "web-01"])
def test_two_letter_hostname_matches_the_stock_linux_pattern(qtype, value):
    """The published pattern refuses db. The platform accepts it.

    One letter already matched, and three or more already matched. Two
    characters starting with a letter and ending in an alphanumeric are the
    gap the plus quantifier opens. The refusal message is not raised.
    """
    out = resolve_answers(
        [q("HOSTNAME", qtype, default="", regex=STOCK_HOSTNAME_RE)],
        {"HOSTNAME": value})
    assert out["errors"] == []
    assert out["answers"]["HOSTNAME"] == value


@pytest.mark.parametrize("value", ["d-", "d_", "-d", "9db", "db!", "zz-ok!!!"])
def test_stock_hostname_pattern_still_refuses_a_real_mismatch(value):
    """Reading the plus as a star does not accept a trailing hyphen or a
    leading digit, and it does not accept a pattern match sitting inside a
    longer string."""
    out = resolve_answers(
        [q("HOSTNAME", "string", default="", regex=STOCK_HOSTNAME_RE)],
        {"HOSTNAME": value})
    assert any("does not match" in e for e in out["errors"])
    assert STOCK_HOSTNAME_RE in " ".join(out["errors"])


def test_two_letter_hostname_still_honours_a_declared_minimum_length():
    out = resolve_answers(
        [q("HOSTNAME", "string", default="", min=3, regex=STOCK_HOSTNAME_RE)],
        {"HOSTNAME": "db"})
    assert any("at least 3" in e for e in out["errors"])
    assert not any("pattern" in e for e in out["errors"])


def test_two_letter_hostname_is_still_refused_by_a_different_pattern():
    """The stock-pattern correction is not an opt-out for every regex."""
    out = resolve_answers(
        [q("HOSTNAME", "hostname", regex="[a-zA-Z]{3,}")],
        {"HOSTNAME": "db"})
    assert any("does not match" in e for e in out["errors"])


def test_catalog_pattern_that_already_uses_a_star_accepts_db():
    """A catalog fix that publishes the star form needs no local rewrite."""
    fixed = "[a-zA-Z]([a-zA-Z0-9_-]*[a-zA-Z0-9])?"
    out = resolve_answers(
        [q("HOSTNAME", "string", default="", regex=fixed)],
        {"HOSTNAME": "db"})
    assert out["errors"] == []
    assert out["answers"]["HOSTNAME"] == "db"


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


IP_CHOICES = {"dhcp": "DHCP", "static": "Static"}


def test_list_answer_matching_a_choice_key_is_accepted():
    """YB_IP_ADDR_TYPE wants the key dhcp, not the label the UI shows."""
    out = resolve_answers(
        [q("YB_IP_ADDR_TYPE", "list", default="dhcp", list=IP_CHOICES)],
        {"YB_IP_ADDR_TYPE": "dhcp"})
    assert out["errors"] == []
    assert out["answers"]["YB_IP_ADDR_TYPE"] == "dhcp"


def test_list_answer_that_is_the_display_label_names_the_key():
    """DHCP is the label. The playbook has to send the key dhcp.

    The platform's own 422 names the question by its display text and does
    not list the keys, so the local refusal has to.
    """
    out = resolve_answers(
        [q("YB_IP_ADDR_TYPE", "list", default="dhcp",
           display="Select the IP Address Type", list=IP_CHOICES)],
        {"YB_IP_ADDR_TYPE": "DHCP"})
    assert out["errors"]
    msg = " ".join(out["errors"])
    assert "YB_IP_ADDR_TYPE" in msg
    assert "valid: dhcp=DHCP, static=Static" in msg
    assert "'DHCP' is the label for key 'dhcp'" in msg
    assert "YB_IP_ADDR_TYPE" not in out["answers"]


def test_bogus_list_answer_lists_the_choices_and_no_key_hint():
    out = resolve_answers(
        [q("YB_IP_ADDR_TYPE", "list", default="dhcp", list=IP_CHOICES)],
        {"YB_IP_ADDR_TYPE": "bogus"})
    assert out["errors"]
    msg = " ".join(out["errors"])
    assert "not a valid choice" in msg
    assert "valid: dhcp=DHCP, static=Static" in msg
    assert "label for key" not in msg
    assert "YB_IP_ADDR_TYPE" not in out["answers"]


def test_list_choices_given_as_a_json_string_are_checked():
    """The question column can come back as a JSON string rather than a dict."""
    out = resolve_answers(
        [q("YB_IP_ADDR_TYPE", "list", default="dhcp",
           list='{"dhcp": "DHCP", "static": "Static"}')],
        {"YB_IP_ADDR_TYPE": "Static"})
    msg = " ".join(out["errors"])
    assert "valid: dhcp=DHCP, static=Static" in msg
    assert "'Static' is the label for key 'static'" in msg


def test_missing_required_list_answer_lists_the_choices():
    out = resolve_answers(
        [q("YB_IP_ADDR_TYPE", "list", required=True, default="",
           list=IP_CHOICES)], {})
    assert any("valid: dhcp=DHCP, static=Static" in e for e in out["errors"])


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


# ── no_log narrowing (B20) ───────────────────────────────────────────────────
#
# ansible-core masks a no_log value by replacing it as a plain SUBSTRING
# everywhere in a module's return data, and it treats integers as values. So
# YB_CPU_CORES: 1 masked every "1" the deploy module printed, including the
# digits inside the recipe's own constraints. These cover the pure half of the
# fix; narrow_no_log() in the module wires them to module.no_log_values.

def test_maskable_strings_matches_what_ansible_would_collect():
    assert maskable_strings("secret") == {"secret"}
    assert maskable_strings(1) == {"1"}
    assert maskable_strings(2.5) == {"2.5"}
    # Booleans and None are skipped by ansible-core, and bool must be checked
    # before int because bool IS an int.
    assert maskable_strings(True) == set()
    assert maskable_strings(None) == set()
    # An empty string is not masked -- masking it would replace the gap
    # between every pair of characters.
    assert maskable_strings("") == set()


def test_maskable_strings_walks_containers():
    assert maskable_strings({"a": 1, "b": ["x", {"c": 2}]}) == {"1", "x", "2"}


def test_ordinary_numeric_answers_become_unmaskable():
    out = unmaskable_answer_strings(
        {"YB_CPU_CORES": 1, "YB_RAM": 2048, "PASSWORD": "hunter2"},
        ["PASSWORD"])
    assert out == {"1", "2048"}


def test_a_credential_answer_is_never_unmasked():
    out = unmaskable_answer_strings({"PASSWORD": "hunter2"}, ["PASSWORD"])
    assert out == set()


def test_a_value_shared_with_a_credential_stays_masked():
    """The whole point: masking must win on a collision, not last-write."""
    out = unmaskable_answer_strings(
        {"YB_CPU_CORES": 1, "PASSWORD": "1"}, ["PASSWORD"])
    assert out == set()


def test_no_secret_vars_means_every_answer_is_unmaskable():
    out = unmaskable_answer_strings({"YB_RAM": 2048}, [])
    assert out == {"2048"}


def test_secret_vars_naming_an_absent_answer_is_harmless():
    out = unmaskable_answer_strings({"YB_RAM": 2048}, ["PASSWORD"])
    assert out == {"2048"}


def test_empty_inputs_do_not_explode():
    assert unmaskable_answer_strings(None, None) == set()
    assert unmaskable_answer_strings({}, []) == set()
