# -*- coding: utf-8 -*-
# Pins the v0.0.8 change that makes metadata.TEA_currency a REQUIRED field.
#
# Import-light: validates against the committed schema file with jsonschema
# directly, never importing pisces_sff (which would drag in biosteam via
# _export). See tests/tier1/test_schema_microorganisms.py for the same rationale.
#
# Why pinned: TEA_currency was an optional string through 0.0.7 and became
# required in 0.0.8, so every conforming file must now declare the currency its
# cost results are reported in (the BioSTEAM exporter writes "USD"). Making a
# field required is a breaking, public-contract change a downstream PISCES
# consumer relies on; a silent revert to optional would let currency-less files
# validate again and desynchronise producers and readers.

import json
import unittest
from pathlib import Path

from jsonschema import Draft7Validator

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "pisces_sff" / "schema" / "sff_schema.json"
)


def load_schema():
    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


class TestTeaCurrencyShape(unittest.TestCase):
    """Structural assertions: TEA_currency is a required metadata string."""

    def setUp(self):
        self.schema = load_schema()
        self.metadata = self.schema["properties"]["metadata"]

    def test_schema_is_at_least_0_0_8(self):
        # The required-ness pinned below is a 0.0.8 change; a floor keeps this
        # from breaking on later additive bumps while still catching a revert
        # to an older schema that had not made the field required.
        version = tuple(int(p) for p in self.schema["version"].split("."))
        self.assertGreaterEqual(version, (0, 0, 8))

    def test_tea_currency_is_a_string_property(self):
        prop = self.metadata["properties"]["TEA_currency"]
        self.assertEqual(prop["type"], "string")

    def test_tea_currency_is_required(self):
        self.assertIn("TEA_currency", self.metadata["required"])


class TestTeaCurrencyValidation(unittest.TestCase):
    """A whole-document validator proves the requirement actually bites."""

    def setUp(self):
        self.validator = Draft7Validator(load_schema())

    def _minimal(self):
        # A minimal-but-valid v0.0.8 document; individual tests corrupt metadata.
        return {
            "metadata": {
                "sff_version": "0.0.8", "TEA_currency": "USD", "TEA_year": 2020,
                "process_simulator": {"name": "BioSTEAM", "version": "2.46.1"},
                "feedstocks": [{"stream_id": "corn"}],
                "products": [{"stream_id": "ethanol"}],
            },
            "units": [{"id": "U1", "unit_type": "Mixer",
                       "quantity_units_for_design_results": {"Area": "m^2"}}],
            "streams": [{"id": "s1", "source_unit_id": "U1", "sink_unit_id": "None",
                         "price": 0.1,
                         "stream_properties": {
                             "total_mass_flow": 1.0,
                             "total_molar_flow": 1.0, "temperature": 300.0,
                             "pressure": 101325.0,
                             "phases": {"l": {
                                 "total_molar_flow": 1.0,
                                 "composition": [
                                     {"component_name": "ethanol",
                                      "mol_fraction": 1.0}]}}}}],
            "utilities": {"heat_utilities": [], "power_utilities": [],
                          "other_utilities": []},
        }

    def test_minimal_document_with_currency_validates(self):
        self.assertEqual(list(self.validator.iter_errors(self._minimal())), [])

    def test_document_missing_tea_currency_is_rejected(self):
        doc = self._minimal()
        del doc["metadata"]["TEA_currency"]
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])


if __name__ == "__main__":
    unittest.main()
