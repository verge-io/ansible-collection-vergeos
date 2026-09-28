# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the protect role's scan/assign helper.

The script shipped with no tests at all. Its `scan` verb is a three-way join
-- tags to tag_members to VMs -- and everything the reconciler does downstream
is decided by what that join returns. A join that quietly matches nothing
reports every VM as untagged, which reads exactly like "nothing to do".
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'protect', 'files'))
import protect_ctl  # noqa: E402


class FakeManager:
    def __init__(self, rows):
        self._rows = list(rows)
        self.updates = []

    def list(self, **_kwargs):
        return [dict(r) for r in self._rows]

    def update(self, key, **fields):
        self.updates.append((key, fields))


class FakeClient:
    """Stands in for VergeClient, with the two raw tables the script reads."""

    def __init__(self, tags=(), tag_members=(), vms=(), profiles=(),
                 readback=None):
        self.tags = FakeManager(tags)
        self.snapshot_profiles = FakeManager(profiles)
        self.vms = FakeManager([])
        self._tag_members = list(tag_members)
        self._vms = list(vms)
        self._readback = readback
        self.requests = []

    def _request(self, method, path, params=None, json_data=None):
        self.requests.append({'method': method, 'path': path,
                              'params': params})
        if path == 'tag_members':
            return [dict(m) for m in self._tag_members]
        if path == 'vms':
            return [dict(v) for v in self._vms]
        if path.startswith('vms/'):
            return dict(self._readback or {})
        raise AssertionError('unexpected request to %r' % path)


def tag(key, name, category='protect'):
    return {'$key': key, 'name': name, 'category_name': category}


def member(tag_key, member_ref):
    return {'tag': tag_key, 'member': member_ref}


def vm(key, name, snapshot_profile='', is_snapshot=False):
    return {'$key': key, 'name': name, 'is_snapshot': is_snapshot,
            'snapshot_profile': snapshot_profile}


# ── the join ─────────────────────────────────────────────────────────────────

class TestScanJoinsTagsToVMs:
    def test_a_tagged_vm_reports_its_protect_class(self):
        result = protect_ctl.scan(FakeClient(
            tags=[tag(1, 'gold')],
            tag_members=[member(1, 'vms/7')],
            vms=[vm(7, 'db-01')]))
        assert result['vms'] == [
            {'key': 7, 'name': 'db-01', 'protect_class': 'gold',
             'snapshot_profile': ''}]

    def test_an_untagged_vm_reports_no_class_rather_than_being_dropped(self):
        """The reconciler needs to see the VM to decide it needs nothing.
        Dropping it and reporting no class are different answers."""
        result = protect_ctl.scan(FakeClient(
            tags=[tag(1, 'gold')], tag_members=[], vms=[vm(7, 'db-01')]))
        assert result['vms'][0]['protect_class'] is None

    def test_a_tag_outside_the_protect_category_is_ignored(self):
        """Tags are a general facility. Treating `DB` as a protect class would
        enrol every VM somebody labelled by application."""
        result = protect_ctl.scan(FakeClient(
            tags=[tag(3, 'DB', category='App')],
            tag_members=[member(3, 'vms/7')],
            vms=[vm(7, 'db-01')]))
        assert result['vms'][0]['protect_class'] is None

    def test_a_tag_with_no_category_is_ignored(self):
        result = protect_ctl.scan(FakeClient(
            tags=[{'$key': 3, 'name': 'loose', 'category_name': None}],
            tag_members=[member(3, 'vms/7')],
            vms=[vm(7, 'db-01')]))
        assert result['vms'][0]['protect_class'] is None

    def test_membership_of_something_that_is_not_a_vm_is_ignored(self):
        """`member` is a reference, not a key: the same table carries users,
        networks and VMs. Reading `users/7` as VM 7 would enrol the wrong
        object.

        A related shape was checked and does NOT occur here, recorded so
        nobody re-checks it: the GROUP membership table renders the same kind
        of reference as `/v4/users/2` through a raw fields=all read while the
        SDK renders `users/1`, which would make a `startswith('vms/')` test
        silently match nothing. Measured on 26.1.8 by tagging a scratch VM,
        `tag_members` returns `vms/41` for BOTH fields=most and fields=all.
        The prefix check is right for this table.
        """
        result = protect_ctl.scan(FakeClient(
            tags=[tag(1, 'gold')],
            tag_members=[member(1, 'users/7')],
            vms=[vm(7, 'db-01')]))
        assert result['vms'][0]['protect_class'] is None

    def test_several_vms_get_their_own_classes(self):
        result = protect_ctl.scan(FakeClient(
            tags=[tag(1, 'gold'), tag(2, 'silver')],
            tag_members=[member(1, 'vms/7'), member(2, 'vms/8')],
            vms=[vm(7, 'db-01'), vm(8, 'web-01')]))
        assert {row['name']: row['protect_class'] for row in result['vms']} == {
            'db-01': 'gold', 'web-01': 'silver'}

    def test_tag_members_is_not_read_when_no_protect_tag_exists(self):
        """A bulk read of every tag membership on the system, for a join that
        cannot produce anything."""
        client = FakeClient(tags=[tag(3, 'DB', category='App')],
                            vms=[vm(7, 'db-01')])
        protect_ctl.scan(client)
        assert not [r for r in client.requests if r['path'] == 'tag_members']


class TestScanSkipsSnapshots:
    def test_a_snapshot_is_not_a_vm_to_protect(self):
        """Snapshots live in the vms table. Enrolling them in a snapshot
        profile is recursive nonsense, and there are far more of them than
        there are VMs."""
        result = protect_ctl.scan(FakeClient(
            vms=[vm(7, 'db-01'), vm(8, 'db-01-2026-01-01', is_snapshot=True)]))
        assert [row['name'] for row in result['vms']] == ['db-01']


class TestScanNormalisesTheProfileField:
    def test_a_null_profile_reads_as_empty_and_not_as_none(self):
        """Downstream compares against a profile key as a string. `None`
        would compare unequal to '' and read as "enrolled in something"."""
        result = protect_ctl.scan(FakeClient(
            vms=[dict(vm(7, 'db-01'), snapshot_profile=None)]))
        assert result['vms'][0]['snapshot_profile'] == ''

    def test_an_enrolment_is_reported_as_it_is_stored(self):
        result = protect_ctl.scan(FakeClient(
            vms=[vm(7, 'db-01', snapshot_profile='2')]))
        assert result['vms'][0]['snapshot_profile'] == '2'

    def test_profiles_are_returned_as_a_name_to_key_map(self):
        result = protect_ctl.scan(FakeClient(
            profiles=[{'$key': 2, 'name': 'nightly'},
                      {'$key': 3, 'name': 'hourly'}]))
        assert result['profiles'] == {'nightly': 2, 'hourly': 3}

    def test_the_projection_asks_for_every_field_the_scan_reads(self):
        """A field that is not fetched arrives as None, and this scan is the
        only thing that tells the reconciler what is already enrolled."""
        client = FakeClient(vms=[vm(7, 'db-01')])
        protect_ctl.scan(client)
        fields = [r['params']['fields'] for r in client.requests
                  if r['path'] == 'vms'][0]
        for name in ('$key', 'name', 'is_snapshot', 'snapshot_profile'):
            assert name in fields, '%s is read but never fetched' % name


# ── writing ──────────────────────────────────────────────────────────────────

class TestPutProfileReadsBackWhatItWrote:
    """The whole reason this verb exists. VergeOS accepts a write it discards
    and answers HTTP 200 -- the defect family behind #8, #10, #18 and #87 --
    so a write that is not read back is a write that might not have happened.
    """

    def test_it_writes_through_the_manager(self):
        client = FakeClient(readback={'snapshot_profile': '2'})
        protect_ctl.put_profile(client, 7, 2)
        assert client.vms.updates == [(7, {'snapshot_profile': 2})]

    def test_it_returns_what_the_platform_actually_stored(self):
        client = FakeClient(readback={'snapshot_profile': '2'})
        assert protect_ctl.put_profile(client, 7, 2) == '2'

    def test_a_discarded_write_comes_back_as_the_old_value(self):
        client = FakeClient(readback={'snapshot_profile': ''})
        assert protect_ctl.put_profile(client, 7, 2) == ''

    def test_a_null_readback_is_an_empty_string(self):
        client = FakeClient(readback={'snapshot_profile': None})
        assert protect_ctl.put_profile(client, 7, 2) == ''


def run_main(argv, client, monkeypatch, capsys):
    monkeypatch.setattr(protect_ctl, 'client', lambda: client)
    monkeypatch.setattr(sys, 'argv', ['protect_ctl'] + argv)
    code = protect_ctl.main()
    return code, capsys.readouterr()


class TestAssignFailsLoudly:
    def test_a_persisted_assignment_exits_zero_and_reports_it(
            self, monkeypatch, capsys):
        client = FakeClient(readback={'snapshot_profile': '2'})
        code, out = run_main(['assign', '--vm-key', '7', '--profile-key', '2'],
                             client, monkeypatch, capsys)
        assert code == 0
        assert json.loads(out.out) == {'vm_key': 7, 'snapshot_profile': '2'}

    def test_an_assignment_that_did_not_persist_exits_two(
            self, monkeypatch, capsys):
        client = FakeClient(readback={'snapshot_profile': ''})
        code, out = run_main(['assign', '--vm-key', '7', '--profile-key', '2'],
                             client, monkeypatch, capsys)
        assert code == 2
        assert 'did not persist' in out.err

    def test_and_says_what_it_wrote_and_what_came_back(
            self, monkeypatch, capsys):
        client = FakeClient(readback={'snapshot_profile': '9'})
        _code, out = run_main(['assign', '--vm-key', '7', '--profile-key', '2'],
                              client, monkeypatch, capsys)
        assert "wrote 2" in out.err
        assert "read back '9'" in out.err


class TestClear:
    @pytest.mark.parametrize('stored', ['', 'None', '0'])
    def test_the_spellings_of_no_enrolment_all_count_as_cleared(
            self, stored, monkeypatch, capsys):
        """The field has been seen empty, null and zero for the same state."""
        client = FakeClient(readback={'snapshot_profile': stored})
        code, _out = run_main(['clear', '--vm-key', '7'],
                              client, monkeypatch, capsys)
        assert code == 0

    def test_a_clear_that_did_not_take_exits_two(self, monkeypatch, capsys):
        client = FakeClient(readback={'snapshot_profile': '2'})
        code, out = run_main(['clear', '--vm-key', '7'],
                             client, monkeypatch, capsys)
        assert code == 2
        assert 'clear did not persist' in out.err

    def test_clear_writes_an_empty_string(self, monkeypatch, capsys):
        client = FakeClient(readback={'snapshot_profile': ''})
        run_main(['clear', '--vm-key', '7'], client, monkeypatch, capsys)
        assert client.vms.updates == [(7, {'snapshot_profile': ''})]


class TestArgumentHandling:
    def test_assign_without_a_vm_key_is_refused(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            run_main(['assign', '--profile-key', '2'], FakeClient(),
                     monkeypatch, capsys)

    def test_assign_without_a_profile_key_is_refused(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            run_main(['assign', '--vm-key', '7'], FakeClient(),
                     monkeypatch, capsys)

    def test_clear_without_a_vm_key_is_refused(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            run_main(['clear'], FakeClient(), monkeypatch, capsys)

    def test_an_unknown_verb_is_refused(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            run_main(['destroy'], FakeClient(), monkeypatch, capsys)

    def test_scan_needs_no_keys_and_emits_json(self, monkeypatch, capsys):
        client = FakeClient(vms=[vm(7, 'db-01')])
        code, out = run_main(['scan'], client, monkeypatch, capsys)
        assert code == 0
        assert json.loads(out.out)['vms'][0]['name'] == 'db-01'
