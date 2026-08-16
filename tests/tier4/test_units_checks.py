# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Tier 4 -- units checks (UNIT-01..07). Runs the FULL validator on valid_doc()
with exactly one thing broken and asserts the target check's CheckResult carries
the catalogue's declared severity, status == "fail", and the correct effect on
is_valid."""

import unittest

from tests._docs import valid_doc, mutate, remove
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests.tier4._run import validate_doc


class TestUNIT01(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """UNIT-01 -- valid_doc() has a single unit id -> CheckResult(UNIT-01,
        error, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["UNIT-01"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """UNIT-01 -- a second unit with id 'U1' duplicates the first ->
        CheckResult(UNIT-01, error, fail); is_valid False."""
        doc = valid_doc()
        doc["units"].append({"id": "U1", "unit_type": "Mixer"})
        is_valid, by_id = validate_doc(doc)
        r = by_id["UNIT-01"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestUNIT02(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """UNIT-02 -- valid_doc()'s unit declares no utility_consumption/
        production_results -> CheckResult(UNIT-02, error, skip); is_valid
        True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["UNIT-02"][0].status, "skip")
        self.assertTrue(is_valid)

    def test_violator(self):
        """UNIT-02 -- U1.utility_consumption_results references utility id
        'ghost', which no declared utility carries -> CheckResult(UNIT-02,
        error, fail); is_valid False."""
        doc = valid_doc()
        doc["units"][0]["utility_consumption_results"] = {"ghost": 1.0}
        is_valid, by_id = validate_doc(doc)
        r = by_id["UNIT-02"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestUNIT03(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """UNIT-03 -- valid_doc()'s unit declares no design_results ->
        CheckResult(UNIT-03, error, skip); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["UNIT-03"][0].status, "skip")
        self.assertTrue(is_valid)

    def test_violator(self):
        """UNIT-03 -- U1.design_results declares key 'volume' with no matching
        entry in quantity_units_for_design_results -> CheckResult(UNIT-03,
        error, fail) (the missing-units branch, not the orphan-key branch);
        is_valid False."""
        doc = valid_doc()
        doc["units"][0]["design_results"] = {"volume": 10.0}
        is_valid, by_id = validate_doc(doc)
        r = by_id["UNIT-03"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestUNIT04(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """UNIT-04 -- valid_doc() has no reaction, hence no reactant reference
        to check -> CheckResult(UNIT-04, error, skip); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["UNIT-04"][0].status, "skip")
        self.assertTrue(is_valid)

    def test_violator(self):
        """UNIT-04 -- U1 gets a reaction with reactant 'ZZ', which no declared
        chemical id matches -> CheckResult(UNIT-04, error, fail); is_valid
        False."""
        doc = valid_doc()
        doc["units"][0]["reactions"] = [
            {"reactant": "ZZ", "conversion": 0.5, "equation": "A -> B"}]
        is_valid, by_id = validate_doc(doc)
        r = by_id["UNIT-04"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestUNIT05(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """UNIT-05 -- valid_doc() has no reaction providing both an equation
        and stoichiometry -> CheckResult(UNIT-05, error, skip); is_valid
        True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["UNIT-05"][0].status, "skip")
        self.assertTrue(is_valid)

    def test_violator(self):
        """UNIT-05 -- U1 gets a reaction whose equation 'Ethanol -> 2 Ethanol'
        parses to net coefficient +1 for Ethanol, while its stoichiometry
        [-1] (array, indexed against the single declared chemical) is -1 for
        Ethanol -- opposite sign, so the two disagree -> CheckResult(UNIT-05,
        error, fail); is_valid False."""
        doc = valid_doc()
        doc["units"][0]["reactions"] = [
            {"equation": "Ethanol -> 2 Ethanol", "stoichiometry": [-1]}]
        is_valid, by_id = validate_doc(doc)
        r = by_id["UNIT-05"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestUNIT06(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """UNIT-06 -- valid_doc() has no reaction declaring stoichiometry ->
        CheckResult(UNIT-06, error, skip); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["UNIT-06"][0].status, "skip")
        self.assertTrue(is_valid)

    def test_violator(self):
        """UNIT-06 -- U1 gets a reaction whose stoichiometry is an object
        keyed '5', an index no chemical declares (valid_doc has only index 0)
        -> _stoich_to_coeffs cannot resolve it -> CheckResult(UNIT-06, error,
        fail); is_valid False."""
        doc = valid_doc()
        doc["units"][0]["reactions"] = [{"stoichiometry": {"5": -1.0}}]
        is_valid, by_id = validate_doc(doc)
        r = by_id["UNIT-06"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestUNIT07(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """UNIT-07 -- valid_doc()'s only unit U1 is attached to both streams
        -> CheckResult(UNIT-07, warning, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["UNIT-07"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """UNIT-07 -- a second unit U2 is declared but no stream references
        it (orphan) -> CheckResult(UNIT-07, warning, fail); is_valid stays
        True (warnings never flip is_valid)."""
        doc = valid_doc()
        doc["units"].append({"id": "U2", "unit_type": "Mixer"})
        is_valid, by_id = validate_doc(doc)
        r = by_id["UNIT-07"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


if __name__ == "__main__":
    unittest.main()
