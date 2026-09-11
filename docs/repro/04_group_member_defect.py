#!/usr/bin/env python
"""VergeOS: a group created just after a group delete can never take members.

    export VERGEOS_HOST=... VERGEOS_USERNAME=... VERGEOS_PASSWORD=...
    python docs/repro/04_group_member_defect.py

Creates and deletes scratch groups named zz-*. Touches nothing else.
Takes about a minute, mostly waiting.
"""

import os
import time

from pyvergeos import VergeClient

MARKER = "error setting field 'members.group'"


def connect():
    client = VergeClient(
        host=os.environ['VERGEOS_HOST'],
        username=os.environ['VERGEOS_USERNAME'],
        password=os.environ['VERGEOS_PASSWORD'],
        verify_ssl=False)
    client.connect()
    return client


def main():
    client = connect()
    user_key = int([dict(u)['$key'] for u in client.users.list()][0])

    def clean():
        for group in client.groups.list():
            row = dict(group)
            if row['name'].startswith('zz-'):
                client.groups.delete(row['$key'])
        time.sleep(8)

    def make(name):
        row = dict(client.groups.create(name=name))
        return int(row['$key']), int(row['identity'])

    def probe(key, who=None):
        """Try to add a member. `who` lets each column of the rolling test use
        a distinct user, so an earlier success cannot show up later as a
        duplicate and be mistaken for the defect."""
        try:
            client.groups.members(key).add_user(who or user_key)
            return 'OK'
        except Exception as exc:                       # noqa: BLE001
            if MARKER in str(exc):
                return 'DEFECT'
            return type(exc).__name__[:8]

    print('1. the window between a group delete and the next create')
    for wait in (0, 1, 2, 3, 4, 5):
        clean()
        trigger, _ = make('zz-trigger')
        client.groups.delete(trigger)
        time.sleep(wait)
        victim, _ = make('zz-victim')
        print('   wait %-3ss -> add member: %s' % (wait, probe(victim)))
        client.groups.delete(victim)

    print()
    print('2. it does not heal on its own')
    clean()
    trigger, _ = make('zz-trigger')
    client.groups.delete(trigger)
    victim, _ = make('zz-victim')
    print('   immediately : %s' % probe(victim))
    for extra in (10, 20, 30):
        time.sleep(10)
        print('   after %-3ss  : %s' % (extra, probe(victim)))
    make('zz-next')
    print('   after creating another group: %s' % probe(victim))

    print()
    print('3. it rolls forward -- each create repairs the previous group')
    clean()
    trigger, _ = make('zz-trigger')
    client.groups.delete(trigger)
    users = [int(dict(u)['$key']) for u in client.users.list()]
    made = []
    for index in range(4):
        made.append(make('zz-roll%d' % index))
        line = '   after creating id=%-3d:' % made[-1][1]
        for position, (key, identity) in enumerate(made):
            line += '  id%d=%-8s' % (identity,
                                     probe(key, users[position % len(users)]))
        print(line)

    clean()
    print()
    print('groups:', [dict(g)['name'] for g in client.groups.list()])


if __name__ == '__main__':
    main()
