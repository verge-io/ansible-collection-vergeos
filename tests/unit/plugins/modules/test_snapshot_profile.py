#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Unit tests for snapshot_profile module"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import pytest
from unittest.mock import MagicMock, patch


from pyvergeos.exceptions import (  # real classes: a Mock here is
    APIError,                       # CALLED as a side_effect, not raised
    AuthenticationError,
    ValidationError,
    VergeConnectionError,
)


def make_row(data):
    obj = MagicMock()
    obj.keys.return_value = list(data.keys())
    obj.__getitem__.side_effect = lambda k: data[k]
    return obj


def make_module(params, check_mode=False):
    module = MagicMock()
    module.params = params
    module.check_mode = check_mode
    module.exit_json.side_effect = SystemExit
    module.fail_json.side_effect = SystemExit
    return module


def default_period(**overrides):
    period = {
        'name': 'Daily',
        'frequency': 'daily',
        'retention': 604800,
        'minute': 0,
        'hour': 2,
        'day_of_week': 'any',
        'day_of_month': 0,
        'month': 0,
        'max_tier': 1,
        'quiesce': False,
        'min_snapshots': 1,
        'immutable': False,
    }
    period.update(overrides)
    return period


def base_params(**overrides):
    params = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False,
        'name': 'vm-backups',
        'state': 'present',
        'description': None,
        'periods': None,
        'purge_periods': False,
    }
    params.update(overrides)
    return params


def run_main(mock_module, mock_client):
    with patch('ansible_collections.vergeio.vergeos.plugins.modules.snapshot_profile.get_vergeos_client',
               return_value=mock_client):
        with patch('ansible_collections.vergeio.vergeos.plugins.modules.snapshot_profile.HAS_PYVERGEOS', True):
            with patch('ansible_collections.vergeio.vergeos.plugins.modules.snapshot_profile.AnsibleModule',
                       return_value=mock_module):
                from ansible_collections.vergeio.vergeos.plugins.modules import (
                    snapshot_profile as sp_module,
                )
                try:
                    sp_module.main()
                except SystemExit:
                    pass


PROFILE_DATA = {'$key': 8, 'name': 'vm-backups', 'description': 'backups'}

# Raw period row as the API returns it (max_tier is a string)
PERIOD_ROW = {
    '$key': 31, 'name': 'Daily', 'frequency': 'daily', 'retention': 604800,
    'minute': 0, 'hour': 2, 'day_of_week': 'any', 'day_of_month': 0,
    'month': 0, 'max_tier': '1', 'quiesce': False,
    'min_snapshots': 1, 'immutable': False,
}


def make_client(profile=None, periods=None):
    client = MagicMock()
    # resolve_one lists and matches client-side; it never calls get(name=).
    client.snapshot_profiles.list.return_value = (
        [] if profile is None else [profile])
    manager = MagicMock()
    manager.list.return_value = periods or []
    client.snapshot_profiles.periods.return_value = manager
    # A bare MagicMock list hangs if something iterates it. An empty list
    # fails a name lookup instead, which is what a missing tag should do.
    client.tags.list.return_value = []
    return client, manager


class TestProfilePresent:
    def test_creates_profile_and_periods(self):
        client, manager = make_client()
        client.snapshot_profiles.create.return_value = make_row(dict(PROFILE_DATA))

        module = make_module(base_params(
            description='backups', periods=[default_period()]))
        run_main(module, client)

        client.snapshot_profiles.create.assert_called_once_with(
            name='vm-backups', description='backups')
        manager.create.assert_called_once()
        assert manager.create.call_args[1]['retention'] == 604800
        result = module.exit_json.call_args[1]
        assert result['changed'] is True
        assert "created profile 'vm-backups'" in result['actions']

    def test_idempotent_when_all_match(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[make_row(dict(PERIOD_ROW))])

        module = make_module(base_params(
            description='backups', periods=[default_period()]))
        run_main(module, client)

        client.snapshot_profiles.update.assert_not_called()
        manager.create.assert_not_called()
        manager.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_max_tier_string_compares_as_int(self):
        # API stores max_tier as '1'; desired 1 must NOT drift
        row = dict(PERIOD_ROW)
        row['max_tier'] = '1'
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)), periods=[make_row(row)])

        module = make_module(base_params(periods=[default_period(max_tier=1)]))
        run_main(module, client)

        manager.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False

    def test_updates_drifted_retention(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[make_row(dict(PERIOD_ROW))])

        module = make_module(base_params(
            periods=[default_period(retention=1209600)]))
        run_main(module, client)

        manager.update.assert_called_once_with(31, retention=1209600)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_adds_missing_period(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[make_row(dict(PERIOD_ROW))])

        module = make_module(base_params(periods=[
            default_period(),
            default_period(name='Weekly', frequency='weekly',
                           retention=2592000, day_of_week='sun'),
        ]))
        run_main(module, client)

        manager.create.assert_called_once()
        assert manager.create.call_args[1]['name'] == 'Weekly'
        assert module.exit_json.call_args[1]['changed'] is True

    def test_purge_deletes_unlisted_period(self):
        stray = dict(PERIOD_ROW)
        stray.update({'$key': 32, 'name': 'Hourly'})
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[make_row(dict(PERIOD_ROW)), make_row(stray)])

        module = make_module(base_params(
            periods=[default_period()], purge_periods=True))
        run_main(module, client)

        manager.delete.assert_called_once_with(32)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_updates_description(self):
        client, manager = make_client(profile=make_row(dict(PROFILE_DATA)))
        client.snapshot_profiles.update.return_value = make_row(dict(PROFILE_DATA))

        module = make_module(base_params(description='new words'))
        run_main(module, client)

        client.snapshot_profiles.update.assert_called_once_with(
            8, description='new words')
        assert module.exit_json.call_args[1]['changed'] is True

    def test_check_mode_writes_nothing(self):
        client, manager = make_client()

        module = make_module(base_params(periods=[default_period()]),
                             check_mode=True)
        run_main(module, client)

        client.snapshot_profiles.create.assert_not_called()
        manager.create.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True


class TestProfileAbsent:
    def test_deletes_existing(self):
        client, _unused = make_client(profile=make_row(dict(PROFILE_DATA)))

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.snapshot_profiles.delete.assert_called_once_with(8)
        assert module.exit_json.call_args[1]['changed'] is True

    def test_absent_missing_no_change(self):
        client, _unused = make_client()

        module = make_module(base_params(state='absent'))
        run_main(module, client)

        client.snapshot_profiles.delete.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False


class TestSkipMissedIsGone:
    """The seventh appearance of the compare-and-map class, caught pre-merge.

    `skip_missed` is not a column on a period row. The API accepts it with
    HTTP 200 and stores it nowhere -- measured by creating a period with
    skip_missed=True and reading back all 20 columns, none of which changed.
    The SDK's periods.create() still offers the keyword, so the signature does
    not give it away.

    It did both harms at once: the value was silently discarded, AND the
    update path compared a missing column (None -> False) against True, so a
    profile with it set reported changed on every run forever. Removed rather
    than deprecated -- the same call as subnet_mask in #18.
    """

    def test_it_is_not_an_option(self):
        """Checked against the spec and the create call, not a bare text
        search -- the source mentions the name on purpose, in the comment
        explaining why it is gone."""
        import re
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            snapshot_profile,
        )
        source = open(snapshot_profile.__file__).read()
        assert not re.search(r'skip_missed\s*=\s*dict\(', source), (
            'skip_missed is back in argument_spec; it is not a period column')
        assert not re.search(r'skip_missed\s*=\s*want\[', source), (
            'skip_missed is being sent on create again; it goes nowhere')

    def test_it_is_not_documented_as_a_period_suboption(self):
        import re
        import yaml
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            snapshot_profile,
        )
        source = open(snapshot_profile.__file__).read()
        doc = yaml.safe_load(
            re.search(r"DOCUMENTATION = r?'''(.*?)'''", source, re.S).group(1))
        assert 'skip_missed' not in doc['options']['periods']['suboptions']

    def test_it_is_not_in_the_period_field_map(self):
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            snapshot_profile,
        )
        assert 'skip_missed' not in snapshot_profile.PERIOD_FIELDS


class TestTheProjectionIsNamed:
    def test_every_compared_period_column_is_fetched(self):
        """#18: a column diffed but never fetched reads as None, so the module
        rewrites the period on every run."""
        from ansible_collections.vergeio.vergeos.plugins.modules import (
            snapshot_profile,
        )
        for pair in snapshot_profile.PERIOD_FIELDS.values():
            assert pair[0] in snapshot_profile.PERIOD_READ_FIELDS, (
                'snapshot_profile compares %r but does not fetch it' % pair[0])


class TestSdkErrorsAreHandled:
    @pytest.mark.parametrize('exc', [
        APIError, AuthenticationError, ValidationError, VergeConnectionError,
    ])
    def test_sdk_errors_do_not_fall_through_to_unexpected(self, exc):
        client, _unused = make_client()
        client.snapshot_profiles.list.side_effect = exc('server said no')
        module = make_module(base_params())

        run_main(module, client)

        module.fail_json.assert_called_once()
        assert 'Unexpected error' not in module.fail_json.call_args[1]['msg']

    def test_duplicate_profile_names_are_refused(self):
        client, _unused = make_client()
        client.snapshot_profiles.list.return_value = [
            make_row(dict(PROFILE_DATA)),
            make_row(dict(PROFILE_DATA, **{'$key': 9})),
        ]
        module = make_module(base_params())

        run_main(module, client)

        assert 'refusing to guess' in module.fail_json.call_args[1]['msg']


# The create() call a period with no tag parameters has always made.
# include_tags=None would be a different call, and an SDK from before
# pyVergeOS #200 rejects the keyword.
UNSCOPED_CREATE = {
    'name': 'Daily',
    'frequency': 'daily',
    'retention': 604800,
    'minute': 0,
    'hour': 2,
    'day_of_week': 'any',
    'day_of_month': 0,
    'month': 0,
    'max_tier': 1,
    'quiesce': False,
    'min_snapshots': 1,
    'immutable': False,
}


def scoped_period(**overrides):
    """A stored partial period whose SDK properties are real values.

    MagicMock grows snapshot_type on demand and it is not a str, which
    would look like "no scope" and hide a compare bug.
    """
    row = make_row(dict(PERIOD_ROW, type=overrides.get(
        'snapshot_type', 'partial_include')))
    row.snapshot_type = overrides.get('snapshot_type', 'partial_include')
    row.partial_tag_keys = list(overrides.get('partial_tag_keys', [3]))
    row.quiesce_tag_keys = list(overrides.get('quiesce_tag_keys', []))
    return row


class OldPeriods:
    """periods.create() as released pyVergeOS still has it: no tag kwargs."""

    def __init__(self):
        self.created = []
        self.updated = []

    def list(self, fields=None):
        return []

    def create(self, name, frequency, retention, minute=0, hour=0,
               day_of_week='any', day_of_month=0, month=0, max_tier=1,
               quiesce=False, min_snapshots=1, immutable=False):
        self.created.append(name)

    def update(self, key, **kwargs):
        self.updated.append((key, kwargs))


class TestUnscopedPeriodsStayUnscoped:
    def test_create_kwargs_are_the_ones_this_module_has_always_sent(self):
        client, manager = make_client()
        client.snapshot_profiles.create.return_value = make_row(dict(PROFILE_DATA))
        module = make_module(base_params(periods=[default_period()]))
        run_main(module, client)

        assert manager.create.call_args.kwargs == UNSCOPED_CREATE

    def test_a_partial_period_is_left_alone_when_the_playbook_names_no_tags(self):
        """Omitting the keys means "do not manage scope", not "make it full".

        An existing playbook has no include_tags. Running it against a
        period somebody scoped in the UI must not clear that scope, and
        must not start sending tag keywords to an SDK that has never
        accepted them.
        """
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[scoped_period()])
        module = make_module(base_params(
            description='backups', periods=[default_period()]))
        run_main(module, client)

        manager.update.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False
        reported = module.exit_json.call_args[1]['profile']['periods'][0]
        assert 'snapshot_type' not in reported
        assert 'partial_tag_keys' not in reported
        fields = manager.list.call_args.kwargs['fields']
        assert 'partial_tags[refs[tag]]' not in fields
        assert 'quiesce_tags[refs[tag]]' not in fields


class TestTagScopedPeriods:
    def test_include_tags_are_passed_through_and_the_columns_are_not(self):
        client, manager = make_client()
        client.snapshot_profiles.create.return_value = make_row(dict(PROFILE_DATA))
        module = make_module(base_params(periods=[
            default_period(include_tags=['gold'], quiesce_tags=['gold'])]))
        run_main(module, client)

        kwargs = manager.create.call_args.kwargs
        assert kwargs['include_tags'] == ['gold']
        assert kwargs['quiesce_tags'] == ['gold']
        assert 'exclude_tags' not in kwargs
        # The SDK writes partial_tag_args. Sending the column ourselves
        # is how a typo gets dropped on the floor.
        assert 'partial_tags' not in kwargs
        assert 'partial_tag_args' not in kwargs
        assert 'quiesce_tags' in kwargs
        assert 'type' not in kwargs

    def test_exclude_tags_are_the_other_scope(self):
        client, manager = make_client()
        client.snapshot_profiles.create.return_value = make_row(dict(PROFILE_DATA))
        module = make_module(base_params(periods=[
            default_period(exclude_tags=['scratch'])]))
        run_main(module, client)

        kwargs = manager.create.call_args.kwargs
        assert kwargs['exclude_tags'] == ['scratch']
        assert 'include_tags' not in kwargs
        assert 'quiesce_tags' not in kwargs

    def test_a_matching_scope_is_not_drift(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[scoped_period(quiesce_tag_keys=[3])])
        client.tags.list.return_value = [{'$key': 3, 'name': 'gold'}]
        module = make_module(base_params(periods=[
            default_period(include_tags=['gold'], quiesce_tags=['gold'])]))
        run_main(module, client)

        manager.update.assert_not_called()
        manager.create.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False
        reported = module.exit_json.call_args[1]['profile']['periods'][0]
        assert reported['snapshot_type'] == 'partial_include'
        assert reported['partial_tag_keys'] == [3]
        assert reported['quiesce_tag_keys'] == [3]
        fields = manager.list.call_args.kwargs['fields']
        assert fields.count('partial_tags[refs[tag]]') == 1
        assert 'quiesce_tags[refs[tag]]' in fields
        assert 'type' in fields
        assert 'partial_tags' not in fields
        assert 'quiesce_tags' not in fields

    def test_a_numeric_key_is_not_looked_up_by_name(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[scoped_period()])
        module = make_module(base_params(periods=[
            default_period(include_tags=['3'])]))
        run_main(module, client)

        manager.update.assert_not_called()
        client.tags.list.assert_not_called()

    def test_a_different_tag_is_written_as_include_tags(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[scoped_period(partial_tag_keys=[9])])
        client.tags.list.return_value = [{'$key': 3, 'name': 'gold'}]
        module = make_module(base_params(periods=[
            default_period(include_tags=['gold'])]))
        run_main(module, client)

        kwargs = manager.update.call_args.kwargs
        assert kwargs['include_tags'] == ['gold']
        assert 'partial_tag_args' not in kwargs
        assert 'partial_tags' not in kwargs
        assert 'exclude_tags' not in kwargs
        assert module.exit_json.call_args[1]['changed'] is True

    def test_dropping_quiesce_tags_sends_an_empty_list(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[scoped_period(quiesce_tag_keys=[3])])
        client.tags.list.return_value = [{'$key': 3, 'name': 'gold'}]
        module = make_module(base_params(periods=[
            default_period(include_tags=['gold'])]))
        run_main(module, client)

        kwargs = manager.update.call_args.kwargs
        assert kwargs['include_tags'] == ['gold']
        assert kwargs['quiesce_tags'] == []

    def test_check_mode_reports_the_scope_change_and_writes_nothing(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[scoped_period(partial_tag_keys=[9])])
        client.tags.list.return_value = [{'$key': 3, 'name': 'gold'}]
        module = make_module(base_params(periods=[
            default_period(include_tags=['gold'])]), check_mode=True)
        run_main(module, client)

        manager.update.assert_not_called()
        manager.create.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is True
        assert any('tag scope' in action
                   for action in module.exit_json.call_args[1]['actions'])

    def test_include_and_exclude_together_are_refused(self):
        client, manager = make_client()
        module = make_module(base_params(periods=[default_period(
            include_tags=['gold'], exclude_tags=['scratch'])]))
        run_main(module, client)

        assert module.fail_json.called
        assert 'both' in module.fail_json.call_args[1]['msg']
        manager.create.assert_not_called()
        client.snapshot_profiles.create.assert_not_called()

    def test_quiesce_tags_alone_are_refused(self):
        client, manager = make_client()
        module = make_module(base_params(periods=[
            default_period(quiesce_tags=['gold'])]))
        run_main(module, client)

        assert module.fail_json.called
        assert 'quiesce' in module.fail_json.call_args[1]['msg'].lower()
        manager.create.assert_not_called()

    def test_an_unknown_tag_name_is_refused(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[scoped_period()])
        client.tags.list.return_value = []
        module = make_module(base_params(periods=[
            default_period(include_tags=['gold'])]))
        run_main(module, client)

        assert "no tag named 'gold'" in module.fail_json.call_args[1]['msg']
        manager.update.assert_not_called()

    def test_an_ambiguous_tag_name_is_refused(self):
        client, manager = make_client(
            profile=make_row(dict(PROFILE_DATA)),
            periods=[scoped_period()])
        client.tags.list.return_value = [
            {'$key': 3, 'name': 'gold'},
            {'$key': 4, 'name': 'gold'},
        ]
        module = make_module(base_params(periods=[
            default_period(include_tags=['gold'])]))
        run_main(module, client)

        assert 'matches 2' in module.fail_json.call_args[1]['msg']
        manager.update.assert_not_called()

    def test_an_sdk_without_the_parameter_fails_before_it_writes(self):
        client, _manager = make_client(profile=make_row(dict(PROFILE_DATA)))
        old = OldPeriods()
        client.snapshot_profiles.periods.return_value = old
        module = make_module(base_params(periods=[
            default_period(include_tags=['gold'])]))
        run_main(module, client)

        assert module.fail_json.called
        message = module.fail_json.call_args[1]['msg']
        assert '200' in message
        assert 'include_tags' in message
        assert old.created == []
        assert old.updated == []
        client.snapshot_profiles.update.assert_not_called()

    def test_a_new_profile_is_not_created_when_the_sdk_cannot_scope_it(self):
        client, _manager = make_client()
        old = OldPeriods()
        client.snapshot_profiles.periods.return_value = old
        module = make_module(base_params(periods=[
            default_period(include_tags=['gold'])]))
        run_main(module, client)

        assert module.fail_json.called
        client.snapshot_profiles.create.assert_not_called()
        assert old.created == []


def test_period_tag_support_matches_the_installed_sdk():
    """The refusal has to follow the SDK that is installed, not a memory
    of which release grew the parameter."""
    import inspect

    from pyvergeos.resources.snapshot_profiles import SnapshotProfilePeriodManager

    from ansible_collections.vergeio.vergeos.plugins.modules import (
        snapshot_profile,
    )

    signature = inspect.signature(SnapshotProfilePeriodManager.create)
    has_param = 'include_tags' in signature.parameters
    assert snapshot_profile.period_tags_supported(
        SnapshotProfilePeriodManager.create) is has_param
    assert snapshot_profile.TAG_READ_FIELDS == [
        'type',
        'partial_tags[refs[tag]]',
        'quiesce_tags[refs[tag]]',
    ]
