#!/usr/bin/env bash
# D1 -- pyvergeos escapes apostrophes in a form VergeOS rejects.
#
#   export VERGEOS_HOST=... VERGEOS_USERNAME=... VERGEOS_PASSWORD=...
#   bash docs/repro/d1_name_escaping.sh
#
# Read-only except for one scratch group, which is deleted at the end.
set -u
U="$VERGEOS_USERNAME:$VERGEOS_PASSWORD"
B="https://$VERGEOS_HOST/api/v4"

echo "== 1. raw HTTP, four controls =========================================="
probe() {
  printf '  %-34s ' "$1"
  curl -sk -m 15 -u "$U" -G "$B/vnets" \
       --data-urlencode "fields=name" --data-urlencode "filter=$2" \
       -w '  [HTTP %{http_code}]\n'
}
probe "exists"                 "name eq 'DMZ'"
probe "absent, plain"          "name eq 'zz-nope'"
probe "absent, DOUBLED quote"  "name eq 'zz-o''brien'"
probe "absent, BACKSLASH"      "name eq 'zz-o\'brien'"

echo
echo "  Expected: rows 1-2 and 4 are HTTP 200. Row 3 -- the form pyvergeos"
echo "  builds -- is HTTP 422 {\"err\":\"Invalid argument\"}."

echo
echo "== 2. what pyvergeos builds (offline) =================================="
python -c "
from pyvergeos.filters import build_filter
print('   build_filter(name=\"zz-o\'brien\") ->', build_filter(name=\"zz-o'brien\"))"

echo
echo "== 3. through the SDK =================================================="
python - <<'PY'
import os
from pyvergeos import VergeClient
from pyvergeos.exceptions import NotFoundError, ValidationError
c = VergeClient(host=os.environ['VERGEOS_HOST'], username=os.environ['VERGEOS_USERNAME'],
                password=os.environ['VERGEOS_PASSWORD'], verify_ssl=False)
c.connect()

# It can CREATE a name with an apostrophe...
g = c.groups.create(name="zz-claude-o'brien", description='D1 repro scratch')
gk = dict(g)['$key']
print("   created group %r" % dict(g)['name'])

# ...but cannot READ IT BACK.
try:
    c.groups.get(name="zz-claude-o'brien")
    print("   get(name=...) -> found  (defect not present)")
except ValidationError as e:
    print("   get(name=...) -> ValidationError: %s   <-- D1" % e)
except NotFoundError as e:
    print("   get(name=...) -> NotFoundError: %s" % e)

# The backslash form does find it.
rows = c._request('GET', 'groups',
                  params={'fields': 'name', 'filter': "name eq 'zz-claude-o\\'brien'"})
print("   backslash filter -> %r" % rows)

c.groups.delete(gk)
print("   cleaned up")
PY

echo
echo "== 4. the same defect via other managers ==============================="
python - <<'PY'
import os
from pyvergeos import VergeClient
c = VergeClient(host=os.environ['VERGEOS_HOST'], username=os.environ['VERGEOS_USERNAME'],
                password=os.environ['VERGEOS_PASSWORD'], verify_ssl=False)
c.connect()
for mgr in ('users', 'groups', 'vms', 'networks'):
    try:
        getattr(c, mgr).get(name="zz-o'brien")
        print("   %-9s -> unexpectedly succeeded" % mgr)
    except Exception as e:
        print("   %-9s -> %s: %s" % (mgr, type(e).__name__, e))
PY
