# -*- coding: utf-8 -*-
# Tests the per-model recipe contract under pisces_sff/models/.
#
# The runner imports a model's load.py and reads module-level declarations off
# it (SIMULATOR selects the export entry point; SIMULATOR_PACKAGE and
# FLOWSHEET_MODEL_PACKAGE are resolved against the environment specification to
# build metadata.reproducibility). A model missing one of those declarations
# fails only at export time, minutes into a simulation -- these tests catch it
# in milliseconds instead, and they apply to every model directory, so a future
# model added by copy-paste is covered without editing this file.
#
# Design notes:
#   * load.py is inspected with `ast`, never imported: importing it would pull
#     in biosteam via `biorefineries`, which is the exact cost these Tier 1
#     tests exist to avoid.

import ast
import unittest
from pathlib import Path

MODELS_ROOT = Path(__file__).resolve().parents[2] / "pisces_sff" / "models"

REQUIRED_CONSTANTS = (
    "SIMULATOR",
    "SIMULATOR_PACKAGE",
    "FLOWSHEET_MODEL_PACKAGE",
    "MODEL_NAME",
    "EXPORT_KWARGS",
)


def model_dirs():
    """Every directory holding a load.py, at any depth under models/."""
    return sorted(p.parent for p in MODELS_ROOT.rglob("load.py"))


def module_constants(path):
    """Map name -> literal value for module-level assignments in a .py file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                try:
                    constants[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return constants


def module_functions(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}


class TestModelsTreeExists(unittest.TestCase):
    def test_at_least_one_model_is_present(self):
        """model_dirs() finds at least one model directory under pisces_sff/models/."""
        self.assertTrue(model_dirs(), f"no model directories found under {MODELS_ROOT}")

    def test_corn_model_is_present(self):
        """"M_BST_01" (the corn dry-grind ethanol recipe) is among the discovered model directory names."""
        names = {d.name for d in model_dirs()}
        self.assertIn("M_BST_01", names)

    def test_biosteam_models_are_grouped(self):
        """A "biosteam_models" directory exists under MODELS_ROOT."""
        # Simulator dispatch is by the SIMULATOR declaration, not by path, but
        # the tree is still grouped per simulator so a non-BioSTEAM model has an
        # obvious home.
        self.assertTrue((MODELS_ROOT / "biosteam_models").is_dir())


class TestModelRecipeContract(unittest.TestCase):
    def test_every_model_has_an_environment_spec(self):
        """Every discovered model directory contains an environment.yaml file."""
        for directory in model_dirs():
            with self.subTest(model=directory.name):
                self.assertTrue((directory / "environment.yaml").is_file())

    def test_every_model_declares_the_required_constants(self):
        """Every model's load.py declares all of REQUIRED_CONSTANTS at module level."""
        for directory in model_dirs():
            constants = module_constants(directory / "load.py")
            for name in REQUIRED_CONSTANTS:
                with self.subTest(model=directory.name, constant=name):
                    self.assertIn(name, constants)

    def test_export_kwargs_is_a_dict(self):
        """Every model's EXPORT_KWARGS constant is a dict."""
        for directory in model_dirs():
            with self.subTest(model=directory.name):
                constants = module_constants(directory / "load.py")
                self.assertIsInstance(constants["EXPORT_KWARGS"], dict)

    def test_model_name_matches_its_directory(self):
        """Every model's MODEL_NAME constant equals its containing directory's name."""
        for directory in model_dirs():
            with self.subTest(model=directory.name):
                constants = module_constants(directory / "load.py")
                self.assertEqual(constants["MODEL_NAME"], directory.name)

    def test_every_model_defines_load(self):
        """Every model's load.py defines a top-level function named "load"."""
        for directory in model_dirs():
            with self.subTest(model=directory.name):
                self.assertIn("load", module_functions(directory / "load.py"))

    def test_package_branches_is_a_dict_when_present(self):
        """Where a model declares PACKAGE_BRANCHES, it is a dict."""
        for directory in model_dirs():
            constants = module_constants(directory / "load.py")
            if "PACKAGE_BRANCHES" in constants:
                with self.subTest(model=directory.name):
                    self.assertIsInstance(constants["PACKAGE_BRANCHES"], dict)


class TestCornExtendedMetadata(unittest.TestCase):
    # The corn model is the flagship recipe and documents the extended_metadata
    # convention. (Missing files are allowed in general -- see load_extended_
    # metadata -- so this pins only corn, not every model.)
    def setUp(self):
        self.corn = (MODELS_ROOT / "biosteam_models" / "M_BST_01")

    def test_corn_ships_extended_metadata(self):
        """The M_BST_01 (corn dry-grind ethanol) model directory contains an extended_metadata.yaml file."""
        self.assertTrue((self.corn / "extended_metadata.yaml").is_file())

    def test_corn_export_kwargs_no_longer_carries_microorganisms(self):
        """corn's EXPORT_KWARGS has no "microorganisms" key and equals {"stoichiometry": "dict"}."""
        constants = module_constants(self.corn / "load.py")
        self.assertNotIn("microorganisms", constants["EXPORT_KWARGS"])
        self.assertEqual(constants["EXPORT_KWARGS"], {"stoichiometry": "dict"})

    def test_corn_extended_metadata_has_expected_keys(self):
        """corn's extended_metadata.yaml carries source_doi/process_title/flowsheet_designers/microorganisms, with the first microorganism named Saccharomyces cerevisiae."""
        import yaml
        data = yaml.safe_load(
            (self.corn / "extended_metadata.yaml").read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        for key in ("source_doi", "process_title", "flowsheet_designers",
                    "microorganisms"):
            self.assertIn(key, data)
        self.assertEqual(data["microorganisms"][0]["name"],
                         "Saccharomyces cerevisiae")


if __name__ == "__main__":
    unittest.main()
