#!/usr/bin/env python3
"""Create/delete the scratch network for the vnet_rule live ladder.

Used because the stock collection `network` module cannot create an
addressed network (it never sends `network_address` — finding #13).
Auth comes from VERGEOS_HOST / VERGEOS_TOKEN / VERGEOS_INSECURE env.

Usage: scratch_net.py create|delete <name>
"""

import os
import sys
import time

from pyvergeos import VergeClient
from pyvergeos.exceptions import NotFoundError

SCRATCH_CIDR = '10.99.99.0/24'
SCRATCH_IP = '10.99.99.1'
WAIT_SECONDS = 120


def wait_power(client, name, want_running):
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        net = client.networks.get(name=name)
        if bool(dict(net).get('running', False)) == want_running:
            return net
        time.sleep(3)
    sys.exit('timed out waiting for network %r to be %s'
             % (name, 'running' if want_running else 'stopped'))


def main():
    verb, name = sys.argv[1], sys.argv[2]
    if not name.startswith('zz-'):
        sys.exit("refusing to touch non-scratch network %r" % name)

    insecure = os.environ.get('VERGEOS_INSECURE', '').lower() in ('1', 'true', 'yes')
    client = VergeClient(
        host=os.environ['VERGEOS_HOST'],
        verify_ssl=not insecure,
        **({'token': os.environ['VERGEOS_TOKEN']} if os.environ.get('VERGEOS_TOKEN')
           else {'username': os.environ['VERGEOS_USERNAME'],
                 'password': os.environ['VERGEOS_PASSWORD']}),
    )
    client.connect()

    if verb == 'create':
        try:
            net = client.networks.get(name=name)
            created = False
        except NotFoundError:
            net = client.networks.create(
                name=name,
                network_type='internal',
                network_address=SCRATCH_CIDR,
                ip_address=SCRATCH_IP,
                description='ansible live-verify scratch network',
            )
            created = True
        # Run the vnet so rule applies exercise the real refresh path
        if not bool(dict(net).get('running', False)):
            net.power_on()
            wait_power(client, name, True)
        print('created' if created else 'exists')
    elif verb == 'delete':
        try:
            net = client.networks.get(name=name)
        except NotFoundError:
            print('absent')
        else:
            if bool(dict(net).get('running', False)):
                net.power_off()
                net = wait_power(client, name, False)
            net.delete()
            print('deleted')
    else:
        sys.exit('unknown verb %r' % verb)


if __name__ == '__main__':
    main()
