# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: the reproducible engine EXCEPT the real simulation. Tests the pure
# deep-compare and recipe-reconstruction directly, and verify_reproducible with a
# STUBBED export callable (export=...), so no biosteam/harness import happens.

import json
import tempfile
import unittest
from pathlib import Path

from tests._validate_loader import V


class TestDeepCompare(unittest.TestCase):
    def test_identical_documents_match(self):
        a = {"x": 1.0, "y": [1, 2], "z": {"k": "v"}}
        self.assertEqual(V._deep_compare_reexport(a, dict(a), 1e-4), [])

    def test_numeric_within_rtol_matches(self):
        diffs = V._deep_compare_reexport({"x": 1.0}, {"x": 1.00001}, 1e-4)
        self.assertEqual(diffs, [])

    def test_numeric_outside_rtol_differs(self):
        diffs = V._deep_compare_reexport({"x": 1.0}, {"x": 2.0}, 1e-4)
        self.assertTrue(diffs)

    def test_zero_reference_state_matches_via_absolute_floor(self):
        self.assertEqual(V._deep_compare_reexport({"h": 0.0}, {"h": 0.0}, 1e-4), [])

    def test_structural_mismatch_flagged(self):
        self.assertTrue(V._deep_compare_reexport({"a": 1}, {"b": 1}, 1e-4))
        self.assertTrue(V._deep_compare_reexport({"a": [1]}, {"a": [1, 2]}, 1e-4))

    def test_non_numeric_leaf_mismatch_flagged(self):
        self.assertTrue(V._deep_compare_reexport({"id": "A"}, {"id": "B"}, 1e-4))

    def test_ignored_paths_do_not_differ(self):
        original = {"metadata": {"tags": ["exported-from-simulator", "reproducible"],
                                 "reproducibility": {"comparison_rtol": 1e-4,
                                     "resolved": {"exported_at": "T1",
                                                  "platform": "P1",
                                                  "python_version": "3.9.1",
                                                  "env_key": "k"}}}}
        reexport = {"metadata": {"tags": ["exported-from-simulator"],
                                 "reproducibility": {
                                     "resolved": {"exported_at": "T2",
                                                  "platform": "P2",
                                                  "python_version": "3.9.2",
                                                  "env_key": "k"}}}}
        self.assertEqual(V._deep_compare_reexport(original, reexport, 1e-4), [])

    def test_env_key_mismatch_is_flagged(self):
        original = {"metadata": {"reproducibility": {"resolved": {"env_key": "a"}}}}
        reexport = {"metadata": {"reproducibility": {"resolved": {"env_key": "b"}}}}
        self.assertTrue(V._deep_compare_reexport(original, reexport, 1e-4))

    def test_bool_vs_int_one_is_flagged(self):
        """bool is an int subclass, so a bare `!=` would coerce True == 1 and
        False == 0/0.0 as equal. Exactly one side being bool must be flagged as
        a diff regardless of numeric equality."""
        self.assertTrue(V._deep_compare_reexport({"x": True}, {"x": 1}, 1e-4))
        self.assertTrue(V._deep_compare_reexport({"x": False}, {"x": 0}, 1e-4))

    def test_bool_vs_bool_equal_matches(self):
        self.assertEqual(V._deep_compare_reexport({"x": True}, {"x": True}, 1e-4), [])

    def test_bool_vs_bool_unequal_is_flagged(self):
        self.assertTrue(V._deep_compare_reexport({"x": True}, {"x": False}, 1e-4))

    def test_recipe_path_present_only_in_original_is_ignored(self):
        """A repo-relative `path` on environment/load_script/extended_metadata
        (recorded by _runner.py._file_record only when the ORIGINAL model
        directory lived under REPO_ROOT) is ignored, because
        _reconstruct_model_dir always writes the reconstruction to a tempdir
        OUTSIDE the repo -- so a re-export can never carry this key. Without
        this ignore, no in-repo model could ever earn `reproducible` (the key
        would always show as "present in only one document")."""
        original = {"metadata": {"reproducibility": {
            "environment": {"filename": "environment.yaml", "content": "x",
                            "path": "pisces_sff/models/foo/environment.yaml"},
            "load_script": {"filename": "load.py", "content": "y",
                            "path": "pisces_sff/models/foo/load.py"},
            "extended_metadata": {"filename": "extended_metadata.yaml",
                                  "content": "z",
                                  "path": "pisces_sff/models/foo/extended_metadata.yaml"},
        }}}
        reexport = {"metadata": {"reproducibility": {
            "environment": {"filename": "environment.yaml", "content": "x"},
            "load_script": {"filename": "load.py", "content": "y"},
            "extended_metadata": {"filename": "extended_metadata.yaml",
                                  "content": "z"},
        }}}
        self.assertEqual(V._deep_compare_reexport(original, reexport, 1e-4), [])


class TestReconstructModelDir(unittest.TestCase):
    def _doc(self):
        return {"metadata": {"sff_version": "0.1.3", "reproducibility": {
            "environment": {"filename": "environment.yaml",
                            "content": "name: env\nline: two\n"},
            "load_script": {"filename": "load.py",
                            "content": "def load():\n    return 1, 2\n"},
            "extended_metadata": {"filename": "extended_metadata.yaml",
                                  "content": "source_doi: x\n"}}}}

    def test_writes_all_three_files_with_lf_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            dest = V._reconstruct_model_dir(self._doc(), Path(d))
            env = (dest / "environment.yaml").read_bytes()
            self.assertEqual(env, b"name: env\nline: two\n")   # LF, no CRLF
            self.assertTrue((dest / "load.py").exists())
            self.assertTrue((dest / "extended_metadata.yaml").exists())

    def test_extended_metadata_optional(self):
        doc = self._doc()
        del doc["metadata"]["reproducibility"]["extended_metadata"]
        with tempfile.TemporaryDirectory() as d:
            dest = V._reconstruct_model_dir(doc, Path(d))
            self.assertFalse((dest / "extended_metadata.yaml").exists())


class TestVerifyReproducibleStubbed(unittest.TestCase):
    def _write(self, doc):
        d = tempfile.mkdtemp()
        p = Path(d) / "corn.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return str(p)

    def _doc(self, rtol=1e-4):
        return {"metadata": {"sff_version": "0.1.3",
                             "tags": ["exported-from-simulator", "reproducible"],
                             "reproducibility": {
                                 "comparison_rtol": rtol,
                                 "environment": {"filename": "environment.yaml",
                                                 "content": "name: e\n"},
                                 "load_script": {"filename": "load.py",
                                                 "content": "def load():\n    pass\n"},
                                 "resolved": {"exported_at": "T1", "env_key": "k"}}},
                "units": [{"id": "U1", "unit_type": "Mixer"}]}

    def test_no_recipe_returns_false(self):
        path = self._write({"metadata": {}})
        matches, diffs = V.verify_reproducible(path, export=lambda *a, **k: None)
        self.assertFalse(matches)

    def test_matching_reexport_returns_true(self):
        doc = self._doc()

        def fake_export(model_dir, output_path, sff_version=None, **kw):
            # Reproduce the file, minus the post-hoc annotations, new timestamp.
            reexport = json.loads(json.dumps(doc))
            reexport["metadata"]["tags"] = ["exported-from-simulator"]
            del reexport["metadata"]["reproducibility"]["comparison_rtol"]
            reexport["metadata"]["reproducibility"]["resolved"]["exported_at"] = "T2"
            Path(output_path).write_text(json.dumps(reexport), encoding="utf-8")
            return Path(output_path)

        matches, diffs = V.verify_reproducible(self._write(doc), export=fake_export)
        self.assertTrue(matches, diffs)

    def test_perturbed_reexport_returns_false(self):
        doc = self._doc()

        def fake_export(model_dir, output_path, sff_version=None, **kw):
            reexport = json.loads(json.dumps(doc))
            reexport["units"][0]["unit_type"] = "Splitter"  # genuine difference
            Path(output_path).write_text(json.dumps(reexport), encoding="utf-8")
            return Path(output_path)

        matches, diffs = V.verify_reproducible(self._write(doc), export=fake_export)
        self.assertFalse(matches)
        self.assertTrue(diffs)

    def test_explicit_rtol_overrides_recorded(self):
        doc = self._doc(rtol=1e-12)  # recorded tolerance is very tight

        def fake_export(model_dir, output_path, sff_version=None, **kw):
            reexport = json.loads(json.dumps(doc))
            reexport["units"][0]["design_value"] = 1.0
            del reexport["metadata"]["reproducibility"]["comparison_rtol"]
            Path(output_path).write_text(json.dumps(reexport), encoding="utf-8")
            return Path(output_path)

        # Seed original with a slightly different numeric leaf.
        doc["units"][0]["design_value"] = 1.00005
        matches_loose, _ = V.verify_reproducible(
            self._write(doc), rtol=1e-3, export=fake_export)
        matches_tight, _ = V.verify_reproducible(
            self._write(doc), export=fake_export)  # uses recorded 1e-12
        self.assertTrue(matches_loose)
        self.assertFalse(matches_tight)


class TestEvaluateSffTagsRunHarnessShortCircuit(unittest.TestCase):
    """evaluate_sff_tags(run_harness=True) must short-circuit the heavy harness
    re-export when the static `reproducible` precondition already fails --
    calling verify_reproducible in that case would waste a real simulation on a
    doomed comparison. Uses a raising `export` stub to prove the harness path
    was never entered."""

    SCHEMA_PATH = (
        Path(__file__).resolve().parents[2] / "pisces_sff" / "schema" /
        "sff_schema.json")

    def _write(self, doc):
        d = tempfile.mkdtemp()
        p = Path(d) / "doc.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return str(p)

    def test_precondition_failure_skips_harness_and_export_is_never_called(self):
        # Recipe present (environment + load_script) but NO comparison_rtol ->
        # the static reproducible precondition fails regardless of overall
        # conformance/declared tags, per _reproducible_precondition. Omitting
        # `metadata.tags` entirely keeps this doc schema-clean (the precondition
        # check itself does not require tags to be declared).
        doc = {"metadata": {"reproducibility": {
                   "environment": {"filename": "environment.yaml",
                                   "content": "name: e\n"},
                   "load_script": {"filename": "load.py",
                                   "content": "def load():\n    pass\n"}}},
               "units": [{"id": "U1", "unit_type": "Mixer"}]}

        # Sanity-check the precondition actually fails before exercising the
        # short-circuit, so this test would fail loudly if the doc stopped
        # triggering the intended branch.
        static_verdict = V.evaluate_sff_tags(
            self._write(doc), str(self.SCHEMA_PATH))
        self.assertTrue(static_verdict["reproducible"]["blocking"])

        def raising_export(*args, **kwargs):
            raise AssertionError(
                "verify_reproducible/export must not run when the static "
                "reproducible precondition already fails")

        verdict = V.evaluate_sff_tags(
            self._write(doc), str(self.SCHEMA_PATH),
            run_harness=True, export=raising_export)
        self.assertFalse(verdict["reproducible"]["earned"])
        self.assertTrue(verdict["reproducible"]["blocking"])
