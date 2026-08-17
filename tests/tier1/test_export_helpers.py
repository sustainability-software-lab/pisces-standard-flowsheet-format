# -*- coding: utf-8 -*-
# Tier 1 walking skeleton: prove the shared biosteam stub lets us import
# pisces_sff._export and exercise its pure / near-pure helpers with NO real
# biosteam loaded. Any helper tested here with a FAKE object is re-verified
# against a REAL object in Tier 2/3 (is_product -> tests/tier2).

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests import _fakes

_export = _fakes.load_export()


class TestStubKeepsBiosteamFake(unittest.TestCase):
    def test_biosteam_import_is_the_stub_not_the_real_package(self):
        """The biosteam and thermosteam modules loaded via _fakes carry the _SFF_STUB marker."""
        self.assertTrue(getattr(sys.modules["biosteam"], "_SFF_STUB", False))
        self.assertTrue(getattr(sys.modules["thermosteam"], "_SFF_STUB", False))


class TestFormatName(unittest.TestCase):
    def test_empty_returns_empty(self):
        """format_name("") -> ""."""
        self.assertEqual(_export.format_name(""), "")

    def test_all_caps_passthrough(self):
        """A recognized all-caps abbreviation ("CSL") is returned unchanged."""
        self.assertEqual(_export.format_name("CSL"), "CSL")

    def test_specific_TAL_mapping(self):
        """format_name("TAL_product") maps via the special-case table to "Triacetic acid lactone"."""
        self.assertEqual(_export.format_name("TAL_product"), "Triacetic acid lactone")

    def test_feedstock_suffix_stripped_and_capitalized(self):
        """format_name("corn_feedstock") strips the "_feedstock" suffix and capitalizes -> "Corn"."""
        self.assertEqual(_export.format_name("corn_feedstock"), "Corn")


class TestIsProductWithFakeStream(unittest.TestCase):
    # is_product is re-verified against a REAL Stream in tests/tier2.
    def _stream(self, cost):
        return types.SimpleNamespace(cost=cost)

    def test_priced_stream_in_products_is_a_product(self):
        """A stream with positive cost that is in all_sys_products -> is_product is True."""
        s = self._stream(1.0)
        self.assertTrue(_export.is_product(s, [s]))

    def test_zero_cost_is_not_a_product(self):
        """A stream with zero cost, even if in all_sys_products -> is_product is False."""
        s = self._stream(0.0)
        self.assertFalse(_export.is_product(s, [s]))

    def test_stream_absent_from_products_is_not_a_product(self):
        """A priced stream absent from all_sys_products -> is_product is False."""
        s = self._stream(1.0)
        self.assertFalse(_export.is_product(s, []))


class _Bag:
    """Hashable-by-identity attribute bag, for fakes that must be usable as a
    dict key or set member. Unlike types.SimpleNamespace (which defines
    __eq__ by field comparison and is therefore unhashable), this stays
    hashable by identity -- matching how biosteam streams/agents behave."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class TestGetVersionedExporter(unittest.TestCase):
    def test_resolves_a_known_version(self):
        """get_versioned_exporter('0.0.12') resolves to the function named
        export_biosteam_flowsheet_sff_0_0_12."""
        self.assertIs(_export.get_versioned_exporter("0.0.12"),
                      _export.export_biosteam_flowsheet_sff_0_0_12)

    def test_unknown_version_raises_value_error_listing_available(self):
        """An unregistered version raises ValueError naming the versions
        available_sff_versions() would report."""
        with self.assertRaises(ValueError) as caught:
            _export.get_versioned_exporter("9.9.9")
        self.assertIn("0.0.12", str(caught.exception))


class TestAvailableSffVersions(unittest.TestCase):
    def test_includes_current_versions_sorted_oldest_first(self):
        """available_sff_versions() discovers every
        export_biosteam_flowsheet_sff_* function in this module and returns
        the versions sorted oldest-first."""
        versions = _export.available_sff_versions()
        self.assertIn("0.0.12", versions)
        self.assertIn("0.0.5", versions)
        numeric = [tuple(int(p) for p in v.split(".")) for v in versions]
        self.assertEqual(numeric, sorted(numeric))


class TestAssignStreamIds(unittest.TestCase):
    def test_pre_0_0_12_keeps_raw_id_including_blanks(self):
        """Below 0.0.12, _assign_stream_ids maps each stream to its raw .ID
        unchanged, blank ids included (historical, byte-stable behavior)."""
        s1 = _Bag(ID="feed")
        s2 = _Bag(ID="")
        resolved = _export._assign_stream_ids([s1, s2], "0.0.11")
        self.assertEqual(resolved, {s1: "feed", s2: ""})

    def test_0_0_12_synthesizes_distinct_ids_for_blank_streams(self):
        """From 0.0.12, streams with a blank .ID get distinct, deterministic
        'unnamed_stream_<n>' ids, n counting blanks in system order."""
        s1 = _Bag(ID="feed")
        s2 = _Bag(ID="")
        s3 = _Bag(ID="")
        resolved = _export._assign_stream_ids([s1, s2, s3], "0.0.12")
        self.assertEqual(resolved[s1], "feed")
        self.assertEqual(resolved[s2], "unnamed_stream_1")
        self.assertEqual(resolved[s3], "unnamed_stream_2")

    def test_0_0_12_named_streams_are_left_alone(self):
        """From 0.0.12, streams with a non-blank .ID are unaffected."""
        s1 = _Bag(ID="A")
        s2 = _Bag(ID="B")
        resolved = _export._assign_stream_ids([s1, s2], "0.0.12")
        self.assertEqual(resolved, {s1: "A", s2: "B"})


class TestGetRequiredArgs(unittest.TestCase):
    def test_returns_positional_or_keyword_params_without_a_default(self):
        """get_required_args returns only POSITIONAL_OR_KEYWORD parameters
        that have no default -> ['a', 'b'] for def f(a, b, c=1, *, d, e=2)."""
        def f(a, b, c=1, *, d, e=2):
            pass
        self.assertEqual(_export.get_required_args(f), ["a", "b"])

    def test_all_defaulted_params_returns_empty_list(self):
        """A function whose every parameter has a default returns []."""
        def g(a=1, b=2):
            pass
        self.assertEqual(_export.get_required_args(g), [])


class TestGetUnitType(unittest.TestCase):
    def test_returns_the_units_line_attribute_verbatim(self):
        """get_unit_type is a passthrough for unit.line."""
        unit = types.SimpleNamespace(line="Mixer")
        self.assertEqual(_export.get_unit_type(unit), "Mixer")


class TestGetDesignSimulationMethod(unittest.TestCase):
    def _unit_of_class(self, module_name, class_name):
        cls = type(class_name, (), {"__module__": module_name})
        return cls()

    def test_biosteam_class_links_to_the_biosteam_repo(self):
        """A unit class under a 'biosteam.' module path links to
        BioSTEAMDevelopmentGroup/biosteam on GitHub."""
        unit = self._unit_of_class("biosteam.units.mixing", "Mixer")
        result = _export.get_design_simulation_method(unit)
        self.assertTrue(result.startswith("Mixer on "))
        self.assertIn(
            "BioSTEAMDevelopmentGroup/biosteam/blob/master/biosteam/units/mixing.py",
            result)

    def test_biorefineries_class_links_to_the_bioindustrial_park_repo(self):
        """A unit class under a 'biorefineries.' module path links to
        BioSTEAMDevelopmentGroup/Bioindustrial-Park on GitHub."""
        unit = self._unit_of_class("biorefineries.corn.units", "CustomUnit")
        result = _export.get_design_simulation_method(unit)
        self.assertIn(
            "Bioindustrial-Park/blob/master/biorefineries/corn/units.py",
            result)

    def test_unrecognized_module_path_has_no_link(self):
        """A class whose module path matches neither known prefix yields an
        empty link address, so the result is just '<ClassName> on '."""
        unit = self._unit_of_class("some_other_package.units", "Thing")
        result = _export.get_design_simulation_method(unit)
        self.assertEqual(result, "Thing on ")


class TestGetDesignInputSpecs(unittest.TestCase):
    def test_reads_only_present_known_params(self):
        """get_design_input_specs reads only the known design-spec attrs that
        are present on the unit -> {'T': 350.0, 'tau': 2.0} for a unit
        exposing T, tau, and an unrelated attribute."""
        unit = types.SimpleNamespace(T=350.0, tau=2.0, some_other_attr=1)
        self.assertEqual(_export.get_design_input_specs(unit),
                         {"T": 350.0, "tau": 2.0})

    def test_no_known_params_present_returns_empty_dict(self):
        """A unit with none of the known design-spec attributes yields {}."""
        unit = types.SimpleNamespace(foo=1)
        self.assertEqual(_export.get_design_input_specs(unit), {})

    def test_attribute_read_failure_raises_DesignInputSpecError(self):
        """A design-spec attribute whose read fails (after hasattr already
        succeeded once) is wrapped in a DesignInputSpecError, chaining the
        underlying exception rather than propagating it bare."""
        class Flaky:
            def __init__(self):
                self.calls = 0

            def __get__(self, obj, objtype=None):
                self.calls += 1
                if self.calls > 1:
                    raise ValueError("boom on second access")
                return 42.0

        class FakeUnit:
            T = Flaky()

        with self.assertRaises(_export.DesignInputSpecError):
            _export.get_design_input_specs(FakeUnit())


class TestGetThermo(unittest.TestCase):
    def test_reads_mixture_and_activity_model_names(self):
        """get_thermo reads mixture.__str__() (stripping the leading '..., '),
        and the Gamma/Phi/PCF class names, fixing BioSTEAM's 'Poyinting' typo
        to 'Poynting' in the PCF name."""
        class Mixture:
            def __str__(self):
                return "IdealMixture(..., some detail)"

        class NRTLActivityCoefficients:
            pass

        class IdealFugacityCoefficients:
            pass

        class PoyintingCorrectionFactors:
            pass

        unit = types.SimpleNamespace(thermo=types.SimpleNamespace(
            mixture=Mixture(), Gamma=NRTLActivityCoefficients,
            Phi=IdealFugacityCoefficients, PCF=PoyintingCorrectionFactors))
        thermo = _export.get_thermo(unit)
        self.assertEqual(thermo["mixture"], "IdealMixture(some detail)")
        self.assertEqual(thermo["gamma"], "NRTLActivityCoefficients")
        self.assertEqual(thermo["phi"], "IdealFugacityCoefficients")
        self.assertEqual(thermo["PCF"], "PoyntingCorrectionFactors")


class TestGetUtilityResults(unittest.TestCase):
    def test_consumption_and_production_are_aggregated_by_agent(self):
        """Positive heat-utility duty accumulates as consumption keyed by
        agent id; power-utility consumption is folded in under 'Marginal grid
        electricity'; other-utility positive mass flow is consumption keyed
        by its own id."""
        steam = _Bag(ID="low_pressure_steam")
        hu = types.SimpleNamespace(agent=steam, duty=1000.0)
        power = types.SimpleNamespace(consumption=50.0, production=0.0)
        ng = _Bag(ID="natural_gas", F_mass=2.0)
        unit = types.SimpleNamespace(heat_utilities=[hu], power_utility=power,
                                     natural_gas=ng)
        u_cons, u_prod, hu_agents, pu_agents, ou_agents = \
            _export.get_utility_results(unit)
        self.assertEqual(u_cons, {"low_pressure_steam": 1000.0,
                                  "Marginal grid electricity": 50.0,
                                  "natural_gas": 2.0})
        self.assertEqual(u_prod, {})
        self.assertEqual(hu_agents, {steam})
        self.assertEqual(pu_agents, {_export.PowerUtility})
        self.assertEqual(ou_agents, {ng})

    def test_negative_heat_duty_is_production_and_none_agent_is_skipped(self):
        """Non-positive heat duty is recorded as production, and a heat
        utility with agent=None (no agent attached) contributes nothing."""
        cw = _Bag(ID="cooling_water")
        hu_prod = types.SimpleNamespace(agent=cw, duty=-500.0)
        hu_none = types.SimpleNamespace(agent=None, duty=1.0)
        unit = types.SimpleNamespace(heat_utilities=[hu_prod, hu_none])
        u_cons, u_prod, hu_agents, pu_agents, ou_agents = \
            _export.get_utility_results(unit)
        self.assertEqual(u_prod, {"cooling_water": -500.0})
        self.assertEqual(u_cons, {})
        self.assertEqual(hu_agents, {cw})

    def test_unit_with_no_utility_attributes_yields_mostly_empty_results(self):
        """A unit exposing none of heat_utilities/power_utility/natural_gas
        still returns empty consumption/production dicts and empty hu/ou
        agent sets; pu_agents unconditionally contains PowerUtility (the set
        literal does not depend on hasattr(unit, 'power_utility'))."""
        unit = types.SimpleNamespace()
        u_cons, u_prod, hu_agents, pu_agents, ou_agents = \
            _export.get_utility_results(unit)
        self.assertEqual(u_cons, {})
        self.assertEqual(u_prod, {})
        self.assertEqual(hu_agents, set())
        self.assertEqual(pu_agents, {_export.PowerUtility})
        self.assertEqual(ou_agents, set())

    def test_natural_gas_none_is_skipped_without_raising(self):
        """A unit whose natural_gas attribute is present but None (no gas
        stream attached) contributes no other-utility agent and does not raise
        AttributeError while reading ou.F_mass."""
        unit = types.SimpleNamespace(natural_gas=None)
        u_cons, u_prod, hu_agents, pu_agents, ou_agents = \
            _export.get_utility_results(unit)
        self.assertEqual(ou_agents, set())
        self.assertEqual(u_cons, {})
        self.assertEqual(u_prod, {})

    def test_power_utility_none_is_skipped_without_raising(self):
        """A unit whose power_utility attribute is present but None contributes
        no power consumption/production and does not raise AttributeError while
        reading pu.consumption."""
        unit = types.SimpleNamespace(power_utility=None)
        u_cons, u_prod, hu_agents, pu_agents, ou_agents = \
            _export.get_utility_results(unit)
        self.assertEqual(u_cons, {})
        self.assertEqual(u_prod, {})
        self.assertEqual(pu_agents, {_export.PowerUtility})


class _FakePhaseStream:
    """Stand-in for `stream[phase]`: only the attributes get_composition /
    get_phase_properties read."""

    def __init__(self, imol, imass, F_mol, F_mass, F_vol=None):
        self.imol = imol
        self.imass = imass
        self.F_mol = F_mol
        self.F_mass = F_mass
        if F_vol is not None:
            self.F_vol = F_vol


class _FakeMultiPhaseStream:
    """Stand-in for a biosteam Stream: `.phases`, `.chemicals`, and
    `stream[phase]` indexing."""

    def __init__(self, phase_symbols, chem_ids, phase_objs):
        self.phases = phase_symbols
        self.chemicals = [types.SimpleNamespace(ID=c) for c in chem_ids]
        self._phase_objs = phase_objs

    def __getitem__(self, p):
        return self._phase_objs[p]


class TestGetComposition(unittest.TestCase):
    def test_both_fractions_for_components_with_positive_molar_flow(self):
        """get_composition (units='both', the default) lists only components
        with positive molar flow in a phase, each carrying both mol_fraction
        and mass_fraction."""
        liquid = _FakePhaseStream(imol={"Water": 10.0, "Ethanol": 0.0},
                                  imass={"Water": 180.0, "Ethanol": 0.0},
                                  F_mol=10.0, F_mass=180.0)
        stream = _FakeMultiPhaseStream(["l"], ["Water", "Ethanol"], {"l": liquid})
        comp = _export.get_composition(stream)
        self.assertEqual(comp, [{"phase": "l", "component_name": "Water",
                                 "mol_fraction": 1.0, "mass_fraction": 1.0}])

    def test_mol_percent_reports_only_mol_fraction(self):
        """units='mol%' includes mol_fraction only, no mass_fraction key."""
        liquid = _FakePhaseStream(imol={"Water": 1.0}, imass={"Water": 18.0},
                                  F_mol=1.0, F_mass=18.0)
        stream = _FakeMultiPhaseStream(["l"], ["Water"], {"l": liquid})
        comp = _export.get_composition(stream, units="mol%")
        self.assertEqual(comp, [{"phase": "l", "component_name": "Water",
                                 "mol_fraction": 1.0}])


class TestGetPhaseProperties(unittest.TestCase):
    def test_bare_number_shape_with_per_phase_composition(self):
        """get_phase_properties(stream, inline=False) reports bare-number
        totals (mass/molar/volumetric) per phase plus that phase's own
        mol/mass composition."""
        liquid = _FakePhaseStream(imol={"Water": 2.0}, imass={"Water": 36.0},
                                  F_mol=2.0, F_mass=36.0, F_vol=0.036)
        stream = _FakeMultiPhaseStream(["l"], ["Water"], {"l": liquid})
        phases = _export.get_phase_properties(stream, inline=False)
        self.assertEqual(phases["l"]["total_mass_flow"], 36.0)
        self.assertEqual(phases["l"]["total_molar_flow"], 2.0)
        self.assertEqual(phases["l"]["total_volumetric_flow"], 0.036)
        self.assertEqual(phases["l"]["composition"], [
            {"component_name": "Water", "mol_fraction": 1.0, "mass_fraction": 1.0}])

    def test_inline_shape_wraps_totals_in_value_units_pairs(self):
        """inline=True wraps each scalar total in the pre-0.0.7
        {"value", "units"} shape."""
        liquid = _FakePhaseStream(imol={"Water": 1.0}, imass={"Water": 18.0},
                                  F_mol=1.0, F_mass=18.0, F_vol=0.018)
        stream = _FakeMultiPhaseStream(["l"], ["Water"], {"l": liquid})
        phases = _export.get_phase_properties(stream, inline=True)
        self.assertEqual(phases["l"]["total_mass_flow"],
                         {"value": 18.0, "units": "kg/h"})


class TestGetEquation(unittest.TestCase):
    def test_delegates_to_get_stoichiometric_string(self):
        """get_equation calls thermosteam's get_stoichiometric_string with the
        reaction's stoichiometry/phases/chemicals and returns its result
        verbatim."""
        rxn = types.SimpleNamespace(stoichiometry=[-1.0, 1.0], phases="l",
                                    chemicals=["A", "B"])
        with mock.patch.object(_export, "get_stoichiometric_string",
                               return_value="A -> B") as fake:
            result = _export.get_equation(rxn)
        fake.assert_called_once_with(stoichiometry=rxn.stoichiometry,
                                     phases=rxn.phases, chemicals=rxn.chemicals)
        self.assertEqual(result, "A -> B")


class TestGetReactions(unittest.TestCase):
    def _chem(self, ID):
        return types.SimpleNamespace(ID=ID)

    def test_single_reaction_dict_stoichiometry(self):
        """A plain (non-Series/Parallel) reaction on a unit is exported with
        its index, equation, reactant, conversion, and dict-form
        stoichiometry (all nonzero entries kept, reactant included with its
        negative sign)."""
        rxn = _export.Reaction()
        rxn.reactant = "A"
        rxn.X = 0.8
        rxn.stoichiometry = [-1.0, 1.0]
        rxn.chemicals = [self._chem("A"), self._chem("B")]
        rxn.phases = "l"

        class FakeUnit:
            pass
        unit = FakeUnit()
        unit.rxn = rxn

        with mock.patch.object(_export, "get_stoichiometric_string",
                               return_value="A -> B"):
            reactions = _export.get_reactions(unit, stoichiometry="dict")

        self.assertEqual(reactions, [{
            "index": 0, "equation": "A -> B", "reactant": "A",
            "conversion": 0.8, "stoichiometry": {"A": -1.0, "B": 1.0},
        }])

    def test_stoichiometry_none_omits_the_stoichiometry_key(self):
        """stoichiometry=None omits the 'stoichiometry' key from the reaction
        entry entirely."""
        rxn = _export.Reaction()
        rxn.reactant = "A"
        rxn.X = 0.5
        rxn.stoichiometry = [-1.0, 1.0]
        rxn.chemicals = [self._chem("A"), self._chem("B")]
        rxn.phases = "l"

        class FakeUnit:
            pass
        unit = FakeUnit()
        unit.rxn = rxn

        with mock.patch.object(_export, "get_stoichiometric_string",
                               return_value="A -> B"):
            reactions = _export.get_reactions(unit, stoichiometry=None)

        self.assertNotIn("stoichiometry", reactions[0])


class TestGetReactionsOrder(unittest.TestCase):
    """Deterministic-ordering guarantee (2026-08-16 determinism fix):
    get_reactions emits reactions in unit.__dict__ insertion order -- the
    order the model author assigned them -- not in process-varying
    set-iteration order. Re-verified against real thermosteam objects in
    tests/tier2/test_export_helpers_real.py."""

    def _rxn(self, reactant, X):
        rxn = _export.Reaction()
        rxn.reactant = reactant
        rxn.X = X
        rxn.stoichiometry = [-1.0, 1.0]
        rxn.chemicals = []
        rxn.phases = "l"
        return rxn

    def test_plain_reactions_emitted_in_assignment_order(self):
        """Eight plain reactions assigned to a fake unit in a known order come
        back in exactly that order, with sequential indices 0..7. (The pre-fix
        set-comprehension collection made this order id()-hash dependent.)"""
        class FakeUnit:
            pass
        unit = FakeUnit()
        conversions = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        for n, X in enumerate(conversions):
            setattr(unit, "rxn_%d" % n, self._rxn("C%d" % n, X))

        with mock.patch.object(_export, "get_stoichiometric_string",
                               return_value="eq"):
            reactions = _export.get_reactions(unit, stoichiometry=None)

        self.assertEqual([r["conversion"] for r in reactions], conversions)
        self.assertEqual([r["reactant"] for r in reactions],
                         ["C%d" % n for n in range(8)])
        self.assertEqual([r["index"] for r in reactions], list(range(8)))

    def test_parent_filter_preserves_order_and_drops_the_child(self):
        """A ParallelReaction assigned between two plain reactions: emission
        order is pre, then the parallel's subreactions in their own iteration
        order (sharing one index, which then increments), then post -- and a
        child reaction whose _parent is the collected ParallelReaction is
        dropped entirely, without disturbing its neighbors' order."""
        class FakeUnit:
            pass
        unit = FakeUnit()
        unit.pre = self._rxn("A", 0.11)
        sub1 = self._rxn("B", 0.22)
        sub2 = self._rxn("C", 0.33)
        unit.par = _export.ParallelReaction([sub1, sub2])
        child = self._rxn("X", 0.99)
        child._parent = unit.par
        unit.child = child
        unit.post = self._rxn("D", 0.44)

        with mock.patch.object(_export, "get_stoichiometric_string",
                               return_value="eq"):
            reactions = _export.get_reactions(unit, stoichiometry=None)

        self.assertEqual([r["conversion"] for r in reactions],
                         [0.11, 0.22, 0.33, 0.44])
        self.assertEqual([r["reactant"] for r in reactions],
                         ["A", "B", "C", "D"])
        self.assertEqual([r["index"] for r in reactions], [0, 1, 1, 2])


class TestTraceFunctionCalls(unittest.TestCase):
    def test_records_only_targeted_functions_in_call_order(self):
        """trace_function_calls(A, F) returns the subset of F that A() calls,
        in the order they were called, ignoring calls to untracked
        functions."""
        calls = []

        def f1():
            calls.append("f1")

        def f2():
            calls.append("f2")

        def untracked():
            calls.append("untracked")

        def A():
            untracked()
            f2()
            f1()

        result = _export.trace_function_calls(A, [f1, f2])
        self.assertEqual(result, [f2, f1])
        self.assertEqual(calls, ["untracked", "f2", "f1"])

    def test_no_matching_calls_returns_an_empty_list(self):
        """An A() that calls none of F returns []."""
        def f1():
            pass

        def A():
            pass

        self.assertEqual(_export.trace_function_calls(A, [f1]), [])


class TestIsFeedstockDirect(unittest.TestCase):
    def _feed(self, ID, source, carbon_flow):
        return types.SimpleNamespace(
            ID=ID, source=source,
            get_atomic_flow=lambda el: carbon_flow if el == "C" else 0.0)

    def test_max_carbon_boundary_feed_is_the_feedstock(self):
        """Among boundary feeds (source is None), the one with the highest
        carbon atomic flow is the feedstock; the rest are not."""
        corn = self._feed("corn", None, 100.0)
        water = self._feed("water", None, 0.0)
        self.assertTrue(_export.is_feedstock(corn, [corn, water]))
        self.assertFalse(_export.is_feedstock(water, [corn, water]))

    def test_blank_id_is_never_a_feedstock(self):
        """A stream with a blank ID is never the feedstock, even with the
        highest carbon flow."""
        blank = self._feed("", None, 100.0)
        self.assertFalse(_export.is_feedstock(blank, [blank]))

    def test_stream_absent_from_feeds_list_is_not_a_feedstock(self):
        """A stream absent from all_sys_feeds is never the feedstock."""
        corn = self._feed("corn", None, 100.0)
        self.assertFalse(_export.is_feedstock(corn, []))

    def test_non_boundary_feed_is_excluded_from_the_comparison(self):
        """A feed with a non-None source is excluded from the max-carbon
        comparison, so it cannot be the feedstock even with the highest
        carbon flow."""
        internal_feed = self._feed("weird", types.SimpleNamespace(), 500.0)
        self.assertFalse(_export.is_feedstock(internal_feed, [internal_feed]))

    def _counting_feed(self, ID, source, carbon_flow, counter):
        def get_atomic_flow(el):
            if el == "C":
                counter[0] += 1
                return carbon_flow
            return 0.0
        return types.SimpleNamespace(ID=ID, source=source,
                                     get_atomic_flow=get_atomic_flow)

    def test_atomic_flow_is_read_once_per_feed(self):
        """is_feedstock reads get_atomic_flow('C') exactly once per boundary
        feed (not twice for the running maximum) when computing the max-carbon
        feed internally."""
        counter = [0]
        corn = self._counting_feed("corn", None, 100.0, counter)
        water = self._counting_feed("water", None, 0.0, counter)
        _export.is_feedstock(corn, [corn, water])
        self.assertEqual(counter[0], 2)

    def test_precomputed_max_feed_avoids_recomputation(self):
        """When the max-carbon feed is supplied, is_feedstock does not scan the
        feeds again -- it reads no atomic flows at all."""
        counter = [0]
        corn = self._counting_feed("corn", None, 100.0, counter)
        water = self._counting_feed("water", None, 0.0, counter)
        best = _export._max_carbon_boundary_feed([corn, water])
        counter[0] = 0
        self.assertTrue(_export.is_feedstock(corn, [corn, water], best))
        self.assertEqual(counter[0], 0)


class TestMaxCarbonBoundaryFeed(unittest.TestCase):
    def _feed(self, ID, source, carbon_flow):
        return types.SimpleNamespace(
            ID=ID, source=source,
            get_atomic_flow=lambda el: carbon_flow if el == "C" else 0.0)

    def test_picks_the_highest_carbon_boundary_feed(self):
        """_max_carbon_boundary_feed returns the boundary feed with the highest
        carbon atomic flow."""
        corn = self._feed("corn", None, 100.0)
        water = self._feed("water", None, 0.0)
        self.assertIs(_export._max_carbon_boundary_feed([corn, water]), corn)

    def test_excludes_non_boundary_feeds(self):
        """A feed with a source is not a boundary feed and cannot be selected."""
        internal = self._feed("internal", types.SimpleNamespace(), 500.0)
        self.assertIsNone(_export._max_carbon_boundary_feed([internal]))

    def test_no_carbon_feed_yields_none(self):
        """When no boundary feed carries carbon, the result is None."""
        water = self._feed("water", None, 0.0)
        self.assertIsNone(_export._max_carbon_boundary_feed([water]))


class TestGetChemicalEntry(unittest.TestCase):
    def _chem(self, ID, CAS=None, formula=None, MW=0.0):
        return types.SimpleNamespace(ID=ID, CAS=CAS, formula=formula, MW=MW)

    def test_vle_chemical_records_cas_as_registry_id(self):
        """A VLE chemical is marked included_in_thermo and records its CAS as
        registry_id, with keys in the byte-stable order."""
        c = self._chem("Water", CAS="7732-18-5", formula="H2O", MW=18.015)
        entry = _export.get_chemical_entry(c, 0, True, "dict")
        self.assertEqual(list(entry.keys()),
                         ["id", "included_in_thermo", "index", "formula",
                          "registry_id", "molar_mass"])
        self.assertEqual(entry, {
            "id": "Water", "included_in_thermo": True, "index": 0,
            "formula": "H2O", "registry_id": "7732-18-5",
            "molar_mass": 18.015})

    def test_non_vle_chemical_omits_registry_id(self):
        """A non-VLE chemical carries molar_mass and no registry_id."""
        c = self._chem("Ash", CAS=None, formula=None, MW=1.0)
        entry = _export.get_chemical_entry(c, 3, False, "dict")
        self.assertNotIn("registry_id", entry)
        self.assertNotIn("formula", entry)
        self.assertEqual(entry["included_in_thermo"], False)
        self.assertEqual(entry["molar_mass"], 1.0)

    def test_falsy_stoichiometry_omits_index(self):
        """index is emitted only when stoichiometry is truthy."""
        c = self._chem("Ash", MW=1.0)
        entry = _export.get_chemical_entry(c, 3, False, None)
        self.assertNotIn("index", entry)

    def test_vle_chemical_without_cas_raises(self):
        """A VLE chemical with no CAS cannot get a valid registry_id, so the
        exporter fails loudly with an SFFExportError naming the chemical rather
        than emitting registry_id: null (which fails schema validation)."""
        c = self._chem("Weird", CAS=None, MW=100.0)
        with self.assertRaises(_export.SFFExportError) as caught:
            _export.get_chemical_entry(c, 0, True, "dict")
        self.assertIn("Weird", str(caught.exception))

    def test_vle_chemical_with_empty_cas_raises(self):
        """An empty-string CAS is also rejected (falsy)."""
        c = self._chem("Weird", CAS="", MW=100.0)
        with self.assertRaises(_export.SFFExportError):
            _export.get_chemical_entry(c, 0, True, "dict")


class TestGetStreamRoles(unittest.TestCase):
    def test_boundary_input_feedstock_and_purchased(self):
        """A priced boundary-input stream that is also the max-carbon feed
        gets ['input', 'purchased_raw_material', 'feedstock']."""
        corn = types.SimpleNamespace(
            ID="corn", source=None, sink=types.SimpleNamespace(), price=1.5,
            get_atomic_flow=lambda el: 100.0 if el == "C" else 0.0)
        roles = _export.get_stream_roles(corn, [corn], [])
        self.assertEqual(roles, ["input", "purchased_raw_material", "feedstock"])

    def test_boundary_output_product(self):
        """A boundary-output stream present in all_sys_products with positive
        cost gets ['output', 'product']."""
        product = types.SimpleNamespace(
            ID="ethanol", source=types.SimpleNamespace(), sink=None,
            cost=10.0, price=0.0)
        roles = _export.get_stream_roles(product, [], [product])
        self.assertEqual(roles, ["output", "product"])

    def test_internal_stream_has_only_the_internal_role(self):
        """A stream with both a source and a sink is internal, with no
        designation roles."""
        internal = types.SimpleNamespace(
            ID="s1", source=types.SimpleNamespace(),
            sink=types.SimpleNamespace(), price=0.0)
        roles = _export.get_stream_roles(internal, [], [])
        self.assertEqual(roles, ["internal"])


class TestWriteSffJson(unittest.TestCase):
    def test_writes_indented_json(self):
        """_write_sff_json serializes the document to the given path as
        indented JSON, round-trippable back to the original dict."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "out.json"
            _export._write_sff_json({"a": 1}, str(path))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})

    def test_unserializable_document_raises_FlowsheetWriteError(self):
        """A document containing a non-JSON-serializable value (a set) raises
        FlowsheetWriteError, chaining the underlying TypeError, rather than
        the raw json.dump exception propagating unwrapped."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "out.json"
            with self.assertRaises(_export.FlowsheetWriteError):
                _export._write_sff_json({"a": {1, 2, 3}}, str(path))


if __name__ == "__main__":
    unittest.main()
