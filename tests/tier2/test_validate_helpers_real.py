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
        """CHEM-03 — chemical formula H2O and declared molar_mass 18.01528 (real agreement) -> check status is pass."""
        c = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": False,
             "formula": "H2O", "molar_mass": 18.01528}]})
        self.assertEqual(
            self.V._check_formula_molar_mass_agreement(c)[0].status, "pass")

    def test_disagreeing_is_warning(self):
        """CHEM-03 — chemical formula H2O but declared molar_mass 99.0 (disagrees with the real formula mass) -> warning-severity fail."""
        c = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": False,
             "formula": "H2O", "molar_mass": 99.0}]})
        r = self.V._check_formula_molar_mass_agreement(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_no_pairs_skips(self):
        """CHEM-03 — chemical with molar_mass but no formula (no formula/molar_mass pair to compare) -> check status is skip."""
        c = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": False, "molar_mass": 18.0}]})
        self.assertEqual(
            self.V._check_formula_molar_mass_agreement(c)[0].status, "skip")


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestQuantityUnitStringsParseable(RealBiosteamTestCase):
    def setUp(self):
        self.V = V

    def test_parseable_registry_passes(self):
        """QU-02 — quantity_units_global entries "kg/hr" and "USD/kg" (both real pint-parseable) -> check status is pass."""
        c = self.V._Context({"quantity_units_global": {
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": "kg/hr"},
            "price": {"aliases": ["price"], "quantity_units": "USD/kg"}}})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "pass")

    def test_empty_design_unit_allowed(self):
        """QU-02 — a unit's design-result quantity_units value "" (documented dimensionless sentinel) -> check status is pass."""
        # '' is the documented dimensionless sentinel for design results.
        c = self.V._Context({"units": [{"id": "U",
            "quantity_units_for_design_results": {"Number of trays": ""}}]})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "pass")

    def test_empty_registry_unit_is_error(self):
        """QU-02 — quantity_units_global entry with quantity_units "" (not the design-result sentinel) -> check status is fail."""
        c = self.V._Context({"quantity_units_global": {
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": ""}}})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "fail")

    def test_gibberish_is_error(self):
        """QU-02 — heat_utility quantity_units_for_utility_results "zorp!!" (not real-pint-parseable) -> check status is fail."""
        c = self.V._Context({"utilities": {"heat_utilities": [
            {"id": "s", "quantity_units_for_utility_results": "zorp!!"}]}})
        self.assertEqual(
            self.V._check_quantity_unit_strings_parseable(c)[0].status, "fail")

    def test_no_quantity_unit_strings_is_vacuous_pass(self):
        """QU-02 — document with no quantity-unit strings at all -> check status is pass, not skip (QU-02 is "Skipped when: never")."""
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
        """STR-10 — single-water-phase stream with mass=18.01528, molar=1.0 (matching the real 18.01528 g/mol molar mass) -> check status is pass."""
        c = self._ctx(mass=18.01528, molar=1.0)
        self.assertEqual(
            self.V._check_mass_molar_flow_consistency(c)[0].status, "pass")

    def test_inconsistent_is_warning(self):
        """STR-10 — single-water-phase stream with mass=100.0, molar=1.0 (inconsistent with the real 18.01528 g/mol molar mass) -> warning-severity fail."""
        c = self._ctx(mass=100.0, molar=1.0)
        r = self.V._check_mass_molar_flow_consistency(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_unresolvable_molar_mass_skips(self):
        """STR-10 — chemical with no formula/molar_mass to resolve -> check status is skip."""
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
        """UTIL-03 — power_utility quantity_units_for_utility_results "kW" (real-pint-parseable) -> check status is pass."""
        c = self.V._Context({"utilities": {"power_utilities": [
            {"id": "grid", "quantity_units_for_utility_results": "kW"}]}})
        self.assertEqual(
            self.V._check_utility_result_units_parseable(c)[0].status, "pass")

    def test_empty_units_are_warning(self):
        """UTIL-03 — power_utility quantity_units_for_utility_results "" -> warning-severity fail."""
        c = self.V._Context({"utilities": {"power_utilities": [
            {"id": "grid", "quantity_units_for_utility_results": ""}]}})
        r = self.V._check_utility_result_units_parseable(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_gibberish_units_are_warning(self):
        """UTIL-03 — heat_utility quantity_units_for_utility_results "zorp!!" (not real-pint-parseable) -> check status is fail."""
        c = self.V._Context({"utilities": {"heat_utilities": [
            {"id": "steam", "quantity_units_for_utility_results": "zorp!!"}]}})
        self.assertEqual(
            self.V._check_utility_result_units_parseable(c)[0].status, "fail")

    def test_no_utilities_is_vacuous_pass(self):
        """UTIL-03 — empty utilities registry -> check status is pass, not skip ("Skipped when: never")."""
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
        """_molar_mass_from_formula("H2O") -> real molar mass ~18.01528 g/mol."""
        self.assertAlmostEqual(
            self.V._molar_mass_from_formula("H2O"), 18.01528, places=3)

    def test_bad_formula_returns_none(self):
        """_molar_mass_from_formula("not a formula") -> None (unparseable formula)."""
        self.assertIsNone(self.V._molar_mass_from_formula("not a formula"))

    def test_context_prefers_declared_mass(self):
        """_Context.molar_mass("A") with a declared molar_mass and no formula -> returns the declared value (42.0), not a derived one."""
        ctx = self.V._Context({"chemicals": [
            {"id": "A", "included_in_thermo": False, "molar_mass": 42.0}]})
        self.assertEqual(ctx.molar_mass("A"), 42.0)

    def test_context_falls_back_to_formula(self):
        """_Context.molar_mass("W") with no declared molar_mass but formula "H2O" -> falls back to the real formula-derived mass ~18.01528."""
        ctx = self.V._Context({"chemicals": [
            {"id": "W", "included_in_thermo": True, "formula": "H2O"}]})
        self.assertAlmostEqual(ctx.molar_mass("W"), 18.01528, places=3)

    def test_context_none_when_unresolvable(self):
        """_Context.molar_mass("X") with neither declared molar_mass nor formula -> None."""
        ctx = self.V._Context({"chemicals": [
            {"id": "X", "included_in_thermo": True}]})
        self.assertIsNone(ctx.molar_mass("X"))


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestUnitParseable(RealBiosteamTestCase):
    def setUp(self):
        self.V = V

    def test_sff_units_parse(self):
        """_unit_is_parseable(u) -> True for every SFF-used unit string (kg/hr, kmol/hr, kW, kJ/hr, USD/kg, USD/kWh, USD/kmol, USD/kJ, m3/hr, K, Pa, g/mol), each real-pint-parseable."""
        for u in ("kg/hr", "kmol/hr", "kW", "kJ/hr", "USD/kg", "USD/kWh",
                  "USD/kmol", "USD/kJ", "m3/hr", "K", "Pa", "g/mol"):
            with self.subTest(unit=u):
                self.assertTrue(self.V._unit_is_parseable(u))

    def test_empty_string_is_parseable_dimensionless(self):
        """_unit_is_parseable("") -> True (the empty string is the documented dimensionless sentinel)."""
        self.assertTrue(self.V._unit_is_parseable(""))

    def test_gibberish_not_parseable(self):
        """_unit_is_parseable("xyz!!") -> False (not a real pint-registered unit)."""
        # 'furlongs per fortnight' are real pint units; use a truly-undefined token.
        self.assertFalse(self.V._unit_is_parseable("xyz!!"))


if __name__ == "__main__":
    unittest.main()
