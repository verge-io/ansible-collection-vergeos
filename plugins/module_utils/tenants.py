# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""The tenant field contract, shared by `tenant` and `tenant_info`.

Two modules read the same three tables. If they read them through different
projections they report different things about the same tenant, and the one
that is wrong is whichever was edited second. The same reasoning as
module_utils/api_keys.py.

Every name below was checked against a live row on VergeOS 26.1.8 before the
modules were touched -- see issue #75.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

GB = 1073741824

# Module parameter -> raw API column on `tenants`.
#
# TenantManager.update() is a raw passthrough: it PUTs whatever kwargs it is
# handed, so these values must be COLUMN names. That is the opposite of
# nas_volume, whose update() translates its keywords, and the reason both
# modules say which namespace their map is in rather than leaving it implied.
UPDATE_FIELD_MAP = {
    'description': 'description',
    'url': 'url',
    'note': 'note',
    'expose_cloud_snapshots': 'expose_cloud_snapshots',
    'allow_branding': 'allow_branding',
}

# create() is NOT a passthrough: it takes friendly keywords and builds the
# body itself, with one rename -- require_password_change becomes the
# `change_password` column. Recorded here rather than discovered later.
CREATE_PARAM_MAP = dict(UPDATE_FIELD_MAP, name='name',
                        tenant_password='password',
                        require_password_change='change_password')

# A tenant cannot be renamed by this module, and the initial password and the
# force-change flag are creation facts rather than settings: neither can be
# read back to compare against, so neither can converge.
IDENTITY_PARAMS = ('name', 'tenant_password', 'require_password_change')

COMPARISON_FIELDS = tuple(sorted(set(UPDATE_FIELD_MAP.values())))

# `running` and `status` are NOT columns on `tenants`. They arrive as joins on
# the status row, and a module that compares a column it never fetched reads
# None forever (#18, #92, #97). Naming them keeps this working if the SDK's
# default projection ever changes.
TENANT_FIELDS = ['$key', 'name', 'is_snapshot'] + list(COMPARISON_FIELDS) + [
    'status#running as running',
    'status#status as status',
]

# `host_node` is the one that matters for issue #24: the platform's own record
# of which physical node a tenant node landed on. It stays empty when the node
# was never placed, which is what separates "no room" from "slow to boot".
NODE_FIELDS = ['$key', 'name', 'cpu_cores', 'ram',
               'machine#status#status as status',
               'machine#status#running as running',
               'machine#status#node#$display as host_node']

# The `tier` COLUMN on tenant_storage holds the storage tier's KEY. The tier
# NUMBER -- what the module's `tier` option means, and what the user typed --
# exists only as the join below. Reading `tier` and comparing it to 1 would
# match the wrong allocation, or none at all.
STORAGE_FIELDS = ['$key', 'provisioned', 'used', 'tier#tier as tier_number']


def tenant_key(tenant):
    """The tenant's key as an int.

    The API returns it as a string on some projections and an int on others.
    ``power_on`` puts it in a JSON body rather than in a URL, so the type is
    not cosmetic.
    """
    return int(dict(tenant)['$key'])
