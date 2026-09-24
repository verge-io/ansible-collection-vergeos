#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Every vergeio.vergeos.* reference must resolve, or be declared.

ansible-lint's syntax-check[unknown-module] does this, but three live ladders
are excluded from it (see .ansible-lint) because they legitimately call
modules that arrive in a later wave. This test covers everything ansible-lint
does plus those three, and covers roles as well as modules, which
syntax-check does not distinguish.

The allowlist below is SELF-EXPIRING. Once a module exists, the test fails
until its entry is removed -- so a stale waiver cannot sit here quietly the
way the six .claude/hooks sanity-ignore entries did (#16).
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import glob
import os
import re

import pytest

# Referenced by a ladder or example, not yet in the collection. Each entry
# must name the issue that removes it.
# Empty, and meant to stay that way. It held 'catalog' (#32) and 'api_key'
# (#30) while their ladders sat on this branch ahead of their modules; both
# landed and both entries expired, which is the mechanism working. Add an
# entry only for a module that is genuinely arriving, always with its issue.
FUTURE = {}

_REFERENCE = re.compile(r'vergeio\.vergeos\.(\w+)')


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..'))


def _present():
    root = _root()
    modules = {os.path.basename(p)[:-3]
               for p in glob.glob(os.path.join(root, 'plugins', 'modules', '*.py'))
               if not os.path.basename(p).startswith('__')}
    roles = {os.path.basename(p)
             for p in glob.glob(os.path.join(root, 'roles', '*'))
             if os.path.isdir(p)}
    plugins = {os.path.basename(p)[:-3]
               for p in glob.glob(os.path.join(root, 'plugins', '*', '*.py'))
               if not os.path.basename(p).startswith('__')}
    return modules | roles | plugins


def _references():
    """Every vergeio.vergeos.<name> in the repository, with where it came from."""
    root = _root()
    found = {}
    for sub in ('tests', 'examples', 'roles', 'plugins', 'inventory'):
        base = os.path.join(root, sub)
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                if not name.endswith(('.yml', '.yaml', '.j2', '.cfg', '.example')):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    text = open(path, errors='ignore').read()
                except OSError:
                    continue
                for match in _REFERENCE.finditer(text):
                    found.setdefault(match.group(1), set()).add(
                        os.path.relpath(path, root))
    return found


def test_every_reference_resolves_or_is_declared():
    present = _present()
    dangling = {}
    for name, where in _references().items():
        if name in present or name in FUTURE:
            continue
        dangling[name] = sorted(where)
    assert not dangling, (
        "These vergeio.vergeos.* references do not resolve to a module, role "
        "or plugin, and are not declared in FUTURE:\n"
        + "\n".join("  %-14s %s" % (k, v) for k, v in sorted(dangling.items()))
        + "\n\nEither it is a typo, or it is a module arriving later -- in "
          "which case add it to FUTURE with the issue that lands it.")


@pytest.mark.parametrize('name', sorted(FUTURE))
def test_the_allowlist_expires_by_itself(name):
    """Once the module lands, its waiver must go.

    This is the check the six dead sanity-ignore files never had: they named
    files that did not exist and nothing noticed for months (#16).
    """
    assert name not in _present(), (
        "vergeio.vergeos.%s now exists, so its FUTURE entry (%s) is stale. "
        "Remove it from tests/unit/test_module_references.py, and remove the "
        "matching exclude_paths entry from .ansible-lint if that was its only "
        "reason to be there." % (name, FUTURE[name]))


def test_allowlist_entries_name_an_issue():
    """A waiver without a reason is how a temporary exception becomes
    permanent."""
    for name, reason in FUTURE.items():
        assert re.search(r'#\d+', reason), (
            "FUTURE[%r] does not name the issue that removes it: %r"
            % (name, reason))
