# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 2: CHEM-03 formula<->molar_mass agreement. Uses _molar_mass_from_formula
# (chemicals library), so gated on SFF_TEST_BIOSTEAM=1.

import importlib.util
import os
import unittest
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_validate.py")
RUN = os.environ.get("SFF_TEST_BIOSTEAM") == "1"


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_chem03_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(RUN, "set SFF_TEST_BIOSTEAM=1 (imports chemicals)")
class TestFormulaMolarMass(unittest.TestCase):
    def setUp(self):
        self.V = load_validate_module()

    def test_agreeing_passes(self):
        c = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": False,
             "formula": "H2O", "molar_mass": 18.01528}]})
        self.assertEqual(
            self.V._check_formula_molar_mass_agreement(c)[0].status, "pass")

    def test_disagreeing_is_warning(self):
        c = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": False,
             "formula": "H2O", "molar_mass": 99.0}]})
        r = self.V._check_formula_molar_mass_agreement(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_no_pairs_skips(self):
        c = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": False, "molar_mass": 18.0}]})
        self.assertEqual(
            self.V._check_formula_molar_mass_agreement(c)[0].status, "skip")


if __name__ == "__main__":
    unittest.main()
