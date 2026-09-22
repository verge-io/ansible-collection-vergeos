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

import re

# Question types whose answers the API wants as integers.
NUMERIC_TYPES = frozenset({"num", "ram", "disksize", "seconds"})
# Types carrying a secret; their variable names drive redaction in the module.
SECRET_TYPES = frozenset({"password"})
# Names that mark an answer as a secret when its type cannot say so. A recipe
# that publishes no questions has no types at all (the vendor "Services"
# recipe), and nothing else would stop its password being logged in clear.
# Over-matching only suppresses a log line, so this errs wide on purpose.
SECRET_NAME_RE = re.compile(
    r"pass|secret|token|api[_-]?key|credential|private[_-]?key", re.I)
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


def _coerce(value, qtype):
    """Coerce one answer to the shape the API expects for its question type."""
    if qtype in NUMERIC_TYPES and _is_int(value):
        return int(value)
    if qtype == "bool" and isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "on", "1"):
            return True
        if low in ("false", "no", "off", "0", ""):
            return False
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
        return (" — NO valid options exist on this system (table %r%s); "
                "create one first"
                % (question.get("table") or "?",
                   ", filter " + repr(question["filter"])
                   if question.get("filter") else ""))
    shown = ", ".join(
        "%s=%s" % (o.get("$key"), o.get("$display") or o.get("name") or o.get("$key"))
        for o in opts[:8])
    return " — valid: %s%s" % (shown, " ..." if len(opts) > 8 else "")


def _declared(bound):
    """A declared min/max, or None. ``min: 0`` is a real floor, not an absence."""
    return None if bound is None or bound == "" else int(bound)


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
                if not re.fullmatch(regex, value):
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
    code runs, so redacting later is too late for that path. What CAN be done
    is to stop masking the non-credential answers in the module's own RETURN
    values, once the recipe's question types have said which answers are
    credentials.

    A value that any secret answer also contributes is never returned here,
    so a password that happens to equal a core count stays masked.
    """
    secret = set(secret_vars or [])
    secret_strings, plain_strings = set(), set()
    for name, value in (answers or {}).items():
        target = secret_strings if name in secret else plain_strings
        target |= maskable_strings(value)
    return plain_strings - secret_strings


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
                       describe_options(name, q, options)))
            elif not required and (default is None or default == ""):
                hints.append(
                    "%r is unanswered and its default is empty (%s)%s"
                    % (name, q.get("display") or qtype,
                       describe_options(name, q, options)))
            continue

        value = _coerce(answers[name], qtype)

        if qtype == "network" and isinstance(value, str) and value != NEW_INTERNAL:
            # Name before key: _is_int used to run first, so a vnet literally
            # named "3" could never be chosen by name — the answer was read as
            # key 3, a different network entirely.
            if vnet_seen.get(value, 0) > 1:
                errors.append(
                    "network name %r is ambiguous — %d networks share it; "
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

        if bool(q.get("required")) and (value is None or value == ""):
            errors.append("required answer %r was supplied empty" % name)
            continue

        errors.extend(check_constraints(name, q, value))

        opts = (options or {}).get(name)
        if opts and qtype in TABLE_BACKED_TYPES and q.get("table"):
            valid = {str(o.get("$key")) for o in opts}
            if str(value) not in valid:
                errors.append("answer %r is not a valid choice%s"
                              % (name, describe_options(name, q, options)))
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
