# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 3: schema-shape tests not tied to a single sff_checks.md requirement ID
# -- the v0.0.12 top-level version pin, the breaking v0.0.7 bare-number
# quantity-unit shape (quantity_units_global, renamed utility keys, per-unit
# quantity_units_for_design_results), and the additive quantity_units_global
# registry / quantity_unit_entry definition. Import-light: validates synthetic
# fragments against the real committed schema via jsonschema, never importing
# pisces_sff.
#
# Originally split across test_schema_constraints_v0_0_12.py (top-level
# TestSchemaVersion), test_schema_quantity_units_0_0_7.py, and
# test_schema_quantity_units_global.py; merged here per sff_checks.md's
# "not tied to one ID" grouping. Every reject/accept assertion is preserved
# verbatim from those files; only class placement and the tier-3 skip gate
# changed.
#
# NAME-COLLISION NOTE: two source files each defined a class named
# TestSchemaVersion. Renamed here to keep them from silently shadowing one
# another in this shared module: TestSchemaVersionConstraints012 (from
# test_schema_constraints_v0_0_12.py), TestSchemaVersionQuantityUnits007 (from
# test_schema_quantity_units_0_0_7.py). Method bodies are unchanged.

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


class TestSchemaVersionConstraints012(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def test_schema_declares_0_0_12(self):
        self.assertEqual(load_schema()["version"], "0.0.12")


# --- from test_schema_quantity_units_0_0_7.py -------------------------------

class TestSchemaVersionQuantityUnits007(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def test_schema_is_at_least_0_0_7(self):
        # The bare-number quantity-unit shape this suite pins was introduced in
        # 0.0.7 and still holds; assert a floor rather than an exact version so
        # a later additive bump (e.g. 0.0.8's TEA_currency) does not break it.
        version = tuple(int(p) for p in load_schema()["version"].split("."))
        self.assertGreaterEqual(version, (0, 0, 7))


class TestScalarsAreBareNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.schema = load_schema()

    def test_stream_price_is_a_number(self):
        price = self.schema["properties"]["streams"]["items"]["properties"]["price"]
        self.assertEqual(price["type"], "number")

    def test_stream_properties_scalars_are_numbers(self):
        props = (self.schema["properties"]["streams"]["items"]
                 ["properties"]["stream_properties"]["properties"])
        for key in ("total_mass_flow", "total_molar_flow",
                    "total_volumetric_flow", "temperature", "pressure"):
            with self.subTest(field=key):
                self.assertEqual(props[key]["type"], "number")
        # temperature keeps its physical floor.
        self.assertEqual(props["temperature"]["minimum"], 0)

    def test_stream_properties_required_is_preserved(self):
        # The three 0.0.7-era members stay required; 0.0.9 additionally requires
        # `phases`, so assert the subset rather than exact equality.
        sp = (self.schema["properties"]["streams"]["items"]
              ["properties"]["stream_properties"])
        self.assertTrue(
            {"pressure", "temperature", "total_molar_flow"}.issubset(sp["required"])
        )

    def test_heat_utility_scalars_are_numbers(self):
        props = (self.schema["properties"]["utilities"]["properties"]
                 ["heat_utilities"]["items"]["properties"])
        for key in ("temperature", "pressure", "regeneration_price",
                    "heat_transfer_price", "temperature_limit"):
            with self.subTest(field=key):
                self.assertEqual(props[key]["type"], "number")


class TestRenamedUtilityKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.util = load_schema()["properties"]["utilities"]["properties"]

    def test_heat_utility_uses_quantity_units_key(self):
        items = self.util["heat_utilities"]["items"]
        self.assertIn("quantity_units_for_utility_results", items["properties"])
        self.assertNotIn("units_for_utility_results", items["properties"])
        self.assertIn("quantity_units_for_utility_results", items["required"])
        self.assertNotIn("units_for_utility_results", items["required"])

    def test_power_utility_price_is_renamed_electrical_energy_price(self):
        items = self.util["power_utilities"]["items"]
        self.assertIn("electrical_energy_price", items["properties"])
        self.assertNotIn("price", items["properties"])
        self.assertEqual(
            items["properties"]["electrical_energy_price"]["type"], "number"
        )
        self.assertIn("quantity_units_for_utility_results", items["properties"])

    def test_other_utility_uses_quantity_units_key(self):
        items = self.util["other_utilities"]["items"]
        self.assertIn("quantity_units_for_utility_results", items["properties"])
        self.assertNotIn("units_for_utility_results", items["properties"])
        self.assertIn("quantity_units_for_utility_results", items["required"])


class TestDesignResultUnitsField(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def test_units_declare_quantity_units_for_design_results(self):
        unit = load_schema()["properties"]["units"]["items"]["properties"]
        field = unit["quantity_units_for_design_results"]
        self.assertEqual(field["type"], "object")
        self.assertEqual(field["additionalProperties"]["type"], "string")


class TestOldShapeIsRejected(unittest.TestCase):
    """A whole-document validator proves the retypings actually bite."""

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.validator = Draft7Validator(load_schema())

    def _minimal(self):
        # A minimal-but-valid v0.0.7 document; individual tests corrupt one field.
        return {
            "metadata": {
                # TEA_currency is required as of 0.0.8; the quantity-unit shape
                # under test is unchanged from 0.0.7, so the doc still exercises it.
                "sff_version": "0.0.7", "TEA_currency": "USD", "TEA_year": 2020,
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

    def test_minimal_v0_0_7_document_validates(self):
        self.assertEqual(list(self.validator.iter_errors(self._minimal())), [])

    def test_inline_price_pair_is_rejected(self):
        doc = self._minimal()
        doc["streams"][0]["price"] = {"value": 0.1, "units": "$/kg"}
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])

    def test_legacy_utility_results_key_is_rejected(self):
        doc = self._minimal()
        doc["utilities"]["heat_utilities"] = [{
            "id": "hps", "temperature": 500.0, "pressure": 101325.0,
            "composition": [], "units_for_utility_results": "kJ/h"}]
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])


# --- from test_schema_quantity_units_global.py ------------------------------

class TestQuantityUnitsGlobalShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

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
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

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
