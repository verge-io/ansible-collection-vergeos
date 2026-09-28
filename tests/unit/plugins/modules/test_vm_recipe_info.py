"""Unit tests for the vm_recipe_info module."""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock, patch

from ansible_collections.vergeio.vergeos.plugins.modules import vm_recipe_info


class FakeManager:
    def __init__(self, rows):
        self._rows = rows

    def list(self, **kwargs):
        return list(self._rows)


class FakeClient:
    def __init__(self, recipes=(), questions=(), tables=None):
        self.vm_recipes = FakeManager(recipes)
        self.recipe_questions = FakeManager(questions)
        self._tables = tables or {}
        self.requests = []

    def _request(self, method, path, params=None, json_data=None):
        self.requests.append(path)
        return self._tables.get(path, [])


RECIPE = {'name': 'Ubuntu 22.04', '$key': 'abc', 'catalog_display': 'Local'}


def question(name, qtype='string', **kw):
    row = {'name': name, 'type': qtype, 'required': False, 'default': 'x'}
    row.update(kw)
    return row


def params(**over):
    base = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'name': None,
        'catalog': None,
        'questions': False,
        'resolve_options': False,
    }
    base.update(over)
    return base


def run(client, **over):
    module = MagicMock()
    module.params = params(**over)
    module.check_mode = False
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)

    with patch.object(vm_recipe_info, 'AnsibleModule', return_value=module), \
         patch.object(vm_recipe_info, 'get_vergeos_client', return_value=client):
        with pytest.raises(SystemExit):
            vm_recipe_info.main()
    return module


def exited(module):
    assert module.exit_json.called, (
        "expected exit_json, got fail_json: %s" % (module.fail_json.call_args,))
    return module.exit_json.call_args[1]


def failed(module):
    assert module.fail_json.called
    return module.fail_json.call_args[1]


def test_lists_every_recipe_when_no_name_given():
    client = FakeClient(recipes=[RECIPE, {'name': 'Rocky 9', '$key': 'd'}])
    result = exited(run(client))
    assert result['changed'] is False
    assert [r['name'] for r in result['recipes']] == ['Ubuntu 22.04', 'Rocky 9']


def test_a_name_narrows_to_one_recipe():
    client = FakeClient(recipes=[RECIPE, {'name': 'Rocky 9', '$key': 'd'}])
    result = exited(run(client, name='Rocky 9'))
    assert len(result['recipes']) == 1
    assert result['recipes'][0]['name'] == 'Rocky 9'


def test_an_unknown_name_fails():
    client = FakeClient(recipes=[RECIPE])
    assert 'no recipe named' in failed(run(client, name='Nope'))['msg']


def test_questions_without_a_name_is_refused():
    """A question set belongs to one recipe, so the pairing is meaningless."""
    client = FakeClient(recipes=[RECIPE])
    assert 'requires name' in failed(run(client, questions=True))['msg']


def test_resolve_options_without_questions_is_refused():
    client = FakeClient(recipes=[RECIPE])
    module = run(client, name='Ubuntu 22.04', resolve_options=True)
    assert 'requires questions' in failed(module)['msg']


def test_questions_are_returned_when_asked_for():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('HOSTNAME', required=True)])
    result = exited(run(client, name='Ubuntu 22.04', questions=True))
    assert [q['name'] for q in result['questions']] == ['HOSTNAME']


def test_no_questions_key_unless_asked_for():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')])
    result = exited(run(client, name='Ubuntu 22.04'))
    assert 'questions' not in result


def test_internal_questions_are_separated_from_answerable_ones():
    """229 database_* and 91 hidden questions live in $database on this
    platform; mixed in, they bury the questions an operator can answer."""
    client = FakeClient(recipes=[RECIPE], questions=[
        question('HOSTNAME'),
        question('YB_VM_KEY', 'hidden'),
        question('YB_CHECK_ISO', 'database_find'),
        question('PLUMBING', 'string', section_name='$database'),
    ])
    result = exited(run(client, name='Ubuntu 22.04', questions=True))
    assert [q['name'] for q in result['questions']] == ['HOSTNAME']
    assert result['internal_questions'] == [
        'PLUMBING', 'YB_CHECK_ISO', 'YB_VM_KEY']


def test_secret_questions_are_named_so_callers_can_no_log_them():
    client = FakeClient(recipes=[RECIPE], questions=[
        question('PASSWORD', 'password'),
        question('API_TOKEN'),
        question('HOSTNAME'),
    ])
    result = exited(run(client, name='Ubuntu 22.04', questions=True))
    assert result['secret_questions'] == ['API_TOKEN', 'PASSWORD']


def test_a_secret_questions_default_is_redacted():
    """The raw row returns a credential default in clear text."""
    client = FakeClient(recipes=[RECIPE], questions=[
        question('PASSWORD', 'password', default='hunter2')])
    result = exited(run(client, name='Ubuntu 22.04', questions=True))
    assert 'hunter2' not in repr(result)


def test_a_non_secret_default_is_left_alone():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('HOSTNAME', default='web')])
    result = exited(run(client, name='Ubuntu 22.04', questions=True))
    assert result['questions'][0]['default'] == 'web'


def test_resolve_options_reads_the_table_the_question_points_at():
    client = FakeClient(
        recipes=[RECIPE],
        questions=[question('SELECT_OS_TIER', 'row', table='cluster_tiers',
                            fields='$key,tier as $display')],
        tables={'cluster_tiers': [{'$key': 4, '$display': '4'}]})
    result = exited(run(client, name='Ubuntu 22.04', questions=True,
                        resolve_options=True))
    assert result['options'] == {
        'SELECT_OS_TIER': [{'$key': 4, '$display': '4'}]}


def test_a_table_backed_question_with_no_rows_reports_an_empty_list():
    """That empty list is the signal that something has to be created first."""
    client = FakeClient(
        recipes=[RECIPE],
        questions=[question('SELECT_OS_TIER', 'row', table='cluster_tiers')],
        tables={'cluster_tiers': []})
    result = exited(run(client, name='Ubuntu 22.04', questions=True,
                        resolve_options=True))
    assert result['options'] == {'SELECT_OS_TIER': []}


def test_options_are_not_looked_up_for_plain_questions():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')])
    result = exited(run(client, name='Ubuntu 22.04', questions=True,
                        resolve_options=True))
    assert result['options'] == {}
    assert client.requests == []


def test_internal_table_backed_questions_are_not_looked_up():
    """A hidden question is not operator input, so its options are noise."""
    client = FakeClient(
        recipes=[RECIPE],
        questions=[question('HIDDEN_ROW', 'row', table='vnets',
                            section_name='$database')],
        tables={'vnets': [{'$key': 1}]})
    result = exited(run(client, name='Ubuntu 22.04', questions=True,
                        resolve_options=True))
    assert result['options'] == {}
    assert client.requests == []
