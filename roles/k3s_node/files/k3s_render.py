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

    args = [role]
    if role == 'server':
        for san in spec.get('tls_sans', []):
            args += ['--tls-san', san]
        for component in spec.get('disable', []):
            args += ['--disable', component]

    install = ('curl -sfL https://get.k3s.io | %s sh -s - %s'
               % (' '.join('%s=%s' % (k, shlex.quote(v))
                           for k, v in sorted(env.items())),
                  ' '.join(args)))

    lines = ['#cloud-config',
             'hostname: %s' % spec['hostname'],
             'package_update: true',
             'packages:',
             '  - curl']

    keys = spec.get('ssh_authorized_keys', [])
    if keys:
        lines.append('ssh_authorized_keys:')
        lines.extend('  - %s' % k for k in keys)

    lines.append('runcmd:')
    lines.append('  - %s' % json.dumps(install))
    if spec.get('phone_home'):
        lines.append('  - %s' % json.dumps(
            'curl -s -m 10 %s || true' % spec['phone_home']))
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', required=True,
                   help='JSON spec file, or - for stdin')
    args = p.parse_args()

    if args.spec == '-':
        spec = json.load(sys.stdin)
    else:
        with open(args.spec) as fh:
            spec = json.load(fh)

    sys.stdout.write(render_userdata(spec))
    return 0


if __name__ == '__main__':
    sys.exit(main())
