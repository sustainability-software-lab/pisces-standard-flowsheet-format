# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: material-balance (i) fraction sums (STR-08) and phase-flow sums
# (STR-09). Import-light -- neither touches molar mass. STR-10 is in Tier 2.

import importlib.util
import unittest
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_validate.py")


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_matbal_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = load_validate_module()


def ctx(**sections):
    return V._Context(sections)


class TestFractionSums(unittest.TestCase):
    def _stream(self, fracs):
        comp = [{"component_name": f"C{i}", "mol_fraction": f}
                for i, f in enumerate(fracs)]
        return {"id": "s", "stream_properties": {
            "phases": {"l": {"total_molar_flow": 1.0, "composition": comp}}}}

    def test_sum_one_passes(self):
        c = ctx(streams=[self._stream([0.4, 0.6])])
        self.assertEqual(V._check_fraction_sums(c)[0].status, "pass")

    def test_sum_off_is_warning(self):
        c = ctx(streams=[self._stream([0.4, 0.4])])
        r = V._check_fraction_sums(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_empty_skips(self):
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "phases": {"l": {"total_molar_flow": 0.0, "composition": []}}}}])
        self.assertEqual(V._check_fraction_sums(c)[0].status, "skip")

    def test_partial_mass_fraction_does_not_false_fail(self):
        # Regression: one entry omits mass_fraction (schema-optional per
        # entry) while both mol_fraction values sum to 1.0. The mass_fraction
        # sub-check must be skipped rather than summing a partial 0.6 and
        # false-failing against 1.0.
        comp = [{"component_name": "C0", "mol_fraction": 0.4, "mass_fraction": 0.6},
                {"component_name": "C1", "mol_fraction": 0.6}]
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "phases": {"l": {"total_molar_flow": 1.0, "composition": comp}}}}])
        self.assertEqual(V._check_fraction_sums(c)[0].status, "pass")


class TestPhaseFlowSums(unittest.TestCase):
    def test_phase_totals_sum_passes(self):
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "total_molar_flow": 3.0,
            "phases": {"l": {"total_molar_flow": 1.0, "composition": []},
                       "g": {"total_molar_flow": 2.0, "composition": []}}}}])
        self.assertEqual(V._check_phase_flow_sums(c)[0].status, "pass")

    def test_mismatch_is_warning(self):
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "total_molar_flow": 5.0,
            "phases": {"l": {"total_molar_flow": 1.0, "composition": []},
                       "g": {"total_molar_flow": 2.0, "composition": []}}}}])
        r = V._check_phase_flow_sums(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_partial_mass_flow_does_not_false_fail(self):
        # Regression: one phase omits total_mass_flow (schema-optional per
        # phase) while both phases declare total_molar_flow summing to the
        # stream total. The true stream-level total_mass_flow (25.0) does NOT
        # equal the numeric-only phase subset (10.0), so a naive partial sum
        # would false-fail; total_mass_flow must instead be skipped entirely
        # (not all phases declare it), while total_molar_flow still passes.
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "total_mass_flow": 25.0, "total_molar_flow": 3.0,
            "phases": {"l": {"total_molar_flow": 1.0, "composition": []},
                       "g": {"total_mass_flow": 10.0, "total_molar_flow": 2.0,
                             "composition": []}}}}])
        r = V._check_phase_flow_sums(c)[0]
        self.assertNotEqual(r.status, "fail")


class TestRelClose(unittest.TestCase):
    def test_tiny_value_vs_exact_zero_is_close(self):
        # Regression: with b=0, a!=0 the old formula reduced to
        # abs(a) <= rel_tol * abs(a), i.e. 1 <= rel_tol, never true for a
        # sub-1 tolerance. A value at/below ZERO_FLOW compared to exact zero
        # must count as agreement.
        self.assertIs(V._rel_close(1e-15, 0.0, V.TOL_FLOW), True)

    def test_real_mismatch_vs_zero_still_fails(self):
        self.assertIs(V._rel_close(1.0, 0.0, V.TOL_FLOW), False)


if __name__ == "__main__":
    unittest.main()
