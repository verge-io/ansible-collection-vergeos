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
version_added: "2.1.0"
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
'''

import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )

GB = 1073741824
POLL_INTERVAL = 5


def get_tenant(client, name):
    """Get tenant by name, or None."""
    try:
        return client.tenants.get(name=name)
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
            for n in tenant.nodes.list()
        ]
        result['storage'] = [
            {
                'tier': dict(s).get('tier_number'),
                'provisioned_gb': round(int(dict(s).get('provisioned') or 0) / float(GB), 2),
            }
            for s in tenant.storage.list()
        ]
    except (APIError, VergeConnectionError):
        # Sub-resource reads are best-effort for the return payload
        pass
    return result


def settings_from_params(params):
    """Updatable tenant fields the user actually specified."""
    settings = {}
    for field in ('description', 'url', 'note'):
        if params.get(field) is not None:
            settings[field] = params[field]
    for field in ('expose_cloud_snapshots', 'allow_branding'):
        if params.get(field) is not None:
            settings[field] = params[field]
    return settings


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
    tenant = client.tenants.update(tenant.key, **changes)
    return True, tenant


def reconcile_nodes(module, tenant, actions):
    desired = module.params.get('nodes')
    if desired is None:
        return False

    changed = False
    existing = {} if tenant is None else {
        dict(n).get('name'): n for n in tenant.nodes.list()
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
        int(dict(s).get('tier_number') or 0): s for s in tenant.storage.list()
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


def wait_for_power(module, client, tenant_key, want_running):
    deadline = time.time() + module.params['wait_timeout']
    while True:
        tenant = client.tenants.get(tenant_key)
        if bool(dict(tenant).get('running', False)) == want_running:
            return tenant
        if time.time() >= deadline:
            module.fail_json(
                msg="Timed out after %ds waiting for tenant to be %s"
                    % (module.params['wait_timeout'],
                       'running' if want_running else 'stopped'))
        time.sleep(POLL_INTERVAL)


def ensure_power(module, client, tenant, want_running, actions):
    if bool(dict(tenant).get('running', False)) == want_running:
        return False, tenant

    actions.append('powered on' if want_running else 'powered off')
    if module.check_mode:
        return True, tenant

    key = dict(tenant)['$key']
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

    _, tenant = ensure_power(module, client, tenant, False, actions)
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
        require_password_change=dict(type='bool', default=False),
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
        tenant = get_tenant(client, module.params['name'])

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
