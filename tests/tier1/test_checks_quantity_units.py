# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: quantity-unit checks QU-01 (pairing), QU-03 (alias uniqueness), QU-04
# (unused entries). Import-light. QU-02 (parseability) is Tier 2.

import importlib.util
import unittest
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "_validate.py")


def load_validate_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_qu_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = load_validate_module()


def ctx(**sections):
    return V._Context(sections)


def stream_with_mass():
    return {"id": "s", "source_unit_id": "None", "sink_unit_id": "U",
            "stream_properties": {"total_mass_flow": 1.0, "total_molar_flow": 1.0,
                                  "temperature": 300.0, "pressure": 1e5,
                                  "phases": {"l": {"total_molar_flow": 1.0,
                                                   "composition": []}}}}


class TestQuantityUnitPairing(unittest.TestCase):
    def test_resolvable_field_passes(self):
        c = ctx(streams=[stream_with_mass()], quantity_units_global={
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": "kg/hr"},
            "molar_flow": {"aliases": ["total_molar_flow"], "quantity_units": "kmol/hr"},
            "temperature": {"aliases": ["temperature"], "quantity_units": "K"},
            "pressure": {"aliases": ["pressure"], "quantity_units": "Pa"}})
        self.assertEqual(V._check_quantity_unit_pairing(c)[0].status, "pass")

    def test_unresolvable_field_fails(self):
        c = ctx(streams=[stream_with_mass()], quantity_units_global={
            "molar_flow": {"aliases": ["total_molar_flow"], "quantity_units": "kmol/hr"},
            "temperature": {"aliases": ["temperature"], "quantity_units": "K"},
            "pressure": {"aliases": ["pressure"], "quantity_units": "Pa"}})
        # total_mass_flow has no alias entry.
        self.assertEqual(V._check_quantity_unit_pairing(c)[0].status, "fail")

    def test_no_quantity_fields_present_is_vacuous_pass(self):
        # QU-01 is "Skipped when: never" (sff_checks.md) -- zero present
        # quantity fields is a vacuous pass, not a skip: every one of zero
        # fields is trivially resolvable.
        c = ctx()
        r = V._check_quantity_unit_pairing(c)[0]
        self.assertEqual(r.status, "pass")


class TestAliasUniqueness(unittest.TestCase):
    def test_disjoint_aliases_pass(self):
        c = ctx(quantity_units_global={
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": "kg/hr"},
            "molar_flow": {"aliases": ["total_molar_flow"], "quantity_units": "kmol/hr"}})
        self.assertEqual(V._check_alias_uniqueness(c)[0].status, "pass")

    def test_shared_alias_fails(self):
        c = ctx(quantity_units_global={
            "mass_flow": {"aliases": ["F"], "quantity_units": "kg/hr"},
            "molar_flow": {"aliases": ["F"], "quantity_units": "kmol/hr"}})
        self.assertEqual(V._check_alias_uniqueness(c)[0].status, "fail")

    def test_empty_registry_is_vacuous_pass(self):
        # QU-03 is "Skipped when: never" (sff_checks.md) -- an empty
        # quantity_units_global registry has no ambiguous aliases.
        c = ctx()
        self.assertEqual(V._check_alias_uniqueness(c)[0].status, "pass")

    def test_duplicate_alias_within_one_entry_is_not_a_collision(self):
        # An entry's own `aliases` list may legally repeat a value (the schema
        # has no uniqueItems on `aliases`). QU-03 is about an alias spanning
        # more than one DISTINCT entry -- a repeat within a single entry must
        # not false-fail.
        c = ctx(quantity_units_global={
            "mass_flow": {"aliases": ["F", "F"], "quantity_units": "kg/hr"}})
        self.assertEqual(V._check_alias_uniqueness(c)[0].status, "pass")


class TestUnusedAliases(unittest.TestCase):
    def test_entry_used_passes(self):
        c = ctx(streams=[stream_with_mass()], quantity_units_global={
            "mass_flow": {"aliases": ["mass_flow", "total_mass_flow", "F_mass"],
                          "quantity_units": "kg/hr"},
            "molar_flow": {"aliases": ["total_molar_flow"], "quantity_units": "kmol/hr"},
            "temperature": {"aliases": ["temperature"], "quantity_units": "K"},
            "pressure": {"aliases": ["pressure"], "quantity_units": "Pa"}})
        # mass_flow entry is used via the total_mass_flow synonym; synonyms
        # mass_flow / F_mass being unused does NOT flag the entry.
        self.assertEqual(V._check_unused_aliases(c)[0].status, "pass")

    def test_entry_unused_is_info(self):
        c = ctx(streams=[stream_with_mass()], quantity_units_global={
            "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": "kg/hr"},
            "molar_flow": {"aliases": ["total_molar_flow"], "quantity_units": "kmol/hr"},
            "temperature": {"aliases": ["temperature"], "quantity_units": "K"},
            "pressure": {"aliases": ["pressure"], "quantity_units": "Pa"},
            "volumetric_flow": {"aliases": ["total_volumetric_flow"],
                                "quantity_units": "m3/hr"}})
        # No stream declares total_volumetric_flow -> that entry is unused.
        r = V._check_unused_aliases(c)[0]
        self.assertEqual((r.status, r.severity), ("fail", "info"))

    def test_empty_registry_is_vacuous_pass(self):
        # QU-04 is "Skipped when: never" (sff_checks.md) -- an empty
        # quantity_units_global registry has no unused entries.
        c = ctx()
        r = V._check_unused_aliases(c)[0]
        self.assertEqual(r.status, "pass")


if __name__ == "__main__":
    unittest.main()
