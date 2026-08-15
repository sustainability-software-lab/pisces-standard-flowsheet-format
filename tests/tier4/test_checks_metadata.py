# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: metadata checks (MET-02, MET-03), GRAPH-01, and the XREF-01 aggregate.

import importlib.util
import unittest
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_validate.py")


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_metaxref_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = load_validate_module()


def ctx(**sections):
    return V._Context(sections)


class TestMetadataStreamRefs(unittest.TestCase):
    def test_resolving_refs_pass(self):
        c = ctx(metadata={"feedstocks": [{"stream_id": "corn"}],
                          "products": [{"stream_id": "eth"}]},
                streams=[{"id": "corn"}, {"id": "eth"}])
        self.assertEqual(V._check_metadata_stream_refs(c)[0].status, "pass")

    def test_dangling_ref_fails(self):
        c = ctx(metadata={"feedstocks": [{"stream_id": "ghost"}]},
                streams=[{"id": "corn"}])
        self.assertEqual(V._check_metadata_stream_refs(c)[0].status, "fail")

    def test_no_refs_skips(self):
        c = ctx(metadata={}, streams=[{"id": "corn"}])
        self.assertEqual(V._check_metadata_stream_refs(c)[0].status, "skip")


class TestMetadataRoleAgreement(unittest.TestCase):
    def test_agreeing_roles_pass(self):
        c = ctx(metadata={"feedstocks": [{"stream_id": "corn"}]},
                streams=[{"id": "corn", "roles": ["input", "feedstock"]}])
        self.assertEqual(V._check_metadata_role_agreement(c)[0].status, "pass")

    def test_missing_role_is_warning(self):
        c = ctx(metadata={"feedstocks": [{"stream_id": "corn"}]},
                streams=[{"id": "corn", "roles": ["input"]}])
        r = V._check_metadata_role_agreement(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_no_roles_skips(self):
        c = ctx(metadata={"feedstocks": [{"stream_id": "corn"}]},
                streams=[{"id": "corn"}])
        self.assertEqual(V._check_metadata_role_agreement(c)[0].status, "skip")


class TestTeaYearPlausible(unittest.TestCase):
    def test_plausible_year_passes(self):
        c = ctx(metadata={"TEA_year": 2020})
        self.assertEqual(V._check_tea_year_plausible(c)[0].status, "pass")

    def test_absent_year_skips(self):
        c = ctx(metadata={})
        r = V._check_tea_year_plausible(c)[0]
        self.assertEqual((r.status, r.severity), ("skip", "warning"))

    def test_year_zero_is_warning(self):
        c = ctx(metadata={"TEA_year": 0})
        r = V._check_tea_year_plausible(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_far_future_year_is_warning(self):
        c = ctx(metadata={"TEA_year": 20000})
        r = V._check_tea_year_plausible(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_current_plus_one_is_allowed(self):
        import datetime as _dt
        c = ctx(metadata={"TEA_year": _dt.date.today().year + 1})
        self.assertEqual(V._check_tea_year_plausible(c)[0].status, "pass")


class TestBoundaryStreamsExist(unittest.TestCase):
    def test_both_boundaries_pass(self):
        c = ctx(streams=[{"id": "a", "source_unit_id": "None", "sink_unit_id": "U"},
                         {"id": "b", "source_unit_id": "U", "sink_unit_id": "None"}])
        self.assertEqual(V._check_boundary_streams_exist(c)[0].status, "pass")

    def test_no_output_is_warning(self):
        c = ctx(streams=[{"id": "a", "source_unit_id": "None", "sink_unit_id": "U"}])
        r = V._check_boundary_streams_exist(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))

    def test_no_streams_is_warning_not_skip(self):
        c = ctx(streams=[])
        r = V._check_boundary_streams_exist(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))


class TestXrefGate(unittest.TestCase):
    def test_passes_when_no_referential_fail(self):
        results = [V._passed("STR-02", "error"), V._failed("STR-08", "warning", "x")]
        self.assertEqual(V._xref_gate(results).status, "pass")

    def test_fails_when_a_referential_check_fails(self):
        results = [V._failed("STR-07", "error", "dangling component")]
        self.assertEqual(V._xref_gate(results).status, "fail")


if __name__ == "__main__":
    unittest.main()
