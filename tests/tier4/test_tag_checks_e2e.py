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
# DEVIATION from the task brief's test_true_extracted_claim_passes: as of this
# task, `metadata.tags` is not yet a schema-declared property -- that lands with
# the schema version bump in a later task. Writing `metadata.tags` straight into
# the JSON document therefore trips the schema's own
# `additionalProperties: {"type": "string"}` on unrecognized keys, which fails
# the SCHEMA check and (correctly, per _conformant) denies every tag via
# non-conformance before TAG-01's earning logic is even exercised -- so the
# positive "declared tag earned -> TAG-01 pass -> is_valid True" path cannot be
# driven through the full file-based pipeline yet. No `_CHECKS` entry reads
# `metadata.tags`, so a schema-legal document that passes the same checks would
# gate identically once that schema property exists. This test therefore
# reproduces that future state exactly: it computes (ctx, results) via the
# now-in-memory `_run_all_checks` core on the tag-free doc (schema-clean), then
# gates on those SAME results with a second _Context whose only difference is a
# declared `metadata.tags` -- exactly what `_tag_gate` will see once the schema
# supports the field. The other four tests are unaffected: their expected
# outcome is `fail`/`is_valid False` either way, so they exercise real
# TAG-01 denial semantics today.

import json
import unittest

from tests._docs import valid_doc
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests._validate_loader import V
from tests.tier4._run import validate_doc, SCHEMA_PATH

with open(SCHEMA_PATH, encoding="utf-8") as _f:
    _SCHEMA = json.load(_f)


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
        claiming it -> TAG-01 pass. See the module-level DEVIATION note: this
        drives _run_all_checks/_tag_gate directly (the schema doesn't accept
        metadata.tags yet) rather than round-tripping through a JSON file whose
        `tags` key the schema would itself reject."""
        doc = valid_doc()
        ctx, results = V._run_all_checks(doc, _SCHEMA)
        tagged_ctx = V._Context(
            {**doc, "metadata": {**doc["metadata"],
                                 "tags": ["extracted-from-prose"]}})
        r = V._tag_gate(tagged_ctx, results)
        self.assertEqual((r.severity, r.status), ("error", "pass"))

    def test_false_extracted_claim_is_error(self):
        """Empty units -> extracted-from-* denied -> claiming it -> TAG-01 error
        fail; is_valid False (UNIT-10 warning-fail alone would not flip is_valid,
        but the false tag claim does)."""
        doc = valid_doc()
        doc["units"] = []
        doc["metadata"]["tags"] = ["extracted-from-prose"]
        is_valid, by_id = validate_doc(doc)
        r = by_id["TAG-01"][0]
        self.assertEqual((r.severity, r.status), ("error", "fail"))
        self.assertFalse(is_valid)

    def test_false_exported_from_simulator_claim_is_error(self):
        """valid_doc() lacks a reproducibility recipe -> MET-07 skips -> exported-
        from-simulator denied -> claiming it -> TAG-01 error fail."""
        doc = valid_doc()
        doc["metadata"]["tags"] = ["exported-from-simulator"]
        is_valid, by_id = validate_doc(doc)
        self.assertEqual(by_id["TAG-01"][0].status, "fail")
        self.assertFalse(is_valid)

    def test_reproducible_claim_without_recipe_is_error(self):
        """Claiming reproducible with no reproducibility block -> TAG-01 error
        (precondition unmet)."""
        doc = valid_doc()
        doc["metadata"]["tags"] = ["reproducible"]
        is_valid, by_id = validate_doc(doc)
        self.assertEqual(by_id["TAG-01"][0].status, "fail")
        self.assertFalse(is_valid)
