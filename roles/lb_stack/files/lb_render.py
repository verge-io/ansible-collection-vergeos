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
import re
import sys

# haproxy section names and hostnames end up in two different grammars --
# an haproxy config and a YAML document -- so they are constrained to what
# is unambiguous in both rather than escaped for each.
SAFE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


def yaml_scalar(value):
    """A YAML scalar that means exactly the string it was given.

    json.dumps produces a double-quoted scalar, and YAML 1.1 double-quoted
    scalars are a superset of JSON strings -- so this is both correct and
    the same trick k3s_render.py already used for its runcmd entries.

    Without it, the renderer emitted bare scalars and cloud-init got
    whatever YAML made of them. Measured:

        hostname: lb1: x   -> ScannerError, the whole user-data is invalid
        hostname: {lb}     -> parses as a MAPPING, silently
        hostname: *lb      -> ComposerError, undefined alias
        extra_runcmd 'echo: hi' -> a mapping inside runcmd, not a command

    The last one is the dangerous shape: the document is valid, the upload
    succeeds, the VM boots, and the command simply never runs.
    """
    return json.dumps(value)


def check_name(kind, value):
    if not isinstance(value, str) or not SAFE_NAME.match(value):
        raise ValueError(
            "%s name %r is not usable: it is written into an haproxy "
            "config and a YAML document, so it must start alphanumeric "
            "and contain only letters, digits, dot, dash or underscore"
            % (kind, value))
    return value


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
        check_name('frontend', fe['name'])
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
        check_name('backend', be['name'])
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
            check_name('server', srv['name'])
            check_name('server host', srv['host'])
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
    """spec['ha']: {vip, auth_pass, interface?, router_id?, priority?,
    state?}. Returns '' when no HA block is declared.

    `auth_pass` is required, and used to carry a hard-coded default.
    A default password in a published repository is the same password on
    every HA pair that never overrode it, and VRRP carries it in clear on
    the wire -- so the only safe default is no default. The old value is
    deliberately not repeated here; treat any pair that predates this as
    having a known secret and rotate it.

    `router_id` has one too, and it has to: keepalived requires a
    virtual_router_id. It must be unique per VRRP domain, and two unrelated
    pairs left at 51 on the same L2 will fight over each other's VIP. Set
    it deliberately.
    """
    ha = spec.get('ha')
    if not ha:
        return ''
    if not ha.get('vip'):
        raise ValueError('ha needs a vip')
    if not ha.get('auth_pass'):
        raise ValueError(
            'ha.auth_pass is required. It used to default to a fixed string '
            'shipped in this repository, which made it the same secret on '
            'every pair that did not override it. VRRP sends it in clear, '
            'so treat it as a shared secret, not a formality.')
    if ha.get('state', 'MASTER') not in ('MASTER', 'BACKUP'):
        raise ValueError("ha.state must be MASTER or BACKUP, got %r"
                         % ha['state'])
    if not SAFE_NAME.match(str(ha['interface'])) \
            if ha.get('interface') else False:
        raise ValueError('ha.interface %r is not an interface name'
                         % ha['interface'])
    if not SAFE_NAME.match(str(ha['auth_pass'])):
        raise ValueError(
            'ha.auth_pass is written unquoted into keepalived.conf, so it '
            'must be alphanumeric with dot, dash or underscore. keepalived '
            'truncates it to 8 characters regardless.')
    return KEEPALIVED_TEMPLATE.format(
        state=ha.get('state', 'MASTER'),
        interface=ha.get('interface', 'eth0'),
        router_id=int(ha.get('router_id', 51)),
        priority=int(ha.get('priority', 100)),
        auth_pass=ha['auth_pass'],
        vip=ha['vip'])


def _cloudinit_file(path, content, mode='0644'):
    # A literal block scalar takes the content verbatim, but a trailing
    # run of spaces on an otherwise blank line is noise that some parsers
    # complain about -- so blank lines stay blank.
    indented = '\n'.join(('      ' + line) if line else ''
                         for line in content.split('\n'))
    return ('  - path: %s\n    permissions: \'%s\'\n'
            '    content: |\n%s' % (yaml_scalar(path), mode, indented))


def render_userdata(spec):
    """Full cloud-init user-data: configs written before packages
    install, services enabled and restarted after."""
    packages = ['haproxy']
    files = [_cloudinit_file('/etc/haproxy/haproxy.cfg',
                             render_haproxy(spec))]
    runcmd = ['  - %s' % yaml_scalar('systemctl enable haproxy'),
              '  - %s' % yaml_scalar('systemctl restart haproxy')]

    keepalived = render_keepalived(spec)
    if keepalived:
        packages.append('keepalived')
        files.append(_cloudinit_file('/etc/keepalived/keepalived.conf',
                                     keepalived))
        runcmd.append('  - %s' % yaml_scalar('systemctl enable keepalived'))
        runcmd.append('  - %s' % yaml_scalar('systemctl restart keepalived'))

    for cmd in spec.get('extra_runcmd', []):
        runcmd.append('  - %s' % yaml_scalar(cmd))

    lines = ['#cloud-config']
    if spec.get('hostname'):
        lines.append('hostname: %s' % yaml_scalar(spec['hostname']))
    lines.append('package_update: true')
    lines.append('packages:')
    lines.extend('  - %s' % yaml_scalar(p) for p in packages)
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

    try:
        if args.spec == '-':
            spec = json.load(sys.stdin)
        else:
            with open(args.spec) as fh:
                spec = json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write('could not read the spec: %s\n' % exc)
        return 2

    render = {'haproxy': render_haproxy,
              'keepalived': render_keepalived,
              'userdata': render_userdata}[args.artifact]
    try:
        sys.stdout.write(render(spec))
    except Exception as exc:                      # noqa: BLE001
        # One clean line, not a traceback. The role surfaces stderr from a
        # no_log task, and a traceback there is both unreadable and a way for
        # a spec -- which carries the cluster token, or the VRRP password --
        # to reach a log through a repr.
        sys.stderr.write('%s: %s\n' % (type(exc).__name__, exc))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
