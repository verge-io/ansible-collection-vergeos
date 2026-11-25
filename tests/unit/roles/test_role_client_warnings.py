"""Role scripts build a VergeClient and leave TLS warnings to the SDK.

billing_export, health_scan, protect_ctl, the rebalance advisor, and
tier_audit take verify_ssl from VERGEOS_INSECURE. pyvergeos filters
InsecureRequestWarning when that is false, and the filter is that one
category. These scripts do not import urllib3, which is not declared in
requirements.txt, and they do not call disable_warnings().
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import urllib3

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

# (module, builder, path under the collection root)
_SCRIPTS = (
    ('billing_export', 'collect',
     'roles/billing_export/files/billing_export.py'),
    ('health_scan', 'collect',
     'roles/health_report/files/health_scan.py'),
    ('protect_ctl', 'client',
     'roles/protect/files/protect_ctl.py'),
    ('advisor', 'client',
     'roles/rebalance_advisor/files/advisor.py'),
    ('tier_audit', 'client',
     'roles/tier_policy/files/tier_audit.py'),
)

for _mod, _fn, _rel in _SCRIPTS:
    sys.path.insert(0, os.path.join(_ROOT, os.path.dirname(_rel)))

import advisor  # noqa: E402
import billing_export  # noqa: E402
import health_scan  # noqa: E402
import protect_ctl  # noqa: E402
import tier_audit  # noqa: E402

_MODULES = {
    'advisor': advisor,
    'billing_export': billing_export,
    'health_scan': health_scan,
    'protect_ctl': protect_ctl,
    'tier_audit': tier_audit,
}


def _quiet_client():
    """A client whose list() calls return no rows."""
    client = MagicMock()
    client.billing.list.return_value = []
    client.tenants.list.return_value = []
    for attr in ('alarms', 'nodes', 'storage_tiers', 'cloud_snapshots',
                 'nas_services'):
        getattr(client, attr).list.return_value = []
    return client


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv('VERGEOS_HOST', 'https://vergeos.example.com')
    monkeypatch.setenv('VERGEOS_USERNAME', 'report')
    monkeypatch.setenv('VERGEOS_PASSWORD', 'secret')
    monkeypatch.delenv('VERGEOS_TOKEN', raising=False)


def test_role_scripts_do_not_import_urllib3():
    for _mod, _fn, rel in _SCRIPTS:
        path = os.path.join(_ROOT, rel)
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        assert 'urllib3' not in text, rel
        assert 'disable_warnings' not in text, rel


@pytest.mark.parametrize('insecure,verify_ssl', [
    (None, True),
    ('', True),
    ('false', True),
    ('0', True),
    ('1', False),
    ('true', False),
    ('True', False),
])
@pytest.mark.parametrize('mod_name,fn_name,rel', _SCRIPTS)
def test_builders_pass_verify_ssl_and_skip_disable_warnings(
        auth_env, monkeypatch, mod_name, fn_name, rel, insecure, verify_ssl):
    if insecure is None:
        monkeypatch.delenv('VERGEOS_INSECURE', raising=False)
    else:
        monkeypatch.setenv('VERGEOS_INSECURE', insecure)

    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(urllib3, 'disable_warnings', _spy)
    with patch('pyvergeos.VergeClient', return_value=_quiet_client()) as ctor:
        getattr(_MODULES[mod_name], fn_name)()
    assert calls == [], rel
    assert ctor.call_args.kwargs['verify_ssl'] is verify_ssl, rel
    assert ctor.call_args.kwargs['host'] == 'https://vergeos.example.com'
    assert ctor.call_args.kwargs['username'] == 'report'
    assert ctor.call_args.kwargs['password'] == 'secret'
    assert 'token' not in ctor.call_args.kwargs


@pytest.mark.parametrize('mod_name,fn_name,rel', _SCRIPTS)
def test_token_auth_is_passed_through(monkeypatch, mod_name, fn_name, rel):
    monkeypatch.setenv('VERGEOS_HOST', 'https://vergeos.example.com')
    monkeypatch.setenv('VERGEOS_TOKEN', 'tok')
    monkeypatch.setenv('VERGEOS_INSECURE', 'true')
    monkeypatch.delenv('VERGEOS_USERNAME', raising=False)
    monkeypatch.delenv('VERGEOS_PASSWORD', raising=False)
    with patch('pyvergeos.VergeClient', return_value=_quiet_client()) as ctor:
        getattr(_MODULES[mod_name], fn_name)()
    assert ctor.call_args.kwargs == {
        'host': 'https://vergeos.example.com',
        'verify_ssl': False,
        'token': 'tok',
    }, rel
