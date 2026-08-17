# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 4: TAG-01's `reproducible` STATIC precondition (recipe present +
# comparison_rtol + MET-07), end-to-end through the full validator. No harness
# runs in this tier.
#
# UPDATE (schema v0.1.3): `metadata.tags` is now a schema-declared property, so
# `test_claim_with_valid_recipe_passes_precondition` has been reverted to drive
# the full file-based `validate_doc()` pipeline directly -- the workaround below,
# kept only as history, is no longer needed.
#
# Prior DEVIATION note (schema v0.1.2 and earlier, no longer applicable): as of
# that state, `metadata.tags` was not yet a schema-declared property -- see the
# identical prior DEVIATION note in tests/tier4/test_tag_checks_e2e.py. Writing
# `metadata.tags` straight into the JSON document tripped the schema's
# `additionalProperties: {"type": "string"}` on unrecognized keys, which failed
# the SCHEMA check and (correctly, per `_conformant`) denied every tag via
# non-conformance before TAG-01's earning logic was even exercised -- so the
# positive "reproducible static precondition met -> TAG-01 pass -> is_valid
# True" path could not be driven through the full file-based pipeline.
# `metadata.reproducibility` (recipe + comparison_rtol) was already schema-legal
# (that object has no additionalProperties restriction), so only the `tags` key
# was the confound. The test instead computed (ctx, results) via the in-memory
# `_run_all_checks` core on the tag-free (schema-clean) doc, then gated on those
# SAME results with a second `_Context` whose only difference was a declared
# `metadata.tags` -- exactly what `_tag_gate` would see once the schema
# supported the field -- and reconstructed `is_valid` from the combined result
# set the same way `validate_flowsheet_against_SFF` does. That future state is
# now the present, so the workaround is gone.

import hashlib
import unittest

from tests._docs import valid_doc
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests.tier4._run import validate_doc


def _recipe(env="name: e\n", load="def load():\n    pass\n"):
    def block(content, filename, fmt):
        return {"format": fmt, "filename": filename, "content": content,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
    return {"environment": block(env, "environment.yaml", "conda-environment-yaml"),
            "load_script": block(load, "load.py", "python"),
            "comparison_rtol": 1e-4}


class TestReproduciblePrecondition(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(4)
        super().setUpClass()

    def test_claim_with_valid_recipe_passes_precondition(self):
        """reproducible claim + complete recipe + matching digests + comparison_
        rtol -> TAG-01 pass (static precondition met; harness not run here).
        Driven through the full file-based validate_doc() pipeline now that
        metadata.tags is a schema-declared property (schema v0.1.3)."""
        doc = valid_doc()
        doc["metadata"]["reproducibility"] = _recipe()
        doc["metadata"]["tags"] = ["reproducible"]
        is_valid, by_id = validate_doc(doc)
        r = by_id["TAG-01"][0]
        self.assertEqual((r.severity, r.status), ("error", "pass"))
        self.assertTrue(is_valid)

    def test_claim_without_comparison_rtol_is_error(self):
        """reproducible claim + recipe but no comparison_rtol -> TAG-01 error
        (precondition unmet); is_valid False. (Once Task 5 adds the schema
        if/then for comparison_rtol, this doc will also fail the schema gate --
        today the TAG-01 precondition alone already denies it.)"""
        doc = valid_doc()
        recipe = _recipe()
        del recipe["comparison_rtol"]
        doc["metadata"]["reproducibility"] = recipe
        doc["metadata"]["tags"] = ["reproducible"]
        is_valid, by_id = validate_doc(doc)
        self.assertEqual(by_id["TAG-01"][0].status, "fail")
        self.assertFalse(is_valid)

    def test_claim_with_bad_digest_is_met07_and_tag01_error(self):
        """reproducible claim + recipe whose content does not match its sha256 ->
        MET-07 error AND TAG-01 error."""
        doc = valid_doc()
        recipe = _recipe()
        recipe["environment"]["sha256"] = "0" * 64
        doc["metadata"]["reproducibility"] = recipe
        doc["metadata"]["tags"] = ["reproducible"]
        is_valid, by_id = validate_doc(doc)
        self.assertEqual(by_id["MET-07"][0].status, "fail")
        self.assertEqual(by_id["TAG-01"][0].status, "fail")
        self.assertFalse(is_valid)
