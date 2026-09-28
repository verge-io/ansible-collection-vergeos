#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: user
short_description: Manage users in VergeOS
version_added: "1.0.0"
description:
  - Create, update, and delete user accounts in VergeOS.
options:
  name:
    description:
      - The username for the user account to manage.
    type: str
    required: true
  state:
    description:
      - The desired state of the user.
    type: str
    choices: [ present, absent ]
    default: present
  user_password:
    description:
      - Password for the user account.
      - Required when creating a new user.
      - >-
        Whether an existing user's password is rewritten is governed by
        O(update_password).
    type: str
  update_password:
    description:
      - When to apply O(user_password) to a user that already exists.
      - V(on_create) only sets the password when the user is created.
      - V(always) rewrites the password on every run.
      - >-
        The default is V(on_create) because VergeOS never returns a stored
        password, so there is nothing to compare against. Before 2.1.0 the
        password was rewritten unconditionally, which made any task that
        supplied O(user_password) report RV(ignore:changed) on every run and
        never converge.
      - >-
        V(always) is the correct choice for password rotation, and it will
        report changed every run by design.
    type: str
    choices: [ always, on_create ]
    default: on_create
    version_added: "2.1.0"
  email:
    description:
      - Email address for the user.
    type: str
  full_name:
    description:
      - Full name of the user.
    type: str
  enabled:
    description:
      - Whether the user account is enabled.
      - Defaults to C(true) when creating. Omit to leave unchanged on update.
    type: bool
  role:
    description:
      - Rejected. VergeOS users have no role column.
      - Access is a permission on an identity. Grant it with
        M(vergeio.vergeos.permission), or put the user in a group that
        already holds it (O(groups), M(vergeio.vergeos.group), or
        M(vergeio.vergeos.member)).
      - Before 2.2.0 this option was accepted and then discarded, so
        C(role=admin) created an ordinary user and reported success.
    type: str
    choices: [ admin, user, readonly ]
  groups:
    description:
      - Names of groups this user should belong to.
      - Additive, and only applied when O(state=present). Each named group
        must already exist. Groups not named here are left alone, and an
        empty list adds nothing and removes nothing.
      - Membership is a row on the group, matched by reference
        (C(/v4/users/N) or C(users/N)) and written with the SDK's
        C(add_user). There is no groups column on the user to set.
      - To remove a membership, use M(vergeio.vergeos.member) with
        C(state=absent), or M(vergeio.vergeos.group) with
        C(exact_members=true).
      - Ignored when O(state=absent), because the user is being removed.
    type: list
    elements: str
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - O(role) always fails the task, including when O(state=absent). Passing
    it used to look like it worked.
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
# There is no role on a VergeOS user. Administration is a permission,
# granted here to a group the new user is placed in. Without the
# permission task this creates an ordinary user who happens to be in
# an empty group.

- name: Create the administrators group
  vergeio.vergeos.group:
    name: administrators
    description: System administrators

- name: Create a new admin user
  vergeio.vergeos.user:
    name: john.doe
    user_password: secure_password
    email: john.doe@example.com
    full_name: John Doe
    groups:
      - administrators
    state: present

- name: Grant the administrators group full control of the system
  vergeio.vergeos.permission:
    group: administrators
    table: "/"
    full_control: true

- name: Create a read-only user
  vergeio.vergeos.user:
    name: viewer
    user_password: viewer_password
    state: present

- name: Grant that user read-only sight of the whole system
  vergeio.vergeos.permission:
    user: viewer
    table: "/"
    rights:
      - list
      - read

- name: Update user email
  vergeio.vergeos.user:
    name: john.doe
    email: john.new@example.com
    state: present

- name: Disable a user
  vergeio.vergeos.user:
    name: john.doe
    enabled: false
    state: present

- name: Delete a user
  vergeio.vergeos.user:
    name: old.user
    state: absent
'''

RETURN = r'''
user:
  description: The user row after the module ran.
  returned: when state is present
  type: dict
  sample:
    $key: 4
    name: john.doe
    email: john.doe@example.com
    displayname: John Doe
    enabled: true
groups:
  description:
    - Group names this run ensured the user belongs to.
    - This is the requested set, not every group the user belongs to.
      Membership outside that set is left in place.
  returned: when O(groups) is set and O(state) is V(present)
  type: list
  elements: str
  sample:
    - administrators
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    resolve_one,
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.rbac import (
    find_membership,
    is_member_identity_defect,
    member_identity_advice,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


def get_user(module, client, name):
    """Get user by name using SDK"""
    try:
        return resolve_one(module, client.users, name, 'user')
    except NotFoundError:
        return None


def create_user(module, client):
    """Create a new user using SDK"""
    if not module.params.get('user_password'):
        module.fail_json(msg="user_password is required when creating a new user")

    user_data = {
        'name': module.params['name'],
        'password': module.params['user_password'],
        'enabled': module.params['enabled'] if module.params['enabled'] is not None else True,
    }

    # Map module params to SDK fields
    if module.params.get('email'):
        user_data['email'] = module.params['email']
    if module.params.get('full_name'):
        user_data['displayname'] = module.params['full_name']

    if module.check_mode:
        user_data_copy = user_data.copy()
        user_data_copy.pop('password', None)
        return True, user_data_copy

    user = client.users.create(**user_data)
    return True, dict(user)


# Module parameter -> raw VergeOS API field, for the update path.
#
# Note 'password': it is write-only. The API accepts it and never returns it,
# so it is correctly absent from a user read with fields=all. That is why
# update_password exists (#73) -- there is nothing to compare against, so
# rewriting it unconditionally made every run report changed. Do not "fix"
# its absence from the read schema; it is not a missing field.
UPDATE_FIELD_MAP = {
    'email': 'email',
    'enabled': 'enabled',
    'full_name': 'displayname',
    'user_password': 'password',
}


def update_user(module, client, user):
    """Update an existing user using SDK"""
    changed = False
    update_data = {}

    user_dict = dict(user)

    # Check simple fields
    if module.params.get('email') is not None:
        if user_dict.get('email') != module.params['email']:
            update_data[UPDATE_FIELD_MAP['email']] = module.params['email']
            changed = True

    if module.params.get('enabled') is not None:
        if user_dict.get('enabled') != module.params['enabled']:
            update_data[UPDATE_FIELD_MAP['enabled']] = module.params['enabled']
            changed = True

    # Map full_name to displayname
    if module.params.get('full_name') is not None:
        if user_dict.get('displayname') != module.params['full_name']:
            update_data[UPDATE_FIELD_MAP['full_name']] = module.params['full_name']
            changed = True

    # Password: there is nothing to diff against -- VergeOS does not return a
    # stored password -- so rewriting it unconditionally meant the task could
    # never converge. Only rewrite when explicitly asked to.
    if (module.params.get('user_password')
            and module.params.get('update_password') == 'always'):
        update_data[UPDATE_FIELD_MAP['user_password']] = module.params['user_password']
        changed = True

    if not changed:
        return False, user_dict

    if module.check_mode:
        user_dict.update({k: v for k, v in update_data.items() if k != 'password'})
        return True, user_dict

    user = user.save(**update_data)
    return True, dict(user)


def delete_user(module, client, user):
    """Delete a user using SDK"""
    if module.check_mode:
        return True

    user.delete()
    return True


# Kept so a task that still says `role: admin` fails with a pointer, rather
# than with Ansible's generic "unsupported parameter". The choices stay in
# the argument spec so `role: superuser` is still an invalid choice; any
# accepted value hits this.
ROLE_REFUSAL = (
    "role is not a field on a VergeOS user, and passing it does not grant "
    "access. Access is a permission on an identity. Grant it with "
    "vergeio.vergeos.permission (full_control on table '/' is system-wide "
    "administration; rights [list, read] on '/' is read-only), or put the "
    "user in a group that already holds that grant (vergeio.vergeos.group, "
    "vergeio.vergeos.member, or the groups option on this module). Before "
    "2.2.0 this option was accepted and discarded, so role=admin created "
    "an ordinary user."
)


def reject_role(module):
    """Fail when O(role) is set. There is nowhere to store it."""
    if module.params.get('role') is not None:
        module.fail_json(msg=ROLE_REFUSAL)


def unique_names(names):
    """Group names in order, without duplicates."""
    seen = []
    for name in names or []:
        if name not in seen:
            seen.append(name)
    return seen


def resolve_groups(module, client, names):
    """``[(name, key), ...]`` for each group, or a named failure.

    Resolved before the user is written, so a typo does not leave a new
    account behind.
    """
    found = []
    for name in names:
        try:
            group = resolve_one(module, client.groups, name, 'group')
        except NotFoundError:
            module.fail_json(msg="Group '%s' not found" % name)
        found.append((name, dict(group)['$key']))
    return found


def ensure_group_memberships(module, client, user_key, groups):
    """Add ``user_key`` to each group that does not already contain them.

    ``groups`` is ``[(name, key), ...]`` from ``resolve_groups``.
    Returns the names that were missing. Additive: nothing is removed.

    Same write as the member module. ``add_user(key)`` posts
    ``member: /v4/users/<key>``. A hand-built ``create(member=<username>)``
    is the wrong shape, and comparing the stored reference to the username
    never matches (issue #92).
    """
    if user_key is None:
        # Check mode on a user that does not exist yet. They cannot already
        # be a member, and there is no key to add.
        if module.check_mode:
            return [name for name, _key in groups]
        module.fail_json(
            msg="the user was created but the API returned no key, so "
                "group membership could not be applied")

    missing = []
    for name, group_key in groups:
        members = client.groups.members(group_key)
        if find_membership(members.list(), user_key) is None:
            missing.append((name, members))

    if module.check_mode:
        return [name for name, _members in missing]

    added = []
    for name, members in missing:
        try:
            members.add_user(int(user_key))
        except Exception as exc:                            # noqa: BLE001
            if is_member_identity_defect(exc):
                module.fail_json(msg=member_identity_advice(name))
            raise
        added.append(name)
    return added


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present', choices=['present', 'absent']),
        user_password=dict(type='str', no_log=True),
        # no_log=False is deliberate and required. Ansible warns about any
        # option whose NAME contains "password" unless told otherwise, and
        # this one is a policy choice ('always' / 'on_create'), not a secret.
        # Without it every user task prints "Module did not set no_log for
        # update_password", which is noise that teaches operators to skip
        # warnings -- and the next warning may matter.
        update_password=dict(type='str', default='on_create',
                             no_log=False,
                             choices=['always', 'on_create']),
        email=dict(type='str'),
        full_name=dict(type='str'),
        enabled=dict(type='bool'),
        role=dict(type='str', choices=['admin', 'user', 'readonly']),
        groups=dict(type='list', elements='str'),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    # Before any API call. A delete that also says role=admin must not
    # succeed while teaching the operator that role is a real option.
    reject_role(module)

    target_username = module.params['name']

    client = get_vergeos_client(module)
    state = module.params['state']
    groups_param = module.params.get('groups')

    try:
        user = get_user(module, client, target_username)

        if state == 'absent':
            # groups is not applied here. state=absent removes the user,
            # and membership goes with the account. Same as group: the
            # users list is irrelevant once the group itself is being deleted.
            if user:
                delete_user(module, client, user)
                module.exit_json(changed=True, msg=f"User '{target_username}' deleted")
            else:
                module.exit_json(changed=False, msg=f"User '{target_username}' does not exist")

        wanted_groups = None
        if groups_param is not None:
            wanted_groups = resolve_groups(
                module, client, unique_names(groups_param))

        if user:
            changed, result_user = update_user(module, client, user)
            user_key = dict(user).get('$key')
        else:
            changed, result_user = create_user(module, client)
            user_key = None if module.check_mode else dict(result_user).get('$key')

        result = {'changed': changed, 'user': result_user}
        if wanted_groups is not None:
            added = ensure_group_memberships(
                module, client, user_key, wanted_groups)
            if added:
                result['changed'] = True
            result['groups'] = [name for name, _key in wanted_groups]
        module.exit_json(**result)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
