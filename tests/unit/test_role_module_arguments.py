#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Every parameter a role hands a module must be one that module accepts.

Ansible does catch this -- ``Unsupported parameters for (vergeio.vergeos.vm)
module: powerstate`` -- but only when the task actually runs. Roles are full
of tasks that do not: a revocation path, a drift-correction branch, an
``always:`` teardown reached once in a hundred runs. Those are exactly the
tasks nobody exercises before shipping, and exactly the ones an operator
reaches under pressure.

Nothing in CI runs a playbook, so this reads the task files instead and
resolves each ``vergeio.vergeos.*`` action against that module's real
``argument_spec``.

It is deliberately a different question from ``test_sdk_call_signatures.py``,
which checks module -> SDK. This is role -> module. The collection has three
layers and this is the seam between the first two.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import ast
import glob
import os

import pytest
import yaml

# Ansible's own task keywords. A task is a mapping of one action plus these,
# so anything else under the action key is a module parameter.
_ACTION_PREFIX = 'vergeio.vergeos.'


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..'))


def _task_files():
    return sorted(glob.glob(os.path.join(_root(), 'roles', '*', 'tasks',
                                         '*.yml')))


_IDS = [os.path.relpath(p, _root()) for p in _task_files()]


def _flatten(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ('block', 'rescue', 'always'):
            yield from _flatten(task.get(key))


def _dict_keywords(node, into):
    """Collect ``a=dict(...)`` kwargs and ``{'a': ...}`` keys from a call."""
    if isinstance(node, ast.Call):
        for keyword in node.keywords:
            if keyword.arg:
                into.add(keyword.arg)
        for arg in node.args:
            _dict_keywords(arg, into)
    elif isinstance(node, ast.Dict):
        for key in node.keys:
            if isinstance(key, ast.Constant):
                into.add(key.value)


def _spec_keys(module_name):
    """Parameter names a module accepts, read from its argument_spec.

    Parsed rather than imported: importing a module executes its
    DOCUMENTATION and its imports, and several of them need the SDK present.
    """
    path = os.path.join(_root(), 'plugins', 'modules', '%s.py' % module_name)
    tree = ast.parse(open(path).read())
    keys = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'update'):
            _dict_keywords(node, keys)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Name)
                        and target.id.endswith('argument_spec')):
                    _dict_keywords(node.value, keys)
    return keys


# vergeos_argument_spec(), merged into every module. Named explicitly rather
# than parsed, so a typo in the shared spec cannot quietly widen this test.
_BASE = frozenset(['host', 'username', 'password', 'api_key', 'insecure'])


def _module_exists(name):
    return os.path.exists(os.path.join(_root(), 'plugins', 'modules',
                                       '%s.py' % name))


@pytest.mark.parametrize('path', _task_files(), ids=_IDS)
def test_roles_pass_only_parameters_their_modules_accept(path):
    with open(path) as handle:
        tasks = list(_flatten(yaml.safe_load(handle)))

    problems = []
    for task in tasks:
        for key, value in task.items():
            if not key.startswith(_ACTION_PREFIX):
                continue
            module_name = key[len(_ACTION_PREFIX):]
            if not _module_exists(module_name):
                problems.append("task %r calls %s, which is not a module in "
                                "this collection"
                                % (task.get('name', '<unnamed>'), key))
                continue
            if not isinstance(value, dict):
                continue            # free-form `module: key=value` string
            unknown = sorted(set(value) - _spec_keys(module_name) - _BASE)
            if unknown:
                problems.append(
                    "task %r passes %s to %s, which does not accept %s"
                    % (task.get('name', '<unnamed>'), unknown, key,
                       'it' if len(unknown) == 1 else 'them'))

    assert not problems, (
        '%s:\n  %s\n\nAnsible would reject these at runtime -- but only if '
        'the task runs. A revocation path or an always: teardown can sit '
        'wrong for a long time.'
        % (os.path.relpath(path, _root()), '\n  '.join(problems)))


def test_the_guard_can_actually_fail():
    """A guard that cannot fail is decoration."""
    import tempfile
    body = (
        "- name: Stop a VM\n"
        "  vergeio.vergeos.vm:\n"
        "    name: thing\n"
        "    powerstate: stopped\n"
    )
    with tempfile.NamedTemporaryFile('w', suffix='.yml', delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        with pytest.raises(AssertionError) as caught:
            test_roles_pass_only_parameters_their_modules_accept(path)
        assert 'powerstate' in str(caught.value)
    finally:
        os.unlink(path)


def test_a_module_that_does_not_exist_is_caught():
    """The other half: `nas_nfs_share_info` looks real and is not.

    Measured while writing the vm_backup ladder -- there is no info module
    for NFS shares, and the playbook failed with "couldn't resolve
    module/action". A role would fail the same way, later.
    """
    import tempfile
    body = (
        "- name: Read the shares\n"
        "  vergeio.vergeos.nas_nfs_share_info:\n"
        "    insecure: true\n"
    )
    with tempfile.NamedTemporaryFile('w', suffix='.yml', delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        with pytest.raises(AssertionError) as caught:
            test_roles_pass_only_parameters_their_modules_accept(path)
        assert 'not a module in this collection' in str(caught.value)
    finally:
        os.unlink(path)


def test_a_correct_task_passes():
    import tempfile
    body = (
        "- name: Ensure a VM\n"
        "  vergeio.vergeos.vm:\n"
        "    host: h\n"
        "    username: u\n"
        "    password: p\n"
        "    insecure: true\n"
        "    name: thing\n"
        "    cpu_cores: 2\n"
        "    ram: 1024\n"
        "    state: present\n"
    )
    with tempfile.NamedTemporaryFile('w', suffix='.yml', delete=False) as fh:
        fh.write(body)
        path = fh.name
    try:
        test_roles_pass_only_parameters_their_modules_accept(path)
    finally:
        os.unlink(path)


def test_the_specs_are_actually_being_read():
    """Guard the guard: an empty spec would make everything pass."""
    assert 'cpu_cores' in _spec_keys('vm')
    assert 'allowed_hosts' in _spec_keys('nas_nfs_share')
    assert 'max_exports' in _spec_keys('vm_export')
    assert 'nonsense_parameter' not in _spec_keys('vm')
