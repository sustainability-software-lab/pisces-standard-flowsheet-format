# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Tier 4 -- chemicals checks (CHEM-01, CHEM-02, CHEM-03, CHEM-04, CHEM-05).
Runs the FULL validator on valid_doc() with exactly one thing broken and asserts
the target check's CheckResult carries the catalogue's declared severity,
status == "fail", and the correct effect on is_valid."""

import unittest

from tests._docs import valid_doc, mutate, remove
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests.tier4._run import validate_doc


class TestCHEM01(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """CHEM-01 -- valid_doc()'s single chemical 'Ethanol' (index 0) is
        trivially unique -> CheckResult(CHEM-01, error, pass); is_valid
        True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["CHEM-01"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """CHEM-01 -- a second chemical is declared with the same id
        'Ethanol' (different index 1) -> CheckResult(CHEM-01, error, fail);
        is_valid False."""
        doc = valid_doc()
        doc["chemicals"].append({
            "id": "Ethanol", "index": 1, "formula": "C2H6O",
            "molar_mass": 46.07, "included_in_thermo": False})
        is_valid, by_id = validate_doc(doc)
        r = by_id["CHEM-01"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestCHEM02(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """CHEM-02 -- valid_doc()'s Ethanol declares molar_mass 46.07 (> 0) ->
        CheckResult(CHEM-02, warning, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["CHEM-02"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """CHEM-02 -- Ethanol's molar_mass is changed to 0 (not > 0) ->
        CheckResult(CHEM-02, warning, fail); is_valid stays True (warnings
        never flip is_valid). As of v0.1.1 this is validator-enforced, not a
        schema constraint."""
        doc = valid_doc()
        mutate(doc, "chemicals/0/molar_mass", 0)
        is_valid, by_id = validate_doc(doc)
        r = by_id["CHEM-02"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


class TestCHEM03(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """CHEM-03 -- valid_doc()'s Ethanol declares formula 'C2H6O' and
        molar_mass 46.07, which agree -> CheckResult(CHEM-03, warning, pass);
        is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["CHEM-03"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """CHEM-03 -- Ethanol's declared molar_mass is changed to 999
        (formula 'C2H6O' computes to ~46.07 g/mol) -> CheckResult(CHEM-03,
        warning, fail); is_valid stays True (warnings never flip
        is_valid)."""
        doc = valid_doc()
        mutate(doc, "chemicals/0/molar_mass", 999.0)
        is_valid, by_id = validate_doc(doc)
        r = by_id["CHEM-03"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


class TestCHEM04(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """CHEM-04 -- valid_doc() declares no reaction using index-based
        stoichiometry, so index coverage is not applicable -> CheckResult
        (CHEM-04, error, skip); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["CHEM-04"][0].status, "skip")
        self.assertTrue(is_valid)

    def test_violator(self):
        """CHEM-04 -- a second chemical 'Water' is declared with no 'index'
        field, and U1 gets an index-based (array) stoichiometry reaction --
        triggering index-coverage enforcement, which then finds 'Water'
        lacking an index -> CheckResult(CHEM-04, error, fail); is_valid
        False."""
        doc = valid_doc()
        doc["chemicals"].append({
            "id": "Water", "included_in_thermo": False, "molar_mass": 18.02})
        doc["units"][0]["reactions"] = [{"stoichiometry": [-1, 1]}]
        is_valid, by_id = validate_doc(doc)
        r = by_id["CHEM-04"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestCHEM05(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """CHEM-05 -- valid_doc()'s only chemical 'Ethanol' is referenced by
        both streams' compositions -> CheckResult(CHEM-05, info, pass);
        is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["CHEM-05"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """CHEM-05 -- a second chemical 'Water' is declared but referenced by
        no stream composition, reaction, or utility composition ->
        CheckResult(CHEM-05, info, fail); is_valid stays True (info never
        flips is_valid)."""
        doc = valid_doc()
        doc["chemicals"].append({
            "id": "Water", "included_in_thermo": False, "molar_mass": 18.02})
        is_valid, by_id = validate_doc(doc)
        r = by_id["CHEM-05"][0]
        self.assertEqual((r.severity, r.status), ("info", "fail"))
        self.assertTrue(is_valid)


if __name__ == "__main__":
    unittest.main()
