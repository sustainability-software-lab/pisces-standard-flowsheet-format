# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 2: the validator's heavy-import helpers and checks on REAL objects --
# molar-mass-from-formula (chemicals) and unit-string parseability
# (thermosteam.units_of_measure.ureg) -- plus the checks built on top of them:
# CHEM-03 (formula<->molar_mass agreement), QU-02 (quantity-unit-string
# parseability), STR-10 (mass<->molar consistency), and UTIL-03 (utility-result
# quantity-unit parseability). Consolidated from test_checks_chem03.py,
# test_checks_qu02.py, test_checks_str10.py, test_checks_util03.py, and
# test_validate_helpers.py (Task 2.4). Gated on RUN_TIER2 (default on); real
# objects require the Tier-1 biosteam/thermosteam stub to be evicted first.

import unittest

from tests._gating import RUN_TIER2
from tests._stub_eviction import RealBiosteamTestCase
from tests._validate_loader import V


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestFormulaMolarMass(RealBiosteamTestCase):
    def setUp(self):
        self.V = V

    def test_agreeing_passes(self):
        c = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": False,
             "formula": "H2O", "molar_mass": 18.01528}]})
        self.assertEqual(
            self.V._check_formula_molar_mass_agreement(c)[0].status, "pass")

    def test_disagreeing_is_warning(self):
        c = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": False,
             "formula": "H2O", "molar_mass": 99.0}]})
        r = self.V._check_formula_molar_mass_agreement(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_no_pairs_skips(self):
        c = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": False, "molar_mass": 18.0}]})
        self.assertEqual(
            self.V._check_formula_molar_mass_agreement(c)[0].status, "skip")


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestQuantityUnitStringsParseable(RealBiosteamTestCase):
    def setUp(self):
        self.V = V

    def test_parseable_registry_passes(self):
        c = self.V._Context({"quantity_units_global": {
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": "kg/hr"},
            "price": {"aliases": ["price"], "quantity_units": "USD/kg"}}})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "pass")

    def test_empty_design_unit_allowed(self):
        # '' is the documented dimensionless sentinel for design results.
        c = self.V._Context({"units": [{"id": "U",
            "quantity_units_for_design_results": {"Number of trays": ""}}]})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "pass")

    def test_empty_registry_unit_is_error(self):
        c = self.V._Context({"quantity_units_global": {
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": ""}}})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "fail")

    def test_gibberish_is_error(self):
        c = self.V._Context({"utilities": {"heat_utilities": [
            {"id": "s", "quantity_units_for_utility_results": "zorp!!"}]}})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "fail")

    def test_no_quantity_unit_strings_is_vacuous_pass(self):
        # QU-02 is "Skipped when: never" (sff_checks.md) -- zero
        # quantity-unit strings present is a vacuous pass, not a skip.
        c = self.V._Context({})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "pass")


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestMassMolarConsistency(RealBiosteamTestCase):
    def setUp(self):
        self.V = V

    def _ctx(self, mass, molar):
        # Single-component water phase: M-bar = 18.01528 g/mol.
        return self.V._Context({
            "chemicals": [{"id": "W", "included_in_thermo": False,
                           "molar_mass": 18.01528}],
            "streams": [{"id": "s", "stream_properties": {
                "total_mass_flow": mass, "total_molar_flow": molar,
                "phases": {"l": {"total_mass_flow": mass, "total_molar_flow": molar,
                                 "composition": [{"component_name": "W",
                                                  "mol_fraction": 1.0}]}}}}]})

    def test_consistent_passes(self):
        c = self._ctx(mass=18.01528, molar=1.0)
        self.assertEqual(
            self.V._check_mass_molar_flow_consistency(c)[0].status, "pass")

    def test_inconsistent_is_warning(self):
        c = self._ctx(mass=100.0, molar=1.0)
        r = self.V._check_mass_molar_flow_consistency(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_unresolvable_molar_mass_skips(self):
        c = self.V._Context({
            "chemicals": [{"id": "X", "included_in_thermo": True}],
            "streams": [{"id": "s", "stream_properties": {
                "total_mass_flow": 5.0, "total_molar_flow": 1.0,
                "phases": {"l": {"total_mass_flow": 5.0, "total_molar_flow": 1.0,
                                 "composition": [{"component_name": "X",
                                                  "mol_fraction": 1.0}]}}}}]})
        self.assertEqual(
            self.V._check_mass_molar_flow_consistency(c)[0].status, "skip")


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestUtilityResultUnitsParseable(RealBiosteamTestCase):
    def setUp(self):
        self.V = V

    def test_parseable_units_pass(self):
        c = self.V._Context({"utilities": {"power_utilities": [
            {"id": "grid", "quantity_units_for_utility_results": "kW"}]}})
        self.assertEqual(
            self.V._check_utility_result_units_parseable(c)[0].status, "pass")

    def test_empty_units_are_warning(self):
        c = self.V._Context({"utilities": {"power_utilities": [
            {"id": "grid", "quantity_units_for_utility_results": ""}]}})
        r = self.V._check_utility_result_units_parseable(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_gibberish_units_are_warning(self):
        c = self.V._Context({"utilities": {"heat_utilities": [
            {"id": "steam", "quantity_units_for_utility_results": "zorp!!"}]}})
        self.assertEqual(
            self.V._check_utility_result_units_parseable(c)[0].status, "fail")

    def test_no_utilities_is_vacuous_pass(self):
        # Skipped when: never (sff_checks.md) -- the field is schema-required
        # for each group, so an empty utilities registry is a genuine pass.
        c = self.V._Context({"utilities": {}})
        self.assertEqual(
            self.V._check_utility_result_units_parseable(c)[0].status, "pass")


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestMolarMass(RealBiosteamTestCase):
    def setUp(self):
        self.V = V

    def test_formula_parses(self):
        self.assertAlmostEqual(
            self.V._molar_mass_from_formula("H2O"), 18.01528, places=3)

    def test_bad_formula_returns_none(self):
        self.assertIsNone(self.V._molar_mass_from_formula("not a formula"))

    def test_context_prefers_declared_mass(self):
        ctx = self.V._Context({"chemicals": [
            {"id": "A", "included_in_thermo": False, "molar_mass": 42.0}]})
        self.assertEqual(ctx.molar_mass("A"), 42.0)

    def test_context_falls_back_to_formula(self):
        ctx = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": True, "formula": "H2O"}]})
        self.assertAlmostEqual(ctx.molar_mass("W"), 18.01528, places=3)

    def test_context_none_when_unresolvable(self):
        ctx = self.V._Context({"chemicals": [
            {"id": "X", "included_in_thermo": True}]})
        self.assertIsNone(ctx.molar_mass("X"))


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestUnitParseable(RealBiosteamTestCase):
    def setUp(self):
        self.V = V

    def test_sff_units_parse(self):
        for u in ("kg/hr", "kmol/hr", "kW", "kJ/hr", "USD/kg", "USD/kWh",
                  "USD/kmol", "USD/kJ", "m3/hr", "K", "Pa", "g/mol"):
            with self.subTest(unit=u):
                self.assertTrue(self.V._unit_is_parseable(u))

    def test_empty_string_is_parseable_dimensionless(self):
        self.assertTrue(self.V._unit_is_parseable(""))

    def test_gibberish_not_parseable(self):
        # 'furlongs per fortnight' are real pint units; use a truly-undefined token.
        self.assertFalse(self.V._unit_is_parseable("xyz!!"))


if __name__ == "__main__":
    unittest.main()
