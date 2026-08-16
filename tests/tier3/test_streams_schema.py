# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 3: stream-schema tests, regrouped by category from the former Tier 1
# per-topic files. Covers sff_checks.md STR-11 (pressure > 0), STR-12
# (total_mass_flow required), the v0.0.9 per-phase stream_properties.phases
# restructuring, the v0.0.10 optional stream `roles` array, and the v0.0.11
# optional stream_properties.enthalpy_flow. Import-light: validates synthetic
# fragments against the real committed schema via jsonschema, never importing
# pisces_sff.
#
# Originally split across test_schema_constraints_v0_0_12.py (STR-11/12),
# test_schema_stream_phases_0_0_9.py, test_schema_stream_roles_0_0_10.py, and
# test_schema_stream_enthalpy_0_0_11.py; merged here per sff_checks.md's stream
# grouping. Every reject/accept assertion is preserved verbatim from those
# files; only class placement and the tier-3 skip gate changed.
#
# NAME-COLLISION NOTE: three source files each defined a class named
# TestSchemaVersion. Renamed here to keep them from silently shadowing one
# another in this shared module: TestSchemaVersionPhases009 (from
# test_schema_stream_phases_0_0_9.py), TestSchemaVersionRoles010 (from
# test_schema_stream_roles_0_0_10.py), TestSchemaVersionEnthalpy011 (from
# test_schema_stream_enthalpy_0_0_11.py). Method bodies are unchanged.

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


class TestSTR11Pressure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_zero_pressure_rejected(self):
        """STR-11 — stream pressure 0 (not >0) → schema rejects the document."""
        doc = minimal_doc()
        doc["streams"][0]["stream_properties"]["pressure"] = 0
        self.assertFalse(self.v.is_valid(doc))

    def test_positive_pressure_accepted(self):
        """STR-11 — pressure 101325 (>0) → schema accepts the document."""
        self.assertTrue(self.v.is_valid(minimal_doc()))


class TestSTR12TotalMassFlowRequired(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_missing_total_mass_flow_rejected(self):
        """STR-12 — stream_properties missing total_mass_flow → schema rejects the document."""
        doc = minimal_doc()
        del doc["streams"][0]["stream_properties"]["total_mass_flow"]
        self.assertFalse(self.v.is_valid(doc))

    def test_total_mass_flow_present_accepted(self):
        """STR-12 — stream_properties includes total_mass_flow → schema accepts the document."""
        self.assertTrue(self.v.is_valid(minimal_doc()))


# --- from test_schema_stream_phases_0_0_9.py -------------------------------

class TestSchemaVersionPhases009(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def test_schema_is_at_least_0_0_9(self):
        """Schema "version" ≥ 0.0.9 — the per-phase stream structure landed at 0.0.9."""
        version = tuple(int(p) for p in load_schema()["version"].split("."))
        self.assertGreaterEqual(version, (0, 0, 9))


class TestStreamPropertiesShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.sp = (load_schema()["properties"]["streams"]["items"]
                   ["properties"]["stream_properties"])

    def test_phases_is_required(self):
        """stream_properties lists "phases" in its required set."""
        self.assertIn("phases", self.sp["required"])

    def test_flat_composition_is_removed(self):
        """stream_properties no longer declares a flat top-level "composition" property (it moved under phases)."""
        self.assertNotIn("composition", self.sp["properties"])

    def test_phases_is_an_object_keyed_by_phase_symbol(self):
        """"phases" is an object whose additionalProperties $ref the stream_phase definition, with minProperties 1."""
        phases = self.sp["properties"]["phases"]
        self.assertEqual(phases["type"], "object")
        self.assertEqual(
            phases["additionalProperties"]["$ref"],
            "#/definitions/stream_phase",
        )
        self.assertEqual(phases["minProperties"], 1)

    def test_whole_stream_totals_are_retained(self):
        """Whole-stream totals (mass/molar/volumetric flow, temperature, pressure) remain number-typed on stream_properties."""
        for key in ("total_mass_flow", "total_molar_flow",
                    "total_volumetric_flow", "temperature", "pressure"):
            with self.subTest(field=key):
                self.assertEqual(self.sp["properties"][key]["type"], "number")


class TestStreamPhaseDefinition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.phase = load_schema()["definitions"]["stream_phase"]

    def test_requires_molar_flow_and_composition(self):
        """The stream_phase definition requires exactly composition + total_molar_flow."""
        self.assertEqual(
            sorted(self.phase["required"]),
            ["composition", "total_molar_flow"],
        )

    def test_phase_totals_are_numbers(self):
        """Per-phase total mass/molar/volumetric flow fields are number-typed in the stream_phase definition."""
        for key in ("total_mass_flow", "total_molar_flow",
                    "total_volumetric_flow"):
            with self.subTest(field=key):
                self.assertEqual(self.phase["properties"][key]["type"], "number")

    def test_composition_items_have_no_phase_field(self):
        """Per-phase composition items drop the "phase" field and require component_name + mol_fraction."""
        item = self.phase["properties"]["composition"]["items"]
        self.assertNotIn("phase", item["properties"])
        self.assertEqual(
            sorted(item["required"]),
            ["component_name", "mol_fraction"],
        )


class TestOldShapeIsRejected(unittest.TestCase):
    """A whole-document validator proves the restructuring actually bites."""

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

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
        """A minimal-but-valid v0.0.9 (per-phase) document → validator reports no errors."""
        self.assertEqual(list(self.validator.iter_errors(self._minimal())), [])

    def test_stream_without_phases_is_rejected(self):
        """Deleting stream_properties.phases → validator reports errors (phases is required)."""
        doc = self._minimal()
        del doc["streams"][0]["stream_properties"]["phases"]
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])

    def test_phase_without_composition_is_rejected(self):
        """Deleting a phase's composition → validator reports errors (composition is required per phase)."""
        doc = self._minimal()
        del doc["streams"][0]["stream_properties"]["phases"]["l"]["composition"]
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])

    def test_empty_phases_object_is_rejected(self):
        """phases set to {} (violates minProperties 1) → validator reports errors."""
        doc = self._minimal()
        doc["streams"][0]["stream_properties"]["phases"] = {}
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])


# --- from test_schema_stream_roles_0_0_10.py --------------------------------

ROLE_ENUM = ["input", "output", "purchased_raw_material",
             "feedstock", "product", "internal"]


class TestSchemaVersionRoles010(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def test_schema_is_at_least_0_0_10(self):
        """Schema "version" ≥ 0.0.10 — the stream `roles` array landed at 0.0.10."""
        version = tuple(int(p) for p in load_schema()["version"].split("."))
        self.assertGreaterEqual(version, (0, 0, 10))


class TestRolesShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.stream_items = (load_schema()["properties"]["streams"]["items"])
        self.roles = self.stream_items["properties"]["roles"]

    def test_roles_is_an_array(self):
        """The stream "roles" property is declared type array."""
        self.assertEqual(self.roles["type"], "array")

    def test_roles_has_unique_items(self):
        """The "roles" array sets uniqueItems true (no duplicate roles)."""
        self.assertTrue(self.roles["uniqueItems"])

    def test_roles_item_enum_is_the_six_role_names(self):
        """Each roles item is constrained to the six role-name enum (input/output/purchased_raw_material/feedstock/product/internal)."""
        self.assertEqual(self.roles["items"]["enum"], ROLE_ENUM)

    def test_roles_is_not_required(self):
        """"roles" is absent from the stream item's required set (optional/additive)."""
        # Optional-and-additive is the whole point: 0.0.9-shaped files that omit
        # roles must still validate against 0.0.10.
        self.assertNotIn("roles", self.stream_items["required"])


class TestRolesValidation(unittest.TestCase):
    """A whole-document validator proves the property actually bites."""

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

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
        """A minimal v0.0.10 document carrying roles → validator reports no errors."""
        self.assertEqual(list(self.validator.iter_errors(self._minimal())), [])

    def test_stream_without_roles_still_validates(self):
        """Deleting roles → still validates (roles is optional)."""
        doc = self._minimal()
        del doc["streams"][0]["roles"]
        self.assertEqual(list(self.validator.iter_errors(doc)), [])

    def test_out_of_enum_role_is_rejected(self):
        """roles ["catalyst"] (off-enum) → validator reports errors."""
        doc = self._minimal()
        doc["streams"][0]["roles"] = ["catalyst"]
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])

    def test_duplicate_role_is_rejected(self):
        """roles ["output", "output"] (duplicate) → validator reports errors (uniqueItems)."""
        doc = self._minimal()
        doc["streams"][0]["roles"] = ["output", "output"]
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])


# --- from test_schema_stream_enthalpy_0_0_11.py -----------------------------

class TestSchemaVersionEnthalpy011(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def test_schema_is_at_least_0_0_11(self):
        """Schema "version" ≥ 0.0.11 — the optional enthalpy_flow stream property landed at 0.0.11."""
        version = tuple(int(p) for p in load_schema()["version"].split("."))
        self.assertGreaterEqual(version, (0, 0, 11))


class TestEnthalpyFlowShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        schema = load_schema()
        self.stream_properties = (
            schema["properties"]["streams"]["items"]
            ["properties"]["stream_properties"]
        )
        self.registry = schema["properties"]["quantity_units_global"]

    def test_enthalpy_flow_is_a_number(self):
        """stream_properties.enthalpy_flow is declared number-typed."""
        prop = self.stream_properties["properties"]["enthalpy_flow"]
        self.assertEqual(prop["type"], "number")

    def test_enthalpy_flow_is_not_required(self):
        """enthalpy_flow is absent from stream_properties.required (optional/additive)."""
        # Optional-and-additive is the whole point: 0.0.10-shaped streams that
        # omit enthalpy_flow must still validate against 0.0.11.
        self.assertNotIn(
            "enthalpy_flow", self.stream_properties.get("required", []))

    def test_registry_declares_enthalpy_flow(self):
        """The quantity_units_global schema declares an enthalpy_flow entry."""
        self.assertIn(
            "enthalpy_flow", self.registry["properties"])


class TestEnthalpyFlowValidation(unittest.TestCase):
    """A whole-document validator proves the property actually bites."""

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

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
                             "total_mass_flow": 1.0,
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
        """A minimal v0.0.11 document carrying enthalpy_flow → validator reports no errors."""
        self.assertEqual(list(self.validator.iter_errors(self._minimal())), [])

    def test_stream_without_enthalpy_flow_still_validates(self):
        """Deleting enthalpy_flow → still validates (enthalpy_flow is optional)."""
        doc = self._minimal()
        del doc["streams"][0]["stream_properties"]["enthalpy_flow"]
        self.assertEqual(list(self.validator.iter_errors(doc)), [])

    def test_non_numeric_enthalpy_flow_is_rejected(self):
        """enthalpy_flow "hot" (non-number) → validator reports errors."""
        doc = self._minimal()
        doc["streams"][0]["stream_properties"]["enthalpy_flow"] = "hot"
        self.assertNotEqual(list(self.validator.iter_errors(doc)), [])


if __name__ == "__main__":
    unittest.main()
