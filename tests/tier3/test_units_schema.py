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
        doc = self._doc_with_reaction(
            {"reactant": "A", "conversion": 0.5, "equation": "A -> B"})
        self.assertTrue(self.v.is_valid(doc))

    def test_conversion_above_one_rejected(self):
        doc = self._doc_with_reaction(
            {"reactant": "A", "conversion": 1.5, "equation": "A -> B"})
        self.assertFalse(self.v.is_valid(doc))

    def test_negative_conversion_rejected(self):
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
        doc = minimal_doc()
        doc["units"][0]["reactions"] = [{"reactant": "A", "equation": "A -> B"}]
        self.assertTrue(self.v.is_valid(doc))

    def test_stoichiometry_only_accepted(self):
        doc = minimal_doc()
        doc["units"][0]["reactions"] = [{"reactant": "A", "stoichiometry": [-1, 1]}]
        self.assertTrue(self.v.is_valid(doc))

    def test_neither_rejected(self):
        doc = minimal_doc()
        doc["units"][0]["reactions"] = [{"reactant": "A", "conversion": 0.5}]
        self.assertFalse(self.v.is_valid(doc))


if __name__ == "__main__":
    unittest.main()
