# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 2: the validator's heavy-import helpers -- molar-mass-from-formula
# (chemicals) and unit-string parseability (thermosteam.units_of_measure.ureg).
# Gated on SFF_TEST_BIOSTEAM=1 because thermosteam import is slow/JIT-heavy.

import importlib.util
import os
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATE_PATH = REPO_ROOT / "pisces_sff" / "_validate.py"
RUN = os.environ.get("SFF_TEST_BIOSTEAM") == "1"


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_helpers_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(RUN, "set SFF_TEST_BIOSTEAM=1 (imports chemicals/thermosteam)")
class TestMolarMass(unittest.TestCase):
    def setUp(self):
        self.V = load_validate_module()

    def test_formula_parses(self):
        self.assertAlmostEqual(
            self.V._molar_mass_from_formula("H2O"), 18.01528, places=3)

    def test_bad_formula_returns_none(self):
        self.assertIsNone(self.V._molar_mass_from_formula("not a formula"))

    def test_context_prefers_declared_mass(self):
        ctx = self.V._Context({"chemicals": [
            {"id": "A", "included_in_thermo": False, "molar_mass": 42.0}]})
        self.assertEqual(ctx.molar_mass("A"), 42.0)

    def test_context_falls_back_to_formula(self):
        ctx = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": True, "formula": "H2O"}]})
        self.assertAlmostEqual(ctx.molar_mass("W"), 18.01528, places=3)

    def test_context_none_when_unresolvable(self):
        ctx = self.V._Context({"chemicals": [
            {"id": "X", "included_in_thermo": True}]})
        self.assertIsNone(ctx.molar_mass("X"))


@unittest.skipUnless(RUN, "set SFF_TEST_BIOSTEAM=1 (imports thermosteam)")
class TestUnitParseable(unittest.TestCase):
    def setUp(self):
        self.V = load_validate_module()

    def test_sff_units_parse(self):
        for u in ("kg/hr", "kmol/hr", "kW", "kJ/hr", "USD/kg", "USD/kWh",
                  "USD/kmol", "USD/kJ", "m3/hr", "K", "Pa", "g/mol"):
            with self.subTest(unit=u):
                self.assertTrue(self.V._unit_is_parseable(u))

    def test_empty_string_is_parseable_dimensionless(self):
        self.assertTrue(self.V._unit_is_parseable(""))

    def test_gibberish_not_parseable(self):
        # 'furlongs per fortnight' are real pint units; use a truly-undefined token.
        self.assertFalse(self.V._unit_is_parseable("xyz!!"))


if __name__ == "__main__":
    unittest.main()
