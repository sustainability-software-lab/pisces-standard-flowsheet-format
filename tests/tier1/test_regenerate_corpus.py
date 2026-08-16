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
        """iter_model_dirs() discovers a directory named "corn_dry_grind_ethanol"."""
        names = {d.name for d in self.m.iter_model_dirs()}
        self.assertIn("corn_dry_grind_ethanol", names)

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


if __name__ == "__main__":
    unittest.main()
