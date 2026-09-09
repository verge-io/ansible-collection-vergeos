# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared helpers for the platform-update modules.

The update lifecycle on VergeOS is system-wide up to the point of applying:
check -> download -> install, all against the cloud as a whole. Only the
final apply is per node, as a restart. That split is why the rolling_update
role drives the first three once and then walks the nodes.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

# The ordered lifecycle. Each state implies the ones before it, which is what
# lets a single `state:` parameter be idempotent -- asking for 'downloaded'
# when updates are already installed is satisfied, not a regression.
STAGES = ('checked', 'downloaded', 'installed')


def _prop(settings, name, default=None):
    """Read a model property or the raw row field behind it.

    The SDK exposes these as properties on the model, but a row fetched with a
    narrower projection is a plain mapping. Reading only the property means an
    update check silently reports 'nothing pending' on the shape it does not
    recognise -- the answer that quietly skips an upgrade.
    """
    value = getattr(settings, name, None)
    if value is not None:
        return value
    try:
        return dict(settings).get(name, default)
    except (TypeError, ValueError):
        return default


def settings_summary(settings):
    """The update facts every caller branches on."""
    return {
        'settings': dict(settings),
        'source': _prop(settings, 'source_name') or '',
        'branch': _prop(settings, 'branch_name') or '',
        'installed': bool(_prop(settings, 'is_installed', False)),
        'reboot_required': bool(_prop(settings, 'is_reboot_required', False)),
        'applying': bool(_prop(settings, 'is_applying_updates', False)),
        'auto_update': bool(_prop(settings, 'is_auto_update', False)),
    }


def stages_to_run(target, summary):
    """Which lifecycle stages still need running to reach ``target``.

    Idempotence is by consequence, not by bookkeeping: the platform records
    that updates are installed, not that a check was performed. So 'installed'
    is skippable when it already holds, while 'checked' and 'downloaded' are
    only skipped when a LATER stage has already been reached -- there is no
    "already checked" flag to read, and re-checking is cheap and harmless.
    """
    if target not in STAGES:
        raise ValueError("unknown update stage %r" % (target,))

    if summary.get('installed'):
        # Everything up to and including install has demonstrably happened.
        return []

    wanted = STAGES[:STAGES.index(target) + 1]
    return list(wanted)
