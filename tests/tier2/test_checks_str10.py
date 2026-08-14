# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 2: STR-10 mass<->molar consistency. Uses _Context.molar_mass, which can
# import the `chemicals` library, so it is gated on SFF_TEST_BIOSTEAM=1.

import importlib.util
import os
import unittest
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_validate.py")
RUN = os.environ.get("SFF_TEST_BIOSTEAM") == "1"


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_str10_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(RUN, "set SFF_TEST_BIOSTEAM=1 (may import chemicals)")
class TestMassMolarConsistency(unittest.TestCase):
    def setUp(self):
        self.V = load_validate_module()

    def _ctx(self, mass, molar):
        # Single-component water phase: M-bar = 18.01528 g/mol.
        return self.V._Context({
            "chemicals": [{"id": "W", "included_in_thermo": False,
                           "molar_mass": 18.01528}],
            "streams": [{"id": "s", "stream_properties": {
                "total_mass_flow": mass, "total_molar_flow": molar,
                "phases": {"l": {"total_mass_flow": mass, "total_molar_flow": molar,
                                 "composition": [{"component_name": "W",
                                                  "mol_fraction": 1.0}]}}}}]})

    def test_consistent_passes(self):
        c = self._ctx(mass=18.01528, molar=1.0)
        self.assertEqual(
            self.V._check_mass_molar_flow_consistency(c)[0].status, "pass")

    def test_inconsistent_is_warning(self):
        c = self._ctx(mass=100.0, molar=1.0)
        r = self.V._check_mass_molar_flow_consistency(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_unresolvable_molar_mass_skips(self):
        c = self.V._Context({
            "chemicals": [{"id": "X", "included_in_thermo": True}],
            "streams": [{"id": "s", "stream_properties": {
                "total_mass_flow": 5.0, "total_molar_flow": 1.0,
                "phases": {"l": {"total_mass_flow": 5.0, "total_molar_flow": 1.0,
                                 "composition": [{"component_name": "X",
                                                  "mol_fraction": 1.0}]}}}}]})
        self.assertEqual(
            self.V._check_mass_molar_flow_consistency(c)[0].status, "skip")


if __name__ == "__main__":
    unittest.main()
