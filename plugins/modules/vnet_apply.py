#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: vnet_apply
short_description: Apply pending firewall rule changes on a VergeOS network
version_added: "2.1.0"
description:
  # Folded scalar: C(apply: false) contains ": ", which YAML reads as a
  # mapping key inside a plain scalar and rejects. Unquoted, the whole
  # DOCUMENTATION block fails to parse and ansible-doc reports the module
  # as having no documentation at all.
  - >-
    Trigger a network's firewall rule refresh so previously staged rule
    changes take effect — the companion to batching several
    M(vergeio.vergeos.vnet_rule) tasks with C(apply: false).
  - Idempotent, the network reports whether a rule apply is pending
    (C(need_fw_apply)); when nothing is pending the module changes
    nothing.
  - When rules are pending but the network is stopped, no refresh is
    possible (the API rejects it) and none is needed — a stopped network
    picks its rules up when it starts. The module reports this and
    changes nothing.
options:
  network:
    description:
      - Name of the network (vnet) to apply rules on.
    type: str
    required: true
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Stage several rules without applying
  vergeio.vergeos.vnet_rule:
    network: Internal
    name: "{{ item.name }}"
    protocol: tcp
    destination_ports: "{{ item.ports }}"
    apply: false
    state: present
  loop:
    - { name: allow-http, ports: "80" }
    - { name: allow-https, ports: "443" }

- name: Apply them once
  vergeio.vergeos.vnet_apply:
    network: Internal
'''

RETURN = r'''
pending:
  description: Whether a rule apply was pending before the module ran.
  returned: always
  type: bool
applied:
  description: Whether a rule refresh was actually triggered.
  returned: always
  type: bool
'''

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


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        network=dict(type='str', required=True),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = get_vergeos_client(module)
    name = module.params['network']

    try:
        try:
            network = client.networks.get(name=name)
        except NotFoundError:
            module.fail_json(msg="Network '%s' not found" % name)

        data = dict(network)
        pending = bool(data.get('need_fw_apply', False))
        running = bool(data.get('running', False))

        # Order matters. A stopped network never sets need_fw_apply -- there is
        # no running router to apply rules to -- so testing `pending` first
        # made this branch unreachable and reported "no pending rule changes"
        # at an operator who had just staged a policy. See issue #19.
        if not running:
            module.exit_json(changed=False, pending=pending, applied=False,
                             msg="Network '%s' is not running; any staged "
                                 'rules take effect when it starts' % name)

        if not pending:
            module.exit_json(changed=False, pending=False, applied=False,
                             msg="No pending rule changes on '%s'" % name)

        if not module.check_mode:
            network.apply_rules()
        module.exit_json(changed=True, pending=True,
                         applied=not module.check_mode,
                         msg="Applied rules on '%s'" % name)

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg='Unexpected error: %s' % e)


if __name__ == '__main__':
    main()
