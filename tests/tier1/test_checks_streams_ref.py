# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: stream referential / roles / zero-flow checks (sff_checks.md STR-01..07,
# STR-13). Import-light; calls each _check_* directly on a synthetic _Context.

import importlib.util
import unittest
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_validate.py")


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_streamsref_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = load_validate_module()


def ctx(**sections):
    return V._Context(sections)


def sp(**kw):
    base = {"total_mass_flow": 1.0, "total_molar_flow": 1.0,
            "temperature": 300.0, "pressure": 1e5,
            "phases": {"l": {"total_molar_flow": 1.0, "composition": [
                {"component_name": "W", "mol_fraction": 1.0}]}}}
    base.update(kw)
    return base


class TestStreamId(unittest.TestCase):
    def test_unique_passes(self):
        c = ctx(streams=[{"id": "a"}, {"id": "b"}])
        self.assertEqual(V._check_stream_id_uniqueness(c)[0].status, "pass")

    def test_duplicate_fails(self):
        c = ctx(streams=[{"id": "a"}, {"id": "a"}])
        self.assertEqual(V._check_stream_id_uniqueness(c)[0].status, "fail")


class TestEndpointRefs(unittest.TestCase):
    def test_boundary_and_unit_pass(self):
        c = ctx(units=[{"id": "U"}],
                streams=[{"id": "s", "source_unit_id": "None", "sink_unit_id": "U"}])
        self.assertEqual(V._check_stream_endpoint_refs(c)[0].status, "pass")

    def test_unknown_endpoint_fails(self):
        c = ctx(units=[{"id": "U"}],
                streams=[{"id": "s", "source_unit_id": "U", "sink_unit_id": "Z"}])
        self.assertEqual(V._check_stream_endpoint_refs(c)[0].status, "fail")


class TestIsolatedStreamEmpty(unittest.TestCase):
    def test_isolated_empty_passes(self):
        c = ctx(streams=[{"id": "s", "source_unit_id": "None",
                          "sink_unit_id": "None", "stream_properties": {
                              "total_mass_flow": 0.0, "total_molar_flow": 0.0,
                              "phases": {}}}])
        self.assertEqual(V._check_isolated_stream_empty(c)[0].status, "pass")

    def test_isolated_with_flow_fails(self):
        c = ctx(chemicals=[{"id": "W"}],
                streams=[{"id": "s", "source_unit_id": "None",
                          "sink_unit_id": "None", "stream_properties": sp()}])
        self.assertEqual(V._check_isolated_stream_empty(c)[0].status, "fail")

    def test_no_isolated_skips(self):
        c = ctx(units=[{"id": "U"}],
                streams=[{"id": "s", "source_unit_id": "None", "sink_unit_id": "U",
                          "stream_properties": sp()}], chemicals=[{"id": "W"}])
        self.assertEqual(V._check_isolated_stream_empty(c)[0].status, "skip")


class TestTopologyRole(unittest.TestCase):
    def test_one_topology_role_passes(self):
        c = ctx(streams=[{"id": "s", "roles": ["input", "feedstock"]}])
        self.assertEqual(V._check_stream_topology_role(c)[0].status, "pass")

    def test_two_topology_roles_fail(self):
        c = ctx(streams=[{"id": "s", "roles": ["input", "output"]}])
        self.assertEqual(V._check_stream_topology_role(c)[0].status, "fail")

    def test_no_roles_skips(self):
        c = ctx(streams=[{"id": "s"}])
        self.assertEqual(V._check_stream_topology_role(c)[0].status, "skip")


class TestRoleTopologyAgreement(unittest.TestCase):
    def test_input_matches_no_source(self):
        c = ctx(units=[{"id": "U"}], streams=[{
            "id": "s", "source_unit_id": "None", "sink_unit_id": "U",
            "roles": ["input"]}])
        self.assertEqual(V._check_stream_role_topology_agreement(c)[0].status,
                         "pass")

    def test_mismatch_is_warning(self):
        c = ctx(units=[{"id": "U"}], streams=[{
            "id": "s", "source_unit_id": "None", "sink_unit_id": "U",
            "roles": ["internal"]}])
        r = V._check_stream_role_topology_agreement(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))


class TestDesignationRoles(unittest.TestCase):
    def test_legal_designation_passes(self):
        c = ctx(streams=[{"id": "s", "roles": ["input", "feedstock"]}])
        self.assertEqual(V._check_stream_designation_roles(c)[0].status, "pass")

    def test_product_on_input_is_warning(self):
        c = ctx(streams=[{"id": "s", "roles": ["input", "product"]}])
        r = V._check_stream_designation_roles(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "warning"))


class TestCompositionComponentRefs(unittest.TestCase):
    def test_resolving_component_passes(self):
        c = ctx(chemicals=[{"id": "W"}], streams=[{
            "id": "s", "stream_properties": sp()}])
        self.assertEqual(V._check_composition_component_refs(c)[0].status, "pass")

    def test_dangling_component_fails(self):
        c = ctx(chemicals=[{"id": "Other"}], streams=[{
            "id": "s", "stream_properties": sp()}])
        self.assertEqual(V._check_composition_component_refs(c)[0].status, "fail")


class TestZeroFlowConsistency(unittest.TestCase):
    def test_all_zero_empty_passes(self):
        c = ctx(streams=[{"id": "s", "stream_properties": {
            "total_mass_flow": 0.0, "total_molar_flow": 0.0, "phases": {}}}])
        self.assertEqual(V._check_zero_flow_consistency(c)[0].status, "pass")

    def test_zero_mass_nonzero_molar_fails(self):
        c = ctx(chemicals=[{"id": "W"}], streams=[{"id": "s",
                "stream_properties": sp(total_mass_flow=0.0)}])
        self.assertEqual(V._check_zero_flow_consistency(c)[0].status, "fail")

    def test_all_nonzero_skips(self):
        c = ctx(chemicals=[{"id": "W"}],
                streams=[{"id": "s", "stream_properties": sp()}])
        self.assertEqual(V._check_zero_flow_consistency(c)[0].status, "skip")


if __name__ == "__main__":
    unittest.main()
