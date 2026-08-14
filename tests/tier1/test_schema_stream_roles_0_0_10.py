# -*- coding: utf-8 -*-
# Pins the additive, optional v0.0.10 stream `roles` property in the committed
# schema.
#
# Import-light (jsonschema on the committed file, never importing pisces_sff).
# Why pinned: v0.0.10 adds an optional `roles` array to every stream so a
# flowsheet can declare the roles a stream plays -- exactly one base topology
# role (input | output | internal) plus any designation roles
# (purchased_raw_material, feedstock, product). It is additive and NOT in the
# stream `required` list, so files that predate it must keep validating; a
# downstream PISCES consumer parses `roles` when present. This test guards both
# the enum/shape and the fact that it stays optional -- a silent tightening
# (adding it to `required`, or dropping a role name) would break producers and
# readers that already wrote 0.0.9-shaped files.

import json
import unittest
from pathlib import Path

from jsonschema import Draft7Validator

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "pisces_sff" / "schema" / "sff_schema.json"
)

ROLE_ENUM = ["input", "output", "purchased_raw_material",
             "feedstock", "product", "internal"]


def load_schema():
    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


class TestSchemaVersion(unittest.TestCase):
    def test_schema_is_at_least_0_0_10(self):
        version = tuple(int(p) for p in load_schema()["version"].split("."))
        self.assertGreaterEqual(version, (0, 0, 10))


class TestRolesShape(unittest.TestCase):
    def setUp(self):
        self.stream_items = (load_schema()["properties"]["streams"]["items"])
        self.roles = self.stream_items["properties"]["roles"]

    def test_roles_is_an_array(self):
        self.assertEqual(self.roles["type"], "array")

    def test_roles_has_unique_items(self):
        self.assertTrue(self.roles["uniqueItems"])

    def test_roles_item_enum_is_the_six_role_names(self):
        self.assertEqual(self.roles["items"]["enum"], ROLE_ENUM)

    def test_roles_is_not_required(self):
        # Optional-and-additive is the whole point: 0.0.9-shaped files that omit
        # roles must still validate against 0.0.10.
        self.assertNotIn("roles", self.stream_items["required"])


class TestRolesValidation(unittest.TestCase):
    """A whole-document validator proves the property actually bites."""

    def setUp(self):
        self.validator = Draft7Validator(load_schema())

    def _minimal(self):
        # A minimal-but-valid v0.0.10 document; individual tests corrupt `roles`.
        return {
            "metadata": {
                "sff_version": "0.0.10", "TEA_currency": "USD", "TEA_year": 2020,
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

    def test_minimal_v0_0_10_document_with_roles_validates(self):
        self.assertEqual(list(self.validator.iter_errors(self._minimal())), [])

    def test_stream_without_roles_still_validates(self):
        doc = self._minimal()
        del doc["streams"][0]["roles"]
        self.assertEqual(list(self.validator.iter_errors(doc)), [])

    def test_out_of_enum_role_is_rejected(self):
        doc = self._minimal()
        doc["streams"][0]["roles"] = ["catalyst"]
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])

    def test_duplicate_role_is_rejected(self):
        doc = self._minimal()
        doc["streams"][0]["roles"] = ["output", "output"]
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])


if __name__ == "__main__":
    unittest.main()
