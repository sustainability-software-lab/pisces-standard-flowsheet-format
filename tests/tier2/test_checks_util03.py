# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 2: UTIL-03 utility-result quantity-unit parseability. Uses
# _unit_is_parseable (thermosteam), so gated on SFF_TEST_BIOSTEAM=1.

import importlib.util
import os
import unittest
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_validate.py")
RUN = os.environ.get("SFF_TEST_BIOSTEAM") == "1"


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_util03_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(RUN, "set SFF_TEST_BIOSTEAM=1 (imports thermosteam)")
class TestUtilityResultUnitsParseable(unittest.TestCase):
    def setUp(self):
        self.V = load_validate_module()

    def test_parseable_units_pass(self):
        c = self.V._Context({"utilities": {"power_utilities": [
            {"id": "grid", "quantity_units_for_utility_results": "kW"}]}})
        self.assertEqual(
            self.V._check_utility_result_units_parseable(c)[0].status, "pass")

    def test_empty_units_are_warning(self):
        c = self.V._Context({"utilities": {"power_utilities": [
            {"id": "grid", "quantity_units_for_utility_results": ""}]}})
        r = self.V._check_utility_result_units_parseable(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_gibberish_units_are_warning(self):
        c = self.V._Context({"utilities": {"heat_utilities": [
            {"id": "steam", "quantity_units_for_utility_results": "zorp!!"}]}})
        self.assertEqual(
            self.V._check_utility_result_units_parseable(c)[0].status, "fail")

    def test_no_utilities_is_vacuous_pass(self):
        # Skipped when: never (sff_checks.md) -- the field is schema-required
        # for each group, so an empty utilities registry is a genuine pass.
        c = self.V._Context({"utilities": {}})
        self.assertEqual(
            self.V._check_utility_result_units_parseable(c)[0].status, "pass")


if __name__ == "__main__":
    unittest.main()
