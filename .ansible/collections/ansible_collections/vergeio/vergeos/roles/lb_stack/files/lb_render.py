"""Render a declarative LB spec into haproxy/keepalived/cloud-init.

The honest-label answer to "VergeOS has no NSX-ALB": a load balancer
that IS a VM, deployed and configured as code. This renderer is the
testable core — a pure function from a spec document to the three
artifacts a deploy needs:

  haproxy.cfg   — frontends/backends with health checks
  keepalived.conf — optional, only when a VIP/peer pair is declared
  user-data     — cloud-init: write configs, install packages, start

No API access, no side effects. The lb_stack role feeds the user-data
to the VergeOS cloud_init module; tests feed specs and compare output.

Usage:
  lb_render.py --spec spec.json [--artifact haproxy|keepalived|userdata]
"""

from __future__ import annotations

import argparse
import json
import sys

HAPROXY_GLOBAL = """\
global
    log /dev/log local0
    maxconn {maxconn}
    user haproxy
    group haproxy
    daemon

defaults
    log global
    mode http
    option httplog
    option dontlognull
    timeout connect {timeout_connect}
    timeout client {timeout_client}
    timeout server {timeout_server}
"""


def render_haproxy(spec):
    """spec['frontends']: [{name, bind_port, default_backend, mode?}]
    spec['backends']:  [{name, balance?, check?, servers: [{name, host, port}]}]
    """
    out = [HAPROXY_GLOBAL.format(
        maxconn=spec.get('maxconn', 2000),
        timeout_connect=spec.get('timeout_connect', '5s'),
        timeout_client=spec.get('timeout_client', '30s'),
        timeout_server=spec.get('timeout_server', '30s'))]

    names = set()
    for fe in spec.get('frontends', []):
        if fe['name'] in names:
            raise ValueError("duplicate frontend name %r" % fe['name'])
        names.add(fe['name'])
        out.append('frontend %s' % fe['name'])
        out.append('    bind *:%d' % int(fe['bind_port']))
        if fe.get('mode', 'http') != 'http':
            out.append('    mode %s' % fe['mode'])
        out.append('    default_backend %s' % fe['default_backend'])
        out.append('')

    backend_names = set()
    for be in spec.get('backends', []):
        if be['name'] in backend_names:
            raise ValueError("duplicate backend name %r" % be['name'])
        backend_names.add(be['name'])
        out.append('backend %s' % be['name'])
        if be.get('mode', 'http') != 'http':
            out.append('    mode %s' % be['mode'])
        out.append('    balance %s' % be.get('balance', 'roundrobin'))
        if be.get('httpchk'):
            out.append('    option httpchk GET %s'
                       % be.get('httpchk_path', '/'))
        check = ' check' if be.get('check', True) else ''
        for srv in be.get('servers', []):
            out.append('    server %s %s:%d%s'
                       % (srv['name'], srv['host'], int(srv['port']), check))
        out.append('')

    # every referenced backend must exist
    for fe in spec.get('frontends', []):
        if fe['default_backend'] not in backend_names:
            raise ValueError("frontend %r references unknown backend %r"
                             % (fe['name'], fe['default_backend']))

    return '\n'.join(out).rstrip() + '\n'


KEEPALIVED_TEMPLATE = """\
vrrp_script chk_haproxy {{
    script "/usr/bin/pgrep -x haproxy"
    interval 2
    weight 2
}}

vrrp_instance LB {{
    state {state}
    interface {interface}
    virtual_router_id {router_id}
    priority {priority}
    advert_int 1
    authentication {{
        auth_type PASS
        auth_pass {auth_pass}
    }}
    virtual_ipaddress {{
        {vip}
    }}
    track_script {{
        chk_haproxy
    }}
}}
"""


def render_keepalived(spec):
    """spec['ha']: {vip, interface?, router_id?, priority?, state?,
    auth_pass?}. Returns '' when no HA block is declared."""
    ha = spec.get('ha')
    if not ha:
        return ''
    return KEEPALIVED_TEMPLATE.format(
        state=ha.get('state', 'MASTER'),
        interface=ha.get('interface', 'eth0'),
        router_id=int(ha.get('router_id', 51)),
        priority=int(ha.get('priority', 100)),
        auth_pass=ha.get('auth_pass', 'vergelb'),
        vip=ha['vip'])


def _cloudinit_file(path, content, mode='0644'):
    indented = '\n'.join('      ' + line for line in content.split('\n'))
    return ('  - path: %s\n    permissions: \'%s\'\n'
            '    content: |\n%s' % (path, mode, indented))


def render_userdata(spec):
    """Full cloud-init user-data: configs written before packages
    install, services enabled and restarted after."""
    packages = ['haproxy']
    files = [_cloudinit_file('/etc/haproxy/haproxy.cfg',
                             render_haproxy(spec))]
    runcmd = ["  - systemctl enable haproxy",
              "  - systemctl restart haproxy"]

    keepalived = render_keepalived(spec)
    if keepalived:
        packages.append('keepalived')
        files.append(_cloudinit_file('/etc/keepalived/keepalived.conf',
                                     keepalived))
        runcmd.append("  - systemctl enable keepalived")
        runcmd.append("  - systemctl restart keepalived")

    for cmd in spec.get('extra_runcmd', []):
        runcmd.append("  - %s" % cmd)

    lines = ['#cloud-config']
    if spec.get('hostname'):
        lines.append('hostname: %s' % spec['hostname'])
    lines.append('package_update: true')
    lines.append('packages:')
    lines.extend('  - %s' % p for p in packages)
    lines.append('write_files:')
    lines.extend(files)
    lines.append('runcmd:')
    lines.extend(runcmd)
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', required=True,
                   help='JSON spec file, or - for stdin')
    p.add_argument('--artifact', default='userdata',
                   choices=['haproxy', 'keepalived', 'userdata'])
    args = p.parse_args()

    if args.spec == '-':
        spec = json.load(sys.stdin)
    else:
        with open(args.spec) as fh:
            spec = json.load(fh)

    render = {'haproxy': render_haproxy,
              'keepalived': render_keepalived,
              'userdata': render_userdata}[args.artifact]
    sys.stdout.write(render(spec))
    return 0


if __name__ == '__main__':
    sys.exit(main())
