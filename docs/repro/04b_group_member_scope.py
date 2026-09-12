#!/usr/bin/env python3
"""Is the post-delete defect specific to groups, or does every object type have it?

For each parent object type: create P, delete P, create P' immediately, then
insert a child row that points at P'. Compared against a control run with no
preceding delete. Fifteen parent/child pairs across nine object types.

Raw HTTP, Python standard library only -- no pyvergeos.

    export VERGEOS_HOST=192.168.1.10
    export VERGEOS_USERNAME=admin
    export VERGEOS_PASSWORD=...
    python3 docs/repro/04b_group_member_scope.py

Creates and deletes scratch objects named zz-*, including a VM, a vnet and a
tenant, and removes every one of them. Allow about six minutes.
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

QUIET = 8
ENOENT = "No such file or directory"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HOST + "/api/v4/" + path.lstrip("/"),
                                 data=data, method=method)
    req.add_header("Authorization", "Basic " + AUTH)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as resp:
            raw, status = resp.read().decode(), resp.status
    except urllib.error.HTTPError as err:
        raw, status = err.read().decode(), err.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def verdict(status, doc):
    if status in (200, 201):
        return "OK"
    if ENOENT in str(doc) and "error setting field" in str(doc):
        return "DEFECT"
    return "%s %s" % (status, str(doc)[:70])


def mk(table, body):
    status, doc = call("POST", table, body)
    if status != 201:
        raise RuntimeError("%s create failed: %s %s" % (table, status, doc))
    return doc["$key"]


def rm(table, key):
    call("DELETE", "%s/%s" % (table, key))


def sweep(*tables):
    for table in tables:
        rows = call("GET", "%s?fields=$key,name" % table)[1]
        for row in rows if isinstance(rows, list) else []:
            if str(row.get("name", "")).startswith("zz-"):
                rm(table, row["$key"])


USER_KEY = int(call("GET", "users?fields=$key,name")[1][0]["$key"])
ADMIN_GROUP = 1


def identity(key):
    return call("GET", "groups/%s?fields=$key,identity" % key)[1]["identity"]


def vm_machine(key):
    return call("GET", "vms/%s?fields=$key,machine" % key)[1]["machine"]


def view_vnet(key):
    return call("GET", "vnet_dns_views/%s?fields=$key,vnet" % key)[1]["vnet"]


def sweep_perms():
    for row in call("GET", "permissions?fields=$key")[1]:
        if int(row["$key"]) > 6:
            rm("permissions", row["$key"])


# label, parent table, make parent(name)->key, make child(pkey)->(status,doc),
# child table to clean up, extra cleanup
PAIRS = [
    ("groups -> members (user into the group)", "groups",
     lambda n: mk("groups", {"name": n}),
     lambda k: call("POST", "members",
                    {"parent_group": k, "member": "users/%d" % USER_KEY}),
     "members", lambda: sweep("groups")),

    ("groups -> permissions", "groups",
     lambda n: mk("groups", {"name": n}),
     lambda k: call("POST", "permissions",
                    {"identity": identity(k), "table": "/", "row": 0,
                     "list": True, "read": True}),
     "permissions", lambda: (sweep_perms(), sweep("groups"))),

    ("groups -> members (group AS a member elsewhere)", "groups",
     lambda n: mk("groups", {"name": n}),
     lambda k: call("POST", "members",
                    {"parent_group": ADMIN_GROUP, "member": "groups/%s" % k}),
     "members", lambda: sweep("groups")),

    ("users -> user_api_keys", "users",
     lambda n: mk("users", {"name": n, "password": "Zz-Temp-1234!"}),
     lambda k: call("POST", "user_api_keys", {"user": k, "name": "zz-key"}),
     "user_api_keys", lambda: sweep("user_api_keys", "users")),

    ("users -> members (user into an existing group)", "users",
     lambda n: mk("users", {"name": n, "password": "Zz-Temp-1234!"}),
     lambda k: call("POST", "members",
                    {"parent_group": ADMIN_GROUP, "member": "users/%s" % k}),
     "members", lambda: sweep("users")),

    ("tag_categories -> tags", "tag_categories",
     lambda n: mk("tag_categories", {"name": n}),
     lambda k: call("POST", "tags", {"category": k, "name": "zz-tag"}),
     "tags", lambda: sweep("tags", "tag_categories")),

    ("snapshot_profiles -> periods", "snapshot_profiles",
     lambda n: mk("snapshot_profiles", {"name": n}),
     lambda k: call("POST", "snapshot_profile_periods",
                    {"profile": k, "name": "zz-per",
                     "frequency": "daily", "retention": 86400}),
     "snapshot_profile_periods",
     lambda: sweep("snapshot_profile_periods", "snapshot_profiles")),

    ("vnets -> vnet_rules", "vnets",
     lambda n: mk("vnets", {"name": n, "type": "internal"}),
     lambda k: call("POST", "vnet_rules",
                    {"vnet": k, "name": "zz-rule",
                     "action": "accept", "direction": "incoming"}),
     "vnet_rules", lambda: sweep("vnet_rules", "vnets")),

    ("vnets -> vnet_dns_views", "vnets",
     lambda n: mk("vnets", {"name": n, "type": "internal"}),
     lambda k: call("POST", "vnet_dns_views", {"vnet": k, "name": "zz-view"}),
     "vnet_dns_views", lambda: sweep("vnet_dns_views", "vnets")),

    ("vnets -> vnet_addresses", "vnets",
     lambda n: mk("vnets", {"name": n, "type": "internal"}),
     lambda k: call("POST", "vnet_addresses",
                    {"vnet": k, "ip": "192.168.0.222", "type": "static",
                     "mac": "f0:db:30:11:22:33"}),
     "vnet_addresses", lambda: sweep("vnets")),

    ("vnet_dns_views -> vnet_dns_zones", "vnet_dns_views",
     lambda n: mk("vnet_dns_views",
                  {"vnet": mk("vnets", {"name": "zz-net-%s" % n,
                                        "type": "internal"}), "name": n}),
     lambda k: call("POST", "vnet_dns_zones",
                    {"vnet": view_vnet(k), "view": k,
                     "name": "zz.example", "type": "master"}),
     "vnet_dns_zones", lambda: sweep("vnet_dns_views", "vnets")),

    ("vms -> machine_nics", "vms",
     lambda n: mk("vms", {"name": n, "cluster": 1, "cpu_cores": 1, "ram": 512}),
     lambda k: call("POST", "machine_nics",
                    {"machine": vm_machine(k), "name": "zz-nic",
                     "interface": "virtio", "vnet": 3}),
     "machine_nics", lambda: sweep("vms")),

    ("vms -> machine_drives", "vms",
     lambda n: mk("vms", {"name": n, "cluster": 1, "cpu_cores": 1, "ram": 512}),
     lambda k: call("POST", "machine_drives",
                    {"machine": vm_machine(k), "name": "zz-drv",
                     "interface": "virtio", "media": "disk",
                     "disksize": 1073741824}),
     "machine_drives", lambda: sweep("vms")),

    ("tenants -> tenant_storage", "tenants",
     lambda n: mk("tenants", {"name": n}),
     lambda k: call("POST", "tenant_storage",
                    {"tenant": k, "tier": 1, "provisioned": 10737418240}),
     "tenant_storage", lambda: sweep("tenants")),
]

print("%-48s %-8s %s" % ("parent -> child", "control", "after delete+create"))
print("-" * 84)
for label, parent, mkparent, mkchild, child_table, clean in PAIRS:
    results = []
    for arm in (False, True):
        clean()
        time.sleep(QUIET)
        if arm:
            rm(parent, mkparent("zz-trigger"))
        key = mkparent("zz-victim")
        status, doc = mkchild(key)
        out = verdict(status, doc)
        if out == "OK" and child_table:
            rm(child_table, doc["$key"])
        rm(parent, key)
        results.append(out)
    flag = "  <-- REPRODUCES" if results[1] == "DEFECT" else ""
    print("%-48s %-8s %s%s" % (label, results[0], results[1], flag))
    clean()

# The decisive one: the same group, in the same second, referenced from two
# different columns of the same table.
print()
print("one armed group, two columns of the members table")
sweep("groups")
time.sleep(QUIET)
rm("groups", mk("groups", {"name": "zz-trigger"}))
victim = mk("groups", {"name": "zz-victim"})

status, doc = call("POST", "members",
                   {"parent_group": ADMIN_GROUP, "member": "groups/%s" % victim})
print("  as members.member (a member of another group) : %s" % verdict(status, doc))
if status == 201:
    rm("members", doc["$key"])

status, doc = call("POST", "members",
                   {"parent_group": victim, "member": "users/%d" % USER_KEY})
print("  as members.group  (the group holding members) : %s" % verdict(status, doc))
if status == 201:
    rm("members", doc["$key"])

rm("groups", victim)
print()
print("groups left :", [g["name"] for g in call("GET", "groups?fields=$key,name")[1]])
print("members left:", call("GET", "members?fields=all")[1])
