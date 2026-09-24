#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Pytest configuration and shared fixtures for unit tests"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import sys
import pytest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Import ansible-core BEFORE any test can take a sys.modules snapshot.
#
# Several tests use `patch.dict('sys.modules', {...})` to stub out pyvergeos.
# patch.dict does not patch selectively: on exit it restores the WHOLE module
# table to the snapshot it took on entry, which deletes every module imported
# while the patch was active. The first test to import an ansible plugin pulls
# in `ansible.constants`, which builds a ConfigManager at import time -- and
# that import is then thrown away. Every later test re-triggers it against a
# half-initialised YAML loader and dies with:
#
#     yaml.constructor.ConstructorError: could not determine a constructor
#     for the tag None  in ".../ansible/config/base.yml", line 4, column 1
#
# That was 47 of the 63 "failures" in this suite (see issue #66). It is not an
# ansible-core/PyYAML incompatibility -- importing ansible here, before the
# first snapshot is taken, makes the restore a no-op and all 47 pass.
#
# Keep these imports. They look unused; they are load-bearing.
# ---------------------------------------------------------------------------
import ansible.constants  # noqa: F401  pylint: disable=unused-import
import ansible.plugins.inventory  # noqa: F401  pylint: disable=unused-import


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Unit tests must never touch the network.

    Before #66 every test in tests/unit/plugins/modules/ patched
    `module_utils.vergeos.get_vergeos_client` -- the definition site -- while
    the modules under test had already bound the name locally via `from ...
    import get_vergeos_client`. The patch therefore did nothing, each test
    built a real VergeClient against `vergeos.example.com`, and the suite spent
    over two minutes in DNS timeouts. Some tests still "passed", which is worse
    than failing: they were asserting on the shape of a connection error.

    This fixture makes that failure mode loud and instant instead of slow and
    silent. A unit test that reaches for a socket now fails with a clear
    message naming the cause.
    """
    import socket

    def _blocked(*args, **kwargs):
        raise RuntimeError(
            'A unit test attempted a network connection. Unit tests must mock '
            'the SDK client. Patch get_vergeos_client in the MODULE UNDER TEST '
            '(e.g. plugins.modules.vm.get_vergeos_client), not in '
            'plugins.module_utils.vergeos where it is defined -- the module '
            'binds the name at import time, so patching the definition site '
            'has no effect. See issue #66.'
        )

    monkeypatch.setattr(socket, 'socket', _blocked)
    monkeypatch.setattr(socket, 'create_connection', _blocked)


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
    """Create a mock SDK resource that `dict()` actually converts.

    The obvious idiom -- setting `__iter__` to yield `data.items()` -- does
    NOT work on a MagicMock. `dict(x)` checks for a `keys` attribute first and
    takes the mapping path when it finds one; MagicMock auto-creates `keys`,
    so `dict(mock)` iterates an empty auto-mock and returns `{}`:

        m = MagicMock(); m.__iter__ = lambda self: iter(data.items())
        dict(m)  ->  {}          # silently empty

    Every module here does `vm_dict = dict(vm)` and then compares fields, so an
    empty dict makes every field look absent: "no change" tests saw a change,
    and "not found" tests saw an object. Configure the mapping protocol
    instead, which works:

        dict(create_mock_resource(data))  ->  data

    `keys` and `__getitem__` are wired with side_effect rather than
    return_value so the mock re-reads `data` on every call -- a test can mutate
    the dict to model a state transition (e.g. stopped -> running) and the mock
    follows. See issue #66.
    """
    mock = MagicMock()
    mock.keys.side_effect = lambda: list(data.keys())
    mock.__getitem__.side_effect = data.__getitem__
    mock.__iter__.side_effect = lambda: iter(data)
    for key, value in data.items():
        setattr(mock, key, value)
    return mock


@pytest.fixture
def make_resource():
    """Fixture wrapper around create_mock_resource()."""
    return create_mock_resource


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Unit tests must not spend real time in poll loops.

    `vm.power_on()` / `power_off()` poll with `for _ in range(30): sleep(2)`.
    A mock that never reaches the target state therefore costs 60 wall-clock
    seconds per test. The loop is a fixed iteration count, not a wall-clock
    deadline, so removing the sleep is safe: the loop still runs at most 30
    times, it just does not wait between attempts.
    """
    import time
    monkeypatch.setattr(time, 'sleep', lambda *_a, **_k: None)


# ---------------------------------------------------------------------------
# pyvergeos stubbing, done once and done correctly.
#
# Three test files each carried their own autouse fixture doing
#
#     patch.dict('sys.modules', {'pyvergeos.exceptions': MagicMock()})
#
# which replaces the exceptions module with a plain MagicMock. Its attributes
# are then MagicMocks too -- so `NotFoundError` is not an exception class.
# `mock.side_effect = NotFoundError('x')` therefore does not RAISE; because the
# value is neither an exception nor an iterable, mock RETURNS it. Every test
# that meant "the VM is missing" was silently testing "the VM exists and is a
# MagicMock", which is how three of them asserted the wrong thing for months.
#
# pyvergeos is a hard requirement (requirements.txt: pyvergeos>=1.0.1), so when
# it is importable we leave it completely alone and tests exercise the real
# exception classes. Only when it is genuinely absent do we install a stub --
# and that stub's exceptions are real Exception subclasses, so `raise` and
# `except` both behave.
# ---------------------------------------------------------------------------
try:
    import pyvergeos  # noqa: F401  pylint: disable=unused-import
    import pyvergeos.exceptions  # noqa: F401  pylint: disable=unused-import
    HAS_REAL_SDK = True
except ImportError:
    HAS_REAL_SDK = False


_SDK_EXCEPTIONS = (
    'VergeOSError', 'NotFoundError', 'AuthenticationError',
    'ValidationError', 'APIError', 'VergeConnectionError',
)


def _build_sdk_stub():
    """A stand-in pyvergeos whose exceptions are genuinely raisable."""
    import types

    exceptions = types.ModuleType('pyvergeos.exceptions')
    base = type('VergeOSError', (Exception,), {})
    exceptions.VergeOSError = base
    for name in _SDK_EXCEPTIONS:
        if name != 'VergeOSError':
            setattr(exceptions, name, type(name, (base,), {}))

    root = types.ModuleType('pyvergeos')
    root.exceptions = exceptions
    root.VergeClient = MagicMock()
    return {'pyvergeos': root, 'pyvergeos.exceptions': exceptions}


@pytest.fixture(autouse=True)
def mock_pyvergeos():
    """Leave a real pyvergeos alone; stub a missing one with real exceptions."""
    if HAS_REAL_SDK:
        yield
        return
    with patch.dict('sys.modules', _build_sdk_stub()):
        yield
