# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Tier 4 -- cross-object checks (XREF-01, GRAPH-01). Runs the FULL validator
on valid_doc() with exactly one thing broken and asserts the target check's
CheckResult carries the catalogue's declared severity, status == "fail", and
the correct effect on is_valid.

XREF-01 is an aggregate (pisces_sff/validate/_validate.py's _xref_gate, not in _CHECKS)
computed from the referential error-checks in _REFERENTIAL_IDS -- it fails iff
any of {STR-02, STR-07, UNIT-02, UNIT-04, UNIT-06, CHEM-04, MET-02, UTIL-04}
failed at error severity. It still appears in the results keyed by check id
"XREF-01"."""

import unittest

from tests._docs import valid_doc, mutate, remove
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests.tier4._run import validate_doc


class TestXREF01(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """XREF-01 -- valid_doc() has no failing referential check -> the
        _xref_gate aggregate yields CheckResult(XREF-01, error, pass);
        is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["XREF-01"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """XREF-01 -- streams/0/sink_unit_id is set to 'ghost' (the STR-02
        dangling-ref break; STR-02 is one of the referential checks the
        aggregate watches) -> the _xref_gate aggregate yields CheckResult
        (XREF-01, error, fail); is_valid False."""
        doc = valid_doc()
        mutate(doc, "streams/0/sink_unit_id", "ghost")
        is_valid, by_id = validate_doc(doc)
        r = by_id["XREF-01"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestGRAPH01(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """GRAPH-01 -- valid_doc() has both a boundary input ('feed': source
        'None') and a boundary output ('prod': sink 'None') -> CheckResult
        (GRAPH-01, warning, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["GRAPH-01"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """GRAPH-01 -- the boundary-output stream 'prod' (streams index 1) is
        removed entirely, leaving a boundary input ('feed') but no boundary
        output -> CheckResult(GRAPH-01, warning, fail); is_valid stays True
        (warnings never flip is_valid). metadata.products is cleared to []
        alongside the removal so MET-02's now-dangling 'prod' stream_id
        reference does not also trip an error-severity fail -- GRAPH-01 is
        meant to be the only broken check here."""
        doc = valid_doc()
        remove(doc, "streams/1")
        mutate(doc, "metadata/products", [])
        is_valid, by_id = validate_doc(doc)
        r = by_id["GRAPH-01"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


if __name__ == "__main__":
    unittest.main()
