"""Unit tests for the permission module."""

import pytest
from unittest.mock import MagicMock, patch

from ansible_collections.vergeio.vergeos.plugins.modules import permission


class Row(dict):
    def __init__(self, row, **props):
        super().__init__(row)
        for name, value in props.items():
            setattr(self, name, value)


class FakePermissions:
    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.granted = []
        self.revoked = []

    def list(self, **kwargs):
        return [dict(r) for r in self.rows]

    def grant(self, table, **kwargs):
        self.granted.append(dict(kwargs, table=table))
        row = {'$key': 99, 'table': table, 'row': kwargs.get('row_key', 0)}
        for name in ('list', 'read', 'create', 'modify', 'delete'):
            row[name] = kwargs.get('can_%s' % name, False)
        return row

    def revoke(self, key):
        self.revoked.append(key)
        self.rows = [r for r in self.rows if r['$key'] != key]


class FakeClient:
    def __init__(self, users=(), groups=(), permissions=()):
        self.users = MagicMock()
        self.users.list.return_value = list(users)
        self.groups = MagicMock()
        self.groups.list.return_value = list(groups)
        self.permissions = FakePermissions(permissions)


ALICE = Row({'name': 'alice'}, identity=7)
OPS = Row({'name': 'ops'}, identity=9)


def run(client, check_mode=False, **over):
    base = {'host': 'h', 'username': 'u', 'password': 'p', 'insecure': False,
            'user': None, 'group': 'ops', 'table': 'vms', 'row': 0,
            'state': 'present', 'rights': ['list'], 'full_control': False}
    base.update(over)
    module = MagicMock()
    module.params = base
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)
    with patch.object(permission, 'AnsibleModule', return_value=module), \
         patch.object(permission, 'get_vergeos_client', return_value=client):
        with pytest.raises(SystemExit):
            permission.main()
    return module


def exited(module):
    assert module.exit_json.called, (
        "expected exit_json, got fail_json: %s" % (module.fail_json.call_args,))
    return module.exit_json.call_args[1]


def failed(module):
    assert module.fail_json.called, (
        "expected fail_json, got exit_json: %s" % (module.exit_json.call_args,))
    return module.fail_json.call_args[1]


def client(perms=()):
    return FakeClient(users=[ALICE], groups=[OPS], permissions=perms)


EXISTING = {'$key': 1, 'table': 'vms', 'row': 0, 'list': 1, 'read': 1,
            'create': 0, 'modify': 0, 'delete': 0}


# ── grant ────────────────────────────────────────────────────────────────────

def test_grants_when_absent():
    c = client()
    result = exited(run(c, rights=['list', 'read']))
    assert result['changed'] is True
    assert c.permissions.granted[0]['can_read'] is True
    assert c.permissions.granted[0]['can_delete'] is False


def test_the_identity_is_reported_in_human_terms():
    assert exited(run(client()))['identity'] == "group 'ops'"


def test_grants_to_a_user_as_well_as_a_group():
    c = client()
    result = exited(run(c, group=None, user='alice'))
    assert result['identity'] == "user 'alice'"
    assert c.permissions.granted[0]['identity_key'] == 7


def test_full_control_grants_everything():
    c = client()
    exited(run(c, full_control=True, rights=['list']))
    granted = c.permissions.granted[0]
    assert all(granted['can_%s' % r] for r in
               ('list', 'read', 'create', 'modify', 'delete'))


def test_rights_not_named_are_denied():
    """The list is the whole grant, not an addition to it."""
    c = client()
    exited(run(c, rights=['read']))
    assert c.permissions.granted[0]['can_list'] is False


def test_a_row_scoped_grant_passes_the_row():
    c = client()
    exited(run(c, row=42, rights=['read']))
    assert c.permissions.granted[0]['row_key'] == 42


def test_check_mode_grants_nothing():
    c = client()
    result = exited(run(c, check_mode=True))
    assert result['changed'] is True
    assert c.permissions.granted == []


def test_an_unknown_principal_fails():
    c = FakeClient(users=[], groups=[])
    assert "no group named" in failed(run(c))['msg']


# ── reconcile ────────────────────────────────────────────────────────────────

def test_no_change_when_rights_already_match():
    c = client([EXISTING])
    result = exited(run(c, rights=['list', 'read']))
    assert result['changed'] is False
    assert result['changed_rights'] == []
    assert c.permissions.granted == [] and c.permissions.revoked == []


def test_drift_is_corrected_and_named():
    c = client([EXISTING])
    result = exited(run(c, rights=['list', 'read', 'modify']))
    assert result['changed'] is True
    assert result['changed_rights'] == ['modify']


def test_correcting_drift_revokes_then_regrants():
    """grant() is the only path the SDK exposes for setting rights, and a
    permission is identified by (identity, table, row) -- so re-granting the
    same triple replaces rather than duplicating."""
    c = client([EXISTING])
    exited(run(c, rights=['list', 'read', 'modify']))
    assert c.permissions.revoked == [1]
    assert len(c.permissions.granted) == 1


def test_removing_a_right_is_also_drift():
    c = client([EXISTING])
    result = exited(run(c, rights=['list']))
    assert result['changed_rights'] == ['read']
    assert c.permissions.granted[0]['can_read'] is False


def test_check_mode_reports_drift_without_writing():
    c = client([EXISTING])
    result = exited(run(c, check_mode=True, rights=['list', 'read', 'delete']))
    assert result['changed'] is True
    assert c.permissions.revoked == [] and c.permissions.granted == []


def test_a_row_level_grant_does_not_satisfy_a_table_level_request():
    c = client([dict(EXISTING, row=42)])
    result = exited(run(c, row=0, rights=['list', 'read']))
    assert result['changed'] is True
    assert c.permissions.granted[0]['row_key'] == 0
    # The row-42 grant was left alone.
    assert c.permissions.revoked == []


# ── revoke ───────────────────────────────────────────────────────────────────

def test_absent_revokes_the_grant():
    c = client([EXISTING])
    result = exited(run(c, state='absent'))
    assert result['changed'] is True
    assert c.permissions.revoked == [1]


def test_absent_is_a_no_op_when_there_is_no_grant():
    c = client()
    result = exited(run(c, state='absent'))
    assert result['changed'] is False
    assert c.permissions.revoked == []


def test_absent_in_check_mode_revokes_nothing():
    c = client([EXISTING])
    exited(run(c, check_mode=True, state='absent'))
    assert c.permissions.revoked == []


def test_absent_only_revokes_the_matching_scope():
    c = client([dict(EXISTING, row=42)])
    result = exited(run(c, row=0, state='absent'))
    assert result['changed'] is False
    assert c.permissions.revoked == []
