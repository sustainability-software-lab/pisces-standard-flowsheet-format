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
#
# v0.2.3 renames the chemical VLE flag included_in_thermo ->
# included_in_thermo_equilibria and makes it optional in files whose
# metadata.tags carries an extracted-from-* tag (TestThermoEquilibriaFlag).

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
            {"id": "A", "included_in_thermo_equilibria": False, "molar_mass": 0}]
        self.assertTrue(self.v.is_valid(doc))

    def test_positive_molar_mass_accepted(self):
        """CHEM-02 — a chemical with molar_mass 46.07 (>0) → schema accepts the document."""
        doc = minimal_doc()
        doc["chemicals"] = [
            {"id": "A", "included_in_thermo_equilibria": False, "molar_mass": 46.07}]
        self.assertTrue(self.v.is_valid(doc))


class TestThermoEquilibriaFlag(unittest.TestCase):
    """v0.2.3: the chemical VLE flag is named included_in_thermo_equilibria
    (formerly included_in_thermo). It is required unless metadata.tags carries
    an extracted-from-* tag -- literature rarely says which chemicals take part
    in phase equilibrium -- and the registry_id / molar_mass if/then rules
    still key on it whenever it is present."""

    EXTRACTED = ("extracted-from-prose", "extracted-from-image",
                 "extracted-from-table")
    # Fields the metadata if/then rules require beside each extracted tag.
    TAG_FIELDS = {"prose_type": "journal_article",
                  "image_type": "process_flow_diagram",
                  "extraction_method": "manual"}

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def _doc(self, chemical, tags=None):
        doc = minimal_doc()
        doc["chemicals"] = [chemical]
        if tags is not None:
            doc["metadata"]["tags"] = list(tags)
            doc["metadata"].update(self.TAG_FIELDS)
        return doc

    def test_schema_declares_the_new_name_only(self):
        """The chemicals item declares included_in_thermo_equilibria and no
        longer declares the legacy included_in_thermo."""
        props = load_schema()["properties"]["chemicals"]["items"]["properties"]
        self.assertIn("included_in_thermo_equilibria", props)
        self.assertNotIn("included_in_thermo", props)

    def test_missing_flag_rejected_without_tags(self):
        """No tags -> a chemical without the flag is rejected."""
        self.assertFalse(self.v.is_valid(self._doc({"id": "A", "molar_mass": 1.0})))

    def test_legacy_name_alone_rejected_without_tags(self):
        """The legacy key does not stand in for the new one."""
        doc = self._doc({"id": "A", "included_in_thermo": False, "molar_mass": 1.0})
        self.assertFalse(self.v.is_valid(doc))

    def test_missing_flag_rejected_for_non_extracted_tags(self):
        """exported-from-simulator alone does not relax the requirement."""
        doc = self._doc({"id": "A", "molar_mass": 1.0}, ["exported-from-simulator"])
        self.assertFalse(self.v.is_valid(doc))

    def test_missing_flag_accepted_for_each_extracted_tag(self):
        """Any single extracted-from-* tag relaxes it; with no flag, neither
        registry_id nor molar_mass is required."""
        for tag in self.EXTRACTED:
            with self.subTest(tag=tag):
                doc = self._doc({"id": "A"}, [tag])
                self.assertTrue(self.v.is_valid(doc), list(self.v.iter_errors(doc)))

    def test_mixed_tags_with_an_extracted_tag_accepted(self):
        """An extracted tag beside another tag still relaxes it."""
        doc = self._doc({"id": "A"}, ["exported-from-simulator", "extracted-from-table"])
        self.assertTrue(self.v.is_valid(doc), list(self.v.iter_errors(doc)))

    def test_flag_still_typed_when_present(self):
        """Optional is not untyped: a non-boolean value is still rejected."""
        doc = self._doc({"id": "A", "included_in_thermo_equilibria": "yes"},
                        ["extracted-from-prose"])
        self.assertFalse(self.v.is_valid(doc))

    def test_conditional_requirements_still_apply_when_present(self):
        """A present flag still triggers its if/then, extracted or not: true
        needs registry_id, false needs molar_mass."""
        for tags in (None, ["extracted-from-prose"]):
            with self.subTest(tags=tags):
                self.assertFalse(self.v.is_valid(self._doc(
                    {"id": "A", "included_in_thermo_equilibria": True}, tags)))
                self.assertFalse(self.v.is_valid(self._doc(
                    {"id": "A", "included_in_thermo_equilibria": False}, tags)))
                self.assertTrue(self.v.is_valid(self._doc(
                    {"id": "A", "included_in_thermo_equilibria": True,
                     "registry_id": "64-17-5"}, tags)))


if __name__ == "__main__":
    unittest.main()
