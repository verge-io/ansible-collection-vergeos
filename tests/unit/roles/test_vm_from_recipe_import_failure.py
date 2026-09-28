# -*- coding: utf-8 -*-

"""A failed recipe import must not burn vm_from_recipe_wait_timeout.

Issue #131. The platform marks the drive status=errors within about two
seconds and never retries. The wait used to treat every media=import drive as
unfinished, sit out the whole budget, and then advise raising the timeout.

Nothing in CI runs a playbook, so this reads the role tasks. The module tests
pin the classification; this pins that the role actually exits on it and that
the verdict quotes status_info without the timeout advice.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os

import yaml


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..', '..'))


def _tasks(name):
    path = os.path.join(_root(), 'roles', 'vm_from_recipe', 'tasks', name)
    with open(path) as handle:
        return yaml.safe_load(handle)


def _task(tasks, name):
    found = [t for t in tasks if isinstance(t, dict) and t.get('name') == name]
    assert len(found) == 1, "expected one task named %r, found %d" % (
        name, len(found))
    return found[0]


def test_the_import_wait_exits_when_a_drive_has_failed():
    wait = _task(_tasks('wait_drives.yml'), 'Wait for drive imports to finish')
    until = wait['until']
    assert isinstance(until, list)
    joined = '\n'.join(until)
    assert 'failed_drives' in joined
    assert 'length > 0' in joined
    # importing becoming empty is not the only way out. A sibling that is
    # still downloading must not hold a drive the platform has already
    # abandoned in this loop.
    assert ' or ' in joined
    assert 'importing' in joined


def test_a_failed_drive_is_reported_with_status_info_and_without_timeout_advice():
    tasks = _tasks('verify_vm.yml')
    failed = _task(tasks, 'No drive may have failed')
    still = _task(tasks, 'No drive may still be importing')

    # The failure verdict has to win over the "still importing" verdict.
    # Otherwise a second drive that is genuinely still downloading makes the
    # role advise raising the timeout for a drive that has already failed.
    assert tasks.index(failed) < tasks.index(still)

    failed_args = failed['ansible.builtin.assert']
    still_args = still['ansible.builtin.assert']
    msg = failed_args['fail_msg']
    assert 'status_info' in failed['vars']['vm_from_recipe_drive_errors']
    assert 'platform reports' in msg
    assert 'failed_drives' in failed_args['that']
    lowered = msg.lower()
    assert 'timeout' not in lowered
    assert 'wait_timeout' not in lowered

    # A drive that is actually still importing keeps the existing advice.
    assert 'Raise vm_from_recipe_wait_timeout' in still_args['fail_msg']
