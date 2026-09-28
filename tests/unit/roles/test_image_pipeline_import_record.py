# -*- coding: utf-8 -*-

"""The image-pipeline ladder must not leave a vm_imports row behind (#162).

The role imports with ``vm_import`` and used to keep the record. VergeOS
does not delete that row when the VM is deleted, and it does not require
the name to be unique, so each ladder run added a ``zz-golden`` row that
``state=absent`` by name could not remove once a second one existed.

Nothing here talks to a cluster. It reads the role, the ladder, and the
lab sweep, which is the same evidence a live run would leave behind.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os

import yaml


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..', '..'))


def _load(rel):
    with open(os.path.join(_root(), rel)) as handle:
        return yaml.safe_load(handle)


def _flatten(node):
    if isinstance(node, list):
        for item in node:
            yield from _flatten(item)
        return
    if not isinstance(node, dict):
        return
    yield node
    for key in ('tasks', 'pre_tasks', 'post_tasks', 'handlers',
                'block', 'rescue', 'always'):
        if key in node:
            yield from _flatten(node[key])


def _imports(node):
    found = []
    for task in _flatten(node):
        action = task.get('vergeio.vergeos.vm_import')
        if isinstance(action, dict):
            found.append((task, action))
    return found


def test_role_deletes_the_import_record_it_just_created():
    tasks = _load('roles/image_pipeline/tasks/main.yml')
    imports = _imports(tasks)
    present = [(task, action) for task, action in imports
               if action.get('state') == 'present']
    absent = [(task, action) for task, action in imports
              if action.get('state') == 'absent']

    assert len(present) == 1
    assert present[0][0].get('register') == 'image_pipeline_import'
    assert len(absent) == 1
    task, action = absent[0]
    assert action['name'] == '{{ image_pipeline_template_name }}'
    assert action['import_key'] == '{{ image_pipeline_import.import_key }}'
    assert 'image_pipeline_import.import_key' in task.get('when', '')


def test_ladder_removes_import_records_before_and_after_the_run():
    plays = _load('tests/live/verify-image-pipeline.yml')
    imports = [(task, action) for task, action in _imports(plays)
               if action.get('state') == 'absent']
    names = [task.get('name') for task, _action in imports]
    targets = [action.get('name') for _task, action in imports]

    assert any(name and 'previous import' in name for name in names), names
    assert any(name and name.startswith('6') for name in names), names
    assert targets
    assert all(target == '{{ tmpl }}' for target in targets), targets

    # The teardown task has to sit in always:, or a failed rung keeps the row.
    always_tasks = []
    for task in _flatten(plays):
        always_tasks.extend(_flatten(task.get('always')))
    always_imports = [(task, action) for task, action in _imports(always_tasks)
                      if action.get('state') == 'absent']
    assert len(always_imports) == 1
    assert always_imports[0][1]['name'] == '{{ tmpl }}'


def test_lab_sweep_reads_vm_imports():
    play = _load('tests/live/assert_lab_clean.yml')[0]
    scan = next(task for task in play['tasks'] if task.get('loop'))
    tables = scan['loop']
    for required in ('vms', 'vm_recipes', 'catalogs', 'vm_recipe_instances',
                     'vnets', 'vm_imports'):
        assert required in tables, tables

    asserted = next(task for task in play['tasks']
                    if 'ansible.builtin.assert' in task)
    success = asserted['ansible.builtin.assert']['success_msg']
    assert 'vm_imports' in success
