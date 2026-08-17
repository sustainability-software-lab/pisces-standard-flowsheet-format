# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 2 walking skeleton: re-verify, against a REAL biosteam Stream, the
# is_product behavior that Task 2 tested with a fake object in Tier 1. This is
# the Tier-1 rule in action: a fake-object assertion earns a real-object check
# in a higher tier. Consolidated from test_helpers_real_objects.py (Task 2.4).
#
# Gated on RUN_TIER2 (default on; imports biosteam, runs a small simulation).

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests._gating import RUN_TIER2
from tests._real_objects import build_small_system_and_tea
from tests._stub_eviction import RealBiosteamTestCase

# Independent ground truth for the fixture's feed composition, used to pin
# get_composition's fractions without re-deriving them from the built
# Stream's own imol/imass -- a bug there would otherwise cancel out against
# an assertion built the same way. Values are the fixture's known feed mass
# flows (Water=1000, Ethanol=100 kg/hr, see build_small_system_and_tea) and
# each chemical's real molar mass in kg/kmol (thermosteam.Chemical('Water').MW
# == 18.01528, thermosteam.Chemical('Ethanol').MW == 46.06844, confirmed
# 2026-08-16).
_FEED_WATER_MASS_FLOW = 1000.0    # kg/hr
_FEED_ETHANOL_MASS_FLOW = 100.0   # kg/hr
_WATER_MW = 18.01528     # kg/kmol
_ETHANOL_MW = 46.06844   # kg/kmol

_WATER_MASS_FRACTION = (
    _FEED_WATER_MASS_FLOW
    / (_FEED_WATER_MASS_FLOW + _FEED_ETHANOL_MASS_FLOW))
_ETHANOL_MASS_FRACTION = (
    _FEED_ETHANOL_MASS_FLOW
    / (_FEED_WATER_MASS_FLOW + _FEED_ETHANOL_MASS_FLOW))

_WATER_MOLAR_FLOW = _FEED_WATER_MASS_FLOW / _WATER_MW
_ETHANOL_MOLAR_FLOW = _FEED_ETHANOL_MASS_FLOW / _ETHANOL_MW
_TOTAL_MOLAR_FLOW = _WATER_MOLAR_FLOW + _ETHANOL_MOLAR_FLOW
_WATER_MOL_FRACTION = _WATER_MOLAR_FLOW / _TOTAL_MOLAR_FLOW
_ETHANOL_MOL_FRACTION = _ETHANOL_MOLAR_FLOW / _TOTAL_MOLAR_FLOW


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestIsProductWithRealStream(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()   # evicts Tier-1 stubs (RealBiosteamTestCase)

        from pisces_sff import _export

        cls._export = _export
        cls.system, cls.H1, cls.tea = build_small_system_and_tea()
        cls.products = list(cls.system.products)
        cls.hot = cls.H1.outs[0]

    def test_priced_outlet_is_a_product(self):
        """is_product(hot, products) with hot.price=1.0 on a real Stream -> True (hot is in system.products)."""
        self.hot.price = 1.0
        self.assertIn(self.hot, self.products)
        self.assertTrue(self._export.is_product(self.hot, self.products))

    def test_zero_priced_outlet_is_not_a_product(self):
        """is_product(hot, products) with hot.price=0.0 on a real Stream -> False."""
        self.hot.price = 0.0
        try:
            self.assertFalse(self._export.is_product(self.hot, self.products))
        finally:
            self.hot.price = 1.0  # restore for other classes sharing the cache


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestExportHelpersAgainstRealObjects(RealBiosteamTestCase):
    """Tier 2 coverage for the remaining _export.py helpers that read a real
    biosteam/thermosteam object: get_composition, get_phase_properties,
    get_utility_results, get_stream_roles, get_unit_type,
    get_design_simulation_method, get_design_input_specs, is_feedstock, and
    get_thermo. is_product is already covered above by
    TestIsProductWithRealStream and is deliberately not duplicated here."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()   # evicts Tier-1 biosteam/thermosteam stubs

        # get_utility_results reads the module-level `PowerUtility` name that
        # _export.py binds via `from biosteam import PowerUtility, System` at
        # import time -- the same class of hazard as the module-level `bst`
        # name documented in test_version_shape_guard.py. If
        # pisces_sff._export was already imported earlier in this process
        # while the Tier-1 fake biosteam stub was installed (e.g. Tier 1
        # collected first in a combined `pytest tests` run), that name stays
        # permanently bound to the fake even after
        # RealBiosteamTestCase.setUpClass() evicts the fake from
        # sys.modules['biosteam'] -- Python only re-resolves a module-level
        # import on a fresh import, and 'pisces_sff._export' is already
        # cached. Discard the whole pisces_sff package tree so the import
        # below re-executes against the (now real, just-evicted)
        # biosteam/thermosteam; sys.modules['biosteam']/['thermosteam']
        # themselves are left untouched, so this does not force a second real
        # import of the simulator itself. Applied to this whole class
        # defensively -- most of its target helpers never touch a
        # biosteam-bound module name directly, but it is harmless when
        # unneeded and required for get_utility_results.
        for key in [k for k in sys.modules
                    if k == "pisces_sff" or k.startswith("pisces_sff.")]:
            del sys.modules[key]

        from pisces_sff import _export

        cls._export = _export
        cls.system, cls.H1, cls.tea = build_small_system_and_tea()
        cls.feed = cls.H1.ins[0]
        cls.hot = cls.H1.outs[0]

    def test_get_composition_mol_percent_matches_real_stream_mol_fractions(self):
        """get_composition(feed, units='mol%') returns one entry per
        (phase, chemical) pair present in the real Stream, each carrying only
        'mol_fraction' (no 'mass_fraction'). Expected for the fixture's
        single liquid-phase feed (Water=1000, Ethanol=100 kg/hr): both
        entries have phase 'l', and mol_fraction matches the value
        independently derived from the known feed mass flows and each
        chemical's real molar mass (Water ~0.9623662167478366, Ethanol
        ~0.03763378325216345), asserted to 1e-9."""
        comp = self._export.get_composition(self.feed, units='mol%')
        by_name = {c['component_name']: c for c in comp}
        self.assertEqual(set(by_name), {'Water', 'Ethanol'})
        for c in comp:
            self.assertEqual(c['phase'], 'l')
            self.assertNotIn('mass_fraction', c)
        self.assertAlmostEqual(
            by_name['Water']['mol_fraction'], _WATER_MOL_FRACTION, places=9)
        self.assertAlmostEqual(
            by_name['Ethanol']['mol_fraction'], _ETHANOL_MOL_FRACTION,
            places=9)

    def test_get_composition_both_matches_real_stream_mass_fractions(self):
        """get_composition(feed, units='both') (the exporter's default) adds
        'mass_fraction' alongside 'mol_fraction'. Expected: mass_fraction
        matches the value independently derived from the known feed mass
        flows alone (Water ~0.9090909090909091 = 1000/1100 kg/hr, Ethanol
        ~0.09090909090909091 = 100/1100 kg/hr), asserted to 1e-9, and
        'mol_fraction' is still present on each entry."""
        comp = self._export.get_composition(self.feed, units='both')
        by_name = {c['component_name']: c for c in comp}
        for name in ('Water', 'Ethanol'):
            self.assertIn('mol_fraction', by_name[name])
        self.assertAlmostEqual(
            by_name['Water']['mass_fraction'], _WATER_MASS_FRACTION,
            places=9)
        self.assertAlmostEqual(
            by_name['Ethanol']['mass_fraction'], _ETHANOL_MASS_FRACTION,
            places=9)

    def test_get_phase_properties_matches_real_stream_totals(self):
        """get_phase_properties(feed, inline=False) returns a dict keyed by
        phase symbol -- {'l'} for this single-liquid-phase fixture -- whose
        total_mass_flow/total_molar_flow/total_volumetric_flow are bare
        numbers equal to feed.F_mass/feed.F_mol/feed.F_vol (1100.0 kg/hr,
        ~57.679 kmol/hr, ~1.130 m3/hr), and whose composition list carries
        both components by name."""
        phases = self._export.get_phase_properties(self.feed, inline=False)
        self.assertEqual(set(phases), {'l'})
        phase = phases['l']
        self.assertEqual(phase['total_mass_flow'], self.feed.F_mass)
        self.assertEqual(phase['total_molar_flow'], self.feed.F_mol)
        self.assertEqual(phase['total_volumetric_flow'], self.feed.F_vol)
        self.assertEqual(phase['total_mass_flow'], 1100.0)
        names = {c['component_name'] for c in phase['composition']}
        self.assertEqual(names, {'Water', 'Ethanol'})

    def test_get_utility_results_matches_real_heat_utility(self):
        """get_utility_results(H1) returns (u_cons, u_prod, hu_agents,
        pu_agents, ou_agents) reflecting H1's single positive-duty heat
        utility (heating feed from 298.15 K to 350 K, real duty
        ~242869.9 kJ/hr): u_cons == {agent.ID: agent.duty} for that one
        agent (positive duty => consumption), u_prod == {} (no
        negative-duty agent), hu_agents == {that one real UtilityAgent},
        pu_agents == {the real biosteam.PowerUtility class} (produced
        unconditionally by get_utility_results regardless of whether H1 has
        a power duty -- this is the module-level name the class's
        pisces_sff.* eviction in setUpClass exists to protect, so it must be
        asserted against the real class, not merely unpacked and discarded),
        and ou_agents == set() (H1 has no natural_gas utility)."""
        u_cons, u_prod, hu_agents, pu_agents, ou_agents = \
            self._export.get_utility_results(self.H1)
        [hu] = self.H1.heat_utilities
        self.assertGreater(hu.duty, 0.0)
        self.assertEqual(u_cons, {hu.agent.ID: hu.duty})
        self.assertEqual(u_prod, {})
        self.assertEqual(hu_agents, {hu.agent})
        self.assertEqual(pu_agents, {self._export.PowerUtility})
        self.assertEqual(ou_agents, set())

    def test_get_stream_roles_matches_real_topology_and_pricing(self):
        """get_stream_roles classifies the fixture's two real streams from
        their actual source/sink/price against system.feeds/system.products:
        feed (sink=H1, no source, price=0.5>0, the system's sole/
        highest-carbon feed) -> ['input', 'purchased_raw_material',
        'feedstock']; hot (source=H1, no sink, price=1.0>0, in
        system.products) -> ['output', 'product']."""
        feed_roles = self._export.get_stream_roles(
            self.feed, self.system.feeds, self.system.products)
        hot_roles = self._export.get_stream_roles(
            self.hot, self.system.feeds, self.system.products)
        self.assertEqual(
            feed_roles, ['input', 'purchased_raw_material', 'feedstock'])
        self.assertEqual(hot_roles, ['output', 'product'])

    def test_get_unit_type_matches_real_unit_line(self):
        """get_unit_type(H1) returns H1.line verbatim -- 'Heat exchanger'
        for a real biosteam.HXutility instance."""
        self.assertEqual(self._export.get_unit_type(self.H1), self.H1.line)
        self.assertEqual(self._export.get_unit_type(self.H1), 'Heat exchanger')

    def test_get_design_simulation_method_matches_real_class_path(self):
        """get_design_simulation_method(H1) returns
        '<classname> on <github link>' derived from H1's real __class__
        module path. Expected: 'HXutility on
        https://github.com/BioSTEAMDevelopmentGroup/biosteam/blob/master/biosteam/units/heat_exchange.py'."""
        result = self._export.get_design_simulation_method(self.H1)
        self.assertEqual(
            result,
            'HXutility on https://github.com/BioSTEAMDevelopmentGroup/'
            'biosteam/blob/master/biosteam/units/heat_exchange.py')

    def test_get_design_input_specs_matches_real_unit_attributes(self):
        """get_design_input_specs(H1) reads whichever of its recognized
        parameter names H1 actually has. Expected {'T': 350, 'V': None} --
        350 is the fixture's set outlet temperature (a T-specified
        HXutility), V is unset -- matching H1.T/H1.V directly."""
        specs = self._export.get_design_input_specs(self.H1)
        self.assertEqual(specs, {'T': self.H1.T, 'V': self.H1.V})
        self.assertEqual(specs, {'T': 350, 'V': None})

    def test_is_feedstock_true_for_the_real_highest_carbon_feed(self):
        """is_feedstock(feed, system.feeds) is True: feed is system's only
        (and therefore trivially highest-carbon-flow) feed, and has a
        non-empty ID."""
        self.assertTrue(
            self._export.is_feedstock(self.feed, self.system.feeds))

    def test_get_thermo_matches_real_unit_thermo_package(self):
        """get_thermo(H1) reads H1.thermo's real mixture/Gamma/Phi/PCF and
        returns their string names -- biosteam's default thermo package for
        a Water/Ethanol system: gamma == 'DortmundActivityCoefficients',
        phi == 'IdealFugacityCoefficients', PCF ==
        'MockPoyntingCorrectionFactors', and mixture contains 'IdealMixture'."""
        thermo = self._export.get_thermo(self.H1)
        self.assertEqual(thermo['gamma'], 'DortmundActivityCoefficients')
        self.assertEqual(thermo['phi'], 'IdealFugacityCoefficients')
        self.assertEqual(thermo['PCF'], 'MockPoyntingCorrectionFactors')
        self.assertIn('IdealMixture', thermo['mixture'])


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestUtilityEmissionSortedById(RealBiosteamTestCase):
    """Deterministic-ordering guarantee (2026-08-16 determinism fix): the
    utilities.heat_utilities / other_utilities arrays are emitted sorted by
    agent id, not in process-varying accumulation-set order. Exercised on a
    real two-unit system that consumes two distinct heat-utility agents:
    heating 298.15 K -> 350 K draws low_pressure_steam, cooling
    350 K -> 310 K draws chilled_water (confirmed against the real installed
    biosteam utility-agent selection in this environment -- not
    cooling_water, whose practical range does not reach a 310 K outlet)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()   # evicts Tier-1 biosteam/thermosteam stubs

        # Same pisces_sff re-import dance as TestExportHelpersAgainstRealObjects
        # above (the export path reads module-level biosteam-bound names such
        # as PowerUtility that a stale cached _export would hold as fakes).
        for key in [k for k in sys.modules
                    if k == "pisces_sff" or k.startswith("pisces_sff.")]:
            del sys.modules[key]

        from pisces_sff import _export

        import biosteam as bst
        bst.settings.set_thermo(["Water", "Ethanol"])
        feed = bst.Stream("det_feed", Water=500, units="kg/hr", T=298.15)
        H_heat = bst.HXutility("DET_H1", ins=feed, outs="det_hot", T=350)
        H_cool = bst.HXutility("DET_H2", ins=H_heat-0, outs="det_cold", T=310)
        system = bst.System("det_sys", path=(H_heat, H_cool))
        system.simulate()
        # Same placeholder finance args as tests/_real_objects.py -- the
        # exporter only reads tea.duration[0].
        tea = bst.TEA(
            system=system, IRR=0.15, duration=(2020, 2030),
            depreciation="MACRS7", income_tax=0.21, operating_days=330.,
            lang_factor=3., construction_schedule=(0.4, 0.6),
            startup_months=0., startup_FOCfrac=0., startup_VOCfrac=0.,
            startup_salesfrac=0., WC_over_FCI=0.05, finance_interest=0.,
            finance_years=0, finance_fraction=0.,
        )
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "det_sorted.json"
        _export.export_biosteam_flowsheet(
            system, str(path), sff_version="0.1.1", tea=tea)
        cls.doc = json.loads(path.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_heat_and_other_utilities_sorted_by_id(self):
        """heat_utilities carries exactly the two agents the system consumed,
        in sorted-id order: ['chilled_water', 'low_pressure_steam'].
        other_utilities is asserted sorted too -- vacuously here (this system
        has no natural_gas utility, so the array is empty), which pins the
        shape without a second fixture."""
        hu_ids = [h["id"] for h in self.doc["utilities"]["heat_utilities"]]
        self.assertEqual(hu_ids, ["chilled_water", "low_pressure_steam"])
        ou_ids = [o["id"] for o in self.doc["utilities"]["other_utilities"]]
        self.assertEqual(ou_ids, sorted(ou_ids))


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestGetReactionsOrderRealObjects(RealBiosteamTestCase):
    """Re-verify Tier 1's get_reactions discovery-order guarantee against
    REAL thermosteam Reaction/ParallelReaction objects assigned to a real
    biosteam unit (2026-08-16 determinism fix). Reactions are atomically
    balanced over the registered chemicals so thermosteam accepts them;
    distinct X values make the emission order observable."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()   # evicts Tier-1 biosteam/thermosteam stubs

        # Same pisces_sff re-import dance as TestExportHelpersAgainstRealObjects
        # above (get_reactions reads the module-level Reaction/ReactionSet/
        # SeriesReaction/ParallelReaction names bound at _export import time).
        for key in [k for k in sys.modules
                    if k == "pisces_sff" or k.startswith("pisces_sff.")]:
            del sys.modules[key]

        from pisces_sff import _export

        cls._export = _export

        import biosteam as bst
        import thermosteam as tmo
        bst.settings.set_thermo(["Water", "Ethanol", "Glucose", "CO2", "O2"])
        r_first = tmo.Reaction("Glucose -> 2 Ethanol + 2 CO2", "Glucose", 0.11)
        r_sub1 = tmo.Reaction("Glucose + 6 O2 -> 6 CO2 + 6 Water", "Glucose", 0.22)
        r_sub2 = tmo.Reaction("Ethanol + 3 O2 -> 2 CO2 + 3 Water", "Ethanol", 0.33)
        r_last = tmo.Reaction("Glucose -> 2 Ethanol + 2 CO2", "Glucose", 0.44)
        # get_reactions reads unit.__dict__, so a real (unsimulated) unit is
        # the right fixture; attribute assignment order IS the discovery order.
        unit = bst.Mixer("DET_RXN_ORDER_M1")
        unit.first_rxn = r_first
        unit.rxn_group = tmo.ParallelReaction([r_sub1, r_sub2])
        unit.last_rxn = r_last
        cls.unit = unit

    def test_reactions_emitted_in_assignment_order(self):
        """Expected emission: first_rxn, then rxn_group's two subreactions in
        their own order (sharing index 1 -- parallel subreactions share an
        index, which then increments), then last_rxn. Conversions
        [0.11, 0.22, 0.33, 0.44] prove the order; indices are [0, 1, 1, 2];
        reactants match each reaction's declared reactant."""
        reactions = self._export.get_reactions(self.unit, stoichiometry="dict")
        self.assertEqual([round(r["conversion"], 9) for r in reactions],
                         [0.11, 0.22, 0.33, 0.44])
        self.assertEqual([r["index"] for r in reactions], [0, 1, 1, 2])
        self.assertEqual([r["reactant"] for r in reactions],
                         ["Glucose", "Glucose", "Ethanol", "Glucose"])


if __name__ == "__main__":
    unittest.main()
