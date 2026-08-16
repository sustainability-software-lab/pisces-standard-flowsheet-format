# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests._docs import valid_doc, mutate, remove
from tests._gating import skip_if_disabled
from tests._validate_loader import V

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "schema" / "sff_schema.json")


def run_validator(doc):
    """Write doc to a temp file and run validate_flowsheet_against_SFF on it."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "doc.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return V.validate_flowsheet_against_SFF(str(p), str(SCHEMA_PATH))


class TestDocsFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)

    def test_valid_doc_is_fully_valid(self):
        """valid_doc() → is_valid True and no error-severity fail (the fixed
        point Tiers 3/4 pivot on)."""
        is_valid, results = run_validator(valid_doc())
        self.assertTrue(is_valid, [r for r in results if r.status == "fail"])
        self.assertFalse([r for r in results
                          if r.status == "fail" and r.severity == "error"])

    def test_mutate_sets_nested_field(self):
        """mutate(doc,'streams/0/stream_properties/pressure',0) sets that field
        to 0 without touching siblings."""
        doc = valid_doc()
        mutate(doc, "streams/0/stream_properties/pressure", 0)
        self.assertEqual(doc["streams"][0]["stream_properties"]["pressure"], 0)

    def test_remove_deletes_nested_field(self):
        """remove(doc,'streams/0/stream_properties/total_mass_flow') deletes that
        key."""
        doc = valid_doc()
        remove(doc, "streams/0/stream_properties/total_mass_flow")
        self.assertNotIn("total_mass_flow",
                         doc["streams"][0]["stream_properties"])


if __name__ == "__main__":
    unittest.main()
