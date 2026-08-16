# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Tier 4 -- utilities checks (UTIL-01, UTIL-02, UTIL-03, UTIL-04). Runs the
FULL validator on valid_doc() with exactly one thing broken and asserts the
target check's CheckResult carries the catalogue's declared severity,
status == "fail", and the correct effect on is_valid.

valid_doc() declares an empty utilities registry (heat/power/other_utilities
all []), so every conformer here exercises the "vacuous pass" branch documented
in pisces_sff/_validate.py's checks -- never a skip (see each check's
"Skipped when: never" comment)."""

import unittest

from tests._docs import valid_doc, mutate, remove
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests.tier4._run import validate_doc


class TestUTIL01(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """UTIL-01 -- valid_doc()'s empty utilities registry has no
        duplicates (vacuous pass) -> CheckResult(UTIL-01, error, pass);
        is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["UTIL-01"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """UTIL-01 -- a heat utility and a power utility both use id 'u1',
        colliding across utility groups -> CheckResult(UTIL-01, error, fail);
        is_valid False."""
        doc = valid_doc()
        doc["utilities"]["heat_utilities"] = [{
            "id": "u1", "temperature": 400.0, "pressure": 101325.0,
            "composition": [], "quantity_units_for_utility_results": "kJ/hr"}]
        doc["utilities"]["power_utilities"] = [{
            "id": "u1", "quantity_units_for_utility_results": "kW"}]
        is_valid, by_id = validate_doc(doc)
        r = by_id["UTIL-01"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestUTIL02(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """UTIL-02 -- valid_doc()'s empty utilities registry has zero
        declared utilities, so all are trivially "referenced" (vacuous pass)
        -> CheckResult(UTIL-02, info, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["UTIL-02"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """UTIL-02 -- a power utility 'elec' is declared, but no unit's
        utility_consumption_results/utility_production_results references it
        -> CheckResult(UTIL-02, info, fail); is_valid stays True (info never
        flips is_valid)."""
        doc = valid_doc()
        doc["utilities"]["power_utilities"] = [{
            "id": "elec", "quantity_units_for_utility_results": "kW"}]
        is_valid, by_id = validate_doc(doc)
        r = by_id["UTIL-02"][0]
        self.assertEqual((r.severity, r.status), ("info", "fail"))
        self.assertTrue(is_valid)


class TestUTIL03(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """UTIL-03 -- valid_doc()'s empty utilities registry has no
        quantity_units_for_utility_results strings to check (vacuous pass)
        -> CheckResult(UTIL-03, warning, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["UTIL-03"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """UTIL-03 -- a power utility 'elec' declares
        quantity_units_for_utility_results = 'nonsense~', which the SFF unit
        parser rejects -> CheckResult(UTIL-03, warning, fail), matching the
        catalogue severity for this check on its own.

        NOTE on is_valid: UTIL-03's only failure condition
        (`not isinstance(s, str) or s == '' or not _unit_is_parseable(s)`) is
        textually identical to the branch QU-02 runs over the very same
        `quantity_units_for_utility_results` string (see
        _iter_quantity_unit_strings/_check_quantity_unit_strings_parseable in
        pisces_sff/_validate.py -- utility-result strings there always have
        empty_ok=False). So any string that fails UTIL-03 necessarily also
        fails QU-02, which is error-severity, and is_valid is False whenever
        any error-severity check fails. There is no alternative break that
        trips UTIL-03 in isolation: the field is schema-required to be a
        non-empty string, so an absent/wrong-typed value fails the schema
        gate instead (also error-severity), and any string value bad enough
        for UTIL-03 is by construction bad enough for QU-02 too. is_valid is
        therefore False here, not True as a naive per-check severity table
        would suggest -- this is a genuine coupling between UTIL-03 and
        QU-02, not a miscrafted break (see task-5.2-report.md)."""
        doc = valid_doc()
        doc["utilities"]["power_utilities"] = [{
            "id": "elec", "quantity_units_for_utility_results": "nonsense~"}]
        is_valid, by_id = validate_doc(doc)
        r = by_id["UTIL-03"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertFalse(is_valid)


class TestUTIL04(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """UTIL-04 -- valid_doc()'s empty utilities registry has no utility
        declaring a composition -> CheckResult(UTIL-04, error, skip);
        is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["UTIL-04"][0].status, "skip")
        self.assertTrue(is_valid)

    def test_violator(self):
        """UTIL-04 -- a heat utility 'lps' declares a composition entry with
        component_name 'ghost', which no declared chemical id matches (the
        referential branch, error severity) -> CheckResult(UTIL-04, error,
        fail); is_valid False."""
        doc = valid_doc()
        doc["utilities"]["heat_utilities"] = [{
            "id": "lps", "temperature": 400.0, "pressure": 101325.0,
            "composition": [{"component_name": "ghost", "mol_fraction": 1.0}],
            "quantity_units_for_utility_results": "kJ/hr"}]
        is_valid, by_id = validate_doc(doc)
        r = by_id["UTIL-04"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


if __name__ == "__main__":
    unittest.main()
