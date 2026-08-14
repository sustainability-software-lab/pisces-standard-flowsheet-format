# -*- coding: utf-8 -*-
# Pins the additive, optional v0.0.11 stream `enthalpy_flow` property (and its
# matching quantity_units_global entry) in the committed schema.
#
# Import-light (jsonschema on the committed file, never importing pisces_sff).
# Why pinned: v0.0.11 adds an optional `enthalpy_flow` number to every stream's
# stream_properties (the whole-stream enthalpy flow rate from biosteam
# `stream.H`, in kJ/hr), plus an `enthalpy_flow` entry in quantity_units_global.
# It is additive and NOT in the stream_properties `required` list, so files that
# predate it must keep validating; a downstream PISCES consumer parses
# `enthalpy_flow` when present. This test guards both the shape and the fact that
# it stays optional -- a silent tightening (adding it to `required`, or dropping
# the registry entry) would break producers and readers that already wrote
# 0.0.10-shaped files.

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


class TestSchemaVersion(unittest.TestCase):
    def test_schema_is_at_least_0_0_11(self):
        version = tuple(int(p) for p in load_schema()["version"].split("."))
        self.assertGreaterEqual(version, (0, 0, 11))


class TestEnthalpyFlowShape(unittest.TestCase):
    def setUp(self):
        schema = load_schema()
        self.stream_properties = (
            schema["properties"]["streams"]["items"]
            ["properties"]["stream_properties"]
        )
        self.registry = schema["properties"]["quantity_units_global"]

    def test_enthalpy_flow_is_a_number(self):
        prop = self.stream_properties["properties"]["enthalpy_flow"]
        self.assertEqual(prop["type"], "number")

    def test_enthalpy_flow_is_not_required(self):
        # Optional-and-additive is the whole point: 0.0.10-shaped streams that
        # omit enthalpy_flow must still validate against 0.0.11.
        self.assertNotIn(
            "enthalpy_flow", self.stream_properties.get("required", []))

    def test_registry_declares_enthalpy_flow(self):
        self.assertIn(
            "enthalpy_flow", self.registry["properties"])


class TestEnthalpyFlowValidation(unittest.TestCase):
    """A whole-document validator proves the property actually bites."""

    def setUp(self):
        self.validator = Draft7Validator(load_schema())

    def _minimal(self):
        # A minimal-but-valid v0.0.11 document; individual tests vary
        # enthalpy_flow.
        return {
            "metadata": {
                "sff_version": "0.0.11", "TEA_currency": "USD", "TEA_year": 2020,
                "process_simulator": {"name": "BioSTEAM", "version": "2.46.1"},
                "feedstocks": [{"stream_id": "corn"}],
                "products": [{"stream_id": "ethanol"}],
            },
            "units": [{"id": "U1", "unit_type": "Mixer",
                       "quantity_units_for_design_results": {"Area": "m^2"}}],
            "streams": [{"id": "s1", "source_unit_id": "U1",
                         "sink_unit_id": "None", "price": 0.1,
                         "roles": ["output", "product"],
                         "stream_properties": {
                             "total_molar_flow": 1.0, "temperature": 300.0,
                             "pressure": 101325.0, "enthalpy_flow": -12345.6,
                             "phases": {"l": {
                                 "total_molar_flow": 1.0,
                                 "composition": [
                                     {"component_name": "ethanol",
                                      "mol_fraction": 1.0}]}}}}],
            "utilities": {"heat_utilities": [], "power_utilities": [],
                          "other_utilities": []},
        }

    def test_minimal_v0_0_11_document_with_enthalpy_flow_validates(self):
        self.assertEqual(list(self.validator.iter_errors(self._minimal())), [])

    def test_stream_without_enthalpy_flow_still_validates(self):
        doc = self._minimal()
        del doc["streams"][0]["stream_properties"]["enthalpy_flow"]
        self.assertEqual(list(self.validator.iter_errors(doc)), [])

    def test_non_numeric_enthalpy_flow_is_rejected(self):
        doc = self._minimal()
        doc["streams"][0]["stream_properties"]["enthalpy_flow"] = "hot"
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])


if __name__ == "__main__":
    unittest.main()
