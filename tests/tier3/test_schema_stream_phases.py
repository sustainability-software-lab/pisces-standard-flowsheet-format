# -*- coding: utf-8 -*-
# Pins the breaking v0.0.9 per-phase stream structure in the committed schema.
#
# Import-light (jsonschema on the committed file, never importing pisces_sff).
# Why pinned: through 0.0.8 a stream carried a single flat `composition` array
# with a per-component `phase` tag and reported total flows only for the whole
# stream. v0.0.9 makes each phase first-class: stream_properties.phases is an
# object keyed by phase symbol, each value a `stream_phase` with its own total
# molar/mass/volumetric flows and its own composition (fractions relative to
# that phase, no per-component `phase` field). This is a public-contract change
# a downstream PISCES consumer parses against; a silent revert would
# desynchronise producers and readers.

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
    def test_schema_is_at_least_0_0_9(self):
        version = tuple(int(p) for p in load_schema()["version"].split("."))
        self.assertGreaterEqual(version, (0, 0, 9))


class TestStreamPropertiesShape(unittest.TestCase):
    def setUp(self):
        self.sp = (load_schema()["properties"]["streams"]["items"]
                   ["properties"]["stream_properties"])

    def test_phases_is_required(self):
        self.assertIn("phases", self.sp["required"])

    def test_flat_composition_is_removed(self):
        self.assertNotIn("composition", self.sp["properties"])

    def test_phases_is_an_object_keyed_by_phase_symbol(self):
        phases = self.sp["properties"]["phases"]
        self.assertEqual(phases["type"], "object")
        self.assertEqual(
            phases["additionalProperties"]["$ref"],
            "#/definitions/stream_phase",
        )
        self.assertEqual(phases["minProperties"], 1)

    def test_whole_stream_totals_are_retained(self):
        for key in ("total_mass_flow", "total_molar_flow",
                    "total_volumetric_flow", "temperature", "pressure"):
            with self.subTest(field=key):
                self.assertEqual(self.sp["properties"][key]["type"], "number")


class TestStreamPhaseDefinition(unittest.TestCase):
    def setUp(self):
        self.phase = load_schema()["definitions"]["stream_phase"]

    def test_requires_molar_flow_and_composition(self):
        self.assertEqual(
            sorted(self.phase["required"]),
            ["composition", "total_molar_flow"],
        )

    def test_phase_totals_are_numbers(self):
        for key in ("total_mass_flow", "total_molar_flow",
                    "total_volumetric_flow"):
            with self.subTest(field=key):
                self.assertEqual(self.phase["properties"][key]["type"], "number")

    def test_composition_items_have_no_phase_field(self):
        item = self.phase["properties"]["composition"]["items"]
        self.assertNotIn("phase", item["properties"])
        self.assertEqual(
            sorted(item["required"]),
            ["component_name", "mol_fraction"],
        )


class TestOldShapeIsRejected(unittest.TestCase):
    """A whole-document validator proves the restructuring actually bites."""

    def setUp(self):
        self.validator = Draft7Validator(load_schema())

    def _minimal(self):
        # A minimal-but-valid v0.0.9 document; individual tests corrupt streams.
        return {
            "metadata": {
                "sff_version": "0.0.9", "TEA_currency": "USD", "TEA_year": 2020,
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

    def test_minimal_v0_0_9_document_validates(self):
        self.assertEqual(list(self.validator.iter_errors(self._minimal())), [])

    def test_stream_without_phases_is_rejected(self):
        doc = self._minimal()
        del doc["streams"][0]["stream_properties"]["phases"]
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])

    def test_phase_without_composition_is_rejected(self):
        doc = self._minimal()
        del doc["streams"][0]["stream_properties"]["phases"]["l"]["composition"]
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])

    def test_empty_phases_object_is_rejected(self):
        doc = self._minimal()
        doc["streams"][0]["stream_properties"]["phases"] = {}
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])


if __name__ == "__main__":
    unittest.main()
