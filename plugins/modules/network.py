#!/usr/bin/python
# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: network
short_description: Manage networks in VergeOS
version_added: "1.0.0"
description:
  - Create, update, and delete networks in VergeOS.
  - Manage network configuration including IP ranges, VLANs, and network settings.
options:
  name:
    description:
      - The name of the network.
    type: str
    required: true
  state:
    description:
      - The desired state of the network.
      - C(present) ensures the network exists with the specified configuration.
      - C(absent) ensures the network is deleted. A running vnet is stopped
        first - the API refuses to delete one that is running, and C(absent)
        already means destroy it.
      - C(running) and C(stopped) also set its power. A vnet is a router
        machine; until it is running, staged firewall rules are configuration
        and nothing more. If the router is C(starting) or C(stopping), the
        module waits for it to settle before deciding whether to send the
        action. Only C(running) and C(stopped) are settled. The C(running)
        boolean stays true through both transitional states.
      - C(restarted) restarts a running vnet. It is an event rather than a
        state to converge on, so it always reports changed. Use
        RV(need_restart) to decide whether one is outstanding - a
        configuration change to a running vnet sets it, and until the restart
        happens the live router keeps the old value. The platform clears
        C(need_restart) when it accepts the action, which is before the
        router cycles, so the module waits until status has left C(running)
        and returned to it.
      - The power states imply C(present)- the configuration is created or
        converged first, then the power is set.
    type: str
    choices: [ present, absent, running, stopped, restarted ]
    default: present
  apply_rules:
    description:
      - Apply staged firewall rules as part of powering on or restarting.
      - This is the SDK's default, and usually what is wanted after staging a
        policy - but it decides whether a rule set becomes live, so it is
        named here rather than left incidental.
      - Ignored for O(state=stopped).
    type: bool
    default: true
    version_added: "2.2.0"
  power_timeout:
    description:
      - Seconds to wait for the vnet to reach the requested power state.
      - Also how long to wait for a transitional status (C(starting),
        C(stopping)) to settle before a power action is sent, and how long
        a restart waits for status to leave C(running) and come back.
      - On expiry the module fails and says what it was still reading, rather
        than reporting a power change that did not finish. Measured on
        26.1.8, power on takes about a second and power off about two. A
        restart is accepted immediately and the router enters C(starting)
        about a second later.
    type: int
    default: 60
    version_added: "2.2.0"
  description:
    description:
      - Description of the network.
    type: str
  network_type:
    description:
      - Type of network to create.
      - >-
        C(vlan) and C(overlay) were accepted before 2.1.0 but are not valid
        VergeOS network types; the API rejected both. A VLAN network is an
        C(external) (or C(internal)) network with C(layer2_type=vlan) and a
        C(vlan_id).
    type: str
    choices: [ internal, external, dmz ]
  ip_address:
    description:
      - Router IP address within the network (API field C(ipaddress)).
    type: str
  network:
    description:
      - CIDR-notation network address (e.g. C(10.10.10.0/24)).
      # Folded scalar: C(Validation error: Gateway is outside of network)
      # contains ": ", which YAML reads as a mapping key inside a plain
      # scalar and rejects -- taking the WHOLE DOCUMENTATION block with it.
      # See ansible-collection-vergeos#17.
      - >-
        Required by the VergeOS API for vnet creation; supplying
        C(ip_address) and C(gateway) without C(network) is rejected by the
        server with C(Validation error: Gateway is outside of network).
    type: str
    version_added: "2.1.0"
  gateway:
    description:
      - Default gateway for the network.
    type: str
  dhcp_enabled:
    description:
      - Whether DHCP is enabled on this network.
    type: bool
  dhcp_start:
    description:
      - Start of the DHCP range.
    type: str
  dhcp_end:
    description:
      - End of the DHCP range (API field C(dhcp_stop)).
      - >-
        Before 2.1.0 this was sent as C(dhcp_end), which the API silently
        discarded, producing a DHCP scope with a start and no end.
    type: str
  layer2_type:
    description:
      - Layer 2 encapsulation for the network.
      - Use C(vlan) together with C(vlan_id) to create a VLAN-tagged network.
    type: str
    choices: [ vlan, vxlan, none ]
    version_added: "2.1.0"
  vlan_id:
    description:
      - VLAN or VXLAN ID (API field C(layer2_id)).
      - Pair with C(layer2_type=vlan) for a VLAN-tagged network.
    type: int
  dns_servers:
    description:
      - List of DNS servers for the network (API field C(dnslist)).
      - >-
        Before 2.1.0 this was silently discarded on update; only the create
        path applied it.
    type: list
    elements: str
  domain:
    description:
      - DNS domain name handed to DHCP clients on this network.
    type: str
    version_added: "2.1.0"
  interface_network:
    description:
      - >-
        Name of the physical vnet (a switch) this network reaches the fabric
        through. Maps to the API's C(interface_vnet), which stores the
        target's key; give the name and the module resolves it.
      - >-
        Without an uplink a vnet is attached to nothing. A VLAN-tagged network
        with no C(interface_network) is an isolated layer 2 that carries the
        tag onto nothing, and the module will report success creating one.
        C(layer2_type) and C(vlan_id) describe the tag; this describes what
        the tag is carried on. Neither is useful without the other.
      - Set to an empty string to detach the network from its uplink.
    type: str
    version_added: "2.1.0"
  rate_limit:
    description:
      - Bandwidth cap for the network, in B(mbytes per second) - not bits, not bytes.
      - >-
        C(0) means explicitly uncapped and is applied like any other value.
        Omitting the parameter leaves whatever is already configured alone.
        The two are not the same thing.
    type: int
    version_added: "2.1.0"
  mtu:
    description:
      - MTU for the network.
    type: int
    version_added: "2.1.0"
  on_power_loss:
    description:
      - Behaviour when power is restored after a loss.
    type: str
    choices: [ power_on, last_state, leave_off ]
    version_added: "2.1.0"
extends_documentation_fragment:
  - vergeio.vergeos.vergeos
author:
  - VergeIO (@vergeio)
'''

EXAMPLES = r'''
- name: Create an internal network
  vergeio.vergeos.network:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "internal-network"
    description: "Internal production network"
    state: present
    network_type: internal
    network: "10.0.0.0/24"
    ip_address: "10.0.0.1"
    dhcp_enabled: true
    dhcp_start: "10.0.0.100"
    dhcp_end: "10.0.0.200"
    dns_servers:
      - "8.8.8.8"
      - "8.8.4.4"

# layer2_type and vlan_id describe the TAG; interface_network describes what
# the tag is carried on. Without an uplink this network is an isolated layer 2
# that carries VLAN 100 onto nothing -- and the module still reports success.
- name: Create a VLAN-tagged external network on the physical fabric
  vergeio.vergeos.network:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "vlan-100"
    state: present
    network_type: external
    layer2_type: vlan
    vlan_id: 100
    interface_network: "ext1 Switch"
    network: "192.168.100.0/24"
    ip_address: "192.168.100.1"

- name: Cap a network at 100 MB/s and move it to a different uplink
  vergeio.vergeos.network:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "vlan-100"
    state: present
    rate_limit: 100
    interface_network: "ext2 Switch"

- name: Remove the bandwidth cap and detach from the fabric
  vergeio.vergeos.network:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "vlan-100"
    state: present
    rate_limit: 0
    interface_network: ""

# A vnet is a router machine. Staged firewall rules are configuration until
# it runs, which is the gap this closes: vnet_rule and vnet_apply both report
# "not running, staged rules take effect when it starts" and neither could
# start it.
- name: Start a vnet, applying whatever rules are staged on it
  vergeio.vergeos.network:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "vlan-100"
    state: running

- name: Start it without letting staged rules become live
  vergeio.vergeos.network:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "vlan-100"
    state: running
    apply_rules: false

- name: Stop a vnet
  vergeio.vergeos.network:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "vlan-100"
    state: stopped

# Changing configuration on a RUNNING vnet updates the record and sets
# need_restart; the live router keeps the old value until it is restarted.
- name: Change the MTU and make it take effect
  vergeio.vergeos.network:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "vlan-100"
    state: present
    mtu: 1400
  register: vnet

- name: Restart only if one is owed
  vergeio.vergeos.network:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "vlan-100"
    state: restarted
  when: vnet.need_restart

- name: Delete a network
  vergeio.vergeos.network:
    host: "vergeos.example.com"
    username: "admin"
    password: "secret"
    name: "old-network"
    state: absent
'''

RETURN = r'''
network:
  description: Information about the network
  returned: when state is present
  type: dict
  sample:
    name: "internal-network"
    description: "Internal production network"
    type: "internal"
    network: "10.0.0.0/24"
    ipaddress: "10.0.0.1"
    dhcp_enabled: true
    dhcp_start: "10.0.0.100"
    dhcp_stop: "10.0.0.200"
    dnslist: "8.8.8.8,8.8.4.4"
running:
  description:
    - Whether the vnet's router is running.
    - Read through the projection C(machine#status#running as running), and
      only reported once C(machine#status#status) is settled. The boolean
      stays true while status is C(starting) or C(stopping), so it is not
      itself proof that a power change finished. It is not a vnet column
      and is absent from C(fields=all).
  returned: when the network exists
  type: bool
  version_added: "2.2.0"
need_restart:
  description:
    - Whether the vnet needs restarting for its configuration to take effect.
    - Measured on 26.1.8- changing C(mtu) on a running vnet sets this, and
      until the restart happens the live router keeps the old value. A run
      that reports C(changed) on such a field has changed the record, not the
      router.
  returned: when the network exists
  type: bool
  version_added: "2.2.0"
changed:
  description: Whether the module made any changes
  returned: always
  type: bool
  sample: true
'''

import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.vergeio.vergeos.plugins.module_utils.vergeos import (
    resolve_one,
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


def get_network(module, client, name):
    """Get network by name, including every field the module compares.

    The SDK's default field set is a subset of the vnet schema and omits
    several fields this module manages -- notably 'dnslist'. Comparing
    against a field the fetch never returned makes it read as None, so the
    module reports 'changed' on every run and never converges (issue #18).
    """
    try:
        return resolve_one(module, client.networks, name, 'network',
                           fields=NETWORK_FIELDS)
    except NotFoundError:
        return None


# Module parameter -> pyvergeos NetworkManager.create() *parameter* name.
#
# The create path calls client.networks.create(**data), so these must be the
# SDK's named arguments -- the SDK is what translates them into API fields
# (ip_address -> ipaddress, dns_servers -> dnslist, network_address ->
# network). Anything that is not a named SDK argument falls through **kwargs
# and is sent to the API verbatim, where an unknown field is accepted and
# discarded: HTTP 200, no error. That silent discard is how #8, #10 and #18
# all shipped unnoticed.
CREATE_PARAM_MAP = {
    'description': 'description',
    'network_type': 'network_type',
    'ip_address': 'ip_address',
    'network': 'network_address',
    'gateway': 'gateway',
    'dhcp_enabled': 'dhcp_enabled',
    'dhcp_start': 'dhcp_start',
    'dhcp_end': 'dhcp_stop',
    'layer2_type': 'layer2_type',
    'vlan_id': 'layer2_id',
    'dns_servers': 'dns_servers',
    'domain': 'domain',
    'mtu': 'mtu',
    'on_power_loss': 'on_power_loss',
    'rate_limit': 'rate_limit',
}

# Module parameter -> raw VergeOS API field.
#
# The update path calls network.save(**data), which PUTs these keys verbatim,
# so every value here must be a real API field as returned by the vnet
# endpoint. tests/live/verify-network-contract.yml asserts exactly that
# against a live system; keep the two in sync.
UPDATE_FIELD_MAP = {
    'description': 'description',
    'network_type': 'type',
    'ip_address': 'ipaddress',
    'network': 'network',
    'gateway': 'gateway',
    'dhcp_enabled': 'dhcp_enabled',
    'dhcp_start': 'dhcp_start',
    'dhcp_end': 'dhcp_stop',
    'layer2_type': 'layer2_type',
    'vlan_id': 'layer2_id',
    'dns_servers': 'dnslist',
    'domain': 'domain',
    'mtu': 'mtu',
    'on_power_loss': 'on_power_loss',
    'rate_limit': 'rate_limit',
}

# interface_network is handled outside the maps above because resolving it
# needs the client: operators give a vnet NAME, the API stores that vnet's
# key. See resolve_interface_vnet().
UPLINK_PARAM = 'interface_network'
UPLINK_API_FIELD = 'interface_vnet'


# Fields the module must fetch in order to diff correctly. The SDK's default
# field set omits some of these (dnslist in particular), and a field that was
# never fetched reads as None, so the comparison never matches.
COMPARISON_FIELDS = (['$key', 'name', UPLINK_API_FIELD]
                     + sorted(set(UPDATE_FIELD_MAP.values())))

# Power state, which is NOT a vnet column.
#
# `running` is a join through the vnet's router machine. It is present in the
# SDK's default projection and absent from GET /vnets?fields=all -- measured
# on 26.1.8, 98 fields and no `running` among them. This module names its
# projection, so a field not listed here does not arrive at all: before these
# two lines the module could not see power state, would have read every vnet
# as stopped, and would have powered on something already up on every run.
# That is the same defect as #18, #92 and the `vm` power-state bug, and it is
# why the fetch list and the diff list are separate.
#
# `need_restart` IS a real column. Measured: changing mtu on a running vnet
# sets it, and until the restart happens the live router keeps the old value.
# The platform clears it when a restart is *accepted*, which is about a
# second before status leaves `running` (#168). It is not proof the router
# cycled.
#
# `status` is the same machine join's state string (`running`, `stopped`,
# `starting`, `stopping`, ...). `running` stays true during `starting` and
# `stopping` -- measured on 26.1.8 -- so the boolean cannot tell a settled
# router from one mid-cycle. Only `running` and `stopped` are settled.
#
# `started` is that machine's start timestamp, unix seconds
# (`machine#status#started`). A one-second poll can miss `starting`. The
# timestamp moving is the other proof that the generation now running is
# not the one restart() was sent against.
POWER_FIELDS = [
    'need_restart',
    'machine#status#running as running',
    'machine#status#status as status',
    'machine#status#started as started',
]

# What get_network() asks for: everything diffed, plus the power state the
# module reports and gates on.
NETWORK_FIELDS = COMPARISON_FIELDS + POWER_FIELDS


def normalize_value(param, value):
    """Coerce a module value into the representation the API stores."""
    if param == 'dns_servers' and isinstance(value, list):
        # The API stores the DNS server list as a comma-separated string.
        return ','.join(value)
    return value


def resolve_interface_vnet(module, client):
    """Resolve the uplink parameter to what the API stores.

    Returns one of:
      None -- the parameter was not supplied; leave the uplink alone
      ''   -- an empty string was supplied; detach from any uplink
      int  -- the $key of the named vnet

    Operators think in vnet names; the API stores the target's key. A vnet
    reaches the physical fabric through this field, so a network created
    without one is attached to nothing regardless of how correct every other
    field looks (issue #60).
    """
    name = module.params.get(UPLINK_PARAM)
    if name is None:
        return None
    if name == '':
        return ''

    try:
        uplink = resolve_one(module, client.networks, name, 'network')
    except NotFoundError:
        module.fail_json(
            msg="Uplink network '%s' not found. %s must name an existing "
                "vnet -- typically a 'physical' one, which is what carries a "
                "network onto the fabric." % (name, UPLINK_PARAM)
        )
    return dict(uplink).get('$key')


def build_network_data(module, uplink_key=None):
    """Build SDK create() kwargs from module params"""
    network_data = {
        'name': module.params['name'],
    }

    for param, sdk_arg in CREATE_PARAM_MAP.items():
        if module.params.get(param) is not None:
            network_data[sdk_arg] = module.params[param]

    if uplink_key is not None:
        network_data[UPLINK_API_FIELD] = uplink_key

    return network_data


def create_network(module, client):
    """Create a new network using SDK"""
    network_data = build_network_data(module, resolve_interface_vnet(module, client))

    if module.check_mode:
        return True, network_data

    network = client.networks.create(**network_data)
    return True, dict(network)


def update_network(module, client, network):
    """Update an existing network using SDK"""
    changed = False
    update_data = {}

    network_dict = dict(network)
    for param, api_field in UPDATE_FIELD_MAP.items():
        if module.params.get(param) is None:
            continue
        desired = normalize_value(param, module.params[param])
        if network_dict.get(api_field) != desired:
            update_data[api_field] = desired
            changed = True

    uplink_key = resolve_interface_vnet(module, client)
    if uplink_key is not None:
        # The stored value may come back as a string key; compare as strings
        # so a converged uplink does not read as a change on every run.
        current = network_dict.get(UPLINK_API_FIELD)
        if str(current or '') != str(uplink_key or ''):
            update_data[UPLINK_API_FIELD] = uplink_key
            changed = True

    if not changed:
        return False, network_dict

    if module.check_mode:
        network_dict.update(update_data)
        return True, network_dict

    network = network.save(**update_data)
    return True, dict(network)


def delete_network(module, client, network):
    """Delete a network, stopping it first if it is running.

    The API refuses to delete a running vnet -- "Network must be stopped to
    delete" -- and before the module could power one off, the only way out of
    that was a REST call the collection did not have. So `state: absent` on a
    running network failed with an opaque API error and no way to act on it.

    Stopping first is not an extra side effect: `absent` already means destroy
    it, and there is no reading of "delete this network" under which leaving
    it running was the intent.
    """
    if module.check_mode:
        return True

    # Same window as a power task (#168): `running` is true while status is
    # `starting` or `stopping`, and poweroff is refused unless status is
    # `running`. Settle first, then stop only if it came back up.
    row = dict(network)
    if is_transitional(row):
        row = wait_until_settled(module, client, module.params['name'])
    if _is_up(row):
        network.power_off()
        wait_for_power(module, client, module.params['name'], False)
        # Re-resolve: the object was fetched before the power change and
        # delete() goes through the manager it was bound to.
        network = get_network(module, client, module.params['name']) or network

    network.delete()
    return True


def power_result(row):
    """The module's return, with the two power facts surfaced.

    `need_restart` is reported rather than buried in the row: a configuration
    change to a running vnet sets it, and a run that reports `changed` on such
    a field has changed the record and not the router. An operator who cannot
    see that has no way to know a restart is owed.
    """
    row = dict(row or {})
    return {
        'network': row,
        'running': reported_running(row),
        'need_restart': bool(row.get('need_restart')),
    }


# Status strings that mean the router has finished moving. Everything else
# the platform reports (`starting`, `stopping`, ...) is a transition, and
# `running` is true during both of those. Measured on 26.1.8 (#168).
SETTLED_STATUSES = ('running', 'stopped')


def machine_status(row):
    """The status string, or None when the projection did not supply one."""
    value = dict(row or {}).get('status')
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def is_running(row):
    """Power boolean, or None when the projection did not supply it.

    None is deliberately distinct from False. `running` is a join rather than
    a column, so a row fetched without POWER_FIELDS has no opinion at all --
    and a module that read "no opinion" as "stopped" would power on a running
    vnet on every run and report a change every time.

    This is the boolean, not the settled state. It stays true while status
    is `starting` or `stopping`. Decisions about whether to send a power
    action use `_is_up` / `is_transitional`, which read `status`.
    """
    value = dict(row).get('running')
    return None if value is None else bool(value)


def is_transitional(row):
    """True when status is present and is neither running nor stopped."""
    status = machine_status(row)
    return status is not None and status not in SETTLED_STATUSES


def _is_up(row):
    """Settled running, with the boolean as a fallback when status is absent.

    A present status wins. `running: true` during `stopping` is not up, and
    `running: true` during `starting` is not a router a poweroff can target.
    """
    status = machine_status(row)
    if status == 'running':
        return True
    if status is not None:
        return False
    return is_running(row) is True


def _is_down(row):
    """Settled stopped, with the boolean as a fallback when status is absent."""
    status = machine_status(row)
    if status == 'stopped':
        return True
    if status is not None:
        return False
    return is_running(row) is False


def reported_running(row):
    """The running fact handed back to a play.

    A transitional status is not reported as running. The boolean would say
    true, and a play that trusted it would send the next power action into
    the window the platform refuses (#168).
    """
    status = machine_status(row)
    if status == 'running':
        return True
    if status == 'stopped':
        return False
    if status is not None:
        return None
    return is_running(row)


def _started_at(row):
    """Unix start timestamp, or None when the row has nothing to compare."""
    value = dict(row or {}).get('started')
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _start_advanced(before, row):
    """True when this row's start timestamp is later than ``before``'s.

    Strictly later, not merely present. A missing timestamp on either side
    is not evidence: absence is not a generation change.
    """
    old = _started_at(before)
    new = _started_at(row)
    if old is None or new is None:
        return False
    return new > old


def _poll(module, client, name, satisfied, wanted, reading):
    """Poll until ``satisfied(row)``, or fail saying what it read.

    Failing on expiry rather than returning is the point. A power task that
    quietly gives up and reports success is how a play goes green against a
    stopped router -- and `vm` does exactly that, looping thirty times and
    then returning changed=True whatever the VM ended up doing.
    """
    deadline = time.time() + max(1, int(module.params['power_timeout']))
    row = None
    while time.time() < deadline:
        time.sleep(1)
        current = get_network(module, client, name)
        if current is None:
            module.fail_json(
                msg="Network '%s' disappeared while waiting for it to be %s"
                    % (name, wanted))
        row = dict(current)
        if satisfied(row):
            return row

    module.fail_json(
        msg="Network '%s' did not reach %s within %ss; it still reads %s. "
            "These operations are asynchronous, but on a healthy system a "
            "vnet powers on in about a second and off in about two, so this "
            "is not slowness."
        % (name, wanted, module.params['power_timeout'],
           reading(row or {})))


def _power_reading(row):
    return 'status=%s running=%s need_restart=%s started=%s' % (
        row.get('status'), row.get('running'), row.get('need_restart'),
        row.get('started'))


def wait_for_power(module, client, name, running):
    """Poll until the vnet is settled in the requested power state.

    The boolean is not the post-condition. It is already true while status
    is `starting`, so a start that returned on `running` would hand the next
    task a router the platform still refuses to power off.
    """
    wanted = 'running' if running else 'stopped'

    def satisfied(row):
        status = machine_status(row)
        if status is not None:
            return status == wanted
        return is_running(row) is running

    return _poll(
        module, client, name, satisfied, wanted, _power_reading)


def wait_until_settled(module, client, name):
    """Poll until status is `running` or `stopped`.

    Called before a power action when the row we already hold is
    transitional. Sending poweroff while status is `starting` is the
    failure "vNet must be in running state to poweroff".
    """
    return _poll(
        module, client, name,
        lambda row: machine_status(row) in SETTLED_STATUSES,
        'settled (running or stopped)',
        _power_reading)


def wait_for_restart(module, client, name, before=None):
    """Poll until the restart has demonstrably happened.

    Clearing ``need_restart`` is not that. Measured on 26.1.8 (#168): the
    platform clears the flag when it accepts the action, while status is
    still `running` and the router is still on the old config. Status
    enters `starting` about a second later, and a following ``poweroff``
    is refused because the vnet is not in the running state.

    The post-condition is a cycle, then settled running, with the debt
    clear. A cycle is status having left `running` (the poll saw
    `starting` / `stopping` / `stopped`) or the start timestamp moving
    past the value captured before ``restart()`` was sent. A one-second
    poll can miss the transitional window; the timestamp is how that poll
    still tells the new generation from the one that accepted the action.
    A flag cleared by the router falling over and staying down is not a
    restart.
    """
    before = dict(before or {})
    seen_leave = {'yes': False}

    def satisfied(row):
        status = machine_status(row)
        if status is not None and status != 'running':
            seen_leave['yes'] = True
        if status != 'running' or row.get('need_restart'):
            return False
        return seen_leave['yes'] or _start_advanced(before, row)

    return _poll(
        module, client, name, satisfied,
        'restarted (status left running and returned to running)',
        _power_reading)


def prepare_power_row(module, client, network):
    """The row a power decision is made on.

    When status is transitional, wait until it is `running` or `stopped`
    before deciding whether an action is still needed. Check mode waits
    too: the wait is a read, and the change it would report depends on
    where the router settles.
    """
    row = dict(network)
    require_power_state(module, row)
    if is_transitional(row):
        return wait_until_settled(module, client, module.params['name'])
    return row


# Power goes through the SDK's power_on/power_off/restart, which POST to
# `vnet_actions` with {"vnet": key, "action": ...}.
#
# NOT `PUT /vnets/<key>?action=poweron`. That returns HTTP 200 with an empty
# body and does nothing at all -- measured on 26.1.8, and it cost a ladder run
# to find, because 200 reads as success and the network simply stayed stopped
# for the full timeout. Recorded here so nobody "simplifies" this into a PUT.


def require_power_state(module, row):
    """Refuse to act on a row that cannot say whether it is running.

    Guessing here is not conservative in either direction: read as stopped, a
    running vnet is powered on again on every run; read as running, a stopped
    one is never started. So the module says which projection is missing
    instead of picking one.
    """
    if is_running(row) is None:
        module.fail_json(
            msg="Network '%s' did not report a power state, so it could not "
                "be determined. `running` is not a vnet column -- it is a "
                "join, fetched as 'machine#status#running as running', and it "
                "is absent from fields=all. This module names its projection "
                "in NETWORK_FIELDS; a row from anywhere else cannot answer "
                "this." % module.params['name'])


def power_on_network(module, client, network):
    """Ensure the vnet's router is running."""
    name = module.params['name']
    row = prepare_power_row(module, client, network)

    if _is_up(row):
        return False, row

    if module.check_mode:
        row['running'] = True
        row['status'] = 'running'
        return True, row

    network.power_on(apply_rules=module.params['apply_rules'])
    return True, wait_for_power(module, client, name, True)


def power_off_network(module, client, network):
    """Ensure the vnet's router is stopped."""
    name = module.params['name']
    row = prepare_power_row(module, client, network)

    if _is_down(row):
        return False, row

    if module.check_mode:
        row['running'] = False
        row['status'] = 'stopped'
        return True, row

    network.power_off()
    return True, wait_for_power(module, client, name, False)


def restart_network(module, client, network):
    """Restart the vnet's router.

    Always a change. A restart is an event, not a state to converge on: there
    is no reading of the system that means "has already been restarted for
    this reason". RV(need_restart) is what says whether one is outstanding.
    """
    name = module.params['name']
    row = prepare_power_row(module, client, network)

    if not _is_up(row):
        module.fail_json(
            msg="Network '%s' is not running, so there is nothing to restart. "
                "Use state=running to start it." % name)

    if module.check_mode:
        return True, row

    network.restart(apply_rules=module.params['apply_rules'])
    # `before` is the settled row from immediately before the action. The
    # wait compares its start timestamp with what comes back, because
    # need_restart clears on accept and status can still say `running`
    # for about a second after that (#168).
    return True, wait_for_restart(module, client, name, row)


def main():
    argument_spec = vergeos_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', default='present',
                   choices=['present', 'absent', 'running', 'stopped',
                            'restarted']),
        apply_rules=dict(type='bool', default=True),
        power_timeout=dict(type='int', default=60),
        description=dict(type='str'),
        network_type=dict(type='str', choices=['internal', 'external', 'dmz']),
        ip_address=dict(type='str'),
        network=dict(type='str'),
        gateway=dict(type='str'),
        dhcp_enabled=dict(type='bool'),
        dhcp_start=dict(type='str'),
        dhcp_end=dict(type='str'),
        layer2_type=dict(type='str', choices=['vlan', 'vxlan', 'none']),
        vlan_id=dict(type='int'),
        dns_servers=dict(type='list', elements='str'),
        domain=dict(type='str'),
        mtu=dict(type='int'),
        interface_network=dict(type='str'),
        rate_limit=dict(type='int'),
        on_power_loss=dict(type='str',
                           choices=['power_on', 'last_state', 'leave_off']),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True
    )

    client = get_vergeos_client(module)
    name = module.params['name']
    state = module.params['state']

    try:
        network = get_network(module, client, name)

        if state == 'absent':
            if network:
                stopped_first = is_running(dict(network)) is True
                delete_network(module, client, network)
                module.exit_json(
                    changed=True,
                    msg=f"Network '{name}' deleted"
                        + (' (stopped first)' if stopped_first else ''))
            else:
                module.exit_json(changed=False, msg=f"Network '{name}' does not exist")

        # The power states imply present. Configuration is converged first,
        # so a vnet this run powers on comes up with the settings the play
        # asked for rather than with whatever it had before.
        if network:
            changed, row = update_network(module, client, network)
        else:
            changed, row = create_network(module, client)

        if changed and not module.check_mode:
            # Re-read through the named projection. Neither create() nor
            # save() returns a row carrying `running` -- it is a join, not a
            # column -- so the object they hand back cannot answer the
            # question the power branch is about to ask. Only when something
            # changed: an unchanged run already holds the row get_network()
            # fetched, and a second call for the same answer is a second call.
            network = get_network(module, client, name)
            if network is not None:
                row = dict(network)

        if state == 'present':
            module.exit_json(changed=changed, **power_result(row))

        if module.check_mode and network is None:
            # A network that does not exist yet, in check mode. There is
            # nothing to power, and saying so beats reporting a power change
            # against an object that was never created.
            module.exit_json(
                changed=True,
                msg=f"check mode: would create '{name}' and set it {state}",
                **power_result(row))

        if state == 'running':
            powered, row = power_on_network(module, client, network)
        elif state == 'stopped':
            powered, row = power_off_network(module, client, network)
        else:
            powered, row = restart_network(module, client, network)

        module.exit_json(changed=changed or powered, **power_result(row))

    except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
        sdk_error_handler(module, e)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")


if __name__ == '__main__':
    main()
