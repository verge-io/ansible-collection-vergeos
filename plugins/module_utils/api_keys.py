# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared field contract for the api_key and api_key_info modules.

Both modules return the same shape, so both have to agree on which API column
each returned name comes from. Two copies of that mapping is exactly the drift
#75 is about: one gets corrected on a live system, the other does not, and the
info module keeps reporting a field the managing module has stopped using.

Every column below was read off a live ``user_api_keys`` row on VergeOS
26.1.8. The table has 11 columns; the 9 used here are real, and ``expires`` is
the one deliberately left alone (see the api_key module's notes).
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type


# Returned name -> API column. The renames are real: the API spells the
# last-login pair lastlogin_*, not last_login_*.
RESULT_FIELD_MAP = {
    'key': '$key',
    'name': 'name',
    'user_name': 'user_name',
    'description': 'description',
    'created': 'created',
    'ip_allow_list': 'ip_allow_list',
    'ip_deny_list': 'ip_deny_list',
    'last_login': 'lastlogin_stamp',
    'last_login_ip': 'lastlogin_ip',
}

# Asked for explicitly rather than relying on the SDK's default projection.
# A column that is read but not fetched comes back as None and the comparison
# silently succeeds or silently fails -- that is #18's shape. user_name in
# particular is a join, not a stored column, and the module MATCHES on it.
LIST_FIELDS = sorted(set(RESULT_FIELD_MAP.values()))

# Columns whose stored form is a comma-joined string, presented as a list.
_LIST_VALUED = ('ip_allow_list', 'ip_deny_list')

# Columns presented as '' rather than None when empty.
_STRING_VALUED = ('description', 'lastlogin_ip')


def join_ip_list(value):
    """Module list parameter -> the API's comma-joined string.

    The SDK's create() runs its own serialize_list(), so a list reaches the
    API correctly either way on 26.1.8 (measured). update() does not -- it
    PUTs raw kwargs -- so the joining has to happen here, and doing it on both
    paths keeps them from drifting apart.
    """
    if not value:
        return ''
    return ','.join(value)


def split_ip_list(value):
    """The API's comma-joined string -> a list for return values."""
    if not value:
        return []
    return [item for item in str(value).split(',') if item]


def key_result(key_obj):
    """One API key row, in the shape both modules document.

    Never includes the secret: it is not stored on the row, and could not be
    returned here even if a caller wanted it.
    """
    data = dict(key_obj)
    result = {}
    for name, column in RESULT_FIELD_MAP.items():
        value = data.get(column)
        if column in _LIST_VALUED:
            value = split_ip_list(value)
        elif column in _STRING_VALUED:
            value = value or ''
        result[name] = value
    return result
