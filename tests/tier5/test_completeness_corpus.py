# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 5: the completeness scorer (pisces_sff/completeness/_completeness.py)
# over the committed corpus. score_flowsheet_completeness runs the real
# validator (QU-02 imports thermosteam), hence RealBiosteamTestCase. Two
# responsibilities: every corpus file scores sanely, and on the real
# current-shape file (SF_BST_01.json) deleting information never raises the
# score -- the R1.0 "applicability by need, never by data" contract, checked
# on a deterministic, stratified sample of deletions (an exhaustive sweep of
# its ~5,500 deletable values takes minutes; this file is sized to run in
# ~15 s, of which ~5 s is the first call's thermosteam import).

import random
import unittest
from pathlib import Path

from tests._completeness_loader import C
from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = (REPO_ROOT / "pisces_sff" / "export" / "exported_flowsheets"
              / "bioindustrial_park")
CORN = CORPUS_DIR / "SF_BST_01.json"

TOL = 1e-12
SEED = 20260923
PER_STRATUM = 7       # random deletions per (section, leaf|sub) stratum
SECTIONS = ("units", "streams", "chemicals", "utilities", "metadata",
            "quantity_units_global")

# Keys carrying identity or references: deleting them rewires the document
# instead of removing one piece of stated information (as in Tier 1).
_STRUCTURAL_KEYS = frozenset({"id", "source_unit_id", "sink_unit_id",
                              "component_name", "stream_id"})


def deletable_paths(node, path=()):
    """Yield (path, kind) for every value held under a dict key, skipping
    _STRUCTURAL_KEYS: 'leaf' for a scalar, 'sub' for a whole dict or list.
    List elements are recursed into, never deleted themselves."""
    items = (node.items() if isinstance(node, dict)
             else enumerate(node) if isinstance(node, list) else ())
    for key, value in items:
        if isinstance(node, dict) and key in _STRUCTURAL_KEYS:
            continue
        if isinstance(value, (dict, list)):
            if isinstance(node, dict):
                yield path + (key,), "sub"
            yield from deletable_paths(value, path + (key,))
        elif isinstance(node, dict):
            yield path + (key,), "leaf"


def without(doc, path):
    """`doc` without the value at `path`. Only the containers along the path
    are copied (shallowly); everything else is shared, which is safe because
    the scorer never mutates its input -- and far cheaper than a deep copy
    of the corpus file."""
    root = dict(doc)
    node = root
    for key in path[:-1]:
        child = node[key]
        child = dict(child) if isinstance(child, dict) else list(child)
        node[key] = child
        node = child
    del node[path[-1]]
    return root


def _first(positions, message):
    """The first position of a non-empty list (fails loudly otherwise)."""
    if not positions:
        raise AssertionError(message)
    return positions[0]


def targeted_paths(doc):
    """The deletions that exercise the fixed monotonicity defects, by name:
    design_simulation_method of three core and three custom units whose
    unit_type does not name their class (so the class is lost or only
    inferred -- CMP-COST-04 / CMP-UOP-03), a whole thermo_property_package
    (CMP-THM-07), a custom and a core unit's purchase_cost_correlations
    entry and its purchase cost (CMP-COST-04's item union), the
    reactions of a multi-reaction unit (CMP-RXN-03), a non-empty feed's
    temperature, an empty boundary stream's flow and stream_properties
    (CMP-FEED-01/02), a phase, a unit's design_input_specs, a chemical's
    registry_id, and each top-level section."""
    units, streams = doc["units"], doc["streams"]
    described = [(i, C._is_core_class(*C._dsm_class(u)))
                 for i, u in enumerate(units)
                 if C._dsm_class(u)[0] and C._dsm_class(u)[0] != u.get("unit_type")]
    core = [i for i, is_core in described if is_core][:3]
    custom = [i for i, is_core in described if not is_core][:3]
    assert len(core) == 3 and len(custom) == 3, (core, custom)
    paths = [("units", i, "design_simulation_method") for i in core + custom]

    def correlated(indices):
        return [i for i in indices if units[i].get("purchase_cost_correlations")]
    core_all = [i for i, is_core in described if is_core]
    custom_all = [i for i, is_core in described if not is_core]
    for i in (_first(correlated(custom_all), "no correlated custom unit"),
              _first(correlated(core_all), "no correlated core unit")):
        item = next(iter(units[i]["purchase_cost_correlations"]))
        paths.append(("units", i, "purchase_cost_correlations", item))
        if item in (units[i].get("purchase_costs") or {}):
            paths.append(("units", i, "purchase_costs", item))
    paths.append(("units", core[0], "thermo_property_package"))
    paths.append(("units", core[0], "design_input_specs"))
    multi = _first([i for i, u in enumerate(units)
                    if len(u.get("reactions") or []) >= 2],
                   "no multi-reaction unit")
    paths.append(("units", multi, "reactions"))

    def boundary(stream):
        return C._is_boundary(stream.get("source_unit_id")) != C._is_boundary(
            stream.get("sink_unit_id"))
    feed = _first([i for i, s in enumerate(streams)
                   if C._is_boundary(s.get("source_unit_id"))
                   and not C._is_empty_stream(s)], "no non-empty feed")
    empty = _first([i for i, s in enumerate(streams)
                    if boundary(s) and C._is_empty_stream(s)],
                   "no empty boundary stream")
    phased = _first([i for i, s in enumerate(streams)
                     if s.get("stream_properties", {}).get("phases")],
                    "no stream with phases")
    phase = next(iter(streams[phased]["stream_properties"]["phases"]))
    paths += [("streams", feed, "stream_properties", "temperature"),
              ("streams", empty, "stream_properties", "total_mass_flow"),
              ("streams", empty, "stream_properties"),
              ("streams", phased, "stream_properties", "phases", phase)]
    chem = _first([i for i, c in enumerate(doc["chemicals"])
                   if c.get("registry_id")], "no chemical with a registry_id")
    paths.append(("chemicals", chem, "registry_id"))
    paths += [(section,) for section in SECTIONS]
    return paths


def sampled_paths(doc):
    """targeted_paths plus a deterministic stratified sample: PER_STRATUM
    paths from each (section, leaf|sub) stratum below the top level, drawn
    with a fixed seed from the sorted stratum."""
    strata = {}
    for path, kind in deletable_paths(doc):
        if len(path) > 1 and path[0] in SECTIONS:
            strata.setdefault((path[0], kind), []).append(path)
    rng = random.Random(SEED)
    paths = list(targeted_paths(doc))
    for key in sorted(strata):
        pool = sorted(strata[key], key=repr)
        paths += rng.sample(pool, min(PER_STRATUM, len(pool)))
    return list(dict.fromkeys(paths))


class TestCompletenessCorpus(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(5)        # gate first (skips before any eviction work)
        super().setUpClass()       # evict the Tier-1 stub so thermosteam is real
        cls.corn = C._read_flowsheet(str(CORN))
        cls.base = C.score_flowsheet_completeness(cls.corn)

    def test_every_corpus_file_scores_sanely(self):
        """score_flowsheet_completeness on every *.json in
        exported_flowsheets/bioindustrial_park (current-shape SF_BST_01 and
        the 17 legacy 0.0.3 files) -> never raises; score and
        representable_score in [0, 1]; representable_score >= score (the
        non-representable criteria are the low-scoring schema gaps); every
        scored criterion in [0, 1]."""
        files = sorted(CORPUS_DIR.glob("*.json"))
        self.assertEqual(len(files), 18)
        for path in files:
            with self.subTest(file=path.name):
                report = C.score_flowsheet_completeness(str(path))
                self.assertTrue(0.0 <= report.score <= 1.0, report.score)
                self.assertTrue(0.0 <= report.representable_score <= 1.0)
                self.assertGreaterEqual(report.representable_score,
                                        report.score)
                for c in report.criteria:
                    if c.status == "scored":
                        self.assertTrue(0.0 <= c.score <= 1.0, c)

    def test_sample_covers_the_targeted_deletions(self):
        """sampled_paths(SF_BST_01) -> deterministic (same list twice);
        contains every targeted deletion plus >= 60 drawn paths; draws from
        every non-empty (section, leaf|sub) stratum below the top level
        (chemicals hold scalars only, so 11 strata)."""
        paths = sampled_paths(self.corn)
        self.assertEqual(paths, sampled_paths(self.corn))
        targeted = targeted_paths(self.corn)
        self.assertTrue(set(targeted) <= set(paths))
        self.assertGreaterEqual(len(paths) - len(targeted), 60)
        kinds = dict(deletable_paths(self.corn))
        strata = {(p[0], k) for p, k in kinds.items()
                  if len(p) > 1 and p[0] in SECTIONS}
        drawn = {(p[0], kinds[p]) for p in paths[len(targeted):]}
        self.assertEqual(drawn, strata)
        self.assertEqual(len(strata), 11)

    def test_deleting_values_never_raises_score(self):
        """Deletion monotonicity on the REAL SF_BST_01.json: for each
        sampled deletion (targeted_paths + the stratified sample, leaves and
        whole sub-objects), score_flowsheet_completeness -> score <= the
        full file's score (tolerance 1e-12), and every criterion scored on
        the full file stays scored with a score <= its full-file score (a
        whole top-level section is held to the overall bound only). The
        per-criterion bound is what catches a defect here: on this file a
        criterion's rise is usually outweighed by drops elsewhere. Expected:
        no violation, and some deletions lower the score."""
        base = self.base
        before = {c.criterion_id: c for c in base.criteria}
        violations, lowered = [], 0
        paths = sampled_paths(self.corn)
        for path in paths:
            report = C.score_flowsheet_completeness(without(self.corn, path))
            if report.score > base.score + TOL:
                violations.append((path, "overall", report.score - base.score))
            lowered += report.score < base.score - TOL
            if len(path) == 1:
                continue
            for c in report.criteria:
                b = before[c.criterion_id]
                if b.status == "scored" and (
                        c.status != "scored" or c.score > b.score + TOL):
                    violations.append((path, c.criterion_id, c.status, c.score))
        self.assertEqual(violations, [])
        self.assertGreater(lowered, len(paths) // 4)


if __name__ == "__main__":
    unittest.main()
