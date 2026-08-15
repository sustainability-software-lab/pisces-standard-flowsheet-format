# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: chemicals checks CHEM-01 (id/index uniqueness), CHEM-04 (index
# coverage), CHEM-05 (unused). Import-light. CHEM-03 (formula<->mass) is Tier 2.

import importlib.util
import unittest
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_validate.py")


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_chem_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = load_validate_module()


def ctx(**sections):
    return V._Context(sections)


class TestChemIdIndexUniqueness(unittest.TestCase):
    def test_unique_passes(self):
        c = ctx(chemicals=[{"id": "A", "index": 0}, {"id": "B", "index": 1}])
        self.assertEqual(V._check_chemical_id_index_uniqueness(c)[0].status, "pass")

    def test_duplicate_id_fails(self):
        c = ctx(chemicals=[{"id": "A"}, {"id": "A"}])
        self.assertEqual(V._check_chemical_id_index_uniqueness(c)[0].status, "fail")

    def test_duplicate_index_fails(self):
        c = ctx(chemicals=[{"id": "A", "index": 0}, {"id": "B", "index": 0}])
        self.assertEqual(V._check_chemical_id_index_uniqueness(c)[0].status, "fail")


class TestIndexCoverage(unittest.TestCase):
    def test_id_keyed_stoichiometry_skips(self):
        # Corn's shape: stoichiometry keyed by chemical id -> not index-based.
        c = ctx(chemicals=[{"id": "A"}, {"id": "B"}],
                units=[{"id": "U", "reactions": [
                    {"reactant": "A", "stoichiometry": {"A": -1, "B": 1}}]}])
        self.assertEqual(V._check_index_coverage(c)[0].status, "skip")

    def test_array_stoichiometry_with_full_indices_passes(self):
        c = ctx(chemicals=[{"id": "A", "index": 0}, {"id": "B", "index": 1}],
                units=[{"id": "U", "reactions": [
                    {"reactant": "A", "stoichiometry": [-1, 1]}]}])
        self.assertEqual(V._check_index_coverage(c)[0].status, "pass")

    def test_array_stoichiometry_missing_index_fails(self):
        c = ctx(chemicals=[{"id": "A", "index": 0}, {"id": "B"}],
                units=[{"id": "U", "reactions": [
                    {"reactant": "A", "stoichiometry": [-1, 1]}]}])
        self.assertEqual(V._check_index_coverage(c)[0].status, "fail")


class TestUnusedChemicals(unittest.TestCase):
    def test_referenced_passes(self):
        c = ctx(chemicals=[{"id": "W"}], streams=[{"id": "s",
                "stream_properties": {"phases": {"l": {"total_molar_flow": 1.0,
                    "composition": [{"component_name": "W", "mol_fraction": 1.0}]}}}}])
        self.assertEqual(V._check_unused_chemicals(c)[0].status, "pass")

    def test_unreferenced_is_info(self):
        c = ctx(chemicals=[{"id": "W"}, {"id": "GHOST"}], streams=[{"id": "s",
                "stream_properties": {"phases": {"l": {"total_molar_flow": 1.0,
                    "composition": [{"component_name": "W", "mol_fraction": 1.0}]}}}}])
        r = V._check_unused_chemicals(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "info"))

    def test_empty_chemicals_is_vacuous_pass(self):
        # sff_checks.md CHEM-05: "Skipped when: never" -- an empty chemicals
        # registry is a vacuous pass, not a skip.
        c = ctx(chemicals=[])
        r = V._check_unused_chemicals(c)[0]
        self.assertEqual((r.status, r.severity), ("pass", "info"))


if __name__ == "__main__":
    unittest.main()
