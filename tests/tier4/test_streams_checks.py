# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Tier 4 -- streams checks (STR-01..10, STR-13). Runs the FULL validator on
valid_doc() with exactly one thing broken and asserts the target check's
CheckResult carries the catalogue's declared severity, status == "fail", and the
correct effect on is_valid.

Note on STR-04/STR-05/STR-06 (see pisces_sff/_validate.py): TOPOLOGY_ROLES =
('input', 'output', 'internal') and DESIGNATION_ROLES = ('purchased_raw_material',
'feedstock', 'product'). valid_doc()'s 'feed' stream carries roles
["input", "feedstock"] (topology 'input', designation 'feedstock'); 'prod'
carries ["output", "product"]."""

import unittest

from tests._docs import valid_doc, mutate, remove
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests.tier4._run import validate_doc


class TestSTR01(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-01 -- valid_doc()'s streams 'feed'/'prod' have unique ids ->
        CheckResult(STR-01, error, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-01"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-01 -- renaming 'prod' to 'feed' duplicates the first stream's
        id -> CheckResult(STR-01, error, fail); is_valid False."""
        doc = valid_doc()
        mutate(doc, "streams/1/id", "feed")
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-01"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestSTR02(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-02 -- valid_doc()'s stream endpoints all resolve to a declared
        unit or the boundary sentinel 'None' -> CheckResult(STR-02, error,
        pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-02"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-02 -- streams/0/sink_unit_id set to 'ghost', which resolves to
        neither a unit nor the boundary -> CheckResult(STR-02, error, fail);
        is_valid False."""
        doc = valid_doc()
        mutate(doc, "streams/0/sink_unit_id", "ghost")
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-02"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestSTR03(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-03 -- valid_doc() has no doubly-isolated stream (source ==
        sink == boundary) -> CheckResult(STR-03, error, skip); is_valid
        True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-03"][0].status, "skip")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-03 -- a third stream 'isolated1' has source_unit_id ==
        sink_unit_id == 'None' (doubly isolated) but carries nonzero flow and
        a non-empty composition -> CheckResult(STR-03, error, fail); is_valid
        False."""
        doc = valid_doc()
        doc["streams"].append({
            "id": "isolated1", "source_unit_id": "None", "sink_unit_id": "None",
            "stream_properties": {
                "total_mass_flow": 1.0, "total_molar_flow": 1.0,
                "temperature": 300.0, "pressure": 101325.0,
                "phases": {"l": {"total_molar_flow": 1.0, "composition": [
                    {"component_name": "Ethanol", "mol_fraction": 1.0}]}},
            },
        })
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-03"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestSTR04(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-04 -- valid_doc()'s streams each carry exactly one topology
        role ('feed': input; 'prod': output) -> CheckResult(STR-04, error,
        pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-04"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-04 -- 'feed' is given roles ["input", "output"], two topology
        roles at once (TOPOLOGY_ROLES = input/output/internal) -> CheckResult
        (STR-04, error, fail); is_valid False."""
        doc = valid_doc()
        mutate(doc, "streams/0/roles", ["input", "output"])
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-04"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestSTR05(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-05 -- valid_doc()'s single topology roles agree with
        connectivity ('feed': source boundary/sink U1 -> expected 'input',
        declared 'input') -> CheckResult(STR-05, warning, pass); is_valid
        True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-05"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-05 -- 'feed' (source 'None' == boundary, sink 'U1') is given
        roles ["output", "feedstock"]; connectivity implies 'input' but the
        declared topology role is 'output' -> CheckResult(STR-05, warning,
        fail); is_valid stays True (warnings never flip is_valid)."""
        doc = valid_doc()
        mutate(doc, "streams/0/roles", ["output", "feedstock"])
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-05"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


class TestSTR06(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-06 -- valid_doc()'s designation roles are legal for their
        topology ('feed': feedstock + input; 'prod': product + output) ->
        CheckResult(STR-06, warning, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-06"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-06 -- 'prod' (topology role 'output') is given designation
        role 'feedstock' instead of 'product' (roles ["output", "feedstock"]);
        'feedstock' requires an 'input' topology role, which is absent ->
        CheckResult(STR-06, warning, fail); is_valid stays True."""
        doc = valid_doc()
        mutate(doc, "streams/1/roles", ["output", "feedstock"])
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-06"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


class TestSTR07(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-07 -- valid_doc()'s phase compositions reference the declared
        chemical 'Ethanol' -> CheckResult(STR-07, error, pass); is_valid
        True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-07"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-07 -- streams/0's phase 'l' composition component_name is set
        to 'ghost', which no declared chemical id matches -> CheckResult
        (STR-07, error, fail); is_valid False."""
        doc = valid_doc()
        mutate(doc, "streams/0/stream_properties/phases/l/composition/0/"
                    "component_name", "ghost")
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-07"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestSTR08(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-08 -- valid_doc()'s phase composition mol_fractions sum to 1
        -> CheckResult(STR-08, warning, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-08"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-08 -- streams/0's phase 'l' composition mol_fraction is
        lowered to 0.5 (the only entry, so the phase sum is 0.5 != 1) ->
        CheckResult(STR-08, warning, fail); is_valid stays True."""
        doc = valid_doc()
        mutate(doc, "streams/0/stream_properties/phases/l/composition/0/"
                    "mol_fraction", 0.5)
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-08"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


class TestSTR09(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-09 -- valid_doc()'s single phase 'l' total_molar_flow (1.0)
        sums to the stream-level total_molar_flow (1.0) -> CheckResult
        (STR-09, warning, pass); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-09"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-09 -- streams/0's phase 'l' total_molar_flow is lowered to 0.5
        while the stream-level total_molar_flow stays 1.0, so the (single)
        phase no longer sums to the stream total -> CheckResult(STR-09,
        warning, fail); is_valid stays True."""
        doc = valid_doc()
        mutate(doc, "streams/0/stream_properties/phases/l/total_molar_flow",
               0.5)
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-09"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


class TestSTR10(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-10 -- valid_doc()'s phase 'l' declares total_molar_flow and a
        composition but no phase-level total_mass_flow, so there is no phase
        with all of {mass, molar, resolvable molar mass} to compare ->
        CheckResult(STR-10, warning, skip); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-10"][0].status, "skip")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-10 -- streams/0's phase 'l' is given total_mass_flow = 999.0,
        wildly inconsistent with total_molar_flow (1.0) * Ethanol's molar
        mass (46.07 g/mol ~= 46.07 kg/hr predicted) -> CheckResult(STR-10,
        warning, fail); is_valid stays True."""
        doc = valid_doc()
        mutate(doc, "streams/0/stream_properties/phases/l/total_mass_flow",
               999.0)
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-10"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


class TestSTR13(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-13 -- valid_doc() has no stream with a zero flow scalar ->
        CheckResult(STR-13, error, skip); is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-13"][0].status, "skip")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-13 -- streams/0's total_mass_flow is set to 0 while
        total_molar_flow (1.0) and the phase composition stay nonzero/
        non-empty, so the stream is not actually empty -> CheckResult
        (STR-13, error, fail); is_valid False."""
        doc = valid_doc()
        mutate(doc, "streams/0/stream_properties/total_mass_flow", 0)
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-13"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)


class TestSTR14(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_conformer(self):
        """STR-14 -- valid_doc()'s identified streams -> pass."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["STR-14"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_violator(self):
        """STR-14 -- an extra doubly-isolated stream with an empty id ->
        CheckResult(STR-14, warning, fail); is_valid stays True. (Blanking an
        *existing* stream's id -- 'feed' or 'prod' -- would also break MET-02,
        since metadata.feedstocks/products reference streams by that same id;
        appending a new, empty, boundary-to-boundary stream isolates the
        violation to STR-14 alone.)"""
        doc = valid_doc()
        doc["streams"].append({"id": "", "source_unit_id": "None",
                                "sink_unit_id": "None"})
        is_valid, by_id = validate_doc(doc)
        r = by_id["STR-14"][0]
        self.assertEqual((r.severity, r.status), ("warning", "fail"))
        self.assertTrue(is_valid)


if __name__ == "__main__":
    unittest.main()
