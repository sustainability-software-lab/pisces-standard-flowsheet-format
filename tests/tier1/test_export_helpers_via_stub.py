# -*- coding: utf-8 -*-
# Tier 1 walking skeleton: prove the shared biosteam stub lets us import
# pisces_sff._export and exercise its pure / near-pure helpers with NO real
# biosteam loaded. Any helper tested here with a FAKE object is re-verified
# against a REAL object in Tier 2/3 (is_product -> tests/tier2).

import sys
import types
import unittest
from pathlib import Path

# Import the sibling stub by adding this directory to sys.path, so the same
# `import _export_stub` works under both pytest (package import) and
# `unittest discover` (top-level import).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _export_stub  # noqa: E402

_export = _export_stub.load_export()


class TestStubKeepsBiosteamFake(unittest.TestCase):
    def test_biosteam_import_is_the_stub_not_the_real_package(self):
        # Checked against _export's own bound globals, not sys.modules:
        # load_export() removes the fakes from sys.modules right after this
        # import completes (so a later tier's real `import thermosteam` is not
        # poisoned -- see _export_stub.py), but _export keeps its own
        # references to the fake classes/module bound at import time. Whether
        # sys.modules still carries the stub by the time this test body runs
        # depends on what else the process has done since, so that would be an
        # order-dependent check; _export's own globals are not.
        self.assertNotEqual(getattr(_export.Reaction, "__module__", ""),
                            "thermosteam")
        self.assertTrue(getattr(_export.bst, "_SFF_STUB", False))


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
