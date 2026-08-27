# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: the tag registry (pisces_sff/tags/tags.yaml) -- the single source of
# truth for tag names, check subsets, and tolerated-skip policies (sff_checks.md
# section 8). Pins: the committed registry's content, the loader's fail-fast
# shape validation, and sync with the schema's metadata.tags enum. Import-light.

import json
import tempfile
import unittest
from pathlib import Path

from tests._validate_loader import V

_REPO = Path(__file__).resolve().parents[2]
SCHEMA_PATH = _REPO / "pisces_sff" / "schema" / "sff_schema.json"
TAGS_YAML_PATH = _REPO / "pisces_sff" / "tags" / "tags.yaml"

# The canonical tag order and policies, as stated in sff_checks.md section 8.
# Deliberately duplicated here (not read from the YAML) so an accidental edit
# to the committed registry fails a test instead of silently redefining policy.
EXPECTED_TAG_ORDER = ("exported-from-simulator", "extracted-from-prose",
                      "extracted-from-image", "extracted-from-table",
                      "reproducible")
EXPECTED_EFS_TOLERATED = {
    "STR-03": "always", "STR-13": "always", "CHEM-04": "always",
    "STR-10": "all_streams_empty",
    "UNIT-04": "no_reactions", "UNIT-05": "no_reactions",
    "UNIT-06": "no_reactions",
}


class _RegistryPatchCase(unittest.TestCase):
    """Base: load a throwaway registry text through the real loader, restoring
    V's registry path + cache afterwards so other tests see the committed one."""

    def _load_from(self, text):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        tmp = Path(td.name) / "tags.yaml"
        tmp.write_text(text, encoding="utf-8")
        old_path, old_cache = V._TAGS_YAML, V._TAG_REGISTRY_CACHE

        def _restore():
            V._TAGS_YAML, V._TAG_REGISTRY_CACHE = old_path, old_cache

        self.addCleanup(_restore)
        V._TAGS_YAML, V._TAG_REGISTRY_CACHE = tmp, None
        return V._load_tag_registry()


class TestCommittedRegistry(unittest.TestCase):
    """The committed pisces_sff/tags/tags.yaml carries exactly the sff_checks.md
    section 8 policy tables."""

    def test_registry_file_exists(self):
        self.assertTrue(TAGS_YAML_PATH.is_file())

    def test_tag_names_in_canonical_order(self):
        self.assertEqual(V._tag_names(), EXPECTED_TAG_ORDER)

    def test_exported_from_simulator_subset_is_all_sentinel(self):
        entry = V._load_tag_registry()["exported-from-simulator"]
        self.assertEqual(entry["class"], "static")
        self.assertIsNone(entry["subset"])  # 'all' sentinel

    def test_exported_from_simulator_tolerated_skips_table(self):
        entry = V._load_tag_registry()["exported-from-simulator"]
        self.assertEqual(entry["tolerated_skips"], EXPECTED_EFS_TOLERATED)

    def test_extracted_tags_subset_and_no_tolerated_skips(self):
        reg = V._load_tag_registry()
        for tag in ("extracted-from-prose", "extracted-from-image",
                    "extracted-from-table"):
            self.assertEqual(reg[tag]["class"], "static")
            self.assertEqual(reg[tag]["subset"],
                             frozenset({"UNIT-10", "STR-14"}))
            self.assertEqual(reg[tag]["tolerated_skips"], {})

    def test_reproducible_is_harness_with_no_subset(self):
        self.assertEqual(V._load_tag_registry()["reproducible"],
                         {"class": "harness"})

    def test_every_condition_name_resolves(self):
        reg = V._load_tag_registry()
        for entry in reg.values():
            for cond in entry.get("tolerated_skips", {}).values():
                self.assertIn(cond, V._TOLERATED_SKIP_CONDITIONS)


class TestLoaderShapeValidation(_RegistryPatchCase):
    """The loader fails fast (ValueError) on malformed registries: the file is
    committed repo infrastructure, so a broken registry must abort tag
    evaluation rather than silently skip the TAG-01 gate."""

    def test_minimal_valid_registry_loads(self):
        reg = self._load_from(
            "tags:\n"
            "  t:\n"
            "    class: static\n"
            "    subset: [UNIT-10]\n")
        self.assertEqual(reg, {"t": {"class": "static",
                                     "subset": frozenset({"UNIT-10"}),
                                     "tolerated_skips": {}}})

    def test_subset_all_maps_to_none_sentinel(self):
        reg = self._load_from(
            "tags:\n"
            "  t:\n"
            "    class: static\n"
            "    subset: all\n")
        self.assertIsNone(reg["t"]["subset"])

    def test_unknown_condition_name_rejected(self):
        with self.assertRaisesRegex(ValueError, "sometimes"):
            self._load_from(
                "tags:\n"
                "  t:\n"
                "    class: static\n"
                "    subset: all\n"
                "    tolerated_skips:\n"
                "      STR-03: sometimes\n")

    def test_unknown_class_rejected(self):
        with self.assertRaisesRegex(ValueError, "class"):
            self._load_from(
                "tags:\n"
                "  t:\n"
                "    class: dynamic\n"
                "    subset: all\n")

    def test_harness_tag_with_subset_rejected(self):
        with self.assertRaisesRegex(ValueError, "harness"):
            self._load_from(
                "tags:\n"
                "  t:\n"
                "    class: harness\n"
                "    subset: all\n")

    def test_unknown_per_tag_key_rejected(self):
        with self.assertRaisesRegex(ValueError, "color"):
            self._load_from(
                "tags:\n"
                "  t:\n"
                "    class: static\n"
                "    subset: all\n"
                "    color: blue\n")

    def test_missing_top_level_tags_mapping_rejected(self):
        with self.assertRaisesRegex(ValueError, "tags"):
            self._load_from("labels:\n  t:\n    class: static\n")

    def test_static_subset_must_be_all_or_string_list(self):
        with self.assertRaisesRegex(ValueError, "subset"):
            self._load_from(
                "tags:\n"
                "  t:\n"
                "    class: static\n"
                "    subset: 7\n")

    def test_result_is_cached(self):
        reg1 = self._load_from(
            "tags:\n"
            "  t:\n"
            "    class: static\n"
            "    subset: all\n")
        self.assertIs(V._load_tag_registry(), reg1)

    def test_duplicate_tag_key_rejected(self):
        """A repeated mapping key is silently last-wins under plain
        yaml.safe_load; _yaml_load_no_duplicates rejects it at load time so a
        registry edit cannot override an earlier entry unnoticed."""
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self._load_from(
                "tags:\n"
                "  t:\n"
                "    class: static\n"
                "    subset: all\n"
                "  t:\n"
                "    class: harness\n")

    def test_duplicate_nested_key_rejected(self):
        """Duplicate keys are rejected at any depth (here inside
        tolerated_skips), not just at the top level -- even when the repeated
        entry's value is identical."""
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self._load_from(
                "tags:\n"
                "  t:\n"
                "    class: static\n"
                "    subset: all\n"
                "    tolerated_skips:\n"
                "      STR-03: always\n"
                "      STR-03: always\n")

    def test_missing_registry_file_rejected(self):
        """A missing registry file surfaces as ValueError ('not readable'),
        never a raw OSError -- matching the model/design-spec registry
        loaders' contract."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        old_path, old_cache = V._TAGS_YAML, V._TAG_REGISTRY_CACHE

        def _restore():
            V._TAGS_YAML, V._TAG_REGISTRY_CACHE = old_path, old_cache

        self.addCleanup(_restore)
        V._TAGS_YAML = Path(td.name) / "no_such.yaml"
        V._TAG_REGISTRY_CACHE = None
        with self.assertRaisesRegex(ValueError, "not readable"):
            V._load_tag_registry()

    def test_invalid_yaml_syntax_rejected(self):
        """Syntactically invalid YAML surfaces as ValueError ('not valid
        YAML'), never a raw yaml.YAMLError leak."""
        with self.assertRaisesRegex(ValueError, "not valid YAML"):
            self._load_from("tags: [unclosed\n")


class TestSchemaEnumSync(unittest.TestCase):
    """The metadata.tags enum in sff_schema.json is hardcoded by design (the
    schema stays a plain, self-contained public contract); this test is the
    guard that keeps it equal to the registry's tag names."""

    @classmethod
    def setUpClass(cls):
        with SCHEMA_PATH.open("r", encoding="utf-8") as f:
            cls.schema = json.load(f)
        cls.metadata = cls.schema["properties"]["metadata"]

    def test_schema_enum_equals_registry_tag_names(self):
        enum = self.metadata["properties"]["tags"]["items"]["enum"]
        self.assertEqual(tuple(enum), V._tag_names())

    def test_schema_if_then_reproducible_is_a_registry_tag(self):
        const = (self.metadata["if"]["properties"]["tags"]["contains"]
                 ["const"])
        self.assertEqual(const, "reproducible")
        self.assertIn(const, V._tag_names())


if __name__ == "__main__":
    unittest.main()
