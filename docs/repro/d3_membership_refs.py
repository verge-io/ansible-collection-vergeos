#!/usr/bin/env python
"""D3 -- GroupMember.member_type/member_key only recognise one of the two
reference forms VergeOS stores, so remove_user() cannot remove any membership
the platform created.

    export VERGEOS_HOST=... VERGEOS_USERNAME=... VERGEOS_PASSWORD=...
    python docs/repro/d3_membership_refs.py

Creates and deletes one scratch group. Does NOT touch Administrators.
"""

import os

from pyvergeos import VergeClient
from pyvergeos.exceptions import NotFoundError


def main():
    client = VergeClient(
        host=os.environ['VERGEOS_HOST'],
        username=os.environ['VERGEOS_USERNAME'],
        password=os.environ['VERGEOS_PASSWORD'],
        verify_ssl=False)
    client.connect()

    print('== 1. the two forms, side by side, in the same column ==============')
    print('   Administrators (group 1), memberships created by VergeOS itself:')
    for m in client.groups.members(1).list():
        print('      member=%-12r member_type=%-9r member_key=%-5r member_name=%r'
              % (dict(m).get('member'), m.member_type, m.member_key, m.member_name))
    print()
    print("   '/users/' in 'users/1' ->", '/users/' in 'users/1')
    print('   ...which is why member_type is Unknown and member_key is None.')
    print('   member_name still works: it reads member_display, a different field.')

    print()
    print('== 2. the consequence, in a scratch group ==========================')
    group = client.groups.create(name='zz-repro-d3', description='D3 repro')
    gkey = int(dict(group)['$key'])
    ukey = int([dict(u)['$key'] for u in client.users.list()
                if dict(u)['name'] == 'labuser'][0])
    members = client.groups.members(gkey)

    # Write the membership the way the PLATFORM writes it -- the bare form.
    # add_user() posts the prefixed form, so it cannot reproduce this itself.
    client._request('POST', 'members',
                    json_data={'parent_group': gkey, 'member': 'users/%d' % ukey})
    print('   membership created, platform-style:',
          [dict(r) for r in members.list()])

    print()
    print('   asking the SDK to remove a user who IS a member:')
    try:
        members.remove_user(ukey)
        print('      removed -- defect not present')
    except NotFoundError as exc:
        print('      NotFoundError: %s' % exc)
        print('      ^ WRONG. Still a member: %s'
              % [dict(r).get('member_display') for r in members.list()])

    client.groups.delete(gkey)
    print()
    print('   cleaned up. groups:', [dict(g)['name'] for g in client.groups.list()])

    print()
    print('== 3. why this is easy to miss =====================================')
    print('   remove_user RAISES rather than silently succeeding, so it looks')
    print('   loud. But the idiomatic idempotence pattern is:')
    print()
    print('       try:')
    print('           members.remove_user(key)')
    print('       except NotFoundError:')
    print('           pass          # "already gone"')
    print()
    print('   which turns "cannot remove this user" into "nothing to do".')
    print('   Offboarding automation would report success and change nothing.')


if __name__ == '__main__':
    main()
