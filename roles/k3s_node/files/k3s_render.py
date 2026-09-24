"""Render a k3s node spec into cloud-init user-data.

The honest-label answer to "VergeOS has no Tanzu/NKE": DIY Kubernetes
you operate, deployed as code. One spec renders ONE node — a `server`
(control plane; the first server bootstraps the cluster) or an `agent`
(worker joining an existing server). A cluster is a playbook that runs
the k3s_node role once per node with a shared token.

Pure function, no API access: tests feed specs and compare output; the
k3s_node role feeds the result to the VergeOS cloud_init module.

Usage:
  k3s_render.py --spec spec.json
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys


def render_userdata(spec):
    """spec:
      hostname   node name (required)
      role       'server' | 'agent' (required)
      token      shared cluster secret (required)
      server_url https://<server>:6443 (required for agents)
      version    exact k3s version to pin ('' = stable channel)
      tls_sans   extra SANs for the server cert (servers only)
      disable    packaged components to skip, e.g. ['traefik']
      ssh_authorized_keys  keys for the default user (ops/debug access)
      phone_home URL to GET once install finishes (deploy verification)
    """
    role = spec.get('role')
    if role not in ('server', 'agent'):
        raise ValueError("role must be 'server' or 'agent', got %r" % role)
    if not spec.get('hostname'):
        raise ValueError('hostname is required')
    if not spec.get('token'):
        raise ValueError('token is required — agents can never join a '
                         'cluster whose token they do not share')
    if role == 'agent' and not spec.get('server_url'):
        raise ValueError('agent needs server_url to join')
    if role == 'server' and spec.get('server_url'):
        raise ValueError('server_url is for agents; a server IS the url')

    env = {'K3S_TOKEN': spec['token']}
    if spec.get('version'):
        env['INSTALL_K3S_VERSION'] = spec['version']
    if role == 'agent':
        env['K3S_URL'] = spec['server_url']

    # Quoted the same way the env values are. They were not, and the
    # inconsistency was the bug: the author reached for shlex.quote on one
    # side of the same shell line and not the other, so
    #
    #   tls_sans: ['k8s.example.com; touch /tmp/pwned']
    #
    # rendered as
    #
    #   sh -s - server --tls-san k8s.example.com; touch /tmp/pwned
    #
    # -- a second command, run as root in the guest, by a spec the operator
    # may well have assembled from inventory.
    args = [role]
    if role == 'server':
        for san in spec.get('tls_sans', []):
            args += ['--tls-san', shlex.quote(str(san))]
        for component in spec.get('disable', []):
            args += ['--disable', shlex.quote(str(component))]

    install = ('curl -sfL https://get.k3s.io | %s sh -s - %s'
               % (' '.join('%s=%s' % (k, shlex.quote(str(v)))
                           for k, v in sorted(env.items())),
                  ' '.join(args)))

    # Every scalar is emitted as JSON, which is a valid YAML double-quoted
    # scalar -- the trick this file already used for runcmd and did not use
    # anywhere else. A bare `hostname: k1: x` makes the whole user-data
    # unparseable; a bare `hostname: {k1}` parses as a MAPPING and the node
    # comes up unnamed with nothing to show for it.
    lines = ['#cloud-config',
             'hostname: %s' % json.dumps(spec['hostname']),
             'package_update: true',
             'packages:',
             '  - %s' % json.dumps('curl')]

    keys = spec.get('ssh_authorized_keys', [])
    if keys:
        lines.append('ssh_authorized_keys:')
        lines.extend('  - %s' % json.dumps(k) for k in keys)

    lines.append('runcmd:')
    lines.append('  - %s' % json.dumps(install))
    if spec.get('phone_home'):
        # Quoted for the same reason the install args are: this is a URL
        # that arrives from a playbook variable and lands in a root shell.
        lines.append('  - %s' % json.dumps(
            'curl -s -m 10 %s || true'
            % shlex.quote(str(spec['phone_home']))))
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', required=True,
                   help='JSON spec file, or - for stdin')
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
    try:
        sys.stdout.write(render_userdata(spec))
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
