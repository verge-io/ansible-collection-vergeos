#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: tenant
short_description: Manage tenants in VergeOS
version_added: "2.2.0"
description:
  - Create, update, power, and delete tenants, including their node
    (compute) and storage allocations, so a tenant-per-customer layout can
    be described declaratively.
  - Tenant nodes are matched by name and storage allocations by tier, so
    repeated runs converge instead of duplicating resources.
options:
  name:
    description:
      - Name of the tenant to manage.
      - Used as the lookup key; renaming an existing tenant is not
        supported by this module.
    type: str
    required: true
  state:
    description:
      - C(present) ensures the tenant exists with the described settings,
        nodes, and storage, leaving its power state alone.
      - C(running) and C(stopped) additionally enforce the power state.
      - C(absent) powers the tenant off if needed and deletes it,
        including all tenant data.
    type: str
    choices: [ present, absent, running, stopped ]
    default: present
  description:
    description:
      - Tenant description.
    type: str
  url:
    description:
      - URL associated with the tenant.
    type: str
  note:
    description:
      - Free-form note for the tenant.
    type: str
  tenant_password:
    description:
      - Initial password for the tenant's auto-created admin user.
      - Only used at creation time; the password of an existing tenant is
        never changed by this module. If omitted on create, VergeOS
        generates a random password.
    type: str
  require_password_change:
    description:
      - Require the tenant admin to change the password on first login.
      - Only used at creation time.
    type: bool
    default: false
  expose_cloud_snapshots:
    description:
      - Allow the tenant to request cloud snapshots.
      - When omitted, new tenants get the VergeOS default and existing
        tenants are left unchanged.
    type: bool
  allow_branding:
    description:
      - Allow the tenant to customize branding.
      - When omitted, new tenants get the VergeOS default and existing
        tenants are left unchanged.
    type: bool
  nodes:
    description:
      - Desired tenant nodes (the tenant's compute allocation), matched by
        O(nodes[].name).
      - Missing nodes are created and drifted C(cpu_cores)/C(ram_gb) are
        corrected. Nodes not listed are left alone unless O(purge_nodes)
        is true.
      - C(cluster) is only applied when a node is created; moving an
        existing tenant node between clusters is not attempted.
    type: list
    elements: dict
    suboptions:
      name:
        description: Node name, the match key for idempotence.
        type: str
        required: true
      cpu_cores:
        description: CPU cores for the node.
        type: int
        default: 4
      ram_gb:
        description: RAM in GB (minimum 2).
        type: int
        default: 16
      cluster:
        description: Cluster key the node runs on (creation only).
        type: int
        default: 1
      description:
        description: Node description (creation only).
        type: str
        default: ''
  purge_nodes:
    description:
      - Delete tenant nodes that are not listed in O(nodes).
      - Ignored when O(nodes) is omitted.
    type: bool
    default: false
  storage:
    description:
      - Desired storage allocations, matched by O(storage[].tier).
      - Missing tiers are created and drifted sizes are corrected. Tiers
        not listed are left alone unless O(purge_storage) is true.
    type: list
    elements: dict
    suboptions:
      tier:
        description: Storage tier number (1-5).
        type: int
        required: true
      provisioned_gb:
        description: Provisioned size in GB (minimum 1).
        type: int
        required: true
  purge_storage:
    description:
      - Delete storage allocations for tiers not listed in O(storage).
      - Removing an allocation that holds data destroys that data.
      - Ignored when O(storage) is omitted.
    type: bool
    default: false
  wait_timeout:
    description:
      - Seconds to wait for a tenant power transition (start, stop, and
        the implicit stop before delete).
    type: int
    default: 180
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Tenant for customer acme with compute and storage quotas
  vergeio.vergeos.tenant:
    name: acme
    description: "ACME Corp production tenant"
    tenant_password: "{{ acme_initial_password }}"
    require_password_change: true
    nodes:
      - name: acme-node1
        cpu_cores: 8
        ram_gb: 32
    storage:
      - tier: 1
        provisioned_gb: 500
      - tier: 3
        provisioned_gb: 2000
    state: running

- name: Grow acme's tier 3 allocation (other tiers untouched)
  vergeio.vergeos.tenant:
    name: acme
    storage:
      - tier: 3
        provisioned_gb: 4000
    state: present

- name: Exactly two nodes, removing any others
  vergeio.vergeos.tenant:
    name: acme
    nodes:
      - name: acme-node1
        cpu_cores: 8
        ram_gb: 32
      - name: acme-node2
        cpu_cores: 8
        ram_gb: 32
    purge_nodes: true
    state: present

- name: Stop a tenant
  vergeio.vergeos.tenant:
    name: acme
    state: stopped

- name: Delete a tenant and all its data
  vergeio.vergeos.tenant:
    name: old-customer
    state: absent
'''

RETURN = r'''
tenant:
  description: State of the tenant after the module ran.
  returned: when state is not absent
  type: dict
  sample:
    key: 7
    name: "acme"
    description: "ACME Corp production tenant"
    running: true
    status: "online"
    nodes:
      - name: "acme-node1"
        cpu_cores: 8
        ram_gb: 32.0
    storage:
      - tier: 1
        provisioned_gb: 500.0
actions:
  description: Human-readable list of every change the module made (or
    would make, in check mode).
  returned: always
  type: list
  elements: str
  sample: ["created tenant 'acme'", "created node 'acme-node1'", "powered on"]
placement:
  description:
    - What the platform showed when a power-on timed out (issue #24).
    - An unplaceable tenant node is not reported as a failure anywhere; the
      tenant simply returns to stopped. C(nodes[].host_node) is empty for
      every node that was never placed, which is what distinguishes "no room
      for it" from "slow to boot".
    - This reports figures; it does not predict placement. See
      C(plugins/module_utils/clusters.py) for why no verdict is offered.
  returned: on power-on timeout
  type: dict
  contains:
    nodes:
      description: One entry per tenant node, with its host or lack of one.
      type: list
      elements: dict
      returned: always
    capacity:
      description: Cluster RAM and core figures, and per-node VM RAM.
      type: dict
      returned: when the cluster tables could be read
  sample:
    nodes:
      - name: "acme-node1"
        ram_mb: 16384
        cpu_cores: 4
        status: "stopped"
        host_node: null
    capacity:
      online_ram_mb: 137472
      used_ram_mb: 74752
'''

import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    resolve_one,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
# The rest of the field contract -- CREATE_PARAM_MAP, IDENTITY_PARAMS and
# COMPARISON_FIELDS -- lives in module_utils/tenants.py and is read from there
# by tests/unit/plugins/modules/test_field_contracts.py. Importing names here
# purely so a test can find them would be dead code in the module.
from ansible_collections.vergeio.vergeos.plugins.module_utils.tenants import (
    GB,
    NODE_FIELDS,
    STORAGE_FIELDS,
    TENANT_FIELDS,
    UPDATE_FIELD_MAP,
    tenant_key,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.clusters import (
    capacity_facts,
    describe_capacity,
    fetch_cluster_status,
    fetch_nodes,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )

POLL_INTERVAL = 5


def get_tenant(module, client, name):
    """One tenant by name, refusing to guess between duplicates (#72/#85)."""
    try:
        return resolve_one(module, client.tenants, name, 'tenant',
                           fields=TENANT_FIELDS)
    except NotFoundError:
        return None


def tenant_result(tenant):
    """Build the sanitized return payload for a tenant."""
    data = dict(tenant)
    result = {
        'key': data.get('$key'),
        'name': data.get('name'),
        'description': data.get('description'),
        'url': data.get('url'),
        'note': data.get('note'),
        'expose_cloud_snapshots': bool(data.get('expose_cloud_snapshots', False)),
        'allow_branding': bool(data.get('allow_branding', False)),
        'running': bool(data.get('running', False)),
        'status': data.get('status'),
    }
    try:
        result['nodes'] = [
            {
                'name': dict(n).get('name'),
                'cpu_cores': dict(n).get('cpu_cores'),
                'ram_gb': round(int(dict(n).get('ram') or 0) / 1024.0, 2),
            }
            for n in tenant.nodes.list(fields=NODE_FIELDS)
        ]
        result['storage'] = [
            {
                'tier': dict(s).get('tier_number'),
                'provisioned_gb': round(int(dict(s).get('provisioned') or 0) / float(GB), 2),
            }
            for s in tenant.storage.list(fields=STORAGE_FIELDS)
        ]
    except (APIError, VergeConnectionError):
        # Sub-resource reads are best-effort for the return payload
        pass
    return result


def settings_from_params(params):
    """Updatable tenant columns the user actually specified.

    Keyed off UPDATE_FIELD_MAP so the set of things this module writes and the
    set it compares cannot drift apart.
    """
    return {column: params[param]
            for param, column in UPDATE_FIELD_MAP.items()
            if params.get(param) is not None}


def create_tenant(module, client, actions):
    params = module.params
    actions.append("created tenant '%s'" % params['name'])
    if module.check_mode:
        return None

    create_args = dict(settings_from_params(params))
    create_args['name'] = params['name']
    if params.get('tenant_password'):
        create_args['password'] = params['tenant_password']
    if params.get('require_password_change'):
        # The SDK's keyword, which it renames to the `change_password` column
        # on the way out. See CREATE_PARAM_MAP.
        create_args['require_password_change'] = True
    return client.tenants.create(**create_args)


def update_tenant_settings(module, client, tenant, actions):
    desired = settings_from_params(module.params)
    current = dict(tenant)
    changes = {}
    for field, value in desired.items():
        current_value = current.get(field)
        if isinstance(value, bool):
            current_value = bool(current_value)
        if current_value != value:
            changes[field] = value

    if not changes:
        return False, tenant

    actions.append('updated settings: %s' % ', '.join(sorted(changes)))
    if module.check_mode:
        return True, tenant
    # Direct PUT via the manager; ResourceObject.save() without kwargs
    # sends an empty body and persists nothing.
    tenant = client.tenants.update(tenant_key(tenant), **changes)
    return True, tenant


def reconcile_nodes(module, tenant, actions):
    desired = module.params.get('nodes')
    if desired is None:
        return False

    changed = False
    existing = {} if tenant is None else {
        dict(n).get('name'): n for n in tenant.nodes.list(fields=NODE_FIELDS)
    }

    for want in desired:
        want_ram_mb = want['ram_gb'] * 1024
        have = existing.get(want['name'])
        if have is None:
            actions.append("created node '%s'" % want['name'])
            changed = True
            if not module.check_mode:
                tenant.nodes.create(
                    cpu_cores=want['cpu_cores'],
                    ram_mb=want_ram_mb,
                    cluster=want['cluster'],
                    name=want['name'],
                    description=want['description'],
                )
            continue

        have_data = dict(have)
        updates = {}
        if int(have_data.get('cpu_cores') or 0) != want['cpu_cores']:
            updates['cpu_cores'] = want['cpu_cores']
        if int(have_data.get('ram') or 0) != want_ram_mb:
            updates['ram'] = want_ram_mb
        if updates:
            actions.append("updated node '%s': %s"
                           % (want['name'], ', '.join(sorted(updates))))
            changed = True
            if not module.check_mode:
                tenant.nodes.update(have_data['$key'], **updates)

    if module.params['purge_nodes']:
        wanted_names = {want['name'] for want in desired}
        for name, have in existing.items():
            if name not in wanted_names:
                actions.append("deleted node '%s'" % name)
                changed = True
                if not module.check_mode:
                    tenant.nodes.delete(dict(have)['$key'])

    return changed


def reconcile_storage(module, tenant, actions):
    desired = module.params.get('storage')
    if desired is None:
        return False

    changed = False
    existing = {} if tenant is None else {
        int(dict(s).get('tier_number') or 0): s
        for s in tenant.storage.list(fields=STORAGE_FIELDS)
    }

    for want in desired:
        want_bytes = want['provisioned_gb'] * GB
        have = existing.get(want['tier'])
        if have is None:
            actions.append('created tier %d storage (%d GB)'
                           % (want['tier'], want['provisioned_gb']))
            changed = True
            if not module.check_mode:
                tenant.storage.create(
                    tier=want['tier'],
                    provisioned_gb=want['provisioned_gb'],
                )
            continue

        if int(dict(have).get('provisioned') or 0) != want_bytes:
            actions.append('resized tier %d storage to %d GB'
                           % (want['tier'], want['provisioned_gb']))
            changed = True
            if not module.check_mode:
                tenant.storage.update_by_tier(
                    want['tier'], provisioned_bytes=want_bytes)

    if module.params['purge_storage']:
        wanted_tiers = {want['tier'] for want in desired}
        for tier in existing:
            if tier not in wanted_tiers:
                actions.append('deleted tier %d storage' % tier)
                changed = True
                if not module.check_mode:
                    tenant.storage.delete_by_tier(tier)

    return changed


def placement_report(module, client, key):
    """Why a tenant that was asked to start is still stopped (#24).

    The platform does not report an unplaceable tenant node as a failure. It
    goes Power On -> Initializing -> Starting -> Stopped, and leaves the node
    with no host: no alarm, no task failure, no status_info, and log rows that
    only restate the transitions. A bare "timed out" repeats that silence back
    at the operator.

    This reads the two things that ARE visible -- which nodes were given a
    host and which were not, and the cluster's own capacity figures -- and
    returns them as text plus a structured dict.

    It deliberately does NOT say whether the node SHOULD have fit. Issue #24
    proposed N-1 headroom as the rule; measuring again on 26.1.8 refuted it in
    both directions (see plugins/module_utils/clusters.py). Asserting a
    mechanism that is wrong would be worse than the silence it replaces.
    """
    detail = {'nodes': [], 'capacity': {}}
    lines = []

    try:
        nodes = [dict(n) for n in
                 client.tenants.nodes(key).list(fields=NODE_FIELDS)]
    except (APIError, VergeConnectionError, NotFoundError):
        return '', detail

    for node in nodes:
        detail['nodes'].append({
            'name': node.get('name'),
            'ram_mb': int(node.get('ram') or 0),
            'cpu_cores': int(node.get('cpu_cores') or 0),
            'status': node.get('status'),
            'host_node': node.get('host_node'),
        })

    unplaced = [n for n in detail['nodes'] if not n['host_node']]
    if unplaced:
        lines.append(
            'never given a physical host: %s. A tenant node with no host was '
            'not placed at all, which is different from placed and slow to '
            'boot -- and the platform reports no reason for it (issue #24).'
            % ', '.join("'%s' (%d MB, %d cores)"
                        % (n['name'], n['ram_mb'], n['cpu_cores'])
                        for n in unplaced))
    placed = [n for n in detail['nodes'] if n['host_node']]
    if placed:
        lines.append('placed: %s'
                     % ', '.join("'%s' on %s (%s)"
                                 % (n['name'], n['host_node'], n['status'])
                                 for n in placed))

    try:
        facts = capacity_facts(fetch_cluster_status(client), fetch_nodes(client))
        detail['capacity'] = facts
        lines.append(describe_capacity(facts))
    except (APIError, VergeConnectionError, NotFoundError):
        lines.append('cluster capacity figures could not be read')

    return ' '.join(lines), detail


def wait_for_power(module, client, key, want_running):
    deadline = time.time() + module.params['wait_timeout']
    while True:
        tenant = client.tenants.get(key, fields=TENANT_FIELDS)
        if bool(dict(tenant).get('running', False)) == want_running:
            return tenant
        if time.time() >= deadline:
            msg = ("Timed out after %ds waiting for tenant to be %s"
                   % (module.params['wait_timeout'],
                      'running' if want_running else 'stopped'))
            detail = {}
            if want_running:
                report, detail = placement_report(module, client, key)
                if report:
                    msg = '%s. %s' % (msg, report)
            module.fail_json(msg=msg, placement=detail)
        time.sleep(POLL_INTERVAL)


def ensure_power(module, client, tenant, want_running, actions):
    if bool(dict(tenant).get('running', False)) == want_running:
        return False, tenant

    actions.append('powered on' if want_running else 'powered off')
    if module.check_mode:
        return True, tenant

    key = tenant_key(tenant)
    if want_running:
        client.tenants.power_on(key)
    else:
        client.tenants.power_off(key)
    tenant = wait_for_power(module, client, key, want_running)
    return True, tenant


def ensure_absent(module, client, tenant, actions):
    if tenant is None:
        module.exit_json(changed=False, actions=actions,
                         msg="Tenant '%s' does not exist" % module.params['name'])

    if bool(dict(tenant).get('is_snapshot', False)):
        module.fail_json(msg="'%s' is a tenant snapshot; this module does not "
                             'manage snapshots' % module.params['name'])

    tenant = ensure_power(module, client, tenant, False, actions)[1]
    actions.append("deleted tenant '%s'" % module.params['name'])
    if not module.check_mode:
        tenant.delete()
    module.exit_json(changed=True, actions=actions)


def ensure_present(module, client, tenant, actions):
    state = module.params['state']
    changed = False

    if tenant is None:
        tenant = create_tenant(module, client, actions)
        changed = True
    else:
        if bool(dict(tenant).get('is_snapshot', False)):
            module.fail_json(msg="'%s' is a tenant snapshot; this module does "
                                 'not manage snapshots' % module.params['name'])
        settings_changed, tenant = update_tenant_settings(
            module, client, tenant, actions)
        changed = changed or settings_changed

    # In check mode on a new tenant there is nothing to reconcile against;
    # record the planned sub-resources instead.
    if tenant is None:
        for want in module.params.get('nodes') or []:
            actions.append("created node '%s'" % want['name'])
        for want in module.params.get('storage') or []:
            actions.append('created tier %d storage (%d GB)'
                           % (want['tier'], want['provisioned_gb']))
        if state == 'running':
            actions.append('powered on')
        module.exit_json(changed=True, actions=actions,
                         tenant={'name': module.params['name']})

    changed = reconcile_nodes(module, tenant, actions) or changed
    changed = reconcile_storage(module, tenant, actions) or changed

    if state in ('running', 'stopped'):
        power_changed, tenant = ensure_power(
            module, client, tenant, state == 'running', actions)
        changed = changed or power_changed

    module.exit_json(changed=changed, actions=actions,
                     tenant=tenant_result(tenant))


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent', 'running', 'stopped']),
        description=dict(type='str'),
        url=dict(type='str'),
        note=dict(type='str'),
        tenant_password=dict(type='str', no_log=True),
        # no_log=False is deliberate and required. ansible-core warns on any
        # option whose NAME looks like a secret -- "Module did not set no_log
        # for require_password_change" on every single run -- and the only way
        # to say "considered, it is a boolean flag" is to say so explicitly.
        require_password_change=dict(type='bool', default=False, no_log=False),
        expose_cloud_snapshots=dict(type='bool'),
        allow_branding=dict(type='bool'),
        nodes=dict(
            type='list', elements='dict',
            options=dict(
                name=dict(type='str', required=True),
                cpu_cores=dict(type='int', default=4),
                ram_gb=dict(type='int', default=16),
                cluster=dict(type='int', default=1),
                description=dict(type='str', default=''),
            ),
        ),
        purge_nodes=dict(type='bool', default=False),
        storage=dict(
            type='list', elements='dict',
            options=dict(
                tier=dict(type='int', required=True),
                provisioned_gb=dict(type='int', required=True),
            ),
        ),
        purge_storage=dict(type='bool', default=False),
        wait_timeout=dict(type='int', default=180),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    actions = []

    try:
        tenant = get_tenant(module, client, module.params['name'])

        if module.params['state'] == 'absent':
            ensure_absent(module, client, tenant, actions)
        else:
            ensure_present(module, client, tenant, actions)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
