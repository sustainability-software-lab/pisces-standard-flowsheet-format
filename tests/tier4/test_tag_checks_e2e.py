# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 4: TAG-01 earning/denial end-to-end on valid_doc() mutations. valid_doc()
# has units + streams identified, so it earns the extracted tags; it lacks
# utilities/costs/reproducibility, so it does NOT earn exported-from-simulator
# (MET-07 skips) -- which makes it a clean subject for both directions.
#
# UPDATE (schema v0.1.3): `metadata.tags` is now a schema-declared property (an
# enum-string array), so `test_true_extracted_claim_passes` has been reverted to
# drive the full file-based `validate_doc()` pipeline directly -- the workaround
# below, kept only as history, is no longer needed. The three denial tests now
# also assert on the TAG-01 message's blocking-check content (UNIT-10, MET-07,
# "no reproducibility recipe"), not just the pass/fail verdict, so a latent
# earning-logic bug that flips the wrong tag's verdict can no longer pass
# silently.
#
# Prior DEVIATION note (schema v0.1.2 and earlier, no longer applicable): as of
# that state, `metadata.tags` was not yet a schema-declared property. Writing
# `metadata.tags` straight into the JSON document therefore tripped the
# schema's own `additionalProperties: {"type": "string"}` on unrecognized keys,
# which failed the SCHEMA check and (correctly, per `_conformant`) denied every
# tag via non-conformance before TAG-01's earning logic was even exercised --
# so the positive "declared tag earned -> TAG-01 pass -> is_valid True" path
# could not be driven through the full file-based pipeline. The test instead
# computed (ctx, results) via the in-memory `_run_all_checks` core on the
# tag-free doc (schema-clean), then gated on those SAME results with a second
# `_Context` whose only difference was a declared `metadata.tags` -- exactly
# what `_tag_gate` would see once the schema supported the field. That future
# state is now the present, so the workaround is gone.

import json
import tempfile
import unittest
from pathlib import Path

from tests._docs import valid_doc
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests.tier4._run import SCHEMA_PATH, V, validate_doc


class TestTAG01(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_no_tags_skips(self):
        """No metadata.tags -> TAG-01 skip; is_valid True."""
        is_valid, by_id = validate_doc(valid_doc())
        self.assertEqual(by_id["TAG-01"][0].status, "skip")
        self.assertTrue(is_valid)

    def test_true_extracted_claim_passes(self):
        """valid_doc() earns extracted-from-prose (units+streams identified) ->
        claiming it -> TAG-01 pass; is_valid True. Driven through the full
        file-based validate_doc() pipeline now that metadata.tags is a
        schema-declared property (schema v0.1.3)."""
        doc = valid_doc()
        doc["metadata"]["tags"] = ["extracted-from-prose"]
        doc["metadata"]["prose_type"] = "journal_article"  # required beside the tag (v0.2.2)
        doc["metadata"]["extraction_method"] = "manual"  # likewise
        is_valid, by_id = validate_doc(doc)
        r = by_id["TAG-01"][0]
        self.assertEqual((r.severity, r.status), ("error", "pass"))
        self.assertTrue(is_valid)

    def test_false_extracted_claim_is_error(self):
        """Empty units -> extracted-from-* denied -> claiming it -> TAG-01 error
        fail; is_valid False (UNIT-10 warning-fail alone would not flip is_valid,
        but the false tag claim does). Discriminating assertion: the TAG-01
        message names UNIT-10 as the blocking check, so a latent earning-logic
        bug that denies the tag for the wrong reason (or a different tag
        entirely) cannot pass silently."""
        doc = valid_doc()
        doc["units"] = []
        doc["metadata"]["tags"] = ["extracted-from-prose"]
        # Keep the doc schema-valid so the denial is UNIT-10's, not the schema's.
        doc["metadata"]["prose_type"] = "journal_article"
        doc["metadata"]["extraction_method"] = "manual"
        is_valid, by_id = validate_doc(doc)
        r = by_id["TAG-01"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertIn("extracted-from-prose", r.message)
        self.assertIn("UNIT-10", r.message)
        self.assertFalse(is_valid)

    def test_false_exported_from_simulator_claim_is_error(self):
        """valid_doc() lacks a reproducibility recipe -> MET-07 skips -> exported-
        from-simulator denied -> claiming it -> TAG-01 error fail.
        Discriminating assertion: the TAG-01 message names MET-07 among the
        blocking checks, pinning the denial to the actual precondition (no
        recipe) rather than some other, coincidentally-failing check."""
        doc = valid_doc()
        doc["metadata"]["tags"] = ["exported-from-simulator"]
        is_valid, by_id = validate_doc(doc)
        r = by_id["TAG-01"][0]
        self.assertEqual(r.status, "fail")
        self.assertIn("exported-from-simulator", r.message)
        self.assertIn("MET-07", r.message)
        self.assertFalse(is_valid)

    def test_reproducible_claim_without_recipe_is_error(self):
        """Claiming reproducible with no reproducibility block -> TAG-01 error
        (precondition unmet). Discriminating assertion: the TAG-01 message
        names the actual missing-recipe precondition, not just "not
        conformant" or some other blocking reason."""
        doc = valid_doc()
        doc["metadata"]["tags"] = ["reproducible"]
        is_valid, by_id = validate_doc(doc)
        r = by_id["TAG-01"][0]
        self.assertEqual(r.status, "fail")
        self.assertIn("reproducible", r.message)
        self.assertIn("no reproducibility recipe", r.message)
        self.assertFalse(is_valid)


class TestExtractedTagSourceTypeE2E(RealBiosteamTestCase):
    """evaluate_sff_tags end-to-end (v0.2.2): an extracted-from-* tag is reported
    earned only when its extraction source-type field and extraction_method are
    present, so a producer
    that stamps whatever evaluate_sff_tags reports earned always writes a
    schema-valid file (the schema requires the field beside the tag)."""

    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def _evaluate(self, doc):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "doc.json"
            p.write_text(json.dumps(doc), encoding="utf-8")
            return V.evaluate_sff_tags(str(p), str(SCHEMA_PATH))

    def test_not_earned_without_field(self):
        """valid_doc() (units + streams identified, no source type or
        extraction method) -> no extracted-from-* tag earned; blocking names
        the missing fields."""
        out = self._evaluate(valid_doc())
        self.assertFalse(out["extracted-from-prose"]["earned"])
        self.assertEqual(out["extracted-from-prose"]["blocking"],
                         ["metadata.prose_type", "metadata.extraction_method"])
        self.assertFalse(out["extracted-from-image"]["earned"])
        self.assertEqual(out["extracted-from-image"]["blocking"],
                         ["metadata.image_type", "metadata.extraction_method"])

    def test_not_earned_without_extraction_method(self):
        """The source-type field alone is not enough."""
        doc = valid_doc()
        doc["metadata"]["prose_type"] = "patent"
        out = self._evaluate(doc)
        self.assertFalse(out["extracted-from-prose"]["earned"])
        self.assertEqual(out["extracted-from-prose"]["blocking"],
                         ["metadata.extraction_method"])

    def test_extracted_doc_without_phase_molar_flow_is_valid(self):
        """v0.2.2: per-phase total_molar_flow is optional beside an
        extracted-from-* tag; such a file validates end to end, earns the tag,
        and TAG-01 passes."""
        doc = valid_doc()
        for s in doc["streams"]:
            for phase in s["stream_properties"]["phases"].values():
                phase.pop("total_molar_flow", None)
        doc["metadata"]["prose_type"] = "patent"
        doc["metadata"]["extraction_method"] = {"pipeline": "llm", "version": 1}
        doc["metadata"]["tags"] = ["extracted-from-prose"]
        is_valid, by_id = validate_doc(doc)
        self.assertEqual(by_id["SCHEMA"][0].status, "pass")
        self.assertEqual(by_id["TAG-01"][0].status, "pass")
        self.assertTrue(is_valid)

    def test_extracted_doc_with_partial_data_is_valid(self):
        """Partial literature data beside an extracted-from-* tag: streams with
        only some stream_properties fields, a mass-basis composition, a cost
        correlation without CEPCI/power rate/basis units/installation factor, a
        heat utility without temperature/pressure/composition, and TEA_year
        'not_reported' -> the file validates end to end, MET-04 skips, and
        TAG-01 passes."""
        doc = valid_doc()
        feed, prod = doc["streams"][0], doc["streams"][1]
        feed["stream_properties"] = {"temperature": 300.0, "total_mass_flow": 46.07}
        prod["stream_properties"] = {"phases": {"l": {"composition": [
            {"component_name": "Ethanol", "mass_fraction": 1.0}]}}}
        unit = doc["units"][0]
        unit["purchase_costs"] = {"Mixer": 1.0e6}
        unit["purchase_cost_correlations"] = {"Mixer": {
            "correlation_type": "power_law", "basis": "Flow rate", "reference_size": 500.0,
            "reference_cost": 2.855e7, "exponent": 0.6}}
        doc["utilities"]["heat_utilities"] = [
            {"id": "steam", "quantity_units_for_utility_results": "kJ/hr"}]
        doc["metadata"]["TEA_year"] = "not_reported"
        doc["metadata"]["prose_type"] = "journal_article"
        doc["metadata"]["extraction_method"] = "manual"
        doc["metadata"]["tags"] = ["extracted-from-prose"]
        is_valid, by_id = validate_doc(doc)
        self.assertEqual(by_id["SCHEMA"][0].status, "pass")
        self.assertEqual(by_id["MET-04"][0].status, "skip")
        self.assertEqual(by_id["TAG-01"][0].status, "pass")
        self.assertTrue(is_valid, [(r.check_id, r.message) for rs in by_id.values() for r in rs
                                   if r.status == "fail" and r.severity == "error"])

    def test_partial_data_without_extracted_tag_is_invalid(self):
        """The same partial streams in an untagged file -> the schema gate fails."""
        doc = valid_doc()
        doc["streams"][0]["stream_properties"] = {"temperature": 300.0}
        is_valid, by_id = validate_doc(doc)
        self.assertEqual(by_id["SCHEMA"][0].status, "fail")
        self.assertFalse(is_valid)

    def test_earned_then_declarable(self):
        """With prose_type and extraction_method set, extracted-from-prose
        is earned, and declaring it yields a valid file with TAG-01 passing."""
        doc = valid_doc()
        doc["metadata"]["prose_type"] = "patent"
        doc["metadata"]["extraction_method"] = None  # any value, null included
        out = self._evaluate(doc)
        self.assertTrue(out["extracted-from-prose"]["earned"])
        self.assertTrue(out["extracted-from-table"]["earned"])
        self.assertFalse(out["extracted-from-image"]["earned"])
        doc["metadata"]["tags"] = ["extracted-from-prose"]
        is_valid, by_id = validate_doc(doc)
        self.assertEqual(by_id["TAG-01"][0].status, "pass")
        self.assertTrue(is_valid)
