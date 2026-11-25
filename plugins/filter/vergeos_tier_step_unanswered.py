# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    drive_tier_has_no_question,
)


def _tier_gap(message, questions):
    return drive_tier_has_no_question(questions, message)


class FilterModule:
    """Tier-gap recipe filter."""

    def filters(self):
        return {
            'vergeos_tier_step_unanswered': _tier_gap,
        }
