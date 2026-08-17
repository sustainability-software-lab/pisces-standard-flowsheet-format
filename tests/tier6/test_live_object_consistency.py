# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 6: in-process live-object consistency guard. Loads the corn model
# in-process (no subprocess/harness), simulates it, exports it in-process to a
# temp file, and asserts the JSON values equal the SAME live biosteam/
# thermosteam objects the exporter read them from, within RTOL=1e-9. This is a
# self-consistency check, not a numeric-baseline check (that is Tier 6's
# sibling, test_end_to_end_export.py, which asserts against recorded
# baselines at RTOL=1e-4): the export and the assertions here read the same
# objects in the same process moments apart, so any daylight between them is a
# genuine export bug, not simulator drift.
#
# Every assertion below mirrors the *exact* expression pisces_sff/_export.py
# used to produce the corresponding JSON value (see the comment atop each test
# for the source line numbers) -- reading a value a different way would make a
# 1e-9 tolerance meaningless.
#
# Gated on SFF_TEST_TIER6 (default on) because it runs a real, un-mocked corn
# biorefinery simulation in this process. Run it with:
#
#     $env:SFF_TEST_TIER6 = "1"
#     & "C:\Users\saran\anaconda3\envs\HP_2024\python.exe" -m pytest tests/tier6/test_live_object_consistency.py -q
#
# Must not run concurrently with any other simulating test (shared numba
# cache); the sibling Tier 6 file documents the same constraint.

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

from tests._gating import RUN_TIER6
from tests._stub_eviction import RealBiosteamTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = (REPO_ROOT / "pisces_sff" / "models" / "biosteam_models"
             / "corn_dry_grind_ethanol")

#: Self-consistency tolerance: the export and these assertions read the same
#: live objects in the same process moments apart, so values should be
#: essentially bit-identical modulo the JSON round-trip.
RTOL = 1e-9

#: Minimum fraction of doc["streams"] entries that must map back to a live,
#: named stream (see test_stream_scalars_match_live_streams). Guards against a
#: stream-id-mapping regression that would silently match zero streams and
#: make tests (b)/(c) vacuously pass. Observed on the corn system: 104/108
#: streams (~96.3%) map back to a live named stream, so a high floor is safe
#: while leaving headroom for a handful of blank-ID auxiliary streams.
_MIN_MATCHED_STREAM_FRACTION = 0.85


def _isclose(actual, expected):
    return math.isclose(actual, expected, rel_tol=RTOL, abs_tol=1e-12)


@unittest.skipUnless(RUN_TIER6, "set SFF_TEST_TIER6=1 (default on) to run; builds/uses the pinned env")
class TestLiveObjectConsistency(RealBiosteamTestCase):
    RTOL = RTOL

    #: Module roots purged from sys.modules before the in-process corn load (see
    #: setUpClass). "pisces_sff" evicts the package tree that tier1 collection
    #: bound to the fake biosteam stub. The simulator roots evict the REAL
    #: biosteam/thermosteam/biorefineries stack that earlier tiers left in a
    #: split-identity state: tier2 builds real biosteam systems and tiers 4/5 run
    #: the real validator (which re-imports thermosteam submodules), and across
    #: that churn two distinct thermosteam.Chemical classes end up resident. If
    #: corn's chemicals are built against one while bst.settings.set_thermo uses
    #: the other, thermosteam's `isinstance(chemicals, Chemicals)` fails and the
    #: load dies with "'Chemical' object has no attribute 'strip'". Purging the
    #: whole stack forces `from biorefineries import corn` below to rebuild one
    #: internally consistent import graph. Safe because this class is the only
    #: in-process consumer of the real simulator after these tiers and nothing
    #: runs after it (the sibling tier6 test exports in a subprocess).
    _PURGE_ROOTS = ("pisces_sff", "biosteam", "thermosteam", "biorefineries",
                    "thermo", "chemicals", "fluids", "flexsolve")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()   # layer 1: evict any Tier-1 biosteam/thermosteam stub
        # layer 2: purge the pisces_sff tree AND the real simulator stack so the
        # in-process corn load below rebuilds a single consistent module graph.
        for key in [k for k in sys.modules
                    if k in cls._PURGE_ROOTS
                    or any(k.startswith(r + ".") for r in cls._PURGE_ROOTS)]:
            del sys.modules[key]

        from pisces_sff._runner import load_model_module
        from pisces_sff._export import export_biosteam_flowsheet
        from pisces_sff._version import read_schema_version

        module = load_model_module(MODEL_DIR)          # file-path import of load.py
        export_kwargs = getattr(module, "EXPORT_KWARGS", None) or {}
        cls.system, cls.tea = module.load()             # already simulated; never re-simulate
        cls.tmp = tempfile.TemporaryDirectory()
        out_path = Path(cls.tmp.name) / "corn.json"
        export_biosteam_flowsheet(
            cls.system, str(out_path),
            sff_version=read_schema_version(), tea=cls.tea, **export_kwargs)
        cls.doc = json.loads(out_path.read_text(encoding="utf-8"))

        # Shared live-object indexes, built once and reused by every test.
        cls.live_streams_by_id = {s.ID: s for s in cls.system.streams if s.ID}
        cls.live_units_by_id = {u.ID: u for u in cls.system.units}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assertNumClose(self, actual, expected, msg):
        self.assertTrue(
            _isclose(actual, expected),
            msg=f"{msg}: got {actual!r}, live value {expected!r}",
        )

    # ------- (a) chemicals: _export.py lines 413-427 -------

    def test_chemical_molar_masses_match_live_objects(self):
        """Each JSON chemical's molar_mass equals the live thermo Chemical.MW
        within RTOL (mirrors _export.py: repr_stream = all_streams[0]; chems =
        repr_stream.chemicals; chemical["molar_mass"] = c.MW), matched by id
        (JSON chemical["id"] == c.ID)."""
        repr_stream = list(self.system.streams)[0]
        live_mw_by_id = {c.ID: c.MW for c in repr_stream.chemicals}
        chemicals = self.doc["chemicals"]
        self.assertTrue(chemicals, "doc['chemicals'] is empty; nothing to compare")
        for chem in chemicals:
            with self.subTest(chemical=chem["id"]):
                self.assertIn(chem["id"], live_mw_by_id)
                self.assertNumClose(
                    chem["molar_mass"], live_mw_by_id[chem["id"]],
                    f"chemical {chem['id']} molar_mass")

    # ------- (b) stream scalars: _export.py lines 366-371 -------

    def test_stream_scalars_match_live_streams(self):
        """Each JSON stream's total_mass_flow/total_molar_flow/temperature/
        pressure equal the live Stream's F_mass/F_mol/T/P within RTOL (mirrors
        _export.py stream_properties assembly at lines 366-371), for every
        stream whose id maps to a live named stream (live_by_id = {s.ID: s for
        s in system.streams if s.ID}; blank-ID streams get a synthesized id
        that cannot be mapped back and are skipped here)."""
        streams = self.doc["streams"]
        self.assertTrue(streams, "doc['streams'] is empty; nothing to compare")
        matched = [s for s in streams if s["id"] in self.live_streams_by_id]
        floor = _MIN_MATCHED_STREAM_FRACTION * len(streams)
        self.assertGreaterEqual(
            len(matched), floor,
            f"only {len(matched)}/{len(streams)} streams mapped back to a "
            f"live named stream (floor {floor:.1f}); the id mapping may be "
            "silently matching almost nothing.")
        for stream in matched:
            live = self.live_streams_by_id[stream["id"]]
            sp = stream["stream_properties"]
            with self.subTest(stream=stream["id"]):
                self.assertNumClose(sp["total_mass_flow"], live.F_mass,
                                    f"stream {stream['id']} total_mass_flow")
                self.assertNumClose(sp["total_molar_flow"], live.F_mol,
                                    f"stream {stream['id']} total_molar_flow")
                self.assertNumClose(sp["temperature"], live.T,
                                    f"stream {stream['id']} temperature")
                self.assertNumClose(sp["pressure"], live.P,
                                    f"stream {stream['id']} pressure")

    # ------- (c) stream composition: _export.py get_phase_properties, lines 1088-1116 -------

    def test_stream_compositions_match_live_streams(self):
        """Each JSON stream's per-phase composition mole/mass fractions equal
        the live Stream's phase-local fractions within RTOL (mirrors
        get_phase_properties: for each phase symbol p in stream.phases,
        sp = stream[p]; mol_fraction = sp.imol[c]/sp.F_mol; mass_fraction =
        sp.imass[c]/sp.F_mass), for the same mapped streams as test (b)."""
        streams = self.doc["streams"]
        matched = [s for s in streams if s["id"] in self.live_streams_by_id]
        compared_any = False
        for stream in matched:
            live = self.live_streams_by_id[stream["id"]]
            phases = stream["stream_properties"].get("phases", {})
            for phase_symbol, phase_data in phases.items():
                sp = live[phase_symbol]
                for entry in phase_data["composition"]:
                    compared_any = True
                    name = entry["component_name"]
                    with self.subTest(stream=stream["id"], phase=phase_symbol,
                                      component=name):
                        self.assertNumClose(
                            entry["mol_fraction"], sp.imol[name] / sp.F_mol,
                            f"{stream['id']}[{phase_symbol}] {name} mol_fraction")
                        self.assertNumClose(
                            entry["mass_fraction"], sp.imass[name] / sp.F_mass,
                            f"{stream['id']}[{phase_symbol}] {name} mass_fraction")
        self.assertTrue(compared_any,
                        "no composition entries were compared across any "
                        "mapped stream; the vacuity guard tripped")

    # ------- (d) unit design_results: _export.py lines 341, 347 -------

    def test_unit_design_results_match_live_units(self):
        """Each JSON unit's design_results dict equals the live unit's
        design_results within RTOL for numeric values, and by exact equality
        for non-numeric values (mirrors _export.py: unit["id"] = ru.ID;
        unit["design_results"] = ru.design_results if hasattr(ru,
        'design_results') else {})."""
        units = self.doc["units"]
        self.assertTrue(units, "doc['units'] is empty; nothing to compare")
        compared_any = False
        for unit in units:
            live = self.live_units_by_id.get(unit["id"])
            with self.subTest(unit=unit["id"]):
                self.assertIsNotNone(live, f"no live unit found for id {unit['id']!r}")
                live_design_results = (live.design_results
                                       if hasattr(live, "design_results") else {})
                self.assertEqual(set(unit["design_results"].keys()),
                                 set(live_design_results.keys()))
                for key, value in unit["design_results"].items():
                    compared_any = True
                    live_value = live_design_results[key]
                    if isinstance(value, bool) or isinstance(live_value, bool):
                        self.assertEqual(value, live_value,
                                         f"unit {unit['id']} design_results[{key!r}]")
                    elif isinstance(value, (int, float)) and isinstance(live_value, (int, float)):
                        self.assertNumClose(
                            value, live_value,
                            f"unit {unit['id']} design_results[{key!r}]")
                    else:
                        self.assertEqual(value, live_value,
                                         f"unit {unit['id']} design_results[{key!r}]")
        self.assertTrue(compared_any,
                        "no design_results entries were compared across any "
                        "unit; the vacuity guard tripped")

    # ------- (e) utility duties: independently reconstructed from live utility
    #             objects, mirroring _export.py get_utility_results (lines 990-1033)
    #             without calling it -------

    @staticmethod
    def _expected_utility_dicts(unit):
        """Reconstruct the expected utility_consumption_results /
        utility_production_results for a live unit by reading its
        heat_utilities / power_utility / natural_gas attributes directly --
        the same attributes get_utility_results (_export.py lines 990-1033)
        reads, guarded with the same hasattr checks, but coded independently
        here so this is a genuine cross-check rather than a call to the
        helper under test."""
        cons, prod = {}, {}

        hus = unit.heat_utilities if hasattr(unit, "heat_utilities") else {}
        for hu in hus:
            if hu.agent is None:
                continue
            if hu.duty > 0:
                cons[hu.agent.ID] = cons.get(hu.agent.ID, 0.0) + hu.duty
            else:
                prod[hu.agent.ID] = prod.get(hu.agent.ID, 0.0) + hu.duty

        if hasattr(unit, "power_utility"):
            pu = unit.power_utility
            if pu.consumption > 0:
                cons["Marginal grid electricity"] = pu.consumption
            if pu.production > 0:
                prod["Marginal grid electricity"] = pu.production

        if hasattr(unit, "natural_gas"):
            ou = unit.natural_gas
            if ou.F_mass > 0:
                cons[ou.ID] = cons.get(ou.ID, 0.0) + ou.F_mass
            else:
                prod[ou.ID] = prod.get(ou.ID, 0.0) + ou.F_mass

        return cons, prod

    def test_utility_duties_match_live_units(self):
        """Each JSON unit's utility_consumption_results / utility_production_
        results equal a dict independently reconstructed from the live unit's
        heat_utilities/power_utility/natural_gas attributes within RTOL (see
        _expected_utility_dicts, mirroring _export.py's get_utility_results
        without calling it)."""
        units = self.doc["units"]
        saw_nonempty = False
        for unit in units:
            live = self.live_units_by_id.get(unit["id"])
            if live is None:
                continue
            expected_cons, expected_prod = self._expected_utility_dicts(live)
            actual_cons = unit["utility_consumption_results"]
            actual_prod = unit["utility_production_results"]
            if expected_cons or expected_prod or actual_cons or actual_prod:
                saw_nonempty = True
            with self.subTest(unit=unit["id"], side="consumption"):
                self.assertEqual(set(actual_cons.keys()), set(expected_cons.keys()))
                for agent_id, expected_value in expected_cons.items():
                    self.assertNumClose(
                        actual_cons[agent_id], expected_value,
                        f"unit {unit['id']} utility_consumption_results[{agent_id!r}]")
            with self.subTest(unit=unit["id"], side="production"):
                self.assertEqual(set(actual_prod.keys()), set(expected_prod.keys()))
                for agent_id, expected_value in expected_prod.items():
                    self.assertNumClose(
                        actual_prod[agent_id], expected_value,
                        f"unit {unit['id']} utility_production_results[{agent_id!r}]")
        self.assertTrue(
            saw_nonempty,
            "every unit's utility_consumption_results/utility_production_results "
            "(actual and reconstructed-expected) were empty; this assertion "
            "would be vacuous")

    # ------- (f) purchase-cost correlations: _export.py
    #             get_purchase_cost_correlations vs live cost_items -------

    def test_purchase_cost_correlations_match_live_units(self):
        """Each JSON unit's purchase_cost_correlations equal values read straight
        from the live unit's cost_items / F_BM (mirrors
        get_purchase_cost_correlations without calling it): correlation_type from
        item.f, basis/basis_units from item.basis/item.units, reference_size=S,
        reference_cost=cost and exponent=n for power_law, reference_CE_index=CE,
        installation_factor=F_BM[id], power_rate=kW -- within RTOL."""
        compared_any = False
        for unit in self.doc["units"]:
            corr = unit.get("purchase_cost_correlations")
            if not corr:
                continue
            live = self.live_units_by_id.get(unit["id"])
            self.assertIsNotNone(live, f"no live unit for id {unit['id']!r}")
            live_items = getattr(live, "cost_items", {}) or {}
            live_fbm = getattr(live, "F_BM", {}) or {}
            for item_id, entry in corr.items():
                self.assertIn(item_id, live_items,
                              f"{unit['id']}: '{item_id}' not in live cost_items")
                item = live_items[item_id]
                compared_any = True
                with self.subTest(unit=unit["id"], item=item_id):
                    is_custom = getattr(item, "f", None) is not None
                    self.assertEqual(
                        entry["correlation_type"],
                        "custom_function" if is_custom else "power_law")
                    self.assertEqual(entry["basis"], str(item.basis))
                    self.assertEqual(entry["basis_units"], str(item.units))
                    self.assertNumClose(entry["reference_size"], float(item.S),
                                        f"{item_id} reference_size")
                    self.assertNumClose(entry["reference_CE_index"], float(item.CE),
                                        f"{item_id} reference_CE_index")
                    self.assertNumClose(entry["installation_factor"],
                                        float(live_fbm.get(item_id, 1.0)),
                                        f"{item_id} installation_factor")
                    self.assertNumClose(entry["power_rate"],
                                        float(getattr(item, "kW", 0.0) or 0.0),
                                        f"{item_id} power_rate")
                    if not is_custom:
                        self.assertNumClose(entry["reference_cost"],
                                            float(item.cost),
                                            f"{item_id} reference_cost")
                        self.assertNumClose(entry["exponent"], float(item.n),
                                            f"{item_id} exponent")
        self.assertTrue(compared_any,
                        "no purchase_cost_correlations were compared across any "
                        "unit; the corn model was expected to have @cost units")


if __name__ == "__main__":
    unittest.main()
