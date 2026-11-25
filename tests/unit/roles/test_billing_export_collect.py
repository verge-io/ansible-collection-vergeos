"""collect() must not turn a failed tenant read into a zero invoice (#207).

An empty allocation is an empty list. NotFoundError means the tenant
disappeared between list() and the read. Anything else has to fail the
export: the old `except Exception: pass` wrote cores, RAM, and storage
as zero and exited 0.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from pyvergeos.exceptions import APIError, NotFoundError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'billing_export', 'files'))
import billing_export  # noqa: E402
from billing_export import GB, collect, main  # noqa: E402


def _client(tenants, nodes, storage, billing=None):
    client = MagicMock()
    client.billing.list.return_value = billing if billing is not None else []
    client.tenants.list.return_value = tenants
    client.tenants.nodes.side_effect = nodes
    client.tenants.storage.side_effect = storage
    return client


def _collect(client):
    with patch('pyvergeos.VergeClient', return_value=client):
        return collect()


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv('VERGEOS_HOST', 'https://vergeos.example.com')
    monkeypatch.setenv('VERGEOS_USERNAME', 'report')
    monkeypatch.setenv('VERGEOS_PASSWORD', 'secret')
    monkeypatch.delenv('VERGEOS_TOKEN', raising=False)


TENANTS = [
    {'$key': 1, 'name': 'lab-tenant', 'running': True},
    {'$key': 2, 'name': 'gone', 'running': True},
]


def _nodes(where, key):
    if where == 'nodes' and key == 2:
        raise NotFoundError('Tenant 2 not found')
    if key == 1:
        return [{'cpu_cores': 4, 'ram': 8192}, {'cpu_cores': 8, 'ram': 16384}]
    return [{'cpu_cores': 2, 'ram': 2048}]


def _storage(where, key):
    if where == 'storage' and key == 2:
        raise NotFoundError('Tenant 2 not found')
    if key == 1:
        return [{'tier_number': 1, 'provisioned': 100 * GB, 'used': 0}]
    return []


@pytest.mark.parametrize('where', ['nodes', 'storage'])
def test_not_found_drops_that_tenant_and_keeps_the_rest(auth_env, where):
    client = _client(
        TENANTS,
        nodes=lambda key: _nodes(where, key),
        storage=lambda key: _storage(where, key),
        billing=[{'$key': 7}],
    )
    billing, tenants, skipped = _collect(client)
    assert billing == [{'$key': 7}]
    assert skipped == 1
    assert [t['row']['name'] for t in tenants] == ['lab-tenant']
    assert tenants[0]['nodes'] == [
        {'cpu_cores': 4, 'ram': 8192}, {'cpu_cores': 8, 'ram': 16384}]
    assert tenants[0]['storage'] == [
        {'tier_number': 1, 'provisioned': 100 * GB, 'used': 0}]


def test_an_empty_allocation_is_a_row_not_a_skip(auth_env):
    """A stopped tenant with no storage returns [], it does not raise."""
    client = _client(
        [{'$key': 5, 'name': 'second-tenant', 'running': False}],
        nodes=lambda key: [],
        storage=lambda key: [],
    )
    _billing, tenants, skipped = _collect(client)
    assert skipped == 0
    assert len(tenants) == 1
    assert tenants[0]['row']['name'] == 'second-tenant'
    assert tenants[0]['nodes'] == []
    assert tenants[0]['storage'] == []


@pytest.mark.parametrize('where,exc', [
    ('nodes', APIError('Internal Server Error')),
    ('storage', APIError('Internal Server Error')),
    ('nodes', ConnectionError('timed out')),
    ('storage', ConnectionError('timed out')),
])
def test_a_real_failure_propagates(auth_env, where, exc):
    def nodes(key):
        if where == 'nodes':
            raise exc
        return [{'cpu_cores': 4, 'ram': 8192}]

    def storage(key):
        if where == 'storage':
            raise exc
        return [{'tier_number': 1, 'provisioned': GB, 'used': 0}]

    client = _client(
        [{'$key': 1, 'name': 'lab-tenant', 'running': True}],
        nodes=nodes,
        storage=storage,
    )
    with pytest.raises(type(exc), match='Internal Server Error|timed out'):
        _collect(client)


def test_the_summary_reports_tenants_skipped(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        billing_export, 'collect',
        lambda: ([{'$key': 1, 'from': 100, 'to': 200, 'created': 110}],
                 [{'row': {'name': 'kept', 'running': True},
                   'nodes': [], 'storage': []}],
                 2))
    dest = tmp_path / 'out'
    monkeypatch.setattr(
        sys, 'argv', ['billing_export.py', '--dest', str(dest)])
    assert main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['tenants_skipped'] == 2
    assert summary['tenant_rows'] == 1
    assert summary['system_rows'] == 1
    assert len(list(dest.glob('*.csv'))) == 2


def test_a_real_failure_writes_no_csv(tmp_path, monkeypatch):
    def boom():
        raise APIError('Internal Server Error')

    monkeypatch.setattr(billing_export, 'collect', boom)
    dest = tmp_path / 'out'
    monkeypatch.setattr(
        sys, 'argv', ['billing_export.py', '--dest', str(dest)])
    with pytest.raises(APIError, match='Internal Server Error'):
        main()
    assert not dest.exists()


def test_fixture_run_reports_zero_skipped(tmp_path, monkeypatch, capsys):
    fixture = tmp_path / 'state.json'
    fixture.write_text(json.dumps({'billing': [], 'tenants': []}))
    dest = tmp_path / 'out'
    monkeypatch.setattr(sys, 'argv', [
        'billing_export.py', '--dest', str(dest), '--fixture', str(fixture)])
    assert main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['tenants_skipped'] == 0
    assert summary['tenant_rows'] == 0
