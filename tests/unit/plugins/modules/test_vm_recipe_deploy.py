
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type
"""Unit tests for the vm_recipe_deploy module.

exit_json and fail_json are wired to raise SystemExit the way the real
AnsibleModule does. Without that, a module goes on executing after it has
reported -- so a test asserting "nothing was created" can pass while the
deploy POST fires on the next line.
"""

import pytest
from unittest.mock import MagicMock, patch

from ansible.module_utils.common.parameters import remove_values

from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    maskable_strings,
)
from ansible_collections.vergeio.vergeos.plugins.modules import vm_recipe_deploy


class FakeManager:
    def __init__(self, rows):
        self._rows = rows

    def list(self, **kwargs):
        return list(self._rows)


class FakeResponse:
    """Enough of a requests.Response for raw_request to read."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = '{}' if payload is not None else ''

    def json(self):
        if self._payload is None:
            raise ValueError('no json')
        return self._payload


class FakeSession:
    def __init__(self, owner):
        self._owner = owner

    def request(self, method, url, params=None, json=None, timeout=None):
        self._owner.posts.append(json)
        status, payload = self._owner._next_response()
        return FakeResponse(status, payload)


class FakeConnection:
    api_base_url = 'https://verge.invalid/api/v4'

    def __init__(self, session):
        self.session = session
        self.is_connected = True


class FakeClient:
    """A client whose POSTs go through the session, not _request.

    post_instance deliberately bypasses pyvergeos _request, because a clean
    recipe simulate answers on HTTP 405 with the whole simulation log in the
    body and _handle_response throws that body away. So the fake has to model
    the session layer, and a response is a (status, document) pair.

    A bare dict in `responses` means HTTP 200. The simulate response is paired
    with 405 explicitly wherever it is used, because that is what the platform
    actually returns and a fake that answered 200 would let a regression to
    status-based dispatch pass.
    """

    def __init__(self, recipes=(), questions=(), networks=(), vms=(),
                 tables=None, responses=None):
        self.vm_recipes = FakeManager(recipes)
        self.recipe_questions = FakeManager(questions)
        self.networks = FakeManager(networks)
        self.vms = FakeManager(vms)
        self._tables = tables or {}
        self._responses = list(responses or [])
        self.posts = []
        self._timeout = 30
        self._connection = FakeConnection(FakeSession(self))

    def _next_response(self):
        if not self._responses:
            return 200, {}
        item = self._responses.pop(0)
        if isinstance(item, tuple):
            return item
        return 200, item

    def _request(self, method, path, params=None, json_data=None):
        # Only table reads come through here now; POSTs go via the session.
        return self._tables.get(path, [])


RECIPE = {'name': 'Ubuntu 22.04', '$key': 'abc', 'catalog_display': 'Local'}
CLEAN_SIMULATE = {'err': 'Simulation complete',
                  'response': {'logs': ['step 1', 'step 2'],
                               'answers': {'YB_VM_KEY': 7},
                               'cloudinit_files': [{'name': 'user-data'}]}}


# What the platform really sends back for a clean simulate: HTTP 405 with the
# log document in the body.
SIMULATE_405 = (405, CLEAN_SIMULATE)


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


def run(client, check_mode=False, no_log_values=None, **over):
    module = MagicMock()
    module.params = params(**over)
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)
    if no_log_values is not None:
        # What AnsibleModule would have collected before main() ran.
        module.no_log_values = set(no_log_values)

    def build(*args, **kwargs):
        spec = kwargs.get('argument_spec')
        if spec is None and args:
            spec = args[0]
        module.argument_spec = spec
        return module

    with patch.object(vm_recipe_deploy, 'AnsibleModule', side_effect=build), \
         patch.object(vm_recipe_deploy, 'get_vergeos_client', return_value=client):
        with pytest.raises(SystemExit):
            vm_recipe_deploy.main()
    return module


def collected_no_log(module_params):
    """The no_log strings ansible-core would collect for this call.

    password and api_key are no_log on their own. answers is no_log as a
    whole, which is what makes an ordinary tier of 1 mask every "1".
    """
    values = set()
    for name in ('password', 'api_key', 'answers'):
        values |= maskable_strings(module_params.get(name))
    return values


def shown(module):
    """The result ansible-core would print, after no_log masking."""
    result = exited(module)
    return remove_values(result, module.no_log_values)


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


# The recipe row from the #178 rerun. SELECT_OS_TIER: 1 is an ordinary answer.
# While "1" stays in no_log_values, ansible-core rewrites every copy of it.
DEBIAN = {
    'name': 'Debian 12 (Bookworm)',
    '$key': 'r1',
    'version': '1.0',
    'creator': 'node1',
    'build': 13,
    'vm_snapshot': 1,
    'catalog_display': 'Local',
    # Whole-value collisions, so masking replaces the field rather than a
    # substring. hunter2 is a password answer. secret is the connection
    # password, a different no_log parameter.
    'pw_echo': 'hunter2',
    'auth_echo': 'secret',
}

TIER_ANSWERS = {
    'SELECT_OS_TIER': 1,
    'HOSTNAME': 'web-01',
    'PASSWORD': 'hunter2',
}


def _debian_client(questions):
    return FakeClient(
        recipes=[DEBIAN],
        questions=questions,
        vms=[{'name': 'web-01', '$key': 12}],
    )


def _run_exists(client, answers):
    module_params = params(recipe=DEBIAN['name'], answers=answers)
    return run(client, no_log_values=collected_no_log(module_params),
               recipe=DEBIAN['name'], answers=answers)


def test_existing_vm_keeps_recipe_fields_when_questions_are_known():
    """A rerun used to exit before narrow_no_log().

    With the questions in hand, a tier of 1 must not scramble the recipe
    row. The password answer, and the connection password, stay masked.
    """
    client = _debian_client([
        question('SELECT_OS_TIER', 'num'),
        question('HOSTNAME'),
        question('PASSWORD', 'password'),
    ])
    module = _run_exists(client, TIER_ANSWERS)
    result = shown(module)

    assert result['changed'] is False
    assert result['already_existed'] is True
    assert result['vm_key'] == '12'
    assert client.posts == []

    recipe = result['recipe']
    assert recipe['name'] == 'Debian 12 (Bookworm)'
    assert recipe['version'] == '1.0'
    assert recipe['creator'] == 'node1'
    assert recipe['build'] == 13
    assert recipe['vm_snapshot'] == 1
    assert recipe['$key'] == 'r1'
    assert recipe['pw_echo'] == 'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER'
    assert recipe['auth_echo'] == 'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER'
    assert '1' not in module.no_log_values
    assert 'web-01' not in module.no_log_values
    assert 'hunter2' in module.no_log_values
    assert 'secret' in module.no_log_values


def test_existing_vm_still_masks_everything_when_no_questions_are_published():
    """No question types means nothing is safe to unmask, rerun included."""
    client = _debian_client([])
    module = _run_exists(client, TIER_ANSWERS)
    result = shown(module)

    assert result['already_existed'] is True
    assert client.posts == []
    recipe = result['recipe']
    assert recipe['name'] == 'Debian ********2 (Bookworm)'
    assert recipe['version'] == '********.0'
    assert recipe['creator'] == 'node********'
    assert recipe['build'] == 'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER'
    assert recipe['vm_snapshot'] == 'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER'
    assert recipe['$key'] == 'r********'
    assert '1' in module.no_log_values


def test_existing_vm_converges_even_when_an_answer_would_be_refused():
    """The lookup classifies answers. It does not turn a rerun into a refusal."""
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('HOSTNAME')],
                        vms=[{'name': 'web-01', '$key': 12}])
    result = exited(run(client, answers={'NOT_A_QUESTION': 'x'}))
    assert result['already_existed'] is True
    assert result['changed'] is False
    assert result['answers_sent'] == []
    assert client.posts == []


def test_a_snapshot_of_the_name_does_not_count_as_convergence():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('HOSTNAME')],
                        vms=[{'name': 'web-01', '$key': 9,
                              'is_snapshot': True}],
                        responses=[SIMULATE_405,
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


def test_unrecognised_bool_answer_fails_before_anything_is_posted():
    """SELECT_CREATE_UEFI: enabled must not reach the platform as a string.

    The platform reads that string as false, so the deploy would report
    success and build a BIOS VM.
    """
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('SELECT_CREATE_UEFI', 'bool')])
    module = run(client, answers={'SELECT_CREATE_UEFI': 'enabled'})
    msg = failed(module)['msg']
    assert 'recognised boolean' in msg
    assert 'true' in msg and 'false' in msg
    assert client.posts == []


@pytest.mark.parametrize('given', [True, 'yes', 1])
def test_recognised_bool_answer_is_sent_as_a_real_boolean(given):
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('SELECT_CREATE_UEFI', 'bool')],
                        responses=[SIMULATE_405,
                                   {'$key': 5, 'response': {'vm': 42}}])
    exited(run(client, answers={'SELECT_CREATE_UEFI': given}))
    assert client.posts[0]['answers']['SELECT_CREATE_UEFI'] is True
    assert client.posts[1]['answers']['SELECT_CREATE_UEFI'] is True


@pytest.mark.parametrize('given', [False, 'off', 0])
def test_recognised_false_answer_is_sent_as_false(given):
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('SELECT_CREATE_UEFI', 'bool')],
                        responses=[SIMULATE_405,
                                   {'$key': 5, 'response': {'vm': 42}}])
    exited(run(client, answers={'SELECT_CREATE_UEFI': given}))
    assert client.posts[0]['answers']['SELECT_CREATE_UEFI'] is False
    assert client.posts[1]['answers']['SELECT_CREATE_UEFI'] is False


@pytest.mark.parametrize('given', [50, 1024, 1048575])
def test_implausible_disksize_fails_before_anything_is_posted(given):
    """YB_DRIVE_OS_SIZE: 50 must not reach the platform as fifty bytes.

    The recipe would build the OS drive at the image size and the deploy
    would report success.
    """
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('YB_DRIVE_OS_SIZE', 'disksize')])
    module = run(client, answers={'YB_DRIVE_OS_SIZE': given})
    msg = failed(module)['msg']
    assert 'bytes' in msg
    assert '53687091200' in msg
    assert '1048576' in msg
    assert client.posts == []


def test_a_list_label_is_refused_locally_and_names_the_key():
    """DHCP is the label the UI shows. The key the recipe wants is dhcp.

    Nothing is posted: the platform would 422 and name the question by its
    display text, without the valid keys.
    """
    client = FakeClient(
        recipes=[RECIPE],
        questions=[question('YB_IP_ADDR_TYPE', 'list', default='dhcp',
                            list={'dhcp': 'DHCP', 'static': 'Static'})])
    module = run(client, answers={'YB_IP_ADDR_TYPE': 'DHCP'})
    msg = failed(module)['msg']
    assert 'not valid' in msg
    assert 'valid: dhcp=DHCP, static=Static' in msg
    assert "'DHCP' is the label for key 'dhcp'" in msg
    assert client.posts == []


def test_a_bogus_list_answer_is_refused_with_the_valid_choices():
    client = FakeClient(
        recipes=[RECIPE],
        questions=[question('YB_IP_ADDR_TYPE', 'list', default='dhcp',
                            list={'dhcp': 'DHCP', 'static': 'Static'})])
    module = run(client, answers={'YB_IP_ADDR_TYPE': 'bogus'})
    msg = failed(module)['msg']
    assert 'valid: dhcp=DHCP, static=Static' in msg
    assert 'label for key' not in msg
    assert client.posts == []


def test_a_list_choice_key_is_sent():
    client = FakeClient(
        recipes=[RECIPE],
        questions=[question('YB_IP_ADDR_TYPE', 'list', default='dhcp',
                            list={'dhcp': 'DHCP', 'static': 'Static'})],
        responses=[SIMULATE_405, {'$key': 5, 'response': {'vm': 42}}])
    exited(run(client, answers={'YB_IP_ADDR_TYPE': 'dhcp'}))
    assert client.posts[0]['answers']['YB_IP_ADDR_TYPE'] == 'dhcp'
    assert client.posts[1]['answers']['YB_IP_ADDR_TYPE'] == 'dhcp'


@pytest.mark.parametrize('given', [0, 1048576, 21474836480])
def test_disksize_byte_count_is_sent(given):
    """Zero and a real byte count are posted. Zero is the recipe default."""
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('YB_DRIVE_OS_SIZE', 'disksize')],
                        responses=[SIMULATE_405,
                                   {'$key': 5, 'response': {'vm': 42}}])
    exited(run(client, answers={'YB_DRIVE_OS_SIZE': given}))
    assert client.posts[0]['answers']['YB_DRIVE_OS_SIZE'] == given
    assert client.posts[1]['answers']['YB_DRIVE_OS_SIZE'] == given


def test_missing_required_answer_fails():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('USER', required=True, default='')])
    module = run(client)
    assert 'missing required answer' in failed(module)['msg']
    assert client.posts == []


def test_valid_answers_are_sent_by_name_only_in_the_result():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('HOSTNAME'), question('USER')],
                        responses=[SIMULATE_405,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'HOSTNAME': 'web-01',
                                         'USER': 'ops'}))
    assert result['answers_sent'] == ['HOSTNAME', 'USER']


def test_secret_answer_values_never_reach_the_result():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('PASSWORD', 'password')],
                        responses=[SIMULATE_405,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'PASSWORD': 'hunter2'}))
    assert 'hunter2' not in repr(result)


def test_hints_are_reported_but_not_fatal_by_default():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('SELECT_OS_TIER', 'row',
                                            default='', table='cluster_tiers')],
                        responses=[SIMULATE_405,
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
                        responses=[SIMULATE_405,
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
        responses=[SIMULATE_405, {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'SELECT_OS_TIER': 4}))
    assert result['changed'] is True


# ── network answers by name ──────────────────────────────────────────────────

def test_a_network_answer_given_by_name_is_resolved_to_its_key():
    client = FakeClient(recipes=[RECIPE],
                        questions=[question('YB_NIC_ETH0', 'network')],
                        networks=[{'name': 'External', '$key': 3}],
                        responses=[SIMULATE_405,
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
                        responses=[SIMULATE_405,
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
                        responses=[(405, dirty)])
    module = run(client, answers={'HOSTNAME': 'web-01'})
    result = failed(module)
    assert 'would build a broken VM' in result['msg']
    assert result['simulate_result']['ok'] is False
    # The simulate POST is the only one that fired.
    assert len(client.posts) == 1


def test_simulate_result_is_reported_on_success():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[SIMULATE_405,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'HOSTNAME': 'web-01'}))
    assert result['simulate_result']['ok'] is True
    assert result['simulate_result']['step_count'] == 2
    assert result['simulate_result']['cloudinit_files'] == ['user-data']


def test_simulate_422_with_a_reason_is_an_answer_refusal_not_transport():
    """The request arrived and the platform refused an answer.

    simulate is on by default, so this is the message most people see. It
    must not say the POST failed at the transport level.
    """
    client = FakeClient(
        recipes=[RECIPE], questions=[question('HOSTNAME')],
        responses=[(422, {'err': "'Select the IP Address Type' is invalid"})])
    module = run(client, answers={'HOSTNAME': 'web-01'})
    msg = failed(module)['msg']
    assert 'transport' not in msg
    assert 'refused the answer set' in msg
    assert 'HTTP 422' in msg
    assert 'Select the IP Address Type' in msg
    # The real deploy must not follow a refused simulate.
    assert len(client.posts) == 1
    assert client.posts[0]['simulate'] is True


def test_simulate_with_no_reason_is_still_a_transport_failure():
    client = FakeClient(
        recipes=[RECIPE], questions=[question('HOSTNAME')],
        responses=[(500, None)])
    module = run(client, answers={'HOSTNAME': 'web-01'})
    msg = failed(module)['msg']
    assert 'transport' in msg
    assert 'HTTP 500' in msg
    assert len(client.posts) == 1


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
                        responses=[SIMULATE_405])
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
                        responses=[SIMULATE_405,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'HOSTNAME': 'web-01'}))
    assert result['changed'] is True
    assert result['vm_key'] == '42'
    assert result['instance_key'] == '5'


def test_auto_update_is_sent_on_the_real_deploy_only():
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[SIMULATE_405,
                                   {'$key': 5, 'response': {'vm': 42}}])
    run(client, auto_update=True, answers={'HOSTNAME': 'web-01'})
    assert 'auto_update' not in client.posts[0]
    assert client.posts[1]['auto_update'] is True


def test_a_deploy_that_reports_no_vm_key_still_succeeds():
    """The deploy was accepted; a missing field must not fail it."""
    client = FakeClient(recipes=[RECIPE], questions=[question('HOSTNAME')],
                        responses=[SIMULATE_405, {'$key': 5}])
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
                        responses=[SIMULATE_405,
                                   {'$key': 5, 'response': {'vm': 42}}])
    result = exited(run(client, answers={'ANYTHING': 'goes'}))
    assert result['changed'] is True
    assert result['answers_sent'] == ['ANYTHING']
    assert client.posts[1]['answers'] == {'ANYTHING': 'goes'}
