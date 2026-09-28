#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""No module may read a parameter it does not declare.

Issue #95: vm.py carried resolve_snapshot_profile(), which read
``module.params['snapshot_profile']``. There is no such option in the module's
argument_spec and none in its documentation. The function was never called, so
the KeyError it would have raised never happened -- it simply sat there,
shipped in v2.1.0, advertising a capability the module does not have.

It survived a rewrite: #85 changed the ``get(name=)`` call INSIDE that dead
function to ``resolve_one``, correctly and pointlessly, because nothing said
the function was unreachable.

Two ways this goes wrong, and this file catches both:

  the option was never added   -> KeyError the first time the line runs, or
                                  dead code advertising a feature, as in #95
  the option was renamed       -> the same, delayed until that branch is hit

Neither is caught by ansible-test. validate-modules compares DOCUMENTATION
against argument_spec, which is a different pair: a parameter can be absent
from both and still be read by the code.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import ast
import glob
import os
import re

import pytest


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..'))


def _shared_auth_options():
    """Whatever vergeos_argument_spec() contributes to every module."""
    path = os.path.join(_root(), 'plugins', 'module_utils', 'vergeos.py')
    with open(path) as handle:
        source = handle.read()
    block = re.search(r'def vergeos_argument_spec\(.*?\n    return dict\((.*?)\n    \)',
                      source, re.S)
    assert block, 'could not find vergeos_argument_spec() in module_utils'
    return set(re.findall(r'^\s{8}(\w+)=dict\(', block.group(1), re.M))


def _declared_options(path):
    """Option names from the module's own argument_spec.update(...) call.

    Returns None only for a file that does not build a spec at all. A module
    with no options of its own -- cluster_info, which takes nothing but the
    connection -- returns an empty set and is still checked, because "declares
    nothing" and "is exempt" are different claims and only one of them is
    true here.
    """
    with open(path) as handle:
        source = handle.read()
    if 'vergeos_argument_spec' not in source:
        return None
    block = re.search(r'argument_spec\.update\((.*?)\n    \)', source, re.S)
    if not block:
        return set()
    names = set(re.findall(r'^\s{8}(\w+)=dict\(', block.group(1), re.M))
    # aliases are legitimate reader names too
    for alias_list in re.findall(r"aliases=\[([^\]]*)\]", block.group(1)):
        names |= set(re.findall(r"'([^']+)'", alias_list))
    return names


def _params_read(path):
    """``(lineno, name)`` for each literal module.params[...] / .get(...)."""
    with open(path) as handle:
        tree = ast.parse(handle.read(), filename=path)

    def is_params(node):
        return (isinstance(node, ast.Attribute) and node.attr == 'params'
                and isinstance(node.value, ast.Name)
                and node.value.id == 'module')

    found = []
    for node in ast.walk(tree):
        # module.params['x']
        if isinstance(node, ast.Subscript) and is_params(node.value):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                found.append((node.lineno, key.value))
        # module.params.get('x')
        elif (isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == 'get'
              and is_params(node.func.value)
              and node.args
              and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)):
            found.append((node.lineno, node.args[0].value))
    return found


_MODULES = sorted(glob.glob(os.path.join(_root(), 'plugins', 'modules', '*.py')))
_IDS = [os.path.basename(p) for p in _MODULES]


@pytest.mark.parametrize('path', _MODULES, ids=_IDS)
def test_every_parameter_read_is_declared(path):
    declared = _declared_options(path)
    if declared is None:
        pytest.skip('%s does not build a vergeos argument spec'
                    % os.path.basename(path))
    allowed = declared | _shared_auth_options()

    undeclared = sorted(
        {(lineno, name) for lineno, name in _params_read(path)
         if name not in allowed})

    assert not undeclared, (
        "%s reads parameters it does not declare:\n  %s\n\n"
        "Either add the option to argument_spec, or delete the code that "
        "reads it. A helper that reads an undeclared parameter is either a "
        "KeyError waiting to happen or dead code advertising a feature the "
        "module does not have -- that is issue #95."
        % (os.path.basename(path),
           '\n  '.join('line %d: %r' % pair for pair in undeclared)))


def test_the_guard_can_actually_fail():
    """#95's exact shape, checked against a throwaway file."""
    import tempfile

    source = (
        'def main():\n'
        '    argument_spec = vergeos_argument_spec()\n'
        '    argument_spec.update(\n'
        "        name=dict(type='str'),\n"
        '    )\n'
        '\n'
        'def helper(module):\n'
        "    return module.params['snapshot_profile']\n"
    )
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as handle:
        handle.write(source)
        path = handle.name
    try:
        assert _declared_options(path) == {'name'}
        assert _params_read(path) == [(8, 'snapshot_profile')]
    finally:
        os.unlink(path)
