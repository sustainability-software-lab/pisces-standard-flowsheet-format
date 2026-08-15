# -*- coding: utf-8 -*-
# Tests for the v0.0.5 `metadata.microorganisms` schema definition.
#
# These tests exist to lock in the string -> list change for the microorganisms
# field: it is now an array of {"name": str, "label"?: str} host objects rather
# than a single scalar string. They deliberately validate against the real,
# committed schema file (pisces_sff/schema/sff_schema.json) so that any future
# edit that silently reverts or breaks this shape fails here.
#
# Design notes:
#   * We use `jsonschema` directly (it is already a dependency of this package,
#     see pisces_sff/_validate.py) instead of importing the top-level
#     `pisces_sff` package. Importing the package would trigger
#     pisces_sff/__init__.py -> _export, which pulls in the heavy optional
#     `biosteam`/`thermosteam` runtime deps that are unnecessary for validating
#     a schema. Keeping this test import-light means it runs anywhere jsonschema
#     is installed, without a full simulation stack.
#   * We validate candidate values against the *microorganisms sub-schema*
#     extracted from the file. This targets exactly the field under test and is
#     independent of the other required metadata fields.

import json
import unittest
from pathlib import Path

from jsonschema import Draft7Validator

# The schema is the single source of truth on `main` (the repo moved from
# versioned per-file schemas to one consolidated sff_schema.json).
SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "pisces_sff"
    / "schema"
    / "sff_schema.json"
)


def load_schema():
    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def microorganisms_subschema(schema):
    # metadata.properties.microorganisms is the definition we are pinning.
    return schema["properties"]["metadata"]["properties"]["microorganisms"]


class TestMicroorganismsSchemaShape(unittest.TestCase):
    """Structural assertions: the field is an array of host objects.

    Proves the definition is the intended list-of-objects shape rather than the
    old scalar string, independent of any particular sample value.
    """

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
