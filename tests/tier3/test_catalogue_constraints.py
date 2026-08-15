# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: pins the v0.0.12 declarative constraints (sff_checks.md MET-01, MET-04,
# MET-05, MET-06, UNIT-04, UNIT-05, STR-11, STR-12, CHEM-02, UTIL-05). Import-light
# like tests/tier1/test_schema_microorganisms.py: validate synthetic fragments
# against the real committed schema via jsonschema, never importing pisces_sff.

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft7Validator

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


class TestSchemaVersion(unittest.TestCase):
    def test_schema_declares_0_0_12(self):
        self.assertEqual(load_schema()["version"], "0.0.12")


class TestMET01SemVer(unittest.TestCase):
    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_valid_semver_accepted(self):
        self.assertTrue(self.v.is_valid(minimal_doc()))

    def test_non_semver_rejected(self):
        doc = minimal_doc()
        doc["metadata"]["sff_version"] = "point three"
        self.assertFalse(self.v.is_valid(doc))


class TestMET05Currency(unittest.TestCase):
    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_empty_currency_rejected(self):
        doc = minimal_doc()
        doc["metadata"]["TEA_currency"] = ""
        self.assertFalse(self.v.is_valid(doc))


class TestMET06Sha256(unittest.TestCase):
    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def _doc_with_repro(self, digest):
        doc = minimal_doc()
        doc["metadata"]["reproducibility"] = {
            "environment": {"format": "conda-environment-yaml", "filename": "e.yaml",
                            "sha256": digest, "content": "x"},
            "load_script": {"format": "python", "filename": "load.py",
                            "sha256": digest, "content": "x"},
        }
        return doc

    def test_valid_digest_accepted(self):
        self.assertTrue(self.v.is_valid(self._doc_with_repro("a" * 64)))

    def test_short_digest_rejected(self):
        self.assertFalse(self.v.is_valid(self._doc_with_repro("abc")))

    def test_uppercase_digest_rejected(self):
        self.assertFalse(self.v.is_valid(self._doc_with_repro("A" * 64)))


class TestUNIT04Conversion(unittest.TestCase):
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


class TestSTR11Pressure(unittest.TestCase):
    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_zero_pressure_rejected(self):
        doc = minimal_doc()
        doc["streams"][0]["stream_properties"]["pressure"] = 0
        self.assertFalse(self.v.is_valid(doc))


class TestSTR12TotalMassFlowRequired(unittest.TestCase):
    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_missing_total_mass_flow_rejected(self):
        doc = minimal_doc()
        del doc["streams"][0]["stream_properties"]["total_mass_flow"]
        self.assertFalse(self.v.is_valid(doc))


class TestCHEM02MolarMass(unittest.TestCase):
    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_zero_molar_mass_rejected(self):
        doc = minimal_doc()
        doc["chemicals"] = [
            {"id": "A", "included_in_thermo": False, "molar_mass": 0}]
        self.assertFalse(self.v.is_valid(doc))


class TestUTIL05TempPressure(unittest.TestCase):
    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_zero_utility_temperature_rejected(self):
        doc = minimal_doc()
        doc["utilities"]["heat_utilities"] = [{
            "id": "lps", "temperature": 0, "pressure": 101325.0,
            "composition": [], "quantity_units_for_utility_results": "kJ/hr"}]
        self.assertFalse(self.v.is_valid(doc))


if __name__ == "__main__":
    unittest.main()
