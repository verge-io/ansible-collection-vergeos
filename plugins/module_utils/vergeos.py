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


class VergeOSAPIError(Exception):
    """Custom exception for VergeOS API errors"""
    pass


class VergeOSAPI:
    """
    Base class for interacting with VergeOS API
    Uses HTTP Basic Authentication
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
    Returns argument spec for VergeOS modules
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
