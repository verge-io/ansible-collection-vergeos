#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Regression tests for four defects that survived a full unit suite.

The write-up they were originally filed against (docs/DEFECTS-D1-D6.md) is
not in this repository, so what each one was is recorded below rather than
cited. A test whose reasoning lives in a file nobody can open is a test
nobody can safely change.

Each test fails on the unfixed module and passes on the fixed one. They are
written against the SAME shapes measured on a live VergeOS 26.1.8 system, not
against what the modules happen to assume -- that inversion is what let D1-D4
survive a full unit suite in the first place.

Pinned here so an "optimisation" has to argue with a measurement:

  D2  a machine_drives row has NO 'tier' key. It has 'preferred_tier', and
      it is a STRING. pyvergeos >= 1.2.7 translates tier -> preferred_tier
      inside DriveManager.update(), and _save() routes ONLY kwargs through
      update() -- attribute-set fields go out as a raw PUT. So the diff has
      to reach save() as kwargs or the translation is skipped and the
      platform discards the write with HTTP 200.

  D3  the platform rejects an empty cloudinit_datasource; 'none' is the
      value that disables cloud-init.

  D4  'enabled' must not carry a default. With one, every partial update
      also asserts enabled=True and silently re-enables a VM somebody
      deliberately disabled. This was latent until pyvergeos 1.2.7 made
      updates persist at all.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

from unittest.mock import MagicMock, patch

from ansible_collections.vergeio.vergeos.plugins.modules import drive as drive_mod
from ansible_collections.vergeio.vergeos.plugins.modules import vm as vm_mod
from ansible_collections.vergeio.vergeos.plugins.modules import cloud_init as ci_mod


def as_row(mock, row):
    """dict() prefers the mapping protocol and MagicMock auto-provides keys(),
    so an __iter__ override decodes as {}. keys() + __getitem__ is what works.
    """
    mock.keys = row.keys
    mock.__getitem__ = lambda self, key: row[key]
    return mock


# ── D2 ───────────────────────────────────────────────────────────────────────

# A real row, captured from a live system. Note what is NOT in it.
LIVE_DRIVE_ROW = {
    '$key': 12, 'name': 'zz-drive', 'interface': 'virtio-scsi', 'media': 'disk',
    'preferred_tier': '4', 'readonly': False, 'enabled': True, 'machine': 41,
}


def test_live_drive_row_has_no_tier_key():
    """The premise of D2. If this ever fails, the rest of D2 is obsolete."""
    assert 'tier' not in LIVE_DRIVE_ROW
    assert LIVE_DRIVE_ROW['preferred_tier'] == '4'
    assert isinstance(LIVE_DRIVE_ROW['preferred_tier'], str)


def _drive_update(params, row=None):
    drive = as_row(MagicMock(), dict(row or LIVE_DRIVE_ROW))
    drive.save.return_value = as_row(MagicMock(), dict(row or LIVE_DRIVE_ROW))
    module = MagicMock()
    module.params = {'tier': None, 'drive_type': None, 'size': None,
                     'read_only': None, **params}
    module.check_mode = False
    changed, _unused = drive_mod.update_drive(module, MagicMock(), drive)
    return changed, drive


def test_d2_drive_tier_converges_against_preferred_tier():
    """Asking for the tier the drive is already on is not a change.

    Unfixed, this compares row['tier'] (absent -> None) against 4 and reports
    changed forever.
    """
    changed, drive = _drive_update({'tier': 4})
    assert changed is False, "tier 4 on a preferred_tier='4' drive is not a change"
    drive.save.assert_not_called()


def test_d2_drive_tier_is_written_as_preferred_tier_not_the_alias():
    """The API field name goes on the wire, not the friendly alias.

    The live row has no 'tier' key -- it has 'preferred_tier', a string.
    Measured on 26.1.8: PUT {'tier': 1} returns HTTP 200 and is silently
    ignored; PUT {'preferred_tier': '1'} applies.

    Writing preferred_tier directly rather than sending 'tier' and relying
    on DriveManager.update() to translate it. The alias route works only
    for kwargs -- ResourceObject._save() sends attribute-set fields as a
    raw PUT that skips the typed update() (pyvergeos#97) -- and needs
    pyvergeos >= 1.2.7. The API field name needs no SDK cooperation.
    """
    changed, drive = _drive_update({'tier': 1})
    assert changed is True
    drive.save.assert_called_once()
    sent = drive.save.call_args[1]
    assert sent.get('preferred_tier') == '1', \
        "the tier diff must be written as preferred_tier, as a string"
    assert 'tier' not in sent, \
        "the raw 'tier' alias must not be sent -- the platform ignores it"


def test_d2_string_and_int_tiers_compare_equal():
    """preferred_tier is a string; the module parameter is an int."""
    changed, _unused = _drive_update(
        {'tier': 1}, row={**LIVE_DRIVE_ROW, 'preferred_tier': '1'})
    assert changed is False


# ── D3 ───────────────────────────────────────────────────────────────────────

def _remove_ci(current_datasource):
    """Run remove_cloudinit against a VM whose datasource is as given."""
    client = MagicMock()
    module = MagicMock()
    module.check_mode = False
    module.params = {'vm_name': 'v', 'vm_id': None}
    row = {'$key': 7}
    if current_datasource is not None:
        row['cloudinit_datasource'] = current_datasource

    with patch.object(ci_mod, 'get_vm') as get_vm, \
         patch.object(ci_mod, 'delete_cloudinit_files', return_value=False), \
         patch.object(ci_mod, 'enable_cloudinit_datasource') as set_ds:
        get_vm.return_value = as_row(MagicMock(), row)
        try:
            ci_mod.remove_cloudinit(client, module)
        except SystemExit:
            pass
    return set_ds, module


def test_d3_disable_sends_none_never_empty_string():
    """'' is rejected by the platform:
    "value '' is not in list for field 'cloudinit_datasource'".
    """
    set_ds, _unused = _remove_ci('nocloud')

    set_ds.assert_called_once()
    assert set_ds.call_args[0][-1] == 'none', \
        "state=absent must send 'none'; '' is rejected by the platform"


def test_d3_disable_is_idempotent_on_an_already_disabled_vm():
    """An already-disabled VM must report changed=false, not re-send 'none'.

    The guard came from main. Without it, state=absent reported changed
    every run on a VM that had never had cloud-init configured.
    """
    for already_off in ('none', 'NONE', None, ''):
        set_ds, module = _remove_ci(already_off)
        set_ds.assert_not_called()
        assert module.exit_json.call_args[1]['changed'] is False, \
            "disabling an already-disabled VM (%r) must be a no-op" % already_off


def _datasource_choices():
    import re
    src = open(ci_mod.__file__).read()
    m = re.search(r"datasource=dict\(type='str', choices=(\[[^\]]*\])\)", src)
    assert m, "could not find the datasource argspec"
    return m.group(1)


def test_d3_datasource_choices_offer_none():
    """'none' has to be selectable, not just used internally.

    Measured on 26.1.8 against a scratch VM:

        PUT cloudinit_datasource 'nocloud' -> accepted
        PUT cloudinit_datasource 'none'    -> accepted
        PUT cloudinit_datasource ''        -> ValidationError,
                                              "value '' is not in list"
        PUT cloudinit_datasource 'NONE'    -> ValidationError

    A freshly created VM reads 'none'. So 'none' is the only value that turns
    cloud-init off, and it was not offered.
    """
    assert "'none'" in _datasource_choices()


def test_d3_the_empty_string_is_not_offered():
    """It was, and it could not have worked.

    The platform rejects '' outright, and the module never sent it anyway:
    state=present coerces any falsy datasource to 'nocloud'. So the one
    alternative the option offered silently meant the default -- while the
    value a reader would expect it to mean, "off", could not be selected.
    """
    assert "''" not in _datasource_choices()
    assert '""' not in _datasource_choices()


def test_d3_every_offered_choice_is_one_the_platform_accepts():
    """The property, rather than the two examples. A choice list is a promise
    that each value does something."""
    import ast
    choices = ast.literal_eval(_datasource_choices())
    assert set(choices) == {'nocloud', 'none'}


def test_d3_present_defaults_the_datasource_to_nocloud():
    """Checked because the documentation promises it and the argument spec
    does not carry it -- the default is applied in main(), which is easy to
    lose. Without it, cloud_init uploads the files and never turns cloud-init
    on, and the VM boots looking exactly like one that did.
    """
    import re
    src = open(ci_mod.__file__).read()
    block = re.search(r"if module\.params\['state'\] == 'present':(.*?)\n\n",
                      src, re.S).group(1)
    assert "module.params['datasource'] = 'nocloud'" in block, (
        "state=present no longer defaults the datasource; the documented "
        "default would become a no-op")


def test_d3_absent_does_not_inherit_the_present_default():
    """state=absent must send 'none', not 'nocloud'. The default is applied
    inside the state=present branch for exactly this reason."""
    import re
    src = open(ci_mod.__file__).read()
    assert re.search(r"if module\.params\['state'\] == 'present':\s*\n"
                     r"(?:\s*#.*\n)*"
                     r"\s*if not module\.params\.get\('datasource'\):", src), (
        "the nocloud default is no longer scoped to state=present")


def test_d3_hostname_is_not_mutually_exclusive_with_user_data():
    """hostname is a gap-filler: it only generates the document the operator
    did not supply. Blocking the combination blocks the ordinary VM-import
    case (custom user-data + generated meta-data) and nothing else.
    """
    import re
    src = open(ci_mod.__file__).read()
    block = re.search(r"mutually_exclusive=\[(.*?)\]", src, re.S).group(1)
    assert "'hostname', 'user_data'" not in block.replace('"', "'")
    assert "'hostname', 'meta_data'" not in block.replace('"', "'")


def test_d3_gap_filler_keeps_operator_user_data_verbatim():
    """The behaviour that makes relaxing the constraint safe."""
    ud = "#cloud-config\npackages: [htop]\n"
    hostname = 'web01'
    user_data, meta_data = ud, None
    if hostname and not user_data:
        user_data = ci_mod.generate_user_data(hostname)
    if hostname and not meta_data:
        meta_data = ci_mod.generate_meta_data(hostname)
    assert user_data == ud, "operator user-data must survive byte-for-byte"
    assert hostname in meta_data


# ── D4 ───────────────────────────────────────────────────────────────────────

def test_d4_enabled_has_no_default():
    """With a default, params['enabled'] is never None, so update_vm() asserts
    enabled=True on every partial update.
    """
    import inspect
    src = inspect.getsource(vm_mod.main)
    assert "enabled=dict(type='bool')" in src, \
        "enabled must not carry a default -- it re-enables disabled VMs"
    assert "enabled=dict(type='bool', default=True)" not in src


def test_d4_partial_update_leaves_a_disabled_vm_disabled():
    """Behaviour guard, NOT the D4 detector.

    Be clear about what this can and cannot catch. D4 lives in the argument
    spec -- `default=True` is what makes params['enabled'] non-None -- and
    this test builds module.params directly, bypassing the spec. So it passes
    on the unfixed module too, and it would be dishonest to present it as a
    regression test for D4.

    test_d4_enabled_has_no_default() is the detector; it fails on the unfixed
    module. This one pins the behaviour that fix is meant to produce: given
    enabled=None, update_vm() must neither setattr the field nor send it.
    """
    row = {'$key': 1, 'name': 'v', 'enabled': False,
           'description': 'old', 'cpu_cores': 1, 'ram': 512}
    mock_vm = as_row(MagicMock(), dict(row))
    mock_vm.save.return_value = as_row(MagicMock(), dict(row))
    # Seed the attribute too. as_row only wires the mapping protocol, so
    # without this mock_vm.enabled is an auto-MagicMock and the setattr the
    # unfixed module performs is undetectable.
    mock_vm.enabled = False
    module = MagicMock()
    module.params = {k: None for k in
                     ('enabled', 'os_family', 'cpu_cores', 'ram', 'machine_type',
                      'machine_subtype', 'bios_type', 'network', 'boot_order')}
    module.params['description'] = 'new'
    module.check_mode = False

    changed, _unused = vm_mod.update_vm(module, MagicMock(), mock_vm)

    assert changed is True

    # Check BOTH routes the module could use to carry the field, because they
    # differ between the fixed and unfixed forms and an assertion on only one
    # of them passes vacuously on the other:
    #   unfixed -> setattr(vm, 'enabled', True); vm.save()
    #   fixed   -> the field never enters update_data at all
    sent = mock_vm.save.call_args[1] if mock_vm.save.call_args else {}
    assert 'enabled' not in sent, \
        "a description-only update must not carry enabled in the save payload"
    assert mock_vm.enabled is False, \
        ("a description-only update must not setattr enabled; it is now %r"
         % (mock_vm.enabled,))


def test_d4_explicit_enabled_false_is_still_honoured():
    """Removing the default must not remove the ability to disable."""
    row = {'$key': 1, 'name': 'v', 'enabled': True, 'description': 'd'}
    mock_vm = as_row(MagicMock(), dict(row))
    mock_vm.save.return_value = as_row(MagicMock(), dict(row))
    module = MagicMock()
    module.params = {k: None for k in
                     ('description', 'os_family', 'cpu_cores', 'ram',
                      'machine_type', 'machine_subtype', 'bios_type',
                      'network', 'boot_order')}
    module.params['enabled'] = False
    module.check_mode = False

    changed, _unused = vm_mod.update_vm(module, MagicMock(), mock_vm)
    assert changed is True


# ── vm.snapshot_profile ──────────────────────────────────────────────────────
# Added with the `protect` role, which enrols VMs by tag and needs this
# parameter. Same family as D1/D2: a field whose live shape is not what the
# obvious code assumes.
#
# client.vms.get() does NOT include snapshot_profile in its default field
# selection, so dict(vm) reports None no matter what the VM is enrolled in.
# Measured on 26.1.8:
#     default get()               -> None
#     get(fields=[...])           -> 2
#     raw GET ?fields=most        -> 2
# Comparing against the default read made enrolment report changed on every
# run, and made clearing a silent no-op: None or '' == '', so the diff was
# always empty and the VM stayed enrolled.

def _vm_update_with_profile(param, current_profile, profile_key=7):
    """Drive update_vm() with a VM whose real enrolment is current_profile."""
    row = {'$key': 1, 'name': 'v', 'description': 'd'}
    mock_vm = as_row(MagicMock(), dict(row))
    mock_vm.save.return_value = as_row(MagicMock(), dict(row))

    client = MagicMock()
    # The explicit-fields read is the ONLY one that surfaces the field.
    client.vms.get.return_value = as_row(
        MagicMock(), {'name': 'v', 'snapshot_profile': current_profile})
    # resolve_one() lists and matches CLIENT-SIDE rather than calling
    # get(name=) -- see #72 and pyVergeOS#100. Stubbing get() alone leaves
    # list() a bare MagicMock whose rows never match the name, so the lookup
    # raises NotFoundError and the test fails somewhere unrelated.
    client.snapshot_profiles.list.return_value = [
        as_row(MagicMock(), {'$key': profile_key, 'name': 'nightly'})]

    module = MagicMock()
    module.params = {k: None for k in
                     ('description', 'enabled', 'os_family', 'cpu_cores',
                      'ram', 'machine_type', 'machine_subtype', 'bios_type',
                      'network', 'boot_order')}
    module.params['snapshot_profile'] = param
    module.check_mode = False

    changed, _unused = vm_mod.update_vm(module, client, mock_vm)
    return changed, client


def test_vm_snapshot_profile_reads_the_field_with_an_explicit_field_list():
    """The guard. Without fields=[...] the read comes back None and every
    comparison below is meaningless."""
    _unused, client = _vm_update_with_profile('nightly', '7')
    assert client.vms.get.called, "current enrolment was never read"
    _unused, kwargs = client.vms.get.call_args
    assert 'fields' in kwargs, (
        "the current profile must be read with an explicit fields list; "
        "the default selection omits snapshot_profile and returns None")
    assert 'snapshot_profile' in kwargs['fields']


def test_vm_snapshot_profile_is_idempotent_when_already_enrolled():
    """Row holds '7' as a string, the profile $key is int 7."""
    changed, _unused = _vm_update_with_profile('nightly', '7', profile_key=7)
    assert changed is False, "re-enrolling in the same profile is not a change"


def test_vm_snapshot_profile_enrols_when_not_yet_enrolled():
    changed, _unused = _vm_update_with_profile('nightly', '', profile_key=7)
    assert changed is True


def test_vm_empty_snapshot_profile_clears_an_existing_enrolment():
    """The case the default read silently broke."""
    changed, _unused = _vm_update_with_profile('', '7')
    assert changed is True, (
        "passing '' must clear the enrolment; comparing against the default "
        "read makes this a no-op")
