"""Unit tests for the vm_drive_info module."""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock, patch

from ansible_collections.vergeio.vergeos.plugins.modules import vm_drive_info


class FakeSubManager:
    def __init__(self, rows):
        self._rows = rows
        self.called_with = None

    def list(self, **kwargs):
        self.called_with = kwargs
        rows = list(self._rows)
        media = kwargs.get('media')
        if media:
            rows = [r for r in rows if r.get('media') == media]
        return rows


class FakeVM(dict):
    def __init__(self, row, drives=()):
        super().__init__(dict({'name': 'web-01'}, **row))
        self.drives = FakeSubManager(drives)


class FakeVMs:
    """A vms manager as resolve_one uses it.

    The module resolves by name through `resolve_one`, which lists and matches
    CLIENT-SIDE rather than calling `get(name=)` -- see #72 and pyVergeOS#100.
    A fake that only stubs `get()` leaves `list()` a bare MagicMock returning
    another MagicMock, whose `.get('name')` never equals the name being looked
    for, so every lookup fails with "VM not found" and every test in the file
    fails for a reason unrelated to what it is testing.
    """

    def __init__(self, vms):
        self._vms = list(vms)
        self.list_kwargs = None

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return list(self._vms)


class FakeClient:
    def __init__(self, vm=None, stats=None):
        self.vms = FakeVMs([vm] if vm is not None else [])
        self._stats = stats
        self.requests = []

    def _request(self, method, path, params=None, json_data=None):
        self.requests.append({'path': path, 'params': params})
        return self._stats


def drive(key, name, media='disk', status='online', **kw):
    row = {'$key': key, 'name': name, 'media': media, 'status': status,
           'status_info': ''}
    row.update(kw)
    return row


def params(**over):
    base = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'vm': 'web-01',
        'media': None,
        'stats': False,
    }
    base.update(over)
    return base


def run(client, **over):
    module = MagicMock()
    module.params = params(**over)
    module.check_mode = False
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)

    with patch.object(vm_drive_info, 'AnsibleModule', return_value=module), \
         patch.object(vm_drive_info, 'get_vergeos_client', return_value=client):
        with pytest.raises(SystemExit):
            vm_drive_info.main()
    return module


def exited(module):
    assert module.exit_json.called, (
        "expected exit_json, got fail_json: %s" % (module.fail_json.call_args,))
    return module.exit_json.call_args[1]


def test_reports_the_vms_drives():
    vm = FakeVM({'machine': 18}, drives=[drive(1, 'OS'), drive(2, 'cd', 'cdrom')])
    result = exited(run(FakeClient(vm)))
    assert [d['name'] for d in result['drives']] == ['OS', 'cd']
    assert result['machine'] == '18'


def test_media_filter_is_passed_to_the_sdk():
    vm = FakeVM({'machine': 18}, drives=[drive(1, 'OS'), drive(2, 'cd', 'cdrom')])
    result = exited(run(FakeClient(vm), media='disk'))
    assert [d['name'] for d in result['drives']] == ['OS']
    assert vm.drives.called_with['media'] == 'disk'


def test_status_info_is_requested_because_it_carries_import_progress():
    vm = FakeVM({'machine': 18}, drives=[drive(1, 'OS')])
    run(FakeClient(vm))
    assert 'status#status_info as status_info' in vm.drives.called_with['fields']


# ── importing ────────────────────────────────────────────────────────────────

def test_media_import_counts_as_unfinished():
    vm = FakeVM({'machine': 18}, drives=[drive(1, 'OS', media='import')])
    result = exited(run(FakeClient(vm)))
    assert [d['name'] for d in result['importing']] == ['OS']
    assert result['failed_drives'] == []


def test_status_importing_counts_as_unfinished():
    """A drive can be status=importing while its media already reads disk."""
    vm = FakeVM({'machine': 18},
                drives=[drive(1, 'OS', status='importing')])
    result = exited(run(FakeClient(vm)))
    assert [d['name'] for d in result['importing']] == ['OS']


def test_a_drive_is_not_double_counted_as_unfinished():
    vm = FakeVM({'machine': 18},
                drives=[drive(1, 'OS', media='import', status='importing')])
    result = exited(run(FakeClient(vm)))
    assert len(result['importing']) == 1


def test_a_finished_drive_is_not_unfinished():
    vm = FakeVM({'machine': 18}, drives=[drive(1, 'OS')])
    result = exited(run(FakeClient(vm)))
    assert result['importing'] == []
    assert result['failed_drives'] == []


def test_a_vm_with_no_drives_reports_an_empty_list():
    """This is the shape a recipe leaves behind when its drive step failed."""
    vm = FakeVM({'machine': 18}, drives=[])
    result = exited(run(FakeClient(vm)))
    assert result['drives'] == []
    assert result['importing'] == []
    assert result['failed_drives'] == []


# ── failed imports ───────────────────────────────────────────────────────────
#
# Issue #131. A cloud-image import the platform cannot download is marked
# status=errors within about two seconds and never retried, but media stays
# import. Counting that as unfinished made vm_from_recipe burn the whole
# wait_timeout and then advise raising it.

def test_an_errored_import_is_not_still_importing():
    """media=import, status=errors must not sit in importing."""
    row = drive(1, 'OS', media='import', status='errors',
                status_info='Unable to download https://example.invalid/os.qcow2')
    assert vm_drive_info.unfinished([row]) == []
    assert vm_drive_info.failed_drives([row]) == [row]


def test_an_errored_import_is_returned_on_failed_drives():
    row = drive(1, 'OS', media='import', status='errors',
                status_info='Unable to download https://example.invalid/os.qcow2')
    vm = FakeVM({'machine': 18}, drives=[row])
    result = exited(run(FakeClient(vm)))
    assert result['importing'] == []
    assert [d['name'] for d in result['failed_drives']] == ['OS']
    assert result['failed_drives'][0]['status'] == 'errors'
    assert result['failed_drives'][0]['status_info'].startswith('Unable to download')


def test_a_failed_drive_does_not_hide_one_still_importing():
    """The wait must be able to see both: exit on the failure, keep waiting
    only when something is actually still importing and nothing has failed.
    """
    still = drive(1, 'OS', media='import', status='importing',
                  status_info='Downloading (12%)')
    dead = drive(2, 'DATA', media='import', status='errors',
                 status_info='Unable to download https://example.invalid/data.qcow2')
    assert [d['name'] for d in vm_drive_info.unfinished([still, dead])] == ['OS']
    assert [d['name'] for d in vm_drive_info.failed_drives([still, dead])] == ['DATA']


def test_status_importing_is_not_a_failure():
    row = drive(1, 'OS', media='import', status='importing')
    assert vm_drive_info.failed_drives([row]) == []
    assert [d['name'] for d in vm_drive_info.unfinished([row])] == ['OS']


# ── stats ────────────────────────────────────────────────────────────────────

def test_stats_are_not_read_unless_asked_for():
    vm = FakeVM({'machine': 18}, drives=[drive(1, 'OS')])
    client = FakeClient(vm)
    result = exited(run(client))
    assert client.requests == []
    assert 'write_bytes' not in result['drives'][0]


def test_stats_are_matched_to_their_own_drive():
    """Addressed by a filter on parent_drive, never by row position.

    machine_drive_stats/<n> resolves to the row whose OWN $key is n, which
    belongs to a different drive -- so a positional read reports another VM's
    IO and reports it as success.
    """
    vm = FakeVM({'machine': 18},
                drives=[drive(1, 'OS'), drive(2, 'DATA')])
    client = FakeClient(vm, stats=[
        {'parent_drive': 2, 'write_bytes': 999, 'writes': 9},
        {'parent_drive': 1, 'write_bytes': 419, 'writes': 4},
    ])
    result = exited(run(client, stats=True))
    by_name = {d['name']: d for d in result['drives']}
    assert by_name['OS']['write_bytes'] == 419
    assert by_name['DATA']['write_bytes'] == 999


def test_the_stats_query_filters_on_every_drive():
    vm = FakeVM({'machine': 18}, drives=[drive(1, 'OS'), drive(2, 'DATA')])
    client = FakeClient(vm, stats=[])
    run(client, stats=True)
    assert client.requests[0]['path'] == 'machine_drive_stats'
    assert client.requests[0]['params']['filter'] == (
        'parent_drive eq 1 or parent_drive eq 2')


def test_a_drive_with_no_stats_row_reports_zeroes():
    """Zero is meaningful here -- every counter is 0 before first power-on."""
    vm = FakeVM({'machine': 18}, drives=[drive(1, 'OS')])
    result = exited(run(FakeClient(vm, stats=[]), stats=True))
    assert result['drives'][0]['write_bytes'] == 0
    assert result['drives'][0]['read_bytes'] == 0


def test_a_count_document_is_treated_as_no_stats():
    """A filter matching nothing returns {"$count": 0}, not []."""
    vm = FakeVM({'machine': 18}, drives=[drive(1, 'OS')])
    result = exited(run(FakeClient(vm, stats={'$count': 0}), stats=True))
    assert result['drives'][0]['write_bytes'] == 0


def test_no_stats_query_is_made_when_there_are_no_drives():
    vm = FakeVM({'machine': 18}, drives=[])
    client = FakeClient(vm, stats=[])
    exited(run(client, stats=True))
    assert client.requests == []
