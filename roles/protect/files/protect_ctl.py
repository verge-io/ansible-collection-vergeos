"""protect_ctl — scan/assign helper for the protect tag reconciler.

Verbs:
  scan                      one bulk pass: every VM's protect-class tag
                            (category 'protect') and its snapshot_profile
                            field, plus profile name->key map. JSON out.
  assign --vm-key K --profile-key P
                            PUT the VM's snapshot_profile and read back;
                            exits 2 loudly if the value did not persist.
  clear --vm-key K          set snapshot_profile back to none.

The protect role now assigns profiles via the collection's `vm` module
(`snapshot_profile` param, added 2026-08-26 on branch
feature/vm-snapshot-profile) — `assign`/`clear` remain here as
break-glass CLI verbs only. Auth: VERGEOS_* env (token preferred).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

TAG_CATEGORY = 'protect'


def client():
    import urllib3
    urllib3.disable_warnings()
    from pyvergeos import VergeClient
    kwargs = {'host': os.environ['VERGEOS_HOST'],
              'verify_ssl': os.environ.get('VERGEOS_INSECURE', '') not in
              ('1', 'true', 'True')}
    if os.environ.get('VERGEOS_TOKEN'):
        kwargs['token'] = os.environ['VERGEOS_TOKEN']
    else:
        kwargs['username'] = os.environ['VERGEOS_USERNAME']
        kwargs['password'] = os.environ['VERGEOS_PASSWORD']
    return VergeClient(**kwargs)


def scan(c):
    # protect-category tags: tag key -> class name
    tag_class = {}
    for t in (dict(t) for t in c.tags.list()):
        if (t.get('category_name') or '') == TAG_CATEGORY:
            tag_class[t.get('$key')] = t.get('name')

    # tag membership (one bulk call; member format "vms/<key>")
    vm_class = {}
    if tag_class:
        for m in c._request('GET', 'tag_members', params={'fields': 'most'}):
            member = str(m.get('member', ''))
            if m.get('tag') in tag_class and member.startswith('vms/'):
                vm_class[int(member.split('/', 1)[1])] = tag_class[m['tag']]

    profiles = {dict(p).get('name'): dict(p).get('$key')
                for p in c.snapshot_profiles.list()}

    vms = []
    for row in c._request('GET', 'vms',
                          params={'fields': '$key,name,is_snapshot,'
                                  'snapshot_profile'}):
        if row.get('is_snapshot'):
            continue
        key = row.get('$key')
        vms.append({'key': key, 'name': row.get('name'),
                    'protect_class': vm_class.get(key),
                    'snapshot_profile': row.get('snapshot_profile') or ''})
    return {'vms': vms, 'profiles': profiles}


def put_profile(c, vm_key, value):
    c.vms.update(vm_key, snapshot_profile=value)
    row = c._request('GET', 'vms/%d' % vm_key,
                     params={'fields': 'snapshot_profile'})
    return str(row.get('snapshot_profile') or '')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('verb', choices=['scan', 'assign', 'clear'])
    p.add_argument('--vm-key', type=int)
    p.add_argument('--profile-key', type=int)
    args = p.parse_args()
    c = client()

    if args.verb == 'scan':
        json.dump(scan(c), sys.stdout, indent=2)
        print()
        return 0

    if args.vm_key is None:
        p.error('--vm-key required')
    if args.verb == 'assign':
        if args.profile_key is None:
            p.error('--profile-key required')
        got = put_profile(c, args.vm_key, args.profile_key)
        if got != str(args.profile_key):
            print('assignment did not persist: wrote %r, read back %r'
                  % (args.profile_key, got), file=sys.stderr)
            return 2
    else:  # clear
        got = put_profile(c, args.vm_key, '')
        if got not in ('', 'None', '0'):
            print('clear did not persist: read back %r' % got,
                  file=sys.stderr)
            return 2
    json.dump({'vm_key': args.vm_key, 'snapshot_profile': got}, sys.stdout)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
