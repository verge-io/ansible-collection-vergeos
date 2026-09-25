#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""resolve_one(): refuse to guess between duplicate names (#72).

VergeOS does not enforce unique names on the tables this collection looks up
by name. The SDK's get(name=) is a documented single-get -- internally
list(filter="name eq <quoted>", limit=1)[0] -- so it returns the FIRST match
and cannot report a second.

Confirmed on VergeOS 26.1.8: two catalogs created with the same name both
succeed, list() shows both, and get(name=) silently returns one of them.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import ast
import glob
import os

import pytest
from unittest.mock import MagicMock

from pyvergeos.exceptions import NotFoundError


def _manager(*rows):
    m = MagicMock()
    m.list.return_value = list(rows)
    return m


def _module():
    m = MagicMock()
    # The real fail_json raises SystemExit. A bare MagicMock returns, which
    # would let resolve_one() fall through and return the wrong object -- the
    # exact failure this helper exists to prevent.
    m.fail_json.side_effect = SystemExit(1)
    return m


def _resolve_one():
    from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import resolve_one
    return resolve_one


class TestSingleMatch:

    def test_one_match_is_returned(self, make_resource):
        row = make_resource({'$key': 7, 'name': 'web-01'})
        assert _resolve_one()(_module(), _manager(row), 'web-01', 'VM') is row

    def test_other_names_are_not_matched(self, make_resource):
        rows = [make_resource({'$key': 1, 'name': 'web-01'}),
                make_resource({'$key': 2, 'name': 'web-02'})]
        out = _resolve_one()(_module(), _manager(*rows), 'web-02', 'VM')
        assert dict(out)['$key'] == 2

    def test_matching_is_exact_not_substring(self, make_resource):
        """'web' must not match 'web-01' -- a prefix match on a delete path is
        how you lose the wrong VM."""
        rows = [make_resource({'$key': 1, 'name': 'web-01'})]
        with pytest.raises(NotFoundError):
            _resolve_one()(_module(), _manager(*rows), 'web', 'VM')

    def test_matching_is_case_sensitive(self, make_resource):
        rows = [make_resource({'$key': 1, 'name': 'Web-01'})]
        with pytest.raises(NotFoundError):
            _resolve_one()(_module(), _manager(*rows), 'web-01', 'VM')


class TestNoMatch:
    """Absent must keep raising NotFoundError, because every caller already
    has a try/except around it -- that is what makes this a drop-in."""

    def test_empty_table_raises_not_found(self):
        with pytest.raises(NotFoundError):
            _resolve_one()(_module(), _manager(), 'web-01', 'VM')

    def test_the_message_names_the_kind_and_the_name(self):
        with pytest.raises(NotFoundError) as exc:
            _resolve_one()(_module(), _manager(), 'web-01', 'VM')
        assert 'VM' in str(exc.value)
        assert 'web-01' in str(exc.value)


class TestDuplicates:
    """The point of the issue."""

    def test_two_matches_refuse_to_guess(self, make_resource):
        rows = [make_resource({'$key': 11, 'name': 'dup'}),
                make_resource({'$key': 22, 'name': 'dup'})]
        module = _module()
        with pytest.raises(SystemExit):
            _resolve_one()(module, _manager(*rows), 'dup', 'catalog')
        msg = module.fail_json.call_args[1]['msg']
        assert 'refusing to guess' in msg
        assert 'catalog' in msg

    def test_the_failure_lists_the_colliding_keys(self, make_resource):
        """Without the keys the operator cannot find what to delete."""
        rows = [make_resource({'$key': 11, 'name': 'dup'}),
                make_resource({'$key': 22, 'name': 'dup'})]
        module = _module()
        with pytest.raises(SystemExit):
            _resolve_one()(module, _manager(*rows), 'dup', 'catalog')
        msg = module.fail_json.call_args[1]['msg']
        assert '11' in msg and '22' in msg

    def test_three_matches_also_refuse(self, make_resource):
        rows = [make_resource({'$key': i, 'name': 'dup'}) for i in (1, 2, 3)]
        module = _module()
        with pytest.raises(SystemExit):
            _resolve_one()(module, _manager(*rows), 'dup', 'VM')
        assert 'Found 3' in module.fail_json.call_args[1]['msg']


class TestListKwargs:

    def test_extra_selectors_are_forwarded(self, make_resource):
        """tag.py narrows by category_name, which list() accepts natively."""
        mgr = _manager(make_resource({'$key': 1, 'name': 'env'}))
        _resolve_one()(_module(), mgr, 'env', 'tag', category_name='cloud')
        assert mgr.list.call_args[1]['category_name'] == 'cloud'

    def test_a_projection_that_omits_name_has_it_added_back(self, make_resource):
        """Matching is client-side, so a projection without 'name' would make
        every row compare unequal and the lookup would silently find nothing."""
        mgr = _manager(make_resource({'$key': 1, 'name': 'n'}))
        _resolve_one()(_module(), mgr, 'n', 'network', fields=['$key', 'ipaddress'])
        assert 'name' in mgr.list.call_args[1]['fields']

    def test_a_projection_already_containing_name_is_untouched(self, make_resource):
        mgr = _manager(make_resource({'$key': 1, 'name': 'n'}))
        _resolve_one()(_module(), mgr, 'n', 'network', fields=['$key', 'name'])
        assert mgr.list.call_args[1]['fields'] == ['$key', 'name']

    def test_fields_all_is_left_alone(self, make_resource):
        mgr = _manager(make_resource({'$key': 1, 'name': 'n'}))
        _resolve_one()(_module(), mgr, 'n', 'network', fields=['all'])
        assert mgr.list.call_args[1]['fields'] == ['all']


class TestNoServerSideNameFilter:
    """Matching must stay client-side.

    list(name=...) is the obvious implementation and is wrong for this
    collection: get(name=) cannot see a second row (#72). pyVergeOS#100
    also stripped '{' from a filter literal until pyvergeos 1.2.8, which
    is now the floor (requirements.txt). The lookup stays client-side.
    """

    def test_name_is_not_passed_to_the_server(self, make_resource):
        mgr = _manager(make_resource({'$key': 1, 'name': 'zz-{brace}'}))
        _resolve_one()(_module(), mgr, 'zz-{brace}', 'catalog')
        assert 'name' not in mgr.list.call_args[1], (
            "name was sent as a server-side filter; on pyvergeos < 1.2.8 a "
            "braced name resolves to the wrong object (pyVergeOS#100)")
        assert 'filter' not in mgr.list.call_args[1]

    @pytest.mark.parametrize('name', ["zz-{brace}", "zz-o'apos", 'zz-"quote"',
                                      'zz-back\\slash', 'zz- space '])
    def test_names_with_filter_metacharacters_resolve_exactly(self, name, make_resource):
        rows = [make_resource({'$key': 1, 'name': name}),
                make_resource({'$key': 2, 'name': 'zz-decoy'})]
        out = _resolve_one()(_module(), _manager(*rows), name, 'catalog')
        assert dict(out)['$key'] == 1


def _plugin_files():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, '..', '..', '..', '..'))
    return sorted(glob.glob(os.path.join(root, 'plugins', '**', '*.py'),
                            recursive=True))


def test_no_plugin_calls_get_by_name():
    """A name= keyword on .get() is the SDK single-get (#72).

    That call is list(filter=..., limit=1)[0]. It returns one row and
    cannot report a second. Name lookups go through resolve_one(), which
    lists without that limit and refuses when more than one row matches.
    Docstrings may still mention the old call; this reads the AST.
    """
    hits = []
    for path in _plugin_files():
        tree = ast.parse(open(path).read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != 'get':
                continue
            if any(kw.arg == 'name' for kw in node.keywords):
                hits.append('%s:%d' % (os.path.relpath(path), node.lineno))
    assert hits == []
