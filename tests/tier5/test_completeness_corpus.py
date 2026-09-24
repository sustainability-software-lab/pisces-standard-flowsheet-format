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

import copy
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
    registry_id, and each top-level section. Revision 2 part B adds: the
    process-water center's design_simulation_method and specs (CMP-UOP-06
    kind by keyword), the phases of the zero-priced natural-gas and air
    feeds (CMP-FEED-02 utility ports identified by composition), a
    formula-named chemical's molar_mass (the corrected CMP-THM-02 name
    tier), and an outlet's phases of a physical-only unit (the R2.4 species
    guard then cannot verify it)."""
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
    # Revision 2 part B branches.
    pwc = _first([i for i, u in enumerate(units)
                  if C._dsm_class(u)[0] == "ProcessWaterCenter"],
                 "no process-water center")
    paths += [("units", pwc, "design_simulation_method"),
              ("units", pwc, "design_input_specs")]
    for sid in ("natural_gas", "dryer_air"):
        i = _first([i for i, s in enumerate(streams) if s.get("id") == sid],
                   f"no stream {sid}")
        paths.append(("streams", i, "stream_properties", "phases"))
    co2 = _first([i for i, c in enumerate(doc["chemicals"])
                  if c.get("id") == "CO2"], "no CO2")
    paths.append(("chemicals", co2, "molar_mass"))
    sc = C._ScoringContext(doc)
    guarded = _first([p for p in range(len(sc.units))
                      if C._species_guarded(sc, p)
                      and sc.unit_outlets.get(sc.units[p].get("id"))],
                     "no guarded physical-only unit")
    outlet = sc.unit_outlets[sc.units[guarded]["id"]][0]
    paths.append(("streams", outlet, "stream_properties", "phases"))
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

    def test_revision_2_part_b_on_the_corn_file(self):
        """Revision 2 part B on SF_BST_01: CMP-UOP-06 applies to the
        process-water center T608 alone (the cost-only plant-air/CIP lump is
        excluded) and scores 0 -- its stream bindings are unstated; the
        zero-priced dryer_air feed is scored like any price (air is not a
        port material) while natural_gas into the dryer D610 keeps half
        credit (R2.9); CO2 (a one-carbon formula id) earns verified name
        credit (R2.3 corrected); the reactive population is exactly the two
        reactor-family units (R2.4); every physical-only unit passes the
        species-consistency guard, so CMP-UOP-04 stays 1."""
        by_id = {c.criterion_id: c for c in self.base.criteria}
        uop6 = by_id["CMP-UOP-06"]
        self.assertEqual((uop6.status, uop6.score), ("scored", 0.0))
        self.assertTrue(all(m.startswith("units[T608]") for m in uop6.missing))
        self.assertTrue(any("process_water_streams" in m for m in uop6.missing))
        feed = by_id["CMP-FEED-02"]
        self.assertFalse(any("dryer_air" in m for m in feed.missing))
        self.assertTrue(any("natural_gas" in m and "natural gas port" in m
                            for m in feed.missing))
        self.assertIn("chemicals[CO2].registry_id (id resolvable by name "
                      "only; molar mass verified)", by_id["CMP-THM-02"].missing)
        sc = C._ScoringContext(self.corn)
        self.assertEqual(len(C._reactive_units(sc)), 2)
        self.assertTrue(all(C._species_consistency(sc, p)[0]
                            for p in range(len(sc.units))))
        self.assertEqual(by_id["CMP-UOP-04"].score, 1.0)

    def test_revision_3_part_b_registry_ids_on_the_corn_file(self):
        """R3.2 review case on SF_BST_01 with the installed `chemicals`
        database (skipped when unavailable), as revised by R4.1/R4.7: the
        two pseudo-components thermosteam does not know (SolubleProtein,
        InsolubleProtein; formula only, 0.25) are exactly the chemicals
        below the name tier -- Starch, Fiber, Yeast, Ash and Cellulose now
        resolve as thermosteam built-ins. CMP-THM-02 is the referenced mean
        (the 4 unreferenced chemicals all score 0.75, above it): (1 + 2 *
        0.25 + 15 * 0.75) / 18 = 0.708. Giving both registry_id 'C'
        (methane: contradicted) or 'InChI=1S/x' (malformed) leaves it there;
        an invented valid-check-digit CAS (98765-43-6) or a fake InChIKey
        reaches only the unresolved tier: (1 + 2 * 0.5 + 15 * 0.75) / 18 =
        0.736. CMP-THM-05 stays scored 0 with both listed in every case;
        Cellulose and Starch given their real but unlisted CAS numbers
        (9004-34-6, 9005-25-8) are still exempt by their built-in names."""
        if C._chemicals_search() is None:
            self.skipTest("chemicals package not importable")
        pseudo = {"SolubleProtein", "InsolubleProtein"}
        below = {c["id"] for c in self.corn["chemicals"]
                 if C._thm_02_credit(c, c["id"])[0] < C.NAME_RESOLVED_CREDIT}
        self.assertEqual(below, pseudo)

        def gamed(ids):
            doc = copy.deepcopy(self.corn)
            for chem in doc["chemicals"]:
                if chem["id"] in ids:
                    chem["registry_id"] = ids[chem["id"]]
            sc = C._ScoringContext(doc)
            return C._score_thm_02(sc)[0], C._score_thm_05(sc)

        base = 12.75 / 18
        for rid, expected in (("C", base), ("InChI=1S/x", base),
                              ("98765-43-6", 13.25 / 18),
                              ("ABCDEFGHIJKLMN-OPQRSTUVWX-Y", 13.25 / 18)):
            thm02, (thm05, _m, missing) = gamed(dict.fromkeys(pseudo, rid))
            with self.subTest(registry_id=rid):
                self.assertAlmostEqual(thm02, expected)
                self.assertEqual((thm05, len(missing)), (0.0, 2))
        _thm02, (thm05, _m, missing) = gamed({"Cellulose": "9004-34-6",
                                               "Starch": "9005-25-8"})
        self.assertEqual((thm05, len(missing)), (0.0, 2))
        self.assertFalse(any(m.startswith("chemicals[Cellulose]") for m in missing))

    def test_revision_4_part_a_on_the_corn_file(self):
        """Revision 4 part A review cases on SF_BST_01 (real database;
        skipped when unavailable). R4.1: Ash, Yeast, Cellulose (as Glucan),
        Starch and Fiber resolve as thermosteam built-ins with agreeing
        molar masses, so CMP-THM-05 lists exactly SolubleProtein and
        InsolubleProtein (it flagged 7 before). R4.5: CMP-THM-04 is
        representable and = (14 x 1 + 5 x 0.75 + 3 x 0.5) / 22 = 0.875: 14
        flags agree with the stream phases, Ethanol/CO2/O2/N2 are exported
        false yet seen as g and l (contradicted: stream evidence only) and
        Cellulose earns its built-in's locked 's'; H3PO4, P4O10 and SO2
        (false, never in a stream) earn 0.5. R4.2: deleting the phase of
        every heat agent's composition entries lowers CMP-UTIL-01 from 1 to
        (2 x 0.75 + 3) / 5 = 0.9. R4.9: TEA_currency 'x' or 'dollars', or
        TEA_year 99999 or 1.5, lowers CMP-SYS-02 from 3/14 to 2/14. R4.11:
        the real validator's QU-01 and QU-02 both pass, CMP-INT-04 = 1."""
        if C._chemicals_search() is None:
            self.skipTest("chemicals package not importable")
        by_id = {c.criterion_id: c for c in self.base.criteria}
        chems = {c["id"]: c for c in self.corn["chemicals"]}
        for cid, builtin in (("Ash", "Ash"), ("Yeast", "Yeast"),
                             ("Cellulose", "Glucan"), ("Starch", "Starch"),
                             ("Fiber", "Fiber")):
            with self.subTest(chemical=cid):
                self.assertEqual(C._name_record(cid).name, builtin)
                self.assertEqual(C._name_tier(chems[cid], cid), "verified")
        thm05 = by_id["CMP-THM-05"]
        self.assertEqual((thm05.score, sorted(m.split("]")[0] for m in
                                              thm05.missing)),
                         (0.0, ["chemicals[InsolubleProtein",
                                "chemicals[SolubleProtein"]))
        thm04 = by_id["CMP-THM-04"]
        self.assertTrue(thm04.representable)
        self.assertAlmostEqual(thm04.score, (14 + 5 * 0.75 + 3 * 0.5) / 22)
        self.assertEqual(by_id["CMP-UTIL-01"].score, 1.0)
        doc = copy.deepcopy(self.corn)
        for agent in doc["utilities"]["heat_utilities"]:
            for entry in agent["composition"]:
                del entry["phase"]
        util01 = C._score_util_01(C._ScoringContext(doc))[0]
        self.assertAlmostEqual(util01, 0.9)
        self.assertAlmostEqual(by_id["CMP-SYS-02"].score, 3 / 14)
        for field, junk in (("TEA_currency", "x"), ("TEA_currency", "dollars"),
                            ("TEA_year", 99999), ("TEA_year", 1.5)):
            doc = copy.deepcopy(self.corn)
            doc["metadata"][field] = junk
            with self.subTest(field=field, value=junk):
                self.assertAlmostEqual(
                    C._score_sys_02(C._ScoringContext(doc))[0], 2 / 14)
        self.assertEqual(by_id["CMP-INT-04"].score, 1.0)

    def test_revision_3_part_b_quantity_units_on_the_corn_file(self):
        """R3.4 review case: SF_BST_01 with every stream (and phase) and
        utility temperature restated in degC under
        quantity_units_global.temperature = 'degC' scores exactly like the
        original on every criterion (within 1e-9; the review measured
        CMP-UOP-02 0.992 -> 0.912 from false T-guard hits before the fix);
        likewise every pressure in bar, and in kPa. R3.9 / R3.10 on the
        original: CMP-UTIL-02 = 0.8 (natural_gas and s78, CH4 agents priced
        0, earn half credit) and CMP-SYS-02 = 3/14 (currency, dollar year,
        target products)."""
        base = {c.criterion_id: c.score for c in self.base.criteria}
        for field, entry, unit, factor, offset in (
                ("temperature", "temperature", "degC", 1.0, -273.15),
                ("pressure", "pressure", "bar", 1e-5, 0.0),
                ("pressure", "pressure", "kPa", 1e-3, 0.0)):
            doc = copy.deepcopy(self.corn)
            holders = [a for group in doc["utilities"].values() for a in group]
            for stream in doc["streams"]:
                sp = stream.get("stream_properties", {})
                holders += [sp] + list((sp.get("phases") or {}).values())
            for holder in holders:
                if isinstance(holder.get(field), (int, float)):
                    holder[field] = holder[field] * factor + offset
            doc["quantity_units_global"][entry]["quantity_units"] = unit
            report = C.score_flowsheet_completeness(doc)
            for c in report.criteria:
                with self.subTest(unit=unit, criterion=c.criterion_id):
                    if base[c.criterion_id] is None:
                        self.assertIsNone(c.score)
                    else:
                        self.assertAlmostEqual(c.score, base[c.criterion_id],
                                               delta=1e-9)
        self.assertAlmostEqual(base["CMP-UTIL-02"], 0.8)
        self.assertAlmostEqual(base["CMP-SYS-02"], 3 / 14)

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
