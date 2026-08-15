# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: self-tests for the shared support libraries under tests/. These are
# tests OF the test infrastructure -- if the inventory or the catalogue parser is
# wrong, every coverage meta-test built on it is wrong in the same direction and
# would still report green. They declare no COVERS: they cover no pisces_sff
# helper, and the coverage meta-test only requires that every inventory entry is
# claimed, not that every class claims something.

import sys
import unittest
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

import _helper_inventory as inv


class TestHelperInventory(unittest.TestCase):
    """The AST-derived inventory of pisces_sff module-level callables.

    Pinned because every Tier 1 and Tier 2 coverage claim is checked against it:
    an inventory that silently loses a module stops requiring tests for it.
    """

    def test_all_eight_modules_are_inventoried(self):
        """
        Every module named in MODULES contributes at least one callable.

        Expected: the set of module prefixes present in inventory() equals
        {'pisces_sff.' + m for m in MODULES}, with no module missing.
        """
        prefixes = {'.'.join(name.split('.')[:2]) for name in inv.inventory()}
        self.assertEqual(prefixes, {f'pisces_sff.{m}' for m in inv.MODULES})

    def test_inventory_finds_a_known_function_and_a_known_class(self):
        """
        The AST walk picks up both `def` and `class` statements at module level.

        Expected: 'pisces_sff._export.get_composition' (a function) and
        'pisces_sff.exceptions.SFFError' (a class) are both in inventory().
        """
        names = inv.inventory()
        self.assertIn('pisces_sff._export.get_composition', names)
        self.assertIn('pisces_sff.exceptions.SFFError', names)

    def test_nested_functions_are_not_inventoried(self):
        """
        Only module-level callables count; a function defined inside another is
        an implementation detail with no independent contract.

        Expected: no inventory entry contains a name defined inside a function
        body -- checked via the known-nested 'wrapper' name used by
        _export.trace_function_calls.
        """
        self.assertNotIn('pisces_sff._export.wrapper', inv.inventory())

    def test_inventory_is_sorted_and_unique(self):
        """
        The inventory is a stable, deduplicated sequence.

        Expected: inventory() equals sorted(set(inventory())) as a tuple, so a
        coverage diff is reproducible run to run.
        """
        names = inv.inventory()
        self.assertEqual(list(names), sorted(set(names)))

    def test_validator_check_functions_are_exempt_from_tiers_1_and_2(self):
        """
        D1: the _check_* functions are Tier 4's subject, exercised end-to-end
        through validate_flowsheet_against_SFF, so they are not also required as
        Tier 1/Tier 2 helper tests.

        Expected: is_exempt() returns a non-empty reason for
        'pisces_sff._validate._check_unit_id_uniqueness' at tiers 1 and 2, and
        that name is absent from required_for_tier(1) and required_for_tier(2).
        """
        name = 'pisces_sff._validate._check_unit_id_uniqueness'
        for tier in (1, 2):
            self.assertTrue(inv.is_exempt(name, tier))
            self.assertNotIn(name, inv.required_for_tier(tier))

    def test_tier_2_exempts_the_four_harness_helpers(self):
        """
        Spec §3: ensure_environment, export_lock, export_model and
        run_model_export are exempt from Tier 2 but required in Tier 1 (the first
        two) or covered by Tier 6 (the last two).

        Expected: all four are absent from required_for_tier(2), and each carries
        a non-empty reason in exemptions_for_tier(2).
        """
        four = (
            'pisces_sff._harness.ensure_environment',
            'pisces_sff._harness.export_lock',
            'pisces_sff._harness.export_model',
            'pisces_sff._runner.run_model_export',
        )
        exemptions = inv.exemptions_for_tier(2)
        for name in four:
            self.assertNotIn(name, inv.required_for_tier(2))
            self.assertTrue(exemptions.get(name))

    def test_every_exemption_carries_a_non_empty_reason(self):
        """
        An exemption without a reason is an untracked coverage hole.

        Expected: every value in EXEMPT is a non-empty string.
        """
        for key, reason in inv.EXEMPT.items():
            with self.subTest(key=key):
                self.assertTrue(reason and reason.strip(), key)

    def test_exempt_keys_all_name_real_callables(self):
        """
        An exemption for a helper that no longer exists is stale bookkeeping that
        would mask a genuine gap if the name were ever reused.

        Expected: every dotted name in EXEMPT appears in inventory(), except the
        rule-based _check_* exemptions which are generated from the inventory
        itself and therefore cannot be stale.
        """
        names = set(inv.inventory())
        for (dotted, tier) in inv.EXEMPT:
            with self.subTest(name=dotted):
                self.assertIn(dotted, names)


if __name__ == '__main__':
    unittest.main()
