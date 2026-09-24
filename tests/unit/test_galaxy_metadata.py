#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""galaxy.yml metadata must describe this repository.

`repository`, `documentation` and `issues` are copied verbatim into
MANIFEST.json and are what Galaxy renders as the Repository / Documentation /
Issue Tracker links on the collection page.

They pointed at `verge-io/ansible-vergeos`, which 404s, and shipped that way
in the v2.1.0 artifact. Nothing checked them, and nothing would have: the
value is a plain string that no test or sanity check reads.

These assertions are deliberately offline -- unit tests must not reach the
network (see the no_network fixture in conftest.py), so correctness is checked
against the known repository path rather than by fetching the URL.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os

import pytest
import yaml

REPO_URL = 'https://github.com/verge-io/ansible-collection-vergeos'

# The name it used to carry. Kept explicitly so the failure says what is
# wrong rather than just that two strings differ.
WRONG_REPO = 'ansible-vergeos'


def _galaxy():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, '..', '..'))
    with open(os.path.join(root, 'galaxy.yml')) as fh:
        return yaml.safe_load(fh)


@pytest.mark.parametrize('field', ['repository', 'documentation', 'issues'])
def test_url_points_at_this_repository(field):
    value = _galaxy().get(field)
    assert value, "galaxy.yml is missing %r" % field
    assert value.startswith(REPO_URL), (
        "galaxy.yml %r is %r, which does not point at this repository. It is "
        "copied into MANIFEST.json and rendered on the Galaxy collection page."
        % (field, value))


@pytest.mark.parametrize('field', ['repository', 'documentation', 'issues', 'homepage'])
def test_no_field_references_the_old_404_name(field):
    value = _galaxy().get(field) or ''
    assert '/%s' % WRONG_REPO not in value, (
        "galaxy.yml %r references %r, which 404s. The repository is %s."
        % (field, WRONG_REPO, REPO_URL))


def test_issues_points_at_the_issue_tracker():
    assert _galaxy()['issues'].endswith('/issues')


def test_required_metadata_is_present():
    """A missing required key fails `ansible-galaxy collection build` only at
    build time; catching it here is cheaper."""
    g = _galaxy()
    for field in ('namespace', 'name', 'version', 'readme', 'authors',
                  'description', 'license', 'repository'):
        assert g.get(field), "galaxy.yml is missing required field %r" % field


def test_namespace_and_name_match_the_collection_path():
    g = _galaxy()
    assert g['namespace'] == 'vergeio'
    assert g['name'] == 'vergeos'


def test_version_is_not_a_prerelease_placeholder():
    """Guards against a bump that forgot the actual number."""
    version = str(_galaxy()['version'])
    assert version.count('.') == 2, "version %r is not X.Y.Z" % version
    assert not version.startswith('0.'), "version %r looks unset" % version
