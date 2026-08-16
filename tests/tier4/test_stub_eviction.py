# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Regression test for Task 1.5.1: Tier 1's install_biosteam_stubs() permanently
replaces sys.modules['thermosteam']/['biosteam'] with fakes, which poisons the
real validator's lazy `from thermosteam.units_of_measure import ureg` import
(QU-02, in pisces_sff/_validate.py::_unit_is_parseable) whenever Tier 1 has
already run earlier in the same pytest process. evict_biosteam_stubs() removes
the fake sys.modules entries so the next import re-loads the real package."""

import sys
import tempfile
import json
import unittest
from pathlib import Path

from tests._docs import valid_doc
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests._validate_loader import V

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "schema" / "sff_schema.json")


def run_validator(doc):
    """Write doc to a temp file and run validate_flowsheet_against_SFF on it."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "doc.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return V.validate_flowsheet_against_SFF(str(p), str(SCHEMA_PATH))


class TestStubEviction(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()   # evicts Tier-1 stubs (RealBiosteamTestCase)

    def test_eviction_restores_real_unit_parsing_after_tier1_stub(self):
        """After install_biosteam_stubs() poisons sys.modules['thermosteam'],
        evict_biosteam_stubs() removes the fake so the real validator re-imports
        real thermosteam → valid_doc() validates, QU-02 not an error-fail,
        is_valid True."""
        from tests._fakes import install_biosteam_stubs
        from tests._stub_eviction import evict_biosteam_stubs
        install_biosteam_stubs()                       # simulate Tier 1 having run
        self.assertTrue(getattr(sys.modules.get("thermosteam"), "_SFF_STUB", False))
        evict_biosteam_stubs()
        self.assertFalse(getattr(sys.modules.get("thermosteam"), "_SFF_STUB", False))
        is_valid, results = run_validator(valid_doc())
        qu02 = [r for r in results if r.check_id == "QU-02"]
        self.assertTrue(is_valid, [r for r in results if r.status == "fail"])
        self.assertFalse([r for r in qu02
                          if r.severity == "error" and r.status == "fail"])

    def test_evict_is_noop_without_stub(self):
        """evict_biosteam_stubs() with no fake stub present does not raise and
        leaves any real thermosteam module in place."""
        from tests._stub_eviction import evict_biosteam_stubs
        evict_biosteam_stubs()   # no exception; real/absent module untouched
        ts = sys.modules.get("thermosteam")
        self.assertFalse(getattr(ts, "_SFF_STUB", False))


if __name__ == "__main__":
    unittest.main()
