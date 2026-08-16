# -*- coding: utf-8 -*-
# Tier 1: load_extended_metadata reads a model's extended_metadata.yaml into a
# dict of authored metadata, with no biosteam loaded. The real biosteam is
# stubbed (via tests._fakes) before pisces_sff is imported, because importing
# the package runs pisces_sff/__init__, which imports _export.

import importlib
import tempfile
import unittest
from pathlib import Path

from tests import _fakes

_fakes.install_biosteam_stubs()
_runner = importlib.import_module("pisces_sff._runner")
load_extended_metadata = _runner.load_extended_metadata


def _write(directory, text):
    (Path(directory) / "extended_metadata.yaml").write_text(text, encoding="utf-8")


class TestLoadExtendedMetadata(unittest.TestCase):
    def test_filename_constant(self):
        """EXTENDED_METADATA_FILENAME is the literal "extended_metadata.yaml"."""
        self.assertEqual(_runner.EXTENDED_METADATA_FILENAME,
                         "extended_metadata.yaml")

    def test_present_file_parses_to_dict(self):
        """A well-formed extended_metadata.yaml parses into a dict with its authored keys intact."""
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
        """A model directory with no extended_metadata.yaml -> UserWarning is raised and {} is returned."""
        with tempfile.TemporaryDirectory() as d:
            with self.assertWarns(UserWarning):
                got = load_extended_metadata(d)
        self.assertEqual(got, {})

    def test_empty_file_returns_empty_without_error(self):
        """An empty extended_metadata.yaml parses (no YAML content) to {} without raising."""
        with tempfile.TemporaryDirectory() as d:
            _write(d, "")
            got = load_extended_metadata(d)
        self.assertEqual(got, {})

    def test_malformed_yaml_raises_valueerror(self):
        """Unparseable YAML (an unterminated quoted string) raises ValueError."""
        with tempfile.TemporaryDirectory() as d:
            _write(d, 'source_doi: "unterminated\n')
            with self.assertRaises(ValueError):
                load_extended_metadata(d)

    def test_non_mapping_top_level_raises_valueerror(self):
        """A YAML document whose top level is a list (not a mapping) raises ValueError."""
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
        """A model dir with extended_metadata.yaml -> build_reproducibility embeds it as a filename/format/content/sha256 record."""
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
        """A model dir with no extended_metadata.yaml -> no "extended_metadata" key, other records unaffected."""
        with tempfile.TemporaryDirectory() as tmp:
            d = self._model_dir(tmp, with_extended=False)
            repro = _runner.build_reproducibility(d, _fixture_module())
        self.assertNotIn("extended_metadata", repro)
        # The existing records are unaffected.
        self.assertIn("environment", repro)
        self.assertIn("load_script", repro)


class TestLoadModelModule(unittest.TestCase):
    def test_imports_load_py_by_file_path(self):
        """load_model_module imports a model directory's load.py by file
        path (no packaging needed) and exposes its top-level names."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "load.py").write_text(
                "MARKER = 'hello'\ndef load():\n    return MARKER\n",
                encoding="utf-8")
            module = _runner.load_model_module(d)
        self.assertEqual(module.MARKER, "hello")
        self.assertEqual(module.load(), "hello")

    def test_missing_load_py_raises_file_not_found(self):
        """A model directory with no load.py raises FileNotFoundError."""
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                _runner.load_model_module(d)


class TestFileRecordHelper(unittest.TestCase):
    def test_embeds_format_filename_sha256_and_content(self):
        """_file_record reads a real file into an embedded record: format,
        filename, sha256 digest, and verbatim decoded content."""
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "environment.yaml"
            # write_bytes (not write_text): write_text's default newline
            # translation would turn '\n' into '\r\n' on Windows, and this
            # test asserts the exact bytes _file_record reads back.
            path.write_bytes(b"name: x\n")
            record = _runner._file_record(path, "conda-environment-yaml")
        self.assertEqual(record["format"], "conda-environment-yaml")
        self.assertEqual(record["filename"], "environment.yaml")
        self.assertEqual(record["content"], "name: x\n")
        self.assertEqual(record["sha256"], hashlib.sha256(b"name: x\n").hexdigest())

    def test_extra_fields_are_merged_in(self):
        """An `extra` mapping passed to _file_record is merged into the
        returned record."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "load.py"
            path.write_text("# x\n", encoding="utf-8")
            record = _runner._file_record(path, "python", {"entry_point": "load"})
        self.assertEqual(record["entry_point"], "load")


class TestInstalledVersionsHelper(unittest.TestCase):
    def test_returns_versions_keyed_only_by_tracked_package_names(self):
        """_installed_versions returns installed-version strings keyed only
        by names drawn from TRACKED_PACKAGES; numpy (a hard dependency of
        this environment) is always resolvable to a version string."""
        versions = _runner._installed_versions()
        self.assertIn("numpy", versions)
        self.assertIsInstance(versions["numpy"], str)
        self.assertTrue(set(versions) <= set(_runner.TRACKED_PACKAGES))


if __name__ == "__main__":
    unittest.main()
