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


if __name__ == "__main__":
    unittest.main()
