# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 2: QU-02 quantity-unit-string parseability. Uses _unit_is_parseable
# (thermosteam), so gated on SFF_TEST_BIOSTEAM=1.

import importlib.util
import os
import unittest
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_validate.py")
RUN = os.environ.get("SFF_TEST_BIOSTEAM") == "1"


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_qu02_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(RUN, "set SFF_TEST_BIOSTEAM=1 (imports thermosteam)")
class TestQuantityUnitStringsParseable(unittest.TestCase):
    def setUp(self):
        self.V = load_validate_module()

    def test_parseable_registry_passes(self):
        c = self.V._Context({"quantity_units_global": {
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": "kg/hr"},
            "price": {"aliases": ["price"], "quantity_units": "USD/kg"}}})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "pass")

    def test_empty_design_unit_allowed(self):
        # '' is the documented dimensionless sentinel for design results.
        c = self.V._Context({"units": [{"id": "U",
            "quantity_units_for_design_results": {"Number of trays": ""}}]})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "pass")

    def test_empty_registry_unit_is_error(self):
        c = self.V._Context({"quantity_units_global": {
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": ""}}})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "fail")

    def test_gibberish_is_error(self):
        c = self.V._Context({"utilities": {"heat_utilities": [
            {"id": "s", "quantity_units_for_utility_results": "zorp!!"}]}})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "fail")

    def test_no_quantity_unit_strings_is_vacuous_pass(self):
        # QU-02 is "Skipped when: never" (sff_checks.md) -- zero
        # quantity-unit strings present is a vacuous pass, not a skip.
        c = self.V._Context({})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "pass")


if __name__ == "__main__":
    unittest.main()
