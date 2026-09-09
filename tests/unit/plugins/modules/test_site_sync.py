"""Unit tests for the site_sync module."""

import pytest
from unittest.mock import MagicMock, patch

from ansible_collections.vergeio.vergeos.plugins.modules import site_sync


class FakeManager:
    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.created = None
        self.updated = None
        self.deleted = None
        self.started = 0
        self.stopped = 0

    def list(self, **kwargs):
        return [dict(r) for r in self.rows]

    def create(self, **kwargs):
        self.created = kwargs
        row = {'name': kwargs['name'], '$key': 99}
        row.update({k: v for k, v in kwargs.items() if k != 'name'})
        self.rows.append(row)
        return row

    def update(self, key, **kwargs):
        self.updated = (key, kwargs)
        for row in self.rows:
            if row['$key'] == key:
                row.update(kwargs)
        return {}

    def delete(self, key):
        self.deleted = key
        self.rows = [r for r in self.rows if r['$key'] != key]

    def get(self, key=None, **kwargs):
        mgr = self

        class Handle:
            def start(self):
                mgr.started += 1

            def stop(self):
                mgr.stopped += 1
        return Handle()


class FakeClient:
    def __init__(self, rows=()):
        self.site_syncs = FakeManager(rows)


def params(**over):
    base = {
        'host': 'vergeos.example.com', 'username': 'admin',
        'password': 'secret', 'insecure': False,
        'name': 'to-dr-site', 'state': 'present', 'site': None,
        'registration_code': None, 'url': None, 'description': None,
        'enabled': None, 'encryption': None, 'compression': None,
        'netinteg': None, 'threads': None, 'file_threads': None,
        'destination_tier': None, 'queue_retry_count': None,
        'queue_retry_interval': None, 'queue_retry_multiplier': None,
        'note': None,
    }
    base.update(over)
    return base


def run(client, check_mode=False, **over):
    module = MagicMock()
    module.params = params(**over)
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)

    with patch.object(site_sync, 'AnsibleModule', return_value=module), \
         patch.object(site_sync, 'get_vergeos_client', return_value=client):
        with pytest.raises(SystemExit):
            site_sync.main()
    return module


def exited(module):
    assert module.exit_json.called, (
        "expected exit_json, got fail_json: %s" % (module.fail_json.call_args,))
    return module.exit_json.call_args[1]


def failed(module):
    assert module.fail_json.called, (
        "expected fail_json, got exit_json: %s" % (module.exit_json.call_args,))
    return module.fail_json.call_args[1]


EXISTING = {'name': 'to-dr-site', '$key': 5, 'enabled': True, 'online': True,
            'threads': 4, 'file_threads': 4, 'encryption': True,
            'queue_retry_interval': 30, 'syncing': False}


# ── create ───────────────────────────────────────────────────────────────────

def test_creates_when_absent():
    client = FakeClient()
    result = exited(run(client, site=1, registration_code='abc123',
                        url='https://dr.example.com'))
    assert result['changed'] is True
    assert client.site_syncs.created['site'] == 1
    assert client.site_syncs.created['registration_code'] == 'abc123'


def test_creating_without_site_or_code_fails_with_a_useful_message():
    module = run(FakeClient())
    msg = failed(module)['msg']
    assert 'site' in msg and 'registration_code' in msg
    assert 'incoming sync on the remote system' in msg


def test_check_mode_creates_nothing():
    client = FakeClient()
    result = exited(run(client, check_mode=True, site=1,
                        registration_code='abc'))
    assert result['changed'] is True
    assert client.site_syncs.created is None


def test_the_registration_code_is_never_echoed_back():
    client = FakeClient()
    result = exited(run(client, site=1, registration_code='s3cr3t'))
    assert 's3cr3t' not in repr(result)


# ── reconcile ────────────────────────────────────────────────────────────────

def test_no_change_when_everything_matches():
    client = FakeClient([EXISTING])
    result = exited(run(client, threads=4, encryption=True))
    assert result['changed'] is False
    assert result['changed_fields'] == []
    assert client.site_syncs.updated is None


def test_drift_is_corrected_and_named():
    client = FakeClient([EXISTING])
    result = exited(run(client, threads=2, file_threads=1))
    assert result['changed'] is True
    assert result['changed_fields'] == ['file_threads', 'threads']
    assert client.site_syncs.updated[1] == {'threads': 2, 'file_threads': 1}


def test_unspecified_fields_are_left_alone():
    """Omitting a parameter must not reset it to a default."""
    client = FakeClient([EXISTING])
    exited(run(client, threads=2))
    assert 'encryption' not in (client.site_syncs.updated[1])


def test_the_verbose_retry_keyword_compares_against_the_row_field():
    """The SDK says queue_retry_interval_seconds; the row says
    queue_retry_interval. Comparing the wrong one reports drift every run."""
    client = FakeClient([EXISTING])
    result = exited(run(client, queue_retry_interval=30))
    assert result['changed'] is False


def test_a_real_retry_interval_change_is_detected():
    client = FakeClient([EXISTING])
    result = exited(run(client, queue_retry_interval=60))
    assert result['changed_fields'] == ['queue_retry_interval_seconds']


def test_the_registration_code_is_not_reconciled_on_an_existing_sync():
    """It is a create-time credential the API does not return. Reconciling it
    would rewrite an established trust relationship on every run."""
    client = FakeClient([EXISTING])
    result = exited(run(client, registration_code='different'))
    assert result['changed'] is False
    assert client.site_syncs.updated is None


def test_enabled_false_disables_without_deleting():
    client = FakeClient([EXISTING])
    result = exited(run(client, enabled=False))
    assert result['changed_fields'] == ['enabled']
    assert client.site_syncs.deleted is None


def test_check_mode_reports_drift_without_writing():
    client = FakeClient([EXISTING])
    result = exited(run(client, check_mode=True, threads=1))
    assert result['changed'] is True
    assert client.site_syncs.updated is None


# ── absent ───────────────────────────────────────────────────────────────────

def test_absent_removes_the_sync():
    client = FakeClient([EXISTING])
    result = exited(run(client, state='absent'))
    assert result['changed'] is True
    assert client.site_syncs.deleted == 5


def test_absent_is_a_no_op_when_already_gone():
    client = FakeClient()
    result = exited(run(client, state='absent'))
    assert result['changed'] is False
    assert client.site_syncs.deleted is None


def test_absent_says_replicated_snapshots_survive():
    client = FakeClient([EXISTING])
    assert 'not removed' in exited(run(client, state='absent'))['msg']


def test_absent_in_check_mode_removes_nothing():
    client = FakeClient([EXISTING])
    exited(run(client, check_mode=True, state='absent'))
    assert client.site_syncs.deleted is None


# ── started / stopped ────────────────────────────────────────────────────────

def test_started_runs_the_sync():
    client = FakeClient([EXISTING])
    result = exited(run(client, state='started'))
    assert client.site_syncs.started == 1
    assert 'started' in result['changed_fields']


def test_started_leaves_a_running_sync_alone():
    """Restarting mid-flight discards the progress already made."""
    client = FakeClient([dict(EXISTING, syncing=True)])
    result = exited(run(client, state='started'))
    assert client.site_syncs.started == 0
    assert result['changed'] is False
    assert 'already running' in result['msg']


def test_started_creates_the_sync_first_when_absent():
    client = FakeClient()
    exited(run(client, state='started', site=1, registration_code='abc'))
    assert client.site_syncs.created is not None
    assert client.site_syncs.started == 1


def test_started_in_check_mode_starts_nothing():
    client = FakeClient([EXISTING])
    exited(run(client, check_mode=True, state='started'))
    assert client.site_syncs.started == 0


def test_stopped_stops_a_running_sync():
    client = FakeClient([dict(EXISTING, syncing=True)])
    result = exited(run(client, state='stopped'))
    assert client.site_syncs.stopped == 1
    assert result['changed'] is True


def test_stopped_is_a_no_op_when_not_running():
    client = FakeClient([EXISTING])
    result = exited(run(client, state='stopped'))
    assert client.site_syncs.stopped == 0
    assert result['changed'] is False


def test_stopped_on_a_missing_sync_fails():
    assert "to stop" in failed(run(FakeClient(), state='stopped'))['msg']
