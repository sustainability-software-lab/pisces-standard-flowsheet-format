# -*- coding: utf-8 -*-
# Unit tests for pisces_sff/exceptions.py — the package exception hierarchy, and
# a guard that the exporter no longer contains bare breakpoint() traps.
#
# Import-light by construction: exceptions.py imports no biosteam, and we load it
# by file path (like tests/tier1/test_quantity_units_helpers.py loads _quantity_units)
# so that importing the pisces_sff package — and thus _export/biosteam — never
# happens here.

import importlib.util
import re
import unittest
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parents[2] / "pisces_sff"
EXCEPTIONS_PATH = PKG_DIR / "exceptions.py"
EXPORT_PATH = PKG_DIR / "export" / "_export.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_exceptions_under_test", EXCEPTIONS_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestHierarchy(unittest.TestCase):
    def setUp(self):
        self.m = load_module()

    def test_all_names_are_exported(self):
        """__all__ lists exactly the four public exception names."""
        self.assertEqual(
            set(self.m.__all__),
            {"SFFError", "SFFExportError",
             "FlowsheetWriteError", "DesignInputSpecError"},
        )

    def test_base_is_an_exception(self):
        """SFFError is declared a subclass of the builtin Exception."""
        self.assertTrue(issubclass(self.m.SFFError, Exception))

    def test_export_error_derives_from_base(self):
        """SFFExportError is declared a subclass of SFFError."""
        self.assertTrue(issubclass(self.m.SFFExportError, self.m.SFFError))

    def test_specific_errors_derive_from_export_error(self):
        """FlowsheetWriteError and DesignInputSpecError both subclass SFFExportError."""
        for name in ("FlowsheetWriteError", "DesignInputSpecError"):
            with self.subTest(exception=name):
                self.assertTrue(
                    issubclass(getattr(self.m, name), self.m.SFFExportError)
                )

    def test_specific_error_is_catchable_as_base(self):
        """Raising DesignInputSpecError → caught by an `except SFFError` handler."""
        # Catching the base must catch any specific subclass — the point of the
        # hierarchy for a consumer that wants to handle any export failure.
        try:
            raise self.m.DesignInputSpecError("boom")
        except self.m.SFFError as caught:
            self.assertIsInstance(caught, self.m.DesignInputSpecError)
        else:
            self.fail("DesignInputSpecError was not caught as SFFError")

    def test_chaining_preserves_cause(self):
        """Re-raising a ValueError as FlowsheetWriteError via `raise ... from e` → __cause__ is the original."""
        original = ValueError("root cause")
        try:
            try:
                raise original
            except ValueError as e:
                raise self.m.FlowsheetWriteError("wrapped") from e
        except self.m.FlowsheetWriteError as caught:
            self.assertIs(caught.__cause__, original)


class TestNoBreakpointsRemain(unittest.TestCase):
    """Guard against a bare breakpoint() creeping back into the exporter."""

    def test_export_source_has_no_breakpoint_call(self):
        """_export.py source text → contains no bare breakpoint() call."""
        source = EXPORT_PATH.read_text(encoding="utf-8")
        # Match a call, not the word in a comment/docstring describing it.
        self.assertNotRegex(source, r"(?<![.\w])breakpoint\s*\(")


if __name__ == "__main__":
    unittest.main()
