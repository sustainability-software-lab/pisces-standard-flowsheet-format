# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: consolidated validator-helper tests. Each `_check_*` is exercised
# directly on a synthetic `_Context` -- import-light, no chemicals/thermosteam
# import. Merged (Task 2.1) from the former test_checks_chemicals.py,
# test_checks_material_balance.py, test_checks_meta_xref.py,
# test_checks_quantity_units.py, test_checks_streams_ref.py,
# test_checks_units.py, test_checks_utilities.py, and
# test_validate_foundation.py -- one file per catalogue category via #%%
# banners. No assertions were added, removed, or changed in the merge; see
# tests/tier1/test_validate_registry.py for the registry-completeness meta-check.

import datetime as _dt
import json
import tempfile
import unittest
from pathlib import Path

from tests._validate_loader import V


def ctx(**sections):
    """Build a `_Context` from the given SFF section kwargs (e.g. streams=[...])."""
    return V._Context(sections)


#%% ------- Units (UNIT-01..07) ------- ##

def statuses(results):
    """Map each result's (check_id, severity) to its status, for multi-result checks."""
    return {(r.check_id, r.severity): r.status for r in results}


class TestUnitId(unittest.TestCase):
    def test_unique_passes(self):
        """Unique unit ids across the units array pass."""
        c = ctx(units=[{"id": "A"}, {"id": "B"}])
        self.assertEqual(V._check_unit_id_uniqueness(c)[0].status, "pass")

    def test_duplicate_fails(self):
        """A duplicated unit id fails with error severity."""
        c = ctx(units=[{"id": "A"}, {"id": "A"}])
        r = V._check_unit_id_uniqueness(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "error"))


class TestUtilityResultRefs(unittest.TestCase):
    def test_resolving_key_passes(self):
        """A utility_consumption_results key that resolves to a declared utility id passes."""
        c = ctx(units=[{"id": "U", "utility_consumption_results": {"steam": 1.0}}],
                utilities={"heat_utilities": [{"id": "steam"}]})
        self.assertEqual(V._check_utility_result_refs(c)[0].status, "pass")

    def test_dangling_key_fails(self):
        """A utility_consumption_results key with no matching utility id fails with error severity."""
        c = ctx(units=[{"id": "U", "utility_consumption_results": {"ghost": 1.0}}],
                utilities={"heat_utilities": []})
        r = V._check_utility_result_refs(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "error"))

    def test_no_results_skips(self):
        """A unit with no utility_consumption_results skips the check."""
        c = ctx(units=[{"id": "U"}], utilities={})
        self.assertEqual(V._check_utility_result_refs(c)[0].status, "skip")


class TestDesignResultPairing(unittest.TestCase):
    def test_paired_passes(self):
        """Every design_results key with a matching quantity_units_for_design_results entry passes."""
        c = ctx(units=[{"id": "U", "design_results": {"Area": 1.0},
                        "quantity_units_for_design_results": {"Area": "m2"}}])
        self.assertEqual(V._check_design_result_units_pairing(c)[0].status, "pass")

    def test_missing_unit_is_error(self):
        """A design_results key with no unit entry fails at error severity (UNIT-03, error)."""
        c = ctx(units=[{"id": "U", "design_results": {"Area": 1.0},
                        "quantity_units_for_design_results": {}}])
        sev = statuses(V._check_design_result_units_pairing(c))
        self.assertEqual(sev[("UNIT-03", "error")], "fail")

    def test_orphan_unit_is_warning(self):
        """A quantity_units_for_design_results entry with no matching design_results key fails at warning severity (UNIT-03, warning)."""
        c = ctx(units=[{"id": "U", "design_results": {"Area": 1.0},
                        "quantity_units_for_design_results":
                            {"Area": "m2", "Ghost": "kg"}}])
        sev = statuses(V._check_design_result_units_pairing(c))
        self.assertEqual(sev[("UNIT-03", "warning")], "fail")


class TestReactantRefs(unittest.TestCase):
    def test_resolving_reactant_passes(self):
        """A reaction reactant that resolves to a declared chemical id passes."""
        c = ctx(units=[{"id": "U", "reactions": [{"reactant": "Glucose"}]}],
                chemicals=[{"id": "Glucose"}])
        self.assertEqual(V._check_reaction_reactant_refs(c)[0].status, "pass")

    def test_dangling_reactant_fails(self):
        """A reaction reactant with no matching chemical id fails."""
        c = ctx(units=[{"id": "U", "reactions": [{"reactant": "Nope"}]}],
                chemicals=[{"id": "Glucose"}])
        self.assertEqual(V._check_reaction_reactant_refs(c)[0].status, "fail")


class TestEquationStoichiometryConsistency(unittest.TestCase):
    def _chems(self):
        return [{"id": "Water", "index": 0}, {"id": "Starch", "index": 1},
                {"id": "Glucose", "index": 2}, {"id": "Ethanol", "index": 3},
                {"id": "CO2", "index": 4}]

    def test_agreeing_passes(self):
        """A reaction equation and its stoichiometry dict that agree pass. Mirrors corn's V310: 'Water + Starch -> Glucose'."""
        c = ctx(units=[{"id": "V310", "reactions": [{
            "reactant": "Starch", "equation": "Water + Starch -> Glucose",
            "stoichiometry": {"Water": -1.0, "Starch": -1.0, "Glucose": 1.0}}]}],
            chemicals=self._chems())
        self.assertEqual(
            V._check_reaction_equation_stoichiometry_consistency(c)[0].status,
            "pass")

    def test_scaled_agreement_passes(self):
        """A stoichiometry dict scaled by a constant factor relative to the equation still passes."""
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose", "equation": "Glucose -> 2 Ethanol + 2 CO2",
            "stoichiometry": {"Glucose": -2.0, "Ethanol": 4.0, "CO2": 4.0}}]}],
            chemicals=self._chems())
        self.assertEqual(
            V._check_reaction_equation_stoichiometry_consistency(c)[0].status,
            "pass")

    def test_disagreement_fails(self):
        """A stoichiometry dict whose ratios do not match the equation fails."""
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose", "equation": "Glucose -> 2 Ethanol + 2 CO2",
            "stoichiometry": {"Glucose": -1.0, "Ethanol": 1.0, "CO2": 2.0}}]}],
            chemicals=self._chems())
        self.assertEqual(
            V._check_reaction_equation_stoichiometry_consistency(c)[0].status,
            "fail")

    def test_only_one_representation_skips(self):
        """A reaction with only an equation (no stoichiometry) skips the consistency check."""
        c = ctx(units=[{"id": "U", "reactions": [
            {"reactant": "Glucose", "equation": "Glucose -> Yeast"}]}],
            chemicals=self._chems())
        self.assertEqual(
            V._check_reaction_equation_stoichiometry_consistency(c)[0].status,
            "skip")


class TestStoichiometryWellformed(unittest.TestCase):
    def test_dict_reactant_negative_passes(self):
        """A dict-form stoichiometry whose reactant entry is negative passes."""
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose",
            "stoichiometry": {"Glucose": -1.0, "Ethanol": 2.0}}]}],
            chemicals=[{"id": "Glucose", "index": 0}, {"id": "Ethanol", "index": 1}])
        self.assertEqual(V._check_stoichiometry_wellformed(c)[0].status, "pass")

    def test_reactant_nonnegative_fails(self):
        """A dict-form stoichiometry whose reactant entry is non-negative fails."""
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose",
            "stoichiometry": {"Glucose": 1.0, "Ethanol": 2.0}}]}],
            chemicals=[{"id": "Glucose", "index": 0}, {"id": "Ethanol", "index": 1}])
        self.assertEqual(V._check_stoichiometry_wellformed(c)[0].status, "fail")

    def test_array_wrong_length_fails(self):
        """An array-form stoichiometry whose length doesn't match the chemicals array fails."""
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose", "stoichiometry": [-1.0]}]}],
            chemicals=[{"id": "Glucose", "index": 0}, {"id": "Ethanol", "index": 1}])
        self.assertEqual(V._check_stoichiometry_wellformed(c)[0].status, "fail")

    def test_unresolvable_key_fails(self):
        """A dict-form stoichiometry key with no matching chemical id fails."""
        c = ctx(units=[{"id": "U", "reactions": [{
            "reactant": "Glucose", "stoichiometry": {"Ghost": -1.0}}]}],
            chemicals=[{"id": "Glucose", "index": 0}])
        self.assertEqual(V._check_stoichiometry_wellformed(c)[0].status, "fail")


class TestUnitConnectivity(unittest.TestCase):
    def test_connected_passes(self):
        """A unit referenced as a stream endpoint passes connectivity."""
        c = ctx(units=[{"id": "U"}],
                streams=[{"id": "s", "source_unit_id": "None", "sink_unit_id": "U"}])
        self.assertEqual(V._check_unit_connectivity(c)[0].status, "pass")

    def test_orphan_is_warning(self):
        """A unit referenced by no stream endpoint fails at warning severity."""
        c = ctx(units=[{"id": "U"}, {"id": "LONELY"}],
                streams=[{"id": "s", "source_unit_id": "None", "sink_unit_id": "U"}])
        r = V._check_unit_connectivity(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))


#%% ------- Streams (STR-01..10, STR-13) ------- ##

def sp(**kw):
    base = {"total_mass_flow": 1.0, "total_molar_flow": 1.0,
            "temperature": 300.0, "pressure": 1e5,
            "phases": {"l": {"total_molar_flow": 1.0, "composition": [
                {"component_name": "W", "mol_fraction": 1.0}]}}}
    base.update(kw)
    return base


class TestStreamId(unittest.TestCase):
    def test_unique_passes(self):
        """Unique stream ids across the streams array pass."""
        c = ctx(streams=[{"id": "a"}, {"id": "b"}])
        self.assertEqual(V._check_stream_id_uniqueness(c)[0].status, "pass")

    def test_duplicate_fails(self):
        """A duplicated stream id fails."""
        c = ctx(streams=[{"id": "a"}, {"id": "a"}])
        self.assertEqual(V._check_stream_id_uniqueness(c)[0].status, "fail")


class TestEndpointRefs(unittest.TestCase):
    def test_boundary_and_unit_pass(self):
        """A boundary ('None') source and a resolving sink unit id both pass."""
        c = ctx(units=[{"id": "U"}],
                streams=[{"id": "s", "source_unit_id": "None", "sink_unit_id": "U"}])
        self.assertEqual(V._check_stream_endpoint_refs(c)[0].status, "pass")

    def test_unknown_endpoint_fails(self):
        """An endpoint referencing a unit id absent from the units array fails."""
        c = ctx(units=[{"id": "U"}],
                streams=[{"id": "s", "source_unit_id": "U", "sink_unit_id": "Z"}])
        self.assertEqual(V._check_stream_endpoint_refs(c)[0].status, "fail")


class TestIsolatedStreamEmpty(unittest.TestCase):
    def test_isolated_empty_passes(self):
        """A stream with both endpoints 'None' and zero flow passes (isolated but empty)."""
        c = ctx(streams=[{"id": "s", "source_unit_id": "None",
                          "sink_unit_id": "None", "stream_properties": {
                              "total_mass_flow": 0.0, "total_molar_flow": 0.0,
                              "phases": {}}}])
        self.assertEqual(V._check_isolated_stream_empty(c)[0].status, "pass")

    def test_isolated_with_flow_fails(self):
        """A stream with both endpoints 'None' but nonzero flow fails."""
        c = ctx(chemicals=[{"id": "W"}],
                streams=[{"id": "s", "source_unit_id": "None",
                          "sink_unit_id": "None", "stream_properties": sp()}])
        self.assertEqual(V._check_isolated_stream_empty(c)[0].status, "fail")

    def test_no_isolated_skips(self):
        """With no doubly-isolated stream present, the check skips."""
        c = ctx(units=[{"id": "U"}],
                streams=[{"id": "s", "source_unit_id": "None", "sink_unit_id": "U",
                          "stream_properties": sp()}], chemicals=[{"id": "W"}])
        self.assertEqual(V._check_isolated_stream_empty(c)[0].status, "skip")


class TestTopologyRole(unittest.TestCase):
    def test_one_topology_role_passes(self):
        """A roles array with exactly one topology role (e.g. 'input') passes."""
        c = ctx(streams=[{"id": "s", "roles": ["input", "feedstock"]}])
        self.assertEqual(V._check_stream_topology_role(c)[0].status, "pass")

    def test_two_topology_roles_fail(self):
        """A roles array with two topology roles ('input' and 'output') fails."""
        c = ctx(streams=[{"id": "s", "roles": ["input", "output"]}])
        self.assertEqual(V._check_stream_topology_role(c)[0].status, "fail")

    def test_no_roles_skips(self):
        """A stream with no roles array skips the topology-role check."""
        c = ctx(streams=[{"id": "s"}])
        self.assertEqual(V._check_stream_topology_role(c)[0].status, "skip")


class TestRoleTopologyAgreement(unittest.TestCase):
    def test_input_matches_no_source(self):
        """An 'input' role on a stream with a 'None' source (boundary) passes."""
        c = ctx(units=[{"id": "U"}], streams=[{
            "id": "s", "source_unit_id": "None", "sink_unit_id": "U",
            "roles": ["input"]}])
        self.assertEqual(V._check_stream_role_topology_agreement(c)[0].status,
                         "pass")

    def test_mismatch_is_warning(self):
        """An 'internal' role on a stream whose topology is actually a boundary input fails at warning severity."""
        c = ctx(units=[{"id": "U"}], streams=[{
            "id": "s", "source_unit_id": "None", "sink_unit_id": "U",
            "roles": ["internal"]}])
        r = V._check_stream_role_topology_agreement(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))


class TestDesignationRoles(unittest.TestCase):
    def test_legal_designation_passes(self):
        """An 'input' stream additionally tagged 'feedstock' passes."""
        c = ctx(streams=[{"id": "s", "roles": ["input", "feedstock"]}])
        self.assertEqual(V._check_stream_designation_roles(c)[0].status, "pass")

    def test_product_on_input_is_warning(self):
        """An 'input' stream tagged 'product' (an illegal designation for an input) fails at warning severity."""
        c = ctx(streams=[{"id": "s", "roles": ["input", "product"]}])
        r = V._check_stream_designation_roles(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))


class TestCompositionComponentRefs(unittest.TestCase):
    def test_resolving_component_passes(self):
        """A composition component_name that resolves to a declared chemical id passes."""
        c = ctx(chemicals=[{"id": "W"}], streams=[{
            "id": "s", "stream_properties": sp()}])
        self.assertEqual(V._check_composition_component_refs(c)[0].status, "pass")

    def test_dangling_component_fails(self):
        """A composition component_name with no matching chemical id fails."""
        c = ctx(chemicals=[{"id": "Other"}], streams=[{
            "id": "s", "stream_properties": sp()}])
        self.assertEqual(V._check_composition_component_refs(c)[0].status, "fail")


class TestZeroFlowConsistency(unittest.TestCase):
    def test_all_zero_empty_passes(self):
        """A stream with zero mass and molar flow and no phases passes."""
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "total_mass_flow": 0.0, "total_molar_flow": 0.0, "phases": {}}}])
        self.assertEqual(V._check_zero_flow_consistency(c)[0].status, "pass")

    def test_zero_mass_nonzero_molar_fails(self):
        """A stream with zero mass flow but nonzero molar flow fails (inconsistent)."""
        c = ctx(chemicals=[{"id": "W"}], streams=[{"id": "s",
                "stream_properties": sp(total_mass_flow=0.0)}])
        self.assertEqual(V._check_zero_flow_consistency(c)[0].status, "fail")

    def test_all_nonzero_skips(self):
        """A stream with nonzero mass and molar flow skips the zero-flow-consistency check."""
        c = ctx(chemicals=[{"id": "W"}],
                streams=[{"id": "s", "stream_properties": sp()}])
        self.assertEqual(V._check_zero_flow_consistency(c)[0].status, "skip")


class TestFractionSums(unittest.TestCase):
    def _stream(self, fracs):
        comp = [{"component_name": f"C{i}", "mol_fraction": f}
                for i, f in enumerate(fracs)]
        return {"id": "s", "stream_properties": {
            "phases": {"l": {"total_molar_flow": 1.0, "composition": comp}}}}

    def test_sum_one_passes(self):
        """mol_fraction entries summing to 1.0 pass."""
        c = ctx(streams=[self._stream([0.4, 0.6])])
        self.assertEqual(V._check_fraction_sums(c)[0].status, "pass")

    def test_sum_off_is_warning(self):
        """mol_fraction entries summing away from 1.0 fail at warning severity."""
        c = ctx(streams=[self._stream([0.4, 0.4])])
        r = V._check_fraction_sums(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_empty_skips(self):
        """An empty composition array with zero total_molar_flow skips the check."""
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "phases": {"l": {"total_molar_flow": 0.0, "composition": []}}}}])
        self.assertEqual(V._check_fraction_sums(c)[0].status, "skip")

    def test_partial_mass_fraction_does_not_false_fail(self):
        """One entry omitting mass_fraction (schema-optional) does not false-fail the mass_fraction sub-check while mol_fraction sums to 1.0 pass. Regression: one entry omits mass_fraction (schema-optional per entry) while both mol_fraction values sum to 1.0. The mass_fraction sub-check must be skipped rather than summing a partial 0.6 and false-failing against 1.0."""
        comp = [{"component_name": "C0", "mol_fraction": 0.4, "mass_fraction": 0.6},
                {"component_name": "C1", "mol_fraction": 0.6}]
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "phases": {"l": {"total_molar_flow": 1.0, "composition": comp}}}}])
        self.assertEqual(V._check_fraction_sums(c)[0].status, "pass")


class TestPhaseFlowSums(unittest.TestCase):
    def test_phase_totals_sum_passes(self):
        """Per-phase total_molar_flow values summing to the stream-level total pass."""
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "total_molar_flow": 3.0,
            "phases": {"l": {"total_molar_flow": 1.0, "composition": []},
                       "g": {"total_molar_flow": 2.0, "composition": []}}}}])
        self.assertEqual(V._check_phase_flow_sums(c)[0].status, "pass")

    def test_mismatch_is_warning(self):
        """Per-phase total_molar_flow values that don't sum to the stream-level total fail at warning severity."""
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "total_molar_flow": 5.0,
            "phases": {"l": {"total_molar_flow": 1.0, "composition": []},
                       "g": {"total_molar_flow": 2.0, "composition": []}}}}])
        r = V._check_phase_flow_sums(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_partial_mass_flow_does_not_false_fail(self):
        """A phase omitting total_mass_flow does not false-fail the mass-flow sub-check; it is skipped rather than compared as a partial sum. Regression: one phase omits total_mass_flow (schema-optional per phase) while both phases declare total_molar_flow summing to the stream total. The true stream-level total_mass_flow (25.0) does NOT equal the numeric-only phase subset (10.0), so a naive partial sum would false-fail; total_mass_flow must instead be skipped entirely (not all phases declare it), while total_molar_flow still passes."""
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "total_mass_flow": 25.0, "total_molar_flow": 3.0,
            "phases": {"l": {"total_molar_flow": 1.0, "composition": []},
                       "g": {"total_mass_flow": 10.0, "total_molar_flow": 2.0,
                             "composition": []}}}}])
        r = V._check_phase_flow_sums(c)[0]
        self.assertNotEqual(r.status, "fail")


class TestRelClose(unittest.TestCase):
    def test_tiny_value_vs_exact_zero_is_close(self):
        """A value at/below ZERO_FLOW compared to exact zero counts as agreement. Regression: with b=0, a!=0 the old formula reduced to abs(a) <= rel_tol * abs(a), i.e. 1 <= rel_tol, never true for a sub-1 tolerance."""
        self.assertIs(V._rel_close(1e-15, 0.0, V.TOL_FLOW), True)

    def test_real_mismatch_vs_zero_still_fails(self):
        """A genuinely nonzero value compared to exact zero is not close."""
        self.assertIs(V._rel_close(1.0, 0.0, V.TOL_FLOW), False)


#%% ------- Chemicals (CHEM-01, CHEM-04, CHEM-05) ------- ##

class TestChemIdIndexUniqueness(unittest.TestCase):
    def test_unique_passes(self):
        """Unique chemical ids and indices pass."""
        c = ctx(chemicals=[{"id": "A", "index": 0}, {"id": "B", "index": 1}])
        self.assertEqual(V._check_chemical_id_index_uniqueness(c)[0].status, "pass")

    def test_duplicate_id_fails(self):
        """A duplicated chemical id fails."""
        c = ctx(chemicals=[{"id": "A"}, {"id": "A"}])
        self.assertEqual(V._check_chemical_id_index_uniqueness(c)[0].status, "fail")

    def test_duplicate_index_fails(self):
        """A duplicated chemical index fails."""
        c = ctx(chemicals=[{"id": "A", "index": 0}, {"id": "B", "index": 0}])
        self.assertEqual(V._check_chemical_id_index_uniqueness(c)[0].status, "fail")


class TestIndexCoverage(unittest.TestCase):
    def test_id_keyed_stoichiometry_skips(self):
        """A stoichiometry dict keyed by chemical id (not index-based) skips the index-coverage check. Corn's shape: stoichiometry keyed by chemical id -> not index-based."""
        c = ctx(chemicals=[{"id": "A"}, {"id": "B"}],
                units=[{"id": "U", "reactions": [
                    {"reactant": "A", "stoichiometry": {"A": -1, "B": 1}}]}])
        self.assertEqual(V._check_index_coverage(c)[0].status, "skip")

    def test_array_stoichiometry_with_full_indices_passes(self):
        """An array-form stoichiometry where every chemical declares an index passes."""
        c = ctx(chemicals=[{"id": "A", "index": 0}, {"id": "B", "index": 1}],
                units=[{"id": "U", "reactions": [
                    {"reactant": "A", "stoichiometry": [-1, 1]}]}])
        self.assertEqual(V._check_index_coverage(c)[0].status, "pass")

    def test_array_stoichiometry_missing_index_fails(self):
        """An array-form stoichiometry where a chemical lacks an index fails."""
        c = ctx(chemicals=[{"id": "A", "index": 0}, {"id": "B"}],
                units=[{"id": "U", "reactions": [
                    {"reactant": "A", "stoichiometry": [-1, 1]}]}])
        self.assertEqual(V._check_index_coverage(c)[0].status, "fail")


class TestUnusedChemicals(unittest.TestCase):
    def test_referenced_passes(self):
        """A chemical referenced in at least one stream composition passes."""
        c = ctx(chemicals=[{"id": "W"}], streams=[{"id": "s",
                "stream_properties": {"phases": {"l": {"total_molar_flow": 1.0,
                    "composition": [{"component_name": "W", "mol_fraction": 1.0}]}}}}])
        self.assertEqual(V._check_unused_chemicals(c)[0].status, "pass")

    def test_unreferenced_is_info(self):
        """A chemical referenced nowhere fails at info severity."""
        c = ctx(chemicals=[{"id": "W"}, {"id": "GHOST"}], streams=[{"id": "s",
                "stream_properties": {"phases": {"l": {"total_molar_flow": 1.0,
                    "composition": [{"component_name": "W", "mol_fraction": 1.0}]}}}}])
        r = V._check_unused_chemicals(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "info"))

    def test_empty_chemicals_is_vacuous_pass(self):
        """An empty chemicals registry is a vacuous pass, not a skip. sff_checks.md CHEM-05: 'Skipped when: never'."""
        c = ctx(chemicals=[])
        r = V._check_unused_chemicals(c)[0]
        self.assertEqual((r.status, r.severity), ("pass", "info"))


#%% ------- Utilities (UTIL-01, UTIL-02, UTIL-04) ------- ##

class TestUtilityIdUniqueness(unittest.TestCase):
    def test_unique_across_groups_passes(self):
        """Utility ids unique across heat/power/other groups pass."""
        c = ctx(utilities={"heat_utilities": [{"id": "steam"}],
                           "power_utilities": [{"id": "grid"}]})
        self.assertEqual(V._check_utility_id_uniqueness(c)[0].status, "pass")

    def test_collision_across_groups_fails(self):
        """A utility id shared across heat/power groups fails."""
        c = ctx(utilities={"heat_utilities": [{"id": "x"}],
                           "power_utilities": [{"id": "x"}]})
        self.assertEqual(V._check_utility_id_uniqueness(c)[0].status, "fail")

    def test_no_utilities_is_vacuous_pass(self):
        """An empty utilities registry is a genuine pass, not a skip. Skipped when: never (sff_checks.md) -- an empty utilities registry has no duplicates."""
        c = ctx(utilities={})
        self.assertEqual(V._check_utility_id_uniqueness(c)[0].status, "pass")


class TestUnusedUtilities(unittest.TestCase):
    def test_used_passes(self):
        """A utility referenced in a unit's utility_consumption_results passes."""
        c = ctx(utilities={"power_utilities": [{"id": "grid"}]},
                units=[{"id": "U", "utility_consumption_results": {"grid": 1.0}}])
        self.assertEqual(V._check_unused_utilities(c)[0].status, "pass")

    def test_unused_is_info(self):
        """A utility referenced by no unit fails at info severity."""
        c = ctx(utilities={"power_utilities": [{"id": "grid"}]},
                units=[{"id": "U"}])
        r = V._check_unused_utilities(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "info"))

    def test_no_utilities_is_vacuous_pass(self):
        """Zero utilities are trivially all referenced, so this is a genuine pass, not a skip. Skipped when: never (sff_checks.md)."""
        c = ctx(utilities={}, units=[{"id": "U"}])
        self.assertEqual(V._check_unused_utilities(c)[0].status, "pass")


class TestUtilityComposition(unittest.TestCase):
    def test_valid_composition_passes(self):
        """A utility composition whose component_name resolves and whose fractions sum correctly passes."""
        c = ctx(chemicals=[{"id": "Water"}], utilities={"heat_utilities": [{
            "id": "steam", "composition": [
                {"component_name": "Water", "mol_fraction": 1.0}]}]})
        self.assertEqual(V._check_utility_composition(c)[0].status, "pass")

    def test_dangling_component_is_error(self):
        """A utility composition component_name with no matching chemical id fails at error severity (UTIL-04, error)."""
        c = ctx(chemicals=[{"id": "Water"}], utilities={"heat_utilities": [{
            "id": "steam", "composition": [
                {"component_name": "Ghost", "mol_fraction": 1.0}]}]})
        sev = {(r.check_id, r.severity): r.status
               for r in V._check_utility_composition(c)}
        self.assertEqual(sev[("UTIL-04", "error")], "fail")

    def test_bad_fraction_sum_is_warning(self):
        """A utility composition whose mol_fraction entries don't sum to 1.0 fails at warning severity (UTIL-04, warning)."""
        c = ctx(chemicals=[{"id": "Water"}], utilities={"heat_utilities": [{
            "id": "steam", "composition": [
                {"component_name": "Water", "mol_fraction": 0.5}]}]})
        sev = {(r.check_id, r.severity): r.status
               for r in V._check_utility_composition(c)}
        self.assertEqual(sev[("UTIL-04", "warning")], "fail")

    def test_no_composition_is_skipped(self):
        """A utility with no composition array skips the check. Skipped when: the composition array is empty/absent (sff_checks.md)."""
        c = ctx(chemicals=[{"id": "Water"}],
                utilities={"heat_utilities": [{"id": "steam"}]})
        self.assertEqual(V._check_utility_composition(c)[0].status, "skip")


#%% ------- Quantity units (QU-01, QU-03, QU-04) ------- ##

def stream_with_mass():
    return {"id": "s", "source_unit_id": "None", "sink_unit_id": "U",
            "stream_properties": {"total_mass_flow": 1.0, "total_molar_flow": 1.0,
                                  "temperature": 300.0, "pressure": 1e5,
                                  "phases": {"l": {"total_molar_flow": 1.0,
                                                   "composition": []}}}}


class TestQuantityUnitPairing(unittest.TestCase):
    def test_resolvable_field_passes(self):
        """Every present quantity field resolving to a quantity_units_global alias passes."""
        c = ctx(streams=[stream_with_mass()], quantity_units_global={
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": "kg/hr"},
            "molar_flow": {"aliases": ["total_molar_flow"], "quantity_units": "kmol/hr"},
            "temperature": {"aliases": ["temperature"], "quantity_units": "K"},
            "pressure": {"aliases": ["pressure"], "quantity_units": "Pa"}})
        self.assertEqual(V._check_quantity_unit_pairing(c)[0].status, "pass")

    def test_unresolvable_field_fails(self):
        """A present quantity field with no matching alias entry fails. total_mass_flow has no alias entry."""
        c = ctx(streams=[stream_with_mass()], quantity_units_global={
            "molar_flow": {"aliases": ["total_molar_flow"], "quantity_units": "kmol/hr"},
            "temperature": {"aliases": ["temperature"], "quantity_units": "K"},
            "pressure": {"aliases": ["pressure"], "quantity_units": "Pa"}})
        self.assertEqual(V._check_quantity_unit_pairing(c)[0].status, "fail")

    def test_no_quantity_fields_present_is_vacuous_pass(self):
        """Zero present quantity fields is a vacuous pass, not a skip. QU-01 is 'Skipped when: never' (sff_checks.md) -- every one of zero fields is trivially resolvable."""
        c = ctx()
        r = V._check_quantity_unit_pairing(c)[0]
        self.assertEqual(r.status, "pass")


class TestAliasUniqueness(unittest.TestCase):
    def test_disjoint_aliases_pass(self):
        """Aliases with no overlap across quantity_units_global entries pass."""
        c = ctx(quantity_units_global={
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": "kg/hr"},
            "molar_flow": {"aliases": ["total_molar_flow"], "quantity_units": "kmol/hr"}})
        self.assertEqual(V._check_alias_uniqueness(c)[0].status, "pass")

    def test_shared_alias_fails(self):
        """The same alias appearing in two distinct quantity_units_global entries fails."""
        c = ctx(quantity_units_global={
            "mass_flow": {"aliases": ["F"], "quantity_units": "kg/hr"},
            "molar_flow": {"aliases": ["F"], "quantity_units": "kmol/hr"}})
        self.assertEqual(V._check_alias_uniqueness(c)[0].status, "fail")

    def test_empty_registry_is_vacuous_pass(self):
        """An empty quantity_units_global registry has no ambiguous aliases, so it is a vacuous pass. QU-03 is 'Skipped when: never' (sff_checks.md)."""
        c = ctx()
        self.assertEqual(V._check_alias_uniqueness(c)[0].status, "pass")

    def test_duplicate_alias_within_one_entry_is_not_a_collision(self):
        """A repeated alias value within a single entry's own aliases list is not a collision. An entry's own `aliases` list may legally repeat a value (the schema has no uniqueItems on `aliases`). QU-03 is about an alias spanning more than one DISTINCT entry."""
        c = ctx(quantity_units_global={
            "mass_flow": {"aliases": ["F", "F"], "quantity_units": "kg/hr"}})
        self.assertEqual(V._check_alias_uniqueness(c)[0].status, "pass")


class TestUnusedAliases(unittest.TestCase):
    def test_entry_used_passes(self):
        """An entry used via any one of its alias synonyms passes, even if other synonyms are unused. mass_flow entry is used via the total_mass_flow synonym; synonyms mass_flow / F_mass being unused does NOT flag the entry."""
        c = ctx(streams=[stream_with_mass()], quantity_units_global={
            "mass_flow": {"aliases": ["mass_flow", "total_mass_flow", "F_mass"],
                          "quantity_units": "kg/hr"},
            "molar_flow": {"aliases": ["total_molar_flow"], "quantity_units": "kmol/hr"},
            "temperature": {"aliases": ["temperature"], "quantity_units": "K"},
            "pressure": {"aliases": ["pressure"], "quantity_units": "Pa"}})
        self.assertEqual(V._check_unused_aliases(c)[0].status, "pass")

    def test_entry_unused_is_info(self):
        """An entry whose alias is never referenced by any stream field fails at info severity. No stream declares total_volumetric_flow -> that entry is unused."""
        c = ctx(streams=[stream_with_mass()], quantity_units_global={
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": "kg/hr"},
            "molar_flow": {"aliases": ["total_molar_flow"], "quantity_units": "kmol/hr"},
            "temperature": {"aliases": ["temperature"], "quantity_units": "K"},
            "pressure": {"aliases": ["pressure"], "quantity_units": "Pa"},
            "volumetric_flow": {"aliases": ["total_volumetric_flow"],
                                "quantity_units": "m3/hr"}})
        r = V._check_unused_aliases(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "info"))

    def test_empty_registry_is_vacuous_pass(self):
        """An empty quantity_units_global registry has no unused entries, so it is a vacuous pass. QU-04 is 'Skipped when: never' (sff_checks.md)."""
        c = ctx()
        r = V._check_unused_aliases(c)[0]
        self.assertEqual(r.status, "pass")


#%% ------- Cross-object (MET-02, MET-03, GRAPH-01, XREF-01) ------- ##

class TestMetadataStreamRefs(unittest.TestCase):
    def test_resolving_refs_pass(self):
        """metadata.feedstocks/products stream_id refs that resolve to declared streams pass."""
        c = ctx(metadata={"feedstocks": [{"stream_id": "corn"}],
                          "products": [{"stream_id": "eth"}]},
                streams=[{"id": "corn"}, {"id": "eth"}])
        self.assertEqual(V._check_metadata_stream_refs(c)[0].status, "pass")

    def test_dangling_ref_fails(self):
        """A metadata stream_id ref with no matching stream id fails."""
        c = ctx(metadata={"feedstocks": [{"stream_id": "ghost"}]},
                streams=[{"id": "corn"}])
        self.assertEqual(V._check_metadata_stream_refs(c)[0].status, "fail")

    def test_no_refs_skips(self):
        """Metadata with no feedstocks/products refs skips the check."""
        c = ctx(metadata={}, streams=[{"id": "corn"}])
        self.assertEqual(V._check_metadata_stream_refs(c)[0].status, "skip")


class TestMetadataRoleAgreement(unittest.TestCase):
    def test_agreeing_roles_pass(self):
        """A feedstock-referenced stream that also carries the 'feedstock' role passes."""
        c = ctx(metadata={"feedstocks": [{"stream_id": "corn"}]},
                streams=[{"id": "corn", "roles": ["input", "feedstock"]}])
        self.assertEqual(V._check_metadata_role_agreement(c)[0].status, "pass")

    def test_missing_role_is_warning(self):
        """A feedstock-referenced stream missing the 'feedstock' role fails at warning severity."""
        c = ctx(metadata={"feedstocks": [{"stream_id": "corn"}]},
                streams=[{"id": "corn", "roles": ["input"]}])
        r = V._check_metadata_role_agreement(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_no_roles_skips(self):
        """A feedstock-referenced stream with no roles array skips the check."""
        c = ctx(metadata={"feedstocks": [{"stream_id": "corn"}]},
                streams=[{"id": "corn"}])
        self.assertEqual(V._check_metadata_role_agreement(c)[0].status, "skip")


class TestTeaYearPlausible(unittest.TestCase):
    def test_plausible_year_passes(self):
        """A plausible TEA_year (e.g. 2020) passes."""
        c = ctx(metadata={"TEA_year": 2020})
        self.assertEqual(V._check_tea_year_plausible(c)[0].status, "pass")

    def test_absent_year_skips(self):
        """No TEA_year present skips the check (warning severity, skip status)."""
        c = ctx(metadata={})
        r = V._check_tea_year_plausible(c)[0]
        self.assertEqual((r.status, r.severity), ("skip", "warning"))

    def test_year_zero_is_warning(self):
        """TEA_year == 0 fails at warning severity."""
        c = ctx(metadata={"TEA_year": 0})
        r = V._check_tea_year_plausible(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_far_future_year_is_warning(self):
        """An implausibly far-future TEA_year (e.g. 20000) fails at warning severity."""
        c = ctx(metadata={"TEA_year": 20000})
        r = V._check_tea_year_plausible(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_current_plus_one_is_allowed(self):
        """A TEA_year one year ahead of the current year is allowed (passes)."""
        c = ctx(metadata={"TEA_year": _dt.date.today().year + 1})
        self.assertEqual(V._check_tea_year_plausible(c)[0].status, "pass")


class TestBoundaryStreamsExist(unittest.TestCase):
    def test_both_boundaries_pass(self):
        """At least one boundary input stream and one boundary output stream passes."""
        c = ctx(streams=[{"id": "a", "source_unit_id": "None", "sink_unit_id": "U"},
                         {"id": "b", "source_unit_id": "U", "sink_unit_id": "None"}])
        self.assertEqual(V._check_boundary_streams_exist(c)[0].status, "pass")

    def test_no_output_is_warning(self):
        """No boundary output stream fails at warning severity."""
        c = ctx(streams=[{"id": "a", "source_unit_id": "None", "sink_unit_id": "U"}])
        r = V._check_boundary_streams_exist(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_no_streams_is_warning_not_skip(self):
        """An empty streams array fails at warning severity rather than skipping."""
        c = ctx(streams=[])
        r = V._check_boundary_streams_exist(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))


class TestXrefGate(unittest.TestCase):
    def test_passes_when_no_referential_fail(self):
        """The XREF-01 aggregate passes when no referential check produced an error-severity fail."""
        results = [V._passed("STR-02", "error"), V._failed("STR-08", "warning", "x")]
        self.assertEqual(V._xref_gate(results).status, "pass")

    def test_fails_when_a_referential_check_fails(self):
        """The XREF-01 aggregate fails when a referential check produced an error-severity fail."""
        results = [V._failed("STR-07", "error", "dangling component")]
        self.assertEqual(V._xref_gate(results).status, "fail")


#%% ------- Foundation (result model, _Context, entry point) ------- ##

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "pisces_sff" / "schema" / "sff_schema.json"
CORN_PATH = (REPO_ROOT / "pisces_sff" / "exported_flowsheets"
             / "bioindustrial_park" / "corn_dry_grind_ethanol.json")


def minimal_doc():
    return {
        "metadata": {"sff_version": "0.0.12", "TEA_currency": "USD",
                     "TEA_year": 2020,
                     "process_simulator": {"name": "BioSTEAM", "version": "2.46.1"},
                     "feedstocks": [{"stream_id": "s1"}],
                     "products": [{"stream_id": "s1"}]},
        "quantity_units_global": {},
        "units": [{"id": "U1", "unit_type": "Mixer"}],
        "streams": [{"id": "s1", "source_unit_id": "None", "sink_unit_id": "U1",
                     "stream_properties": {
                         "total_mass_flow": 1.0, "total_molar_flow": 1.0,
                         "temperature": 300.0, "pressure": 101325.0,
                         "phases": {"l": {"total_molar_flow": 1.0,
                                          "composition": []}}}}],
        "chemicals": [],
        "utilities": {"heat_utilities": [], "power_utilities": [],
                      "other_utilities": []},
    }


class TestCheckResult(unittest.TestCase):
    def test_fields(self):
        """A CheckResult namedtuple exposes check_id, severity, and status as given."""
        r = V.CheckResult("X-01", "error", "fail", "boom", "streams.0")
        self.assertEqual(r.check_id, "X-01")
        self.assertEqual(r.severity, "error")
        self.assertEqual(r.status, "fail")


class TestContext(unittest.TestCase):
    def test_indexes_built(self):
        """`_Context` built from a minimal doc exposes the expected unit_ids/stream_ids/util_ids indexes."""
        ctx = V._Context(minimal_doc())
        self.assertEqual(ctx.unit_ids, {"U1"})
        self.assertEqual(ctx.stream_ids, {"s1"})
        self.assertEqual(ctx.util_ids, set())

    def test_tolerates_missing_sections(self):
        """`_Context` built from an empty dict does not raise and exposes empty defaults."""
        ctx = V._Context({})  # nothing present; must not raise
        self.assertEqual(ctx.units, [])
        self.assertEqual(ctx.chem_by_id, {})


class TestEntryPoint(unittest.TestCase):
    def _write(self, doc):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(doc, tmp)
        tmp.close()
        return tmp.name

    def test_schema_invalid_doc_is_invalid(self):
        """A document missing a required field (TEA_currency) is reported schema-invalid via a SCHEMA-tagged fail result."""
        doc = minimal_doc()
        del doc["metadata"]["TEA_currency"]  # required -> schema fail
        path = self._write(doc)
        is_valid, results = V.validate_flowsheet_against_SFF(path, str(SCHEMA_PATH))
        self.assertFalse(is_valid)
        self.assertTrue(any(r.check_id == "SCHEMA" and r.status == "fail"
                            for r in results))


#%% ------- Coverage-only: private module-level helpers (Task 3.1) ------- ##
# These private helpers back the _check_* functions exercised above, but are
# not yet exercised UNDER THEIR OWN NAME anywhere in Tier 1. Each is called
# directly here on a small synthetic input. _molar_mass_from_formula and the
# formula-driven paths of CHEM-03/STR-10 use the real, lightweight `chemicals`
# formula parser (confirmed importable standalone in ~0.1s, touching neither
# sys.modules['thermosteam'] nor ['biosteam']) rather than a fake -- so these
# are genuine assertions, not vacuous ones. _unit_is_parseable itself (and the
# substantive parseable/unparseable behavior of QU-02/UTIL-03, which depends
# on it) is the one helper in this file's neighborhood that stays real-only
# and exempted in test_coverage_meta.py: it needs the real
# thermosteam.units_of_measure pint registry, which the Tier-1 biosteam stub
# (installed by test_export_helpers.py, running first alphabetically) poisons
# for the rest of the pytest process -- see tests/_stub_eviction.py. QU-02 and
# UTIL-03's documented vacuous-pass-on-no-input path never reaches
# _unit_is_parseable, so that specific behavior is exercised for real below.


class TestDuplicatesHelper(unittest.TestCase):
    def test_values_appearing_more_than_once_are_reported(self):
        """_duplicates returns the set of values that appear more than once."""
        self.assertEqual(V._duplicates(["a", "b", "a", "c", "c", "c"]), {"a", "c"})

    def test_none_values_are_ignored(self):
        """A repeated None never counts as a duplicate."""
        self.assertEqual(V._duplicates([None, None, "x"]), set())

    def test_no_repeats_returns_empty_set(self):
        """All-distinct values return an empty set."""
        self.assertEqual(V._duplicates(["a", "b"]), set())


class TestAliasIndexHelper(unittest.TestCase):
    def test_maps_each_alias_to_its_declaring_entry_keys(self):
        """_alias_index maps an alias shared by two entries to both entry keys,
        and an alias declared by only one entry to just that key."""
        c = ctx(quantity_units_global={
            "mass_flow": {"aliases": ["F", "total_mass_flow"]},
            "molar_flow": {"aliases": ["F"]}})
        idx = V._alias_index(c)
        self.assertEqual(sorted(idx["F"]), ["mass_flow", "molar_flow"])
        self.assertEqual(idx["total_mass_flow"], ["mass_flow"])

    def test_empty_registry_yields_empty_index(self):
        """An empty quantity_units_global registry indexes no aliases."""
        self.assertEqual(V._alias_index(ctx()), {})


class TestExpectedTopologyHelper(unittest.TestCase):
    def test_both_endpoints_present_is_internal(self):
        """A stream with a real source and a real sink is 'internal'."""
        self.assertEqual(
            V._expected_topology({"source_unit_id": "A", "sink_unit_id": "B"}),
            "internal")

    def test_sink_only_is_input(self):
        """A boundary source with a real sink is 'input'."""
        self.assertEqual(
            V._expected_topology({"source_unit_id": "None", "sink_unit_id": "B"}),
            "input")

    def test_source_only_is_output(self):
        """A real source with a boundary sink is 'output'."""
        self.assertEqual(
            V._expected_topology({"source_unit_id": "A", "sink_unit_id": "None"}),
            "output")

    def test_doubly_isolated_is_none(self):
        """A doubly-boundary stream (STR-03 territory) has no expected topology."""
        self.assertIsNone(
            V._expected_topology({"source_unit_id": "None", "sink_unit_id": "None"}))


class TestIterNamedCompositionsHelper(unittest.TestCase):
    def test_yields_phase_symbol_and_its_composition_list(self):
        """_iter_named_compositions yields (phase_symbol, composition_list)
        pairs, one per declared phase."""
        stream = {"stream_properties": {"phases": {
            "l": {"composition": [{"component_name": "W"}]}}}}
        self.assertEqual(list(V._iter_named_compositions(stream)),
                         [("l", [{"component_name": "W"}])])

    def test_no_phases_yields_nothing(self):
        """A stream with no phases block yields no compositions."""
        self.assertEqual(list(V._iter_named_compositions({})), [])


class TestIterQuantityUnitStringsHelper(unittest.TestCase):
    def test_yields_global_design_and_utility_result_entries(self):
        """_iter_quantity_unit_strings yields one (location, unit_string,
        empty_allowed) tuple per quantity_units_global entry, per unit
        design-result unit, and per utility result-unit string; only the
        design-result entries allow an empty string."""
        c = ctx(
            quantity_units_global={"mass_flow": {"quantity_units": "kg/hr"}},
            units=[{"id": "U", "quantity_units_for_design_results": {"Area": "m2"}}],
            utilities={"heat_utilities": [
                {"id": "steam", "quantity_units_for_utility_results": "kJ/hr"}]})
        got = list(V._iter_quantity_unit_strings(c))
        self.assertIn(("quantity_units_global.mass_flow", "kg/hr", False), got)
        self.assertIn(("U.design['Area']", "m2", True), got)
        self.assertIn(("steam.utility_results", "kJ/hr", False), got)


class TestIterReactionsHelper(unittest.TestCase):
    def test_yields_unit_reaction_pairs_in_order(self):
        """_iter_reactions yields (unit, reaction) for every reaction dict,
        in declaration order."""
        c = ctx(units=[{"id": "U", "reactions": [
            {"reactant": "A"}, {"reactant": "B"}]}])
        got = list(V._iter_reactions(c))
        self.assertEqual([r["reactant"] for _, r in got], ["A", "B"])
        self.assertTrue(all(u["id"] == "U" for u, _ in got))

    def test_unit_without_reactions_yields_nothing(self):
        """A unit with no reactions array contributes no pairs."""
        self.assertEqual(list(V._iter_reactions(ctx(units=[{"id": "U"}]))), [])


class TestMeanMolarMassHelper(unittest.TestCase):
    def test_weighted_average_over_declared_molar_masses(self):
        """_mean_molar_mass computes the mol-fraction-weighted average molar
        mass over a phase composition."""
        c = ctx(chemicals=[{"id": "W", "molar_mass": 18.0},
                           {"id": "E", "molar_mass": 46.0}])
        comp = [{"component_name": "W", "mol_fraction": 0.5},
                {"component_name": "E", "mol_fraction": 0.5}]
        self.assertAlmostEqual(V._mean_molar_mass(comp, c), 32.0)

    def test_unresolvable_component_returns_none(self):
        """A composition entry whose component has no resolvable molar mass
        makes the whole average unresolvable (None)."""
        c = ctx(chemicals=[])
        comp = [{"component_name": "Ghost", "mol_fraction": 1.0}]
        self.assertIsNone(V._mean_molar_mass(comp, c))


class TestParseEquationHelper(unittest.TestCase):
    def test_parses_simple_equation_to_signed_coefficients(self):
        """_parse_equation turns 'A + B -> C' into LHS-negative/RHS-positive
        coefficients keyed by chemical id."""
        c = ctx(chemicals=[{"id": "A"}, {"id": "B"}, {"id": "C"}])
        self.assertEqual(V._parse_equation("A + B -> C", c),
                         {"A": -1.0, "B": -1.0, "C": 1.0})

    def test_parses_leading_numeric_coefficients(self):
        """A leading numeric coefficient on a species term is honored."""
        c = ctx(chemicals=[{"id": "A"}, {"id": "B"}])
        self.assertEqual(V._parse_equation("A -> 2 B", c), {"A": -1.0, "B": 2.0})

    def test_missing_arrow_returns_none(self):
        """An equation with no '->' cannot be parsed."""
        c = ctx(chemicals=[{"id": "A"}])
        self.assertIsNone(V._parse_equation("A", c))

    def test_unresolvable_species_returns_none(self):
        """A species that resolves to no declared chemical id returns None."""
        c = ctx(chemicals=[{"id": "A"}])
        self.assertIsNone(V._parse_equation("A -> Ghost", c))


class TestPresentGlobalQuantityFieldsHelper(unittest.TestCase):
    def test_detects_present_stream_price_and_chemical_fields(self):
        """_present_global_quantity_fields reports every quantity field that
        appears as a numeric value somewhere in the document."""
        c = ctx(streams=[{"price": 1.0,
                          "stream_properties": {"temperature": 300.0}}],
                chemicals=[{"molar_mass": 18.0}])
        self.assertEqual(V._present_global_quantity_fields(c),
                         {"price", "temperature", "molar_mass"})

    def test_empty_document_reports_no_fields(self):
        """An empty document has no present quantity fields."""
        self.assertEqual(V._present_global_quantity_fields(ctx()), set())


class TestReactionUsesIndexStoichiometryHelper(unittest.TestCase):
    def test_array_form_is_always_index_based(self):
        """An array-form stoichiometry is always index-based."""
        c = ctx(chemicals=[{"id": "A"}])
        self.assertTrue(
            V._reaction_uses_index_stoichiometry({"stoichiometry": [-1, 1]}, c))

    def test_id_keyed_dict_is_not_index_based(self):
        """A dict-form stoichiometry keyed entirely by chemical id is not
        index-based (Corn's shape)."""
        c = ctx(chemicals=[{"id": "A"}, {"id": "B"}])
        self.assertFalse(V._reaction_uses_index_stoichiometry(
            {"stoichiometry": {"A": -1, "B": 1}}, c))

    def test_dict_with_an_unresolvable_key_is_index_based(self):
        """A dict-form stoichiometry key that is not a chemical id is treated
        as an index reference."""
        c = ctx(chemicals=[{"id": "A"}])
        self.assertTrue(
            V._reaction_uses_index_stoichiometry({"stoichiometry": {"0": -1}}, c))

    def test_no_stoichiometry_is_not_index_based(self):
        """A reaction without a stoichiometry field is not index-based."""
        self.assertFalse(V._reaction_uses_index_stoichiometry({}, ctx()))


class TestReferencedChemicalIdsHelper(unittest.TestCase):
    def test_collects_refs_from_composition_and_reactions(self):
        """_referenced_chemical_ids unions chemical ids referenced by stream
        composition and reaction reactants."""
        c = ctx(chemicals=[{"id": "W"}, {"id": "E"}],
                streams=[{"stream_properties": {"phases": {"l": {
                    "composition": [{"component_name": "W"}]}}}}],
                units=[{"reactions": [{"reactant": "E"}]}])
        self.assertEqual(V._referenced_chemical_ids(c), {"W", "E"})


class TestSameReactionUpToScaleHelper(unittest.TestCase):
    def test_agreeing_up_to_a_positive_scale_factor(self):
        """Two coefficient maps describing the same reaction at different
        (positive) scales agree."""
        self.assertTrue(V._same_reaction_up_to_scale(
            {"A": -1.0, "B": 1.0}, {"A": -2.0, "B": 2.0}))

    def test_disagreeing_ratios_do_not_match(self):
        """Coefficient maps whose ratios differ do not agree."""
        self.assertFalse(V._same_reaction_up_to_scale(
            {"A": -1.0, "B": 1.0}, {"A": -1.0, "B": 2.0}))

    def test_different_component_sets_disagree(self):
        """Coefficient maps over different component sets can never agree."""
        self.assertFalse(V._same_reaction_up_to_scale(
            {"A": -1.0}, {"A": -1.0, "B": 1.0}))


class TestStoichToCoeffsHelper(unittest.TestCase):
    def test_array_form_resolves_by_declared_index(self):
        """An array-form stoichiometry resolves each array position via the
        chemical declaring that index."""
        c = ctx(chemicals=[{"id": "A", "index": 0}, {"id": "B", "index": 1}])
        coeffs, err = V._stoich_to_coeffs([-1.0, 1.0], c)
        self.assertIsNone(err)
        self.assertEqual(coeffs, {"A": -1.0, "B": 1.0})

    def test_dict_form_resolves_by_id(self):
        """A dict-form stoichiometry resolves keys directly as chemical ids."""
        c = ctx(chemicals=[{"id": "A"}])
        coeffs, err = V._stoich_to_coeffs({"A": -1.0}, c)
        self.assertIsNone(err)
        self.assertEqual(coeffs, {"A": -1.0})

    def test_wrong_length_array_is_reported_as_an_error(self):
        """An array whose length disagrees with the chemicals array returns
        (None, reason) rather than a partial mapping."""
        c = ctx(chemicals=[{"id": "A", "index": 0}])
        coeffs, err = V._stoich_to_coeffs([-1.0, 1.0], c)
        self.assertIsNone(coeffs)
        self.assertIsNotNone(err)

    def test_neither_list_nor_dict_is_an_error(self):
        """A stoichiometry that is neither a list nor a dict returns
        (None, reason)."""
        coeffs, err = V._stoich_to_coeffs("nonsense", ctx())
        self.assertIsNone(coeffs)
        self.assertIsNotNone(err)


class TestStreamCompositionsHelper(unittest.TestCase):
    def test_yields_each_phases_composition_list(self):
        """_stream_compositions yields the composition list of every phase."""
        stream = {"stream_properties": {"phases": {
            "l": {"composition": [{"component_name": "W"}]}}}}
        self.assertEqual(list(V._stream_compositions(stream)),
                         [[{"component_name": "W"}]])

    def test_non_dict_stream_yields_nothing(self):
        """A non-dict stream (defensive branch) yields no compositions."""
        self.assertEqual(list(V._stream_compositions("not a stream")), [])


class TestStreamFlowScalarsHelper(unittest.TestCase):
    def test_yields_stream_and_phase_level_numeric_flows(self):
        """_stream_flow_scalars yields both the stream-level and every
        phase-level flow scalar."""
        stream = {"stream_properties": {"total_mass_flow": 1.0, "phases": {
            "l": {"total_mass_flow": 0.5}}}}
        self.assertEqual(sorted(V._stream_flow_scalars(stream)), [0.5, 1.0])

    def test_non_numeric_or_absent_values_are_skipped(self):
        """A stream with no flow fields (only temperature) yields nothing."""
        stream = {"stream_properties": {"temperature": 300.0, "phases": {}}}
        self.assertEqual(list(V._stream_flow_scalars(stream)), [])


class TestStreamIsEmptyHelper(unittest.TestCase):
    def test_zero_flow_and_no_composition_is_empty(self):
        """A stream with only ~zero flow scalars and no composition entries
        is empty."""
        stream = {"stream_properties": {"total_mass_flow": 0.0, "phases": {}}}
        self.assertTrue(V._stream_is_empty(stream))

    def test_nonzero_flow_is_not_empty(self):
        """A stream with a nonzero flow scalar is not empty."""
        stream = {"stream_properties": {"total_mass_flow": 5.0, "phases": {}}}
        self.assertFalse(V._stream_is_empty(stream))


class TestValidateJsonAgainstSchemaHelper(unittest.TestCase):
    def test_the_current_shape_corpus_file_is_schema_valid(self):
        """validate_json_against_schema reports the current-shape reference
        corpus file (corn_dry_grind_ethanol.json) as schema-valid, no errors."""
        is_valid, errors = V.validate_json_against_schema(str(CORN_PATH), str(SCHEMA_PATH))
        self.assertTrue(is_valid)
        self.assertEqual(errors, [])

    def test_a_document_missing_a_required_field_is_invalid(self):
        """A document missing the required metadata.TEA_currency field is
        reported invalid with a non-empty list of human-readable errors."""
        doc = minimal_doc()
        del doc["metadata"]["TEA_currency"]
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(doc, tmp)
        tmp.close()
        is_valid, errors = V.validate_json_against_schema(tmp.name, str(SCHEMA_PATH))
        self.assertFalse(is_valid)
        self.assertTrue(errors)


class TestMolarMassFromFormulaHelper(unittest.TestCase):
    def test_parses_a_known_formula(self):
        """_molar_mass_from_formula('H2O') resolves to water's molar mass via
        the real, lightweight `chemicals` formula parser (~18.015 g/mol)."""
        self.assertAlmostEqual(V._molar_mass_from_formula("H2O"), 18.01528, places=3)

    def test_unparseable_formula_returns_none(self):
        """A formula the parser recognizes no atoms in returns None rather
        than a bogus zero mass."""
        self.assertIsNone(V._molar_mass_from_formula("not a formula"))


class TestCheckFormulaMolarMassAgreementHelper(unittest.TestCase):
    def test_agreeing_formula_and_declared_mass_pass(self):
        """CHEM-03: a formula-derived molar mass that agrees with the
        declared value (within TOL_MOLAR_MASS) passes."""
        c = ctx(chemicals=[{"id": "W", "formula": "H2O", "molar_mass": 18.01528}])
        self.assertEqual(
            V._check_formula_molar_mass_agreement(c)[0].status, "pass")

    def test_disagreeing_formula_and_declared_mass_is_a_warning(self):
        """CHEM-03: a formula/declared-mass disagreement fails at warning
        severity."""
        c = ctx(chemicals=[{"id": "W", "formula": "H2O", "molar_mass": 99.0}])
        r = V._check_formula_molar_mass_agreement(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_no_formula_declared_mass_pair_skips(self):
        """A chemical with a declared molar_mass but no formula (or vice
        versa) contributes no checkable pair -> skip."""
        c = ctx(chemicals=[{"id": "W", "molar_mass": 18.0}])
        self.assertEqual(
            V._check_formula_molar_mass_agreement(c)[0].status, "skip")


class TestCheckMassMolarFlowConsistencyHelper(unittest.TestCase):
    def _ctx(self, mass, molar):
        # Single-component water phase, declared molar mass (no formula
        # parsing needed): M-bar = 18.01528 g/mol.
        return ctx(
            chemicals=[{"id": "W", "molar_mass": 18.01528}],
            streams=[{"id": "s", "stream_properties": {
                "total_mass_flow": mass, "total_molar_flow": molar,
                "phases": {"l": {"total_mass_flow": mass, "total_molar_flow": molar,
                                 "composition": [{"component_name": "W",
                                                  "mol_fraction": 1.0}]}}}}])

    def test_consistent_mass_and_molar_flow_passes(self):
        """STR-10: mass flow equal to molar flow * mean molar mass passes."""
        c = self._ctx(mass=18.01528, molar=1.0)
        self.assertEqual(
            V._check_mass_molar_flow_consistency(c)[0].status, "pass")

    def test_inconsistent_mass_and_molar_flow_is_a_warning(self):
        """STR-10: a mass flow that disagrees with molar flow * mean molar
        mass fails at warning severity."""
        c = self._ctx(mass=100.0, molar=1.0)
        r = V._check_mass_molar_flow_consistency(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_unresolvable_molar_mass_skips(self):
        """A component with no resolvable molar mass (no declared value, no
        formula) makes that phase unresolvable -> skip."""
        c = ctx(
            chemicals=[{"id": "X"}],
            streams=[{"id": "s", "stream_properties": {
                "total_mass_flow": 5.0, "total_molar_flow": 1.0,
                "phases": {"l": {"total_mass_flow": 5.0, "total_molar_flow": 1.0,
                                 "composition": [{"component_name": "X",
                                                  "mol_fraction": 1.0}]}}}}])
        self.assertEqual(
            V._check_mass_molar_flow_consistency(c)[0].status, "skip")


class TestCheckQuantityUnitStringsParseableVacuousPass(unittest.TestCase):
    def test_no_quantity_unit_strings_present_is_a_vacuous_pass(self):
        """QU-02 is 'Skipped when: never' (sff_checks.md): zero present
        quantity-unit strings is a vacuous pass, not a skip. This path never
        reaches _unit_is_parseable (the loop body never executes), so it is
        exercisable without the real thermosteam unit registry."""
        self.assertEqual(
            V._check_quantity_unit_strings_parseable(ctx())[0].status, "pass")


class TestCheckUtilityResultUnitsParseableVacuousPass(unittest.TestCase):
    def test_no_utilities_present_is_a_vacuous_pass(self):
        """UTIL-03 is 'Skipped when: never' (sff_checks.md): an empty
        utilities registry is a vacuous pass. This path never reaches
        _unit_is_parseable (the loop body never executes), so it is
        exercisable without the real thermosteam unit registry."""
        c = ctx(utilities={})
        self.assertEqual(
            V._check_utility_result_units_parseable(c)[0].status, "pass")


if __name__ == "__main__":
    unittest.main()
