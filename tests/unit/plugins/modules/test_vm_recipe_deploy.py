"""Unit tests for the vm_recipe_deploy module.

exit_json and fail_json are wired to raise SystemExit the way the real
AnsibleModule does. Without that, a module goes on executing after it has
reported -- so a test asserting "nothing was created" can pass while the
deploy POST fires on the next line.
"""

import pytest
from unittest.mock import MagicMock, patch

from ansible_collections.vergeio.vergeos.plugins.modules import vm_recipe_deploy


class FakeManager:
    def __init__(self, rows):
        self._rows = rows

    def list(self, **kwargs):
        return list(self._rows)


class FakeClient:
    def __init__(self, recipes=(), questions=(), networks=(), vms=(),
                 tables=None, responses=None):
        self.vm_recipes = FakeManager(recipes)
        self.recipe_questions = FakeManager(questions)
        self.networks = FakeManager(networks)
        self.vms = FakeManager(vms)
        self._tables = tables or {}
        self._responses = list(responses or [])
        self.posts = []

    def _request(self, method, path, params=None, json_data=None):
        if method == 'POST':
            self.posts.append(json_data)
            return self._responses.pop(0) if self._responses else {}
        return self._tables.get(path, [])


RECIPE = {'name': 'Ubuntu 22.04', '$key': 'abc', 'catalog_display': 'Local'}
CLEAN_SIMULATE = {'err': 'Simulation complete',
                  'response': {'logs': ['step 1', 'step 2'],
                               'answers': {'YB_VM_KEY': 7},
                               'cloudinit_files': [{'name': 'user-data'}]}}


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
        'name': 'web-01',
        'recipe': 'Ubuntu 22.04',
        'catalog': None,
        'answers': {},
        'prune_unknown': False,
        'auto_update': False,
        'simulate': True,
        'fail_on_hints': False,
    }
    base.update(over)
    return base


def run(client, check_mode=False, **over):
    module = MagicMock()
    module.params = params(**over)
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)

    with patch.object(vm_recipe_deploy, 'AnsibleModule', return_value=module), \
         patch.object(vm_recipe_deploy, 'get_vergeos_client', return_value=client):
        with pytest.raises(SystemExit):
            vm_recipe_deploy.main()
    return module


def exited(module):
    assert module.exit_json.called, (
        "expected exit_json, got fail_json: %s" % (module.fail_json.call_args,))
    return module.exit_json.call_args[1]


def failed(module):
    assert module.fail_json.called, (
        "expected fail_json, got exit_json: %s" % (module.exit_json.call_args,))
    return module.fail_json.call_args[1]


# ── recipe resolution ────────────────────────────────────────────────────────

def test_unknown_recipe_fails_before_anything_is_created():
    client = FakeClient(recipes=[RECIPE])
    module = run(client, recipe='Nope')
    assert 'no recipe named' in failed(module)['msg']
    assert client.posts == []


def test_ambiguous_recipe_is_refused():
    client = FakeClient(recipes=[
        {'name': 'Ubuntu 22.04', '$key': 'a', 'catalog_display': 'Local'},
        {'name': 'Ubuntu 22.04', '$key': 'b', 'catalog_display': 'Vendor'},
    ])
    module = run(client)
    assert '2 recipes' in failed(module)['msg']
    assert client.posts == []


# ── convergence ──────────────────────────────────────────────────────────────

def test_existing_vm_is_the_idempotence_key():
    client = FakeClient(recipes=[RECIPE],
                        vms=[{'name': 'web-01', '$key': 12}])
    result = exited(run(client))
    assert result['changed'] is False
    assert result['already_existed'] is True
    assert result['vm_key'] == '12'
    assert client.posts == []


def test_convergence_does_not_even_simulate():
    """An existing VM means no deploy, so the simulate would be pointless."""
    client = FakeClient(recipes=[RECIPE],
                        vms=[{'name': 'web-01', '$key': 12}])
    run(client)
    assert client.posts == []


def test_a_snapshot_of_the_name_does_not_count_as_convergence():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('HOSTNAME')],
                        vms=[{'name': 'web-01', '$key': 9,
                              'is_snapshot': True}],
                        responses=[CLEAN_SIMULATE,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client))
    assert result['already_existed'] is False
    assert result['changed'] is True


# ── answer validation ────────────────────────────────────────────────────────

def test_unknown_answer_fails_and_nothing_is_posted():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')])
    module = run(client, answers={'HOTSNAME': 'typo'})
    assert 'not valid' in failed(module)['msg']
    assert client.posts == []


def test_missing_required_answer_fails():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('USER', required=True, default='')])
    module = run(client)
    assert 'missing required answer' in failed(module)['msg']
    assert client.posts == []


def test_valid_answers_are_sent_by_name_only_in_the_result():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('HOSTNAME'), question('USER')],
                        responses=[CLEAN_SIMULATE,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'HOSTNAME': 'web-01',
                                         'USER': 'ops'}))
    assert result['answers_sent'] == ['HOSTNAME', 'USER']


def test_secret_answer_values_never_reach_the_result():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('PASSWORD', 'password')],
                        responses=[CLEAN_SIMULATE,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'PASSWORD': 'hunter2'}))
    assert 'hunter2' not in repr(result)


def test_hints_are_reported_but_not_fatal_by_default():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('SELECT_OS_TIER', 'row',
                                            default='', table='cluster_tiers')],
                        responses=[CLEAN_SIMULATE,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client))
    assert result['hints']
    assert result['changed'] is True


def test_fail_on_hints_makes_them_fatal_before_the_simulate():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('SELECT_OS_TIER', 'row',
                                            default='', table='cluster_tiers')])
    module = run(client, fail_on_hints=True)
    assert 'fail_on_hints' in failed(module)['msg']
    assert client.posts == []


def test_prune_unknown_reports_what_it_dropped():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[CLEAN_SIMULATE,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, prune_unknown=True,
                        answers={'HOSTNAME': 'web-01', 'NOTHERE': 1}))
    assert result['pruned'] == ['NOTHERE']
    assert result['answers_sent'] == ['HOSTNAME']


# ── table-backed options ─────────────────────────────────────────────────────

def test_a_bad_table_backed_choice_is_caught_locally():
    client = FakeClient(
        recipes=[RECIPE],
        questions=[question('SELECT_OS_TIER', 'row', table='cluster_tiers',
                            fields='$key,$display')],
        tables={'cluster_tiers': [{'$key': 4, '$display': '4'}]})
    module = run(client, answers={'SELECT_OS_TIER': 99})
    assert 'not a valid choice' in failed(module)['msg']
    assert client.posts == []


def test_a_good_table_backed_choice_passes():
    client = FakeClient(
        recipes=[RECIPE],
        questions=[question('SELECT_OS_TIER', 'row', table='cluster_tiers',
                            fields='$key,$display')],
        tables={'cluster_tiers': [{'$key': 4, '$display': '4'}]},
        responses=[CLEAN_SIMULATE, {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'SELECT_OS_TIER': 4}))
    assert result['changed'] is True


# ── network answers by name ──────────────────────────────────────────────────

def test_a_network_answer_given_by_name_is_resolved_to_its_key():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('YB_NIC_ETH0', 'network')],
                        networks=[{'name': 'External', '$key': 3}],
                        responses=[CLEAN_SIMULATE,
                                   {'$key': 5, 'response': {'vm': 42}}])
    run(client, answers={'YB_NIC_ETH0': 'External'})
    assert client.posts[-1]['answers']['YB_NIC_ETH0'] == 3


def test_an_unknown_network_name_fails():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('YB_NIC_ETH0', 'network')],
                        networks=[{'name': 'External', '$key': 3}])
    module = run(client, answers={'YB_NIC_ETH0': 'Nope'})
    assert 'not found' in failed(module)['msg']
    assert client.posts == []


# ── simulate ─────────────────────────────────────────────────────────────────

def test_simulate_runs_before_the_real_deploy():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[CLEAN_SIMULATE,
                                   {'$key': 5, 'response': {'vm': 42}}])
    exited(run(client, answers={'HOSTNAME': 'web-01'}))
    assert len(client.posts) == 2
    assert client.posts[0]['simulate'] is True
    assert 'simulate' not in client.posts[1]


def test_a_failed_simulate_stops_the_deploy():
    """The API calls this "Simulation complete" even so.

    Reading only the top-level field lets a deploy through that would build a
    VM with no OS drive.
    """
    dirty = {'err': 'Simulation complete',
             'response': {'logs': ['step 1',
                                   'Error executing API command: CREATE_OS_DRIVE'],
                          'answers': {}}}
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[dirty])
    module = run(client, answers={'HOSTNAME': 'web-01'})
    result = failed(module)
    assert 'would build a broken VM' in result['msg']
    assert result['simulate_result']['ok'] is False
    # The simulate POST is the only one that fired.
    assert len(client.posts) == 1


def test_simulate_result_is_reported_on_success():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[CLEAN_SIMULATE,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'HOSTNAME': 'web-01'}))
    assert result['simulate_result']['ok'] is True
    assert result['simulate_result']['step_count'] == 2
    assert result['simulate_result']['cloudinit_files'] == ['user-data']


def test_simulate_can_be_turned_off():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[{'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, simulate=False,
                        answers={'HOSTNAME': 'web-01'}))
    assert len(client.posts) == 1
    assert 'simulate_result' not in result


# ── check mode ───────────────────────────────────────────────────────────────

def test_check_mode_simulates_and_creates_nothing():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[CLEAN_SIMULATE])
    result = exited(run(client, check_mode=True,
                        answers={'HOSTNAME': 'web-01'}))
    assert result['changed'] is False
    assert result['simulate_result']['ok'] is True
    # Exactly one POST, and it was the simulation.
    assert len(client.posts) == 1
    assert client.posts[0]['simulate'] is True


def test_check_mode_still_fails_on_a_bad_answer_set():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')])
    module = run(client, check_mode=True, answers={'HOTSNAME': 'typo'})
    assert 'not valid' in failed(module)['msg']
    assert client.posts == []


def test_check_mode_with_simulate_off_creates_nothing():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')])
    result = exited(run(client, check_mode=True, simulate=False,
                        answers={'HOSTNAME': 'web-01'}))
    assert result['changed'] is False
    assert client.posts == []


# ── deploy ───────────────────────────────────────────────────────────────────

def test_deploy_reports_the_vm_key_the_post_returned():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[CLEAN_SIMULATE,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'HOSTNAME': 'web-01'}))
    assert result['changed'] is True
    assert result['vm_key'] == '42'
    assert result['instance_key'] == '5'


def test_auto_update_is_sent_on_the_real_deploy_only():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[CLEAN_SIMULATE,
                                   {'$key': 5, 'response': {'vm': 42}}])
    run(client, auto_update=True, answers={'HOSTNAME': 'web-01'})
    assert 'auto_update' not in client.posts[0]
    assert client.posts[1]['auto_update'] is True


def test_a_deploy_that_reports_no_vm_key_still_succeeds():
    """The deploy was accepted; a missing field must not fail it."""
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[CLEAN_SIMULATE, {'$key': 5}])
    result = exited(run(client, answers={'HOSTNAME': 'web-01'}))
    assert result['changed'] is True
    assert result['vm_key'] == ''


# ── recipes that publish no questions ────────────────────────────────────────

def test_a_recipe_with_no_questions_passes_answers_through():
    """The vendor "Services" recipe is like this.

    Validating against an empty question set would reject every answer as
    unknown, so the answers go through and the simulate is the only check.
    """
    client = FakeClient(recipes=[RECIPE], questions=[],
                        responses=[CLEAN_SIMULATE,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'ANYTHING': 'goes'}))
    assert result['changed'] is True
    assert result['answers_sent'] == ['ANYTHING']
    assert client.posts[1]['answers'] == {'ANYTHING': 'goes'}
