"""Rendering tests for the LB stack (pure functions, no API).

The haproxy half asserts on text, because an haproxy config IS text and its
grammar is the thing under test.

The user-data half does not, any more. It used to read

    assert 'hostname: lb1' in ud
    assert '  - touch /tmp/done' in ud

which is a claim about the bytes and not about the document. Every one of
those passed while the renderer emitted bare YAML scalars, so a hostname of
``lb1: x`` made the whole user-data unparseable and ``extra_runcmd:
['echo: hi']`` became a *mapping* inside ``runcmd`` -- a valid document that
uploads, boots, and simply never runs the command. What cloud-init sees is a
parsed document, so that is what gets asserted.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'lb_stack', 'files'))
from lb_render import (  # noqa: E402
    check_name,
    render_haproxy,
    render_keepalived,
    render_userdata,
    yaml_scalar,
)

HA = {'vip': '192.0.2.100/24', 'auth_pass': 'PLACEHOLDER', 'router_id': 77}


def spec(**over):
    base = {
        'hostname': 'lb1',
        'frontends': [{'name': 'web', 'bind_port': 80,
                       'default_backend': 'app'}],
        'backends': [{'name': 'app', 'servers': [
            {'name': 'a', 'host': '192.0.2.10', 'port': 8081},
            {'name': 'b', 'host': '192.0.2.10', 'port': 8082}]}],
    }
    base.update(over)
    return base


def doc(**over):
    """The rendered user-data, as cloud-init would parse it."""
    return yaml.safe_load(render_userdata(spec(**over)))


class TestHaproxy:
    def test_frontend_and_backend_rendered(self):
        cfg = render_haproxy(spec())
        assert 'frontend web' in cfg
        assert 'bind *:80' in cfg
        assert 'default_backend app' in cfg
        assert 'server a 192.0.2.10:8081 check' in cfg
        assert 'server b 192.0.2.10:8082 check' in cfg
        assert 'balance roundrobin' in cfg

    def test_tcp_mode_propagates(self):
        s = spec()
        s['frontends'][0]['mode'] = 'tcp'
        s['backends'][0]['mode'] = 'tcp'
        cfg = render_haproxy(s)
        assert cfg.count('mode tcp') == 2

    def test_check_can_be_disabled(self):
        s = spec()
        s['backends'][0]['check'] = False
        assert ' check' not in render_haproxy(s)

    def test_unknown_backend_reference_rejected(self):
        s = spec()
        s['frontends'][0]['default_backend'] = 'ghost'
        with pytest.raises(ValueError, match='unknown backend'):
            render_haproxy(s)

    def test_duplicate_names_rejected(self):
        s = spec()
        s['backends'].append(dict(s['backends'][0]))
        with pytest.raises(ValueError, match='duplicate backend'):
            render_haproxy(s)

    def test_httpchk_optional(self):
        s = spec()
        assert 'httpchk' not in render_haproxy(s)
        s['backends'][0]['httpchk'] = True
        assert 'option httpchk GET /' in render_haproxy(s)
        s['backends'][0]['httpchk_path'] = '/health'
        assert 'option httpchk GET /health' in render_haproxy(s)

    def test_tunables_land_in_defaults(self):
        cfg = render_haproxy(spec(maxconn=500, timeout_client='60s'))
        assert 'maxconn 500' in cfg
        assert 'timeout client 60s' in cfg


class TestNamesThatWouldBreakTwoGrammars:
    """A section name lands in an haproxy config AND in a YAML document.

    Escaping it for one does not help with the other, so the names are
    constrained instead. A frontend called ``web server`` is two tokens to
    haproxy; one containing a newline ends the section early and silently
    changes what the load balancer does.
    """

    @pytest.mark.parametrize('bad', ['web server', 'web\nfrontend evil',
                                     '-web', '', 'web:80', 'web#1'])
    def test_a_name_that_is_not_one_token_is_refused(self, bad):
        s = spec()
        s['frontends'][0]['name'] = bad
        with pytest.raises(ValueError, match='not usable'):
            render_haproxy(s)

    def test_a_server_host_is_checked_too(self):
        s = spec()
        s['backends'][0]['servers'][0]['host'] = 'a b'
        with pytest.raises(ValueError, match='not usable'):
            render_haproxy(s)

    @pytest.mark.parametrize('good', ['web', 'web-1', 'web_1', 'app.example',
                                      '192.0.2.1', 'a'])
    def test_ordinary_names_are_fine(self, good):
        assert check_name('frontend', good) == good


class TestKeepalived:
    def test_no_ha_block_renders_nothing(self):
        assert render_keepalived(spec()) == ''

    def test_vip_pair_rendered(self):
        cfg = render_keepalived(spec(ha=dict(HA, priority=150,
                                             state='BACKUP')))
        assert '192.0.2.100/24' in cfg
        assert 'priority 150' in cfg
        assert 'state BACKUP' in cfg
        assert 'pgrep -x haproxy' in cfg
        assert 'virtual_router_id 77' in cfg

    def test_the_auth_pass_is_the_one_given(self):
        assert 'auth_pass PLACEHOLDER' in render_keepalived(spec(ha=HA))


class TestTheDefaultPasswordThatWasShipped:
    """`auth_pass` used to carry a hard-coded default.

    A default password in a published repository is the same password on
    every HA pair that never overrode it, and VRRP carries it in clear on
    the wire. The only safe default is no default.

    The old value is deliberately not written down here. Asserting that one
    particular string is absent would be the weaker test anyway: what the
    code must not have is a fallback of any kind, so that is what is
    checked, by reading the call itself.
    """

    def test_ha_without_auth_pass_is_refused(self):
        with pytest.raises(ValueError, match='auth_pass is required'):
            render_keepalived(spec(ha={'vip': '192.0.2.100/24'}))

    def test_the_refusal_reaches_the_user_data_path_too(self):
        with pytest.raises(ValueError, match='auth_pass is required'):
            render_userdata(spec(ha={'vip': '192.0.2.100/24'}))

    def test_auth_pass_is_read_with_no_fallback_at_all(self):
        """`ha.get('auth_pass')` must take exactly one argument.

        A two-argument .get() IS a default, whatever string it holds, so
        this forbids the shape rather than one value -- and it cannot be
        satisfied by renaming the secret.
        """
        import ast
        import lb_render
        tree = ast.parse(open(lb_render.__file__).read())

        defaulted = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'get'):
                continue
            if not node.args:
                continue
            key = node.args[0]
            if not (isinstance(key, ast.Constant)
                    and key.value == 'auth_pass'):
                continue
            if len(node.args) > 1:
                defaulted.append(ast.unparse(node))

        assert not defaulted, (
            'auth_pass is read with a fallback: %s. Any default here is a '
            'shared secret published with the collection.'
            % ', '.join(defaulted))

    def test_the_rendered_config_contains_only_the_given_secret(self):
        """Nothing is substituted in behind the caller's back."""
        rendered = render_keepalived(spec(ha=HA))
        assert rendered.count('auth_pass') == 1
        assert 'auth_pass %s' % HA['auth_pass'] in rendered

    def test_a_vip_is_still_required(self):
        with pytest.raises(ValueError, match='vip'):
            render_keepalived(spec(ha={'auth_pass': 'x'}))

    def test_an_auth_pass_that_would_break_the_config_is_refused(self):
        with pytest.raises(ValueError, match='auth_pass'):
            render_keepalived(spec(ha=dict(HA, auth_pass='has spaces')))

    def test_a_nonsense_state_is_refused(self):
        with pytest.raises(ValueError, match='MASTER or BACKUP'):
            render_keepalived(spec(ha=dict(HA, state='PRIMARY')))


class TestUserdata:
    def test_minimal_userdata_installs_and_starts_haproxy(self):
        rendered = render_userdata(spec())
        assert rendered.startswith('#cloud-config')
        parsed = doc()
        assert parsed['hostname'] == 'lb1'
        assert parsed['packages'] == ['haproxy']
        assert 'systemctl restart haproxy' in parsed['runcmd']
        assert parsed['write_files'][0]['path'] == '/etc/haproxy/haproxy.cfg'

    def test_ha_spec_adds_keepalived(self):
        parsed = doc(ha=HA)
        assert parsed['packages'] == ['haproxy', 'keepalived']
        assert [f['path'] for f in parsed['write_files']] == [
            '/etc/haproxy/haproxy.cfg', '/etc/keepalived/keepalived.conf']
        assert 'systemctl restart keepalived' in parsed['runcmd']

    def test_no_ha_means_no_keepalived_anywhere(self):
        assert 'keepalived' not in render_userdata(spec())

    def test_the_embedded_config_survives_the_round_trip(self):
        """The haproxy config goes in as a literal block and has to come
        back out byte for byte, or the load balancer is configured with
        something nobody wrote."""
        parsed = doc()
        assert parsed['write_files'][0]['content'] == render_haproxy(spec())

    def test_extra_runcmd_appended(self):
        assert 'touch /tmp/done' in doc(extra_runcmd=['touch /tmp/done'])['runcmd']


class TestScalarsMeanWhatTheySay:
    """Measured before the fix, with a real YAML parser:

        hostname: lb1: x        -> ScannerError, the document is unusable
        hostname: {lb}          -> parses as a MAPPING, silently
        hostname: *lb           -> ComposerError, undefined alias
        runcmd: - echo: hi      -> a mapping inside runcmd, not a command

    The third and fourth are the dangerous ones: the document is valid, the
    upload succeeds, the VM boots, and the thing you asked for never
    happens.
    """

    @pytest.mark.parametrize('awkward', [
        'lb1: x', '{lb}', '*lb', '&lb', 'lb#1', '[lb]', 'yes', 'null',
        '2026-01-01', "lb'1", 'lb"1', '!lb', '%lb', '@lb', '  lb  ',
    ])
    def test_a_hostname_arrives_exactly_as_written(self, awkward):
        assert doc(hostname=awkward)['hostname'] == awkward

    @pytest.mark.parametrize('awkward', [
        'echo: hi', '- nested', '{a: b}', 'echo "quoted"', "echo 'single'",
        'echo #hash', '*star', 'true',
    ])
    def test_a_runcmd_entry_stays_a_string(self, awkward):
        commands = doc(extra_runcmd=[awkward])['runcmd']
        assert commands[-1] == awkward
        assert isinstance(commands[-1], str)

    def test_every_rendered_document_parses(self):
        """The property, rather than the examples."""
        for hostname in ['lb1', 'lb1: x', '{lb}', '*lb']:
            for extra in [[], ['echo: hi'], ['- nested']]:
                yaml.safe_load(render_userdata(
                    spec(hostname=hostname, extra_runcmd=extra)))

    def test_yaml_scalar_is_a_quoted_scalar(self):
        assert yaml_scalar('a: b') == '"a: b"'
        assert yaml.safe_load('k: %s' % yaml_scalar('a: b')) == {'k': 'a: b'}
