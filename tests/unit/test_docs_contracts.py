# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Docs that have already drifted once, pinned to the things they describe.

vm and vm_info RETURN samples used to advertise power_state and id. Neither
key is on a VM row. The README had no module index, so a new module file
could land without a reader being able to find it.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import ast
import glob
import json
import os

import pytest
import yaml


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..'))


def _assign(path, name):
    """The literal assigned to ``name`` in a Python module."""
    tree = ast.parse(open(path).read())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError('%s has no %s assignment' % (path, name))


def _return_block(module_name):
    path = os.path.join(_root(), 'plugins', 'modules', module_name + '.py')
    loaded = yaml.safe_load(_assign(path, 'RETURN'))
    assert isinstance(loaded, dict), module_name + ' RETURN did not parse'
    return loaded


def _sample_keys(block, top):
    sample = block[top]['sample']
    if isinstance(sample, list):
        assert len(sample) == 1, top + ' sample should be one row'
        row = sample[0]
    else:
        row = sample
    assert isinstance(row, dict)
    return set(row)


def _contains_keys(block, top):
    contains = block[top].get('contains') or {}
    return set(contains)


def _fixture():
    path = os.path.join(_root(), 'tests', 'fixtures', 'vm_return_rows.json')
    with open(path) as handle:
        return json.load(handle)


# Aliases of pyvergeos VM_DEFAULT_FIELDS, identical on 1.2.7 and 1.6.1.
DEFAULT_PROJECTION = {
    '$key', 'name', 'description', 'enabled', 'cpu_cores', 'ram', 'os_family',
    'guest_agent', 'uefi', 'secure_boot', 'machine_type', 'created', 'modified',
    'is_snapshot', 'machine', 'node_key', 'cluster_key', 'ha_group',
    'cloudinit_datasource', 'running', 'status', 'node_name', 'cluster_name',
}

# What this collection's roles read off a vm_info row.
ROLE_FIELDS = {'$key', 'name', 'status', 'running', 'node_name'}

FORBIDDEN = {'power_state', 'id'}


def _aliases(fields):
    out = set()
    for field in fields:
        if ' as ' in field:
            out.add(field.rsplit(' as ', 1)[1].strip())
        else:
            out.add(field)
    return out


class TestVmReturnSamples:
    """RETURN samples against the row the module returns."""

    def test_fixture_is_the_default_projection_plus_the_two_named_columns(self):
        fixture = _fixture()
        assert set(fixture['default_projection']) == DEFAULT_PROJECTION
        assert set(fixture['vm_info_extra']) == {'snapshot_profile', 'tags'}
        assert set(fixture['live_list_keys']) <= DEFAULT_PROJECTION
        for key in ('$key', 'status', 'running'):
            assert key in fixture['live_list_keys']
        assert not (FORBIDDEN & set(fixture['default_projection']))

    def test_vm_sample_is_the_projection_the_module_fetches(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import vm
        block = _return_block('vm')
        sample = _sample_keys(block, 'vm')
        fetched = _aliases(vm.VM_FIELDS)
        assert sample == fetched, (
            'vm RETURN sample drifted from VM_FIELDS. '
            'sample-only=%s projection-only=%s'
            % (sorted(sample - fetched), sorted(fetched - sample)))
        assert _contains_keys(block, 'vm') == sample
        assert {'$key', 'status', 'running'} <= sample
        assert not (FORBIDDEN & sample)

    def test_vm_sample_keys_are_real_columns_or_the_named_boot_order(self):
        """boot_order is a column the default summary omits. Everything else
        the vm module returns is on the default projection."""
        fixture = _fixture()
        sample = _sample_keys(_return_block('vm'), 'vm')
        assert set(fixture['vm_only']) == {'boot_order'}
        assert 'boot_order' in sample
        invented = sample - {'boot_order'} - DEFAULT_PROJECTION
        assert not invented, 'vm RETURN documents keys that are not on a VM row: %s' % sorted(invented)

    def test_vm_info_sample_is_a_subset_of_the_fixture_row(self):
        fixture = _fixture()
        allowed = set(fixture['default_projection']) | set(fixture['vm_info_extra'])
        block = _return_block('vm_info')
        sample = _sample_keys(block, 'vms')
        invented = sample - allowed
        assert not invented, (
            'vm_info RETURN documents keys that are not on the fixture row: %s'
            % sorted(invented))
        assert ROLE_FIELDS <= sample
        assert ROLE_FIELDS <= _contains_keys(block, 'vms')
        assert {'snapshot_profile', 'tags'} <= sample
        assert not (FORBIDDEN & sample)
        assert not (FORBIDDEN & _contains_keys(block, 'vms'))


class TestReadmeModuleIndex:
    """Every plugins/modules/*.py file appears in the README index."""

    def _section(self):
        readme = open(os.path.join(_root(), 'README.md')).read()
        start = readme.index('<!-- module-index: begin -->')
        end = readme.index('<!-- module-index: end -->')
        return readme[start:end]

    def _rows(self):
        rows = {}
        for line in self._section().splitlines():
            if not line.startswith('| `'):
                continue
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            name = cells[0].strip('`')
            rows[name] = {'purpose': cells[1], 'readonly': cells[2]}
        return rows

    def _modules(self):
        paths = glob.glob(os.path.join(_root(), 'plugins', 'modules', '*.py'))
        return {
            os.path.basename(path)[:-3]: path
            for path in paths
            if not os.path.basename(path).startswith('__')
        }

    def test_every_module_file_is_in_the_index(self):
        present = set(self._modules())
        indexed = set(self._rows())
        assert present == indexed, (
            'README module index drifted. missing=%s extra=%s'
            % (sorted(present - indexed), sorted(indexed - present)))

    def test_purpose_is_the_module_short_description(self):
        rows = self._rows()
        for name, path in sorted(self._modules().items()):
            doc = yaml.safe_load(_assign(path, 'DOCUMENTATION'))
            assert rows[name]['purpose'] == doc['short_description'], name

    def test_readonly_column_matches_the_info_suffix(self):
        for name, row in self._rows().items():
            expect = 'yes' if name.endswith('_info') else 'no'
            assert row['readonly'] == expect, name


class TestSdkFloorAndKnownBugs:
    def test_requirements_floor_is_the_brace_fix(self):
        text = open(os.path.join(_root(), 'requirements.txt')).read()
        assert 'pyvergeos>=1.2.8\n' in text
        ee = open(os.path.join(_root(), 'meta', 'ee-requirements.txt')).read()
        assert 'pyvergeos>=1.2.8' in ee

    def test_compatibility_doc_records_the_brace_fix_and_1_6_1(self):
        text = open(os.path.join(_root(), 'docs', 'SDK-COMPATIBILITY.md')).read()
        assert '1.6.1' in text
        assert '1.2.8' in text
        assert 'still broken' not in text.lower()
        assert 'quote_value()' in text

    def test_b8_is_recorded_as_skipped(self):
        text = open(os.path.join(_root(), 'docs', 'KNOWN-BUGS.md')).read()
        assert '## B8' in text
        assert 'never assigned' in text

    @pytest.mark.parametrize('rel', [
        'roles/vm_from_recipe/tasks/power_on.yml',
        'roles/restore_drill/tasks/main.yml',
    ])
    def test_roles_no_longer_say_the_power_state_docs_lie(self, rel):
        text = open(os.path.join(_root(), rel)).read()
        assert 'power_state' not in text or 'no `power_state`' in text
        assert 'documentation mentions' not in text
        assert 'does not exist on it' not in text
