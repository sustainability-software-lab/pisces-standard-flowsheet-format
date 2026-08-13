# -*- coding: utf-8 -*-
# Tier 1: load_extended_metadata reads a model's extended_metadata.yaml into a
# dict of authored metadata, with no biosteam loaded. The real biosteam is
# stubbed (via _export_stub) before pisces_sff is imported, because importing
# the package runs pisces_sff/__init__, which imports _export.

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _export_stub  # noqa: E402

_export_stub.install_biosteam_stubs()
_runner = importlib.import_module("pisces_sff._runner")
load_extended_metadata = _runner.load_extended_metadata


def _write(directory, text):
    (Path(directory) / "extended_metadata.yaml").write_text(text, encoding="utf-8")


class TestLoadExtendedMetadata(unittest.TestCase):
    def test_filename_constant(self):
        self.assertEqual(_runner.EXTENDED_METADATA_FILENAME,
                         "extended_metadata.yaml")

    def test_present_file_parses_to_dict(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d,
                   'source_doi: "10.1234/x"\n'
                   'process_title: "T"\n'
                   'flowsheet_designers: "A; B"\n'
                   'microorganisms:\n'
                   '  - name: "Saccharomyces cerevisiae"\n'
                   '    label: "ethanologen"\n')
            got = load_extended_metadata(d)
        self.assertEqual(got["source_doi"], "10.1234/x")
        self.assertEqual(got["process_title"], "T")
        self.assertEqual(got["flowsheet_designers"], "A; B")
        self.assertEqual(got["microorganisms"][0]["name"],
                         "Saccharomyces cerevisiae")

    def test_missing_file_warns_and_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertWarns(UserWarning):
                got = load_extended_metadata(d)
        self.assertEqual(got, {})

    def test_empty_file_returns_empty_without_error(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "")
            got = load_extended_metadata(d)
        self.assertEqual(got, {})

    def test_malformed_yaml_raises_valueerror(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, 'source_doi: "unterminated\n')
            with self.assertRaises(ValueError):
                load_extended_metadata(d)

    def test_non_mapping_top_level_raises_valueerror(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "- just\n- a\n- list\n")
            with self.assertRaises(ValueError):
                load_extended_metadata(d)


import types


def _fixture_module():
    return types.SimpleNamespace(
        SIMULATOR_PACKAGE="biosteam",
        FLOWSHEET_MODEL_PACKAGE="biorefineries",
        PACKAGE_BRANCHES={},
    )


ENV_YML = (
    "name: fixture\n"
    "dependencies:\n"
    "  - pip:\n"
    "    - biosteam==2.46.1\n"
    "    - biorefineries==0.0.0\n"
)


class TestBuildReproducibilityEmbedsExtendedMetadata(unittest.TestCase):
    def _model_dir(self, tmp, with_extended):
        d = Path(tmp)
        (d / "environment.yaml").write_text(ENV_YML, encoding="utf-8")
        (d / "load.py").write_text("# fixture load.py\n", encoding="utf-8")
        if with_extended:
            _write(d, 'process_title: "Fixture"\n')
        return d

    def test_record_present_when_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._model_dir(tmp, with_extended=True)
            repro = _runner.build_reproducibility(d, _fixture_module())
        self.assertIn("extended_metadata", repro)
        rec = repro["extended_metadata"]
        self.assertEqual(rec["filename"], "extended_metadata.yaml")
        self.assertEqual(rec["format"], "yaml")
        self.assertIn("Fixture", rec["content"])
        self.assertEqual(len(rec["sha256"]), 64)

    def test_record_absent_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._model_dir(tmp, with_extended=False)
            repro = _runner.build_reproducibility(d, _fixture_module())
        self.assertNotIn("extended_metadata", repro)
        # The existing records are unaffected.
        self.assertIn("environment", repro)
        self.assertIn("load_script", repro)


if __name__ == "__main__":
    unittest.main()
