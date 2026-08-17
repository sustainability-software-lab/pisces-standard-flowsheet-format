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
# DEVIATION from the task brief's test_claim_with_valid_recipe_passes_precondition:
# as of this task, `metadata.tags` is not yet a schema-declared property (that
# lands with the schema version bump in Task 5) -- see the identical DEVIATION
# note in tests/tier4/test_tag_checks_e2e.py. Writing `metadata.tags` straight
# into the JSON document trips the schema's `additionalProperties:
# {"type": "string"}` on unrecognized keys, which fails the SCHEMA check and
# (correctly, per _conformant) denies every tag via non-conformance before
# TAG-01's earning logic is even exercised -- so the positive "reproducible
# static precondition met -> TAG-01 pass -> is_valid True" path cannot be driven
# through the full file-based pipeline yet. `metadata.reproducibility` (recipe +
# comparison_rtol) IS already schema-legal (that object has no
# additionalProperties restriction), so only the `tags` key is the confound. This
# test therefore reproduces the future state exactly: it computes (ctx, results)
# via the now-in-memory `_run_all_checks` core on the tag-free (schema-clean) doc,
# then gates on those SAME results with a second _Context whose only difference
# is a declared `metadata.tags` -- exactly what `_tag_gate` will see once the
# schema supports the field -- and reconstructs `is_valid` from the combined
# result set the same way `validate_flowsheet_against_SFF` does. The two negative
# tests are unaffected by the confound (their expected outcome, `fail`/`is_valid
# False`, holds either way -- via the schema gate today, and via TAG-01 itself
# regardless), so they stay file-based per the brief.

import hashlib
import json
import unittest

from tests._docs import valid_doc
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests._validate_loader import V
from tests.tier4._run import validate_doc, SCHEMA_PATH

with open(SCHEMA_PATH, encoding="utf-8") as _f:
    _SCHEMA = json.load(_f)


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
        rtol -> TAG-01 pass (static precondition met; harness not run here). See
        the module DEVIATION note: this drives _run_all_checks/_tag_gate directly
        (the schema doesn't accept metadata.tags yet) rather than round-tripping
        through a JSON file whose `tags` key the schema would itself reject."""
        doc = valid_doc()
        doc["metadata"]["reproducibility"] = _recipe()
        ctx, results = V._run_all_checks(doc, _SCHEMA)
        tagged_ctx = V._Context(
            {**doc, "metadata": {**doc["metadata"], "tags": ["reproducible"]}})
        r = V._tag_gate(tagged_ctx, results)
        self.assertEqual((r.severity, r.status), ("error", "pass"))
        is_valid = not any(x.status == "fail" and x.severity == "error"
                           for x in results + [r])
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
