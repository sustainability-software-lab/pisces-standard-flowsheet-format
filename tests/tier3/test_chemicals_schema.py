# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 3: chemical-schema tests, regrouped by category from the former Tier 1
# test_schema_constraints_v0_0_12.py. Covers sff_checks.md CHEM-02 (positive
# molar_mass). Import-light: validates synthetic fragments against the real
# committed schema via jsonschema, never importing pisces_sff.
#
# As of v0.1.1 CHEM-02 is NO LONGER a schema constraint (the `exclusiveMinimum: 0`
# on molar_mass was removed); it is now a validator warning (see the tier-4 test
# test_chemicals_checks.py). These tests therefore guard that the schema gate
# accepts BOTH a zero and a positive molar_mass — the schema no longer rejects a
# non-positive value.

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


class TestCHEM02MolarMass(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_zero_molar_mass_accepted_by_schema(self):
        """CHEM-02 — molar_mass 0 is accepted by the schema as of v0.1.1 (the
        constraint moved to the validator as a warning; the schema no longer
        rejects a non-positive molar_mass)."""
        doc = minimal_doc()
        doc["chemicals"] = [
            {"id": "A", "included_in_thermo": False, "molar_mass": 0}]
        self.assertTrue(self.v.is_valid(doc))

    def test_positive_molar_mass_accepted(self):
        """CHEM-02 — a chemical with molar_mass 46.07 (>0) → schema accepts the document."""
        doc = minimal_doc()
        doc["chemicals"] = [
            {"id": "A", "included_in_thermo": False, "molar_mass": 46.07}]
        self.assertTrue(self.v.is_valid(doc))


if __name__ == "__main__":
    unittest.main()
