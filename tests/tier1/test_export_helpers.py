# -*- coding: utf-8 -*-
# Tier 1 walking skeleton: prove the shared biosteam stub lets us import
# pisces_sff._export and exercise its pure / near-pure helpers with NO real
# biosteam loaded. Any helper tested here with a FAKE object is re-verified
# against a REAL object in Tier 2/3 (is_product -> tests/tier2).

import sys
import types
import unittest

from tests import _fakes

_export = _fakes.load_export()


class TestStubKeepsBiosteamFake(unittest.TestCase):
    def test_biosteam_import_is_the_stub_not_the_real_package(self):
        self.assertTrue(getattr(sys.modules["biosteam"], "_SFF_STUB", False))
        self.assertTrue(getattr(sys.modules["thermosteam"], "_SFF_STUB", False))


class TestFormatName(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(_export.format_name(""), "")

    def test_all_caps_passthrough(self):
        self.assertEqual(_export.format_name("CSL"), "CSL")

    def test_specific_TAL_mapping(self):
        self.assertEqual(_export.format_name("TAL_product"), "Triacetic acid lactone")

    def test_feedstock_suffix_stripped_and_capitalized(self):
        self.assertEqual(_export.format_name("corn_feedstock"), "Corn")


class TestIsProductWithFakeStream(unittest.TestCase):
    # is_product is re-verified against a REAL Stream in tests/tier2.
    def _stream(self, cost):
        return types.SimpleNamespace(cost=cost)

    def test_priced_stream_in_products_is_a_product(self):
        s = self._stream(1.0)
        self.assertTrue(_export.is_product(s, [s]))

    def test_zero_cost_is_not_a_product(self):
        s = self._stream(0.0)
        self.assertFalse(_export.is_product(s, [s]))

    def test_stream_absent_from_products_is_not_a_product(self):
        s = self._stream(1.0)
        self.assertFalse(_export.is_product(s, []))


if __name__ == "__main__":
    unittest.main()
