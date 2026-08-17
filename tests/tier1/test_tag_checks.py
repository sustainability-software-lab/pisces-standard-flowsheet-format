# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: MET-07 / UNIT-10 / STR-14 unit behavior on crafted docs, plus the
# _has_reactions / _all_streams_empty helpers. Import-light (loads _validate by
# file path; touches no biosteam).

import hashlib
import unittest

from tests._validate_loader import V


def _one(check, doc):
    """Run a single check on a fresh _Context(doc); return its sole CheckResult."""
    results = check(V._Context(doc))
    assert len(results) == 1, results
    return results[0]


class TestMET07(unittest.TestCase):
    def _repro(self, content):
        return {"metadata": {"reproducibility": {"environment": {
            "content": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}}}}

    def test_no_reproducibility_block_skips(self):
        r = _one(V._check_reproducibility_content_digests, {"metadata": {}})
        self.assertEqual((r.check_id, r.severity, r.status), ("MET-07", "error", "skip"))

    def test_matching_digest_passes(self):
        r = _one(V._check_reproducibility_content_digests, self._repro("name: x\n"))
        self.assertEqual((r.severity, r.status), ("error", "pass"))

    def test_mismatched_digest_fails(self):
        doc = self._repro("name: x\n")
        doc["metadata"]["reproducibility"]["environment"]["sha256"] = "0" * 64
        r = _one(V._check_reproducibility_content_digests, doc)
        self.assertEqual((r.severity, r.status), ("error", "fail"))

    def test_block_without_content_or_sha256_skips(self):
        doc = {"metadata": {"reproducibility": {"environment": {"content": "x"}}}}
        r = _one(V._check_reproducibility_content_digests, doc)
        self.assertEqual(r.status, "skip")


class TestUNIT10(unittest.TestCase):
    def test_empty_units_fails(self):
        r = _one(V._check_units_present_identified, {"units": []})
        self.assertEqual((r.check_id, r.severity, r.status), ("UNIT-10", "warning", "fail"))

    def test_missing_units_key_fails(self):
        r = _one(V._check_units_present_identified, {})
        self.assertEqual(r.status, "fail")

    def test_well_identified_units_pass(self):
        r = _one(V._check_units_present_identified,
                 {"units": [{"id": "U1", "unit_type": "Mixer"}]})
        self.assertEqual(r.status, "pass")

    def test_unit_missing_unit_type_fails(self):
        r = _one(V._check_units_present_identified, {"units": [{"id": "U1"}]})
        self.assertEqual(r.status, "fail")

    def test_unit_blank_id_fails(self):
        r = _one(V._check_units_present_identified,
                 {"units": [{"id": "", "unit_type": "Mixer"}]})
        self.assertEqual(r.status, "fail")


class TestSTR14(unittest.TestCase):
    def test_empty_streams_fails(self):
        r = _one(V._check_streams_present_identified, {"streams": []})
        self.assertEqual((r.check_id, r.severity, r.status), ("STR-14", "warning", "fail"))

    def test_well_identified_streams_pass(self):
        r = _one(V._check_streams_present_identified, {"streams": [{"id": "s1"}]})
        self.assertEqual(r.status, "pass")

    def test_stream_blank_id_fails(self):
        r = _one(V._check_streams_present_identified, {"streams": [{"id": ""}]})
        self.assertEqual(r.status, "fail")


class TestReactionAndEmptinessHelpers(unittest.TestCase):
    def test_has_reactions_true_when_a_unit_declares_one(self):
        doc = {"units": [{"id": "U1", "reactions": [{"reactant": "A"}]}]}
        self.assertTrue(V._has_reactions(V._Context(doc)))

    def test_has_reactions_false_when_none(self):
        self.assertFalse(V._has_reactions(V._Context({"units": [{"id": "U1"}]})))

    def test_all_streams_empty_true_for_zero_flow_streams(self):
        doc = {"streams": [{"id": "s", "stream_properties": {"total_mass_flow": 0.0}}]}
        self.assertTrue(V._all_streams_empty(V._Context(doc)))

    def test_all_streams_empty_false_when_a_stream_carries_flow(self):
        doc = {"streams": [{"id": "s", "stream_properties": {"total_mass_flow": 5.0}}]}
        self.assertFalse(V._all_streams_empty(V._Context(doc)))
