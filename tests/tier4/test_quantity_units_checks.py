# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Tier 4 -- quantity_units checks (QU-01, QU-02, QU-03, QU-04). Runs the FULL
validator on valid_doc() with exactly one thing broken and asserts the target
check's CheckResult carries the catalogue's declared severity,
status == "fail", and the correct effect on is_valid.

valid_doc()'s quantity_units_global declares 5 entries -- temperature,
pressure, mass_flow, molar_flow, molar_mass -- each with a single alias that
matches a field actually present in the document (see tests/_docs.py)."""

import unittest

from tests._docs import valid_doc, mutate, remove
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests.tier4._run import validate_doc


class TestQU01(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """QU-01 -- valid_doc()'s present global quantity fields (mass_flow,
        molar_flow, temperature, pressure, molar_mass) all resolve to a
        quantity_units_global alias -> CheckResult(QU-01, error, pass);
        is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["QU-01"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """QU-01 -- the quantity_units_global 'mass_flow' entry (whose only
        alias is 'total_mass_flow') is removed, leaving the present
        total_mass_flow field with no resolving alias -> CheckResult(QU-01,
        error, fail); is_valid False."""
        doc = valid_doc()
        remove(doc, "quantity_units_global/mass_flow")
        is_valid, by_id = validate_doc(doc)
        r = by_id["QU-01"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestQU02(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """QU-02 -- valid_doc()'s 5 quantity_units_global unit strings (K,
        Pa, kg/hr, kmol/hr, g/mol) are all parseable -> CheckResult(QU-02,
        error, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["QU-02"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """QU-02 -- quantity_units_global.temperature.quantity_units is set
        to 'nonsense~', which the SFF unit parser rejects -> CheckResult
        (QU-02, error, fail); is_valid False."""
        doc = valid_doc()
        mutate(doc, "quantity_units_global/temperature/quantity_units",
               "nonsense~")
        is_valid, by_id = validate_doc(doc)
        r = by_id["QU-02"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestQU03(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """QU-03 -- valid_doc()'s 5 quantity_units_global entries each
        declare a distinct alias -> CheckResult(QU-03, error, pass); is_valid
        True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["QU-03"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """QU-03 -- the 'pressure' entry's aliases are set to
        ["pressure", "temperature"], so alias 'temperature' now maps to both
        the 'temperature' and 'pressure' entries (ambiguous) -> CheckResult
        (QU-03, error, fail); is_valid False."""
        doc = valid_doc()
        mutate(doc, "quantity_units_global/pressure/aliases",
               ["pressure", "temperature"])
        is_valid, by_id = validate_doc(doc)
        r = by_id["QU-03"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestQU04(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """QU-04 -- valid_doc()'s 5 quantity_units_global entries each have
        an alias matching a present field -> CheckResult(QU-04, info, pass);
        is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["QU-04"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """QU-04 -- a new quantity_units_global entry 'unused_qty' is added
        whose only alias, 'totally_unused_field', matches no field present
        anywhere in the document -> CheckResult(QU-04, info, fail); is_valid
        stays True (info never flips is_valid)."""
        doc = valid_doc()
        mutate(doc, "quantity_units_global/unused_qty",
               {"aliases": ["totally_unused_field"], "quantity_units": "kg"})
        is_valid, by_id = validate_doc(doc)
        r = by_id["QU-04"][0]
        self.assertEqual((r.severity, r.status), ("info", "fail"))
        self.assertTrue(is_valid)


if __name__ == "__main__":
    unittest.main()
