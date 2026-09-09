"""Every Jinja filter and test used in a role or example must actually exist.

This exists because a nonexistent filter is invisible to the tools you would
expect to catch it. ``ansible-lint`` does not evaluate templates and
``ansible-playbook --syntax-check`` does not either, so an expression like
``| map('div', 60)`` -- there is no ``div`` filter -- passes both cleanly and
fails only at runtime, part-way through a play, on the system you least want
to be debugging on.

Static on purpose. Rendering the expressions was tried first and does not
work: ansible-core 2.21's Templar returns unrendered text rather than raising
for an unknown filter, so a render-based check reports success on everything
and is worse than no check at all.
"""

import pathlib
import re

import pytest

from ansible.plugins.loader import filter_loader, test_loader, init_plugin_loader

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Jinja2 builtins, which Ansible does not re-register as plugins.
BUILTIN_FILTERS = {
    'abs', 'attr', 'batch', 'capitalize', 'center', 'count', 'default', 'd',
    'dictsort', 'escape', 'e', 'filesizeformat', 'first', 'float',
    'forceescape', 'format', 'groupby', 'indent', 'int', 'items', 'join',
    'last', 'length', 'list', 'lower', 'map', 'max', 'min', 'pprint',
    'random', 'reject', 'rejectattr', 'replace', 'reverse', 'round', 'safe',
    'select', 'selectattr', 'slice', 'sort', 'string', 'striptags', 'sum',
    'title', 'tojson', 'trim', 'truncate', 'unique', 'upper', 'urlencode',
    'urlize', 'wordcount', 'wordwrap', 'xmlattr',
}
BUILTIN_TESTS = {
    'boolean', 'callable', 'defined', 'divisibleby', 'eq', 'equalto',
    'escaped', 'even', 'false', 'filter', 'float', 'ge', 'gt', 'greaterthan',
    'in', 'integer', 'iterable', 'le', 'lt', 'lessthan', 'mapping', 'ne',
    'none', 'number', 'odd', 'sameas', 'sequence', 'string', 'test', 'true',
    'undefined',
}

EXPRESSION = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
PIPE = re.compile(r"\|\s*([a-zA-Z_][a-zA-Z0-9_]*)")
# map('f') / select('t') / reject('t') take a plugin name as the FIRST argument.
FIRST_ARG = re.compile(r"\b(?:map|select|reject)\(\s*'([a-zA-Z_][a-zA-Z0-9_]*)'")
# selectattr('attr', 'test') -- the SECOND argument is the test. The first is
# an attribute name and checking it produces nothing but false positives.
SECOND_ARG = re.compile(
    r"\b(?:selectattr|rejectattr)\(\s*'[^']*'\s*,\s*'([a-zA-Z_][a-zA-Z0-9_]*)'")


def _known():
    init_plugin_loader()
    names = set()
    for loader in (filter_loader, test_loader):
        for plugin in loader.all():
            names.add(plugin._load_name)
            names.update(getattr(plugin, 'ansible_aliases', ()) or ())
    return names | BUILTIN_FILTERS | BUILTIN_TESTS


KNOWN = _known()


def _yaml_files():
    for directory in ('roles', 'examples'):
        yield from sorted((ROOT / directory).rglob('*.yml'))


def _used_names(text):
    for expr in EXPRESSION.findall(text):
        for name in PIPE.findall(expr):
            yield name
        for name in FIRST_ARG.findall(expr):
            yield name
        for name in SECOND_ARG.findall(expr):
            yield name


@pytest.mark.parametrize(
    'path', list(_yaml_files()), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_jinja_filter_exists(path):
    missing = sorted({
        name for name in _used_names(path.read_text(encoding='utf-8'))
        if name not in KNOWN
    })
    assert not missing, (
        "%s uses Jinja filter/test(s) that do not exist: %s"
        % (path.relative_to(ROOT), ", ".join(missing)))


def test_the_check_would_catch_a_missing_filter():
    """Guard the guard.

    A checker that silently matches nothing passes every file and is worse
    than no checker, which is exactly how the first version of this failed.
    """
    assert 'div' not in KNOWN
    used = set(_used_names("x: \"{{ [60] | map('div', 60) | list }}\""))
    assert 'div' in used, "the real bug this file exists for went undetected"
    assert {'map', 'list'} <= used


def test_the_check_does_not_flag_attribute_names():
    """selectattr's first argument is an attribute, not a test."""
    used = list(_used_names(
        "x: \"{{ rows | selectattr('write_bytes', 'gt', 0) | list }}\""))
    assert 'write_bytes' not in used
    assert 'gt' in used
