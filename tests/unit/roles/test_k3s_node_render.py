"""Rendering tests for the k3s node recipe (pure function, no API).

Two things this file now asserts that it did not.

The document, rather than the bytes. ``assert '  - ssh-ed25519 AAA x' in ud``
is a claim about text; cloud-init reads a parsed document, and a bare scalar
can parse into something else entirely -- ``hostname: {k1}`` is a mapping,
not a string, and the node comes up unnamed with nothing to show for it.

And the shell line, all of it. The renderer already reached for
``shlex.quote`` on the environment values and not on the install arguments,
four lines apart, so a ``tls_sans`` entry could close the command and start
another one as root inside the guest.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import shlex
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'k3s_node', 'files'))
from k3s_render import render_userdata  # noqa: E402


def server(**over):
    base = {'hostname': 'k3s-s1', 'role': 'server', 'token': 'tok'}
    base.update(over)
    return base


def agent(**over):
    base = {'hostname': 'k3s-a1', 'role': 'agent', 'token': 'tok',
            'server_url': 'https://192.0.2.5:6443'}
    base.update(over)
    return base


def install_line(spec):
    """The one command cloud-init will actually run, as it will see it."""
    return yaml.safe_load(render_userdata(spec))['runcmd'][0]


class TestValidation:
    def test_role_required(self):
        with pytest.raises(ValueError, match='role must be'):
            render_userdata({'hostname': 'x', 'token': 't'})

    def test_token_required(self):
        with pytest.raises(ValueError, match='token is required'):
            render_userdata({'hostname': 'x', 'role': 'server'})

    def test_agent_needs_server_url(self):
        with pytest.raises(ValueError, match='server_url'):
            render_userdata({'hostname': 'x', 'role': 'agent',
                             'token': 't'})

    def test_server_rejects_server_url(self):
        with pytest.raises(ValueError, match='a server IS the url'):
            render_userdata(server(server_url='https://x:6443'))

    def test_hostname_required(self):
        with pytest.raises(ValueError, match='hostname'):
            render_userdata({'role': 'server', 'token': 't'})


class TestServer:
    def test_server_install_line(self):
        line = install_line(server())
        assert 'K3S_TOKEN=tok' in line
        assert 'sh -s - server' in line
        assert 'K3S_URL' not in line

    def test_version_pin(self):
        assert 'INSTALL_K3S_VERSION=v1.31.4+k3s1' in \
            install_line(server(version='v1.31.4+k3s1'))

    def test_no_pin_uses_stable_channel(self):
        assert 'INSTALL_K3S_VERSION' not in install_line(server())

    def test_tls_sans_and_disable(self):
        line = install_line(server(tls_sans=['lb.example.com'],
                                   disable=['traefik']))
        assert '--tls-san lb.example.com' in line
        assert '--disable traefik' in line

    def test_ordinary_values_are_not_needlessly_quoted(self):
        """shlex.quote leaves safe strings alone, so the install line stays
        readable in a console log."""
        assert "'" not in install_line(server(disable=['traefik']))


class TestAgent:
    def test_agent_join_line(self):
        line = install_line(agent())
        assert 'K3S_URL=https://192.0.2.5:6443' in line
        assert 'sh -s - agent' in line
        assert '--tls-san' not in line


class TestNothingInTheSpecCanStartASecondCommand:
    """The renderer quoted the env values and not the arguments.

    Before the fix, ``tls_sans: ['k8s.example.com; touch /tmp/pwned']``
    rendered as

        sh -s - server --tls-san k8s.example.com; touch /tmp/pwned

    which cloud-init runs as root in the guest. A spec assembled from
    inventory, a CMDB or a recipe answer is not a trusted string.

    The check is a shell's own word splitting: if the payload survives
    ``shlex.split`` as exactly ONE token, the shell cannot have read any of
    it as syntax.
    """

    INJECTIONS = ['x; touch /tmp/pwned', 'x && id', 'x | sh', 'x`id`',
                  'x$(id)', "x'y", 'x\nid', 'x >/tmp/out', 'x;id']

    @pytest.mark.parametrize('payload', INJECTIONS)
    def test_a_tls_san_cannot_escape(self, payload):
        words = shlex.split(install_line(server(tls_sans=[payload])))
        assert words.count(payload) == 1
        assert words[words.index(payload) - 1] == '--tls-san'

    @pytest.mark.parametrize('payload', INJECTIONS)
    def test_a_disable_value_cannot_escape(self, payload):
        words = shlex.split(install_line(server(disable=[payload])))
        assert words.count(payload) == 1
        assert words[words.index(payload) - 1] == '--disable'

    @pytest.mark.parametrize('payload', INJECTIONS)
    def test_a_token_cannot_escape(self, payload):
        words = shlex.split(install_line(server(token=payload)))
        assert 'K3S_TOKEN=%s' % payload in words

    @pytest.mark.parametrize('payload', INJECTIONS)
    def test_a_server_url_cannot_escape(self, payload):
        words = shlex.split(install_line(agent(server_url=payload)))
        assert 'K3S_URL=%s' % payload in words

    @pytest.mark.parametrize('payload', INJECTIONS)
    def test_a_phone_home_url_cannot_escape(self, payload):
        doc = yaml.safe_load(render_userdata(server(phone_home=payload)))
        words = shlex.split(doc['runcmd'][1])
        assert words.count(payload) == 1

    def test_the_guard_would_notice_an_unquoted_value(self):
        """An unquoted payload splits into several words, which is the
        whole defect. Assembled here so this class cannot pass vacuously."""
        unquoted = 'sh -s - server --tls-san x; touch /tmp/pwned'
        assert shlex.split(unquoted).count('x; touch /tmp/pwned') == 0


class TestCloudConfig:
    def test_ssh_keys_embedded(self):
        doc = yaml.safe_load(
            render_userdata(server(ssh_authorized_keys=['ssh-ed25519 AAA x'])))
        assert doc['ssh_authorized_keys'] == ['ssh-ed25519 AAA x']

    def test_phone_home_appended(self):
        doc = yaml.safe_load(
            render_userdata(server(phone_home='http://192.0.2.9:8081/done')))
        assert 'http://192.0.2.9:8081/done' in doc['runcmd'][1]
        assert doc['runcmd'][1].endswith('|| true')

    def test_userdata_is_valid_yaml(self):
        doc = yaml.safe_load(render_userdata(
            agent(ssh_authorized_keys=['ssh-ed25519 AAA x'],
                  phone_home='http://h/done')))
        assert doc['hostname'] == 'k3s-a1'
        assert doc['packages'] == ['curl']
        assert len(doc['runcmd']) == 2
        assert 'get.k3s.io' in doc['runcmd'][0]

    def test_the_token_is_in_the_document_because_it_has_to_be(self):
        """It is the one secret that must reach the guest. The role marks
        every task that handles it no_log; the document itself carries it."""
        assert 'tok' in install_line(server())


class TestScalarsMeanWhatTheySay:
    @pytest.mark.parametrize('awkward', [
        'k1: x', '{k1}', '*k1', '&k1', 'k1#1', '[k1]', 'yes', 'null',
        "k1'1", 'k1"1', '!k1', '%k1',
    ])
    def test_a_hostname_arrives_exactly_as_written(self, awkward):
        doc = yaml.safe_load(render_userdata(server(hostname=awkward)))
        assert doc['hostname'] == awkward

    @pytest.mark.parametrize('awkward', [
        'ssh-rsa AAA user@host', 'ssh-ed25519 AAA {weird}',
        'ssh-ed25519 AAA a: b',
    ])
    def test_an_ssh_key_stays_a_string(self, awkward):
        doc = yaml.safe_load(
            render_userdata(server(ssh_authorized_keys=[awkward])))
        assert doc['ssh_authorized_keys'] == [awkward]

    def test_every_rendered_document_parses(self):
        for hostname in ['k1', 'k1: x', '{k1}', '*k1']:
            for token in ['t', "we$ird'tok"]:
                yaml.safe_load(render_userdata(
                    server(hostname=hostname, token=token)))
