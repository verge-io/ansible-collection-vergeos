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

import functools
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


# Deliberately NOT computed at import time. init_plugin_loader() mutates
# global Ansible state, and pytest imports every test module during collection
# — so doing this at module level reaches into unrelated test files before any
# test has run. Measured: it changed the pass/fail pattern of the pre-existing,
# non-isolated vm and inventory suites. functools.cache keeps it to one call.
@functools.cache
def _known():
    init_plugin_loader()
    names = set()
    for loader in (filter_loader, test_loader):
        for plugin in loader.all():
            names.add(plugin._load_name)
            names.update(getattr(plugin, 'ansible_aliases', ()) or ())
    return names | BUILTIN_FILTERS | BUILTIN_TESTS


def _yaml_files():
    # tests/live is included deliberately. The ladders are the densest Jinja
    # in the repository -- they are where the assertions live -- and they are
    # NOT run by CI, so a nonexistent filter in one of them surfaces only when
    # somebody runs it against a real cluster, which is the worst time to find
    # out.
    for directory in ('roles', 'examples', 'tests/live'):
        yield from sorted((ROOT / directory).rglob('*.yml'))


# A quoted literal inside an expression is data, not syntax. Regexes in this
# repository are full of alternation:
#
#     names | select('match', '^(zz|ZZ)-from-edge$')
#     selectattr('name', 'match', '(?i)^zz-(' ~ p ~ '|from-' ~ p ~ ')')
#
# Scanning those for `|` yields "ZZ" and "from" as filter names, and the check
# fails on two ladders that are perfectly correct. A false positive is not a
# harmless kind of wrong here: a guard that cries wolf is a guard somebody
# deletes. String contents are blanked before the pipe scan -- and only before
# THAT scan, because map('div') and selectattr('x', 'gt') need their literals.
STRING = re.compile(r"'[^']*'|\"[^\"]*\"")


def _without_string_literals(expr):
    return STRING.sub(lambda m: "'" + ' ' * (len(m.group(0)) - 2) + "'", expr)


def _used_names(text):
    for expr in EXPRESSION.findall(text):
        yield from PIPE.findall(_without_string_literals(expr))
        yield from FIRST_ARG.findall(expr)
        yield from SECOND_ARG.findall(expr)


@pytest.mark.parametrize(
    'path', list(_yaml_files()), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_jinja_filter_exists(path):
    missing = sorted({
        name for name in _used_names(path.read_text(encoding='utf-8'))
        if name not in _known()
    })
    assert not missing, (
        "%s uses Jinja filter/test(s) that do not exist: %s"
        % (path.relative_to(ROOT), ", ".join(missing)))


def test_the_check_would_catch_a_missing_filter():
    """Guard the guard.

    A checker that silently matches nothing passes every file and is worse
    than no checker, which is exactly how the first version of this failed.
    """
    assert 'div' not in _known()
    used = set(_used_names("x: \"{{ [60] | map('div', 60) | list }}\""))
    assert 'div' in used, "the real bug this file exists for went undetected"
    assert {'map', 'list'} <= used


@pytest.mark.parametrize('expression', [
    "{{ names | select('match', '^(zz|ZZ)-from-edge$') | list }}",
    "{{ rows | selectattr('name', 'match', '(?i)^zz-(' ~ p ~ '|from-' ~ p ~ ')') }}",
    '{{ x | regex_search("a|b") }}',
])
def test_a_pipe_inside_a_quoted_regex_is_not_a_filter(expression):
    """Both of these are real expressions from the live ladders. Before the
    literals were blanked they reported `ZZ` and `from` as missing filters."""
    used = set(_used_names(expression))
    assert not ({'ZZ', 'from', 'b'} & used), sorted(used)


def test_blanking_literals_does_not_hide_a_real_filter_after_one():
    """The blanking must preserve length and delimiters, or a filter applied
    after a string argument disappears with it."""
    used = set(_used_names("{{ xs | map('trim') | join(', ') | upper }}"))
    assert {'map', 'join', 'upper', 'trim'} <= used


def test_the_check_does_not_flag_attribute_names():
    """selectattr's first argument is an attribute, not a test."""
    used = list(_used_names(
        "x: \"{{ rows | selectattr('write_bytes', 'gt', 0) | list }}\""))
    assert 'write_bytes' not in used
    assert 'gt' in used
