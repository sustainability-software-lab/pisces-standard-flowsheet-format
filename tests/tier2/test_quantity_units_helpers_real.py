# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 2: quantity_units_for_design_results (pisces_sff/export/_quantity_units.py)
# against a REAL biosteam unit's design_results/_units. _quantity_units.py is
# deliberately import-light -- no biosteam/thermosteam imports of its own (see
# its module docstring) -- so this helper never binds a biosteam-derived
# module-level name the way _export.py's `bst`/`PowerUtility` do. The
# pisces_sff.* stub-eviction guard used in test_export_helpers_real.py and
# test_version_shape_guard.py is therefore not needed here: re-importing
# pisces_sff.export._quantity_units after a Tier-1 combined-process import would
# yield an identical module either way. RealBiosteamTestCase is still
# required, though -- build_small_system_and_tea() needs the REAL
# biosteam/thermosteam (not the Tier-1 stub) to build a real HXutility.
#
# Gated on RUN_TIER2 (default on); real objects require the Tier-1
# biosteam/thermosteam stub to be evicted first.

import unittest

from tests._gating import RUN_TIER2
from tests._real_objects import build_small_system_and_tea
from tests._stub_eviction import RealBiosteamTestCase


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestQuantityUnitsForDesignResultsWithRealUnit(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()   # evicts Tier-1 biosteam/thermosteam stubs

        from pisces_sff.export._quantity_units import quantity_units_for_design_results

        cls.quantity_units_for_design_results = staticmethod(
            quantity_units_for_design_results)
        cls.system, cls.H1, cls.tea = build_small_system_and_tea()

    def test_maps_known_design_result_keys_to_their_real_units(self):
        """quantity_units_for_design_results(H1) maps each of H1's real
        design_results keys to H1._units[key] when present, read straight
        from the real object rather than hardcoded independently. Expected
        for the fixture's HXutility: 'Area' -> 'ft^2', 'Overall heat
        transfer coefficient' -> 'kW/m^2/K', 'Operating pressure' -> 'psi',
        'Total tube length' -> 'ft'."""
        result = self.quantity_units_for_design_results(self.H1)
        self.assertEqual(result['Area'], self.H1._units['Area'])
        self.assertEqual(result['Area'], 'ft^2')
        self.assertEqual(
            result['Overall heat transfer coefficient'], 'kW/m^2/K')
        self.assertEqual(result['Operating pressure'], 'psi')
        self.assertEqual(result['Total tube length'], 'ft')

    def test_design_result_key_missing_from_units_maps_to_empty_string(self):
        """A design_results key with no matching _units entry maps to ''
        (dimensionless/unspecified) rather than raising or being omitted.
        Expected for the fixture: 'Fouling correction factor' is a real
        H1.design_results key that is genuinely absent from H1._units, so it
        maps to ''."""
        result = self.quantity_units_for_design_results(self.H1)
        self.assertIn('Fouling correction factor', self.H1.design_results)
        self.assertNotIn('Fouling correction factor', self.H1._units)
        self.assertEqual(result['Fouling correction factor'], '')

    def test_result_keys_match_design_results_keys_exactly(self):
        """The returned dict's key set equals H1.design_results.keys()
        exactly -- one quantity-unit entry per real design result, no more,
        no fewer."""
        result = self.quantity_units_for_design_results(self.H1)
        self.assertEqual(set(result), set(self.H1.design_results))


if __name__ == "__main__":
    unittest.main()
