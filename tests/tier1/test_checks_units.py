# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: units checks (sff_checks.md UNIT-01..07 validator parts). Import-light:
# calls each _check_* directly on a synthetic _Context; no chemicals/thermosteam.

import importlib.util
import unittest
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_validate.py")


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_units_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = load_validate_module()


def ctx(**sections):
    return V._Context(sections)


def statuses(results):
    return {(r.check_id, r.severity): r.status for r in results}


class TestUnitId(unittest.TestCase):
    def test_unique_passes(self):
        c = ctx(units=[{"id": "A"}, {"id": "B"}])
        self.assertEqual(V._check_unit_id_uniqueness(c)[0].status, "pass")

    def test_duplicate_fails(self):
        c = ctx(units=[{"id": "A"}, {"id": "A"}])
        r = V._check_unit_id_uniqueness(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "error"))


class TestUtilityResultRefs(unittest.TestCase):
    def test_resolving_key_passes(self):
        c = ctx(units=[{"id": "U", "utility_consumption_results": {"steam": 1.0}}],
                utilities={"heat_utilities": [{"id": "steam"}]})
        self.assertEqual(V._check_utility_result_refs(c)[0].status, "pass")

    def test_dangling_key_fails(self):
        c = ctx(units=[{"id": "U", "utility_consumption_results": {"ghost": 1.0}}],
                utilities={"heat_utilities": []})
        r = V._check_utility_result_refs(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "error"))

    def test_no_results_skips(self):
        c = ctx(units=[{"id": "U"}], utilities={})
        self.assertEqual(V._check_utility_result_refs(c)[0].status, "skip")


class TestDesignResultPairing(unittest.TestCase):
    def test_paired_passes(self):
        c = ctx(units=[{"id": "U", "design_results": {"Area": 1.0},
                        "quantity_units_for_design_results": {"Area": "m2"}}])
        self.assertEqual(V._check_design_result_units_pairing(c)[0].status, "pass")

    def test_missing_unit_is_error(self):
        c = ctx(units=[{"id": "U", "design_results": {"Area": 1.0},
                        "quantity_units_for_design_results": {}}])
        sev = statuses(V._check_design_result_units_pairing(c))
        self.assertEqual(sev[("UNIT-03", "error")], "fail")

    def test_orphan_unit_is_warning(self):
        c = ctx(units=[{"id": "U", "design_results": {"Area": 1.0},
                        "quantity_units_for_design_results":
                            {"Area": "m2", "Ghost": "kg"}}])
        sev = statuses(V._check_design_result_units_pairing(c))
        self.assertEqual(sev[("UNIT-03", "warning")], "fail")


class TestReactantRefs(unittest.TestCase):
    def test_resolving_reactant_passes(self):
        c = ctx(units=[{"id": "U", "reactions": [{"reactant": "Glucose"}]}],
                chemicals=[{"id": "Glucose"}])
        self.assertEqual(V._check_reaction_reactant_refs(c)[0].status, "pass")

    def test_dangling_reactant_fails(self):
        c = ctx(units=[{"id": "U", "reactions": [{"reactant": "Nope"}]}],
                chemicals=[{"id": "Glucose"}])
        self.assertEqual(V._check_reaction_reactant_refs(c)[0].status, "fail")


class TestEquationStoichiometryConsistency(unittest.TestCase):
    def _chems(self):
        return [{"id": "Water", "index": 0}, {"id": "Starch", "index": 1},
                {"id": "Glucose", "index": 2}, {"id": "Ethanol", "index": 3},
                {"id": "CO2", "index": 4}]

    def test_agreeing_passes(self):
        # Mirrors corn's V310: 'Water + Starch -> Glucose'.
        c = ctx(units=[{"id": "V310", "reactions": [{
            "reactant": "Starch", "equation": "Water + Starch -> Glucose",
            "stoichiometry": {"Water": -1.0, "Starch": -1.0, "Glucose": 1.0}}]}],
            chemicals=self._chems())
        self.assertEqual(
            V._check_reaction_equation_stoichiometry_consistency(c)[0].status,
            "pass")

    def test_scaled_agreement_passes(self):
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose", "equation": "Glucose -> 2 Ethanol + 2 CO2",
            "stoichiometry": {"Glucose": -2.0, "Ethanol": 4.0, "CO2": 4.0}}]}],
            chemicals=self._chems())
        self.assertEqual(
            V._check_reaction_equation_stoichiometry_consistency(c)[0].status,
            "pass")

    def test_disagreement_fails(self):
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose", "equation": "Glucose -> 2 Ethanol + 2 CO2",
            "stoichiometry": {"Glucose": -1.0, "Ethanol": 1.0, "CO2": 2.0}}]}],
            chemicals=self._chems())
        self.assertEqual(
            V._check_reaction_equation_stoichiometry_consistency(c)[0].status,
            "fail")

    def test_only_one_representation_skips(self):
        c = ctx(units=[{"id": "U", "reactions": [
            {"reactant": "Glucose", "equation": "Glucose -> Yeast"}]}],
            chemicals=self._chems())
        self.assertEqual(
            V._check_reaction_equation_stoichiometry_consistency(c)[0].status,
            "skip")


class TestStoichiometryWellformed(unittest.TestCase):
    def test_dict_reactant_negative_passes(self):
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose",
            "stoichiometry": {"Glucose": -1.0, "Ethanol": 2.0}}]}],
            chemicals=[{"id": "Glucose", "index": 0}, {"id": "Ethanol", "index": 1}])
        self.assertEqual(V._check_stoichiometry_wellformed(c)[0].status, "pass")

    def test_reactant_nonnegative_fails(self):
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose",
            "stoichiometry": {"Glucose": 1.0, "Ethanol": 2.0}}]}],
            chemicals=[{"id": "Glucose", "index": 0}, {"id": "Ethanol", "index": 1}])
        self.assertEqual(V._check_stoichiometry_wellformed(c)[0].status, "fail")

    def test_array_wrong_length_fails(self):
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose", "stoichiometry": [-1.0]}]}],
            chemicals=[{"id": "Glucose", "index": 0}, {"id": "Ethanol", "index": 1}])
        self.assertEqual(V._check_stoichiometry_wellformed(c)[0].status, "fail")

    def test_unresolvable_key_fails(self):
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose", "stoichiometry": {"Ghost": -1.0}}]}],
            chemicals=[{"id": "Glucose", "index": 0}])
        self.assertEqual(V._check_stoichiometry_wellformed(c)[0].status, "fail")


class TestUnitConnectivity(unittest.TestCase):
    def test_connected_passes(self):
        c = ctx(units=[{"id": "U"}],
                streams=[{"id": "s", "source_unit_id": "None", "sink_unit_id": "U"}])
        self.assertEqual(V._check_unit_connectivity(c)[0].status, "pass")

    def test_orphan_is_warning(self):
        c = ctx(units=[{"id": "U"}, {"id": "LONELY"}],
                streams=[{"id": "s", "source_unit_id": "None", "sink_unit_id": "U"}])
        r = V._check_unit_connectivity(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))


if __name__ == "__main__":
    unittest.main()
