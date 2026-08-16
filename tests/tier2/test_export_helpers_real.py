# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 2 walking skeleton: re-verify, against a REAL biosteam Stream, the
# is_product behavior that Task 2 tested with a fake object in Tier 1. This is
# the Tier-1 rule in action: a fake-object assertion earns a real-object check
# in a higher tier. Consolidated from test_helpers_real_objects.py (Task 2.4).
#
# Gated on RUN_TIER2 (default on; imports biosteam, runs a small simulation).

import unittest

from tests._gating import RUN_TIER2
from tests._real_objects import build_small_system_and_tea
from tests._stub_eviction import RealBiosteamTestCase


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestIsProductWithRealStream(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()   # evicts Tier-1 stubs (RealBiosteamTestCase)

        from pisces_sff import _export

        cls._export = _export
        cls.system, cls.H1, cls.tea = build_small_system_and_tea()
        cls.products = list(cls.system.products)
        cls.hot = cls.H1.outs[0]

    def test_priced_outlet_is_a_product(self):
        self.hot.price = 1.0
        self.assertIn(self.hot, self.products)
        self.assertTrue(self._export.is_product(self.hot, self.products))

    def test_zero_priced_outlet_is_not_a_product(self):
        self.hot.price = 0.0
        try:
            self.assertFalse(self._export.is_product(self.hot, self.products))
        finally:
            self.hot.price = 1.0  # restore for other classes sharing the cache


if __name__ == "__main__":
    unittest.main()
