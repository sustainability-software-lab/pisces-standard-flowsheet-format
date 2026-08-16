# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

import importlib
import os
import unittest

import tests._gating as gating


class TestGating(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        # Reload again so tests._gating's module-level RUN_TIER* booleans match
        # the restored environment, not whatever env the test body last set --
        # otherwise the module is left stale for anything that imports it later
        # in the same process (README documents RUN_TIER1..RUN_TIER6 as public).
        importlib.reload(gating)

    def test_unset_is_enabled(self):
        """No SFF_TEST_TIER3 in env → tier_enabled(3) is True."""
        os.environ.pop("SFF_TEST_TIER3", None)
        importlib.reload(gating)
        self.assertTrue(gating.tier_enabled(3))

    def test_falsy_values_disable(self):
        """SFF_TEST_TIER2 in {0,false,no,off,''} → tier_enabled(2) is False."""
        for val in ("0", "false", "No", "off", ""):
            os.environ["SFF_TEST_TIER2"] = val
            importlib.reload(gating)
            self.assertFalse(gating.tier_enabled(2), val)

    def test_truthy_values_enable(self):
        """SFF_TEST_TIER2=1 (or any non-falsy) → tier_enabled(2) is True."""
        os.environ["SFF_TEST_TIER2"] = "1"
        importlib.reload(gating)
        self.assertTrue(gating.tier_enabled(2))

    def test_skip_if_disabled_raises(self):
        """skip_if_disabled(5) raises SkipTest when tier 5 is disabled."""
        os.environ["SFF_TEST_TIER5"] = "0"
        importlib.reload(gating)
        with self.assertRaises(unittest.SkipTest):
            gating.skip_if_disabled(5)


if __name__ == "__main__":
    unittest.main()
