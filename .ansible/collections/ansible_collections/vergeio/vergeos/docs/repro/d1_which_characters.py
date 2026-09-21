#!/usr/bin/env python
"""Which characters can a VergeOS name contain, and which break pyvergeos?

    export VERGEOS_HOST=... VERGEOS_USERNAME=... VERGEOS_PASSWORD=...
    python docs/repro/d1_which_characters.py

Answers the only question that decides whether D1 is worth fixing: does
VergeOS accept these names at all, and if so which ones can pyvergeos then
find again.

Creates and deletes scratch groups. Nothing else is touched.
"""

import os

from pyvergeos import VergeClient
from pyvergeos.exceptions import APIError, NotFoundError, ValidationError

CANDIDATES = [
    ('apostrophe', "zz-obrien's-vm"),
    ('backslash', 'zz-back\\slash'),
    ('percent', 'zz-100%-full'),
    ('underscore', 'zz_prod_db'),
    ('double quote', 'zz-say-"hi"'),
    ('space', 'zz prod db'),
    ('ampersand', 'zz-r&d'),
    ('parentheses', 'zz-db-(primary)'),
    ('comma', 'zz-a,b'),
    ('colon', 'zz-a:b'),
    ('plus', 'zz-a+b'),
    ('hash', 'zz-a#b'),
    ('plain control', 'zz-plain-name'),
]


def main():
    client = VergeClient(
        host=os.environ['VERGEOS_HOST'],
        username=os.environ['VERGEOS_USERNAME'],
        password=os.environ['VERGEOS_PASSWORD'],
        verify_ssl=False)
    client.connect()

    print('%-14s %-20s %-9s %s'
          % ('character', 'name', 'VergeOS', 'pyvergeos get(name=)'))
    print('-' * 90)

    created = []
    broken = []
    for label, name in CANDIDATES:
        try:
            group = client.groups.create(name=name, description='charset probe')
            created.append(dict(group)['$key'])
        except Exception as exc:                       # noqa: BLE001
            print('%-14s %-20r %-9s (not created: %s)' % (label, name, 'REFUSED', exc))
            continue

        try:
            client.groups.get(name=name)
            verdict = 'found'
        except ValidationError as exc:
            verdict = '*** ValidationError: %s' % exc
            broken.append(label)
        except NotFoundError:
            verdict = '*** NotFoundError -- but it exists'
            broken.append(label)
        except APIError as exc:
            verdict = '*** %s: %s' % (type(exc).__name__, exc)
            broken.append(label)

        print('%-14s %-20r %-9s %s' % (label, name, 'accepts', verdict))

    for key in created:
        client.groups.delete(key)

    print()
    print('VergeOS accepted %d of %d names.' % (len(created), len(CANDIDATES)))
    print('pyvergeos could not find %d of them: %s'
          % (len(broken), ', '.join(broken) or 'none'))
    print()
    print('cleanup -> groups:', [dict(g)['name'] for g in client.groups.list()])


if __name__ == '__main__':
    main()
