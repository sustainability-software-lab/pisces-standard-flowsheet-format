# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 2: the full validator over the committed corn corpus file. Runs every
# check (QU-02 imports thermosteam; STR-10/CHEM-03 import chemicals), so gated on
# SFF_TEST_BIOSTEAM=1. Asserts the reference corpus is clean at error severity and
# that a deliberately-broken variant is caught.

import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATE_PATH = REPO_ROOT / "pisces_sff" / "_validate.py"
SCHEMA_PATH = REPO_ROOT / "pisces_sff" / "schema" / "sff_schema.json"
CORN_PATH = (REPO_ROOT / "pisces_sff" / "exported_flowsheets"
             / "bioindustrial_park" / "corn_dry_grind_ethanol.json")
RUN = os.environ.get("SFF_TEST_BIOSTEAM") == "1"


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_corpus_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(RUN, "set SFF_TEST_BIOSTEAM=1 (imports thermosteam/chemicals)")
class TestCornCorpusIntegration(unittest.TestCase):
    def setUp(self):
        self.V = load_validate_module()

    def _write(self, doc):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(doc, tmp)
        tmp.close()
        return tmp.name

    def test_corn_is_valid_with_no_error_findings(self):
        is_valid, results = self.V.validate_flowsheet_against_SFF(
            str(CORN_PATH), str(SCHEMA_PATH))
        error_fails = [r for r in results
                       if r.status == "fail" and r.severity == "error"]
        self.assertEqual(error_fails, [],
                         f"unexpected error findings: {error_fails}")
        self.assertTrue(is_valid)

    def test_schema_gate_and_xref_pass_on_corn(self):
        _, results = self.V.validate_flowsheet_against_SFF(
            str(CORN_PATH), str(SCHEMA_PATH))
        by_id = {r.check_id: r for r in results}
        self.assertEqual(by_id["SCHEMA"].status, "pass")
        self.assertEqual(by_id["XREF-01"].status, "pass")

    def test_corn_reports_expected_unused_chemicals_info(self):
        # CHEM-05 legitimately flags the four thermo-only chemicals as info; this
        # is advisory, not a failure, and must not affect is_valid.
        _, results = self.V.validate_flowsheet_against_SFF(
            str(CORN_PATH), str(SCHEMA_PATH))
        chem05 = next(r for r in results if r.check_id == "CHEM-05")
        self.assertEqual(chem05.severity, "info")
        for name in ("Cellulose", "H3PO4", "P4O10", "SO2"):
            self.assertIn(name, chem05.message)

    def test_broken_component_ref_is_caught(self):
        doc = json.loads(CORN_PATH.read_text(encoding="utf-8"))
        # Point a composition component at a non-existent chemical.
        broken = copy.deepcopy(doc)
        phase = broken["streams"][0]["stream_properties"]["phases"]
        first = next(iter(phase.values()))
        first["composition"][0]["component_name"] = "NoSuchChemical"
        path = self._write(broken)
        is_valid, results = self.V.validate_flowsheet_against_SFF(
            path, str(SCHEMA_PATH))
        self.assertFalse(is_valid)
        by_id = {r.check_id: r.status for r in results}
        self.assertEqual(by_id["STR-07"], "fail")
        self.assertEqual(by_id["XREF-01"], "fail")


@unittest.skipUnless(RUN, "set SFF_TEST_BIOSTEAM=1 (imports thermosteam/chemicals)")
class TestMinimalValidDoc(unittest.TestCase):
    def setUp(self):
        self.V = load_validate_module()

    def _minimal_valid_doc(self):
        return {
            "metadata": {"sff_version": "0.0.12", "TEA_currency": "USD",
                         "TEA_year": 2020,
                         "process_simulator": {"name": "BioSTEAM", "version": "2.46.1"},
                         "feedstocks": [{"stream_id": "s1"}],
                         "products": [{"stream_id": "s1"}]},
            "quantity_units_global": {
                "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": "kg/hr"},
                "molar_flow": {"aliases": ["total_molar_flow"], "quantity_units": "kmol/hr"},
                "temperature": {"aliases": ["temperature"], "quantity_units": "K"},
                "pressure": {"aliases": ["pressure"], "quantity_units": "Pa"},
            },
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

    def _write(self, doc):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(doc, tmp)
        tmp.close()
        return tmp.name

    def test_minimal_schema_valid_doc_is_valid(self):
        path = self._write(self._minimal_valid_doc())
        is_valid, results = self.V.validate_flowsheet_against_SFF(
            path, str(SCHEMA_PATH))
        error_fails = [r for r in results
                       if r.status == "fail" and r.severity == "error"]
        self.assertEqual(error_fails, [], f"unexpected error findings: {error_fails}")
        self.assertTrue(is_valid)
        by_id = {r.check_id: r for r in results}
        self.assertEqual(by_id["SCHEMA"].status, "pass")


if __name__ == "__main__":
    unittest.main()
