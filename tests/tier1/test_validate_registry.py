# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: the check registry is complete. Runs every registered check on an EMPTY
# _Context -- which parses no unit strings, compositions, or formulas, so no
# chemicals/thermosteam import happens -- and asserts the full set of validator
# IDs is present exactly once.

import unittest

from tests._validate_loader import V

EXPECTED_IDS = {
    "MET-02", "MET-03", "MET-04",
    "UNIT-01", "UNIT-02", "UNIT-03", "UNIT-04", "UNIT-05", "UNIT-06", "UNIT-07",
    "UNIT-08", "UNIT-09",
    "STR-01", "STR-02", "STR-03", "STR-04", "STR-05", "STR-06", "STR-07",
    "STR-08", "STR-09", "STR-10", "STR-13",
    "CHEM-01", "CHEM-02", "CHEM-03", "CHEM-04", "CHEM-05",
    "UTIL-01", "UTIL-02", "UTIL-03", "UTIL-04",
    "QU-01", "QU-02", "QU-03", "QU-04",
    "GRAPH-01",
}


class TestRegistry(unittest.TestCase):
    def test_all_registered_and_callable(self):
        """_CHECKS is nonempty and every entry is a callable."""
        self.assertTrue(V._CHECKS)
        for check in V._CHECKS:
            self.assertTrue(callable(check))

    def test_registry_emits_every_expected_id_once(self):
        """Running every registered check on an empty _Context emits each ID in EXPECTED_IDS exactly once."""
        ctx = V._Context({})
        ids = []
        for check in V._CHECKS:
            for r in check(ctx):
                self.assertIsInstance(r, V.CheckResult)
                ids.append(r.check_id)
        self.assertEqual(sorted(ids), sorted(EXPECTED_IDS),
                         "registry must emit each validator check exactly once")


if __name__ == "__main__":
    unittest.main()
