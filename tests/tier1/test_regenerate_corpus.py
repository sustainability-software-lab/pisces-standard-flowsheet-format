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
    Path(__file__).resolve().parents[2] / "pisces_sff" / "export" / "_regenerate_corpus.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_regenerate_corpus_under_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def corn_registry():
    """Minimal fake registry matching the committed corn recipe. Only the two
    fields regenerate_corpus reads (model_dir, flowsheet) are required --
    full-field validation is load_model_registry's job, tested in
    test_registry.py."""
    return {
        "M_BST_01": {
            "flowsheet": "SF_BST_01",
            "model_dir": "biosteam_models/M_BST_01",
        },
    }


def _make_model_stubs(models_root, registry):
    """Create a load.py stub for each registry entry's model_dir under
    models_root, so regenerate_corpus's unregistered-dir scan sees exactly
    the registered recipes (isolates the test from the real pisces_sff/export/models/
    tree)."""
    for entry in registry.values():
        d = Path(models_root) / entry["model_dir"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "load.py").write_text("# stub\n", encoding="utf-8")


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

    def test_calls_export_once_per_entry_and_names_outputs_by_flowsheet_id(self):
        """regenerate_corpus calls the injected export once per registry entry,
        resolving the model dir from entry.model_dir and naming the output
        <flowsheet>.json (SF_BST_01.json for corn) flat in output_dir."""
        calls = []

        def fake_export(model_dir, output_path, sff_version=None):
            calls.append((Path(model_dir), Path(output_path)))
            Path(output_path).write_text("{}", encoding="utf-8")
            return Path(output_path)

        reg = corn_registry()
        with tempfile.TemporaryDirectory() as tmp, \
             tempfile.TemporaryDirectory() as mroot:
            _make_model_stubs(mroot, reg)
            written = self.m.regenerate_corpus(tmp, models_root=mroot,
                                               export=fake_export, registry=reg)
            self.assertEqual([p.name for p in written], ["SF_BST_01.json"])
            self.assertEqual(len(calls), 1)
            model_dir, out = calls[0]
            self.assertEqual(
                model_dir,
                Path(mroot) / "biosteam_models" / "M_BST_01")
            self.assertEqual(out.parent, Path(tmp))
            self.assertTrue(out.is_file())

    def test_unregistered_model_dir_raises_before_exporting(self):
        """A load.py directory on disk that is absent from the registry ->
        ValueError naming the dir; the injected export is never called."""
        calls = []

        def fake_export(model_dir, output_path, sff_version=None):
            calls.append(model_dir)

        with tempfile.TemporaryDirectory() as models_root, \
             tempfile.TemporaryDirectory() as out:
            rogue = Path(models_root) / "biosteam_models" / "M_BST_99"
            rogue.mkdir(parents=True)
            (rogue / "load.py").write_text("# stub\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                self.m.regenerate_corpus(out, models_root=models_root,
                                         export=fake_export, registry={})
            self.assertIn("M_BST_99", str(ctx.exception))
            self.assertEqual(calls, [])

    def test_entries_export_in_sorted_model_id_order(self):
        """Two registered fake models -> exports run in sorted-model-id order
        and outputs are named by each entry's flowsheet id."""
        order = []

        def fake_export(model_dir, output_path, sff_version=None):
            order.append(Path(output_path).name)
            Path(output_path).write_text("{}", encoding="utf-8")
            return Path(output_path)

        registry = {
            "M_BST_02": {"flowsheet": "SF_BST_02",
                         "model_dir": "biosteam_models/M_BST_02"},
            "M_BST_01": {"flowsheet": "SF_BST_01",
                         "model_dir": "biosteam_models/M_BST_01"},
        }
        with tempfile.TemporaryDirectory() as models_root, \
             tempfile.TemporaryDirectory() as out:
            for entry in registry.values():
                d = Path(models_root) / entry["model_dir"]
                d.mkdir(parents=True)
                (d / "load.py").write_text("# stub\n", encoding="utf-8")
            self.m.regenerate_corpus(out, models_root=models_root,
                                     export=fake_export, registry=registry)
        self.assertEqual(order, ["SF_BST_01.json", "SF_BST_02.json"])

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

        reg = corn_registry()
        with tempfile.TemporaryDirectory() as tmp, \
             tempfile.TemporaryDirectory() as mroot:
            _make_model_stubs(mroot, reg)
            self.m.regenerate_corpus(tmp, models_root=mroot, export=fake_export,
                                     sff_version="0.0.7", registry=reg)

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

        reg = corn_registry()
        with tempfile.TemporaryDirectory() as tmp, \
             tempfile.TemporaryDirectory() as mroot:
            _make_model_stubs(mroot, reg)
            self.m.regenerate_corpus(tmp, models_root=mroot, export=fake_export,
                                     registry=reg)

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

        reg = corn_registry()
        with tempfile.TemporaryDirectory() as d, \
             tempfile.TemporaryDirectory() as mroot:
            _make_model_stubs(mroot, reg)
            written = self.m.regenerate_corpus(
                d, models_root=mroot, export=fake_export, stamp_reproducible=True,
                comparison_rtol=1e-4, verify=lambda p, rtol=None: (True, []),
                registry=reg)
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

        reg = corn_registry()
        with tempfile.TemporaryDirectory() as d, \
             tempfile.TemporaryDirectory() as mroot:
            _make_model_stubs(mroot, reg)
            with self.assertRaises(RuntimeError):
                self.m.regenerate_corpus(
                    d, models_root=mroot, export=fake_export,
                    stamp_reproducible=True, comparison_rtol=1e-4,
                    verify=lambda p, rtol=None: (False, ["some/path: 1 != 2"]),
                    registry=reg)
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
    newline handling, no trailing newline) -- see pisces_sff/export/_export.py's
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


class TestMainCli(unittest.TestCase):
    def setUp(self):
        self.m = load_module()

    def test_cli_defaults_leave_stamping_off(self):
        """main([]) -> regenerate called with stamp_reproducible=False and
        comparison_rtol=1e-4 (ordinary regen stays single-simulation)."""
        received = {}

        def fake_regenerate(output_dir, sff_version=None,
                            stamp_reproducible=False, comparison_rtol=1e-4):
            received.update(stamp_reproducible=stamp_reproducible,
                            comparison_rtol=comparison_rtol)
            return []

        rc = self.m.main([], _regenerate=fake_regenerate)
        self.assertEqual(rc, 0)
        self.assertFalse(received["stamp_reproducible"])
        self.assertEqual(received["comparison_rtol"], 1e-4)

    def test_cli_threads_stamp_flags(self):
        """main(['--stamp-reproducible', '--comparison-rtol', '1e-05']) ->
        regenerate called with stamp_reproducible=True, comparison_rtol=1e-5,
        output_dir=CORPUS_DIR."""
        received = {}

        def fake_regenerate(output_dir, sff_version=None,
                            stamp_reproducible=False, comparison_rtol=1e-4):
            received.update(output_dir=Path(output_dir),
                            stamp_reproducible=stamp_reproducible,
                            comparison_rtol=comparison_rtol)
            return []

        rc = self.m.main(["--stamp-reproducible", "--comparison-rtol", "1e-05"],
                         _regenerate=fake_regenerate)
        self.assertEqual(rc, 0)
        self.assertTrue(received["stamp_reproducible"])
        self.assertEqual(received["comparison_rtol"], 1e-5)
        self.assertEqual(received["output_dir"], self.m.CORPUS_DIR)


if __name__ == "__main__":
    unittest.main()
