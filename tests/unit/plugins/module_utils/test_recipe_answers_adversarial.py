"""Adversarial cases for the recipe_deploy answer resolver.

Separate from test_recipe_answers.py, which pins the intended behaviour.
This file is the hostile-input sweep: junk types, boundary numbers, name
shadowing, injection-shaped strings and duplicate rows. Several of these
found real defects; each such test names the defect in its docstring.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    NEW_INTERNAL,
    resolve_answers,
    scan_simulate,
)


def q(name, qtype="string", **kw):
    row = {"name": name, "type": qtype, "required": False, "default": "x"}
    row.update(kw)
    return row


def errs(out):
    return " | ".join(out["errors"])


# -- numeric answers that are not numbers ------------------------------------

@pytest.mark.parametrize("junk", [
    "4096MB", "4 096", "1e3", "0x400", "1024.0", "  ", "one thousand",
    "1024;reboot", "²⁰⁴⁸", "1024½",
])
def test_non_integer_answers_to_numeric_questions_are_refused(junk):
    """A numeric question given a non-integer must not reach the API.

    Defect found: _coerce returned the string unchanged and check_constraints
    only inspected str for string-ish types and int for numeric ones, so a
    junk string matched neither branch and passed local validation whole.
    """
    out = resolve_answers([q("YB_RAM", "ram", min="1024", max="65536")],
                          {"YB_RAM": junk})
    assert out["errors"], "%r passed validation for a ram question" % junk
    assert "YB_RAM" in errs(out)


def test_numeric_looking_characters_that_int_refuses_do_not_crash():
    """Defect found: _is_int tested str.isdigit(), which is True for
    superscripts and vulgar fractions that int() then rejects — so "2**2" was
    accepted as an integer and raised ValueError in _coerce one line later,
    surfacing as a resolver traceback rather than a rejected answer."""
    out = resolve_answers([q("N", "num")], {"N": "²"})
    assert out["errors"]


def test_unicode_decimal_digits_convert_rather_than_being_refused():
    """Arabic-Indic digits are unambiguous decimals and int() reads them, so
    they are accepted and normalised — the check is that they become the
    integer 4096, not that they survive as a string."""
    out = resolve_answers([q("YB_RAM", "ram", min="1024")],
                          {"YB_RAM": "٤٠٩٦"})
    assert out["errors"] == []
    assert out["answers"]["YB_RAM"] == 4096


@pytest.mark.parametrize("qtype", ["num", "ram", "disksize", "seconds"])
def test_float_answers_to_numeric_questions_are_refused(qtype):
    out = resolve_answers([q("N", qtype)], {"N": 1024.5})
    assert out["errors"], "a float passed validation for a %s question" % qtype


def test_bool_answer_to_a_numeric_question_is_refused():
    out = resolve_answers([q("YB_RAM", "ram")], {"YB_RAM": True})
    assert out["errors"]


# -- bool answers that are not booleans --------------------------------------

@pytest.mark.parametrize("junk", [
    "enabled", "UEFI", "uefi", "Enabled", "y", "t", "n", "f",
    "2", "1.0", "onoff", "none", "null", "yesno",
])
def test_unrecognised_bool_strings_are_refused(junk):
    """A bool question given a word _coerce does not know must not reach the API.

    Defect found: _coerce returned the string unchanged and check_constraints
    had no bool branch, so "enabled" and "UEFI" passed local validation. The
    platform reads an unrecognised string as false, and the deploy reported
    success -- SELECT_CREATE_UEFI: enabled built a BIOS VM.
    """
    out = resolve_answers([q("SELECT_CREATE_UEFI", "bool")],
                          {"SELECT_CREATE_UEFI": junk})
    assert out["errors"], "%r passed validation for a bool question" % junk
    msg = errs(out)
    assert "SELECT_CREATE_UEFI" in msg
    assert "recognised boolean" in msg
    for word in ("true", "yes", "on", "1", "false", "no", "off", "0"):
        assert word in msg, "%r missing from %s" % (word, msg)
    # Failing closed: not recorded as the True the playbook meant to ask for.
    assert out["answers"].get("SELECT_CREATE_UEFI") is not True


@pytest.mark.parametrize("junk", [2, -1, 10, 1.0, 0.0, None])
def test_non_boolean_scalars_are_refused_for_bool_questions(junk):
    """Integer 0 and 1 are booleans. Every other number is not.

    Defect found: only strings were inspected, so an integer other than the
    words _coerce knew -- or a float, or null -- was forwarded untouched.
    """
    out = resolve_answers([q("B", "bool")], {"B": junk})
    assert out["errors"], "%r passed validation for a bool question" % junk
    assert "recognised boolean" in errs(out)
    assert out["answers"].get("B") is not True


def test_a_list_answer_to_a_bool_question_is_refused():
    out = resolve_answers([q("B", "bool")], {"B": [True]})
    assert out["errors"]
    assert out["answers"].get("B") is not True


def test_required_bool_given_null_names_the_accepted_values():
    """Null on a required bool must not be reported as merely empty.

    The empty-answer check used to fire first and skip the list of accepted
    values, so the operator was told the answer was empty and not what would
    have been accepted.
    """
    out = resolve_answers([q("B", "bool", required=True, default="")],
                          {"B": None})
    assert "recognised boolean" in errs(out)
    assert "supplied empty" not in errs(out)


def test_list_answer_to_a_numeric_question_is_refused():
    out = resolve_answers([q("YB_RAM", "ram")], {"YB_RAM": [1024]})
    assert out["errors"]


# -- numeric boundaries ------------------------------------------------------

@pytest.mark.parametrize("value,ok", [
    (1023, False), (1024, True), (65536, True), (65537, False),
    (0, False), (-1, False),
])
def test_numeric_bounds_are_inclusive_at_both_ends(value, ok):
    out = resolve_answers([q("YB_RAM", "ram", min="1024", max="65536")],
                          {"YB_RAM": value})
    assert (out["errors"] == []) is ok, errs(out)


def test_negative_answer_is_refused_even_with_no_min_declared():
    """A recipe with no min must still not accept a negative size."""
    out = resolve_answers([q("YB_DRIVE_OS_SIZE", "disksize")],
                          {"YB_DRIVE_OS_SIZE": -1})
    assert out["errors"]


@pytest.mark.parametrize("value", [50, 1024, 1048575])
def test_disksize_above_zero_and_under_one_megabyte_is_refused(value):
    """A disksize answer is bytes. 50 must not reach the API as fifty bytes.

    Defect found: check_constraints accepted any non-negative whole number,
    so YB_DRIVE_OS_SIZE: 50 was posted. The recipe built the OS drive at the
    image size (3 GB) and the deploy reported success.
    """
    out = resolve_answers([q("YB_DRIVE_OS_SIZE", "disksize")],
                          {"YB_DRIVE_OS_SIZE": value})
    assert out["errors"], "%r passed validation for a disksize question" % value
    msg = errs(out)
    assert "bytes" in msg
    assert "53687091200" in msg
    assert "1048576" in msg


@pytest.mark.parametrize("value", [0, 1048576, 21474836480, 53687091200])
def test_disksize_zero_and_real_byte_counts_pass(value):
    """Zero means use the recipe default. A real byte count is sent on."""
    out = resolve_answers([q("YB_DRIVE_OS_SIZE", "disksize")],
                          {"YB_DRIVE_OS_SIZE": value})
    assert out["errors"] == [], errs(out)
    assert out["answers"]["YB_DRIVE_OS_SIZE"] == value


def test_min_zero_is_honoured_and_not_treated_as_absent():
    """min='0' is falsy as a string only if you forget it is '0', not ''."""
    out = resolve_answers([q("N", "num", min="0", max="10")], {"N": -5})
    assert out["errors"]


# -- string boundaries and patterns ------------------------------------------

@pytest.mark.parametrize("qtype", ["string", "hostname", "password", "num",
                                   "ram", "disksize"])
def test_max_zero_means_no_limit_not_a_limit_of_zero(qtype):
    """Regression: tightening the bounds check to honour a declared min of 0
    also started honouring max=0, and most stock recipes ship max=0 on their
    string questions to mean 'unbounded'. Every answer to every stock recipe
    was refused as 'allows at most 0' until this was put back."""
    # 4096 bytes is under the disksize floor and would be refused for that
    # reason. A real byte count still has to pass when max=0 means unbounded.
    if qtype == "disksize":
        value = 21474836480
    elif qtype in ("num", "ram"):
        value = 4096
    else:
        value = "a fairly long answer"
    out = resolve_answers([q("A", qtype, max=0, min=0)], {"A": value})
    assert out["errors"] == [], errs(out)


def test_string_over_max_length_is_refused():
    out = resolve_answers([q("HOSTNAME", "hostname", max="15")],
                          {"HOSTNAME": "z" * 16})
    assert "at most 15" in errs(out)


def test_string_regex_is_anchored_not_searched():
    """A recipe pattern must match the whole answer, not a substring."""
    pat = r"[a-zA-Z]([a-zA-Z0-9_-]+[a-zA-Z0-9])?"
    out = resolve_answers([q("HOSTNAME", "hostname", regex=pat)],
                          {"HOSTNAME": "zz-ok!!!"})
    assert "does not match" in errs(out)


def test_an_uncompilable_recipe_regex_does_not_crash_the_resolver():
    out = resolve_answers([q("H", "hostname", regex="[unclosed")],
                          {"H": "anything"})
    assert out["answers"]["H"] == "anything"


@pytest.mark.parametrize("value", [
    "zz-$(id)", "zz-`id`", "zz-;reboot", "zz-\nHOST: evil", "zz-'; DROP--",
    "zz-\\'", 'zz-"quoted"', "zz- null",
])
def test_injection_shaped_strings_survive_as_data(value):
    """They must round-trip verbatim: quoting is the transport's job, and a
    resolver that mangled them would hide a transport bug rather than fix it."""
    out = resolve_answers([q("HOSTNAME")], {"HOSTNAME": value})
    assert out["answers"]["HOSTNAME"] == value


# -- network answers ---------------------------------------------------------

VNETS = [{"name": "External", "$key": 3}, {"name": "Internal", "$key": 4}]


def test_network_by_name_resolves_to_its_key():
    out = resolve_answers([q("NIC", "network")], {"NIC": "External"},
                          vnets=VNETS)
    assert out["answers"]["NIC"] == 3


def test_unknown_network_is_refused():
    out = resolve_answers([q("NIC", "network")], {"NIC": "Nope"}, vnets=VNETS)
    assert "not found" in errs(out)


def test_new_internal_sentinel_passes_through():
    out = resolve_answers([q("NIC", "network")],
                          {"NIC": NEW_INTERNAL}, vnets=VNETS)
    assert out["answers"]["NIC"] == NEW_INTERNAL


def test_duplicate_network_names_are_refused_not_silently_picked():
    """Defect found: vnet_keys was a dict built by assignment, so two vnets
    sharing a name meant last-one-wins and the operator got an arbitrary
    network with no warning. The role refuses an ambiguous RECIPE name; an
    ambiguous NETWORK name has to be refused for the same reason."""
    dupes = [{"name": "DMZ", "$key": 7}, {"name": "DMZ", "$key": 9}]
    out = resolve_answers([q("NIC", "network")], {"NIC": "DMZ"}, vnets=dupes)
    assert out["errors"], "an ambiguous network name was silently resolved"
    assert "DMZ" in errs(out)


def test_a_network_named_like_a_key_prefers_the_name():
    """Defect found: _is_int ran before the name lookup, so a vnet literally
    named '3' could never be selected by name -- the answer was read as key 3,
    which is a different network."""
    shadow = [{"name": "3", "$key": 11}, {"name": "External", "$key": 3}]
    out = resolve_answers([q("NIC", "network")], {"NIC": "3"}, vnets=shadow)
    assert out["answers"]["NIC"] == 11, \
        "a network named '3' resolved to key 3, not to itself"


def test_numeric_network_answer_still_works_when_no_name_shadows_it():
    out = resolve_answers([q("NIC", "network")], {"NIC": "3"}, vnets=VNETS)
    assert out["answers"]["NIC"] == 3


# -- table-backed questions --------------------------------------------------

TIERS = {"SELECT_OS_TIER": [{"$key": 1, "$display": "1"},
                            {"$key": 4, "$display": "4"}]}


def test_invalid_table_choice_is_refused_and_lists_the_valid_ones():
    out = resolve_answers([q("SELECT_OS_TIER", "row", table="clusters")],
                          {"SELECT_OS_TIER": 99}, options=TIERS)
    assert "not a valid choice" in errs(out)
    assert "1=1" in errs(out) and "4=4" in errs(out)


def test_empty_option_set_says_so_rather_than_failing_mid_deploy():
    out = resolve_answers([q("T", "row", table="clusters", required=True,
                             default="")], {}, options={"T": []})
    assert "NO valid options exist" in errs(out)


# -- answer-set shape --------------------------------------------------------

def test_answer_names_are_case_sensitive_and_a_case_slip_is_reported():
    out = resolve_answers([q("HOSTNAME", required=True, default="")],
                          {"hostname": "zz"})
    assert "unknown answer" in errs(out)
    assert "missing required" in errs(out)


def test_prune_reports_every_dropped_key():
    out = resolve_answers([q("A")], {"A": "1", "B": "2", "C": "3"},
                          prune_unknown=True)
    assert out["pruned"] == ["B", "C"]
    assert out["errors"] == []


def test_pruning_never_drops_a_key_the_recipe_does_define():
    out = resolve_answers([q("A")], {"A": "1", "B": "2"}, prune_unknown=True)
    assert out["answers"] == {"A": "1"}


def test_empty_answer_set_reports_every_missing_required_question():
    qs = [q("A", required=True, default=""), q("B", required=True, default="")]
    out = resolve_answers(qs, {})
    assert len(out["errors"]) == 2


# -- secrets -----------------------------------------------------------------

def test_password_questions_are_reported_as_secrets():
    out = resolve_answers([q("PASSWORD", "password")], {"PASSWORD": "hunter2"})
    assert out["secret_vars"] == ["PASSWORD"]


def test_a_recipe_with_no_questions_still_flags_secret_looking_answers():
    """Defect found: the non-introspectable passthrough returned
    secret_vars=[] unconditionally. The role drives no_log off that list, so
    every answer of the vendor Services recipe -- its PASSWORD included -- was
    logged in the clear on both the simulate and the deploy POST."""
    out = resolve_answers([], {"YB_HOSTNAME": "nas", "PASSWORD": "hunter2"})
    assert out["introspectable"] is False
    assert "PASSWORD" in out["secret_vars"]


@pytest.mark.parametrize("name", [
    "PASSWORD", "VPNPASS", "CLUSTER_SECRET", "root_password", "apiKey",
    "DB_PASSWD", "TOKEN",
])
def test_secret_looking_names_are_flagged_when_types_are_unavailable(name):
    out = resolve_answers([], {name: "x"})
    assert name in out["secret_vars"]


def test_secret_values_are_never_placed_in_errors_or_hints():
    out = resolve_answers([q("PASSWORD", "password", required=True, max="8")],
                          {"PASSWORD": "hunter2-too-long"})
    assert out["errors"]
    assert "hunter2" not in errs(out)


# -- inline list choices -----------------------------------------------------

@pytest.mark.parametrize("raw", ["not-json", ["dhcp", "static"], 12, None, ""])
def test_a_garbage_list_choice_field_does_not_crash_or_refuse(raw):
    """A list column that is not a key/label mapping is not a choice set.

    Guessing at it would refuse answers the recipe would accept.
    """
    out = resolve_answers(
        [q("YB_IP_ADDR_TYPE", "list", default="dhcp", list=raw)],
        {"YB_IP_ADDR_TYPE": "dhcp"})
    assert out["errors"] == [], errs(out)
    assert out["answers"]["YB_IP_ADDR_TYPE"] == "dhcp"


def test_a_container_answer_to_a_list_question_is_still_a_single_value():
    out = resolve_answers(
        [q("YB_IP_ADDR_TYPE", "list", list={"dhcp": "DHCP", "static": "Static"})],
        {"YB_IP_ADDR_TYPE": ["dhcp"]})
    assert "single value" in errs(out)
    # A container is refused by the shared single-value check. The choice
    # list is not also consulted, so this stays that check and not a
    # "not a valid choice" on top of it.
    assert "not a valid choice" not in errs(out)


def test_a_label_shared_by_two_keys_names_both():
    out = resolve_answers(
        [q("P", "list", list={"a": "Same", "b": "Same"})],
        {"P": "Same"})
    msg = errs(out)
    assert "label for keys" in msg
    assert "'a'" in msg and "'b'" in msg


def test_a_differently_cased_key_is_named_when_it_is_not_the_label():
    out = resolve_answers(
        [q("P", "list", list={"dhcp": "Automatic"})],
        {"P": "DHCP"})
    assert "matches key 'dhcp'" in errs(out)


def test_table_backed_list_ignores_an_inline_mapping():
    """WINDOWS_ISO is type list over a table. That table is the choice set."""
    out = resolve_answers(
        [q("WINDOWS_ISO", "list", table="files", list={"dhcp": "DHCP"})],
        {"WINDOWS_ISO": "dhcp"},
        options={"WINDOWS_ISO": [{"$key": "server.iso",
                                  "$display": "server.iso"}]})
    msg = errs(out)
    assert "not a valid choice" in msg
    assert "dhcp=DHCP" not in msg
    assert "server.iso" in msg


# -- scan_simulate -----------------------------------------------------------

def test_simulation_complete_with_a_failed_step_is_not_ok():
    doc = {"err": "Simulation complete",
           "response": {"logs": ["step 1 ok",
                                 "Error executing API command: no such tier"]}}
    assert scan_simulate(doc)["ok"] is False


def test_a_response_that_is_not_a_dict_does_not_crash_the_scan():
    for doc in ({"response": []}, {"response": None}, {}, {"response": "str"}):
        out = scan_simulate(doc)
        assert out["ok"] is False


def test_logs_that_are_not_strings_do_not_crash_the_scan():
    doc = {"err": "Simulation complete",
           "response": {"logs": [None, 42, {"m": "x"},
                                 "Error executing recipe"]}}
    out = scan_simulate(doc)
    assert out["ok"] is False
