#!/usr/bin/env python3
"""Rendering tests for the k3s node recipe (pure function, no API)."""

import os
import sys

import pytest

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
        ud = render_userdata(server())
        assert 'K3S_TOKEN=tok' in ud
        assert 'sh -s - server' in ud
        assert 'K3S_URL' not in ud

    def test_version_pin(self):
        ud = render_userdata(server(version='v1.31.4+k3s1'))
        assert 'INSTALL_K3S_VERSION=v1.31.4+k3s1' in ud

    def test_no_pin_uses_stable_channel(self):
        assert 'INSTALL_K3S_VERSION' not in render_userdata(server())

    def test_tls_sans_and_disable(self):
        ud = render_userdata(server(tls_sans=['lb.example.com'],
                                    disable=['traefik']))
        assert '--tls-san lb.example.com' in ud
        assert '--disable traefik' in ud

    def test_token_with_shell_chars_is_quoted(self):
        import yaml
        doc = yaml.safe_load(render_userdata(server(token="we$ird'tok")))
        # decoded runcmd carries the shlex-quoted token so the shell
        # can never expand or split it
        assert "K3S_TOKEN='we$ird'\"'\"'tok'" in doc['runcmd'][0]


class TestAgent:
    def test_agent_join_line(self):
        ud = render_userdata(agent())
        assert 'K3S_URL=https://192.0.2.5:6443' in ud
        assert 'sh -s - agent' in ud
        assert '--tls-san' not in ud


class TestCloudConfig:
    def test_ssh_keys_embedded(self):
        ud = render_userdata(server(ssh_authorized_keys=['ssh-ed25519 AAA x']))
        assert 'ssh_authorized_keys:' in ud
        assert '  - ssh-ed25519 AAA x' in ud

    def test_phone_home_appended(self):
        ud = render_userdata(server(phone_home='http://192.0.2.9:8081/done'))
        assert 'curl -s -m 10 http://192.0.2.9:8081/done || true' in ud

    def test_userdata_is_valid_yaml(self):
        import yaml
        doc = yaml.safe_load(render_userdata(
            agent(ssh_authorized_keys=['ssh-ed25519 AAA x'],
                  phone_home='http://h/done')))
        assert doc['hostname'] == 'k3s-a1'
        assert doc['packages'] == ['curl']
        assert len(doc['runcmd']) == 2
        assert 'get.k3s.io' in doc['runcmd'][0]
