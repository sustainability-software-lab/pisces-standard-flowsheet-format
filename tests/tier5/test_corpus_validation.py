# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 5: an encoded outcome table for the committed corpus. Runs the full
# validate_flowsheet_against_SFF (QU-02 imports thermosteam; STR-10/CHEM-03
# import chemicals) over every *.json in exported_flowsheets/bioindustrial_park
# and asserts each file's is_valid matches its recorded expectation, so an
# unexpected flip fails loudly. Per-check semantic assertions live in Tier 4
# (tests/tier4/test_streams_checks.py, test_chemicals_checks.py,
# test_cross_object_checks.py, test_docs_fixture.py) -- Tier 5's single
# responsibility is this corpus outcome table.

import json
import unittest
from pathlib import Path

from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests._validate_loader import V

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "pisces_sff" / "schema" / "sff_schema.json"
CORPUS_DIR = (REPO_ROOT / "pisces_sff" / "export" / "exported_flowsheets"
              / "bioindustrial_park")

# Recorded outcomes (see canonical validation ss1 in CLAUDE.md): only
# SF_BST_01.json (corn dry-grind ethanol) has been re-exported to the current
# schema shape and validates cleanly; the other 17 are now named
# SF_BST_02..18.json (renamed 2026-08-18, contents untouched), remain
# old-shape and fail the schema gate. This is the known, intended state --
# not a regression.
EXPECTED = {
    "SF_BST_01.json": True,
    "SF_BST_02.json": False,
    "SF_BST_03.json": False,
    "SF_BST_04.json": False,
    "SF_BST_05.json": False,
    "SF_BST_06.json": False,
    "SF_BST_07.json": False,
    "SF_BST_08.json": False,
    "SF_BST_09.json": False,
    "SF_BST_10.json": False,
    "SF_BST_11.json": False,
    "SF_BST_12.json": False,
    "SF_BST_13.json": False,
    "SF_BST_14.json": False,
    "SF_BST_15.json": False,
    "SF_BST_16.json": False,
    "SF_BST_17.json": False,
    "SF_BST_18.json": False,
}


class TestCorpusValidation(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(5)        # gate first (skips before any eviction work)
        super().setUpClass()       # evict the Tier-1 stub so thermosteam is real

    def test_on_disk_set_matches_the_table(self):
        """The set of *.json in exported_flowsheets/bioindustrial_park equals
        EXPECTED's keys -> a newly added/removed corpus file forces a table
        update (fails until then)."""
        on_disk = {p.name for p in CORPUS_DIR.glob("*.json")}
        self.assertEqual(on_disk, set(EXPECTED))

    def test_each_file_matches_its_recorded_outcome(self):
        """Each corpus file's validate_flowsheet_against_SFF is_valid equals its
        recorded EXPECTED value (corn True; the 17 stale files False) -> any
        unexpected flip fails."""
        for name, expected in EXPECTED.items():
            with self.subTest(file=name):
                is_valid, _ = V.validate_flowsheet_against_SFF(
                    str(CORPUS_DIR / name), str(SCHEMA_PATH))
                self.assertEqual(is_valid, expected)


# Registry-driven design-input-spec coverage on the committed corn file
# (SF_BST_01.json, the only corpus file carrying design_input_specs at all).
# JSON-only -- no biosteam/thermosteam import needed, so this class is a plain
# unittest.TestCase rather than RealBiosteamTestCase.
#
# WHY this guard exists: pisces_sff/export/_design_specs.py resolves a unit's specs
# by walking type(unit).__mro__ against the class-name-keyed registry
# (pisces_sff/export/design_specs/biosteam.yaml); a unit whose class (or any
# alias under which the pinned recipe env resolves it) is not listed silently
# gets {} with no error -- resolve_design_input_specs's documented contract for
# "no ancestor is listed" is to return {} rather than raise. That silence bit
# once during this branch's own development: the model recipe's pinned
# biosteam resolved the corn fermenter under the class name
# NRELBatchBioreactor rather than the class the registry was originally keyed
# under, so V405 (the fermenter) re-exported with design_input_specs == {} --
# a totally silent loss of its tau/V/T/P/Nmin/Nmax specs that nothing but a
# pinned re-export would have surfaced. An alias entry for
# NRELBatchBioreactor was added and V405 was restored. This test class pins
# that recovery, and the coverage/no-None invariants around it, against a
# silent regression on a future re-export (e.g. a pinned-env class-name skew
# reintroducing an unresolved unit, or a registry edit that regresses
# coverage) -- so the next time it happens, a test fails loudly instead of a
# unit quietly losing its specs.
class TestDesignSpecCoverage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(5)
        with open(CORPUS_DIR / "SF_BST_01.json", encoding="utf-8") as f:
            cls.doc = json.load(f)
        cls.units = {u["id"]: u for u in cls.doc["units"]}

    def test_exact_set_of_units_with_no_design_input_specs(self):
        """Pins the exact set of units whose design_input_specs == {} --
        computed directly from the committed file. A unit type dropping out
        of (or newly falling out of) registry coverage changes this set, so
        this test forces that change to be a deliberate, reviewed diff rather
        than a silent one."""
        # E313 (JetCooker) left this set when its hand-added registry entry
        # restored the T spec (hand-curated additions, 2026-08-26).
        expected_empty = {
            "MH101", "M104", "MH604", "MH612", "other_facilities",
            "T608",
        }
        actual_empty = {uid for uid, u in self.units.items()
                        if u.get("design_input_specs") == {}}
        self.assertEqual(actual_empty, expected_empty)

    def test_every_pump_has_a_non_none_P(self):
        """Every Pump unit's design_input_specs carries a non-None 'P' --
        the P/outs[0].P fallback curated into the Pump registry entry must
        keep working for every pump in the corpus, not just some."""
        pumps = [u for u in self.units.values()
                if u.get("unit_type") == "Pump"]
        self.assertGreater(len(pumps), 0)
        for u in pumps:
            with self.subTest(unit=u["id"]):
                self.assertIn("P", u.get("design_input_specs", {}))
                self.assertIsNotNone(u["design_input_specs"]["P"])

    def test_V405_carries_the_fermenter_specs(self):
        """V405 (the corn fermenter) must carry at least {tau, V, T, P} --
        the exact specs this branch once lost to a pinned-env class-name
        skew (the recipe's biosteam resolved it as NRELBatchBioreactor,
        which had no registry entry) and then restored by adding that
        alias entry. A regression here means the alias entry was lost or
        the resolved class name changed again. V_wf is a class-attribute
        design value (not an _init param) hand-curated into the alias
        entry -- the generator's signature sweep would never restore it."""
        specs = self.units["V405"].get("design_input_specs", {})
        for param in ("tau", "V", "T", "P", "V_wf"):
            with self.subTest(param=param):
                self.assertIn(param, specs)

    def test_E313_carries_the_jet_cooker_T(self):
        """E313 (JetCooker, a biorefinery-defined class) resolves through a
        hand-added JetCooker registry entry -- biorefinery classes are never
        swept by the generator, so only that hand entry keeps its T spec
        from silently collapsing back to the empty Unit fallback."""
        specs = self.units["E313"].get("design_input_specs", {})
        self.assertIn("T", specs)
        self.assertIsNotNone(specs["T"])

    def test_no_unit_has_a_none_spec_value(self):
        """The 0.1.4+ exporter is documented to omit a param entirely rather
        than ever emit a null design-spec value (_design_specs.py:
        resolve_design_input_specs). Pin that no committed unit violates it."""
        for uid, u in self.units.items():
            specs = u.get("design_input_specs", {})
            for param, value in specs.items():
                with self.subTest(unit=uid, param=param):
                    self.assertIsNotNone(value)


if __name__ == "__main__":
    unittest.main()
