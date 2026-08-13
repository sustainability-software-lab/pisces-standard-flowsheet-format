# -*- coding: utf-8 -*-
# Tests for the v0.0.6 `metadata.reproducibility` schema definition.
#
# This block is what makes an exported flowsheet rebuildable from the JSON
# alone, so its shape is a public contract: PISCES reads `simulator_package`
# and `flowsheet_model_package` to index provenance without parsing the
# embedded YAML, and reads `environment.content` / `load_script.content` to
# reconstruct the recipe. The assertions below pin (a) that the block stays
# optional -- all 18 pre-existing flowsheets must keep validating -- and (b)
# that a package pin can never be ambiguous: it names either a VCS commit or a
# PyPI version, and a commit is meaningless without the repository URL.
#
# Design notes:
#   * As in tests/tier1/test_schema_microorganisms.py, this test uses `jsonschema`
#     directly rather than importing `pisces_sff`, which would drag in the
#     heavy biosteam/thermosteam stack for what is purely a schema check.

import json
import unittest
from pathlib import Path

from jsonschema import Draft7Validator

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "pisces_sff"
    / "schema"
    / "sff_schema.json"
)


def load_schema():
    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


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


class TestReproducibilityIsOptional(unittest.TestCase):
    """Additive by design: existing flowsheets must not become invalid."""

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


if __name__ == "__main__":
    unittest.main()
