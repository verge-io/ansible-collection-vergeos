#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vm_recipe_deploy
short_description: Deploy a VM from a VergeOS recipe
version_added: "2.1.0"
description:
  - Deploy a VM from a native VergeOS recipe, validating the answer set against
    the recipe's own questions before anything is created.
  - Answers are validated locally (unknown names, types, C(min)/C(max)/C(regex),
    and the valid values of table-backed questions), then the deploy is run
    server-side as a simulation, and only then for real.
  - The deploy is asynchronous. The module returns once the platform has
    accepted it and reported the key of the VM being built; it does not wait
    for the VM's drives to finish importing. Use the
    R(vm_from_recipe role,ansible_collections.vergeio.vergeos.vm_from_recipe_role)
    when you need to wait for a bootable VM.
options:
  name:
    description:
      - Name for the new VM. Also the recipe instance's name, because the
        instance's name field is a proxy for the VM's.
      - An existing VM of this name is the idempotence key, so a second run
        reports C(changed=false) rather than deploying again.
    type: str
    required: true
  recipe:
    description:
      - Recipe name as it appears in the catalog.
      - Matched exactly, including case and punctuation.
    type: str
    required: true
  catalog:
    description:
      - Catalog name, used to disambiguate when two catalogs carry a recipe of
        the same name.
      - Without it, an ambiguous name is refused rather than guessed.
    type: str
  answers:
    description:
      - Recipe question answers, as a mapping of question variable name to
        value.
      - Names are validated against the recipe's own question set; an unknown
        name is an error, never a silent no-op.
      - Defaults are not sent - the platform applies them itself.
      - Network-type answers may be given as a network name or as a vnet key.
        The literal C(__new_internal__) creates a new internal network.
      - Not required does not mean safe to omit. Some stock recipes carry
        questions that are optional with an empty default whose empty value
        then fails mid-deploy; those are reported in RV(hints), and
        O(fail_on_hints) makes them fatal.
      - Marked C(no_log), because recipe answers routinely carry a guest
        password. One side effect is worth knowing - Ansible scrubs every
        value in this mapping from the task's output, so a non-secret answer
        that happens to equal another string in the result is masked there
        too.
        Answer values are never echoed back regardless - RV(answers_sent)
        reports names only.
    type: dict
    default: {}
  prune_unknown:
    description:
      - Drop and report answers this recipe does not define, instead of failing.
      - For applying one answer set across recipes with differing question sets.
      - Off by default, because with it on a typo silently vanishes.
    type: bool
    default: false
  auto_update:
    description:
      - Update the instance when the recipe publishes a new version.
    type: bool
    default: false
  simulate:
    description:
      - Run the server-side simulation before deploying.
      - The simulation is a real dry run - it renders cloud-init and walks
        every recipe step, creating nothing - and it is the only preflight a
        deploy has. Turning it off removes that.
      - Its result is scanned rather than trusted; see RV(simulate_result).
    type: bool
    default: true
  fail_on_hints:
    description:
      - Treat RV(hints) as fatal. A hint is an unanswered question whose
        default is empty.
    type: bool
    default: false
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
notes:
  - Supports C(check_mode). Under check mode the answer set is validated and
    the server-side simulation is run, so a check-mode run is a real preflight
    rather than a guess. Nothing is created.
  - There is no C(state) and no way to remove a VM with this module. Deleting a
    recipe instance does not delete the VM it created, so an C(absent) that
    removed the instance would look like a teardown while leaving the VM
    running. Use M(vergeio.vergeos.vm) with C(state=absent).
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Deploy a VM from a stock recipe
  vergeio.vergeos.vm_recipe_deploy:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "web-01"
    recipe: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    answers:
      HOSTNAME: "web-01"
      USER: "ops"
      PASSWORD: "{{ vault_guest_password }}"
      YB_CPU_CORES: 2
      YB_RAM: 4096
      YB_NIC_ETH0: "External"
      SELECT_OS_TIER: 4
  register: deployed

- name: Show what was built
  ansible.builtin.debug:
    msg: "VM key {{ deployed.vm_key }}"

- name: Preflight only - validate answers and simulate, create nothing
  vergeio.vergeos.vm_recipe_deploy:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "web-02"
    recipe: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    answers:
      HOSTNAME: "web-02"
      SELECT_OS_TIER: 4
  check_mode: true
  register: preflight

- name: Refuse to proceed if any question was left with an empty default
  vergeio.vergeos.vm_recipe_deploy:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "web-03"
    recipe: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    answers:
      HOSTNAME: "web-03"
      SELECT_OS_TIER: 4
    fail_on_hints: true
'''

RETURN = r'''
vm_key:
  description:
    - Key of the VM the deploy built, or of the pre-existing VM when the module
      converged.
    - Empty under check mode, and when the platform did not report one.
  returned: always
  type: str
  sample: "42"
instance_key:
  description: Key of the recipe instance the deploy created.
  returned: when a deploy ran
  type: str
  sample: "7"
recipe:
  description: The resolved recipe row.
  returned: always
  type: dict
  sample:
    name: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    version: 3
    catalog_display: "Local"
already_existed:
  description:
    - Whether a VM of this name was already present, in which case no deploy
      was attempted.
  returned: always
  type: bool
  sample: false
answers_sent:
  description:
    - Names of the answers that were sent, never their values.
  returned: always
  type: list
  elements: str
  sample: ["HOSTNAME", "SELECT_OS_TIER", "USER"]
hints:
  description:
    - Unanswered questions whose default is empty. Not fatal unless
      O(fail_on_hints) is set, but this is the shape of the stock-recipe trap
      where an empty storage tier makes OS-drive creation fail mid-deploy.
  returned: always
  type: list
  elements: str
pruned:
  description: Answers dropped because this recipe does not define them.
  returned: when O(prune_unknown) is true
  type: list
  elements: str
simulate_result:
  description:
    - Verdict of the server-side simulation.
    - 'The API reports success as C({"err": "Simulation complete"}) even
      when its own log contains failed steps, so C(ok) comes from scanning
      the log rather than from that field.'
  returned: when O(simulate) is true
  type: dict
  contains:
    ok:
      description: Whether the simulation was clean.
      type: bool
      returned: always
    errors:
      description: Failed steps found in the simulation log.
      type: list
      elements: str
      returned: always
    step_count:
      description: Number of recipe steps the simulation walked.
      type: int
      returned: always
    cloudinit_files:
      description: Names of the cloud-init files the simulation rendered.
      type: list
      elements: str
      returned: always
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    resolve_answers,
    scan_simulate,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.vm_recipes import (
    deployed_vm_key,
    fetch_networks,
    fetch_options,
    fetch_questions,
    find_vm_by_name,
    post_instance,
    simulate_transport_error,
    SUCCESS,
    resolve_recipe,
)

if HAS_PYVERGEOS:
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )


def resolve(client, recipe_key, answers, prune_unknown):
    """Validate the answer set, resolving table-backed options as needed.

    Two passes on purpose. The first tells us which questions are table-backed
    and so which tables have to be read; the second validates the answers with
    those valid values in hand, which is what lets a bad choice be reported as
    "not a valid choice, valid are ..." instead of surfacing mid-deploy.
    """
    questions = fetch_questions(client, recipe_key)
    networks = fetch_networks(client)

    first = resolve_answers(questions, answers, vnets=networks,
                            prune_unknown=prune_unknown)
    options = fetch_options(client, first.get('needs_options'))
    if not options:
        return first, questions

    return resolve_answers(questions, answers, vnets=networks,
                           options=options,
                           prune_unknown=prune_unknown), questions


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        recipe=dict(type='str', required=True),
        catalog=dict(type='str'),
        answers=dict(type='dict', default={}, no_log=True),
        prune_unknown=dict(type='bool', default=False),
        auto_update=dict(type='bool', default=False),
        simulate=dict(type='bool', default=True),
        fail_on_hints=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    params = module.params
    name = params['name']
    client = get_vergeos_client(module)

    try:
        recipe, error = resolve_recipe(client, params['recipe'],
                                       params.get('catalog'))
        if error:
            module.fail_json(msg=error)

        result = dict(
            changed=False,
            recipe=recipe,
            vm_key='',
            already_existed=False,
            answers_sent=[],
            hints=[],
        )

        # Converge on the VM, not on the recipe instance: some recipes set
        # YB_DETACH_RECIPE, which drops the instance row once the guest agent
        # reports in, so the instance is not a durable identity. The VM is.
        existing = find_vm_by_name(client, name)
        if existing:
            result.update(
                already_existed=True,
                vm_key=str(existing.get('$key') or ''),
                msg="VM '%s' already exists; this module never re-deploys "
                    "over an existing VM." % name,
            )
            module.exit_json(**result)

        resolved, _questions = resolve(client, recipe['$key'],
                                       params['answers'],
                                       params['prune_unknown'])

        result['answers_sent'] = sorted(resolved['answers'])
        result['hints'] = resolved['hints']
        if params['prune_unknown']:
            result['pruned'] = resolved['pruned']

        if resolved['errors']:
            module.fail_json(
                msg="the answer set is not valid for recipe '%s': %s"
                    % (params['recipe'], "; ".join(resolved['errors'])),
                **result)

        if params['fail_on_hints'] and resolved['hints']:
            module.fail_json(
                msg="fail_on_hints is set and questions are unanswered whose "
                    "defaults are empty: %s" % "; ".join(resolved['hints']),
                **result)

        if params['simulate']:
            status, document = post_instance(client, recipe['$key'], name,
                                             resolved['answers'],
                                             simulate=True)
            # The status is deliberately not treated as pass/fail. A clean
            # simulate answers HTTP 405 with err='Simulation complete' on
            # 26.1.8; only a missing response document means the call itself
            # failed.
            transport = simulate_transport_error(status, document)
            if transport:
                module.fail_json(msg=transport, **result)
            verdict = scan_simulate(document)
            verdict['http_status'] = status
            result['simulate_result'] = verdict
            if not verdict['ok']:
                module.fail_json(
                    msg="the simulated deploy reported failed steps, so a real "
                        "deploy would build a broken VM. The API still called "
                        "this \"Simulation complete\"; these came out of its "
                        "own log: %s" % "; ".join(verdict['errors']),
                    **result)

        if module.check_mode:
            result['msg'] = ("check mode: answers validated%s, nothing created."
                             % (" and simulation clean" if params['simulate']
                                else ""))
            module.exit_json(**result)

        status, document = post_instance(client, recipe['$key'], name,
                                         resolved['answers'],
                                         auto_update=params['auto_update'])
        if status not in SUCCESS:
            module.fail_json(
                msg="the deploy POST was refused: HTTP %s -- %s"
                    % (status, (document or {}).get('err')
                       if isinstance(document, dict) else document),
                **result)

        # The POST hands back the key of the VM it built, so the identity of
        # what was just deployed is known without inferring it from the name.
        result.update(
            changed=True,
            vm_key=deployed_vm_key(document),
            instance_key=str((document or {}).get('$key') or ''),
            msg="deploy accepted; the VM's drives may still be importing.",
        )
        module.exit_json(**result)

    except NotFoundError as e:
        module.fail_json(msg=f"Resource not found: {e}")
    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
