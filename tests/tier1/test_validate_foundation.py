# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: the validator foundation -- result model, _Context, and the
# validate_flowsheet_against_SFF entry point's schema-gate/invalid-doc path.
# Loads _validate.py by path so the heavy pisces_sff package need not import.
# The positive (schema-valid AND fully checks-clean) case now needs the live
# _CHECKS registry, whose QU-01/QU-02 require a populated quantity_units_global
# parsed via thermosteam -- that case lives in Tier 2
# (tests/tier2/test_validate_corpus_integration.py::TestMinimalValidDoc), not
# here, to keep this file import-light.

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATE_PATH = REPO_ROOT / "pisces_sff" / "_validate.py"
SCHEMA_PATH = REPO_ROOT / "pisces_sff" / "schema" / "sff_schema.json"
CORN_PATH = (REPO_ROOT / "pisces_sff" / "exported_flowsheets"
             / "bioindustrial_park" / "corn_dry_grind_ethanol.json")


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = load_validate_module()


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
        r = V.CheckResult("X-01", "error", "fail", "boom", "streams.0")
        self.assertEqual(r.check_id, "X-01")
        self.assertEqual(r.severity, "error")
        self.assertEqual(r.status, "fail")


class TestContext(unittest.TestCase):
    def test_indexes_built(self):
        ctx = V._Context(minimal_doc())
        self.assertEqual(ctx.unit_ids, {"U1"})
        self.assertEqual(ctx.stream_ids, {"s1"})
        self.assertEqual(ctx.util_ids, set())

    def test_tolerates_missing_sections(self):
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
        doc = minimal_doc()
        del doc["metadata"]["TEA_currency"]  # required -> schema fail
        path = self._write(doc)
        is_valid, results = V.validate_flowsheet_against_SFF(path, str(SCHEMA_PATH))
        self.assertFalse(is_valid)
        self.assertTrue(any(r.check_id == "SCHEMA" and r.status == "fail"
                            for r in results))


if __name__ == "__main__":
    unittest.main()
