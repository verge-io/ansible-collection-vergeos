#!/usr/bin/env python3
"""VergeOS: a group created just after a group delete can never take members.

Raw HTTP, Python standard library only -- no pyvergeos, no pip install. This
exists so the platform team can reproduce the defect without the SDK anywhere
in the picture.

    export VERGEOS_HOST=192.168.1.10
    export VERGEOS_USERNAME=admin
    export VERGEOS_PASSWORD=...
    python3 docs/repro/04_group_member_defect_http.py

Creates and deletes scratch groups named zz-*. Adds a member and removes it
again. Touches nothing else. Takes about four minutes, almost all of it
waiting for the system to go quiet between runs.
"""

import base64
import json
import os
import ssl
import time
import urllib.error
import urllib.request

HOST = "https://" + os.environ["VERGEOS_HOST"]
AUTH = base64.b64encode(
    ("%s:%s" % (os.environ["VERGEOS_USERNAME"],
                os.environ["VERGEOS_PASSWORD"])).encode()).decode()
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

MARKER = "error setting field 'members.group'"
QUIET = 8          # seconds of no group writes before each run
SETTLE = 6.0       # the workaround: pause after a group delete


def call(method, path, body=None):
    """(status, parsed body). Does not raise on 4xx/5xx -- the errors are the point."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HOST + "/api/v4/" + path.lstrip("/"),
                                 data=data, method=method)
    req.add_header("Authorization", "Basic " + AUTH)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as resp:
            raw, status = resp.read().decode(), resp.status
    except urllib.error.HTTPError as err:
        raw, status = err.read().decode(), err.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


USERS = [int(u["$key"]) for u in call("GET", "users?fields=$key,name")[1]]


def mk(name):
    return int(call("POST", "groups", {"name": name})[1]["$key"])


def rm(key):
    call("DELETE", "groups/%d" % key)


def probe(key, user=None):
    """Try to add a member, then undo it so the user can be probed again."""
    status, doc = call("POST", "members",
                       {"parent_group": key,
                        "member": "users/%d" % (user or USERS[0])})
    if status == 201:
        call("DELETE", "members/%s" % doc["$key"])
        return "OK"
    return "DEFECT" if MARKER in str(doc) else str(status)


def quiet():
    for group in call("GET", "groups?fields=$key,name")[1]:
        if group["name"].startswith("zz-"):
            rm(int(group["$key"]))
    time.sleep(QUIET)


def arm():
    """Do the one thing that triggers it, and return the group it lands on."""
    trigger = mk("zz-trigger")
    rm(trigger)
    return mk("zz-victim")


print("1. only a group DELETE arms it")
cases = (
    ("create Y",                              lambda: mk("zz-y")),
    ("create X, create Y",                    lambda: (mk("zz-x"), mk("zz-y"))[1]),
    ("create X, DELETE X, create Y",          lambda: arm()),
    ("create X, wait 4s, create Y",           lambda: (mk("zz-x"), time.sleep(4), mk("zz-y"))[2]),
    ("create X, DELETE X, wait 4s, create Y", lambda: (rm(mk("zz-x")), time.sleep(4), mk("zz-y"))[2]),
)
for label, build in cases:
    quiet()
    print("   %-40s -> %s" % (label, probe(build())))

print()
print("2. the window, by bisection")
for wait in (0, 1, 2, 3, 4, 5, 6):
    quiet()
    rm(mk("zz-trigger"))
    time.sleep(wait)
    print("   wait %ss -> %s" % (wait, probe(mk("zz-victim"))))

print()
print("3. it never heals on its own")
for wait in (0, 5, 30):
    quiet()
    victim = arm()
    time.sleep(wait)
    print("   probed %2ss later -> %s" % (wait, probe(victim)))

print()
print("4. several groups inside one window -- the LAST one is the affected one")
quiet()
victim = arm()
later = mk("zz-later")
print("   victim (first in the window) -> %s" % probe(victim, USERS[0]))
print("   later  (last in the window)  -> %s" % probe(later, USERS[1 % len(USERS)]))

print()
print("5. a later group MASKS the fault -- it does not repair it")
quiet()
victim = arm()
print("   victim, straight away        -> %s" % probe(victim, USERS[0]))
time.sleep(10)
later = mk("zz-later")
print("   victim, after one new group  -> %s" % probe(victim, USERS[1 % len(USERS)]))
print("   the new group itself         -> %s" % probe(later, USERS[2 % len(USERS)]))
rm(later)
print("   victim, after deleting it    -> %s" % probe(victim, USERS[0]))
print("   -> the fault was hidden, not fixed")

print()
print("6. the window is timed from the DELETE, not restarted by each create")
quiet()
rm(mk("zz-trigger"))
time.sleep(2)
first = mk("zz-first")
time.sleep(2.5)
second = mk("zz-second")
# only B matters here: it is 4.5s after the delete (outside the window) but
# 2.5s after A (inside one, if creates restarted the clock). B is fine.
print("   A, 2.0s after the delete        -> %s" % probe(first, USERS[0]))
print("   B, 4.5s after it, 2.5s after A  -> %s" % probe(second, USERS[1 % len(USERS)]))

print()
print("7. the workaround: delete it, wait %gs, create it again" % SETTLE)
quiet()
victim = arm()
print("   first attempt   -> %s" % probe(victim))
rm(victim)
time.sleep(SETTLE)
victim = mk("zz-victim")
print("   after rebuild   -> %s" % probe(victim))
time.sleep(10)
rm(mk("zz-churn"))
print("   after later churn -> %s" % probe(victim, USERS[1 % len(USERS)]))
print("   -> a genuine fix, unlike masking")

quiet()
print()
print("groups left :", [g["name"] for g in call("GET", "groups?fields=$key,name")[1]])
print("members left:", call("GET", "members?fields=all")[1])
