# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""SMART triage for physical drives.

The platform reports SMART attributes and its own vSAN IO errors, each with a
warning flag. It does not rank them, and they are not equally urgent -- a
drive that is merely warm and a drive with uncorrectable sectors both set "a
warning", and treating those the same means either replacing healthy drives or
ignoring failing ones.

The groupings below are the conventional reading of these attributes, not
platform policy, which is why every one of them is overridable by the caller.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

# Media is failing. Reallocated, pending and uncorrectable sectors are all
# the drive telling you it could not read or write something and had to do
# something about it. These do not improve on their own.
CRITICAL_FLAGS = ('realloc_sectors_warn', 'current_pending_sector_warn',
                  'offline_uncorrectable_warn')

# Wearing out or running hot. Both are real and both are actionable, but they
# are plan-a-replacement / check-the-airflow rather than pull-it-now, and a
# hot drive is often the rack's problem rather than the drive's.
WARNING_FLAGS = ('wear_level_warn', 'temp_warn')

# Age alone. A drive being old is not a fault, and treating power-on hours as
# a warning makes every long-lived healthy drive shout.
INFO_FLAGS = ('hours_warn',)

# Fields the SDK model renames, model name -> raw row name. Reading only one
# spelling silently reports 0 for everything on the other shape, which is
# indistinguishable from a healthy drive.
RAW = {
    'temperature': 'temp',
    'firmware': 'fw',
    'size_bytes': 'size',
    'smart_enabled': 'smart',
}
# Looked up in BOTH directions. The reason strings are built from the SMART
# flag names, which are the RAW spelling ('temp_warn' -> 'temp'), so a row
# carrying only the model's 'temperature' needs the reverse mapping or the
# reason reads "temp=0" on a drive running at 71 degrees.
MODEL = {raw: model for model, raw in RAW.items()}


def _field(row, name, default=0):
    """Read a field by either its model name or the raw row's name."""
    for candidate in (name, RAW.get(name), MODEL.get(name)):
        if candidate and candidate in row:
            return row[candidate]
    return default


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def classify(row, critical_flags=None, warning_flags=None):
    """Triage one drive row.

    Returns the row with ``severity`` (ok / info / warning / critical) and
    ``reasons`` added.

    vSAN IO errors outrank every SMART flag. A SMART warning is the drive's
    own prediction; a vSAN read or write error is the platform reporting that
    an operation against this drive actually failed. Prediction versus
    measurement -- the measurement wins.
    """
    critical_flags = CRITICAL_FLAGS if critical_flags is None else tuple(critical_flags)
    warning_flags = WARNING_FLAGS if warning_flags is None else tuple(warning_flags)

    out = dict(row)
    reasons = []
    severity = 'ok'

    read_errors = _int(_field(row, 'vsan_read_errors'))
    write_errors = _int(_field(row, 'vsan_write_errors'))
    if read_errors or write_errors:
        severity = 'critical'
        reasons.append(
            'vSAN IO errors (%d read, %d write) -- the platform has actually '
            'failed operations against this drive%s'
            % (read_errors, write_errors,
               (': ' + str(_field(row, 'vsan_last_error', '')))
               if _field(row, 'vsan_last_error', '') else ''))

    for flag in critical_flags:
        if row.get(flag):
            severity = 'critical'
            reasons.append('%s is set (%s=%s)'
                           % (flag, flag[:-5], _field(row, flag[:-5])))

    if severity != 'critical':
        for flag in warning_flags:
            if row.get(flag):
                severity = 'warning'
                reasons.append('%s is set (%s=%s)'
                               % (flag, flag[:-5], _field(row, flag[:-5])))

    if severity == 'ok':
        for flag in INFO_FLAGS:
            if row.get(flag):
                severity = 'info'
                reasons.append('%s is set (%s=%s)'
                               % (flag, flag[:-5], _field(row, flag[:-5])))

    # Not a severity of its own: a drive can be repairing while perfectly
    # healthy (it was just replaced). It is surfaced because pulling a second
    # drive mid-repair is how a rebuild becomes a data-loss event.
    out['repairing'] = bool(_field(row, 'vsan_repairing', False))

    # SMART being off is not a fault, but it means the SMART flags above are
    # all silent -- so a drive with SMART disabled reads as healthy whether it
    # is or not. Worth saying out loud rather than counting as ok.
    smart_off = not bool(_field(row, 'smart_enabled', True))
    if smart_off:
        reasons.append('SMART is not enabled, so its health flags say nothing')
        if severity == 'ok':
            severity = 'info'

    out['severity'] = severity
    out['reasons'] = reasons
    out['smart_enabled'] = not smart_off
    return out


def by_severity(rows, *wanted):
    """Rows whose severity is any of ``wanted``."""
    return [r for r in rows if r.get('severity') in wanted]
