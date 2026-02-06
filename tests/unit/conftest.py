#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Pytest configuration and shared fixtures for unit tests"""

import sys
import pytest
from unittest.mock import MagicMock


@pytest.fixture(scope='session', autouse=True)
def setup_collection_path():
    """Ensure the collection is in the Python path"""
    import os
    collection_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if collection_root not in sys.path:
        sys.path.insert(0, collection_root)


@pytest.fixture
def mock_ansible_module():
    """Create a mock AnsibleModule with common defaults"""
    module = MagicMock()
    module.params = {
        'host': 'vergeos.example.com',
        'username': 'admin',
        'password': 'secret',
        'insecure': False
    }
    module.check_mode = False
    return module


@pytest.fixture
def mock_vergeos_client():
    """Create a mock VergeOS SDK client"""
    client = MagicMock()
    client.vms = MagicMock()
    client.networks = MagicMock()
    client.vnets = MagicMock()
    client.users = MagicMock()
    client.groups = MagicMock()
    client.nics = MagicMock()
    client.files = MagicMock()
    client.clusters = MagicMock()
    return client


def create_mock_resource(data):
    """Helper to create mock SDK resource objects that support dict()"""
    mock = MagicMock()
    mock.__iter__ = lambda self: iter(data.items())
    for key, value in data.items():
        setattr(mock, key, value)
    return mock
