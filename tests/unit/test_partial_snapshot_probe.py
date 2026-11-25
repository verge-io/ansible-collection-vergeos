# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The live partial-snapshot helper, without a cluster.

The ladder is the thing that talks to VergeOS. These tests pin the order
that ladder depends on: poll GET cloud_snapshots/N?fields=all after
create(wait=True), call find_vms only once that status has left building,
retry that POST on ValidationError 'System snapshot is still building',
and read cloud_snapshot_vms until a row exists. cloud_snapshots.get() is
not that check. They also pin the filter and the POST body, which are
easy to "tidy" into something the platform does not fill in.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'live'))
import partial_snapshot_probe as probe  # noqa: E402


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(probe.time, 'sleep', lambda _seconds: None)


class Snap(dict):
    """A snapshot row that dict() and .get both see."""


class Cloud:
    def __init__(self, created_status='normal', gets=None):
        self.created = None
        self.gets = []
        self.deleted = []
        self._created_status = created_status
        self._gets = list(gets or [])

    def create(self, **kwargs):
        self.created = kwargs
        return Snap({'$key': 9, 'status': self._created_status})

    def get(self, key):
        self.gets.append(key)
        if self._gets:
            status = self._gets.pop(0)
        else:
            status = 'normal'
        return Snap({'$key': key, 'status': status})

    def delete(self, key):
        self.deleted.append(key)


class Client:
    def __init__(self, cloud=None, statuses=None):
        self.cloud_snapshots = cloud or Cloud()
        self.calls = []
        self._members = [
            {'name': 'zz-partial-in', 'is_snapshot': False,
             'cloud_snapshot': 9},
        ]
        self._snapshots = []
        self._statuses = list(statuses or [])
        self._status_hook = None
        self.snapshot_profiles = None

    def _request(self, method, path, params=None, json_data=None):
        self.calls.append({
            'method': method, 'path': path,
            'params': params, 'json_data': json_data,
        })
        if path == 'cloud_snapshot_vms':
            return list(self._members)
        if path == 'cloud_snapshots':
            return list(self._snapshots)
        if method == 'GET' and path.startswith('cloud_snapshots/'):
            key = int(path.rsplit('/', 1)[-1])
            if self._status_hook is not None:
                hook = self._status_hook
                self._status_hook = None
                hook()
            item = self._statuses.pop(0) if self._statuses else 'normal'
            if isinstance(item, dict):
                return item
            if item is None:
                return {'$key': key}
            return {'$key': key, 'status': item}
        return None


def test_settled_treats_a_missing_status_as_not_done():
    assert probe.settled(None) is False
    assert probe.settled('') is False
    assert probe.settled('building') is False
    assert probe.settled('normal') is True


def test_create_refuses_to_snapshot_the_whole_cloud():
    client = Client()
    with pytest.raises(ValueError, match='whole cloud'):
        probe.create_partial(client, 'zz-partial-snap', '')
    assert client.cloud_snapshots.created is None
    assert client.calls == []


def test_create_without_the_parameter_does_not_call_create():
    class OldCloud:
        def create(self, name=None, wait=False, wait_timeout=300,
                   retention_seconds=None):
            raise AssertionError('create was called')

    client = Client()
    client.cloud_snapshots = OldCloud()
    with pytest.raises(ValueError, match='include_tags'):
        probe.create_partial(client, 'zz-partial-snap', 'zz-partial')


def test_create_polls_fields_all_before_find_vms():
    """wait=True's get() is not the status we trust (#202).

    get() would say normal immediately. The fields=all read is still
    building, then normal. find_vms runs only after that, and the
    membership GET is after find_vms (#201). The write is include_tags,
    not a column name.
    """
    cloud = Cloud(created_status='normal', gets=['normal'])
    client = Client(cloud, statuses=['building', 'normal'])
    result = probe.create_partial(
        client, 'zz-partial-snap', 'zz-partial', timeout=30)

    assert cloud.created['include_tags'] == ['zz-partial']
    assert cloud.created['wait'] is True
    assert cloud.created['name'] == 'zz-partial-snap'
    assert 'partial_tags' not in cloud.created
    assert 'partial_tag_args' not in cloud.created
    # get() is the read wait=True already trusted. It must not be the
    # settle check, or find_vms runs while fields=all still says building.
    assert cloud.gets == []
    assert [call['path'] for call in client.calls] == [
        'cloud_snapshots/9', 'cloud_snapshots/9',
        'cloud_snapshot_actions', 'cloud_snapshot_vms']
    assert client.calls[0]['params'] == {'fields': 'all'}
    assert client.calls[1]['params'] == {'fields': 'all'}
    assert client.calls[2]['json_data'] == {
        'action': 'find_vms', 'cloud_snapshot': 9}
    assert client.calls[3]['params']['filter'] == (
        'cloud_snapshot eq 9 and is_snapshot eq false')
    assert result == {
        'key': 9,
        'status': 'normal',
        'members': ['zz-partial-in'],
    }


def test_create_retries_while_a_system_snapshot_is_building(monkeypatch):
    """A profile period can start a system snapshot before this create.

    create() raises ValidationError 'System snapshot is still building'
    until that finishes. Listed rows can already read normal, so the
    retry is on the error, not on a fields=all poll. A different
    validation error is not retried.
    """
    class Busy(Exception):
        pass

    cloud = Cloud()
    attempts = []

    def create(**kwargs):
        attempts.append(dict(kwargs))
        if len(attempts) < 3:
            raise Busy('System snapshot is still building')
        return Snap({'$key': 9, 'status': 'normal'})

    cloud.create = create
    client = Client(cloud, statuses=['normal'])
    monkeypatch.setattr(probe, '_validation_error_type', lambda: Busy)

    result = probe.create_partial(
        client, 'zz-partial-snap', 'zz-partial', timeout=30)

    assert len(attempts) == 3
    assert attempts[-1]['include_tags'] == ['zz-partial']
    assert attempts[-1]['wait'] is True
    assert result['key'] == 9
    assert result['status'] == 'normal'


def test_another_validation_error_is_not_retried(monkeypatch):
    class Busy(Exception):
        pass

    cloud = Cloud()
    attempts = []

    def create(**kwargs):
        attempts.append(1)
        raise Busy('name already used')

    cloud.create = create
    client = Client(cloud)
    monkeypatch.setattr(probe, '_validation_error_type', lambda: Busy)

    with pytest.raises(Busy, match='name already used'):
        probe.create_partial(
            client, 'zz-partial-snap', 'zz-partial', timeout=30)
    assert attempts == [1]


def test_a_create_error_that_is_not_validation_is_not_retried(monkeypatch):
    class Busy(Exception):
        pass

    cloud = Cloud()
    attempts = []

    def create(**kwargs):
        attempts.append(1)
        raise RuntimeError('connection reset')

    cloud.create = create
    client = Client(cloud)
    monkeypatch.setattr(probe, '_validation_error_type', lambda: Busy)

    with pytest.raises(RuntimeError, match='connection reset'):
        probe.create_partial(
            client, 'zz-partial-snap', 'zz-partial', timeout=30)
    assert attempts == [1]


def test_still_building_past_the_deadline_is_reported(monkeypatch):
    class Busy(Exception):
        pass

    cloud = Cloud()
    attempts = []

    def create(**kwargs):
        attempts.append(1)
        raise Busy('System snapshot is still building')

    cloud.create = create
    client = Client(cloud)
    monkeypatch.setattr(probe, '_validation_error_type', lambda: Busy)
    ticks = iter([0, 0, 400])
    monkeypatch.setattr(probe.time, 'time', lambda: next(ticks))

    with pytest.raises(TimeoutError, match='still building'):
        probe.create_partial(
            client, 'zz-partial-snap', 'zz-partial', timeout=5)
    assert len(attempts) == 2


def _install_request(client, handler):
    real = client._request

    def request(method, path, params=None, json_data=None):
        outcome = handler(method, path, params, json_data)
        if outcome is not None:
            return outcome
        return real(method, path, params=params, json_data=json_data)

    client._request = request


def test_find_vms_retries_while_the_system_snapshot_is_building(monkeypatch):
    """create() can be accepted while find_vms is still refused (#224)."""
    class Busy(Exception):
        pass

    client = Client()
    posts = []

    def handler(method, path, _params, json_data):
        if method == 'POST' and path == 'cloud_snapshot_actions':
            posts.append(dict(json_data))
            if len(posts) < 8:
                raise Busy('System snapshot is still building')
        return None

    _install_request(client, handler)
    monkeypatch.setattr(probe, '_validation_error_type', lambda: Busy)
    slept = []
    monkeypatch.setattr(probe.time, 'sleep', slept.append)
    monkeypatch.setattr(probe.time, 'time', lambda: 0)

    probe.find_vms(client, 9, timeout=30)

    assert len(posts) == 8
    assert posts[-1] == {'action': 'find_vms', 'cloud_snapshot': 9}
    assert slept == [2, 4, 8, 16, 30, 30, 30]


def test_find_vms_does_not_retry_a_different_validation_error(monkeypatch):
    class Busy(Exception):
        pass

    client = Client()
    posts = []

    def handler(_method, path, _params, _json_data):
        if path == 'cloud_snapshot_actions':
            posts.append(1)
            raise Busy('snapshot is locked')
        return None

    _install_request(client, handler)
    monkeypatch.setattr(probe, '_validation_error_type', lambda: Busy)

    with pytest.raises(Busy, match='locked'):
        probe.find_vms(client, 9, timeout=30)
    assert posts == [1]


def test_find_vms_does_not_retry_an_error_that_is_not_validation(monkeypatch):
    class Busy(Exception):
        pass

    client = Client()
    posts = []

    def handler(_method, _path, _params, _json_data):
        posts.append(1)
        raise RuntimeError('connection reset')

    _install_request(client, handler)
    monkeypatch.setattr(probe, '_validation_error_type', lambda: Busy)

    with pytest.raises(RuntimeError, match='connection reset'):
        probe.find_vms(client, 9, timeout=30)
    assert posts == [1]


def test_find_vms_still_building_past_the_deadline_is_reported(monkeypatch):
    class Busy(Exception):
        pass

    client = Client()
    posts = []

    def handler(_method, _path, _params, _json_data):
        posts.append(1)
        raise Busy('System snapshot is still building')

    _install_request(client, handler)
    monkeypatch.setattr(probe, '_validation_error_type', lambda: Busy)
    ticks = iter([0, 0, 400])
    monkeypatch.setattr(probe.time, 'time', lambda: next(ticks))

    with pytest.raises(TimeoutError, match='still building'):
        probe.find_vms(client, 9, timeout=5)
    assert len(posts) == 2


def test_create_retries_find_vms_then_waits_for_members(monkeypatch):
    """The POST and the membership read are separate waits (#224)."""
    class Busy(Exception):
        pass

    client = Client(statuses=['normal'])
    client._members = []
    posts = []
    reads = []

    def handler(method, path, _params, json_data):
        if method == 'POST' and path == 'cloud_snapshot_actions':
            posts.append(dict(json_data))
            if len(posts) < 2:
                raise Busy('System snapshot is still building')
        if path == 'cloud_snapshot_vms':
            reads.append(1)
            if len(reads) >= 3:
                client._members = [{
                    'name': 'zz-partial-in',
                    'is_snapshot': False,
                    'cloud_snapshot': 9,
                }]
        return None

    _install_request(client, handler)
    monkeypatch.setattr(probe, '_validation_error_type', lambda: Busy)

    result = probe.create_partial(
        client, 'zz-partial-snap', 'zz-partial', timeout=30)

    assert len(posts) == 2
    assert posts[0] == {'action': 'find_vms', 'cloud_snapshot': 9}
    assert len(reads) == 3
    assert result['members'] == ['zz-partial-in']
    assert result['key'] == 9
    assert result['status'] == 'normal'
    actions = [
        call['path'] for call in client.calls
        if call['path'] == 'cloud_snapshot_actions']
    assert actions == ['cloud_snapshot_actions']


def test_create_does_not_read_members_when_find_vms_is_refused(monkeypatch):
    class Busy(Exception):
        pass

    client = Client(statuses=['normal'])
    posts = []

    def handler(_method, path, _params, _json_data):
        if path == 'cloud_snapshot_actions':
            posts.append(1)
            raise Busy('snapshot is locked')
        return None

    _install_request(client, handler)
    monkeypatch.setattr(probe, '_validation_error_type', lambda: Busy)

    with pytest.raises(Busy, match='locked'):
        probe.create_partial(
            client, 'zz-partial-snap', 'zz-partial', timeout=30)
    assert posts == [1]
    assert all(call['path'] != 'cloud_snapshot_vms' for call in client.calls)


def test_membership_is_polled_until_a_row_exists():
    client = Client()
    client._members = []
    reads = []
    real = client._request

    def request(method, path, params=None, json_data=None):
        if path == 'cloud_snapshot_vms':
            reads.append(1)
            if len(reads) >= 3:
                client._members = [{
                    'name': 'zz-partial-in',
                    'is_snapshot': False,
                    'cloud_snapshot': 9,
                }]
        return real(method, path, params=params, json_data=json_data)

    client._request = request

    names = probe.wait_for_members(client, 9, timeout=30)

    assert names == ['zz-partial-in']
    assert len(reads) == 3
    assert [call['path'] for call in client.calls] == [
        'cloud_snapshot_vms', 'cloud_snapshot_vms', 'cloud_snapshot_vms']


def test_empty_membership_past_the_deadline_is_an_error(monkeypatch):
    client = Client()
    client._members = []
    ticks = iter([0, 0, 50])
    monkeypatch.setattr(probe.time, 'time', lambda: next(ticks))

    with pytest.raises(TimeoutError, match='membership still empty'):
        probe.wait_for_members(client, 9, timeout=5)
    assert [call['path'] for call in client.calls] == [
        'cloud_snapshot_vms', 'cloud_snapshot_vms']


def test_a_wait_timeout_still_polls_the_row_it_created(monkeypatch):
    class Slow(Exception):
        pass

    cloud = Cloud(gets=['building', 'normal'])

    def explode(**_kwargs):
        raise Slow('gave up')

    cloud.create = explode
    client = Client(cloud, statuses=['building', 'normal'])
    client._snapshots = [{'$key': 9, 'name': 'zz-partial-snap'}]
    monkeypatch.setattr(probe, '_wait_timeout_type', lambda: Slow)

    result = probe.create_partial(client, 'zz-partial-snap', 'zz-partial')

    assert result['key'] == 9
    assert result['status'] == 'normal'
    assert client.calls[0]['path'] == 'cloud_snapshots'
    assert client.calls[1]['path'] == 'cloud_snapshots/9'
    assert client.calls[1]['params'] == {'fields': 'all'}
    assert client.calls[3]['json_data']['action'] == 'find_vms'


def test_still_building_at_the_deadline_is_an_error():
    client = Client(statuses=['building', 'building', 'building'])
    ticks = iter([0, 0, 50])
    monkeypatch_time = probe.time.time
    probe.time.time = lambda: next(ticks)
    try:
        with pytest.raises(TimeoutError, match='still building'):
            probe.wait_until_settled(client, 9, timeout=5)
    finally:
        probe.time.time = monkeypatch_time
    assert [call['path'] for call in client.calls] == [
        'cloud_snapshots/9', 'cloud_snapshots/9']
    assert all(call['params'] == {'fields': 'all'} for call in client.calls)


def test_a_slow_status_read_is_retried_until_the_budget(monkeypatch):
    """One VergeTimeoutError is not the end of the wait.

    pyvergeos renames a slow GET to VergeTimeoutError, and that class is
    not the builtin TimeoutError. The fields=all poll has to keep going,
    because the snapshot it is waiting on may still be building.
    """
    class Slow(Exception):
        pass

    client = Client(statuses=['normal'])

    def fail_once():
        raise Slow('slow read')

    client._status_hook = fail_once
    monkeypatch.setattr(probe, '_wait_timeout_type', lambda: Slow)

    status = probe.wait_until_settled(client, 9, timeout=30)

    assert status == 'normal'
    assert [call['path'] for call in client.calls] == [
        'cloud_snapshots/9', 'cloud_snapshots/9']


def test_scrub_removes_password_and_token(monkeypatch):
    monkeypatch.setenv('VERGEOS_PASSWORD', 's3cret-value')
    monkeypatch.setenv('VERGEOS_TOKEN', 'tok-value')
    text = probe.scrub('denied for s3cret-value using tok-value')
    assert 's3cret-value' not in text
    assert 'tok-value' not in text
    assert '<VERGEOS_PASSWORD>' in text
    assert '<VERGEOS_TOKEN>' in text


def test_main_prints_one_scrubbed_line(monkeypatch, capsys):
    monkeypatch.setenv('VERGEOS_PASSWORD', 's3cret-value')

    def boom():
        raise RuntimeError('auth failed for s3cret-value')

    monkeypatch.setattr(probe, 'client', boom)
    rc = probe.main(['create', '--name', 'zz-partial-snap',
                     '--include-tag', 'zz-partial'])
    err = capsys.readouterr().err
    assert rc == 1
    assert 's3cret-value' not in err
    assert 'RuntimeError: auth failed for <VERGEOS_PASSWORD>' in err


def test_delete_removes_only_the_named_snapshot():
    client = Client()
    client._snapshots = [
        {'$key': 9, 'name': 'zz-partial-snap'},
        {'$key': 10, 'name': 'nightly'},
    ]
    result = probe.delete_named(client, 'zz-partial-snap')
    assert result == {'deleted': [9]}
    assert client.cloud_snapshots.deleted == [9]


def test_delete_of_a_missing_name_is_empty():
    client = Client()
    assert probe.delete_named(client, 'zz-partial-snap') == {'deleted': []}


class Period(dict):
    def __init__(self, data, kind, partial, quiesce):
        super(Period, self).__init__(data)
        self.snapshot_type = kind
        self.partial_tag_keys = partial
        self.quiesce_tag_keys = quiesce


class Profiles:
    def __init__(self, periods):
        self._periods = periods
        self.listed_fields = None
        self._rows = [{'$key': 4, 'name': 'protect-zz-partial'}]

    def list(self):
        return list(self._rows)

    def periods(self, key):
        assert key == 4
        parent = self

        class Manager:
            def list(self, fields=None):
                parent.listed_fields = fields
                return list(parent._periods)

        return Manager()


def test_show_period_reads_the_sdk_properties_and_the_projection():
    period = Period(
        {'$key': 8, 'name': 'Daily', 'type': 'partial_include'},
        'partial_include', [5], [5])
    profiles = Profiles([period])
    client = Client()
    client.snapshot_profiles = profiles

    result = probe.show_period(client, 'protect-zz-partial', 'Daily')

    assert result == {
        'profile_key': 4,
        'period_key': 8,
        'snapshot_type': 'partial_include',
        'partial_tag_keys': [5],
        'quiesce_tag_keys': [5],
    }
    assert profiles.listed_fields == [
        '$key', 'name', 'type',
        'partial_tags[refs[tag]]',
        'quiesce_tags[refs[tag]]',
    ]


def test_show_period_refuses_an_sdk_that_cannot_read_the_scope():
    profiles = Profiles([{' $key': 8, 'name': 'Daily'}])
    # A plain dict has no snapshot_type. That is a released pyVergeOS.
    profiles._periods = [{'$key': 8, 'name': 'Daily'}]
    client = Client()
    client.snapshot_profiles = profiles

    with pytest.raises(ValueError, match='200'):
        probe.show_period(client, 'protect-zz-partial', 'Daily')


def test_show_period_refuses_two_profiles_of_the_same_name():
    profiles = Profiles([])
    profiles._rows = [
        {'$key': 4, 'name': 'protect-zz-partial'},
        {'$key': 5, 'name': 'protect-zz-partial'},
    ]
    client = Client()
    client.snapshot_profiles = profiles

    with pytest.raises(ValueError, match='found 2'):
        probe.show_period(client, 'protect-zz-partial', 'Daily')
