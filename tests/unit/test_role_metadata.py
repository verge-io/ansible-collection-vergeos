#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Guards for roles/*/meta.

A role declares its own min_ansible_version, independently of the
collection's requires_ansible. Nothing checks that the two agree, and on the
port branch they did not: 18 roles carried either "2.14" or "2.16" while the
collection declares ">=2.15.0". Both are wrong in opposite directions --

  2.14  promises support for a version CI has never run and the collection
        itself refuses to install on,
  2.16  is an undeclared restriction: the collection says it runs on 2.15,
        and on 2.15 this role would not.

Neither is caught by ansible-test or ansible-lint, because each file is
internally valid. The mismatch is only visible when you read both.
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


def _roles():
    return sorted(p for p in glob.glob(os.path.join(_root(), 'roles', '*'))
                  if os.path.isdir(p))


def _collection_floor():
    with open(os.path.join(_root(), 'meta', 'runtime.yml')) as fh:
        requires = yaml.safe_load(fh)['requires_ansible']
    match = re.search(r'(\d+)\.(\d+)', requires)
    return '%s.%s' % (match.group(1), match.group(2))


_ROLE_IDS = [os.path.basename(p) for p in _roles()]


@pytest.mark.parametrize('role', _roles(), ids=_ROLE_IDS)
def test_role_floor_matches_the_collection_floor(role):
    meta_path = os.path.join(role, 'meta', 'main.yml')
    assert os.path.exists(meta_path), '%s has no meta/main.yml' % role
    with open(meta_path) as fh:
        declared = str(yaml.safe_load(fh)['galaxy_info']['min_ansible_version'])
    floor = _collection_floor()
    assert declared == floor, (
        "%s declares min_ansible_version %r but the collection's "
        "requires_ansible floor is %r. A lower number promises a version CI "
        "never runs; a higher one is a restriction the collection does not "
        "declare. Move both, or neither."
        % (os.path.basename(role), declared, floor))


@pytest.mark.parametrize('role', _roles(), ids=_ROLE_IDS)
def test_role_documents_its_arguments(role):
    """A role with no argument_specs accepts anything and validates nothing,
    including a misspelled variable name, which then silently takes its
    default."""
    spec = os.path.join(role, 'meta', 'argument_specs.yml')
    assert os.path.exists(spec), (
        "%s has no meta/argument_specs.yml, so its variables are neither "
        "validated nor documented" % os.path.basename(role))


@pytest.mark.parametrize('role', _roles(), ids=_ROLE_IDS)
def test_role_defaults_cover_every_documented_option(role):
    """An option documented but absent from defaults/main.yml is undefined at
    runtime unless the caller happens to set it -- the role fails on first use
    with an undefined-variable error rather than a named one."""
    spec_path = os.path.join(role, 'meta', 'argument_specs.yml')
    defaults_path = os.path.join(role, 'defaults', 'main.yml')
    with open(spec_path) as fh:
        options = set((yaml.safe_load(fh)['argument_specs']['main']
                       .get('options') or {}))
    defaults = {}
    if os.path.exists(defaults_path):
        with open(defaults_path) as fh:
            defaults = yaml.safe_load(fh) or {}
    # Required options are the caller's job to supply.
    with open(spec_path) as fh:
        declared = (yaml.safe_load(fh)['argument_specs']['main']
                    .get('options') or {})
    optional = {name for name, body in declared.items()
                if not body.get('required')}
    missing = sorted((options & optional) - set(defaults))
    assert not missing, (
        "%s documents optional variables with no entry in defaults/main.yml: "
        "%s" % (os.path.basename(role), missing))
