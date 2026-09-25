# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""
VergeOS Ansible Collection - Module Utilities

This module provides shared utilities for all VergeOS Ansible modules.
Requires the pyvergeos SDK: pip install pyvergeos
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import sys

from ansible.module_utils.basic import env_fallback

# SDK Integration
try:
    from pyvergeos import VergeClient
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )
    HAS_PYVERGEOS = True
except ImportError:
    HAS_PYVERGEOS = False
    # Define placeholder exceptions for type hints when SDK not installed
    NotFoundError = Exception
    AuthenticationError = Exception
    ValidationError = Exception
    APIError = Exception
    VergeConnectionError = Exception


def get_vergeos_client(module):
    """
    Create a VergeClient instance from Ansible module params.

    Args:
        module: AnsibleModule instance with vergeos_argument_spec() params

    Returns:
        VergeClient: Configured SDK client

    Raises:
        module.fail_json if pyvergeos is not installed
    """
    if not HAS_PYVERGEOS:
        module.fail_json(
            msg="The pyvergeos SDK is required for this module, and it is "
                "not importable from the interpreter this module ran under: "
                "%s.\n"
                "If pyvergeos IS installed (e.g. in a virtualenv), Ansible is "
                "simply running the module under a different interpreter. "
                "That happens when a play targets a named inventory host, "
                "because Ansible then discovers an interpreter instead of "
                "reusing the one running ansible-playbook. Either add "
                "`delegate_to: localhost` to the task, or pin "
                "`ansible_python_interpreter` for the host.\n"
                "Otherwise install it with: pip install pyvergeos"
                % sys.executable
        )

    # Strip protocol prefix if present (SDK expects hostname only)
    host = module.params['host']
    if host.startswith('https://'):
        host = host[8:]
    elif host.startswith('http://'):
        host = host[7:]

    # Auth method: api_key takes precedence when set (the SDK's
    # connect() prefers token over username/password). Otherwise
    # fall back to BASIC auth via username + password.
    api_key = module.params.get('api_key') or None

    return VergeClient(
        host=host,
        username=module.params.get('username') or "",
        password=module.params.get('password') or "",
        token=api_key,
        verify_ssl=not module.params.get('insecure', False)
    )


# Fields a module must name when it needs a vnet's power state.
#
#   need_fw_apply   a real vnet column
#   running         NOT a column. It is a join through the router machine,
#                   which the SDK requests as
#                   "machine#status#running as running". A raw
#                   GET /vnets?fields=all does not return it at all, measured
#                   on 26.1.8. Asking for the bare name happens to work on
#                   pyvergeos 1.6.1 because the SDK maps it; the alias form is
#                   what works on the 1.2.7 floor, which network_info already
#                   learned the hard way (#25).
#
# Getting this wrong is not a small bug: `running` reads as None, every
# network looks stopped, and a module that branches on it silently stops doing
# its job while reporting success. It lives here, not in each module, because
# two copies of one field contract is the drift #75 exists to stop.
VNET_STATUS_FIELDS = [
    '$key',
    'name',
    'need_fw_apply',
    'machine#status#running as running',
]


def resolve_one(module, manager, name, kind, **list_kwargs):
    """Resolve a name to exactly one object, or refuse to guess.

    VergeOS does not enforce unique names on the tables this collection looks
    up by name. The SDK's ``get(name=...)`` is a documented *single*-get: it
    issues ``list(filter="name eq <quoted>", limit=1)[0]``, so it returns the
    FIRST match and cannot see, let alone report, a second. Confirmed on
    26.1.8 -- two catalogs created with the same name both succeed, and
    ``get(name=)`` silently returns one of them.

    That is fine for the SDK, whose contract is a single-get. It is not fine
    here, because the collection offers name-based UX over those tables and
    then updates and deletes what it finds (issue #72).

    Matching is done CLIENT-SIDE rather than with ``list(name=...)``.
    ``get(name=)`` returns the first row and cannot report a second, and
    this collection then updates and deletes what it finds (#72).
    pyVergeOS#100 used to be a second reason: ``quote_value()`` stripped
    ``{`` from a filter literal, so a braced name resolved to a different
    object. That fix shipped in pyvergeos 1.2.8, which is now the floor
    (published as 1.6.1). Client-side equality stays, because of the
    ambiguous-name case, and it has no escaping surface.
    ``member.py`` already routed around ``get(name=)`` for the related
    apostrophe reason; this generalises that.

    Args:
        module: AnsibleModule, used to fail loudly on ambiguity.
        manager: an SDK manager, e.g. ``client.vms``.
        name: the name to resolve.
        kind: human-readable noun for messages, e.g. "VM".
        **list_kwargs: forwarded to ``manager.list()`` -- ``fields=`` to limit
            the projection, or extra selectors such as ``category_name=``.

    Returns:
        The single matching object.

    Raises:
        NotFoundError: when nothing matches. This preserves the contract of
            the ``get(name=)`` it replaces, so existing ``try/except
            NotFoundError`` blocks keep working unchanged.
        Calls ``module.fail_json`` when more than one object matches.
    """
    fields = list_kwargs.get('fields')
    if fields is not None and 'all' not in fields and 'name' not in fields:
        # Matching needs the name back. A projection that omits it would make
        # every row compare unequal and the lookup would silently find nothing.
        list_kwargs = dict(list_kwargs, fields=list(fields) + ['name'])

    matches = [obj for obj in manager.list(**list_kwargs)
               if dict(obj).get('name') == name]

    if not matches:
        raise NotFoundError("%s with name '%s' not found" % (kind, name))

    if len(matches) > 1:
        keys = [str(dict(o).get('$key')) for o in matches]
        module.fail_json(
            msg="Found %d %s objects named '%s' (keys: %s); refusing to guess "
                "which one you meant. VergeOS does not enforce unique names on "
                "this table. Delete the duplicates, or rename them so the "
                "target is unambiguous."
                % (len(matches), kind, name, ', '.join(keys)))

    return matches[0]


def sdk_error_handler(module, e):
    """
    Map pyvergeos SDK exceptions to module.fail_json() calls.

    Args:
        module: AnsibleModule instance
        e: Exception from pyvergeos SDK
    """
    if isinstance(e, NotFoundError):
        module.fail_json(msg=f"Resource not found: {e}")
    elif isinstance(e, AuthenticationError):
        module.fail_json(msg=f"Authentication failed: {e}")
    elif isinstance(e, ValidationError):
        module.fail_json(msg=f"Validation error: {e}")
    elif isinstance(e, VergeConnectionError):
        module.fail_json(msg=f"Connection failed: {e}")
    elif isinstance(e, APIError):
        module.fail_json(msg=f"API error: {e}")
    else:
        module.fail_json(msg=f"Unexpected error: {e}")


def cloudinit_datasource_differs(current, desired):
    """True when the VM's cloudinit_datasource is not the requested value.

    The platform stores the lowercase form (``nocloud``, ``none``). A match
    is not a write: putting the same value again is what made every run
    report changed (#125).
    """
    return str(current or '').lower() != str(desired or '').lower()


def _normalize_cloudinit_render(value):
    """Map a render value to the form the API stores.

    Modules send the friendly name ``No``. The list row comes back ``no``.
    Those are the same setting, and treating them as different rewrites the
    file on every run.
    """
    text = str(value or '').strip().lower()
    return {
        'no': 'no',
        'variables': 'variables',
        'jinja2': 'jinja2',
    }.get(text, text)


def cloudinit_file_needs_update(client, file_key, contents, stored_render,
                                desired_render='No'):
    """True when the stored cloud-init file is not what would be written.

    ``GET cloudinit_files/<key>`` does not return contents, including with
    ``fields=all``, ``fields=contents`` and ``fields=most``.
    ``client.cloudinit_files.get_content(key)`` returns the stored contents
    exactly, and it has been on pyvergeos since the collection's 1.2.7 floor.
    ``render`` is on the list row. A missing render is not evidence of drift.
    """
    stored = client.cloudinit_files.get_content(int(file_key))
    if isinstance(stored, bytes):
        stored = stored.decode('utf-8')
    if stored != contents:
        return True
    if stored_render is None:
        return False
    return (_normalize_cloudinit_render(stored_render)
            != _normalize_cloudinit_render(desired_render))


def alias_platform_key(row):
    """Copy the platform identifier ``$key`` onto ``key``.

    VergeOS rows identify themselves with ``$key``. Jinja cannot read that
    name with dot notation, and ansible-core 2.21's validate-modules rejects
    it as a documented return key (``bad-return-value-key``). ``key`` is the
    same value, and it is the name ``vm`` and ``vm_info`` document.

    ``$key`` stays on the row. Roles already read it with brackets, and
    removing it would change the payload those plays see. A row with no
    ``$key`` (a check-mode create, which has not been assigned one) is
    returned unchanged. An existing ``key`` is left alone.
    """
    if not isinstance(row, dict) or '$key' not in row or 'key' in row:
        return row
    aliased = dict(row)
    aliased['key'] = aliased['$key']
    return aliased


def vergeos_argument_spec():
    """
    Returns argument spec for VergeOS modules.

    Includes authentication parameters with environment variable fallbacks:
    - host: VERGEOS_HOST
    - username: VERGEOS_USERNAME
    - password: VERGEOS_PASSWORD
    - api_key: VERGEOS_API_KEY
    - insecure: VERGEOS_INSECURE

    Either (username AND password) OR api_key must be supplied.
    When both are provided, api_key wins (the SDK's connect()
    prefers token-based auth, bypassing 2FA / TOTP on tenants
    that require it for username+password logins).
    """
    return dict(
        host=dict(
            type='str',
            required=True,
            fallback=(env_fallback, ['VERGEOS_HOST'])
        ),
        username=dict(
            type='str',
            required=False,
            fallback=(env_fallback, ['VERGEOS_USERNAME'])
        ),
        password=dict(
            type='str',
            required=False,
            no_log=True,
            fallback=(env_fallback, ['VERGEOS_PASSWORD'])
        ),
        api_key=dict(
            type='str',
            required=False,
            no_log=True,
            fallback=(env_fallback, ['VERGEOS_API_KEY'])
        ),
        insecure=dict(
            type='bool',
            default=False,
            fallback=(env_fallback, ['VERGEOS_INSECURE'])
        )
    )
