#!/usr/bin/env python3
"""Rendering tests for the LB stack (pure functions, no API)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..',
                                'roles', 'lb_stack', 'files'))
from lb_render import (  # noqa: E402
    render_haproxy,
    render_keepalived,
    render_userdata,
)


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


class TestKeepalived:
    def test_no_ha_block_renders_nothing(self):
        assert render_keepalived(spec()) == ''

    def test_vip_pair_rendered(self):
        cfg = render_keepalived(spec(ha={'vip': '192.0.2.100/24',
                                         'priority': 150,
                                         'state': 'BACKUP'}))
        assert '192.0.2.100/24' in cfg
        assert 'priority 150' in cfg
        assert 'state BACKUP' in cfg
        assert 'pgrep -x haproxy' in cfg


class TestUserdata:
    def test_minimal_userdata_installs_and_starts_haproxy(self):
        ud = render_userdata(spec())
        assert ud.startswith('#cloud-config')
        assert 'hostname: lb1' in ud
        assert '  - haproxy' in ud
        assert 'keepalived' not in ud
        assert '/etc/haproxy/haproxy.cfg' in ud
        assert 'systemctl restart haproxy' in ud

    def test_ha_spec_adds_keepalived(self):
        ud = render_userdata(spec(ha={'vip': '192.0.2.100/24'}))
        assert '  - keepalived' in ud
        assert '/etc/keepalived/keepalived.conf' in ud
        assert 'systemctl restart keepalived' in ud

    def test_config_content_is_embedded_indented(self):
        ud = render_userdata(spec())
        assert '      frontend web' in ud
        assert '      server a 192.0.2.10:8081 check' in ud

    def test_extra_runcmd_appended(self):
        ud = render_userdata(spec(extra_runcmd=['touch /tmp/done']))
        assert '  - touch /tmp/done' in ud

    def test_userdata_is_valid_yaml(self):
        import yaml
        doc = yaml.safe_load(render_userdata(spec(
            ha={'vip': '192.0.2.100/24'})))
        assert doc['packages'] == ['haproxy', 'keepalived']
        assert doc['write_files'][0]['path'] == '/etc/haproxy/haproxy.cfg'
        assert 'frontend web' in doc['write_files'][0]['content']
