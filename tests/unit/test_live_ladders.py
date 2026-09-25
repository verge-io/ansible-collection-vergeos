#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Guards for tests/live/, which CI cannot run.

The live ladders need a VergeOS system, so nothing in CI executes them. That
makes them the easiest files in the repository to let rot, and they have
rotted twice already:

  #54  the ported README described ~20 ladders that were never on this
       branch, plus a results table dated 2026-09-11.
  #32  verify-catalog.yml shelled out to docs/repro/d1-d6/readback.py, a
       helper that does not exist here. The ladder had passed on the port
       branch, where it did. Two rungs died on a missing file after nine
       rungs of real assertions had already run.
  #44  verify-vnet-rule.yml invoked the same kind of missing helper as a BARE
       name -- argv: [..., scratch_net.py, ...] -- which the check written for
       #32 did not see, while it caught the identical reference in
       verify-network-policy.yml four lines of YAML away. A guard that catches
       one spelling of a defect and not the other is worse than none: it reads
       as coverage. Both spellings are checked now.

None of these was catchable by ansible-lint: a README is not a playbook, and
lint does not resolve the argv of a command task. All are catchable by reading
the directory, which is what this file does.

#29 is the same kind of rot. ``localhost.ini`` is the interpreter pin every
ladder depends on, and an unquoted Jinja value makes the INI parser reject
the whole file. ``ansible-inventory`` still exits 0 when that happens -- it
warns and falls back to implicit localhost -- so a test that only checks the
exit code stays green while the pin is gone.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import glob
import os
import re
import tempfile

import pytest
from ansible.inventory.manager import InventoryManager
from ansible.parsing.dataloader import DataLoader


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..'))


def _live_dir():
    return os.path.join(_root(), 'tests', 'live')


def _ladders():
    return sorted(os.path.basename(p)
                  for p in glob.glob(os.path.join(_live_dir(), 'verify-*.yml')))


def _readme():
    with open(os.path.join(_live_dir(), 'README.md')) as fh:
        return fh.read()


def test_every_ladder_is_documented():
    """A ladder nobody knows about is a ladder nobody runs."""
    readme = _readme()
    missing = [name for name in _ladders() if '`%s`' % name not in readme]
    assert not missing, (
        "these ladders exist but tests/live/README.md does not mention them: %s"
        % missing)


def test_the_readme_names_no_ladder_that_is_missing():
    """#54: the ported README documented ladders that were never here."""
    on_disk = set(_ladders())
    named = set(re.findall(r'`(verify-[\w-]+\.ya?ml)`', _readme()))
    ghosts = sorted(named - on_disk)
    assert not ghosts, (
        "tests/live/README.md documents ladders that do not exist: %s.\n"
        "Either the file was never ported or the name drifted." % ghosts)


# A path built from playbook_dir is a real filesystem reference...
_PLAYBOOK_DIR_PATH = re.compile(r'\{\{\s*playbook_dir\s*\}\}(/[^"\'\s\]]+)')

# ...but it is not the only spelling: a bare script name in an argv list is a
# repo-relative reference too, and that is the one #44 slipped through on.
_SCRIPT_NAME = re.compile(r'[\w./-]+\.(?:py|sh)\b')

# ...and a third spelling, which cost a live run of verify-health-report.yml:
# the ladder defines a var FROM playbook_dir and then references files through
# that var. The two checks above see `{{ playbook_dir }}/../../tests/fixtures`,
# resolve it to a directory that exists, and never look at
# `{{ fixtures }}/health-healthy.json` -- which did not exist here at all. One
# level of indirection is enough to cover it and cheap to follow.
_VAR_FROM_PLAYBOOK_DIR = re.compile(
    r'^\s*(\w+):\s*["\']\{\{\s*playbook_dir\s*\}\}(/[^"\']*)["\']',
    re.M)

# Paths that are deliberately outside this repository.
_NOT_OURS = ('/usr/', '/bin/', '/etc/', '/tmp/', 'python')


def _playbook_files():
    return sorted(glob.glob(os.path.join(_live_dir(), '*.yml')))


@pytest.mark.parametrize('path', _playbook_files(),
                         ids=[os.path.basename(p) for p in _playbook_files()])
def test_playbook_dir_references_resolve(path):
    """#32: a ladder shelled out to a helper script that is not in this repo.

    The failure surfaced only on a live run, nine rungs deep, as
    "can't open file ... readback.py". Resolving the path here costs nothing
    and fails in CI instead.
    """
    with open(path) as fh:
        body = fh.read()
    # Comments are prose. They name files by their repo path
    # ("module_utils/rbac.py") as shorthand, which is not a reference the
    # playbook resolves and not this test's business -- scanning them turns a
    # useful guard into one that has to be argued with.
    executable = '\n'.join(line for line in body.splitlines()
                           if not line.lstrip().startswith('#'))

    candidates = list(_PLAYBOOK_DIR_PATH.findall(body))
    candidates += [name for name in _SCRIPT_NAME.findall(executable)
                   if not any(skip in name for skip in _NOT_OURS)]

    dangling = []
    for ref in candidates:
        target = os.path.normpath(os.path.join(_live_dir(), ref.lstrip('/')))
        if not os.path.exists(target):
            dangling.append(ref)

    # One level of indirection: `fixtures: "{{ playbook_dir }}/../fixtures"`
    # followed by `"{{ fixtures }}/health-healthy.json"`.
    for name, base in _VAR_FROM_PLAYBOOK_DIR.findall(body):
        used = re.compile(r'\{\{\s*%s\s*\}\}(/[^"\'\s}]+)' % re.escape(name))
        for suffix in used.findall(executable):
            ref = base.rstrip('/') + suffix
            target = os.path.normpath(os.path.join(_live_dir(),
                                                   ref.lstrip('/')))
            if not os.path.exists(target):
                dangling.append('{{ %s }}%s -> %s' % (name, suffix, ref))
    assert not dangling, (
        "%s references files that do not exist in this repository: %s"
        % (os.path.basename(path), sorted(set(dangling))))


def _group_names(host):
    names = []
    for group in host.groups:
        names.append(getattr(group, 'name', group))
    return names


def _inventory(path):
    return InventoryManager(loader=DataLoader(), sources=[path])


def test_localhost_ini_parses_and_pins_the_interpreter():
    """#29. The pin is a single host var, and the host is in the local group.

    Implicit localhost also answers to the name ``localhost`` and already
    defaults to ansible_playbook_python, which is why a rejected inventory
    looks like it worked. Membership of ``local`` is what distinguishes the
    file actually being read.
    """
    path = os.path.join(_live_dir(), 'localhost.ini')
    inventory = _inventory(path)
    hosts = [host.name for host in inventory.get_hosts()]
    assert hosts == ['localhost'], (
        "tests/live/localhost.ini did not parse. ansible-inventory exits 0 "
        "and falls back to implicit localhost when the INI plugin rejects "
        "the file, so an empty host list is the failure, not a non-zero "
        "exit. Unquoted Jinja is split on whitespace (#29). Got %s" % hosts)
    host = inventory.get_host('localhost')
    assert 'local' in _group_names(host), (
        "localhost is the implicit one, not the host from localhost.ini. "
        "groups: %s" % _group_names(host))
    pin = host.vars.get('ansible_python_interpreter')
    assert pin == '{{ ansible_playbook_python }}', (
        "ansible_python_interpreter is %r, not the playbook interpreter. "
        "Quoting has to survive INI parsing as the Jinja expression itself, "
        "not as a literal quoted string and not as three tokens." % pin)
    assert host.vars.get('ansible_connection') == 'local'


def test_unquoted_jinja_is_not_an_inventory():
    """The line #29 shipped with, so this guard can fail.

    Measured: the INI plugin tokenises on whitespace, ``ansible_playbook_python``
    has no ``=``, and the whole file is rejected. A quoted value parses.
    """
    body = (
        "[local]\n"
        "localhost ansible_connection=local "
        "ansible_python_interpreter={{ ansible_playbook_python }}\n"
    )
    handle = tempfile.NamedTemporaryFile('w', suffix='.ini', delete=False)
    try:
        handle.write(body)
        handle.close()
        inventory = _inventory(handle.name)
        hosts = [host.name for host in inventory.get_hosts()]
        assert hosts == [], (
            "an unquoted Jinja interpreter pin parsed as an inventory "
            "(%s). The INI host line splits on whitespace, so this value "
            "has to be quoted or the file is invalid (#29)." % hosts)
    finally:
        os.unlink(handle.name)
