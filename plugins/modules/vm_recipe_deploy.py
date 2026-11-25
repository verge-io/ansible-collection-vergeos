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
version_added: "2.2.0"
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
      - A boolean question accepts a real boolean, the integers C(0) and C(1),
        or the strings C(true), C(yes), C(on), C(1) and C(false), C(no),
        C(off), C(0). Any other value, such as C(enabled), is refused. The
        platform reads an unrecognised string as false, so sending it would
        build the wrong VM and still report success.
      - A C(disksize) question is in bytes. C(0) means use the recipe's
        default. A value above zero and under 1 MB (1048576) is refused,
        because a number like C(50) is fifty bytes. The recipe then builds
        the OS drive at the image size and still reports success. 50 GB is
        C(53687091200). A question whose own minimum, maximum, or default
        is itself under 1 MB is a byte count the recipe published, such as
        a block size of 65536, and that value is accepted.
      - A C(list) question that carries its own choices (for example
        C(YB_IP_ADDR_TYPE) with C(dhcp) and C(static)) is checked locally.
        The value has to be the choice's key, not the label the UI shows.
        C(DHCP) is refused, and the message names the value it received, the
        key C(dhcp) and the valid choices.
        YAML reads an unquoted C(yes), C(no), C(true), C(false), C(on) or
        C(off) as a boolean. When the keys include those words the boolean
        is still refused (it is not rewritten to the string) and the message
        says to quote it, for example C("no").
      - Stock Linux recipes publish a C(HOSTNAME) pattern that matches one
        letter, or three or more. A two-letter name such as C(db) does not
        match it, because the optional group uses a plus quantifier where a
        star was intended. The platform does not enforce recipe patterns on
        deploy. For that one published pattern the collection matches as if
        the quantifier were a star, so C(db) is accepted. A value that still
        does not match, such as a leading digit or a trailing hyphen, is
        refused. Every other pattern is unchanged, and it has to match the
        whole answer.
      - Not required does not mean safe to omit. Some stock recipes carry
        questions that are optional with an empty default whose empty value
        then fails mid-deploy; those are reported in RV(hints), and
        O(fail_on_hints) makes them fatal.
      - Marked C(no_log), and it has to stay that way. A guest password is a
        normal answer, and ansible-core writes the module invocation before
        this module runs and before it can read the recipe's question types.
        A password in a parameter that is not C(no_log) is in that log, on
        every supported ansible-core.
      - Put credentials here. Put ordinary answers in O(plain_answers).
        ansible-core 2.22 registers every value of a C(no_log) parameter when
        the module starts and masks that text in later output for the rest
        of the run, including the play recap. This module cannot take a value
        back off that list. A short ordinary answer such as C(1), C(0) or
        C(no) left in this mapping is masked that way. A playbook that still
        passes its whole answer set here keeps working, and the password
        stays hidden.
      - When the recipe publishes questions, this module still stops
        scrubbing non-credential answers out of its own return on cores that
        rewrite the result. That includes the return that reports a VM of
        this name already exists. A tier of C(1) leaves RV(recipe) readable.
        A password answer stays masked. The mask text depends on the
        ansible-core version. It may be a run of asterisks,
        C(VALUE_SPECIFIED_IN_NO_LOG_PARAMETER), C($REDACTED$), or another
        placeholder.
      - When the recipe publishes no questions, nothing can be classified, so
        every value left here stays masked, on a first deploy and on a rerun.
        A short value can be scrubbed out of RV(recipe), and a C(HOSTNAME)
        equal to O(name) is masked wherever that name appears. This module's
        messages identify the VM by key for that reason; use RV(vm_key), or
        read the name from a separate M(vergeio.vergeos.vm_info) task.
      - Answer values are never echoed back regardless. RV(answers_sent)
        reports names only.
    type: dict
    default: {}
  plain_answers:
    description:
      - Ordinary recipe answers, as a mapping of question variable name to
        value. Not C(no_log).
      - Use this for answers that are not credentials. On ansible-core 2.22 a
        short value such as C(1), C(0) or C(no) is then not masked in later
        output. Names, types and defaults follow the same rules as O(answers).
        The two mappings are merged. A question named in both is an error.
      - Do not put a credential here. A password-typed question, or a name
        that reads like one (C(PASSWORD), C(SECRET), C(TOKEN) and similar),
        is refused. The value has already been written to the module log,
        because this parameter is not C(no_log). Pass it in O(answers).
    type: dict
    default: {}
    version_added: "2.3.0"
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
# Connection options are omitted throughout. They fall back to VERGEOS_HOST,
# VERGEOS_USERNAME, VERGEOS_PASSWORD and VERGEOS_INSECURE.

# Recipe question names are not guessable, and the valid values for some of
# them depend on the system you are pointed at, so ask before you answer.
# resolve_options turns table backed questions into the values that are
# genuinely valid here.
- name: Find out what a recipe wants to know
  vergeio.vergeos.vm_recipe_info:
    name: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    questions: true
    resolve_options: true
  register: recipe

# PASSWORD is passed in answers, which is no_log. The other answers are
# passed in plain_answers, which is not. On ansible-core 2.22 a short value
# in a no_log parameter is masked in later output for the rest of the run.
#
# Two answers are worth singling out.
#
# YB_NIC_ETH0 is answered explicitly because the recipe's own default builds
# a brand new internal network, which leaves the VM with a NIC attached to
# nothing while every other check still passes.
#
# SELECT_OS_TIER is optional with an empty default, and an empty value fails
# partway through building the VM, so it has to be answered. Do not write a
# tier number in by hand: tier numbering is per system and plenty of systems
# have only tier 1, so a playbook with tier 4 in it fails everywhere else
# with "is not a valid choice". Take the tier from the lookup above.
- name: Deploy a VM from a stock recipe
  vergeio.vergeos.vm_recipe_deploy:
    name: "web-01"
    recipe: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    plain_answers:
      HOSTNAME: "web-01"
      USER: "ops"
      YB_CPU_CORES: 2
      YB_RAM: 4096                   # MB
      YB_DRIVE_OS_SIZE: 53687091200  # BYTES, not GB. This is 50 GB.
      YB_IP_ADDR_TYPE: dhcp
      YB_NIC_ETH0: "External"
      SELECT_OS_TIER: >-
        {{ recipe.options.SELECT_OS_TIER | map(attribute='$key') | first }}
    answers:
      PASSWORD: "{{ vault_guest_password }}"
  register: deployed

- name: Show what was built
  ansible.builtin.debug:
    msg: "VM key {{ deployed.vm_key }}"

# Check mode validates the answer set and then runs the platform's own
# simulation of the whole deploy, so it is a real preflight rather than a
# guess. Note that HOSTNAME, USER and PASSWORD are required by this recipe,
# and leaving any of them out fails validation before the simulation is
# reached at all.
- name: Preflight only, validate answers and simulate, create nothing
  vergeio.vergeos.vm_recipe_deploy:
    name: "web-02"
    recipe: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    plain_answers:
      HOSTNAME: "web-02"
      USER: "ops"
      YB_NIC_ETH0: "External"
      SELECT_OS_TIER: >-
        {{ recipe.options.SELECT_OS_TIER | map(attribute='$key') | first }}
    answers:
      PASSWORD: "{{ vault_guest_password }}"
  check_mode: true
  register: preflight

# fail_on_hints turns every unanswered question with an empty default into an
# error. It is a useful gate in CI, but it asks more of you than it looks:
# the stock recipes carry several questions that are optional, have empty
# defaults and are perfectly fine left alone, so you have to answer all of
# them before this will let a deploy through.
#
# One of them has a sharp edge. YB_NIC_ETH0_INTERNAL_GATEWAY is a network
# type question, so an empty string is rejected with "network '' not found"
# and it has to name a real network, even when you are not creating an
# internal network at all. Answering the question is what satisfies the
# check, and the value goes unused on this path.
- name: Refuse to proceed if any question was left with an empty default
  vergeio.vergeos.vm_recipe_deploy:
    name: "web-03"
    recipe: "Ubuntu Server 22.04 (Jammy Jellyfish)"
    plain_answers:
      HOSTNAME: "web-03"
      USER: "ops"
      YB_IP_ADDR_TYPE: dhcp
      YB_NIC_ETH0: "External"
      SELECT_OS_TIER: >-
        {{ recipe.options.SELECT_OS_TIER | map(attribute='$key') | first }}
      SSH_KEY: "{{ lookup('file', '~/.ssh/id_ed25519.pub') }}"
      SELECT_CREATE_UEFI: false
      YB_CLUSTER: 1
      YB_NIC_ETH0_GW: ""
      YB_NIC_ETH0_NS: ""
      YB_NIC_ETH0_INTERNAL_GATEWAY: "External"
    answers:
      PASSWORD: "{{ vault_guest_password }}"
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
no_log_answers:
  description:
    - Names of answers whose values were still C(no_log) after the recipe's
      question types were applied.
    - Values are never included. A password-typed question stays in this list.
      An ordinary answer is removed once its value is no longer masked.
    - Absent when the recipe could not be resolved. Empty when every supplied
      answer was ordinary, or when no answer contributed a maskable string.
  returned: when the recipe was resolved
  type: list
  elements: str
  sample: ["PASSWORD"]
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

try:
    from ansible.module_utils.secrets import register_secrets
except ImportError:
    # ansible-core before 2.22 has no secrets registry. no_log_values is what
    # those cores use to scrub a result.
    register_secrets = None

from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    get_vergeos_client,
    sdk_error_handler,
    vergeos_argument_spec,
    HAS_PYVERGEOS,
)
from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    answers_retained_in_no_log,
    looks_secret,
    maskable_strings,
    resolve_answers,
    scan_simulate,
    unmaskable_answer_strings,
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


def narrow_no_log(module, resolved):
    """Stop scrubbing non-credential answers out of this task's return.

    ``answers`` is ``no_log`` and must stay that way. Recipe answers routinely
    carry passwords, and ansible-core logs the invocation before any module
    code runs. On cores that rewrite a result, ansible-core also replaces a
    ``no_log`` value as a plain substring, integers included, so the ordinary
    answer ``YB_CPU_CORES: 1`` used to turn "requires at least 512" into a
    string whose digits were gone.

    ansible-core 2.22 registers every ``no_log`` value when the module starts
    and masks it in later output for the rest of the run. Removing a string
    from ``module.no_log_values`` does not undo that registration. Ordinary
    answers belong in ``plain_answers``, which is not ``no_log``, so they are
    never registered. This function still runs for playbooks that pass those
    answers in ``answers``: it keeps the return readable on cores that scrub
    the result. It does not change what 2.22 prints afterwards.

    Called once the recipe's question types have said which answers are
    credentials, and before any return that includes the recipe row, including
    the already-exists return. Three things keep it conservative:

      * a recipe that publishes no questions cannot be classified, so nothing
        is unmasked at all;
      * a value that any secret answer also contributes stays masked, even if
        a non-secret answer happens to share it;
      * a value any OTHER no_log parameter contributes -- ``password``,
        ``api_key`` -- stays masked, read from the argument spec rather than
        named here so a future no_log parameter is covered automatically.

    Everything before this call still runs fully masked. That is deliberate:
    it fails closed.
    """
    if not resolved.get('introspectable'):
        return

    unmaskable = unmaskable_answer_strings(module.params.get('answers'),
                                           resolved.get('secret_vars'))
    if not unmaskable:
        return

    for name, spec in (module.argument_spec or {}).items():
        if name != 'answers' and spec.get('no_log'):
            unmaskable -= maskable_strings(module.params.get(name))

    module.no_log_values -= unmaskable


def conceal_named_plain_secrets(module, plain):
    """Hide plain_answers whose names already look like credentials.

    No recipe lookup is required, so this runs before every failure return.
    A password-typed question whose name does not match is concealed later,
    once the question types are known.
    """
    values = [value for name, value in (plain or {}).items()
              if looks_secret(name)]
    conceal_secret_values(module, values)


def combine_answers(module):
    """Merge ``plain_answers`` with the no_log ``answers`` mapping.

    A question named in both is refused. The values are not quoted back: one
    of them may be a password, and this runs before the recipe is known.
    Names that already look like credentials are concealed first, so an
    overlap, a login failure, or an unknown recipe cannot return them.
    """
    plain = module.params.get('plain_answers') or {}
    hidden = module.params.get('answers') or {}
    conceal_named_plain_secrets(module, plain)
    overlap = sorted(set(plain) & set(hidden))
    if overlap:
        module.fail_json(
            msg="the same answer was passed in both plain_answers and "
                "answers: %s. Pass each question in one of them."
                % ", ".join(repr(name) for name in overlap))
    merged = dict(plain)
    merged.update(hidden)
    return merged, plain


def conceal_secret_values(module, values):
    """Hide strings that were passed outside a no_log parameter.

    On cores before 2.22, adding them to ``no_log_values`` scrubs the result.
    On 2.22, ``register_secrets`` is what masks later output. The invocation
    log has already been written by then; this cannot rewrite it.
    """
    strings = set()
    for value in values:
        strings |= maskable_strings(value)
    strings.discard('')
    if not strings:
        return
    module.no_log_values.update(strings)
    if register_secrets is not None:
        register_secrets(strings)


def refuse_plain_credentials(module, plain, resolved):
    """Refuse a credential that arrived through ``plain_answers``.

    The value is already in the module log. Name the question and stop,
    rather than sending it on.
    """
    secret = set(resolved.get('secret_vars') or [])
    leaked = [name for name in sorted(plain or {})
              if name in secret or looks_secret(name)]
    if not leaked:
        return
    conceal_secret_values(module, [plain[name] for name in leaked])
    module.fail_json(
        msg="these answers are credentials and were passed in "
            "plain_answers, which is written to the module log: %s. "
            "Pass them in answers."
            % ", ".join(repr(name) for name in leaked))


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        recipe=dict(type='str', required=True),
        catalog=dict(type='str'),
        answers=dict(type='dict', default={}, no_log=True),
        plain_answers=dict(type='dict', default={}),
        prune_unknown=dict(type='bool', default=False),
        auto_update=dict(type='bool', default=False),
        simulate=dict(type='bool', default=True),
        fail_on_hints=dict(type='bool', default=False),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    # Before the client. A duplicated question name does not need a login,
    # and one of the two values may be a password.
    merged, plain = combine_answers(module)

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

        # Before any return that includes the recipe row. The already-exists
        # path used to leave here while every answer was still a no_log value,
        # so a rerun scrambled recipe.name / version / build with the same
        # substring masking a first deploy had already stopped. Question types
        # say which answers are credentials. A recipe that publishes none
        # cannot be classified and stays fully masked. See narrow_no_log().
        resolved, _questions = resolve(client, recipe['$key'],
                                       merged,
                                       params['prune_unknown'])
        narrow_no_log(module, resolved)
        # A credential in plain_answers is already in the invocation log.
        # Refuse it before the already-exists return, which would otherwise
        # look like success.
        refuse_plain_credentials(module, plain, resolved)
        # Recorded after narrowing. Names only; the values stay in no_log_values.
        result['no_log_answers'] = answers_retained_in_no_log(
            merged, module.no_log_values)

        # Converge on the VM, not on the recipe instance: some recipes set
        # YB_DETACH_RECIPE, which drops the instance row once the guest agent
        # reports in, so the instance is not a durable identity. The VM is.
        existing = find_vm_by_name(client, name)
        if existing:
            # Identify by key. A recipe that publishes no questions cannot be
            # classified, so every answer stays no_log and a HOSTNAME equal to
            # the VM name would still be scrubbed out of a message that quoted
            # the name. The key does not collide with an answer.
            result.update(
                already_existed=True,
                vm_key=str(existing.get('$key') or ''),
                msg="a VM of this name already exists (key %s); this module "
                    "never re-deploys over an existing VM."
                    % (existing.get('$key') or '?'),
            )
            module.exit_json(**result)

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
            # 26.1.8. A 4xx that names a reason means the request arrived.
            # 400 and 422 refuse the answer set. 401 and 403 refuse the
            # account. Any other 4xx is a refusal that does not blame the
            # answers. Only a reply with no reason is a transport failure.
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
