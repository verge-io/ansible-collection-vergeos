# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

from ansible_collections.vergeio.vergeos.plugins.module_utils.recipe_answers import (
    fill_recipe_answers,
)


def _fill(answers, questions, options=None, files=None, tiers=None):
    return fill_recipe_answers(questions, answers, options, files, tiers)


class FilterModule:
    """Fill recipe answers filter."""

    def filters(self):
        return {
            'vergeos_fill_answers': _fill,
        }
