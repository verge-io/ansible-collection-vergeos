"""Unit tests for the recipe answer resolver (pure functions, no API)."""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    answers_retained_in_no_log,
    credential_answer_concealed,
    drive_tier_has_no_question,
    fill_recipe_answers,
    maskable_strings,
    recipe_not_applicable,
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
    assert "(got 'DHCP')" in msg
    assert "valid: dhcp=DHCP, static=Static" in msg
    assert "'DHCP' is the label for key 'dhcp'" in msg
    assert "YAML turned the unquoted word" not in msg
    assert "YB_IP_ADDR_TYPE" not in out["answers"]


def test_bogus_list_answer_lists_the_choices_and_no_key_hint():
    out = resolve_answers(
        [q("YB_IP_ADDR_TYPE", "list", default="dhcp", list=IP_CHOICES)],
        {"YB_IP_ADDR_TYPE": "bogus"})
    assert out["errors"]
    msg = " ".join(out["errors"])
    assert "not a valid choice" in msg
    assert "(got 'bogus')" in msg
    assert "valid: dhcp=DHCP, static=Static" in msg
    assert "label for key" not in msg
    assert "YAML turned the unquoted word" not in msg
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


CDROM_CHOICES = {
    "yes": "Use Local Media Image",
    "no": "No",
    "netboot": "Netboot Bootloader",
}


def _cdrom_question():
    return q("SELECT_CREATE_CDROM", "list", default="yes",
             display="Create a CD-Rom Drive", list=CDROM_CHOICES)


@pytest.mark.parametrize("given,example", [(False, "no"), (True, "yes")])
def test_unquoted_yaml_bool_on_a_yes_no_list_is_refused_with_a_quoting_hint(
        given, example):
    """An unquoted no/yes is a boolean by the time it arrives.

    SELECT_CREATE_CDROM keys are the strings yes, no and netboot. The
    boolean is refused, the message shows the value received (the same
    "(got ...)" form as a bool question), and it says quoting the string
    fixes the YAML conversion. The bool is not rewritten to "no" or "yes".
    """
    out = resolve_answers([_cdrom_question()], {"SELECT_CREATE_CDROM": given})
    assert out["errors"], "%r was accepted for a yes/no list" % given
    assert "SELECT_CREATE_CDROM" not in out["answers"]
    assert out["answers"].get("SELECT_CREATE_CDROM") != example
    msg = " ".join(out["errors"])
    assert "not a valid choice" in msg
    assert "(got %r)" % given in msg
    assert ("valid: yes=Use Local Media Image, no=No, "
            "netboot=Netboot Bootloader") in msg
    assert "YAML turned the unquoted word into a boolean" in msg
    assert 'quote the string (for example "%s")' % example in msg


@pytest.mark.parametrize("given", ["no", "yes", "netboot"])
def test_quoted_cdrom_choice_is_accepted(given):
    """The string "no" is the answer the recipe wants. Quoting is the fix."""
    out = resolve_answers([_cdrom_question()], {"SELECT_CREATE_CDROM": given})
    assert out["errors"] == []
    assert out["answers"]["SELECT_CREATE_CDROM"] == given


@pytest.mark.parametrize("given", ["maybe", "False", "enabled"])
def test_invalid_cdrom_strings_name_the_value_without_a_yaml_hint(given):
    """A string that is not a key is still refused, and it is not a boolean.

    The YAML-quoting hint is only for a real bool against yes/no-style keys.
    The string "False" is not that bool.
    """
    out = resolve_answers([_cdrom_question()], {"SELECT_CREATE_CDROM": given})
    assert out["errors"]
    assert "SELECT_CREATE_CDROM" not in out["answers"]
    msg = " ".join(out["errors"])
    assert "not a valid choice" in msg
    assert "(got %r)" % given in msg
    assert "YAML turned the unquoted word" not in msg


@pytest.mark.parametrize("choices,given,example", [
    ({"true": "Yes", "false": "No"}, False, "false"),
    ({"true": "Yes", "false": "No"}, True, "true"),
    ({"on": "On", "off": "Off"}, False, "off"),
    ({"on": "On", "off": "Off"}, True, "on"),
    ({"Yes": "Y", "No": "N"}, False, "No"),
    ({"Yes": "Y", "No": "N"}, True, "Yes"),
])
def test_yaml_quoting_hint_covers_true_false_on_off_and_key_case(
        choices, given, example):
    out = resolve_answers(
        [q("P", "list", default="", list=choices)],
        {"P": given})
    assert out["errors"]
    assert "P" not in out["answers"]
    msg = " ".join(out["errors"])
    assert "(got %r)" % given in msg
    assert "YAML turned the unquoted word into a boolean" in msg
    assert 'quote the string (for example "%s")' % example in msg


def test_a_bool_against_list_keys_yaml_would_not_rewrite_has_no_quoting_hint():
    """dhcp/static has no yes/no key, so a boolean is refused without the hint."""
    out = resolve_answers(
        [q("YB_IP_ADDR_TYPE", "list", default="dhcp", list=IP_CHOICES)],
        {"YB_IP_ADDR_TYPE": False})
    assert out["errors"]
    assert "YB_IP_ADDR_TYPE" not in out["answers"]
    msg = " ".join(out["errors"])
    assert "not a valid choice" in msg
    assert "(got False)" in msg
    assert "YAML turned the unquoted word" not in msg


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


def test_retained_no_log_names_are_only_values_still_held():
    held = {"fuzz", "secret"}
    names = answers_retained_in_no_log(
        {"ZZ_SECRET": "fuzz", "ZZ_HOSTNAME": "zz-fuzz-host",
         "YB_CPU_CORES": 1, "ZZ_NOTE": ""},
        held)
    assert names == ["ZZ_SECRET"]


def test_retained_no_log_names_drop_a_value_removed_from_the_set():
    """Classification off removes the password string. The name goes with it."""
    names = answers_retained_in_no_log(
        {"ZZ_SECRET": "fuzz", "ZZ_HOSTNAME": "zz-fuzz-host"},
        set())
    assert names == []


def test_a_value_shared_with_another_no_log_parameter_stays_listed():
    names = answers_retained_in_no_log(
        {"YB_CPU_CORES": 1, "ZZ_SECRET": "1"}, {"1"})
    assert names == ["YB_CPU_CORES", "ZZ_SECRET"]


def _refusal(no_log_answers, msg=None):
    """The ZZ_RAM_MB refusal. It quotes the recipe and not the password."""
    return {
        "msg": msg or (
            "the answer set is not valid for recipe 'zz-fuzz-recipe': "
            "answer 'ZZ_RAM_MB' is 256; the recipe requires at least 512"),
        "recipe": {
            "name": "zz-fuzz-recipe",
            "description": "fuzz ladder recipe, for the no_log check",
            "catalog_display": "zz-fuzz-cat",
        },
        "answers_sent": ["YB_CPU_CORES", "ZZ_HOSTNAME", "ZZ_RAM_MB", "ZZ_SECRET"],
        "hints": [],
        "no_log_answers": no_log_answers,
    }


def test_recipe_name_alone_does_not_conceal_a_password():
    """The live rung used to pass on this result. The password was not no_log."""
    result = _refusal([])
    assert "zz-fuzz-recipe" in result["msg"]
    assert "fuzz" in result["msg"]
    assert credential_answer_concealed(result, "ZZ_SECRET", "fuzz") is False


def test_a_password_kept_no_log_is_concealed_when_the_recipe_name_matches():
    result = _refusal(["ZZ_SECRET"])
    assert credential_answer_concealed(result, "ZZ_SECRET", "fuzz") is True
    assert credential_answer_concealed(result, "ZZ_NOTE", "fuzz") is False


def test_an_echoed_password_is_not_concealed_even_when_listed():
    result = _refusal(
        ["ZZ_SECRET"],
        msg=("the answer set is not valid for recipe 'zz-fuzz-recipe': "
             "refusing fuzz"),
    )
    assert credential_answer_concealed(result, "ZZ_SECRET", "fuzz") is False


def test_a_rewritten_result_with_the_name_still_listed_is_concealed():
    """Cores that rewrite the return replace the marker. The list still decides."""
    result = _refusal(["ZZ_SECRET"])
    result["msg"] = result["msg"].replace("fuzz", "********")
    result["recipe"]["name"] = "zz-********-recipe"
    result["recipe"]["description"] = "******** ladder recipe, for the no_log check"
    assert "********" in result["msg"]
    assert credential_answer_concealed(result, "ZZ_SECRET", "fuzz") is True


def test_a_missing_no_log_list_is_not_concealed():
    result = _refusal(["ZZ_SECRET"])
    del result["no_log_answers"]
    assert credential_answer_concealed(result, "ZZ_SECRET", "fuzz") is False


def test_an_empty_password_is_not_concealed():
    result = _refusal(["ZZ_SECRET"])
    assert credential_answer_concealed(result, "ZZ_SECRET", "") is False


# ── matrix answer fill ───────────────────────────────────────────────────────

def test_required_secrets_are_named_and_not_invented():
    questions = [
        q("MINIO_ADMIN_PASSWORD", "password", required=True, default=""),
        q("CLUSTER_SECRET", "string", required=True, default=""),
        q("VPNPASS", "string", required=True, default=""),
        q("HOSTNAME", required=True, default=""),
    ]
    out = fill_recipe_answers(questions, {"HOSTNAME": "zz-mx"})
    assert out["plain"] == {"HOSTNAME": "zz-mx"}
    assert out["secrets"] == {}
    assert out["unanswered_secrets"] == [
        "CLUSTER_SECRET", "MINIO_ADMIN_PASSWORD", "VPNPASS"]


@pytest.mark.parametrize("default", [
    "VALUE_SPECIFIED_IN_NO_LOG_PARAMETER",
    "$REDACTED$",
    "********",
    "published-default",
])
def test_a_required_credential_default_is_not_copied(default):
    """vm_recipe_info redacts a credential default to the no_log token.

    Posting that token would install it. A real published default stays on
    the platform: the filler does not copy that either.
    """
    questions = [q("MINIO_ADMIN_PASSWORD", "password", required=True,
                   default=default)]
    out = fill_recipe_answers(questions, {})
    assert out["secrets"] == {}
    assert out["plain"] == {}
    assert default not in out["secrets"].values()
    assert out["unanswered_secrets"] == ["MINIO_ADMIN_PASSWORD"]


def test_a_supplied_secret_is_kept():
    questions = [q("PASSWORD", "password", required=True, default="")]
    out = fill_recipe_answers(questions, {"PASSWORD": "caller-secret"})
    assert out["secrets"] == {"PASSWORD": "caller-secret"}
    assert out["unanswered_secrets"] == []
    assert out["plain"] == {}


def test_a_supplied_secret_wins_over_a_redacted_default():
    questions = [q("PASSWORD", "password", required=True,
                   default="VALUE_SPECIFIED_IN_NO_LOG_PARAMETER")]
    out = fill_recipe_answers(questions, {"PASSWORD": "caller-secret"})
    assert out["secrets"] == {"PASSWORD": "caller-secret"}
    assert out["unanswered_secrets"] == []


def test_a_no_log_placeholder_is_not_sent_as_a_plain_default():
    questions = [q("HOSTNAME", "string", required=True,
                   default="VALUE_SPECIFIED_IN_NO_LOG_PARAMETER")]
    out = fill_recipe_answers(questions, {})
    assert out["plain"]["HOSTNAME"] != "VALUE_SPECIFIED_IN_NO_LOG_PARAMETER"
    assert out["secrets"] == {}


def test_required_bool_and_list_are_filled_from_the_question():
    questions = [
        q("REOPEN", "bool", required=True, default="", display="Reopen File During Writes"),
        q("YB_IP_ADDR_TYPE", "list", required=True, default="",
          list={"dhcp": "DHCP", "static": "Static"}),
    ]
    out = fill_recipe_answers(questions, {})
    assert out["secrets"] == {}
    assert out["plain"]["REOPEN"] is False
    assert out["plain"]["YB_IP_ADDR_TYPE"] == "dhcp"


def test_required_table_question_uses_the_first_option():
    questions = [q("SELECT_OS_TIER", "row", required=True, default="",
                   table="cluster_tiers")]
    out = fill_recipe_answers(
        questions, {}, {"SELECT_OS_TIER": [{"$key": 4, "$display": "4"}]})
    assert out["plain"]["SELECT_OS_TIER"] == 4
    assert out["secrets"] == {}


def test_table_question_with_no_rows_is_left_unset():
    questions = [q("WINDOWS_ISO", "list", required=True, default="",
                   table="files")]
    out = fill_recipe_answers(questions, {}, {"WINDOWS_ISO": []})
    assert "WINDOWS_ISO" not in out["plain"]
    assert out["secrets"] == {}


def test_supplied_numeric_below_the_question_minimum_is_raised():
    questions = [q("YB_RAM", "ram", default="1024", min=16384)]
    out = fill_recipe_answers(questions, {"YB_RAM": 1024, "HOSTNAME": "zz-mx"})
    assert out["plain"]["YB_RAM"] == 16384
    assert out["plain"]["HOSTNAME"] == "zz-mx"


def test_numeric_default_below_the_minimum_is_sent_as_the_minimum():
    """The matrix did not mention this question. The published default is too small."""
    questions = [q("YB_RAM", "ram", required=False, default="1024", min=16384)]
    out = fill_recipe_answers(questions, {})
    assert out["plain"]["YB_RAM"] == 16384


def test_numeric_at_or_above_the_minimum_is_left_alone():
    questions = [q("YB_RAM", "ram", default="1024", min=2048)]
    out = fill_recipe_answers(questions, {"YB_RAM": 4096})
    assert out["plain"]["YB_RAM"] == 4096


def test_timezone_question_with_a_zero_default_is_utc():
    questions = [q("VM_TIMEZONE", "string", required=False, default="0")]
    out = fill_recipe_answers(questions, {})
    assert out["plain"]["VM_TIMEZONE"] == "UTC"
    assert out["secrets"] == {}


def test_supplied_timezone_of_zero_is_replaced():
    questions = [q("VM_TIMEZONE", "string", default="America/New_York")]
    out = fill_recipe_answers(questions, {"VM_TIMEZONE": 0})
    assert out["plain"]["VM_TIMEZONE"] == "UTC"


def test_a_real_timezone_answer_is_kept():
    questions = [q("YB_TIMEZONE", "string", default="0")]
    out = fill_recipe_answers(questions, {"YB_TIMEZONE": "America/New_York"})
    assert out["plain"]["YB_TIMEZONE"] == "America/New_York"


def test_api_check_step_is_not_invented_when_it_is_not_a_question():
    """API_CHECK_VW is a recipe command, not an answer, unless the recipe says so."""
    questions = [q("HOSTNAME", required=False, default="host")]
    out = fill_recipe_answers(questions, {"HOSTNAME": "zz-mx"})
    assert "API_CHECK_VW" not in out["plain"]
    assert "API_CHECK_VW" not in out["secrets"]


def test_internal_questions_are_not_filled():
    questions = [q("API_CHECK_VW", "database_find", required=True, default="")]
    out = fill_recipe_answers(questions, {})
    assert out["plain"] == {}
    assert out["secrets"] == {}
    assert out["unfilled"] == []


def test_a_supplied_internal_answer_is_kept_and_posted():
    """VM_TIMEZONE is often an internal question. The override must survive."""
    questions = [q("VM_TIMEZONE", "database_find", required=False,
                   default="settings/timezone")]
    filled = fill_recipe_answers(questions, {"VM_TIMEZONE": "UTC"})
    assert filled["plain"]["VM_TIMEZONE"] == "UTC"
    posted = resolve_answers(questions, {"VM_TIMEZONE": "UTC"})
    assert posted["answers"]["VM_TIMEZONE"] == "UTC"
    assert "VM_TIMEZONE" not in posted["pruned"]


def test_timezone_settings_default_is_replaced_with_utc():
    questions = [q("VM_TIMEZONE", "string", required=False,
                   default="settings/timezone")]
    out = fill_recipe_answers(questions, {})
    assert out["plain"]["VM_TIMEZONE"] == "UTC"


def test_required_question_with_a_concrete_default_is_sent():
    questions = [
        q("MINIO_USER", "string", required=True, default="minio",
          display="Minio System User"),
        q("INSTANCES", "num", required=True, default="2", display="Instances"),
    ]
    out = fill_recipe_answers(questions, {})
    assert out["plain"]["MINIO_USER"] == "minio"
    assert out["plain"]["INSTANCES"] == 2


def test_required_list_uses_the_first_choice_when_the_default_is_not_one():
    questions = [q("PICK", "list", required=True, default="settings/users",
                   list={"a": "A", "b": "B"})]
    out = fill_recipe_answers(questions, {})
    assert out["plain"]["PICK"] == "a"
    assert out["unfilled"] == []


def test_required_iso_with_no_choices_is_unfilled():
    questions = [q("INSTALL_ISO", "list", required=True, default="",
                   table="files", display="Install ISO")]
    out = fill_recipe_answers(questions, {}, {"INSTALL_ISO": []})
    assert "INSTALL_ISO" not in out["plain"]
    assert out["unfilled"] == [{
        "name": "INSTALL_ISO",
        "display": "Install ISO",
        "reason": "no existing ISO for required question 'Install ISO'",
    }]


def test_required_iso_download_url_is_not_sent():
    """A download URL is not a file on this system.

    The platform rejected that send for Install ISO. A caller can still
    pass the URL, which is how the Windows recipes name an image.
    """
    questions = [q("INSTALL_ISO", "list", required=True,
                   default="https://example.invalid/vergeos.iso",
                   table="files", display="Install ISO")]
    out = fill_recipe_answers(questions, {}, {"INSTALL_ISO": []})
    assert "INSTALL_ISO" not in out["plain"]
    assert out["unfilled"][0]["reason"].startswith("no existing ISO")
    supplied = fill_recipe_answers(
        questions, {"INSTALL_ISO": "https://example.invalid/vergeos.iso"},
        {"INSTALL_ISO": []})
    assert supplied["plain"]["INSTALL_ISO"] == "https://example.invalid/vergeos.iso"
    assert supplied["unfilled"] == []


def test_install_iso_uses_an_iso_file_not_another_row():
    questions = [q("INSTALL_ISO", "list", required=True, default="",
                   table="files", display="Install ISO")]
    options = {"INSTALL_ISO": [
        {"$key": 3, "name": "disk.raw", "type": "raw"},
        {"$key": 9, "name": "vergeos.iso", "$display": "vergeos.iso",
         "type": "iso"},
    ]}
    out = fill_recipe_answers(questions, {}, options)
    assert out["plain"]["INSTALL_ISO"] == 9
    assert out["unfilled"] == []


def test_install_iso_uses_a_read_only_file_listing():
    """No option rows. The check-mode matrix passes file_info's ISO list."""
    questions = [q("INSTALL_ISO", "list", required=True, default="",
                   table="files", display="Install ISO")]
    files = [{"$key": 12, "name": "verge-io-install.iso", "type": "iso"}]
    out = fill_recipe_answers(questions, {}, {"INSTALL_ISO": []}, files)
    assert out["plain"]["INSTALL_ISO"] == 12
    assert out["unfilled"] == []


def test_install_iso_url_choices_ignore_the_default_and_the_local_file():
    """VergeOS Install ISO publishes download URLs as its own list.

    The default points at marketplace.vergeos.com and is not one of those
    choices. A file from file_info is not one either. The answer is the
    first https choice. Nothing is downloaded.
    """
    first = "https://updates.vergeos.com/public/verge-io-install-26.2.iso"
    second = "https://updates.vergeos.com/public/verge-io-install-26.3.iso"
    questions = [q(
        "INSTALL_ISO", "list", required=True, display="Install ISO",
        table="files",
        default="https://marketplace.vergeos.com/public/verge-io-install.iso",
        list={first: "VergeOS 26.2", second: "VergeOS 26.3"},
    )]
    files = [{"$key": 12, "name": "verge-io-install.iso", "type": "iso"}]
    out = fill_recipe_answers(questions, {}, {"INSTALL_ISO": []}, files)
    assert out["plain"]["INSTALL_ISO"] == first
    assert out["unfilled"] == []
    assert 12 not in out["plain"].values()


def test_install_iso_uses_the_default_when_it_is_a_list_choice():
    first = "https://updates.vergeos.com/public/verge-io-install-26.2.iso"
    chosen = "https://updates.vergeos.com/public/verge-io-install-26.3.iso"
    questions = [q(
        "INSTALL_ISO", "list", required=True, display="Install ISO",
        default=chosen,
        list={first: "VergeOS 26.2", chosen: "VergeOS 26.3"},
    )]
    files = [{"$key": 12, "name": "verge-io-install.iso", "type": "iso"}]
    out = fill_recipe_answers(questions, {}, {}, files)
    assert out["plain"]["INSTALL_ISO"] == chosen


def test_install_iso_list_without_an_https_choice_stays_unfilled():
    questions = [q(
        "INSTALL_ISO", "list", required=True, display="Install ISO",
        default="https://marketplace.vergeos.com/not-a-choice.iso",
        list={"local": "not a url"},
    )]
    files = [{"$key": 12, "name": "verge-io-install.iso", "type": "iso"}]
    out = fill_recipe_answers(questions, {}, {}, files)
    assert "INSTALL_ISO" not in out["plain"]
    assert out["unfilled"][0]["reason"] == (
        "no usable choice for required question 'Install ISO'")


def test_install_iso_prefers_the_question_options_over_the_listing():
    questions = [q("INSTALL_ISO", "list", required=True, default="",
                   table="files", display="Install ISO")]
    options = {"INSTALL_ISO": [
        {"$key": 4, "name": "recipe.iso", "type": "iso"},
    ]}
    files = [{"$key": 9, "name": "other.iso", "type": "iso"}]
    out = fill_recipe_answers(questions, {}, options, files)
    assert out["plain"]["INSTALL_ISO"] == 4


def test_blocksize_default_is_a_valid_disksize():
    """Filesystem Performance Test publishes BLOCKSIZE 65536.

    disksize is bytes. The 1 MB floor refuses a drive size of 50, which
    is fifty bytes. It was wrong for this question: 65536 is 64 KiB, the
    block size the recipe itself publishes. The filler already sent that
    default. The validator now accepts it.
    """
    question = q("BLOCKSIZE", "disksize", required=True, default="65536",
                 min=512, max=0, display="Block Size")
    filled = fill_recipe_answers([question], {})
    assert filled["plain"]["BLOCKSIZE"] == 65536
    out = resolve_answers([question], filled["plain"])
    assert out["errors"] == [], " ".join(out["errors"])
    assert out["answers"]["BLOCKSIZE"] == 65536


def test_optional_tier_question_uses_its_own_choices():
    """SELECT_OS_TIER is optional and its default is empty.

    Leaving it unset makes the drive step send preferred_tier ''.
    """
    questions = [q("SELECT_OS_TIER", "row", required=False, default="",
                   table="cluster_tiers", display="OS Drive Tier")]
    options = {"SELECT_OS_TIER": [
        {"$key": 1, "$display": "1"},
        {"$key": 4, "$display": "4"},
    ]}
    out = fill_recipe_answers(questions, {}, options, None, [4])
    assert out["plain"]["SELECT_OS_TIER"] == 1
    assert out["unfilled"] == []


def test_tier_question_without_choices_uses_a_tier_that_exists():
    questions = [q("YB_DRIVE_TIER", "num", required=False, default="",
                   display="Drive Tier")]
    out = fill_recipe_answers(questions, {}, {}, None, [1, 4])
    assert out["plain"]["YB_DRIVE_TIER"] == 1


def test_hidden_tier_question_is_answered():
    questions = [q("SELECT_DATA_TIER", "hidden", required=False, default="",
                   section_name="$database", table="storage_tiers",
                   display="Data Tier")]
    options = {"SELECT_DATA_TIER": [{"$key": 2, "$display": "2"}]}
    out = fill_recipe_answers(questions, {}, options)
    assert out["plain"]["SELECT_DATA_TIER"] == 2


def test_a_supplied_tier_is_kept():
    questions = [q("SELECT_OS_TIER", "row", required=False, default="",
                   table="cluster_tiers")]
    out = fill_recipe_answers(
        questions, {"SELECT_OS_TIER": 1},
        {"SELECT_OS_TIER": [{"$key": 4, "$display": "4"}]}, None, [4])
    assert out["plain"]["SELECT_OS_TIER"] == 1


def test_drive_create_command_is_not_a_tier_question():
    """CREATE_TIER0_DRIVE_1 is the step that reads preferred_tier."""
    questions = [q("CREATE_TIER0_DRIVE_1", "database_create", required=False,
                   default="", section_name="$database")]
    out = fill_recipe_answers(questions, {}, {}, None, [1])
    assert out["plain"] == {}
    assert out["unfilled"] == []
    message = ("Error executing API command 'CREATE_TIER0_DRIVE_1': "
               "Error creating file for new drive: value '' is not in list "
               "for field 'preferred_tier'")
    assert drive_tier_has_no_question(questions, message) is True
    assert drive_tier_has_no_question(
        [q("SELECT_OS_TIER", "row", table="cluster_tiers")], message) is False


def test_tier_question_with_no_tier_anywhere_is_unfilled():
    questions = [q("SELECT_OS_TIER", "row", required=False, default="",
                   table="cluster_tiers", display="OS Drive Tier")]
    out = fill_recipe_answers(questions, {}, {"SELECT_OS_TIER": []}, None, [])
    assert "SELECT_OS_TIER" not in out["plain"]
    assert out["unfilled"][0]["reason"] == (
        "no storage tier for question 'OS Drive Tier'")


def test_vergeos_shaped_answers_pass_the_module_validator():
    """List default ``0`` stays ``0`` when that choice exists."""
    iso = "https://updates.vergeos.com/public/verge-io-install-26.2.iso"
    drive_counts = {"0": "-- None --", "1": "1", "2": "2", "3": "3", "4": "4"}
    questions = [
        q("HOSTNAME", "hostname", required=True, default="!!",
          regex="[a-z][a-z0-9-]*"),
        q("USER", required=True, default=""),
        q("PASSWORD", "password", required=True, default=""),
        q("YB_CPU_CORES", "num", required=False, default="1", min=1),
        q("YB_RAM", "ram", required=False, default="512", min=1024),
        q("YB_DRIVE_OS_SIZE", "disksize", required=True, default="", min=0),
        q("BLOCKSIZE", "disksize", required=True, default="65536", min=512,
          max=0),
        q("INSTALL_ISO", "list", required=True, display="Install ISO",
          default="https://marketplace.vergeos.com/nope.iso",
          list={iso: "26.2",
                "https://updates.vergeos.com/public/verge-io-install-26.3.iso": "26.3"}),
        q("ISO_NAME", required=False, default=""),
        q("TIER0_DRIVE_TIER", "list", required=False, default="1",
          table="cluster_tiers", list=drive_counts),
        q("TIER1_DRIVE_TIER", "list", required=False, default="1",
          table="cluster_tiers", list=drive_counts),
        q("SWAP_TIER", "list", required=False, default="0",
          table="cluster_tiers", display="Swap Tier", list=drive_counts),
        q("TIER2_DRIVES", "list", required=False, default="0",
          table="cluster_tiers", display="Drives", list=drive_counts),
        q("TIER3_DRIVES", "list", required=False, default="0",
          table="cluster_tiers", display="Drives", list=drive_counts),
        q("TIER4_DRIVES", "list", required=False, default="0",
          table="cluster_tiers", display="Drives", list=drive_counts),
        q("TIER5_DRIVES", "list", required=False, default="0",
          table="cluster_tiers", display="Drives", list=drive_counts),
        q("REOPEN", "bool", required=False, default=""),
        q("ASSET", "string", required=True, default="***", regex="[A-Za-z0-9]+",
          min=2),
        q("YB_IP_ADDR_TYPE", "list", required=False, default="static",
          list={"dhcp": "DHCP", "static": "Static"}),
        q("CREATE_TIER0_DRIVE_1", "database_create", section_name="$database"),
    ]
    keep_zero = ["SWAP_TIER", "TIER2_DRIVES", "TIER3_DRIVES",
                 "TIER4_DRIVES", "TIER5_DRIVES"]
    keep_one = ["TIER0_DRIVE_TIER", "TIER1_DRIVE_TIER"]
    options = {name: [{"$key": 1, "$display": "1"}]
               for name in keep_zero + keep_one}
    filled = fill_recipe_answers(
        questions,
        {"HOSTNAME": "zz-mx", "USER": "ladder", "PASSWORD": "secret"},
        options, None, [1])
    posted = dict(filled["plain"])
    posted.update(filled["secrets"])
    assert posted["PASSWORD"] == "secret"
    assert posted["INSTALL_ISO"] == iso
    assert posted["YB_RAM"] == 1024
    assert posted["YB_DRIVE_OS_SIZE"] == 1048576
    assert posted["BLOCKSIZE"] == 65536
    assert posted["ASSET"] != "***"
    for name in keep_zero:
        assert posted.get(name, "0") in (0, "0"), name
        assert posted.get(name) != 1
    for name in keep_one:
        assert posted.get(name, "1") in (1, "1"), name
    assert "CREATE_TIER0_DRIVE_1" not in posted
    out = resolve_answers(questions, posted, options=options)
    assert out["errors"] == [], " ".join(out["errors"])


def test_zero_choice_is_kept_and_default_zero_is_not_replaced():
    choices = {"0": "-- None --", "1": "1", "2": "2", "3": "3", "4": "4"}
    question = q("TIER2_DRIVES", "list", required=False, default="0",
                 table="cluster_tiers", display="Drives", list=choices)
    options = {"TIER2_DRIVES": [{"$key": 1, "$display": "1"},
                                {"$key": 0, "$display": "-- None --"}]}
    filled = fill_recipe_answers([question], {}, options, None, [1])
    assert filled["plain"].get("TIER2_DRIVES", "0") in (0, "0")
    assert filled["plain"].get("TIER2_DRIVES") != 1
    for answer in (0, "0"):
        out = resolve_answers([question], {"TIER2_DRIVES": answer},
                              options=options)
        assert out["errors"] == [], " ".join(out["errors"])
        assert out["answers"]["TIER2_DRIVES"] == answer


def test_optional_list_with_a_valid_default_is_not_replaced():
    questions = [q("SWAP_TIER", "list", required=False, default="1",
                   table="cluster_tiers")]
    options = {"SWAP_TIER": [{"$key": 1, "$display": "1"}]}
    out = fill_recipe_answers(questions, {}, options)
    assert "SWAP_TIER" not in out["plain"]


def test_drive_size_of_fifty_is_still_refused():
    """The 1 MB floor stays for a drive whose own bounds are not that small."""
    question = q("YB_DRIVE_OS_SIZE", "disksize", required=True, default="0",
                 min=0, max=0)
    out = resolve_answers([question], {"YB_DRIVE_OS_SIZE": 50})
    assert out["errors"]
    assert "bytes" in " ".join(out["errors"])
    filled = fill_recipe_answers([question], {})
    assert filled["plain"]["YB_DRIVE_OS_SIZE"] == 1048576
    accepted = resolve_answers([question], filled["plain"])
    assert accepted["errors"] == []


def test_optional_empty_question_is_not_filled():
    questions = [q("SSH_KEY", required=False, default="")]
    out = fill_recipe_answers(questions, {})
    assert out["plain"] == {}


def test_recipe_not_downloaded_is_not_applicable():
    assert recipe_not_applicable({"name": "Gitlab", "downloaded": False}) is True
    assert recipe_not_applicable({"name": "Gitlab", "downloaded": 0}) is True
    assert recipe_not_applicable({"name": "Ubuntu", "downloaded": True}) is False
    assert recipe_not_applicable({"name": "Local"}) is False


def test_simulate_not_downloaded_message_is_not_applicable():
    msg = ("the simulate POST refused the answer set: HTTP 422 -- "
           "Recipe is not downloaded or published")
    assert recipe_not_applicable(msg) is True
    assert recipe_not_applicable({"downloaded": True}, message=msg) is True
    assert recipe_not_applicable("answer 'YB_RAM' is 1024") is False
