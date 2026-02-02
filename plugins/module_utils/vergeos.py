#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

"""
VergeOS Ansible Collection - Module Utilities

This module provides shared utilities for all VergeOS Ansible modules.
Requires the pyvergeos SDK: pip install pyvergeos
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

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
            msg="The pyvergeos SDK is required for this module. "
                "Install it with: pip install pyvergeos"
        )

    # Strip protocol prefix if present (SDK expects hostname only)
    host = module.params['host']
    if host.startswith('https://'):
        host = host[8:]
    elif host.startswith('http://'):
        host = host[7:]

    return VergeClient(
        host=host,
        username=module.params['username'],
        password=module.params['password'],
        verify_ssl=not module.params.get('insecure', False)
    )


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


def vergeos_argument_spec():
    """
    Returns argument spec for VergeOS modules.

    Includes authentication parameters with environment variable fallbacks:
    - host: VERGEOS_HOST
    - username: VERGEOS_USERNAME
    - password: VERGEOS_PASSWORD
    - insecure: VERGEOS_INSECURE
    """
    return dict(
        host=dict(
            type='str',
            required=True,
            fallback=(env_fallback, ['VERGEOS_HOST'])
        ),
        username=dict(
            type='str',
            required=True,
            fallback=(env_fallback, ['VERGEOS_USERNAME'])
        ),
        password=dict(
            type='str',
            required=True,
            no_log=True,
            fallback=(env_fallback, ['VERGEOS_PASSWORD'])
        ),
        insecure=dict(
            type='bool',
            default=False,
            fallback=(env_fallback, ['VERGEOS_INSECURE'])
        )
    )
