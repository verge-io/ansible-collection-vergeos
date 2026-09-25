#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""#125: cloud_init and windows_unattend must converge.

Both modules reported changed=true on every state=present run, including an
identical re-apply and check mode, because they never compared stored state
to the requested state. They PUT the datasource and rewrote every file.

The file row is the wrong place to look. GET cloudinit_files/<key> does not
return contents, with fields=all, fields=contents or fields=most. The rows
below carry a `contents` value that is deliberately NOT the stored document,
so a comparison against the row passes for the wrong reason. The stored
document is what get_content() returns.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from unittest.mock import MagicMock, patch

from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    cloudinit_file_needs_update,
)
from ansible_collections.vergeio.vergeos.plugins.modules import cloud_init as ci_mod
from ansible_collections.vergeio.vergeos.plugins.modules import windows_unattend as wu_mod


USER_DATA = "#cloud-config\nmanage_etc_hosts: true\nhostname: web01\n"
META_DATA = "instance-id: web01-001\nlocal-hostname: web01\n"
NET_CONFIG = "version: 2\nethernets:\n  eth0:\n    dhcp4: true\n"
UNATTEND = "<?xml version=\"1.0\"?><unattend><ComputerName>ZZ</ComputerName></unattend>\n"


def as_row(row):
    """dict() prefers the mapping protocol and MagicMock auto-provides keys(),
    so an __iter__ override decodes as {}. keys() + __getitem__ is what works.
    """
    mock = MagicMock()
    mock.keys = row.keys
    mock.__getitem__ = lambda self, key: row[key]
    return mock


def _puts(client):
    return [call for call in client._request.call_args_list
            if call.args and call.args[0] == 'PUT']


def _run_cloud_init(params, vm_row, files, stored, check_mode=False):
    """stored: {file_key: document get_content returns}."""
    client = MagicMock()
    module = MagicMock()
    module.check_mode = check_mode
    module.params = {
        'vm_name': 'zz-vm',
        'vm_id': None,
        'datasource': 'nocloud',
        'user_data': None,
        'meta_data': None,
        'network_config': None,
        'hostname': None,
        'network': None,
        'state': 'present',
    }
    module.params.update(params)
    module.exit_json.side_effect = SystemExit

    client.cloudinit_files.list_for_vm.return_value = [as_row(dict(row)) for row in files]
    client.cloudinit_files.get_content.side_effect = (
        lambda key, **kwargs: stored[int(key)])
    client.cloudinit_files.create.return_value = as_row({'$key': 501})

    with patch.object(ci_mod, 'get_vm', return_value=as_row(dict(vm_row))):
        try:
            ci_mod.configure_cloudinit(client, module)
        except SystemExit:
            pass
    return client, module.exit_json.call_args[1]


def _vm(datasource='nocloud'):
    return {'$key': 7, 'name': 'zz-vm', 'cloudinit_datasource': datasource}


def _file(key, name, render='no', contents='NOT THE STORED DOCUMENT'):
    """contents on the row is a decoy. The platform does not return it."""
    return {'$key': key, 'name': name, 'render': render, 'contents': contents}


def test_get_content_bytes_compare_equal_to_the_text_that_would_be_written():
    """get_content() can return bytes. The same document is not a change."""
    client = MagicMock()
    client.cloudinit_files.get_content.return_value = USER_DATA.encode('utf-8')
    assert cloudinit_file_needs_update(client, '11', USER_DATA, 'no') is False
    client.cloudinit_files.get_content.assert_called_once_with(11)


def test_get_content_bytes_that_differ_are_a_write():
    client = MagicMock()
    client.cloudinit_files.get_content.return_value = b'other\n'
    assert cloudinit_file_needs_update(client, 11, USER_DATA, 'no') is True


class TestCloudInitConvergence:
    def test_identical_reapply_does_not_write(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA, 'meta_data': META_DATA},
            _vm('nocloud'),
            [_file(11, '/user-data'), _file(12, '/meta-data')],
            {11: USER_DATA, 12: META_DATA},
        )
        assert result['changed'] is False
        assert _puts(client) == []
        client.cloudinit_files.update.assert_not_called()
        client.cloudinit_files.create.assert_not_called()
        assert client.cloudinit_files.get_content.call_count == 2
        names = [item['name'] for item in result['cloudinit_files']]
        assert names == ['/user-data', '/meta-data']

    def test_check_mode_identical_reapply_does_not_report_changed(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA},
            _vm('nocloud'),
            [_file(11, '/user-data')],
            {11: USER_DATA},
            check_mode=True,
        )
        assert result['changed'] is False
        assert _puts(client) == []
        client.cloudinit_files.update.assert_not_called()
        client.cloudinit_files.create.assert_not_called()

    def test_row_contents_are_not_the_comparison(self):
        """The decoy on the row matches the request. The stored document does
        not. A fix that compared the row would report changed=false and leave
        the real document in place."""
        client, result = _run_cloud_init(
            {'user_data': USER_DATA},
            _vm('nocloud'),
            [_file(11, '/user-data', contents=USER_DATA)],
            {11: USER_DATA + 'packages: [htop]\n'},
        )
        assert result['changed'] is True
        client.cloudinit_files.update.assert_called_once_with(
            key=11, contents=USER_DATA, render='No')
        assert _puts(client) == []

    def test_matching_get_content_ignores_a_different_row_contents(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA},
            _vm('nocloud'),
            [_file(11, '/user-data', contents='')],
            {11: USER_DATA},
        )
        assert result['changed'] is False
        client.cloudinit_files.update.assert_not_called()

    def test_render_no_matches_the_friendly_name_the_module_sends(self):
        """The list row stores 'no'. The module sends 'No'. Those are the
        same setting; a literal compare would rewrite the file forever."""
        for stored_render in ('no', 'No', 'NO'):
            client, result = _run_cloud_init(
                {'user_data': USER_DATA},
                _vm('nocloud'),
                [_file(11, '/user-data', render=stored_render)],
                {11: USER_DATA},
            )
            assert result['changed'] is False, stored_render
            client.cloudinit_files.update.assert_not_called()

    def test_a_different_render_is_a_write_even_when_contents_match(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA},
            _vm('nocloud'),
            [_file(11, '/user-data', render='variables')],
            {11: USER_DATA},
        )
        assert result['changed'] is True
        client.cloudinit_files.update.assert_called_once_with(
            key=11, contents=USER_DATA, render='No')

    def test_only_the_file_that_differs_is_written(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA, 'meta_data': META_DATA,
             'network_config': NET_CONFIG},
            _vm('nocloud'),
            [_file(11, '/user-data'), _file(12, '/meta-data'),
             _file(13, '/network-config')],
            {11: USER_DATA, 12: 'instance-id: other\n', 13: NET_CONFIG},
        )
        assert result['changed'] is True
        client.cloudinit_files.update.assert_called_once_with(
            key=12, contents=META_DATA, render='No')
        client.cloudinit_files.create.assert_not_called()
        assert _puts(client) == []

    def test_a_missing_file_is_created_and_the_match_is_left_alone(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA, 'meta_data': META_DATA},
            _vm('nocloud'),
            [_file(12, '/meta-data')],
            {12: META_DATA},
        )
        assert result['changed'] is True
        client.cloudinit_files.create.assert_called_once_with(
            vm_key=7, name='/user-data', contents=USER_DATA, render='No')
        client.cloudinit_files.update.assert_not_called()
        client.cloudinit_files.get_content.assert_called_once_with(12)

    def test_check_mode_reports_a_missing_file_without_creating_it(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA},
            _vm('nocloud'),
            [],
            {},
            check_mode=True,
        )
        assert result['changed'] is True
        client.cloudinit_files.create.assert_not_called()
        assert _puts(client) == []

    def test_check_mode_reports_a_content_change_without_writing(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA},
            _vm('nocloud'),
            [_file(11, '/user-data')],
            {11: 'other\n'},
            check_mode=True,
        )
        assert result['changed'] is True
        client.cloudinit_files.update.assert_not_called()
        assert _puts(client) == []
        client.cloudinit_files.get_content.assert_called_once_with(11)

    def test_datasource_is_put_only_when_it_differs(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA, 'datasource': 'nocloud'},
            _vm('none'),
            [_file(11, '/user-data')],
            {11: USER_DATA},
        )
        assert result['changed'] is True
        assert len(_puts(client)) == 1
        assert _puts(client)[0].args[1] == 'vms/7'
        assert _puts(client)[0].kwargs['json_data'] == {
            'cloudinit_datasource': 'nocloud'}
        client.cloudinit_files.update.assert_not_called()

    def test_check_mode_reports_a_datasource_change_without_putting(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA},
            _vm('none'),
            [_file(11, '/user-data')],
            {11: USER_DATA},
            check_mode=True,
        )
        assert result['changed'] is True
        assert _puts(client) == []
        client.cloudinit_files.update.assert_not_called()

    def test_datasource_none_converges_when_already_none(self):
        """state=present with datasource=none stages files without turning
        cloud-init on. Repeating it must not PUT 'none' again."""
        client, result = _run_cloud_init(
            {'user_data': USER_DATA, 'datasource': 'none'},
            _vm('none'),
            [_file(11, '/user-data')],
            {11: USER_DATA},
        )
        assert result['changed'] is False
        assert _puts(client) == []
        assert result['datasource'] == 'none'

    def test_datasource_compare_is_case_insensitive(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA, 'datasource': 'nocloud'},
            _vm('NoCloud'),
            [_file(11, '/user-data')],
            {11: USER_DATA},
        )
        assert result['changed'] is False
        assert _puts(client) == []

    def test_hostname_gap_fill_is_compared_as_the_generated_document(self):
        user = ci_mod.generate_user_data('web01')
        meta = ci_mod.generate_meta_data('web01')
        client, result = _run_cloud_init(
            {'hostname': 'web01'},
            _vm('nocloud'),
            [_file(11, '/user-data'), _file(12, '/meta-data')],
            {11: user, 12: meta},
        )
        assert result['changed'] is False
        client.cloudinit_files.update.assert_not_called()
        assert _puts(client) == []

    def test_a_one_character_difference_is_still_a_write(self):
        client, result = _run_cloud_init(
            {'user_data': USER_DATA},
            _vm('nocloud'),
            [_file(11, '/user-data')],
            {11: USER_DATA[:-1]},
        )
        assert result['changed'] is True
        client.cloudinit_files.update.assert_called_once()


def _run_unattend(params, vm_row, files, stored, check_mode=False):
    client = MagicMock()
    module = MagicMock()
    module.check_mode = check_mode
    module.params = {
        'vm_name': 'zz-win',
        'vm_id': None,
        'unattend_xml': UNATTEND,
        'state': 'present',
    }
    module.params.update(params)
    module.exit_json.side_effect = SystemExit

    objs = [as_row(dict(row)) for row in files]
    client.cloudinit_files.list_for_vm.return_value = objs
    client.cloudinit_files.get_content.side_effect = (
        lambda key, **kwargs: stored[int(key)])
    client.cloudinit_files.create.return_value = as_row({'$key': 77})

    with patch.object(wu_mod, 'get_vm', return_value=as_row(dict(vm_row))):
        try:
            wu_mod.configure_unattend(client, module)
        except SystemExit:
            pass
    return client, module.exit_json.call_args[1], objs


class TestWindowsUnattendConvergence:
    def test_identical_reapply_does_not_write(self):
        client, result, _objs = _run_unattend(
            {},
            _vm('nocloud'),
            [_file(9, '/unattend.xml')],
            {9: UNATTEND},
        )
        assert result['changed'] is False
        assert _puts(client) == []
        client.cloudinit_files.update.assert_not_called()
        client.cloudinit_files.create.assert_not_called()
        client.cloudinit_files.get_content.assert_called_once_with(9)
        assert result['unattend_file'] == {'key': '9', 'name': '/unattend.xml'}

    def test_check_mode_identical_reapply_does_not_report_changed(self):
        client, result, _objs = _run_unattend(
            {},
            _vm('nocloud'),
            [_file(9, '/unattend.xml')],
            {9: UNATTEND},
            check_mode=True,
        )
        assert result['changed'] is False
        assert _puts(client) == []
        client.cloudinit_files.update.assert_not_called()

    def test_stored_document_wins_over_the_row(self):
        client, result, _objs = _run_unattend(
            {},
            _vm('nocloud'),
            [_file(9, '/unattend.xml', contents=UNATTEND)],
            {9: UNATTEND.replace('ZZ', 'OTHER')},
        )
        assert result['changed'] is True
        client.cloudinit_files.update.assert_called_once_with(
            key=9, contents=UNATTEND, render='No')
        assert _puts(client) == []

    def test_a_missing_file_is_created(self):
        client, result, _objs = _run_unattend({}, _vm('nocloud'), [], {})
        assert result['changed'] is True
        client.cloudinit_files.create.assert_called_once_with(
            vm_key=7, name='/unattend.xml', contents=UNATTEND, render='No')
        client.cloudinit_files.get_content.assert_not_called()

    def test_check_mode_reports_a_missing_file_without_creating_it(self):
        client, result, _objs = _run_unattend(
            {}, _vm('nocloud'), [], {}, check_mode=True)
        assert result['changed'] is True
        client.cloudinit_files.create.assert_not_called()
        assert _puts(client) == []

    def test_check_mode_reports_a_content_change_without_writing(self):
        client, result, _objs = _run_unattend(
            {},
            _vm('nocloud'),
            [_file(9, '/unattend.xml')],
            {9: '<unattend/>'},
            check_mode=True,
        )
        assert result['changed'] is True
        client.cloudinit_files.update.assert_not_called()
        assert _puts(client) == []

    def test_datasource_is_put_only_when_it_is_not_nocloud(self):
        client, result, _objs = _run_unattend(
            {},
            _vm('none'),
            [_file(9, '/unattend.xml')],
            {9: UNATTEND},
        )
        assert result['changed'] is True
        assert len(_puts(client)) == 1
        assert _puts(client)[0].kwargs['json_data'] == {
            'cloudinit_datasource': 'nocloud'}
        client.cloudinit_files.update.assert_not_called()

    def test_check_mode_reports_a_datasource_change_without_putting(self):
        client, result, _objs = _run_unattend(
            {},
            _vm('none'),
            [_file(9, '/unattend.xml')],
            {9: UNATTEND},
            check_mode=True,
        )
        assert result['changed'] is True
        assert _puts(client) == []
        client.cloudinit_files.update.assert_not_called()

    def test_render_difference_is_a_write(self):
        client, result, _objs = _run_unattend(
            {},
            _vm('nocloud'),
            [_file(9, '/unattend.xml', render='jinja2')],
            {9: UNATTEND},
        )
        assert result['changed'] is True
        client.cloudinit_files.update.assert_called_once()

    def test_absent_removes_an_existing_file(self):
        client, result, objs = _run_unattend(
            {'state': 'absent', 'unattend_xml': None},
            _vm('nocloud'),
            [_file(9, '/unattend.xml')],
            {9: UNATTEND},
        )
        assert result['changed'] is True
        objs[0].delete.assert_called_once()
        client.cloudinit_files.get_content.assert_not_called()
        assert _puts(client) == []

    def test_absent_is_unchanged_when_the_file_is_already_gone(self):
        client, result, _objs = _run_unattend(
            {'state': 'absent', 'unattend_xml': None},
            _vm('nocloud'),
            [],
            {},
        )
        assert result['changed'] is False
        client.cloudinit_files.get_content.assert_not_called()

    def test_check_mode_absent_reports_the_delete_without_doing_it(self):
        client, result, objs = _run_unattend(
            {'state': 'absent', 'unattend_xml': None},
            _vm('nocloud'),
            [_file(9, '/unattend.xml')],
            {9: UNATTEND},
            check_mode=True,
        )
        assert result['changed'] is True
        objs[0].delete.assert_not_called()
