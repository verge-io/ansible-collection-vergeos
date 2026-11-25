# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Answer resolution and simulate-log scanning for VergeOS recipe deploys.

Deliberately free of Ansible, of pyvergeos and of the network, so the
interesting logic is directly unit-testable. The SDK-facing glue lives in
module_utils/vm_recipes.py instead.

Why scan_simulate() exists: a simulated deploy reports success as
``{"err": "Simulation complete"}`` even when its own log contains
``Error executing API command`` lines. Trusting the top-level field alone
reports a deploy as fine that would in fact build a VM with no OS drive.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import json
import re

# Question types whose answers the API wants as integers.
NUMERIC_TYPES = frozenset({"num", "ram", "disksize", "seconds"})
# A disksize answer is bytes. Anything above zero and under 1 MB was sent
# through: 50 became fifty bytes, the recipe built the OS drive at the
# image's own size, and the deploy reported success. Zero is the recipe's
# "use the default" and stays valid. 1048576 is the count the issue names.
_DISKSIZE_MIN_BYTES = 1048576
# 50 * 1024**3. Quoted in the refusal so a GB-shaped answer has the byte
# form next to it.
_DISKSIZE_50GB_BYTES = 53687091200
# Words a bool question accepts, compared after strip and lower. An empty
# string is false, which is the coercion this already had. Anything else used
# to be forwarded as a string, and the platform reads a string it does not
# know as false -- so SELECT_CREATE_UEFI: enabled built a BIOS VM and the
# deploy still reported success.
_BOOL_TRUE_WORDS = ("true", "yes", "on", "1")
_BOOL_FALSE_WORDS = ("false", "no", "off", "0")
_BOOL_TRUE = frozenset(_BOOL_TRUE_WORDS)
_BOOL_FALSE = frozenset(_BOOL_FALSE_WORDS + ("",))
# Inline list keys YAML 1.1 reads as booleans when left unquoted. Order is
# the example quoted back to the operator: the word they most likely wrote
# for this boolean, then the other spellings. "1" and "0" are not here; an
# unquoted 1 is an integer, and a list key of "1" is a different refusal.
_YAML_LIST_TRUE_WORDS = ("yes", "true", "on")
_YAML_LIST_FALSE_WORDS = ("no", "false", "off")
_YAML_LIST_BOOL_WORDS = _YAML_LIST_TRUE_WORDS + _YAML_LIST_FALSE_WORDS
# Types carrying a secret; their variable names drive redaction in the module.
SECRET_TYPES = frozenset({"password"})
# Names that mark an answer as a secret when its type cannot say so. A recipe
# that publishes no questions has no types at all (the vendor "Services"
# recipe), and nothing else would stop its password being logged in clear.
# Over-matching only suppresses a log line, so this errs wide on purpose.
SECRET_NAME_RE = re.compile(
    r"pass|secret|token|api[_-]?key|credential|private[_-]?key", re.I)
# A question whose name is a timezone. An empty or zero answer makes the
# recipe script run its timezone API command with 0.
_TIMEZONE_NAME_RE = re.compile(r"time.?zone", re.IGNORECASE)
# A default that names a settings row, such as settings/timezone, rather than
# a value the API will store. The platform does not treat that as an answer.
_SETTINGS_REF_RE = re.compile(r"^(?:settings/)?[A-Za-z0-9_]+/[A-Za-z0-9_]+$")
_NOT_DOWNLOADED_TEXT = "not downloaded or published"
_UNFILLED = object()
_MISSING = object()
# ansible-core substitutes one of these for a no_log value. vm_recipe_info
# writes the first one over a credential default. None of them is an answer.
_NO_LOG_PLACEHOLDERS = frozenset((
    "VALUE_SPECIFIED_IN_NO_LOG_PARAMETER",
    "$REDACTED$",
    "********",
))
# The API accepts this verbatim for a network answer: "make a new internal net".
NEW_INTERNAL = "__new_internal__"
# Substrings marking a failed step inside a simulate log.
ERROR_MARKERS = ("Error executing API command", "Error executing recipe")
# Types whose valid values come from a database table, not the question row.
TABLE_BACKED_TYPES = frozenset({"row", "list", "cluster"})
# Section holding a recipe's internal database automation. Not user input:
# on this platform 229 database_* and 91 hidden questions live here, and
# nagging the operator to answer recipe plumbing makes the hint list useless.
INTERNAL_SECTION = "$database"
INTERNAL_TYPES = frozenset({
    "hidden", "database_create", "database_edit", "database_find", "field",
})
_TIER_COMMAND_TYPES = frozenset({
    "database_create", "database_edit", "database_find", "field",
})
_TIER_TABLES = frozenset({"storage_tiers", "cluster_tiers"})
_TIER_NAME_RE = re.compile(
    r"(?:^|_)tier(?:$|_)|preferred.?tier", re.IGNORECASE)
_TIER_DISPLAY_RE = re.compile(r"\btier\b", re.IGNORECASE)
_STEP_NAME_RE = re.compile(
    r"^(?:CREATE|EDIT|GET|CHANGE|DELETE|API)_", re.IGNORECASE)
_EMPTY_PREFERRED_TIER = "value '' is not in list for field 'preferred_tier'"


def _is_int(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        s = value.strip()
        # isdecimal, not isdigit: isdigit also accepts superscripts and other
        # numeric-looking characters that int() then refuses, so "2**2" said
        # yes here and raised ValueError one line later in _coerce.
        return s.isdecimal() or (s.startswith("-") and s[1:].isdecimal())
    return False


def _coerce_bool(value):
    """A real bool for a known form, or None when ``value`` is not one.

    Integer 0 and 1 are accepted the same way the strings ``"0"`` and ``"1"``
    are, so a playbook that already passes them keeps working. ``bool`` is a
    subclass of ``int``, so it is handled first. None here means "not a
    boolean form", not false: callers must not treat it as False.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _BOOL_TRUE:
            return True
        if low in _BOOL_FALSE:
            return False
    return None


def _bool_answer_error(name, value):
    """Refusal for a bool answer that is not a form ``_coerce_bool`` accepts."""
    return ("answer %r is not a recognised boolean (got %r); accepted values "
            "are %s and %s" % (name, value, ", ".join(_BOOL_TRUE_WORDS),
                               ", ".join(_BOOL_FALSE_WORDS)))


def _disksize_answer_error(name, value):
    """Refusal for a disksize above zero and under 1 MB.

    The unit is bytes. ``50`` is fifty bytes, not fifty gigabytes.
    """
    return ("answer %r is %d; a disksize answer is in bytes, so 50 GB is "
            "%d, and a value above zero but under 1 MB (%d) is refused"
            % (name, value, _DISKSIZE_50GB_BYTES, _DISKSIZE_MIN_BYTES))


def _coerce(value, qtype):
    """Coerce one answer to the shape the API expects for its question type."""
    if qtype in NUMERIC_TYPES and _is_int(value):
        return int(value)
    if qtype == "bool":
        coerced = _coerce_bool(value)
        # False is a successful coercion. Only None means "leave it alone".
        if coerced is not None:
            return coerced
    return value


def looks_secret(name):
    """True when an answer name reads like a credential.

    A backstop for the type check, not a replacement: it is the only signal
    available when a recipe publishes no questions.
    """
    return bool(SECRET_NAME_RE.search(name or ""))


def is_internal(question):
    """True for questions that are recipe plumbing rather than operator input."""
    return (question.get("type") in INTERNAL_TYPES
            or question.get("sect") == INTERNAL_SECTION
            or question.get("section_name") == INTERNAL_SECTION)


def is_tier_question(question):
    """True for a question that supplies preferred_tier, not a drive-create step."""
    if not isinstance(question, dict):
        return False
    name = question.get("name") or ""
    if _STEP_NAME_RE.match(name):
        return False
    qtype = question.get("type")
    if qtype in _TIER_COMMAND_TYPES:
        return False
    table = str(question.get("table") or "")
    if table in _TIER_TABLES:
        return True
    display = question.get("display") or ""
    if _TIER_NAME_RE.search(name) or _TIER_DISPLAY_RE.search(display):
        return qtype in TABLE_BACKED_TYPES or qtype in NUMERIC_TYPES or qtype in (
            "string", "hidden", "list", None)
    return False


def drive_tier_has_no_question(questions, message):
    """True when preferred_tier is '' and no question supplies it."""
    if isinstance(message, (list, tuple)):
        text = " ".join(str(item) for item in message)
    else:
        text = "" if message is None else str(message)
    if _EMPTY_PREFERRED_TIER not in text:
        return False
    return not any(is_tier_question(q) for q in questions or [])


def _missing_choice_key(key):
    """True for no key. ``0``, ``False`` and ``'0'`` are keys."""
    return key is None or key == ""


def accepted_choice_keys(question, options):
    """Choice keys ``resolve_answers`` will accept, in list order.

    A list question's own mapping comes first, including ``0``. Table rows
    are added after that. ``0`` is not dropped.
    """
    qtype = question.get("type")
    keys = []
    seen = set()

    def add(key):
        if _missing_choice_key(key):
            return
        marker = str(key)
        if marker in seen:
            return
        seen.add(marker)
        keys.append(key)

    if qtype == "list":
        # Key 0 stays. Other inline keys do not override a table.
        for key in _parsed_list(question):
            if question.get("table") and str(key) != "0":
                continue
            add(key)
    if qtype in TABLE_BACKED_TYPES and question.get("table"):
        rows = (options or {}).get(question.get("name")) or []
        for row in rows:
            if isinstance(row, dict):
                add(row.get("$key"))
    return keys


def _matching_choice(question, keys):
    """The published default when it is one of ``keys``, else None."""
    default = question.get("default")
    if _missing_choice_key(default):
        return None
    text = str(default).strip()
    for key in keys:
        if str(key) == text:
            return key
    return None


def describe_options(name, question, options):
    """Render the valid values for a table-backed question.

    Without this, a missing row-type answer surfaces as a bare "Missing
    required answer" (or worse, an empty value that fails mid-deploy), with
    nothing saying what the acceptable values were.
    """
    opts = (options or {}).get(name)
    if opts is None:
        return ""
    if not opts:
        return (", NO valid options exist on this system (table %r%s); "
                "create one first"
                % (question.get("table") or "?",
                   ", filter " + repr(question["filter"])
                   if question.get("filter") else ""))

    def label(row):
        for field in ("$display", "name", "$key"):
            value = row.get(field)
            if not _missing_choice_key(value):
                return value
        return row.get("$key")

    shown = ", ".join(
        "%s=%s" % (o.get("$key"), label(o))
        for o in opts[:8])
    return ", valid: %s%s" % (shown, " ..." if len(opts) > 8 else "")


def _inline_list_choices(question):
    """Fixed choices on a list question, as ``{key: label}``, or {}.

    A table-backed list (WINDOWS_ISO over ``files``) takes its choices from
    the table, which ``describe_options`` already renders. This is the mapping
    the recipe stores on the question itself, such as
    ``{"dhcp": "DHCP", "static": "Static"}``. The platform may hand that
    mapping back as a dict or as a JSON string. Anything else is not a choice
    list, and guessing at it would refuse answers the recipe would accept.
    """
    if question.get("type") != "list" or question.get("table"):
        return {}
    raw = question.get("list")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    choices = {}
    for key, label in raw.items():
        if key is None or key == "":
            continue
        choices[str(key)] = "" if label is None else str(label)
    return choices


def describe_list_choices(question):
    """``, valid: key=label, ...`` for an inline list question, or ''."""
    choices = _inline_list_choices(question)
    if not choices:
        return ""
    items = list(choices.items())
    shown = ", ".join("%s=%s" % (key, label) for key, label in items[:8])
    return ", valid: %s%s" % (shown, " ..." if len(items) > 8 else "")


def _choice_suffix(name, question, options):
    """The ``valid:`` note for a table-backed question, else an inline list."""
    listed = describe_options(name, question, options)
    if listed:
        return listed
    return describe_list_choices(question)


def _list_label_hint(value, choices):
    """Name the key when ``value`` is a display label (or a cased key), or ''.

    The platform's own refusal names the question by its display label and
    does not list the keys a playbook has to send. ``DHCP`` is the label for
    ``dhcp``; saying so is the difference between a message the operator can
    act on and one that only repeats the label they already typed.
    """
    folded = value.strip().lower()
    if not folded:
        return ""
    label_hits = [key for key, label in choices.items()
                  if label.strip().lower() == folded]
    if len(label_hits) == 1:
        return "; %r is the label for key %r" % (value, label_hits[0])
    if len(label_hits) > 1:
        return "; %r is the label for keys %s" % (
            value, ", ".join(repr(key) for key in label_hits))
    key_hits = [key for key in choices
                if key.lower() == folded and key != value]
    if len(key_hits) == 1:
        return "; %r matches key %r" % (value, key_hits[0])
    return ""


def _yaml_bool_list_hint(value, choices):
    """Advise quoting when YAML 1.1 delivered a boolean for a string key.

    An unquoted ``no`` in a playbook is the boolean ``False`` by the time it
    arrives. The choice keys are strings, so the boolean is refused. It is
    not rewritten to ``"no"``. The hint is added only when a key is one of
    the words YAML would have turned into a boolean (yes/no, true/false,
    on/off): that is when the refusal otherwise lists the word they typed
    as a valid choice.
    """
    if not isinstance(value, bool):
        return ""
    by_lower = {}
    for key in choices:
        by_lower.setdefault(key.lower(), key)
    if not any(word in by_lower for word in _YAML_LIST_BOOL_WORDS):
        return ""
    preferred = (_YAML_LIST_FALSE_WORDS if value is False
                 else _YAML_LIST_TRUE_WORDS)
    example = None
    for word in preferred + _YAML_LIST_BOOL_WORDS:
        found = by_lower.get(word)
        if found is not None:
            example = found
            break
    if example is None:
        return ""
    return ("; YAML turned the unquoted word into a boolean; "
            "quote the string (for example \"%s\")" % example)


def _declared(bound):
    """A declared min/max, or None. ``min: 0`` is a real floor, not an absence."""
    return None if bound is None or bound == "" else int(bound)


# Stock Linux recipes publish this HOSTNAME pattern. The optional group is
# one-or-more of the class and then one alphanumeric, so a name is either one
# letter or at least three characters. "db" is neither. The quantifier was
# meant to be "*": zero or more, then one alphanumeric. The pattern belongs
# to the recipe catalog. VergeOS does not enforce it on deploy -- a simulate
# with HOSTNAME db completes and the rendered hostname is db -- so applying
# the published string unchanged is the only refusal. Match the corrected
# form for this one string. A value that still fails is refused, and the
# message quotes the pattern the recipe published. Every other pattern is
# applied unchanged with re.fullmatch.
_STOCK_HOSTNAME_REGEX = "[a-zA-Z]([a-zA-Z0-9_-]+[a-zA-Z0-9])?"
_STOCK_HOSTNAME_REGEX_AS_INTENDED = "[a-zA-Z]([a-zA-Z0-9_-]*[a-zA-Z0-9])?"


def _regex_to_apply(regex):
    """Pattern to fullmatch against an answer.

    The stock HOSTNAME pattern is one quantifier off. Applying it unchanged
    refuses two-letter names the platform accepts. Anything else, including
    a catalog that has already published the star form, is the recipe's own
    pattern.
    """
    if regex == _STOCK_HOSTNAME_REGEX:
        return _STOCK_HOSTNAME_REGEX_AS_INTENDED
    return regex


def check_constraints(name, question, value):
    """Enforce the question's own min/max/regex before the API has to."""
    errors = []
    qtype = question.get("type")
    mn, mx = _declared(question.get("min")), _declared(question.get("max"))

    # A container is not an answer to any question type, and letting one
    # through means it is json-encoded into the payload as a list or object.
    if isinstance(value, (list, dict, tuple)):
        return ["answer %r must be a single value, not a %s"
                % (name, type(value).__name__)]

    if qtype in NUMERIC_TYPES:
        # The gate that was missing: _coerce only converts what _is_int
        # accepts, so anything else — "4096MB", 1024.5, True, None — arrived
        # here matching neither the str branch nor the int branch below and
        # was sent to the API unchecked.
        if not isinstance(value, int) or isinstance(value, bool):
            return ["answer %r must be a whole number for a %s question, "
                    "but is %s" % (name, qtype, type(value).__name__)]
        # No recipe means a negative core count, size or timeout, so an
        # undeclared minimum still floors at zero rather than at -infinity.
        floor = 0 if mn is None else mn
        if value < floor:
            errors.append(
                "answer %r is %d; %s" % (name, value,
                                         "the recipe requires at least %d" % mn
                                         if mn is not None
                                         else "a %s cannot be negative" % qtype))
        # max=0 is the platform's "no limit", not a limit of zero.
        if mx is not None and mx > 0 and value > mx:
            errors.append("answer %r is %d; the recipe allows at most %d"
                          % (name, value, mx))
        # The gate that was missing: a whole number still went through, so
        # YB_DRIVE_OS_SIZE: 50 was fifty bytes. The recipe built the OS drive
        # at the image size and the deploy reported success. Zero stays.
        # A question that publishes a size under 1 MB (BLOCKSIZE 65536 is
        # 64 KiB) is a byte count, not a drive written in the wrong unit.
        if (qtype == "disksize" and 0 < value < _DISKSIZE_MIN_BYTES
                and not _disksize_accepts_sub_megabyte(question)):
            errors.append(_disksize_answer_error(name, value))
        return errors

    if qtype == "bool":
        # The gate that was missing: _coerce only converts the words it knows
        # (and integer 0 and 1). "enabled" and "UEFI" arrived here still
        # strings, matched no branch below, and were sent on. The platform
        # reads that string as false.
        if not isinstance(value, bool):
            return [_bool_answer_error(name, value)]
        return errors

    if isinstance(value, str) and qtype in ("string", "hostname", "textarea",
                                            "password"):
        # max=0 is "no limit" here too, not a limit of zero. Most stock
        # recipes ship max=0 on their string questions, so reading it as a
        # real bound refuses every answer including the correct one.
        if mx is not None and mx > 0 and len(value) > mx:
            errors.append("answer %r is %d characters; the recipe allows at "
                          "most %d" % (name, len(value), mx))
        if mn is not None and len(value) < mn:
            errors.append("answer %r is %d characters; the recipe requires at "
                          "least %d" % (name, len(value), mn))
        regex = question.get("regex")
        if regex:
            try:
                # fullmatch, not search: a pattern matches the whole answer.
                # The stock HOSTNAME string is the one exception to applying
                # the published text byte for byte; see _regex_to_apply.
                if not re.fullmatch(_regex_to_apply(regex), value):
                    errors.append("answer %r does not match the recipe's "
                                  "pattern %s" % (name, regex))
            except re.error:
                pass
    return errors


def maskable_strings(value):
    """Every string ansible-core would add to ``no_log_values`` for ``value``.

    Mirrors ansible-core's ``_return_datastructure_name``: strings if
    non-empty, numbers stringified, booleans and None skipped, containers
    walked. Kept in step with it deliberately -- this is the set we reason
    about when deciding what may safely stop being masked.
    """
    out = set()
    if isinstance(value, (bool, type(None))):
        return out
    if isinstance(value, (bytes, str)):
        if value:
            out.add(value.decode() if isinstance(value, bytes) else value)
        return out
    if isinstance(value, dict):
        for item in value.values():
            out |= maskable_strings(item)
        return out
    if isinstance(value, (list, tuple, set)):
        for item in value:
            out |= maskable_strings(item)
        return out
    if isinstance(value, (int, float)):
        out.add(str(value))
    return out


def unmaskable_answer_strings(answers, secret_vars):
    """Strings contributed ONLY by answers that are not credentials.

    Ansible masks a ``no_log`` value by replacing it as a plain SUBSTRING
    anywhere in a module's output, and it treats integers as values too. So a
    perfectly ordinary answer like ``YB_CPU_CORES: 1`` masks every "1" the
    module ever prints -- including the digits inside the recipe's own
    constraints, which is how "requires at least 512" came out as
    "requires at least 5********2".

    ``answers`` has to stay ``no_log`` in the argument spec: it routinely
    carries passwords, and ansible-core logs the invocation before any module
    code runs, so redacting later is too late for that path. Ordinary answers
    belong in ``plain_answers``, which is not ``no_log``. What CAN still be
    done for values that a playbook leaves in ``answers`` is to stop masking
    the non-credential ones in the module's own RETURN values, once the
    recipe's question types have said which answers are credentials. On
    ansible-core 2.22 that does not change later output: those strings were
    registered when the module started.

    A value that any secret answer also contributes is never returned here,
    so a password that happens to equal a core count stays masked.
    """
    secret = set(secret_vars or [])
    secret_strings, plain_strings = set(), set()
    for name, value in (answers or {}).items():
        target = secret_strings if name in secret else plain_strings
        target |= maskable_strings(value)
    return plain_strings - secret_strings


def answers_retained_in_no_log(answers, no_log_values):
    """Names whose values are still entirely inside ``no_log_values``.

    A name is included only when the answer contributes at least one maskable
    string and every one of those strings is still present. An empty value
    contributes nothing and is left out. Values are not returned.
    """
    held = set(no_log_values or ())
    names = []
    for name, value in (answers or {}).items():
        strings = maskable_strings(value)
        if strings and strings <= held:
            names.append(name)
    return sorted(names)


_ECHO_FIELDS = (
    "msg", "hints", "answers_sent", "pruned", "no_log_answers",
    "exception",
)


def _recipe_name(result):
    recipe = result.get("recipe") if isinstance(result, dict) else None
    if isinstance(recipe, dict):
        name = recipe.get("name")
        return "" if name is None else str(name)
    if isinstance(recipe, str):
        return recipe
    return ""


def _field_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def answer_value_echoed(result, value, recipe_name=""):
    """True when ``value`` shows up outside the recipe name.

    The refusal quotes the recipe. That name is removed from ``msg`` before
    the search, so a recipe that happens to contain the same letters is not
    an echo of the answer. The recipe row itself is not searched.
    """
    if not isinstance(result, dict):
        return False
    text = "" if value is None else str(value)
    if not text:
        return False
    msg = _field_text(result.get("msg"))
    if recipe_name:
        msg = msg.replace(str(recipe_name), "")
    if text in msg:
        return True
    for key in _ECHO_FIELDS:
        if key == "msg":
            continue
        if text in _field_text(result.get(key)):
            return True
    return False


def credential_answer_concealed(result, name, value):
    """True when ``name`` stayed no_log and ``value`` was not echoed.

    ``result['no_log_answers']`` is the list of names still masked. A message
    that only quotes the recipe does not count, and neither does an empty
    list. The recipe name cannot make this true.
    """
    if not isinstance(result, dict) or not isinstance(name, str) or not name:
        return False
    text = "" if value is None else str(value)
    if not text:
        return False
    retained = result.get("no_log_answers")
    if not isinstance(retained, (list, tuple, set)) or name not in retained:
        return False
    return not answer_value_echoed(result, text, _recipe_name(result))


def _is_no_log_placeholder(value):
    """True for the tokens ansible-core prints in place of a no_log value."""
    return isinstance(value, str) and value.strip() in _NO_LOG_PLACEHOLDERS


def _blankish(value):
    """True for the values a recipe treats as 'not set': empty, or zero."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() in ("", "0"):
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)) and value == 0:
        return True
    return False


def _is_timezone_question(name):
    return bool(_TIMEZONE_NAME_RE.search(name or ""))


def _is_credential_question(question, name):
    qtype = (question or {}).get("type")
    return qtype in SECRET_TYPES or looks_secret(name)


def recipe_not_applicable(value, message=None):
    """True when a recipe cannot be simulated because it is not on this system.

    ``value`` is a recipe row, or the module's failure message. A row whose
    ``downloaded`` field is false is not applicable. So is a simulate that
    came back with the platform's ``not downloaded or published`` reason.
    A row that does not say either way is left to the simulate.
    """
    if isinstance(value, str):
        return _NOT_DOWNLOADED_TEXT in value
    text = "" if message is None else str(message)
    if _NOT_DOWNLOADED_TEXT in text:
        return True
    if not isinstance(value, dict) or "downloaded" not in value:
        return False
    flag = value.get("downloaded")
    return flag is False or flag in (0, "0", "false", "False")


def _disksize_accepts_sub_megabyte(question):
    """True when this question's own min, max, or default is under 1 MB.

    The 1 MB floor catches an OS-drive size written in the wrong unit: 50
    was sent as fifty bytes. BLOCKSIZE on the filesystem test is the same
    type and a different quantity. Its published default is 65536, which is
    64 KiB. Refusing that default was the validator. The filler was already
    sending the question's own value.
    """
    for raw in (question.get("min"), question.get("default"), question.get("max")):
        if not _is_int(raw):
            continue
        number = int(raw)
        if 0 < number < _DISKSIZE_MIN_BYTES:
            return True
    return False


def _numeric_floor(question):
    """The smallest value worth sending for a required numeric question.

    The question's own minimum wins when it is a real size. Zero is the
    disksize sentinel for 'use the recipe default', so a required disksize
    with no positive minimum is sent as 1 MB, which is the smallest size
    the validator accepts above that sentinel.
    """
    mn = _declared(question.get("min"))
    qtype = question.get("type")
    if mn is not None and mn > 0:
        return mn
    if qtype == "disksize":
        return _DISKSIZE_MIN_BYTES
    return 1


def _lift_to_minimum(question, value):
    """Raise a numeric answer that sits below the question's minimum."""
    if question.get("type") not in NUMERIC_TYPES:
        if _is_timezone_question(question.get("name")) and _blankish(value):
            return "UTC"
        return value
    mn = _declared(question.get("min"))
    if not _is_int(value):
        return value
    number = int(value)
    if mn is None:
        return number
    if number < mn:
        return mn
    return number


def _string_fill(question):
    """A string the question's own pattern and length will accept, or None."""
    mn = _declared(question.get("min")) or 0
    mx = _declared(question.get("max"))
    regex = question.get("regex")
    seeds = ["zz-mx", "a", "zzmx1"]
    if mn > 1:
        seeds.append("x" * mn)
    for seed in seeds:
        candidate = seed
        if mn and len(candidate) < mn:
            candidate = (candidate + ("x" * mn))[:mn]
        if mx is not None and mx > 0 and len(candidate) > mx:
            continue
        if not regex:
            return candidate
        try:
            if re.fullmatch(_regex_to_apply(regex), candidate):
                return candidate
        except re.error:
            return candidate
    return None


def _default_is_concrete(question, options):
    """True when the published default is a value the API will accept.

    An empty default is not. Neither is a settings-row reference such as
    ``settings/timezone``: the recipe script looks that row up, and a missing
    row fails the command. A no_log placeholder is not a value either.
    ``vm_recipe_info`` writes ``VALUE_SPECIFIED_IN_NO_LOG_PARAMETER`` over a
    credential default, and other cores print ``$REDACTED$`` or asterisks.
    A list or table default counts only when it is one of the choices, or an
    http URL the recipe downloads itself. An ISO question with its own list
    accepts the default only when that default is one of those choices.
    """
    default = question.get("default")
    if _blankish(default) or _is_no_log_placeholder(default):
        return False
    if isinstance(default, str):
        text = default.strip()
        if text.startswith("settings/") or _SETTINGS_REF_RE.match(text):
            return False
    qtype = question.get("type")
    if qtype == "list" or qtype in TABLE_BACKED_TYPES:
        inline = _inline_list_choices(question)
        rows = (options or {}).get(question.get("name")) or []
        keys = set(inline)
        for row in rows:
            key = row.get("$key")
            if key is not None:
                keys.add(str(key))
        if _is_iso_question(question):
            choices = _parsed_list(question)
            if not choices:
                return False
            return isinstance(default, str) and default.strip() in choices
        if str(default) in keys:
            return True
        if isinstance(default, str) and (
                default.startswith("http://") or default.startswith("https://")):
            return True
        return False
    return True


def _choice_fill(question, options):
    """First valid choice for a list or table-backed question, or None."""
    inline = _inline_list_choices(question)
    if inline:
        return next(iter(inline))
    rows = (options or {}).get(question.get("name")) or []
    if not rows:
        return None
    return rows[0].get("$key")


def _parsed_list(question):
    """The question's own list choices, in published order.

    Unlike ``_inline_list_choices``, a table does not hide this. The VergeOS
    Install ISO question is a list of download URLs and the platform accepts
    only those URLs. The mapping may arrive as a dict or as a JSON string.
    A list of URL strings is the same set of choices.
    """
    if question.get("type") != "list":
        return {}
    raw = question.get("list")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except ValueError:
            return {}
    choices = {}
    if isinstance(raw, dict):
        pairs = raw.items()
    elif isinstance(raw, (list, tuple)):
        pairs = [(item, "") for item in raw if not _missing_choice_key(item)]
    else:
        return {}
    for key, label in pairs:
        if _missing_choice_key(key):
            continue
        choices[str(key)] = "" if label is None else str(label)
    return choices


def _is_https(value):
    return isinstance(value, str) and value.startswith("https://")


def _accepts_existing_file(question):
    """True when a file key is a legal answer for this question."""
    if str(question.get("table") or "") == "files":
        return True
    return str(question.get("type") or "") == "file"


def _is_iso_question(question):
    """A list or table question whose answer is an ISO file."""
    qtype = question.get("type")
    if qtype != "list" and qtype not in TABLE_BACKED_TYPES:
        return False
    if str(question.get("table") or "") == "files":
        return True
    blob = "%s %s" % (question.get("name") or "", question.get("display") or "")
    return "iso" in blob.lower()


def _iso_row_answer(row):
    """The file key (or name) of an ISO row, or None."""
    if not isinstance(row, dict):
        return None
    kind = str(row.get("type") or "").lower()
    texts = []
    for field in ("name", "$display", "filename"):
        text = row.get(field)
        if isinstance(text, str):
            texts.append(text.strip().lower())
    if kind != "iso" and not any(text.endswith(".iso") for text in texts):
        return None
    if row.get("$key") is not None:
        return row.get("$key")
    for field in ("name", "filename"):
        text = row.get(field)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _first_iso_answer(rows, preferred=None):
    """First ISO in ``rows``, preferring one the question already names."""
    found = None
    for row in rows or []:
        answer = _iso_row_answer(row)
        if answer is None:
            continue
        if preferred is not None and str(answer) == str(preferred):
            return answer
        if found is None:
            found = answer
    return found


def _iso_list_choice(question):
    """One of the question's own list choices, or None when it has none.

    The default is used only when it is one of those choices. Otherwise the
    first https choice is used. A list that contains neither is not usable:
    the caller must not substitute a local file.
    """
    choices = _parsed_list(question)
    if not choices:
        return None
    default = question.get("default")
    if isinstance(default, str) and default.strip() in choices:
        return default.strip()
    for key in choices:
        if _is_https(key):
            return key
    for label in choices.values():
        if _is_https(label):
            return label
    return _UNFILLED


def _iso_answer(question, options, files):
    """The ISO answer, ``_UNFILLED`` when the list has nothing usable, or None.

    A question that publishes its own list is answered from that list. A
    local file, from the question's option rows or from a read-only
    ``file_info`` listing, is used only when there is no such list and the
    question's type or table accepts a file. Nothing is downloaded.
    """
    listed = _iso_list_choice(question)
    if listed is not None:
        return listed
    if not _accepts_existing_file(question):
        return None
    rows = (options or {}).get(question.get("name")) or []
    preferred = question.get("default")
    choice = _first_iso_answer(rows, preferred)
    if choice is not None:
        return choice
    return _first_iso_answer(files, preferred)


def _tier_value(question, value):
    """A tier answer in the type the question stores."""
    if question.get("type") in NUMERIC_TYPES and _is_int(value):
        return int(value)
    return value


def _tier_choice(question, options, tiers):
    """A tier ``resolve_answers`` will accept, or None."""
    keys = accepted_choice_keys(question, options)
    if not keys:
        rows = (options or {}).get(question.get("name")) or []
        keys = [row.get("$key") for row in rows
                if isinstance(row, dict) and row.get("$key") is not None]
    if keys:
        matched = _matching_choice(question, keys)
        return _tier_value(question, matched if matched is not None else keys[0])
    for tier in tiers or []:
        if _is_int(tier) and int(tier) > 0:
            return _tier_value(question, tier)
    return None


def _fill_missing(question, options, files=None, tiers=None):
    """A value for a question the caller did not answer, or a sentinel.

    ``_MISSING`` means leave it to the platform. ``_UNFILLED`` means a
    required choice has no options and no usable default, so nothing honest
    can be sent.

    A required question is answered explicitly. A default that merely names a
    settings row is not an answer, and a list default is not an answer unless
    it is one of the choices or a download URL. An ISO question that publishes
    its own list is answered with the default when that default is a choice,
    otherwise with the first https choice. A local file is used only when
    that list is absent and the question accepts a file.
    A timezone question
    whose default is not a real zone is sent as UTC even when optional,
    because the recipe script otherwise runs that command with the bad
    default. A numeric default below the question minimum is raised to it.
    """
    name = question.get("name")
    qtype = question.get("type")
    concrete = _default_is_concrete(question, options)

    if _is_timezone_question(name) and not concrete:
        return "UTC"

    if qtype in NUMERIC_TYPES and _is_int(question.get("default")):
        lifted = _lift_to_minimum(question, question.get("default"))
        if lifted != int(question.get("default")):
            return lifted

    if not _is_iso_question(question):
        keys = accepted_choice_keys(question, options)
        if keys and _matching_choice(question, keys) is None:
            return keys[0]

    if is_tier_question(question) and not concrete:
        choice = _tier_choice(question, options, tiers)
        if choice is None:
            return _UNFILLED
        return choice

    if not question.get("required"):
        return _MISSING

    # A required credential is left unanswered, even when it publishes a
    # default. Copying that default would post it. vm_recipe_info has
    # already replaced a non-empty credential default with the no_log
    # placeholder, so the value here is not the platform's secret. A real
    # default stays on the platform, which applies it when the answer is
    # omitted. Inventing a password here would install that password.
    if _is_credential_question(question, name):
        return _MISSING

    if _is_iso_question(question):
        choice = _iso_answer(question, options, files)
        if choice is None or choice is _UNFILLED:
            return _UNFILLED
        return choice

    if concrete:
        default = question.get("default")
        if qtype in NUMERIC_TYPES:
            default = _lift_to_minimum(question, default)
        if not check_constraints(name, question, _coerce(default, qtype)):
            return default

    if qtype == "bool":
        return False
    if qtype in NUMERIC_TYPES:
        return _numeric_floor(question)
    if qtype == "list" or qtype in TABLE_BACKED_TYPES:
        choice = _choice_fill(question, options)
        if choice is None:
            return _UNFILLED
        return choice
    if qtype in ("string", "hostname", "textarea"):
        filled = _string_fill(question)
        if filled is None:
            return _MISSING
        return filled
    return _MISSING


def _unfilled_item(question):
    """Why a required choice was left unset."""
    name = question.get("name")
    display = question.get("display") or name
    if is_tier_question(question):
        reason = "no storage tier for question '%s'" % display
    elif _parsed_list(question):
        reason = "no usable choice for required question '%s'" % display
    elif _is_iso_question(question):
        reason = "no existing ISO for required question '%s'" % display
    else:
        reason = "no choices for required question '%s'" % display
    return {"name": name, "display": display, "reason": reason}


def fill_recipe_answers(questions, answers, options=None, files=None,
                        tiers=None):
    """Complete an answer set from the recipe's own questions.

    Returns ``plain``, ``secrets``, and ``unanswered_secrets``.

    ``secrets`` holds only credential values the caller already supplied.
    A published credential default is not copied, including the no_log
    placeholder ``vm_recipe_info`` substitutes for one.
    ``unanswered_secrets`` names required credentials that were left unset,
    whether or not a default was published.
    This function does not invent a password. ``unfilled`` names required
    choices that have no options and no usable default. ``files`` is a
    read-only listing of files already on the system. An ISO question
    that publishes its own list is answered from that list. The listing
    is used only when there is no such list and the question accepts a
    file. ``tiers`` is the tier numbers present on the system. A tier
    question with no choices of its own is answered with the first of
    those. Nothing is downloaded or uploaded.

    Supplied answers are kept, including ones whose question is internal.
    Supplied numeric answers below the question minimum are raised to that
    minimum. A required question the caller did not answer is filled from a
    concrete default, the type, the option list, or the minimum.
    """
    supplied = dict(answers or {})
    by_name = {}
    for question in questions or []:
        name = question.get("name")
        if name:
            by_name[name] = question

    plain = {}
    secrets = {}
    unanswered_secrets = []
    unfilled = []

    def _store(name, value, question):
        if _is_credential_question(question, name):
            secrets[name] = value
        else:
            plain[name] = value

    for name in sorted(supplied):
        question = by_name.get(name)
        if question is None:
            _store(name, supplied[name], None)
            continue
        # Keep a value the caller set, even on an internal question. The
        # matrix override for VM_TIMEZONE is one of these: dropping it sends
        # the platform back to the settings/timezone default.
        _store(name, _lift_to_minimum(question, supplied[name]), question)

    for name in sorted(by_name):
        if name in plain or name in secrets:
            continue
        question = by_name[name]
        if question.get("enabled") is False:
            continue
        if is_internal(question) and not is_tier_question(question):
            continue
        filled = _fill_missing(question, options, files, tiers)
        if filled is _UNFILLED:
            unfilled.append(_unfilled_item(question))
            continue
        if filled is _MISSING:
            if (question.get("required")
                    and _is_credential_question(question, name)):
                unanswered_secrets.append(name)
            continue
        _store(name, filled, question)

    return {
        "plain": plain,
        "secrets": secrets,
        "unanswered_secrets": unanswered_secrets,
        "unfilled": unfilled,
    }


def resolve_answers(questions, answers, vnets=None, options=None,
                    prune_unknown=False):
    """Validate and shape user answers against a recipe's question set.

    Defaults are NOT sent: the platform applies them itself, so echoing them
    back only adds noise and a second place for them to drift. Questions left
    unanswered whose default is empty are reported as hints, because that is
    the shape of the stock-recipe trap where an empty ``preferred_tier`` makes
    OS-drive creation fail.
    """
    by_name = {q.get("name"): q for q in questions if q.get("name")}
    # Counted, not just mapped: two vnets sharing a name used to mean
    # last-one-wins, so the operator silently got whichever network the API
    # happened to list second. An ambiguous network name is refused for the
    # same reason an ambiguous recipe name is.
    vnet_keys = {}
    vnet_seen = {}
    for v in vnets or []:
        if v.get("name") is not None:
            label = str(v["name"])
            vnet_seen[label] = vnet_seen.get(label, 0) + 1
            vnet_keys.setdefault(label, v.get("$key"))

    errors = []
    hints = []
    resolved = {}
    secret_vars = []
    needs_options = []

    # A recipe whose questions are not in recipe_questions cannot be
    # introspected. The vendor "Services" recipe is like this. Validating
    # against an empty set would reject every answer as unknown, so pass
    # them through and let the simulate be the check.
    introspectable = bool(by_name)
    if not introspectable:
        for name in sorted(answers):
            resolved[name] = answers[name]
        return {
            "answers": resolved,
            "errors": [],
            "hints": ["this recipe publishes no questions to recipe_questions, "
                      "so answers cannot be validated locally; the simulate is "
                      "the only check"],
            # With no question types there is nothing to mark a password as
            # one, and the module redacts off this list — so an empty list
            # here logged the whole answer set, secrets included.
            "secret_vars": sorted(n for n in answers if looks_secret(n)),
            "needs_options": [],
            "introspectable": False,
            "pruned": [],
        }

    pruned = []
    for name in sorted(answers):
        if name not in by_name:
            if prune_unknown:
                # Opt-in: one answer set applied across many recipes, where
                # a key that this recipe does not define is expected rather
                # than a typo. Always reported, never silent.
                pruned.append(name)
            else:
                errors.append(
                    "unknown answer %r - not a question on this recipe" % name)

    for name, q in by_name.items():
        qtype = q.get("type")
        # Type first, name as a backstop: a recipe is free to declare a
        # credential as a plain string, and one that does must not be the
        # reason a password reaches the log.
        if qtype in SECRET_TYPES or looks_secret(name):
            secret_vars.append(name)
        if is_internal(q):
            # Still send a value the caller set. Dropping it here makes the
            # platform use the question's default, which for VM_TIMEZONE is a
            # settings row that may not exist.
            if name in answers:
                resolved[name] = answers[name]
            continue
        if qtype in TABLE_BACKED_TYPES and q.get("table"):
            needs_options.append({
                "name": name,
                "table": q.get("table"),
                "filter": q.get("filter") or "",
                # The question carries the field spec the UI uses to render
                # its options (e.g. "$key,tier as $display,tier").
                "fields": q.get("fields") or "$key,$display",
            })

        supplied = name in answers
        if not supplied:
            required = bool(q.get("required"))
            default = q.get("default")
            if required and not default:
                errors.append(
                    "missing required answer %r (%s)%s"
                    % (name, q.get("display") or qtype,
                       _choice_suffix(name, q, options)))
            elif not required and (default is None or default == ""):
                hints.append(
                    "%r is unanswered and its default is empty (%s)%s"
                    % (name, q.get("display") or qtype,
                       _choice_suffix(name, q, options)))
            continue

        value = _coerce(answers[name], qtype)

        if qtype == "network" and isinstance(value, str) and value != NEW_INTERNAL:
            # Name before key: _is_int used to run first, so a vnet literally
            # named "3" could never be chosen by name — the answer was read as
            # key 3, a different network entirely.
            if vnet_seen.get(value, 0) > 1:
                errors.append(
                    "network name %r is ambiguous, %d networks share it; "
                    "give the vnet key instead (answer %r)"
                    % (value, vnet_seen[value], name))
                continue
            if value in vnet_keys:
                value = vnet_keys[value]
            elif _is_int(value):
                value = int(value)
            else:
                errors.append("network %r not found (answer %r)" % (value, name))
                continue

        # A bool is not "empty". "" has already coerced to False, and a
        # required null has to name the accepted values rather than say
        # "supplied empty" and skip that list. check_constraints does it.
        if (qtype != "bool" and bool(q.get("required"))
                and (value is None or value == "")):
            errors.append("required answer %r was supplied empty" % name)
            continue

        errors.extend(check_constraints(name, q, value))

        choice_keys = accepted_choice_keys(q, options)
        if (choice_keys and qtype in TABLE_BACKED_TYPES and q.get("table")
                and not isinstance(value, (list, dict, tuple))):
            if str(value) not in {str(key) for key in choice_keys}:
                errors.append("answer %r is not a valid choice%s"
                              % (name, describe_options(name, q, options)))
                continue

        # Inline list choices live on the question (``list: {dhcp: DHCP}``),
        # not in a table. The platform refuses the label and names the
        # question by its display text, without the keys.
        choices = _inline_list_choices(q)
        if choices and not isinstance(value, (list, dict, tuple)):
            text = value if isinstance(value, str) else str(value)
            if text not in choices:
                # "(got %r)" matches _bool_answer_error: a string prints as
                # 'enabled', a boolean as False. str(False) is "False", which
                # is not what the operator typed, so a boolean does not also
                # get the label/case hint that stringifies it.
                label_hint = ("" if isinstance(value, bool)
                              else _list_label_hint(text, choices))
                errors.append(
                    "answer %r is not a valid choice (got %r)%s%s%s"
                    % (name, value, describe_list_choices(q),
                       label_hint,
                       _yaml_bool_list_hint(value, choices)))
                continue

        resolved[name] = value

    return {
        "answers": resolved,
        "errors": sorted(errors),
        "hints": sorted(hints),
        "secret_vars": sorted(secret_vars),
        "needs_options": needs_options,
        "introspectable": True,
        "pruned": sorted(pruned),
    }


def scan_simulate(document):
    """Decide whether a simulated deploy actually succeeded.

    ``document`` is the raw body of POST /v4/vm_recipe_instances with
    ``simulate: true``.
    """
    # Every shape below has been seen from this API or is one field away from
    # it, and this function is the deploy gate — it must not raise, because a
    # traceback here reads as "the module is broken", not "the deploy is unsafe".
    if not isinstance(document, dict):
        document = {}
    response = document.get("response")
    if not isinstance(response, dict):
        response = {}
    logs = response.get("logs")
    if not isinstance(logs, list):
        logs = []
    top = document.get("err")

    errors = [str(line) for line in logs
              if any(marker in str(line) for marker in ERROR_MARKERS)]

    if top and top != "Simulation complete":
        errors.insert(0, top)
    if not top and not response:
        errors.insert(0, "simulate returned no response document")

    answers = response.get("answers") or {}
    return {
        "ok": not errors,
        "errors": errors,
        "vm_key": answers.get("YB_VM_KEY"),
        "machine_key": answers.get("YB_MACHINE_KEY"),
        "cloudinit_files": [f.get("name")
                            for f in response.get("cloudinit_files") or []],
        "step_count": len(logs),
    }
