#!/usr/bin/env bash
# D2 -- pyvergeos discards the body of any non-2xx reply, including the
#       recipe simulate, which answers HTTP 405 WITH the whole report.
#
#   export VERGEOS_HOST=... VERGEOS_USERNAME=... VERGEOS_PASSWORD=...
#   bash docs/repro/d2_discarded_body.sh
#
# Read-only: a simulate is a dry run. Verified at the end.
set -u
U="$VERGEOS_USERNAME:$VERGEOS_PASSWORD"
B="https://$VERGEOS_HOST/api/v4"

# A recipe key is a hash string, so it MUST be quoted in the JSON body.
KEY=$(curl -sk -m 20 -u "$U" "$B/vm_recipes?fields=%24key,name" | python -c "
import json,sys
for r in json.load(sys.stdin):
    if r.get('name','').startswith('Ubuntu Server 22.04'):
        print(r['\$key']); break")

# A network answer must be the vnet KEY, not its name.
NET=$(curl -sk -m 20 -u "$U" "$B/vnets?fields=%24key,name" | python -c "
import json,sys
for r in json.load(sys.stdin):
    if r.get('name')=='External': print(r['\$key']); break")

echo "recipe=$KEY  vnet=$NET"

python -c "
import json,sys
print(json.dumps({'recipe':sys.argv[1],'name':'zz-repro-d2','simulate':True,
 'answers':{'HOSTNAME':'zz-repro-d2','USER':'ops','PASSWORD':'Lab-Only-1234',
            'YB_CPU_CORES':1,'YB_RAM':1024,'YB_NIC_ETH0':int(sys.argv[2]),
            'SELECT_OS_TIER':1}}))" "$KEY" "$NET" > /tmp/d2body.json

echo
echo "== 1. raw HTTP =========================================================="
curl -sk -m 90 -u "$U" -X POST -H 'Content-Type: application/json' \
     --data @/tmp/d2body.json -o /tmp/d2.json \
     -w '  HTTP STATUS: %{http_code}   <-- not a 2xx\n' \
     "$B/vm_recipe_instances"
python -c "
import json
d = json.load(open('/tmp/d2.json'))
r = d.get('response') or {}
print('  err          : %r' % d.get('err'))
print('  response keys: %s' % sorted(r.keys()))
print('  log entries  : %d' % len(r.get('logs') or []))
print('  cloudinit    : %s' % [f.get('name') for f in r.get('cloudinit_files') or []])
print('  new VM key   : %s' % (r.get('answers') or {}).get('YB_VM_KEY'))"

echo
echo "== 2. the identical call through pyvergeos ============================="
python - <<'PY'
import os, json
from pyvergeos import VergeClient
from pyvergeos.exceptions import APIError
c = VergeClient(host=os.environ['VERGEOS_HOST'], username=os.environ['VERGEOS_USERNAME'],
                password=os.environ['VERGEOS_PASSWORD'], verify_ssl=False)
c.connect()
body = json.load(open('/tmp/d2body.json'))
body['name'] = body['answers']['HOSTNAME'] = 'zz-repro-d2-sdk'
try:
    print('  returned:', c._request('POST', 'vm_recipe_instances', json_data=body))
except APIError as e:
    print('  raised  : %s' % type(e).__name__)
    print('  str(e)  : %r' % str(e))
    print('  status  : %s' % e.status_code)
    print('  attrs   : %s' % [a for a in dir(e) if not a.startswith('__')])
    print('  -> no body anywhere. The 28-entry log is gone.')

import inspect
print()
print("  and create() cannot even request a simulate:")
print("  'simulate' in create() source:",
      'simulate' in inspect.getsource(c.vm_recipe_instances.create))
PY

echo
echo "== 3. confirm nothing was created ======================================"
curl -sk -m 20 -u "$U" "$B/vms?fields=most" | python -c "
import json,sys
print('  zz-repro VMs:', [v['name'] for v in json.load(sys.stdin) if 'zz-repro' in v['name']] or 'none')"
