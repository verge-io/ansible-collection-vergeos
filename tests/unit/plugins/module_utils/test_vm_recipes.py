"""Unit tests for the recipe SDK glue.

The client is mocked with real dicts rather than MagicMocks on purpose:
``dict(MagicMock())`` returns ``{}`` because dict() prefers the mapping
protocol and MagicMock auto-provides ``keys``, so a mocked row silently
decodes empty and every field comparison compares nothing.
"""

import pytest

from ansible_collections.vergeio.vergeos.plugins.module_utils.vm_recipes import (
    deployed_vm_key,
    fetch_networks,
    fetch_options,
    fetch_questions,
    find_vm_by_name,
    post_instance,
    resolve_recipe,
)


class FakeManager:
    def __init__(self, rows):
        self._rows = rows

    def list(self, **kwargs):
        self.called_with = kwargs
        return list(self._rows)


class FakeClient:
    def __init__(self, recipes=(), questions=(), networks=(), vms=(),
                 tables=None, post_response=None):
        self.vm_recipes = FakeManager(recipes)
        self.recipe_questions = FakeManager(questions)
        self.networks = FakeManager(networks)
        self.vms = FakeManager(vms)
        self._tables = tables or {}
        self._post_response = post_response
        self.requests = []

    def _request(self, method, path, params=None, json_data=None):
        self.requests.append({'method': method, 'path': path,
                              'params': params, 'json_data': json_data})
        if method == 'POST':
            return self._post_response
        return self._tables.get(path, [])


def recipe(name, key='abc', catalog='Local'):
    return {'name': name, '$key': key, 'catalog_display': catalog}


# ── resolve_recipe ───────────────────────────────────────────────────────────

def test_resolve_recipe_finds_one_by_exact_name():
    client = FakeClient(recipes=[recipe('Ubuntu 22.04'), recipe('Rocky 9')])
    row, error = resolve_recipe(client, 'Ubuntu 22.04')
    assert error is None
    assert row['name'] == 'Ubuntu 22.04'


def test_resolve_recipe_missing_name_is_an_error_listing_what_exists():
    client = FakeClient(recipes=[recipe('Ubuntu 22.04')])
    row, error = resolve_recipe(client, 'Ubunut 22.04')
    assert row is None
    # The typo case: the message has to show what was actually there.
    assert 'Ubuntu 22.04' in error


def test_resolve_recipe_ambiguous_name_is_refused_not_guessed():
    client = FakeClient(recipes=[recipe('Ubuntu', key='a', catalog='Local'),
                                 recipe('Ubuntu', key='b', catalog='Vendor')])
    row, error = resolve_recipe(client, 'Ubuntu')
    assert row is None
    assert '2 recipes' in error
    assert 'catalog' in error


def test_resolve_recipe_catalog_disambiguates():
    client = FakeClient(recipes=[recipe('Ubuntu', key='a', catalog='Local'),
                                 recipe('Ubuntu', key='b', catalog='Vendor')])
    row, error = resolve_recipe(client, 'Ubuntu', catalog='Vendor')
    assert error is None
    assert row['$key'] == 'b'


def test_resolve_recipe_catalog_that_matches_nothing_is_an_error():
    client = FakeClient(recipes=[recipe('Ubuntu', catalog='Local')])
    row, error = resolve_recipe(client, 'Ubuntu', catalog='Nope')
    assert row is None
    assert "in catalog 'Nope'" in error


@pytest.mark.parametrize('name', [
    "zz-quote-o'brien",
    'back\\slash',
    "both'\\mixed",
])
def test_resolve_recipe_matches_names_needing_odata_escaping(name):
    """Names are matched client-side, so no escape can be got wrong.

    pyvergeos escapes ``'`` SQL-style by doubling it, while the VergeOS 26.1.8
    measurements behind this module recorded backslash-escaping as the form the
    platform accepts. Rather than pick a side, resolve_recipe never builds a
    filter -- and this test pins that, because a future "optimisation" to a
    server-side filter would silently match nothing for these names.
    """
    client = FakeClient(recipes=[recipe(name)])
    row, error = resolve_recipe(client, name)
    assert error is None
    assert row['name'] == name
    assert client.requests == []


def test_resolve_recipe_never_sends_a_filter():
    client = FakeClient(recipes=[recipe('Ubuntu')])
    resolve_recipe(client, 'Ubuntu')
    assert 'filter' not in (client.vm_recipes.called_with or {})


# ── fetch_questions / fetch_networks ────────────────────────────────────────

def test_fetch_questions_scopes_to_the_recipe():
    client = FakeClient(questions=[{'name': 'HOSTNAME', 'type': 'string'}])
    rows = fetch_questions(client, 'deadbeef')
    assert rows == [{'name': 'HOSTNAME', 'type': 'string'}]
    assert client.recipe_questions.called_with == {
        'recipe_ref': 'vm_recipes/deadbeef'}


def test_fetch_networks_returns_plain_dicts():
    client = FakeClient(networks=[{'name': 'External', '$key': 3}])
    assert fetch_networks(client) == [{'name': 'External', '$key': 3}]


# ── fetch_options ───────────────────────────────────────────────────────────

def test_fetch_options_reads_the_table_the_question_points_at():
    client = FakeClient(tables={'cluster_tiers': [{'$key': 4, '$display': '4'}]})
    options = fetch_options(client, [{'name': 'SELECT_OS_TIER',
                                      'table': 'cluster_tiers',
                                      'filter': '',
                                      'fields': '$key,tier as $display'}])
    assert options == {'SELECT_OS_TIER': [{'$key': 4, '$display': '4'}]}
    assert client.requests[0]['params'] == {'fields': '$key,tier as $display'}


def test_fetch_options_passes_the_questions_filter_through():
    client = FakeClient(tables={'vnets': []})
    fetch_options(client, [{'name': 'NET', 'table': 'vnets',
                            'filter': "type eq 'internal'", 'fields': '$key'}])
    assert client.requests[0]['params']['filter'] == "type eq 'internal'"


def test_fetch_options_treats_a_count_document_as_no_rows():
    """A filter matching nothing returns {"$count": 0}, not [].

    Left as a mapping it reads as one option row, so a question with no valid
    values would look like it had one.
    """
    client = FakeClient(tables={'cluster_tiers': {'$count': 0}})
    options = fetch_options(client, [{'name': 'T', 'table': 'cluster_tiers',
                                      'filter': '', 'fields': '$key'}])
    assert options == {'T': []}


def test_fetch_options_with_nothing_to_resolve_makes_no_calls():
    client = FakeClient()
    assert fetch_options(client, []) == {}
    assert fetch_options(client, None) == {}
    assert client.requests == []


# ── post_instance ───────────────────────────────────────────────────────────

def test_post_instance_sends_the_simulate_flag_only_when_asked():
    client = FakeClient(post_response={})
    post_instance(client, 'abc', 'web-01', {'HOSTNAME': 'web-01'})
    assert 'simulate' not in client.requests[0]['json_data']

    post_instance(client, 'abc', 'web-01', {}, simulate=True)
    assert client.requests[1]['json_data']['simulate'] is True


def test_post_instance_sends_auto_update_only_when_asked():
    client = FakeClient(post_response={})
    post_instance(client, 'abc', 'web-01', {})
    assert 'auto_update' not in client.requests[0]['json_data']

    post_instance(client, 'abc', 'web-01', {}, auto_update=True)
    assert client.requests[1]['json_data']['auto_update'] is True


def test_post_instance_posts_to_the_instances_table():
    client = FakeClient(post_response={})
    post_instance(client, 'abc', 'web-01', {'A': 1})
    assert client.requests[0]['method'] == 'POST'
    assert client.requests[0]['path'] == 'vm_recipe_instances'
    assert client.requests[0]['json_data']['recipe'] == 'abc'
    assert client.requests[0]['json_data']['name'] == 'web-01'
    assert client.requests[0]['json_data']['answers'] == {'A': 1}


# ── deployed_vm_key ─────────────────────────────────────────────────────────

def test_deployed_vm_key_reads_the_key_the_post_reported():
    assert deployed_vm_key({'response': {'vm': 42}}) == '42'


@pytest.mark.parametrize('document', [
    None, {}, 'a string', {'response': None}, {'response': 'text'},
    {'response': {}}, {'response': {'vm': None}}, [],
])
def test_deployed_vm_key_never_raises_on_a_surprising_document(document):
    """This runs after a deploy the platform already accepted.

    A missing field here must not turn a successful deploy into a traceback.
    """
    assert deployed_vm_key(document) == ''


# ── find_vm_by_name ─────────────────────────────────────────────────────────

def test_find_vm_by_name_finds_the_vm():
    client = FakeClient(vms=[{'name': 'other', '$key': 1},
                             {'name': 'web-01', '$key': 2}])
    assert find_vm_by_name(client, 'web-01')['$key'] == 2


def test_find_vm_by_name_returns_none_when_absent():
    client = FakeClient(vms=[{'name': 'other', '$key': 1}])
    assert find_vm_by_name(client, 'web-01') is None


def test_find_vm_by_name_ignores_snapshots():
    """A snapshot carries its VM's name and must not count as the VM."""
    client = FakeClient(vms=[{'name': 'web-01', '$key': 9,
                              'is_snapshot': True}])
    assert find_vm_by_name(client, 'web-01') is None


def test_find_vm_by_name_accepts_a_row_without_the_snapshot_field():
    """is_snapshot is absent on some projections; absence is not truth."""
    client = FakeClient(vms=[{'name': 'web-01', '$key': 2}])
    assert find_vm_by_name(client, 'web-01')['$key'] == 2


def test_find_vm_by_name_matches_exactly_not_by_prefix():
    client = FakeClient(vms=[{'name': 'web-011', '$key': 1},
                             {'name': 'web-01', '$key': 2}])
    assert find_vm_by_name(client, 'web-01')['$key'] == 2
