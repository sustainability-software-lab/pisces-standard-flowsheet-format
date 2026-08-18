# -*- coding: utf-8 -*-
# Tier 1: unit-test the corpus-regeneration ORCHESTRATION without simulating.
# _regenerate_corpus.py performs no biosteam import at module top (export_model
# is imported lazily, only when no `export` callable is injected), so we load it
# by file path -- like tests/tier1/test_exceptions.py loads exceptions.py -- and
# inject a fake export to assert discovery + the per-model output-path loop.
# The REAL harness path is exercised in Tier 3 (tests/tier3).

import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_regenerate_corpus.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_regenerate_corpus_under_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestIterModelDirs(unittest.TestCase):
    def setUp(self):
        self.m = load_module()

    def test_finds_the_corn_model(self):
        """iter_model_dirs() discovers a directory named "M_BST_01" (corn dry-grind ethanol)."""
        names = {d.name for d in self.m.iter_model_dirs()}
        self.assertIn("M_BST_01", names)

    def test_every_discovered_dir_has_a_load_script(self):
        """Every directory returned by iter_model_dirs() contains a load.py file."""
        for directory in self.m.iter_model_dirs():
            with self.subTest(model=directory.name):
                self.assertTrue((directory / "load.py").is_file())


class TestRegenerateCorpusLoop(unittest.TestCase):
    def setUp(self):
        self.m = load_module()

    def test_calls_export_once_per_model_and_names_outputs(self):
        """regenerate_corpus calls the injected export once per discovered model and writes one .json per model named by its stem."""
        calls = []

        def fake_export(model_dir, output_path, sff_version=None):
            calls.append((Path(model_dir), Path(output_path)))
            Path(output_path).write_text("{}", encoding="utf-8")
            return Path(output_path)

        model_names = {d.name for d in self.m.iter_model_dirs()}
        with tempfile.TemporaryDirectory() as tmp:
            written = self.m.regenerate_corpus(tmp, export=fake_export)
            self.assertEqual(len(written), len(model_names))
            self.assertEqual(len(calls), len(model_names))
            for path in written:
                self.assertEqual(path.suffix, ".json")
                self.assertIn(path.stem, model_names)
                self.assertTrue(path.is_file())

    def test_threads_explicit_sff_version_to_export(self):
        """regenerate_corpus(sff_version="0.0.7") passes that exact version to every export call."""
        # An explicit sff_version must reach the export callable unchanged, so a
        # caller (or `python -m ... --sff-version`) can target a chosen schema
        # version without editing any source pin.
        received = []

        def fake_export(model_dir, output_path, sff_version=None):
            received.append(sff_version)
            Path(output_path).write_text("{}", encoding="utf-8")
            return Path(output_path)

        with tempfile.TemporaryDirectory() as tmp:
            self.m.regenerate_corpus(tmp, export=fake_export, sff_version="0.0.7")

        self.assertTrue(received)
        self.assertTrue(all(v == "0.0.7" for v in received))

    def test_omitted_sff_version_defers_to_the_export_default(self):
        """regenerate_corpus called with no sff_version passes sff_version=None to every export call."""
        # With no version given, regenerate_corpus passes sff_version=None so the
        # export callable's own default applies -- for the real harness that
        # default is read_schema_version(), which is how the corpus auto-syncs to
        # the current schema without a manual pin.
        received = []

        def fake_export(model_dir, output_path, sff_version="SENTINEL"):
            received.append(sff_version)
            Path(output_path).write_text("{}", encoding="utf-8")
            return Path(output_path)

        with tempfile.TemporaryDirectory() as tmp:
            self.m.regenerate_corpus(tmp, export=fake_export)

        self.assertTrue(received)
        self.assertTrue(all(v is None for v in received))

    def test_stamp_reproducible_appends_tag_and_rtol(self):
        """regenerate_corpus(stamp_reproducible=True) with a passing verify ->
        each written file gains reproducible tag + comparison_rtol."""
        import json

        def fake_export(model_dir, output_path, sff_version=None, **kw):
            doc = {"metadata": {"sff_version": "0.1.3",
                                "tags": ["exported-from-simulator"],
                                "reproducibility": {"environment": {},
                                                    "load_script": {}}}}
            Path(output_path).write_text(json.dumps(doc), encoding="utf-8")
            return Path(output_path)

        with tempfile.TemporaryDirectory() as d:
            written = self.m.regenerate_corpus(
                d, export=fake_export, stamp_reproducible=True,
                comparison_rtol=1e-4, verify=lambda p, rtol=None: (True, []))
            for path in written:
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("reproducible", doc["metadata"]["tags"])
                self.assertEqual(
                    doc["metadata"]["reproducibility"]["comparison_rtol"], 1e-4)

    def test_stamp_reproducible_raises_and_does_not_write_on_failed_verify(self):
        """regenerate_corpus(stamp_reproducible=True) with a FAILING verify ->
        raises RuntimeError rather than stamping a false reproducible claim, and
        leaves the file's tags/comparison_rtol untouched."""
        import json

        def fake_export(model_dir, output_path, sff_version=None, **kw):
            doc = {"metadata": {"sff_version": "0.1.3",
                                "tags": ["exported-from-simulator"]}}
            Path(output_path).write_text(json.dumps(doc), encoding="utf-8")
            return Path(output_path)

        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                self.m.regenerate_corpus(
                    d, export=fake_export, stamp_reproducible=True,
                    comparison_rtol=1e-4,
                    verify=lambda p, rtol=None: (False, ["some/path: 1 != 2"]))
            # Whichever file was mid-stamp when the first failure raised must
            # not have gained the tag or comparison_rtol.
            for path in Path(d).glob("*.json"):
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("reproducible", doc["metadata"].get("tags", []))
                self.assertNotIn(
                    "comparison_rtol", doc["metadata"].get("reproducibility", {}))


class TestWriteJson(unittest.TestCase):
    """_write_json must match _export.py's _write_sff_json byte-for-byte (same
    json.dump call shape: indent=4, default ensure_ascii, no explicit encoding/
    newline handling, no trailing newline) -- see pisces_sff/_export.py's
    _write_sff_json (`json.dump(doc, f, indent=4)` inside `open(path, "w")`).
    A stamped corpus file must stay diff-clean against a freshly
    harness-exported one apart from the two stamped keys."""

    def setUp(self):
        self.m = load_module()

    def test_matches_write_sff_json_formatting(self):
        import json as _json

        doc = {"metadata": {"sff_version": "0.1.3", "tags": ["reproducible"]},
              "unicode_check": "→"}  # non-ASCII to probe ensure_ascii
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.json"
            self.m._write_json(out, doc)
            raw = out.read_bytes()

            expected_path = Path(d) / "expected.json"
            with open(expected_path, "w") as f:  # mirrors _write_sff_json exactly
                _json.dump(doc, f, indent=4)
            expected = expected_path.read_bytes()

        self.assertEqual(raw, expected)
        self.assertFalse(raw.endswith(b"\n"))          # no trailing newline
        self.assertNotIn(b"\xe2\x86\x92", raw)          # ensure_ascii escapes it
        self.assertIn(b"\\u2192", raw)                  # escaped as →

    def test_round_trips_the_document(self):
        import json as _json

        doc = {"a": 1, "b": [1, 2, 3], "metadata": {"tags": ["x"]}}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.json"
            self.m._write_json(out, doc)
            self.assertEqual(_json.loads(out.read_text(encoding="utf-8")), doc)


if __name__ == "__main__":
    unittest.main()
