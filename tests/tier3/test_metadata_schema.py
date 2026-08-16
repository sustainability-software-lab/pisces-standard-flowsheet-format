# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 3: metadata-schema tests, regrouped by category from the former Tier 1
# per-topic files. Covers sff_checks.md MET-01 (semver), MET-05 (TEA_currency
# non-empty), MET-06 (sha256 digest pattern), the 0.0.8 TEA_currency
# required-ness, the 0.0.6 metadata.reproducibility block, and the v0.0.5
# metadata.microorganisms shape. Import-light: validates synthetic fragments
# against the real committed schema via jsonschema, never importing pisces_sff.
#
# Originally split across test_schema_constraints_v0_0_12.py (MET-01/05/06),
# test_schema_tea_currency.py, test_schema_reproducibility.py, and
# test_schema_microorganisms.py; merged here per sff_checks.md's metadata
# grouping. Every reject/accept assertion is preserved verbatim from those
# files; only class placement and the tier-3 skip gate changed.

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


def microorganisms_subschema(schema):
    # metadata.properties.microorganisms is the definition we are pinning.
    return schema["properties"]["metadata"]["properties"]["microorganisms"]


def reproducibility_subschema(schema):
    return schema["properties"]["metadata"]["properties"]["reproducibility"]


def minimal_block():
    """The smallest reproducibility block the schema accepts."""
    return {
        "environment": {
            "format": "conda-environment-yaml",
            "filename": "environment.yaml",
            "sha256": "0" * 64,
            "content": "name: sff-test\n",
        },
        "load_script": {
            "format": "python",
            "filename": "load.py",
            "sha256": "1" * 64,
            "content": "def load():\n    pass\n",
        },
    }


class TestMET01SemVer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_valid_semver_accepted(self):
        self.assertTrue(self.v.is_valid(minimal_doc()))

    def test_non_semver_rejected(self):
        doc = minimal_doc()
        doc["metadata"]["sff_version"] = "point three"
        self.assertFalse(self.v.is_valid(doc))


class TestMET05Currency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def test_empty_currency_rejected(self):
        doc = minimal_doc()
        doc["metadata"]["TEA_currency"] = ""
        self.assertFalse(self.v.is_valid(doc))


class TestMET06Sha256(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

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


class TestTeaCurrencyShape(unittest.TestCase):
    """Structural assertions: TEA_currency is a required metadata string."""

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

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

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

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


class TestReproducibilityIsOptional(unittest.TestCase):
    """Additive by design: existing flowsheets must not become invalid."""

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.schema = load_schema()

    def test_block_is_declared(self):
        self.assertIn("reproducibility", self.schema["properties"]["metadata"]["properties"])

    def test_block_is_not_required(self):
        self.assertNotIn("reproducibility", self.schema["properties"]["metadata"]["required"])

    def test_block_is_an_object(self):
        # metadata declares additionalProperties: {"type": "string"}, so an
        # object-valued property is only expressible if declared explicitly.
        self.assertEqual(reproducibility_subschema(self.schema)["type"], "object")


class TestReproducibilityBlockValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.validator = Draft7Validator(reproducibility_subschema(load_schema()))

    def assertValid(self, block):
        errors = sorted(self.validator.iter_errors(block), key=lambda e: list(e.path))
        self.assertEqual(errors, [], f"unexpected errors: {[e.message for e in errors]}")

    def assertInvalid(self, block):
        self.assertTrue(list(self.validator.iter_errors(block)))

    def test_minimal_block_is_valid(self):
        self.assertValid(minimal_block())

    def test_environment_is_required(self):
        block = minimal_block()
        del block["environment"]
        self.assertInvalid(block)

    def test_load_script_is_required(self):
        block = minimal_block()
        del block["load_script"]
        self.assertInvalid(block)

    def test_environment_content_is_required(self):
        # Without the verbatim text the JSON stops being self-sufficient.
        block = minimal_block()
        del block["environment"]["content"]
        self.assertInvalid(block)

    def test_commit_pinned_package_is_valid(self):
        block = minimal_block()
        block["simulator_package"] = {
            "name": "biosteam",
            "url": "https://github.com/BioSTEAMDevelopmentGroup/biosteam",
            "commit": "e2d3942dd1076a4516efc91ae194f9e558428551",
        }
        self.assertValid(block)

    def test_version_pinned_package_is_valid(self):
        block = minimal_block()
        block["flowsheet_model_package"] = {"name": "biorefineries", "version": "2.25.0"}
        self.assertValid(block)

    def test_package_without_commit_or_version_is_rejected(self):
        # A package record that pins nothing does not reproduce anything.
        block = minimal_block()
        block["simulator_package"] = {"name": "biosteam"}
        self.assertInvalid(block)

    def test_commit_without_url_is_rejected(self):
        # A bare SHA cannot be fetched; the repository must be named.
        block = minimal_block()
        block["simulator_package"] = {
            "name": "biosteam",
            "commit": "e2d3942dd1076a4516efc91ae194f9e558428551",
        }
        self.assertInvalid(block)

    def test_version_pinned_package_needs_no_url(self):
        block = minimal_block()
        block["simulator_package"] = {"name": "biosteam", "version": "2.46.1"}
        self.assertValid(block)

    def test_resolved_block_is_accepted(self):
        block = minimal_block()
        block["resolved"] = {
            "python_version": "3.9.25",
            "platform": "Windows-10-10.0.26200-SP0",
            "env_key": "a" * 64,
            "exported_at": "2026-08-11T12:00:00Z",
            "package_versions": {"biosteam": "2.46.1"},
        }
        self.assertValid(block)


class TestMicroorganismsSchemaShape(unittest.TestCase):
    """Structural assertions: the field is an array of host objects.

    Proves the definition is the intended list-of-objects shape rather than the
    old scalar string, independent of any particular sample value.
    """

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.schema = load_schema()
        self.subschema = microorganisms_subschema(self.schema)

    def test_field_is_an_array(self):
        # Was `type: "string"` before this change; must now be `array`.
        self.assertEqual(self.subschema["type"], "array")

    def test_items_require_a_name(self):
        # Every host entry must carry a `name`; `label` stays optional.
        items = self.subschema["items"]
        self.assertEqual(items["type"], "object")
        self.assertIn("name", items["properties"])
        self.assertIn("label", items["properties"])
        self.assertEqual(items["required"], ["name"])

    def test_requires_at_least_one_entry_when_present(self):
        # minItems: 1 means an empty list is invalid, so the field either is
        # absent or names at least one host. This is what lets the exporter
        # safely omit the key when no organism is supplied.
        self.assertEqual(self.subschema.get("minItems"), 1)

    def test_field_is_optional_on_metadata(self):
        # microorganisms must NOT be in metadata.required: not every flowsheet
        # involves a microbial host, and existing v0.0.5 exports never emitted
        # the field. Keeping it optional preserves backward compatibility.
        required = self.schema["properties"]["metadata"].get("required", [])
        self.assertNotIn("microorganisms", required)


class TestMicroorganismsValidation(unittest.TestCase):
    """Behavioural checks: good values validate, bad values are rejected.

    Uses Draft7Validator (the same validator pisces_sff._validate uses) against
    the extracted sub-schema so we exercise the real schema semantics.
    """

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.validator = Draft7Validator(microorganisms_subschema(load_schema()))

    def assertValid(self, value):
        errors = list(self.validator.iter_errors(value))
        self.assertEqual(
            errors, [], msg=f"expected {value!r} to validate; got {errors}"
        )

    def assertInvalid(self, value):
        errors = list(self.validator.iter_errors(value))
        self.assertNotEqual(
            errors, [], msg=f"expected {value!r} to be rejected, but it validated"
        )

    def test_single_host_validates(self):
        self.assertValid([{"name": "E. coli"}])

    def test_multi_host_co_culture_with_label_validates(self):
        # The motivating case: a co-culture with a qualifying label.
        self.assertValid(
            [
                {"name": "E. coli"},
                {"name": "P. putida", "label": "co-culture host"},
            ]
        )

    def test_bare_string_is_rejected(self):
        # The old scalar form must no longer validate; this is the breaking
        # change that this PR intentionally introduces.
        self.assertInvalid("E. coli")

    def test_empty_list_is_rejected(self):
        # Guarded by minItems: 1.
        self.assertInvalid([])

    def test_entry_without_name_is_rejected(self):
        # `name` is required on each host entry.
        self.assertInvalid([{"label": "unnamed host"}])

    def test_entry_with_empty_name_is_rejected(self):
        # name has minLength: 1, so an empty string is not a valid host name.
        self.assertInvalid([{"name": ""}])


if __name__ == "__main__":
    unittest.main()
