# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 3: unit-schema (reaction) tests, regrouped by category from the former
# Tier 1 test_schema_constraints_v0_0_12.py. Covers sff_checks.md UNIT-04
# (conversion bounded to [0, 1]) and UNIT-05 (a reaction needs an equation or
# stoichiometry). Import-light: validates synthetic fragments against the real
# committed schema via jsonschema, never importing pisces_sff.
#
# Every reject/accept assertion is preserved verbatim from
# test_schema_constraints_v0_0_12.py; only class placement and the tier-3 skip
# gate changed.

import json
import unittest
from pathlib import Path

from jsonschema import Draft7Validator

from tests._gating import skip_if_disabled

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "schema" / "sff_schema.json"
)


def load_schema():
    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def minimal_doc():
    """A minimal flowsheet that validates against the whole schema."""
    return {
        "metadata": {
            "sff_version": "0.0.12",
            "TEA_currency": "USD",
            "TEA_year": 2020,
            "process_simulator": {"name": "BioSTEAM", "version": "2.46.1"},
            "feedstocks": [{"stream_id": "s1"}],
            "products": [{"stream_id": "s1"}],
        },
        "units": [{"id": "U1", "unit_type": "Mixer"}],
        "streams": [{
            "id": "s1", "source_unit_id": "None", "sink_unit_id": "U1",
            "stream_properties": {
                "total_mass_flow": 1.0, "total_molar_flow": 1.0,
                "temperature": 300.0, "pressure": 101325.0,
                "phases": {"l": {"total_molar_flow": 1.0, "composition": []}},
            },
        }],
        "utilities": {"heat_utilities": [], "power_utilities": [], "other_utilities": []},
    }


class TestUNIT04Conversion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def _doc_with_reaction(self, reaction):
        doc = minimal_doc()
        doc["units"][0]["reactions"] = [reaction]
        return doc

    def test_conversion_in_range_accepted(self):
        """UNIT-04 — reaction conversion 0.5 (within [0, 1]) → schema accepts the document."""
        doc = self._doc_with_reaction(
            {"reactant": "A", "conversion": 0.5, "equation": "A -> B"})
        self.assertTrue(self.v.is_valid(doc))

    def test_conversion_above_one_rejected(self):
        """UNIT-04 — reaction conversion 1.5 (>1) → schema rejects the document."""
        doc = self._doc_with_reaction(
            {"reactant": "A", "conversion": 1.5, "equation": "A -> B"})
        self.assertFalse(self.v.is_valid(doc))

    def test_negative_conversion_rejected(self):
        """UNIT-04 — reaction conversion -0.1 (<0) → schema rejects the document."""
        doc = self._doc_with_reaction(
            {"reactant": "A", "conversion": -0.1, "equation": "A -> B"})
        self.assertFalse(self.v.is_valid(doc))


class TestUNIT05EquationOrStoichiometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_equation_only_accepted(self):
        """UNIT-05 — reaction supplying only `equation` → schema accepts the document."""
        doc = minimal_doc()
        doc["units"][0]["reactions"] = [{"reactant": "A", "equation": "A -> B"}]
        self.assertTrue(self.v.is_valid(doc))

    def test_stoichiometry_only_accepted(self):
        """UNIT-05 — reaction supplying only `stoichiometry` → schema accepts the document."""
        doc = minimal_doc()
        doc["units"][0]["reactions"] = [{"reactant": "A", "stoichiometry": [-1, 1]}]
        self.assertTrue(self.v.is_valid(doc))

    def test_neither_rejected(self):
        """UNIT-05 — reaction with neither `equation` nor `stoichiometry` → schema rejects the document."""
        doc = minimal_doc()
        doc["units"][0]["reactions"] = [{"reactant": "A", "conversion": 0.5}]
        self.assertFalse(self.v.is_valid(doc))


class TestUNIT09PurchaseCostCorrelations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def _doc(self, correlation):
        doc = minimal_doc()
        doc["units"][0]["purchase_cost_correlations"] = {"Reactor": correlation}
        return doc

    _POWER_LAW = {
        "correlation_type": "power_law", "basis": "Duty", "basis_units": "kJ/hr",
        "reference_size": 1.0, "reference_cost": 45000.0, "exponent": 0.6,
        "reference_CE_index": 567.0, "installation_factor": 2.3, "power_rate": 0.0,
    }
    _CUSTOM = {
        "correlation_type": "custom_function", "basis": "Duty",
        "basis_units": "kJ/hr", "reference_size": 1.0,
        "reference_CE_index": 567.0, "installation_factor": 1.0, "power_rate": 0.0,
    }

    def test_power_law_accepted(self):
        """UNIT-09 — a complete power_law correlation → schema accepts."""
        self.assertTrue(self.v.is_valid(self._doc(dict(self._POWER_LAW))))

    def test_custom_function_accepted(self):
        """UNIT-09 — a custom_function correlation without cost/exponent → accepted."""
        self.assertTrue(self.v.is_valid(self._doc(dict(self._CUSTOM))))

    def test_bad_correlation_type_rejected(self):
        """UNIT-09 — correlation_type outside the enum → rejected."""
        c = dict(self._POWER_LAW, correlation_type="six_tenths")
        self.assertFalse(self.v.is_valid(self._doc(c)))

    def test_power_law_missing_reference_cost_rejected(self):
        """UNIT-09 — power_law without reference_cost → rejected by if/then."""
        c = dict(self._POWER_LAW)
        del c["reference_cost"]
        self.assertFalse(self.v.is_valid(self._doc(c)))

    def test_power_law_missing_exponent_rejected(self):
        """UNIT-09 — power_law without exponent → rejected by if/then."""
        c = dict(self._POWER_LAW)
        del c["exponent"]
        self.assertFalse(self.v.is_valid(self._doc(c)))

    def test_nonpositive_reference_size_rejected(self):
        """UNIT-09 — reference_size 0 (not > 0) → rejected."""
        self.assertFalse(self.v.is_valid(
            self._doc(dict(self._POWER_LAW, reference_size=0))))

    def test_nonpositive_reference_CE_index_rejected(self):
        """UNIT-09 — reference_CE_index 0 (not > 0) → rejected."""
        self.assertFalse(self.v.is_valid(
            self._doc(dict(self._POWER_LAW, reference_CE_index=0))))

    def test_unknown_field_rejected(self):
        """UNIT-09 — an undeclared field → rejected (additionalProperties false)."""
        self.assertFalse(self.v.is_valid(
            self._doc(dict(self._POWER_LAW, bogus=1))))


if __name__ == "__main__":
    unittest.main()
