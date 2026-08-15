# -*- coding: utf-8 -*-
# Pins the additive `quantity_units_global` registry and its reusable
# `definitions/quantity_unit_entry`. Import-light: validates against the
# committed schema file with jsonschema directly, never importing pisces_sff
# (which would drag in biosteam via _export). See tests/tier1/test_schema_microorganisms.py
# for the same rationale.
#
# Why pinned: quantity_units_global is the single machine-readable source of
# units for every bare-number quantity in a v0.0.7 file. A consumer resolves a
# field (e.g. "T", "total_mass_flow") to a unit through the entry's `aliases`
# and `quantity_units`; if either is dropped or retyped, resolution breaks
# silently. The field is optional at the top level (a producer may omit it and
# fall back to documented defaults), so this suite proves the *shape*, not its
# presence in any particular file.

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


class TestQuantityUnitsGlobalShape(unittest.TestCase):
    def setUp(self):
        self.schema = load_schema()

    def test_registry_is_an_optional_top_level_object(self):
        self.assertIn("quantity_units_global", self.schema["properties"])
        self.assertEqual(
            self.schema["properties"]["quantity_units_global"]["type"], "object"
        )
        # Optional: adding it must not force every existing file to carry it.
        self.assertNotIn("quantity_units_global", self.schema.get("required", []))

    def test_entry_definition_requires_aliases_and_quantity_units(self):
        entry = self.schema["definitions"]["quantity_unit_entry"]
        self.assertEqual(entry["type"], "object")
        self.assertEqual(sorted(entry["required"]), ["aliases", "quantity_units"])
        self.assertEqual(entry["properties"]["aliases"]["type"], "array")
        self.assertEqual(entry["properties"]["aliases"]["minItems"], 1)
        self.assertEqual(
            entry["properties"]["aliases"]["items"]["type"], "string"
        )
        self.assertEqual(entry["properties"]["quantity_units"]["type"], "string")

    def test_canonical_quantities_reference_the_entry_definition(self):
        # Every widely-used scalar and price the exporter emits must be declared.
        props = self.schema["properties"]["quantity_units_global"]["properties"]
        for key in ("temperature", "pressure", "mass_flow", "molar_flow",
                    "volumetric_flow", "molar_mass", "price",
                    "electrical_energy_price", "regeneration_price",
                    "heat_transfer_price"):
            with self.subTest(quantity=key):
                self.assertEqual(
                    props[key]["$ref"], "#/definitions/quantity_unit_entry"
                )

    def test_additional_quantities_also_use_the_entry_definition(self):
        # A producer may declare quantities beyond the canonical set.
        reg = self.schema["properties"]["quantity_units_global"]
        self.assertEqual(
            reg["additionalProperties"]["$ref"], "#/definitions/quantity_unit_entry"
        )


class TestQuantityUnitEntryValidation(unittest.TestCase):
    def setUp(self):
        schema = load_schema()
        # Resolve the $ref by validating against the whole schema's definition.
        self.validator = Draft7Validator(
            {"definitions": schema["definitions"],
             "$ref": "#/definitions/quantity_unit_entry"}
        )

    def assertValid(self, value):
        errors = list(self.validator.iter_errors(value))
        self.assertEqual(errors, [], msg=f"expected {value!r} to validate; got {errors}")

    def assertInvalid(self, value):
        self.assertNotEqual(list(self.validator.iter_errors(value)), [])

    def test_full_entry_validates(self):
        self.assertValid({"aliases": ["temperature", "T"], "quantity_units": "K"})

    def test_entry_without_aliases_is_rejected(self):
        self.assertInvalid({"quantity_units": "K"})

    def test_entry_with_empty_aliases_is_rejected(self):
        self.assertInvalid({"aliases": [], "quantity_units": "K"})

    def test_entry_without_quantity_units_is_rejected(self):
        self.assertInvalid({"aliases": ["temperature"]})


if __name__ == "__main__":
    unittest.main()
