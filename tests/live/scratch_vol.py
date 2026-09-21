"""Create/delete the scratch NAS volume for the vm_export ladder.

Auth from VERGEOS_HOST / VERGEOS_TOKEN / VERGEOS_INSECURE env.

Usage: scratch_vol.py create|delete <name> <nas-service-name>
"""

import os
import sys

from pyvergeos import VergeClient
from pyvergeos.exceptions import NotFoundError


def main():
    verb, name, service = sys.argv[1], sys.argv[2], sys.argv[3]
    if not name.startswith('zz-'):
        sys.exit("refusing to touch non-scratch volume %r" % name)

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
            client.nas_volumes.get(name=name)
            print('exists')
        except NotFoundError:
            client.nas_volumes.create(
                name=name, service=service, size_gb=10, tier=1,
                description='ansible live-verify scratch volume')
            print('created')
    elif verb == 'delete':
        try:
            vol = client.nas_volumes.get(name=name)
        except NotFoundError:
            print('absent')
        else:
            # An online volume cannot be deleted ("Unable to delete
            # online drive") — disable it first.
            import time
            key = dict(vol)['$key']
            client.nas_volumes.update(key, enabled=False)
            time.sleep(3)
            client.nas_volumes.delete(key)
            print('deleted')
    else:
        sys.exit('unknown verb %r' % verb)


if __name__ == '__main__':
    main()
