# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: the model registry (pisces_sff/models/all_models.yaml) loader and,
# from Task 4 of the naming-convention plan on, the README generator. _registry
# holds no package-relative imports (yaml is imported lazily), so we load it by
# file path -- like test_regenerate_corpus.py -- keeping this tier import-light
# in the repo's sense (may import yaml, never biosteam).

import importlib.util
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "pisces_sff" / "_registry.py"
MODELS_ROOT = REPO_ROOT / "pisces_sff" / "models"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_registry_under_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALID_ENTRY = textwrap.dedent("""\
    models:
      M_BST_01:
        flowsheet: SF_BST_01
        simulator: biosteam
        model_dir: biosteam_models/M_BST_01
        flowsheet_file: bioindustrial_park/SF_BST_01.json
        title: Corn dry-grind ethanol
        description: A test description.
        source_corpus: Bioindustrial-Park
    """)


def make_tree(tmp, registry_yaml,
              models=("biosteam_models/M_BST_01",),
              flowsheets=("bioindustrial_park/SF_BST_01.json",)):
    """Build <tmp>/models/** and <tmp>/exported_flowsheets/** plus the registry
    file at <tmp>/models/all_models.yaml; return the registry path."""
    root = Path(tmp)
    models_root = root / "models"
    for rel in models:
        d = models_root / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "load.py").write_text("# stub\n", encoding="utf-8")
    fs_root = root / "exported_flowsheets"
    for rel in flowsheets:
        f = fs_root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}", encoding="utf-8")
    registry = models_root / "all_models.yaml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(registry_yaml, encoding="utf-8")
    return registry


class TestLoadModelRegistry(unittest.TestCase):
    def setUp(self):
        self.m = load_module()

    def test_valid_registry_loads(self):
        """load_model_registry on a well-formed temp tree -> dict keyed by
        model id, entry carries all seven required fields."""
        with tempfile.TemporaryDirectory() as tmp:
            registry = self.m.load_model_registry(make_tree(tmp, VALID_ENTRY))
            self.assertEqual(set(registry), {"M_BST_01"})
            entry = registry["M_BST_01"]
            for field in ("flowsheet", "simulator", "model_dir",
                          "flowsheet_file", "title", "description",
                          "source_corpus"):
                self.assertIn(field, entry)
            self.assertEqual(entry["flowsheet"], "SF_BST_01")

    def test_missing_file_raises(self):
        """load_model_registry on a nonexistent path -> ValueError naming the path."""
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "models" / "all_models.yaml"
            with self.assertRaises(ValueError) as ctx:
                self.m.load_model_registry(missing)
            self.assertIn("all_models.yaml", str(ctx.exception))

    def test_malformed_yaml_raises(self):
        """load_model_registry on invalid YAML -> ValueError (not a YAMLError leak)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = make_tree(tmp, "models: [unclosed")
            with self.assertRaises(ValueError):
                self.m.load_model_registry(path)

    def test_non_mapping_models_raises(self):
        """A registry whose top-level 'models' is not a mapping -> ValueError."""
        with tempfile.TemporaryDirectory() as tmp:
            path = make_tree(tmp, "models:\n")
            with self.assertRaises(ValueError):
                self.m.load_model_registry(path)

    def test_missing_required_field_raises(self):
        """An entry missing 'title' -> ValueError naming the missing field."""
        bad = VALID_ENTRY.replace("    title: Corn dry-grind ethanol\n", "")
        with tempfile.TemporaryDirectory() as tmp:
            path = make_tree(tmp, bad)
            with self.assertRaises(ValueError) as ctx:
                self.m.load_model_registry(path)
            self.assertIn("title", str(ctx.exception))

    def test_bad_model_id_pattern_raises(self):
        """A model id not matching ^M_[A-Z]+_\\d{2,}$ (lowercase sim code) -> ValueError."""
        bad = VALID_ENTRY.replace("M_BST_01:", "M_bst_01:")
        with tempfile.TemporaryDirectory() as tmp:
            path = make_tree(tmp, bad)
            with self.assertRaises(ValueError) as ctx:
                self.m.load_model_registry(path)
            self.assertIn("M_bst_01", str(ctx.exception))

    def test_bad_flowsheet_id_pattern_raises(self):
        """A flowsheet id not matching ^SF_[A-Z]+_\\d{2,}$ (single digit) -> ValueError."""
        bad = VALID_ENTRY.replace("flowsheet: SF_BST_01", "flowsheet: SF_BST_1")
        with tempfile.TemporaryDirectory() as tmp:
            path = make_tree(tmp, bad)
            with self.assertRaises(ValueError) as ctx:
                self.m.load_model_registry(path)
            self.assertIn("SF_BST_1", str(ctx.exception))

    def test_duplicate_flowsheet_id_raises(self):
        """Two models claiming the same flowsheet id -> ValueError naming both."""
        dup = VALID_ENTRY + (
            "  M_BST_02:\n"
            "    flowsheet: SF_BST_01\n"
            "    simulator: biosteam\n"
            "    model_dir: biosteam_models/M_BST_02\n"
            "    flowsheet_file: bioindustrial_park/SF_BST_01.json\n"
            "    title: Duplicate\n"
            "    description: Claims the same flowsheet.\n"
            "    source_corpus: Bioindustrial-Park\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = make_tree(tmp, dup,
                             models=("biosteam_models/M_BST_01",
                                     "biosteam_models/M_BST_02"))
            with self.assertRaises(ValueError) as ctx:
                self.m.load_model_registry(path)
            self.assertIn("SF_BST_01", str(ctx.exception))

    def test_duplicate_model_key_raises(self):
        """The same model id as a repeated YAML mapping key -> ValueError from
        _yaml_load_no_duplicates. Plain yaml.safe_load silently keeps the last
        occurrence, which would let a registry edit override an earlier entry
        unnoticed."""
        dup = VALID_ENTRY + (
            "  M_BST_01:\n"
            "    flowsheet: SF_BST_01\n"
            "    simulator: biosteam\n"
            "    model_dir: biosteam_models/M_BST_01\n"
            "    flowsheet_file: bioindustrial_park/SF_BST_01.json\n"
            "    title: Duplicate key\n"
            "    description: Repeats the M_BST_01 mapping key.\n"
            "    source_corpus: Bioindustrial-Park\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = make_tree(tmp, dup)
            with self.assertRaises(ValueError) as ctx:
                self.m.load_model_registry(path)
            self.assertIn("duplicate", str(ctx.exception).lower())
            self.assertIn("M_BST_01", str(ctx.exception))

    def test_dangling_model_dir_raises(self):
        """A model_dir with no load.py on disk -> ValueError naming the dir."""
        bad = VALID_ENTRY.replace("model_dir: biosteam_models/M_BST_01",
                                  "model_dir: biosteam_models/M_BST_99")
        with tempfile.TemporaryDirectory() as tmp:
            path = make_tree(tmp, bad)
            with self.assertRaises(ValueError) as ctx:
                self.m.load_model_registry(path)
            self.assertIn("M_BST_99", str(ctx.exception))

    def test_dangling_flowsheet_file_raises(self):
        """A flowsheet_file that does not exist on disk -> ValueError naming it."""
        bad = VALID_ENTRY.replace(
            "flowsheet_file: bioindustrial_park/SF_BST_01.json",
            "flowsheet_file: bioindustrial_park/SF_BST_99.json")
        with tempfile.TemporaryDirectory() as tmp:
            path = make_tree(tmp, bad)
            with self.assertRaises(ValueError) as ctx:
                self.m.load_model_registry(path)
            self.assertIn("SF_BST_99", str(ctx.exception))


class TestCommittedRegistry(unittest.TestCase):
    """Consistency guards over the real, committed all_models.yaml (spec 6.3)."""

    def setUp(self):
        self.m = load_module()

    def test_committed_registry_loads(self):
        """load_model_registry() with defaults succeeds on the committed file
        (validating every path it references exists on disk)."""
        registry = self.m.load_model_registry()
        self.assertTrue(registry)

    def test_corn_pairing(self):
        """The committed registry pairs M_BST_01 <-> SF_BST_01 with simulator
        'biosteam' -- the authoritative pairing is this entry, not the string
        convention."""
        registry = self.m.load_model_registry()
        entry = registry["M_BST_01"]
        self.assertEqual(entry["flowsheet"], "SF_BST_01")
        self.assertEqual(entry["simulator"], "biosteam")
        self.assertEqual(entry["model_dir"], "biosteam_models/M_BST_01")
        self.assertEqual(entry["flowsheet_file"],
                         "bioindustrial_park/SF_BST_01.json")

    def test_every_model_dir_on_disk_is_registered(self):
        """Every load.py directory under pisces_sff/models/ appears in the
        registry (mirror of regenerate_corpus's runtime hard error) -> expected:
        on-disk set is a subset of the registered set."""
        registry = self.m.load_model_registry()
        registered = {(MODELS_ROOT / e["model_dir"]).resolve()
                      for e in registry.values()}
        on_disk = {p.parent.resolve() for p in MODELS_ROOT.rglob("load.py")}
        self.assertTrue(
            on_disk <= registered,
            f"unregistered model dir(s): {sorted(on_disk - registered)} -- "
            f"register them in pisces_sff/models/all_models.yaml")


class TestReadmeGeneration(unittest.TestCase):
    def setUp(self):
        self.m = load_module()

    def test_render_is_deterministic_and_complete(self):
        """render_registry_readme twice on the committed registry -> identical
        strings containing the AUTO-GENERATED banner, the regeneration command,
        the hook activation lines, the git-log traceability note, and one table
        row per registry entry."""
        registry = self.m.load_model_registry()
        text = self.m.render_registry_readme(registry)
        self.assertEqual(text, self.m.render_registry_readme(registry))
        self.assertIn("AUTO-GENERATED", text)
        self.assertIn("python -m pisces_sff._registry", text)
        self.assertIn("git config core.hooksPath .githooks", text)
        self.assertIn("git log --follow", text)
        for model_id, entry in registry.items():
            self.assertIn(f"| {model_id} | {entry['flowsheet']} |", text)

    def test_write_writes_lf_identical_files(self):
        """write_registry_readmes to two temp paths -> both files byte-identical,
        LF-only line endings, trailing newline."""
        registry = self.m.load_model_registry()
        with tempfile.TemporaryDirectory() as tmp:
            paths = (Path(tmp) / "a.md", Path(tmp) / "b.md")
            written = self.m.write_registry_readmes(registry, paths=paths)
            self.assertEqual([Path(p) for p in written], list(paths))
            blobs = [p.read_bytes() for p in paths]
            self.assertEqual(blobs[0], blobs[1])
            self.assertNotIn(b"\r", blobs[0])
            self.assertTrue(blobs[0].endswith(b"\n"))

    def test_committed_readmes_are_in_sync(self):
        """Both committed READMEs byte-match a fresh render of the committed
        registry -> if this fails, run: python -m pisces_sff._registry"""
        expected = self.m.render_registry_readme(
            self.m.load_model_registry()).encode("utf-8")
        for path in self.m.README_PATHS:
            with self.subTest(readme=path.name, parent=path.parent.name):
                self.assertEqual(
                    path.read_bytes(), expected,
                    f"{path} is stale -- regenerate with: "
                    f"python -m pisces_sff._registry")

    def test_table_cells_survive_pipes_and_newlines(self):
        """A registry entry whose title carries a literal '|' and a YAML
        block-scalar newline, and whose description carries a '|', renders as
        exactly ONE single-line table row with exactly 7 column-boundary
        pipes (6 cells) -- the hostile characters are escaped/collapsed, not
        allowed to silently add columns or rows. render_registry_readme only
        reads the six displayed fields, so a hand-built entry suffices."""
        registry = {
            "M_BST_98": {
                "flowsheet": "SF_BST_98",
                "simulator": "biosteam",
                "model_dir": "biosteam_models/M_BST_98",
                "flowsheet_file": "bioindustrial_park/SF_BST_98.json",
                "title": "Corn | maize\nethanol",
                "description": "Uses a 60|40 split.",
                "source_corpus": "Bioindustrial-Park",
            },
        }
        text = self.m.render_registry_readme(registry)
        rows = [line for line in text.splitlines() if "M_BST_98" in line]
        self.assertEqual(len(rows), 1, "hostile cells must not add rows")
        row = rows[0]
        boundary_pipes = row.count("|") - row.count("\\|")
        self.assertEqual(
            boundary_pipes, 7,
            f"hostile cells must not add columns: {row!r}")
        self.assertIn("Corn \\| maize ethanol", row)
        self.assertIn("60\\|40", row)


if __name__ == "__main__":
    unittest.main()
