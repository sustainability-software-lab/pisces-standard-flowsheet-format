# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: static tag evaluation -- tolerated-skip predicates, _earned_tags, the
# TAG-01 aggregate, and evaluate_sff_tags (static). Import-light.

import json
import tempfile
import unittest
from pathlib import Path

from tests._docs import valid_doc
from tests._validate_loader import V

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "schema" / "sff_schema.json")

with SCHEMA_PATH.open("r", encoding="utf-8") as _f:
    _SCHEMA = json.load(_f)


def _R(check_id, severity, status):
    return V.CheckResult(check_id, severity, status, "", "<root>")


class TestSchemaGateAndRunAllChecks(unittest.TestCase):
    """_schema_gate/_run_all_checks: the in-memory core factored out of
    validate_flowsheet_against_SFF (Step 3). Both take an in-memory doc/schema
    dict -- no temp file -- so the exporter's future tag self-check and
    evaluate_sff_tags can share them without file I/O."""

    def test_schema_gate_pass_on_valid_doc(self):
        r = V._schema_gate(valid_doc(), _SCHEMA)
        self.assertEqual((r.check_id, r.severity, r.status), ("SCHEMA", "error", "pass"))

    def test_schema_gate_fail_on_missing_required_top_level_keys(self):
        r = V._schema_gate({}, _SCHEMA)
        self.assertEqual((r.check_id, r.status), ("SCHEMA", "fail"))
        self.assertNotEqual(r.message, "")

    def test_run_all_checks_returns_ctx_and_ordered_results_without_tag01(self):
        ctx, results = V._run_all_checks(valid_doc(), _SCHEMA)
        self.assertIsInstance(ctx, V._Context)
        self.assertEqual(results[0].check_id, "SCHEMA")
        self.assertEqual(results[0].status, "pass")
        self.assertEqual(results[-1].check_id, "XREF-01")
        self.assertNotIn("TAG-01", {r.check_id for r in results})

    def test_run_all_checks_schema_fail_still_runs_checks(self):
        # A schema-invalid doc must still run the structural checks (per
        # _Context's documented tolerance of missing/malformed sections) rather
        # than raising or short-circuiting.
        ctx, results = V._run_all_checks({}, _SCHEMA)
        self.assertEqual(results[0].check_id, "SCHEMA")
        self.assertEqual(results[0].status, "fail")
        self.assertGreater(len(results), 2)


class TestSkipTolerated(unittest.TestCase):
    # _skip_tolerated is per-tag from the tags.yaml registry rewire on: the
    # tag's tolerated_skips table names a condition per check id.
    EFS = "exported-from-simulator"

    def test_always_tolerated_ids(self):
        ctx = V._Context({})
        for cid in ("STR-03", "STR-13", "CHEM-04"):
            self.assertTrue(V._skip_tolerated(self.EFS, cid, ctx))

    def test_str10_tolerated_only_when_all_streams_empty(self):
        empty = V._Context({"streams": [{"id": "s",
                            "stream_properties": {"total_mass_flow": 0.0}}]})
        nonempty = V._Context({"streams": [{"id": "s",
                              "stream_properties": {"total_mass_flow": 5.0}}]})
        self.assertTrue(V._skip_tolerated(self.EFS, "STR-10", empty))
        self.assertFalse(V._skip_tolerated(self.EFS, "STR-10", nonempty))

    def test_reaction_checks_tolerated_only_without_reactions(self):
        none = V._Context({"units": [{"id": "U1"}]})
        some = V._Context({"units": [{"id": "U1",
                          "reactions": [{"reactant": "A"}]}]})
        for cid in ("UNIT-04", "UNIT-05", "UNIT-06"):
            self.assertTrue(V._skip_tolerated(self.EFS, cid, none))
            self.assertFalse(V._skip_tolerated(self.EFS, cid, some))

    def test_untolerated_id_blocks(self):
        self.assertFalse(V._skip_tolerated(self.EFS, "MET-07", V._Context({})))

    def test_extracted_tags_tolerate_nothing(self):
        ctx = V._Context({})
        for tag in ("extracted-from-prose", "extracted-from-image",
                    "extracted-from-table"):
            for cid in ("STR-03", "STR-13", "CHEM-04", "UNIT-10", "STR-14"):
                self.assertFalse(V._skip_tolerated(tag, cid, ctx))


class TestEarnedTags(unittest.TestCase):
    def _verdict(self, results, doc=None):
        return V._earned_tags(V._Context(doc or {}), results)

    def test_exported_from_simulator_earned_when_only_tolerated_skips(self):
        results = [_R("SCHEMA", "error", "pass"),
                   _R("STR-03", "error", "skip"),   # tolerated
                   _R("CHEM-04", "error", "skip"),  # tolerated
                   _R("CHEM-05", "info", "fail"),   # info ignored
                   _R("UNIT-07", "warning", "pass")]
        v = self._verdict(results)
        self.assertTrue(v["exported-from-simulator"]["earned"])
        self.assertEqual(v["exported-from-simulator"]["blocking"], [])

    def test_exported_from_simulator_blocked_by_untolerated_skip(self):
        results = [_R("SCHEMA", "error", "pass"),
                   _R("MET-07", "error", "skip")]  # not tolerated
        v = self._verdict(results)
        self.assertFalse(v["exported-from-simulator"]["earned"])
        self.assertIn("MET-07", v["exported-from-simulator"]["blocking"])

    def test_exported_from_simulator_blocked_by_warning_fail(self):
        results = [_R("SCHEMA", "error", "pass"),
                   _R("UNIT-07", "warning", "fail")]
        v = self._verdict(results)
        self.assertFalse(v["exported-from-simulator"]["earned"])

    def test_not_conformant_blocks_every_static_tag(self):
        results = [_R("SCHEMA", "error", "pass"),
                   _R("STR-02", "error", "fail"),  # error-fail => not conformant
                   _R("UNIT-10", "warning", "pass"),
                   _R("STR-14", "warning", "pass")]
        v = self._verdict(results)
        self.assertFalse(v["exported-from-simulator"]["earned"])
        self.assertFalse(v["extracted-from-prose"]["earned"])
        self.assertFalse(v["extracted-from-image"]["earned"])
        self.assertFalse(v["extracted-from-table"]["earned"])

    def test_extracted_tags_earned_on_unit_and_stream_presence(self):
        results = [_R("SCHEMA", "error", "pass"),
                   _R("UNIT-10", "warning", "pass"),
                   _R("STR-14", "warning", "pass"),
                   _R("UNIT-02", "error", "skip")]  # outside subset -> irrelevant
        v = self._verdict(results)
        self.assertTrue(v["extracted-from-prose"]["earned"])
        self.assertTrue(v["extracted-from-image"]["earned"])
        self.assertTrue(v["extracted-from-table"]["earned"])

    def test_extracted_tags_denied_when_unit10_fails(self):
        results = [_R("SCHEMA", "error", "pass"),
                   _R("UNIT-10", "warning", "fail"),
                   _R("STR-14", "warning", "pass")]
        v = self._verdict(results)
        self.assertFalse(v["extracted-from-prose"]["earned"])

    def test_declared_reflects_metadata_tags(self):
        doc = {"metadata": {"tags": ["extracted-from-prose"]}}
        v = self._verdict([_R("SCHEMA", "error", "pass")], doc)
        self.assertTrue(v["extracted-from-prose"]["declared"])
        self.assertFalse(v["exported-from-simulator"]["declared"])

    def test_reproducible_static_earned_is_none(self):
        v = self._verdict([_R("SCHEMA", "error", "pass")])
        self.assertIsNone(v["reproducible"]["earned"])

    def test_reproducible_precondition_blocks_without_recipe(self):
        v = self._verdict([_R("SCHEMA", "error", "pass"),
                           _R("MET-07", "error", "skip")])
        self.assertTrue(v["reproducible"]["blocking"])  # no recipe / no rtol

    def test_reproducible_precondition_holds_with_recipe_and_rtol(self):
        doc = {"metadata": {"reproducibility": {
            "environment": {"content": "x", "sha256": "y"},
            "load_script": {"content": "x", "sha256": "y"},
            "comparison_rtol": 1e-4}}}
        v = self._verdict([_R("SCHEMA", "error", "pass"),
                           _R("MET-07", "error", "pass")], doc)
        self.assertEqual(v["reproducible"]["blocking"], [])


class TestTagGate(unittest.TestCase):
    def _gate(self, doc, results):
        return V._tag_gate(V._Context(doc), results)

    def test_skips_when_no_tags(self):
        r = self._gate({"metadata": {}}, [_R("SCHEMA", "error", "pass")])
        self.assertEqual((r.check_id, r.status), ("TAG-01", "skip"))

    def test_passes_when_declared_tag_is_earned(self):
        doc = {"metadata": {"tags": ["extracted-from-prose"]}}
        results = [_R("SCHEMA", "error", "pass"),
                   _R("UNIT-10", "warning", "pass"),
                   _R("STR-14", "warning", "pass")]
        r = self._gate(doc, results)
        self.assertEqual((r.severity, r.status), ("error", "pass"))

    def test_fails_when_declared_tag_is_not_earned(self):
        doc = {"metadata": {"tags": ["extracted-from-prose"]}}
        results = [_R("SCHEMA", "error", "pass"),
                   _R("UNIT-10", "warning", "fail"),
                   _R("STR-14", "warning", "pass")]
        r = self._gate(doc, results)
        self.assertEqual((r.severity, r.status), ("error", "fail"))

    def test_fails_when_reproducible_precondition_unmet(self):
        doc = {"metadata": {"tags": ["reproducible"]}}
        results = [_R("SCHEMA", "error", "pass"),
                   _R("MET-07", "error", "skip")]  # no recipe
        r = self._gate(doc, results)
        self.assertEqual(r.status, "fail")


class TestEvaluateSffTagsStatic(unittest.TestCase):
    def _tmp(self, doc):
        d = tempfile.mkdtemp()
        p = Path(d) / "doc.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return str(p)

    def test_static_reports_reproducible_earned_none(self):
        doc = {"metadata": {}, "units": [{"id": "U1", "unit_type": "Mixer"}],
               "streams": [{"id": "s1"}]}
        out = V.evaluate_sff_tags(self._tmp(doc), str(SCHEMA_PATH))
        self.assertIsNone(out["reproducible"]["earned"])
        self.assertIn("exported-from-simulator", out)
        self.assertIsInstance(out["extracted-from-prose"]["earned"], bool)
