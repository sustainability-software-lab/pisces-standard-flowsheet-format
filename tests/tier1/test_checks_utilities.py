# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: utilities checks UTIL-01 (id uniqueness), UTIL-02 (unused), UTIL-04
# (composition). Import-light. UTIL-03 (unit parseability) is Tier 2.

import importlib.util
import unittest
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_validate.py")


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_util_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = load_validate_module()


def ctx(**sections):
    return V._Context(sections)


class TestUtilityIdUniqueness(unittest.TestCase):
    def test_unique_across_groups_passes(self):
        c = ctx(utilities={"heat_utilities": [{"id": "steam"}],
                           "power_utilities": [{"id": "grid"}]})
        self.assertEqual(V._check_utility_id_uniqueness(c)[0].status, "pass")

    def test_collision_across_groups_fails(self):
        c = ctx(utilities={"heat_utilities": [{"id": "x"}],
                           "power_utilities": [{"id": "x"}]})
        self.assertEqual(V._check_utility_id_uniqueness(c)[0].status, "fail")

    def test_no_utilities_is_vacuous_pass(self):
        # Skipped when: never (sff_checks.md) -- an empty utilities registry
        # has no duplicates, so this is a genuine pass, not a skip.
        c = ctx(utilities={})
        self.assertEqual(V._check_utility_id_uniqueness(c)[0].status, "pass")


class TestUnusedUtilities(unittest.TestCase):
    def test_used_passes(self):
        c = ctx(utilities={"power_utilities": [{"id": "grid"}]},
                units=[{"id": "U", "utility_consumption_results": {"grid": 1.0}}])
        self.assertEqual(V._check_unused_utilities(c)[0].status, "pass")

    def test_unused_is_info(self):
        c = ctx(utilities={"power_utilities": [{"id": "grid"}]},
                units=[{"id": "U"}])
        r = V._check_unused_utilities(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "info"))

    def test_no_utilities_is_vacuous_pass(self):
        # Skipped when: never (sff_checks.md) -- zero utilities are trivially
        # all "referenced", so this is a genuine pass, not a skip.
        c = ctx(utilities={}, units=[{"id": "U"}])
        self.assertEqual(V._check_unused_utilities(c)[0].status, "pass")


class TestUtilityComposition(unittest.TestCase):
    def test_valid_composition_passes(self):
        c = ctx(chemicals=[{"id": "Water"}], utilities={"heat_utilities": [{
            "id": "steam", "composition": [
                {"component_name": "Water", "mol_fraction": 1.0}]}]})
        self.assertEqual(V._check_utility_composition(c)[0].status, "pass")

    def test_dangling_component_is_error(self):
        c = ctx(chemicals=[{"id": "Water"}], utilities={"heat_utilities": [{
            "id": "steam", "composition": [
                {"component_name": "Ghost", "mol_fraction": 1.0}]}]})
        sev = {(r.check_id, r.severity): r.status
               for r in V._check_utility_composition(c)}
        self.assertEqual(sev[("UTIL-04", "error")], "fail")

    def test_bad_fraction_sum_is_warning(self):
        c = ctx(chemicals=[{"id": "Water"}], utilities={"heat_utilities": [{
            "id": "steam", "composition": [
                {"component_name": "Water", "mol_fraction": 0.5}]}]})
        sev = {(r.check_id, r.severity): r.status
               for r in V._check_utility_composition(c)}
        self.assertEqual(sev[("UTIL-04", "warning")], "fail")

    def test_no_composition_is_skipped(self):
        # Skipped when: the composition array is empty/absent (sff_checks.md).
        c = ctx(chemicals=[{"id": "Water"}],
                utilities={"heat_utilities": [{"id": "steam"}]})
        self.assertEqual(V._check_utility_composition(c)[0].status, "skip")


if __name__ == "__main__":
    unittest.main()
