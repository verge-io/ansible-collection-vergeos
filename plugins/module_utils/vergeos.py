#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, VergeIO
# MIT License

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import json
import base64
from ansible.module_utils.basic import env_fallback
from ansible.module_utils.urls import open_url
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError

# SDK Integration (v2.0.0+)
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


# =============================================================================
# DEPRECATED: Legacy HTTP client (retained for backward compatibility)
# These classes will be removed in a future version.
# Use get_vergeos_client() and pyvergeos SDK instead.
# =============================================================================

class VergeOSAPIError(Exception):
    """
    Custom exception for VergeOS API errors.

    .. deprecated:: 2.0.0
        Use pyvergeos SDK exceptions instead.
    """
    pass


class VergeOSAPI:
    """
    Base class for interacting with VergeOS API.
    Uses HTTP Basic Authentication.

    .. deprecated:: 2.0.0
        Use get_vergeos_client() and pyvergeos SDK instead.
    """

    def __init__(self, module):
        self.module = module
        self.host = module.params.get('host')
        self.username = module.params.get('username')
        self.password = module.params.get('password')
        self.validate_certs = not module.params.get('insecure', False)
        self.base_url = f"https://{self.host}/api/v4"

        # Create Basic Auth credentials
        credentials = f"{self.username}:{self.password}"
        encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
        self.auth_header = f"Basic {encoded_credentials}"

    def _make_request(self, method, endpoint, data=None, params=None):
        """Make an authenticated API request using Basic Auth"""
        url = f"{self.base_url}/{endpoint}"
        headers = {
            'Authorization': self.auth_header,
            'Content-Type': 'application/json'
        }

        try:
            response = open_url(
                url,
                method=method,
                data=json.dumps(data) if data else None,
                headers=headers,
                validate_certs=self.validate_certs
            )

            if response.code == 204:  # No content
                return None

            response_text = response.read()
            if not response_text:
                return None

            return json.loads(response_text)
        except HTTPError as e:
            try:
                error_body = e.read().decode('utf-8')
            except:
                error_body = str(e)
            raise VergeOSAPIError(f"API request failed: {e.code} - {error_body}")
        except URLError as e:
            raise VergeOSAPIError(f"Connection failed: {str(e.reason)}")
        except Exception as e:
            raise VergeOSAPIError(f"Request error: {str(e)}")

    def get(self, endpoint, params=None):
        """Make a GET request"""
        return self._make_request('GET', endpoint, params=params)

    def post(self, endpoint, data):
        """Make a POST request"""
        return self._make_request('POST', endpoint, data=data)

    def put(self, endpoint, data):
        """Make a PUT request"""
        return self._make_request('PUT', endpoint, data=data)

    def delete(self, endpoint):
        """Make a DELETE request"""
        return self._make_request('DELETE', endpoint)


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
