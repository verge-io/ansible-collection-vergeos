#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for the physical_drive_info module.

The module had none. The triage helper underneath was well covered; the module
around it -- the projection it asks for and the node filter it applies -- was
not tested at all, and the node filter never worked.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import json
import os

import pytest
from unittest.mock import MagicMock, patch


from pyvergeos.exceptions import (  # real classes: a Mock here is
    APIError,                       # CALLED as a side_effect, not raised
    AuthenticationError,
    ValidationError,
    VergeConnectionError,
)


def captured_rows():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, '..', '..', '..', '..'))
    with open(os.path.join(root, 'tests', 'fixtures', 'api',
                           'physical_drives.json')) as handle:
        return json.load(handle)['rows']


def make_row(data):
    """A mock SDK resource that dict() converts and that exposes .key.

    MagicMock's auto-created keys() makes dict(mock) return {} -- see
    create_mock_resource in conftest (issue #66). Wire the mapping protocol
    the same way, and set .key from $key the way ResourceObject does.
    """
    obj = MagicMock()
    obj.keys.side_effect = lambda: list(data.keys())
    obj.__getitem__.side_effect = data.__getitem__
    obj.__iter__.side_effect = lambda: iter(data)
    for key, value in data.items():
        setattr(obj, key, value)
    if '$key' in data:
        obj.key = data['$key']
    return obj


def make_module(params):
    module = MagicMock()
    module.params = params
    module.check_mode = False
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def base_params(**overrides):
    params = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'node': None,
        'severity': 'all',
        'critical_flags': None,
        'warning_flags': None,
    }
    params.update(overrides)
    return params


def make_client(rows=None, nodes=None):
    """Two drives from the captured fixture, one per node.

    The captured rows carry no node -- there is no node column -- so the join
    the module asks for is added here, which is exactly what the live API does
    when the projection names it.

    ``nodes`` defaults to one entry per distinct node_name in ``rows``, so
    resolve_one can turn a node name into a key the way the live module does.
    """
    if rows is None:
        captured = captured_rows()
        rows = [dict(captured[0], node_name='node1'),
                dict(captured[1], node_name='node2')]
    if nodes is None:
        seen = []
        nodes = []
        for row in rows:
            name = row.get('node_name')
            if name and name not in seen:
                seen.append(name)
                nodes.append({'$key': len(seen), 'name': name})
    client = MagicMock()
    client.physical_drives.list.return_value = [make_row(r) for r in rows]
    client.nodes.list.return_value = [make_row(n) for n in nodes]
    return client


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'physical_drive_info.get_vergeos_client',
               return_value=mock_client), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'physical_drive_info.HAS_PYVERGEOS', True), \
         patch('ansible_collections.vergeio.vergeos.plugins.modules.'
               'physical_drive_info.AnsibleModule', return_value=mock_module):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            physical_drive_info as mod,
        )
        try:
            mod.main()
        except SystemExit:
            pass


class TestTheProjection:
    def test_the_node_join_is_asked_for_by_name(self):
        """There is no node column on machine_drive_phys. Without the join the
        module cannot say which machine to walk to, and on identical hardware
        nothing else in the row distinguishes two drives."""
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            physical_drive_info as mod,
        )
        assert mod.NODE_FIELD == 'parent_drive#machine#name as node_name'
        assert mod.NODE_FIELD in mod.DRIVE_FIELDS

    def test_every_triage_flag_is_fetched(self):
        """A flag that is read but not fetched is None for every drive, so
        every drive passes. In this module that is a failing drive reported as
        healthy."""
        from ansible_collections.vergeio.vergeos.plugins.module_utils import (
            physical_drives as pd,
        )
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            physical_drive_info as mod,
        )
        for flag in pd.CRITICAL_FLAGS + pd.WARNING_FLAGS + pd.INFO_FLAGS:
            assert flag in mod.DRIVE_FIELDS, (
                'triage reads %r but the projection does not fetch it' % flag)
        for raw in pd.RAW.values():
            assert raw in mod.DRIVE_FIELDS

    def test_the_projection_is_passed_to_the_sdk(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            physical_drive_info as mod,
        )
        client = make_client()

        run_main(make_module(base_params()), client)

        assert client.physical_drives.list.call_args[1]['fields'] == \
            mod.DRIVE_FIELDS


class TestTheNodeFilter:
    def test_filtering_by_node_scopes_through_the_sdk(self):
        """node: must construct PhysicalDriveManager(client, node_key=...),
        not filter the unscoped fleet client-side. client.physical_drives is
        unscoped only; the pyVergeOS#143 fix lives on the constructor."""
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            physical_drive_info as mod,
        )
        client = make_client()
        scoped = MagicMock()
        scoped.list.return_value = [
            make_row(dict(captured_rows()[1], node_name='node2')),
        ]

        with patch.object(mod, 'PhysicalDriveManager', return_value=scoped) as ctor:
            module = make_module(base_params(node='node2'))
            run_main(module, client)

        ctor.assert_called_once()
        assert ctor.call_args[0][0] is client
        assert ctor.call_args[1]['node_key'] == 2
        scoped.list.assert_called_once_with(fields=mod.DRIVE_FIELDS)
        client.physical_drives.list.assert_not_called()

        drives = module.exit_json.call_args[1]['drives']
        assert len(drives) == 1
        assert drives[0]['node_name'] == 'node2'

    def test_a_node_filter_used_to_match_nothing_at_all(self):
        """The old bug, pinned. `location` is the slot ("nvme0"), so no node
        name is ever a substring of it -- matching on location returned an
        empty list on every system, and "0 drives, none failing" read as good
        news. Scoping now goes through the SDK by node key."""
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            physical_drive_info as mod,
        )
        client = make_client()
        scoped = MagicMock()
        scoped.list.return_value = [
            make_row(dict(captured_rows()[0], node_name='node1')),
        ]

        with patch.object(mod, 'PhysicalDriveManager', return_value=scoped):
            module = make_module(base_params(node='node1'))
            run_main(module, client)

        drives = module.exit_json.call_args[1]['drives']
        assert drives, 'the node filter matched nothing'
        assert all('node1' not in str(d.get('location')) for d in drives), (
            'this test is only meaningful while location does NOT contain '
            'the node name')

    def test_an_unknown_node_fails_instead_of_reporting_silence(self):
        """A typo must not read as "0 drives, none failing"."""
        client = make_client()
        module = make_module(base_params(node='node9'))
        run_main(module, client)

        module.fail_json.assert_called_once()
        msg = module.fail_json.call_args[1]['msg']
        assert 'node9' in msg
        assert 'Unexpected error' not in msg
        client.physical_drives.list.assert_not_called()

    def test_no_node_filter_reports_every_drive(self):
        client = make_client()

        module = make_module(base_params())
        run_main(module, client)

        assert len(module.exit_json.call_args[1]['drives']) == 2
        client.physical_drives.list.assert_called_once()


class TestSeverityFloor:
    def _client_with(self, **flags):
        captured = captured_rows()
        return make_client([dict(captured[0], node_name='node1', **flags),
                            dict(captured[1], node_name='node2')])

    def test_all_reports_healthy_drives_too(self):
        module = make_module(base_params(severity='all'))
        run_main(module, self._client_with())
        assert len(module.exit_json.call_args[1]['drives']) == 2

    def test_critical_reports_only_failing_drives(self):
        module = make_module(base_params(severity='critical'))
        run_main(module, self._client_with(realloc_sectors_warn=True))

        result = module.exit_json.call_args[1]
        assert len(result['drives']) == 1
        assert result['drives'][0]['severity'] == 'critical'
        assert result['counts']['critical'] == 1
        assert result['counts']['ok'] == 1

    def test_warning_floor_includes_critical(self):
        """`severity:` means "this and above"; a floor that excluded worse
        news than you asked for would be a trap."""
        module = make_module(base_params(severity='warning'))
        run_main(module, self._client_with(realloc_sectors_warn=True))

        assert len(module.exit_json.call_args[1]['drives']) == 1

    def test_vsan_errors_outrank_a_clean_smart_report(self):
        module = make_module(base_params(severity='critical'))
        run_main(module, self._client_with(vsan_read_errors=3))

        drives = module.exit_json.call_args[1]['drives']
        assert len(drives) == 1
        assert 'vSAN IO errors' in drives[0]['reasons'][0]

    def test_repairing_is_reported_separately_from_severity(self):
        """A rebuilding drive is often perfectly healthy -- it may have just
        been replaced. It is reported because pulling a second drive
        mid-rebuild is how a rebuild becomes a data-loss event."""
        module = make_module(base_params())
        run_main(module, self._client_with(vsan_repairing=1))

        result = module.exit_json.call_args[1]
        assert len(result['repairing']) == 1
        assert result['counts']['ok'] == 2


class TestSdkErrorsAreHandled:
    @pytest.mark.parametrize('exc', [
        APIError, AuthenticationError, ValidationError, VergeConnectionError,
    ])
    def test_sdk_errors_do_not_fall_through_to_unexpected(self, exc):
        client = make_client()
        client.physical_drives.list.side_effect = exc('server said no')
        module = make_module(base_params())

        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'Unexpected error' not in module.fail_json.call_args[1]['msg']
