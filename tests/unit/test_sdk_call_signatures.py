#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Every SDK call this collection makes must exist, with the keywords used.

Issue #92: `member` called ``client.users.get(username=...)``. No pyvergeos
version has ever had a ``username`` parameter -- it is ``name``, on 1.2.6 and
on 1.6.1 alike -- so the module raised TypeError before reaching any VergeOS
logic. It shipped in v2.1.0 and in every release before it.

Nothing caught it, and unit tests as normally written cannot: the client is a
MagicMock, and a MagicMock accepts *any* keyword argument happily. The mock
that was supposed to prove the module worked was the reason the bug was
invisible.

So this test does not mock. It reads the real SDK's signatures and checks the
calls the modules actually write:

    client.<manager>.<method>(<keywords>)

Manager classes are resolved from VergeClient's property return annotations,
which needs no connection -- VergeClient.__init__ connects eagerly, so
instantiating one here is not an option.

What it deliberately does NOT do: follow variables, or check argument types,
or check anything that is not a direct two-dot call on a name called `client`.
A narrow check that is always right beats a broad one that gets muted.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import ast
import glob
import importlib
import inspect
import os
import pkgutil

import pytest


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..'))


def _manager_classes():
    """Every ``*Manager`` class in pyvergeos.resources, by name."""
    from pyvergeos import resources
    found = {}
    for mod in pkgutil.iter_modules(resources.__path__):
        module = importlib.import_module('pyvergeos.resources.%s' % mod.name)
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and name.endswith('Manager'):
                found.setdefault(name, obj)
    return found


def _client_managers():
    """``{attribute: manager class}`` for VergeClient, without connecting.

    Each manager is a property whose return annotation names its class. The
    annotation is a string (the module uses postponed evaluation and imports
    the classes under TYPE_CHECKING), so it is matched by name.
    """
    from pyvergeos.client import VergeClient
    classes = _manager_classes()
    resolved = {}
    for attr, prop in vars(VergeClient).items():
        if not isinstance(prop, property) or prop.fget is None:
            continue
        annotation = prop.fget.__annotations__.get('return')
        if isinstance(annotation, str) and annotation in classes:
            resolved[attr] = classes[annotation]
    return resolved


def _calls_in(path):
    """``(lineno, manager, method, keywords)`` for each ``client.X.Y(...)``."""
    with open(path) as handle:
        tree = ast.parse(handle.read(), filename=path)

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        owner = func.value
        if not (isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == 'client'):
            continue
        keywords = sorted(kw.arg for kw in node.keywords if kw.arg)
        found.append((node.lineno, owner.attr, func.attr, keywords))
    return found


_MODULE_FILES = sorted(
    glob.glob(os.path.join(_root(), 'plugins', 'modules', '*.py'))
    + glob.glob(os.path.join(_root(), 'plugins', 'module_utils', '*.py'))
    + glob.glob(os.path.join(_root(), 'plugins', 'inventory', '*.py')))
_MODULE_IDS = [os.path.relpath(p, _root()) for p in _MODULE_FILES]


@pytest.mark.parametrize('path', _MODULE_FILES, ids=_MODULE_IDS)
def test_every_client_call_exists_on_the_real_sdk(path):
    managers = _client_managers()
    assert managers, 'could not resolve any manager from VergeClient'

    problems = []
    for lineno, manager, method, keywords in _calls_in(path):
        cls = managers.get(manager)
        if cls is None:
            problems.append(
                'line %d: client.%s is not a VergeClient manager'
                % (lineno, manager))
            continue

        attr = getattr(cls, method, None)
        if attr is None:
            problems.append(
                'line %d: %s has no %r' % (lineno, cls.__name__, method))
            continue
        if not callable(attr):
            continue

        try:
            signature = inspect.signature(attr)
        except (TypeError, ValueError):       # C-implemented or wrapped
            continue

        accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD
                          for p in signature.parameters.values())
        if accepts_any:
            continue

        allowed = {name for name, p in signature.parameters.items()
                   if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                 inspect.Parameter.KEYWORD_ONLY)
                   and name != 'self'}
        unknown = sorted(set(keywords) - allowed)
        if unknown:
            problems.append(
                'line %d: %s.%s() does not accept %s (it takes %s)'
                % (lineno, cls.__name__, method, unknown, sorted(allowed)))

    assert not problems, (
        '%s makes SDK calls that do not exist:\n  %s\n\n'
        'This is issue #92. A MagicMock accepts anything, so unit tests '
        'cannot see it -- only the real signature can.'
        % (os.path.relpath(path, _root()), '\n  '.join(problems)))


def test_the_guard_can_actually_fail():
    """A guard that cannot fail is decoration.

    #92's exact call, checked against the real SDK.
    """
    managers = _client_managers()
    signature = inspect.signature(managers['users'].get)
    assert 'username' not in signature.parameters, (
        'pyvergeos has grown a username parameter; #92 needs revisiting')
    assert 'name' in signature.parameters
