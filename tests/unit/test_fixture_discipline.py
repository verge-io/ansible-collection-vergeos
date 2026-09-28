# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Guards against the ways a test in this repo has passed without testing.

Every rule here was written after a real bug, not in anticipation of one.
Each names the bug it would have caught.

Two distinct root causes, and they need different guards:

  A. INVENTED FIXTURES. The code reads a field name the author believed in,
     the fixture asserts the same belief, and the two are wrong together
     (B2, B4, B6). Guarded by api_fixtures.row(), which refuses a field the
     platform did not send, plus the self-tests below proving it still does.

  B. VACUOUS MOCKS. The test runs but exercises nothing, so it would pass
     against broken code (B16). Guarded by the source scans below.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import pathlib
import re

import pytest

from ansible_collections.vergeio.vergeos.tests.unit import api_fixtures as F

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEST_FILES = sorted((ROOT / 'tests' / 'unit').rglob('test_*.py'))


def _read(path):
    return path.read_text(encoding='utf-8')


# ── A. the captured-fixture mechanism still works ────────────────────────────

def test_there_are_captured_fixtures_at_all():
    assert F.available(), (
        'tests/fixtures/api is empty. Run tests/capture_api_fixtures.py '
        'against a real system.')


def test_every_capture_records_the_call_that_produced_it():
    """Without the call, a capture cannot be checked against anything.

    'running' is present in client.nodes.list() and absent from
    GET /nodes?fields=all. The same membership row is 'users/1' through the
    SDK and '/v4/users/2' through GET /members?fields=all. The shape belongs
    to the call, not the table.
    """
    for name in F.available():
        assert F.call_for(name), 'capture %r does not say where it came from' % name


@pytest.mark.parametrize(('fixture', 'invented', 'bug'), [
    ('nodes', 'online', 'B4'),
    ('nodes', 'needs_restart', 'B4'),
    ('group_members', 'member_name', 'B2'),
    ('group_members', 'member_type', 'B2'),
    ('update_settings', 'is_installed', 'B6'),
    ('update_settings', 'is_reboot_required', 'B6'),
])
def test_row_refuses_the_field_names_that_caused_real_bugs(fixture, invented, bug):
    """The regression test for the guard itself.

    Each of these is a field name that was actually read by shipped code and
    actually asserted by a passing unit test, while the platform sent
    something else.
    """
    with pytest.raises(AssertionError) as caught:
        F.row(fixture, **{invented: True})
    assert invented in str(caught.value)
    assert F.call_for(fixture) in str(caught.value), (
        'the refusal should show which call was captured, so the reader can '
        'go and capture a different one')


@pytest.mark.parametrize(('fixture', 'real'), [
    ('nodes', 'running'),
    ('nodes', 'need_restart'),
    ('nodes', 'maintenance'),
    ('group_members', 'member'),
    ('group_members', 'member_display'),
    ('update_settings', 'installed'),
    ('update_settings', 'reboot_required'),
    ('cluster_status', 'used_ram'),
    ('cluster_status', 'online_ram'),
])
def test_row_accepts_the_field_names_the_platform_really_sends(fixture, real):
    """The other half. A guard that refused everything would also 'pass'."""
    assert real in F.fields(fixture)
    F.row(fixture, **{real: 0})


def test_row_refuses_to_build_on_an_empty_capture():
    """An empty capture means "nothing of this kind is configured", which is
    not the same as "this row has no fields". Building on it would invent a
    shape."""
    empty = [n for n in F.available() if not F.rows(n)]
    if not empty:
        pytest.skip('every capture has rows on the system that produced them')
    with pytest.raises(AssertionError, match='captured zero rows'):
        F.row(empty[0])


# ── B. mocks that cannot pass vacuously ──────────────────────────────────────

def test_no_test_stubs_pyvergeos_out_of_sys_modules():
    """B16. Replacing pyvergeos.exceptions with a MagicMock makes
    NotFoundError a Mock attribute rather than an exception class.
    unittest.mock treats a non-exception side_effect as a CALLABLE -- it calls
    it and returns the result instead of raising -- so a test asserting the
    not-found path never entered it.

    pyvergeos is a declared requirement; there is nothing to stub.
    """
    offenders = [str(p.relative_to(ROOT)) for p in TEST_FILES
                 if re.search(r"patch\.dict\(\s*['\"]sys\.modules['\"]", _read(p))
                 and 'pyvergeos' in _read(p)]
    assert not offenders, (
        'these files stub pyvergeos into sys.modules, which turns its '
        'exceptions into Mocks that are called instead of raised: %s'
        % ', '.join(offenders))


def test_no_test_patches_a_name_where_it_is_defined_rather_than_used():
    """B16. Modules do `from ...module_utils.vergeos import get_vergeos_client`,
    which binds the name in the MODULE's namespace at import. Patching
    module_utils.vergeos rebinds nothing the module will consult, so the
    module builds a real client and the test's mock is never used.
    """
    pattern = re.compile(
        r"patch\(\s*['\"][^'\"]*module_utils\.vergeos\.(get_vergeos_client|HAS_PYVERGEOS)")
    # test_vergeos.py is the test FOR module_utils.vergeos, so for it that
    # target is where the name is looked up. Exempting it by name rather than
    # by a looser pattern, so the exemption stays visible.
    subject_is_module_utils_vergeos = 'module_utils/test_vergeos.py'
    offenders = [str(p.relative_to(ROOT)) for p in TEST_FILES
                 if pattern.search(_read(p))
                 and subject_is_module_utils_vergeos not in p.as_posix()]
    assert not offenders, (
        'patch where the name is looked up (the module under test), not where '
        'it is defined: %s' % ', '.join(offenders))


def test_no_test_fakes_a_row_by_assigning_dunder_iter():
    """B16. dict() prefers the mapping protocol and MagicMock auto-provides
    keys(), so an __iter__ override is ignored and dict(mock) is {}. Seven
    fixtures did this; one test failed on it and six passed regardless of what
    the row contained.

    Use a real dict, or api_fixtures.row().
    """
    pattern = re.compile(r'\.__iter__\s*=\s*lambda')
    offenders = [str(p.relative_to(ROOT)) for p in TEST_FILES
                 if pattern.search(_read(p))]
    assert not offenders, (
        'dict() ignores __iter__ when keys() exists, so these rows decode as '
        '{}: %s' % ', '.join(offenders))


# ── C. a capture is a public artefact ────────────────────────────────────────
#
# These files are committed, so whatever the capture system happened to be
# called ships with them. Field-name redaction covers the fields somebody
# thought of; it did not cover cluster_name, clusters.name, member_display or
# creator, and the captures made before this guard existed carried the lab's
# cluster name in two files and an operator's username in a third.

CAPTURE_SCRIPT = ROOT / 'tests' / 'capture_api_fixtures.py'


def _captures():
    return sorted((ROOT / 'tests' / 'fixtures' / 'api').glob('*.json'))


def test_every_capture_records_that_it_was_identity_scrubbed():
    """The placeholders are recorded, not the values they replaced.

    An empty list is a legitimate answer -- it means there was nothing to
    alias. A MISSING list means the capture predates the scrubbing, which is
    the state the leaked ones were in.
    """
    import json
    for path in _captures():
        meta = json.loads(path.read_text(encoding='utf-8')).get('_meta', {})
        assert 'identities_substituted' in meta, (
            "%s does not record an identity substitution, so it was written "
            "by a capture script that did not do one. Re-run "
            "tests/capture_api_fixtures.py." % path.name)


@pytest.mark.parametrize('path', _captures(), ids=lambda p: p.name)
def test_no_capture_publishes_an_address_a_home_path_or_an_address_book(path):
    """The shapes this repository must never publish, checked where they would
    actually arrive. A network capture is a list of the site's subnets; a
    user capture is a list of who works there."""
    private = re.compile(r'\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))'
                         r'\.\d{1,3}\.\d{1,3}\b')
    home = re.compile(r'/home/[A-Za-z0-9_.-]+')
    email = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')

    text = path.read_text(encoding='utf-8')
    found = sorted(set(private.findall(text)) | set(home.findall(text))
                   | {m for m in email.findall(text)
                      if not m.endswith('@example.com')})
    assert not found, '%s would publish %s' % (path.name, ', '.join(found))


def test_the_capture_script_refuses_rather_than_relying_on_this_test():
    """A guard that only fails in CI has already let the value be written to
    disk, where the next `git add -A` finds it. The script checks its own
    output and declines to write the file."""
    source = CAPTURE_SCRIPT.read_text(encoding='utf-8')
    assert 'def leaks(' in source, (
        'capture_api_fixtures.py has no leak check of its own')
    assert 'NOT WRITTEN' in source, (
        'capture_api_fixtures.py does not refuse to write a leaking capture')
