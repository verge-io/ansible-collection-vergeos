# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    recipe_not_applicable,
)


class FilterModule:
    """Not-applicable recipe filter."""

    def filters(self):
        return {
            'vergeos_not_applicable': recipe_not_applicable,
        }
