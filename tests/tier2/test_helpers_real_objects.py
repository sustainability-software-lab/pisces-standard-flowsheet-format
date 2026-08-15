# -*- coding: utf-8 -*-
# Tier 2 walking skeleton: re-verify, against a REAL biosteam Stream, the
# is_product behavior that Task 2 tested with a fake object in Tier 1. This is
# the Tier-1 rule in action: a fake-object assertion earns a real-object check
# in a higher tier.
#
# Gated on SFF_TEST_BIOSTEAM=1 (imports biosteam, runs a small simulation).

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _real_system import build_small_system_and_tea  # noqa: E402

RUN_TIER_2 = os.environ.get("SFF_TEST_BIOSTEAM") == "1"


@unittest.skipUnless(RUN_TIER_2, "set SFF_TEST_BIOSTEAM=1 to run (imports biosteam)")
class TestIsProductWithRealStream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
