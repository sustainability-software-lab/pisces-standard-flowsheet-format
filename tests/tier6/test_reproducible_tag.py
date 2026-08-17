# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 6: the committed corn file earns the `reproducible` tag. Re-exports corn
# from its embedded recipe via the harness and deep-compares against the
# COMMITTED file (ignoring metadata.tags, comparison_rtol, and volatile
# resolved.* fields). Heavy sim; gated on SFF_TEST_TIER6; never concurrent with
# the sibling Tier 6 tests (shared numba cache / export lock).

import unittest
from pathlib import Path

from tests._gating import RUN_TIER6
from tests._stub_eviction import RealBiosteamTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
CORN = (REPO_ROOT / "pisces_sff" / "exported_flowsheets" / "bioindustrial_park"
        / "corn_dry_grind_ethanol.json")


@unittest.skipUnless(RUN_TIER6, "set SFF_TEST_TIER6=1 (default on) to run; builds/uses the pinned env")
class TestReproducibleTag(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from pisces_sff import verify_reproducible, evaluate_sff_tags
        cls.verify_reproducible = staticmethod(verify_reproducible)
        cls.evaluate_sff_tags = staticmethod(evaluate_sff_tags)

    def test_committed_corn_reproduces(self):
        """verify_reproducible(committed corn) -> matches within the recorded
        comparison_rtol (harness re-export from the embedded recipe equals the
        committed file field-for-field, ignoring tags/comparison_rtol/volatile
        resolved.*)."""
        matches, diffs = self.verify_reproducible(str(CORN))
        self.assertTrue(matches, diffs[:10])

    def test_evaluate_reports_reproducible_earned(self):
        """evaluate_sff_tags(committed corn, run_harness=True) -> reproducible.
        earned is True and exported-from-simulator.earned is True."""
        verdict = self.evaluate_sff_tags(str(CORN), run_harness=True)
        self.assertTrue(verdict["reproducible"]["earned"])
        self.assertTrue(verdict["exported-from-simulator"]["earned"])
