# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Tier 4 — metadata checks (MET-02, MET-03, MET-04). Runs the FULL validator on
valid_doc() with exactly one thing broken and asserts the target check's
CheckResult carries the catalogue's declared severity, status == "fail", and the
correct effect on is_valid."""

import unittest

from tests._docs import valid_doc, mutate
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests.tier4._run import validate_doc


class TestMET02(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()   # evicts Tier-1 stubs (RealBiosteamTestCase)

    def test_conformer_passes(self):
        """MET-02 — valid_doc() has resolvable feed/product refs → CheckResult
        (MET-02, error, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["MET-02"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_dangling_ref_fails(self):
        """MET-02 — feedstock stream_id 'ghost' resolves to no stream →
        CheckResult(MET-02, error, fail); is_valid False."""
        doc = valid_doc()
        mutate(doc, "metadata/feedstocks/0/stream_id", "ghost")
        is_valid, by_id = validate_doc(doc)
        r = by_id["MET-02"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestMET03(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()   # evicts Tier-1 stubs (RealBiosteamTestCase)

    def test_conformer_passes(self):
        """MET-03 — valid_doc() feedstock stream 'feed' carries roles
        ["input", "feedstock"], agreeing with metadata.feedstocks → CheckResult
        (MET-03, warning, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["MET-03"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_role_mismatch_fails(self):
        """MET-03 — pointing metadata.feedstocks[0] at stream 'prod' (roles
        ["output", "product"], no "feedstock") disagrees with the designation
        → CheckResult(MET-03, warning, fail); is_valid stays True (warnings
        never flip is_valid)."""
        doc = valid_doc()
        mutate(doc, "metadata/feedstocks/0/stream_id", "prod")
        is_valid, by_id = validate_doc(doc)
        r = by_id["MET-03"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


class TestMET04(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()   # evicts Tier-1 stubs (RealBiosteamTestCase)

    def test_conformer_passes(self):
        """MET-04 — valid_doc() TEA_year 2020 is plausible → CheckResult
        (MET-04, warning, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["MET-04"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_implausible_year_fails(self):
        """MET-04 — TEA_year 1500 is implausible → CheckResult(MET-04, warning,
        fail); is_valid stays True (warnings never flip is_valid)."""
        doc = valid_doc()
        mutate(doc, "metadata/TEA_year", 1500)
        is_valid, by_id = validate_doc(doc)
        r = by_id["MET-04"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


if __name__ == "__main__":
    unittest.main()
