#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""A role that parses command output must still run that command in check mode.

Issue #28. Six roles aborted under ``--check`` with a stack trace instead of
skipping cleanly -- and all six are read-only reporters, which is precisely
when an operator reaches for ``--check``::

    TASK [health_report : Run the health scan]
    skipping: [localhost]

    TASK [health_report : Parse the report]
    [ERROR]: The filter plugin 'ansible.builtin.from_json' failed:
    Expecting value: line 1 column 1 (char 0)

The shape is always the same. A ``command`` task is read-only and says so with
``changed_when: false``, but does not say ``check_mode: false``. Under
``--check`` Ansible skips it, ``<var>.stdout`` is empty, and the next task
pipes ``''`` into ``from_json``.

``changed_when: false`` and ``check_mode: false`` look like they mean the same
thing and do not. The first is a claim about the report; the second is
permission to run anyway. A read-only task needs both.

There are two valid answers, not one, and the difference is whether the
command WRITES anything:

  read-only  ``check_mode: false`` on the producer. It runs, the parse works,
             and --check reports real findings -- which is the whole point of
             running a reporting role in check mode.

  writes     leave the producer skipped, and make the PARSE survive an empty
             stdout: ``| default('{}', true) | from_json``. billing_export is
             this case -- it writes CSVs to the controller, and a --check run
             that quietly produced an invoicing artifact would be worse than
             one that crashed.

Forcing the first answer everywhere would make check mode write files. The
guard accepts either.

What it does NOT accept, because it does not work: ``when: not
ansible_check_mode`` on the parse. ``set_fact`` finalizes its arguments before
the condition is evaluated, so ``from_json`` still runs on the empty string
and the play still aborts with the same trace. That looked obviously correct,
passed review in this file's own first draft, and cost a live ladder run to
disprove -- which is why it is named here rather than left as an omission.

Why this is a test and not a lint rule: nothing in CI runs a playbook at all,
in check mode or otherwise -- the ladders need a live cluster. This check
needs neither. It reads the task files, finds every variable that is parsed as
JSON, and insists the task that produced it is allowed to run.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import glob
import os
import re

import pytest
import yaml


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..'))


def _task_files():
    return sorted(glob.glob(os.path.join(_root(), 'roles', '*', 'tasks',
                                         '*.yml')))


_IDS = [os.path.relpath(p, _root()) for p in _task_files()]

# `foo_raw.stdout | from_json`, and the same through a `json_query`/`trim`
# chain -- the variable name is what matters, not the filters after it.
_PARSED = re.compile(r'(\w+)\s*\.\s*stdout\b[^}]*\|\s*from_json')

# Same shape, but capturing the variable, for the "defended" case.
_DEFENDED_VAR = re.compile(
    r'(\w+)\s*\.\s*stdout\b[^}]*\|\s*default\([^)]*\)[^}]*\|\s*from_json')

# Task keys that produce stdout worth parsing. `uri` and the vergeos modules
# are unaffected: they run in check mode, or return structured data already.
_PRODUCERS = ('ansible.builtin.command', 'ansible.builtin.shell',
              'ansible.builtin.script', 'command', 'shell', 'script')

# The second remedy, for a producer that writes: make the parse survive an
# empty stdout. It must be in the expression -- see the module docstring for
# why a `when:` on the same task is not enough.
_PARSE_DEFENDED = re.compile(
    r'\.\s*stdout\b[^}]*\|\s*default\([^)]*\)[^}]*\|\s*from_json')


def _flatten(tasks):
    """Every task in a file, including the ones inside block/rescue/always."""
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ('block', 'rescue', 'always'):
            yield from _flatten(task.get(key))


def _load(path):
    with open(path) as handle:
        return list(_flatten(yaml.safe_load(handle)))


@pytest.mark.parametrize('path', _task_files(), ids=_IDS)
def test_every_parsed_command_runs_in_check_mode(path):
    with open(path) as handle:
        body = handle.read()

    parsed = set(_PARSED.findall(body))
    if not parsed:
        return

    registered = {task['register']: task
                  for task in _load(path)
                  if isinstance(task.get('register'), str)}

    # The other remedy: the parse itself tolerates an empty stdout.
    defended = set(_DEFENDED_VAR.findall(body))

    offenders = []
    for name in sorted(parsed):
        task = registered.get(name)
        if task is None:
            continue                      # registered in another file
        if not any(key in task for key in _PRODUCERS):
            continue                      # not a command; check mode is fine
        if task.get('check_mode') is False:
            continue
        if name in defended:
            continue
        offenders.append('%r (registered by %r)'
                         % (name, task.get('name', '<unnamed>')))

    assert not offenders, (
        "%s parses these as JSON, but the task that produces them is skipped "
        "under --check, so from_json is handed an empty string and the play "
        "aborts with a stack trace: %s.\n\n"
        "Two ways out, and which one depends on whether the command WRITES:\n"
        "  read-only -> `check_mode: false` on the producing task. Note that "
        "`changed_when: false` is not the same claim: it says the task "
        "changes nothing, not that it is safe to run anyway.\n"
        "  writes    -> `| default(\'{}\', true) | from_json` on the parse, so "
        "check mode produces nothing instead of crashing. A `when:` on that "
        "task does NOT work: set_fact finalizes its args before the condition "
        "is evaluated.\n"
        "This is issue #28."
        % (os.path.relpath(path, _root()), ', '.join(offenders)))


def test_the_guard_can_actually_fail():
    """A guard that cannot fail is decoration.

    #28's exact shape, assembled here rather than left to a role to
    reintroduce.
    """
    import tempfile
    body = (
        "- name: Run the scan\n"
        "  ansible.builtin.command:\n"
        "    cmd: /bin/true\n"
        "  register: probe_raw\n"
        "  changed_when: false\n"
        "\n"
        "- name: Parse it\n"
        "  ansible.builtin.set_fact:\n"
        "    probe: \"{{ probe_raw.stdout | from_json }}\"\n"
    )
    with tempfile.NamedTemporaryFile('w', suffix='.yml', delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        with pytest.raises(AssertionError) as caught:
            test_every_parsed_command_runs_in_check_mode(path)
        assert 'probe_raw' in str(caught.value)
    finally:
        os.unlink(path)


def test_a_defended_parse_is_also_accepted():
    """The remedy for a producer that writes.

    Demanding `check_mode: false` everywhere would make a --check run of
    billing_export actually write an invoicing CSV.
    """
    import tempfile
    body = (
        "- name: Run the export\n"
        "  ansible.builtin.command:\n"
        "    cmd: /bin/true\n"
        "  register: probe_raw\n"
        "  changed_when: true\n"
        "\n"
        "- name: Parse it\n"
        "  ansible.builtin.set_fact:\n"
        "    probe: \"{{ probe_raw.stdout | default('{}', true) "
        "| from_json }}\"\n"
    )
    with tempfile.NamedTemporaryFile('w', suffix='.yml', delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        test_every_parsed_command_runs_in_check_mode(path)   # must not raise
    finally:
        os.unlink(path)


def test_a_bare_when_is_not_accepted():
    """The remedy that looks right and is not.

    set_fact finalizes its arguments before evaluating `when`, so from_json
    runs on the empty string anyway. Measured: it aborted a live ladder run
    with the identical trace the condition was added to prevent.
    """
    import tempfile
    body = (
        "- name: Run the export\n"
        "  ansible.builtin.command:\n"
        "    cmd: /bin/true\n"
        "  register: probe_raw\n"
        "  changed_when: true\n"
        "\n"
        "- name: Parse it\n"
        "  ansible.builtin.set_fact:\n"
        "    probe: \"{{ probe_raw.stdout | from_json }}\"\n"
        "  when: not ansible_check_mode\n"
    )
    with tempfile.NamedTemporaryFile('w', suffix='.yml', delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        with pytest.raises(AssertionError):
            test_every_parsed_command_runs_in_check_mode(path)
    finally:
        os.unlink(path)
