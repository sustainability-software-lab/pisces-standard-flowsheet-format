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

import unittest

from tests._docs import valid_doc
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests.tier4._run import validate_doc


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
