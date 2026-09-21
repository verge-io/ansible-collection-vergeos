# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared helpers for the platform-update modules.

The update lifecycle on VergeOS is system-wide up to the point of applying:
check -> download -> install, all against the cloud as a whole. Only the
final apply is per node, as a restart.

Applying is NOT hand-rolled here. The platform already does a rolling reboot
-- pyvergeos ``update_settings.update_all(force=)`` posts
``{action: 'all'}`` to ``update_actions``, documented as "Reboot nodes one at
a time with workload migration", and ``nodes.restart()`` posts to
``nodes/{key}/maintenance_reboot``, documented as "safely reboots the node by
first migrating workloads and then restarting". Reimplementing
drain -> wait -> restart on top of those means owning two race conditions the
platform already handles, and the first version of the rolling_update role
got one of them wrong (bug B10: it read ``maintenance=True`` as "evacuation
finished", which the platform sets in under a second while RAM is still
resident).

What is left for a role to add is the part the platform does not do: refusing
to start when the cluster cannot survive losing a node. See
module_utils/clusters.py.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

# The ordered lifecycle. Each state implies the ones before it, which is what
# lets a single `state:` parameter be idempotent -- asking for 'downloaded'
# when updates are already installed is satisfied, not a regression.
STAGES = ('checked', 'downloaded', 'installed')

# 'applied' sits outside STAGES on purpose. The first three are idempotent by
# consequence -- the platform records that updates are installed, so asking
# again is satisfiable without doing anything. Applying is a reboot; there is
# no state in which "apply again" is a no-op, so it must not be reachable by
# implication from a lower stage.
APPLIED = 'applied'


# Model property -> the raw row's field name. Read off a live VergeOS 26.1.8
# update_settings row, not inferred: the raw row drops the "is_" prefix
# entirely and uses *_display for the two names.
#
#   raw: installed, reboot_required, applying_updates, auto_update,
#        branch_display, source_display
#
# The first version of this file used the property names for the fallback too,
# which made the fallback dead code -- it looked like a safety net and could
# never fire.
RAW_FIELD = {
    'is_installed': 'installed',
    'is_reboot_required': 'reboot_required',
    'is_applying_updates': 'applying_updates',
    'is_auto_update': 'auto_update',
    'branch_name': 'branch_display',
    'source_name': 'source_display',
}


def _prop(settings, name, default=None):
    """Read a model property, or the raw row field behind it.

    The SDK exposes these as properties, but dict() conversion drops them and
    a row fetched with a narrower projection is a plain mapping. Reading only
    the property means an update check silently reports 'nothing pending' on
    the shape it does not recognise -- the answer that quietly skips an
    upgrade.
    """
    value = getattr(settings, name, None)
    if value is not None:
        return value
    try:
        row = dict(settings)
    except (TypeError, ValueError):
        return default
    for candidate in (name, RAW_FIELD.get(name)):
        if candidate and row.get(candidate) is not None:
            return row[candidate]
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


def apply_installed(settings, force=False):
    """Apply installed updates by rolling the nodes, via the platform's own
    action.

    ``update_all`` is the documented route -- download, install, then reboot
    nodes one at a time with workload migration. Reached through the SDK's
    private ``_action`` when only the apply half is wanted, because pyvergeos
    wraps ``check``, ``download``, ``install`` and ``all`` but not ``apply``,
    even though ``update_sources`` accepts it (``refresh, download, install,
    apply, all``) and ``_action`` passes an unmapped name straight through.

    ``force`` permits nodes carrying workloads that cannot be migrated -- GPU
    passthrough, for instance -- to reboot those workloads rather than
    stalling. It is a data-availability decision, so it is never implied.
    """
    action = getattr(settings, '_action', None)
    if action is None:
        # Older SDKs without the private helper: update_all is the closest
        # public equivalent. It re-runs download and install first, which are
        # no-ops once installed, and then reboots.
        return settings.update_all(force=force)
    return action('apply', force=force)
