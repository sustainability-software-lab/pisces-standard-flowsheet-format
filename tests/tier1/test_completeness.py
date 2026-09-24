# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: pisces_sff/completeness/_completeness.py -- the completeness scorer,
# every helper by name on synthetic documents built from tests/_docs.py. The
# module is loaded by file path (tests/_completeness_loader.py) so nothing
# imports pisces_sff/biosteam. Criterion functions are called directly on a
# _ScoringContext with hand-made validator results, so the real validator's
# QU-02 (which needs the real thermosteam unit registry, poisoned by the Tier-1
# biosteam stub) is never consulted for a verdict. End-to-end calls of
# score_flowsheet_completeness/main either use documents WITHOUT any
# quantity-unit strings, so QU-02 has nothing to parse and never imports
# thermosteam, or compare two scores taken in the same process state, or run
# inside unit_registry(), which fakes the validator's unit parser -- an
# end-to-end score pinned without it would differ between a run of this file
# alone and a run collecting the other Tier-1 modules (which install the
# stub).

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests._completeness_loader import C
from tests._docs import mutate, remove, valid_doc
from tests._validate_loader import V

CR = C._validator().CheckResult


# ---------------------------------------------------------------- helpers

def ctx(doc, results=(), registry=None):
    """A _ScoringContext over `doc` (registry None -> the committed one)."""
    return C._ScoringContext(doc, results, registry=registry)


def run(scorer, doc, results=(), registry=None):
    """(score, message, missing) of one criterion function on `doc`."""
    return scorer(ctx(doc, results, registry))


def unit_doc(unit_fields=None, specs=None, out_T=300.0, out_P=101325.0):
    """valid_doc with U1 turned into the given unit (feed -> U1 -> prod); the
    product's temperature / pressure are set to out_T / out_P (None drops
    the field)."""
    doc = valid_doc()
    unit = {"id": "U1", "unit_type": "Mixer"}
    unit.update(unit_fields or {})
    if specs is not None:
        unit["design_input_specs"] = specs
    doc["units"][0] = unit
    sp = doc["streams"][1]["stream_properties"]
    for key, value in (("temperature", out_T), ("pressure", out_P)):
        if value is None:
            del sp[key]
        else:
            sp[key] = value
    return doc


def label_mixer_doc():
    """valid_doc with a second feed 'feed2' into U1, so U1's bare unit_type
    'Mixer' is a plausible mixer family claim (R4.14: a label-only mixer
    needs >= 2 inlets; valid_doc's one-inlet U1 has unknown family)."""
    doc = valid_doc()
    doc["streams"].append(dict(copy.deepcopy(doc["streams"][0]), id="feed2"))
    return doc


def cls(name):
    """Unit fields naming class `name` on a biosteam source URL (a core-list
    class is then core; R2.6: another repository's URL would make even a
    core-list name a custom class)."""
    return {"design_simulation_method":
            f"{name} on https://github.com/BioSTEAMDevelopmentGroup/biosteam/"
            f"blob/master/biosteam/units/__init__.py"}


def check(check_id, status, severity="error", message="", path="<root>"):
    return CR(check_id, severity, status, message, path)


def legacy_doc():
    """A <=0.0.6-shape document: inline {value, units}, flat composition."""
    return {
        "metadata": {"sff_version": "0.0.3", "TEA_year": 2019,
                     "process_simulator": {"name": "BioSTEAM", "version": "2"}},
        "units": [{"id": "U1", "unit_type": "Mixer",
                   "design_results": {"Flow rate": 5.0}}],
        "streams": [
            {"id": "feed", "source_unit_id": "None", "sink_unit_id": "U1",
             "price": {"value": 0.1, "units": "$/kg"},
             "stream_properties": {
                 "total_mass_flow": {"value": 18.0, "units": "kg/h"},
                 "total_molar_flow": {"value": 1.0, "units": "kmol/h"},
                 "temperature": {"value": 300.0, "units": "K"},
                 "pressure": {"value": 101325.0, "units": ""},
                 "composition": [{"phase": "l", "component_name": "Water",
                                  "mol_fraction": 1.0}]}},
        ],
        "chemicals": [{"id": "Water", "formula": "H2O", "molar_mass": 18.0}],
        "utilities": {"power_utilities": [
            {"id": "grid", "price": {"value": 0.07, "units": "$/kWh"}}]},
    }


def unitless_doc():
    """valid_doc without quantity_units_global: carries no quantity-unit
    string, so the real validator's QU-02 never imports thermosteam."""
    doc = valid_doc()
    remove(doc, "quantity_units_global")
    return doc


# (MW, formula) of the database records the fake name search returns.
KNOWN_RECORDS = {"Water": (18.01528, "H2O"), "Ethanol": (46.06844, "C2H6O"),
                 "Glucose": (180.15588, "C6H12O6"), "CO2": (44.0095, "CO2")}

# A full property package of real thermosteam model class names (as the
# exporter writes them), and a second one differing in gamma.
FULL_PKG = {"mixture": "IdealMixture(include_excess_energies=False)",
            "gamma": "DortmundActivityCoefficients",
            "phi": "IdealFugacityCoefficients",
            "PCF": "MockPoyntingCorrectionFactors"}
UNIFAC_PKG = dict(FULL_PKG, gamma="UNIFACActivityCoefficients")


@contextlib.contextmanager
def name_lookup(resolvable=(), search="fake"):
    """Replace the `chemicals` name search (CMP-THM-02 name tier) with a fake
    that resolves exactly `resolvable` (search=None: package unavailable),
    with an empty lookup cache; restores both afterwards. A resolved name
    returns a record with the MW / formula of KNOWN_RECORDS (None / None
    for others), or of `resolvable[name]` when `resolvable` is a dict of
    (MW, formula)."""
    if search == "fake":
        def search(name):
            if name in resolvable:
                mw, formula = (resolvable[name] if isinstance(resolvable, dict)
                               else KNOWN_RECORDS.get(name, (None, None)))
                return types.SimpleNamespace(MW=mw, formula=formula)
            raise ValueError(f"Chemical name ({name}) not recognized")
    saved_search = list(C._CHEMICALS_SEARCH)
    saved_cache = dict(C._NAME_LOOKUP)
    C._CHEMICALS_SEARCH[:] = [search]
    C._NAME_LOOKUP.clear()
    try:
        yield
    finally:
        C._CHEMICALS_SEARCH[:] = saved_search
        C._NAME_LOOKUP.clear()
        C._NAME_LOOKUP.update(saved_cache)


@contextlib.contextmanager
def unit_registry(parseable=("K", "Pa", "kg/hr", "hr")):
    """Point the validator's QU-02 unit parser (its module-level `_UREG`
    cache of thermosteam's pint registry) at a fake that parses exactly
    `parseable`; restores it afterwards. For end-to-end scores of documents
    that carry unit strings: the Tier-1 thermosteam stub has no unit
    registry, so without this QU-02 (and hence CMP-INT-04 and the overall
    score) would depend on whether that stub or the real thermosteam is in
    sys.modules when the test runs."""
    class Registry:
        @staticmethod
        def Quantity(value, units):
            if units not in parseable:
                raise ValueError(f"unit {units!r} not recognized")
            return value
    validator = C._validator()
    saved = validator._UREG
    validator._UREG = Registry()
    try:
        yield
    finally:
        validator._UREG = saved


# ------------------------------------------------- lazy sibling modules

class TestSiblingModules(unittest.TestCase):
    def test_sibling_module_prefers_sys_modules(self):
        """_sibling_module: a module already in sys.modules under the qualname
        -> that very object is returned (the real package module is reused)."""
        fake = types.ModuleType("fake_completeness_sibling")
        sys.modules["fake.completeness.sibling"] = fake
        try:
            self.assertIs(C._sibling_module("fake.completeness.sibling",
                                            C._VALIDATE_PATH), fake)
        finally:
            del sys.modules["fake.completeness.sibling"]

    def test_sibling_module_loads_by_path_and_caches(self):
        """_sibling_module: absent from sys.modules -> loaded from the file
        path once and cached (second call returns the same object)."""
        a = C._sibling_module("absent.sibling.validate", C._VALIDATE_PATH)
        b = C._sibling_module("absent.sibling.validate", C._VALIDATE_PATH)
        self.assertIs(a, b)
        self.assertTrue(hasattr(a, "_run_all_checks"))

    def test_validator_exposes_in_memory_route(self):
        """_validator() -> the validator module with _run_all_checks,
        _tag_gate, _Context and _SCHEMA_FILE (the in-memory route)."""
        v = C._validator()
        for name in ("_run_all_checks", "_tag_gate", "_Context", "_SCHEMA_FILE"):
            self.assertTrue(hasattr(v, name), name)

    def test_design_spec_registry_loads_committed_file(self):
        """_design_spec_registry() -> (registry dict holding Pump/HXutility,
        None error), cached across calls."""
        registry, error = C._design_spec_registry()
        self.assertIsNone(error)
        self.assertIn("Pump", registry)
        self.assertIn("HXutility", registry)
        self.assertIs(C._design_spec_registry()[0], registry)

    def test_chemicals_search_is_lazy_and_cached(self):
        """_chemicals_search: first call imports chemicals' search_chemical
        (or None when the package is unavailable) and caches it -> the same
        object on the next call."""
        saved = list(C._CHEMICALS_SEARCH)
        C._CHEMICALS_SEARCH.clear()
        try:
            first = C._chemicals_search()
            self.assertIs(C._chemicals_search(), first)
            self.assertTrue(first is None or callable(first))
        finally:
            C._CHEMICALS_SEARCH[:] = saved

    def test_name_resolvable(self):
        """_name_resolvable / _database_record: a name the search resolves
        -> True; one it rejects (ValueError) -> False, cached so the search
        runs once per name; blank / non-string -> False; search unavailable
        (None) or raising anything -> False, never an exception. A built-in
        pseudo-component ('Fiber') the search rejects still resolves (R4.1),
        even with the search unavailable; 'SolubleProtein' (no built-in)
        does not."""
        calls = []

        def search(name):
            calls.append(name)
            if name == "Ethanol":
                return object()
            raise ValueError("not recognized")

        with name_lookup(search=search):
            self.assertTrue(C._name_resolvable("Ethanol"))
            self.assertFalse(C._name_resolvable("SolubleProtein"))
            self.assertFalse(C._name_resolvable("SolubleProtein"))
            self.assertEqual(calls, ["Ethanol", "SolubleProtein"])
            self.assertTrue(C._name_resolvable("Fiber"))
            self.assertIsNone(C._database_record("Fiber"))
            self.assertFalse(C._name_resolvable(""))
            self.assertFalse(C._name_resolvable(None))
        with name_lookup(search=None):
            self.assertFalse(C._name_resolvable("Ethanol"))
            self.assertTrue(C._name_resolvable("Fiber"))
        with name_lookup(search=lambda name: 1 / 0):
            self.assertFalse(C._name_resolvable("Ethanol"))

    def test_name_resolvable_real_database(self):
        """_name_resolvable with the installed `chemicals` package (skipped
        when it cannot be imported): 'Ethanol' and 'Water' resolve in the
        database; 'Fiber' only as a thermosteam built-in (_database_record
        None); 'SolubleProtein' not at all."""
        with name_lookup(search=C._chemicals_search()):
            if C._chemicals_search() is None:
                self.skipTest("chemicals package not importable")
            self.assertTrue(C._name_resolvable("Ethanol"))
            self.assertTrue(C._name_resolvable("Water"))
            self.assertIsNone(C._database_record("Fiber"))
            self.assertTrue(C._name_resolvable("Fiber"))
            self.assertFalse(C._name_resolvable("SolubleProtein"))


# --------------------------------------------------- criteria registry

MIN_YAML = """\
categories:
  thermo: Thermo
criteria:
  - id: CMP-THM-01
    category: thermo
    title: T
    weight: 1
    criticality: essential
    representable_since: "0.0.0"
    rationale: R
    sff_paths: [chemicals]
exclusions:
  - id: EXC-01
    kind: not_capturable
    title: T
    rationale: R
"""


def write_yaml(text):
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestCriteriaRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = C.load_completeness_criteria()

    def _bad(self, old, new):
        path = write_yaml(MIN_YAML.replace(old, new))
        try:
            with self.assertRaises(ValueError):
                C._parse_criteria(path)
        finally:
            os.remove(path)

    def test_code_and_yaml_ids_in_sync(self):
        """_CRITERIA keys == criteria.yaml ids, same order -> every criterion
        has exactly one scorer and vice versa."""
        self.assertEqual(list(C._CRITERIA), list(self.registry["criteria"]))

    def test_default_registry_is_cached(self):
        """load_completeness_criteria() twice -> the same cached object."""
        self.assertIs(C.load_completeness_criteria(), self.registry)

    def test_every_criterion_has_substantive_rationale(self):
        """Every criterion: title and a rationale of >= 100 characters, a
        known category -> documentation-grade registry."""
        for cid, entry in self.registry["criteria"].items():
            self.assertGreaterEqual(len(entry["rationale"]), 100, cid)
            self.assertIn(entry["category"], self.registry["categories"], cid)

    def test_representable_since_matches_schema_history(self):
        """Not-representable (null) criteria are exactly the schema gaps;
        UOP-05 (design-spec units) since 0.0.7 (R5.2: a spec key aliased in
        quantity_units_global), COST-04 since 0.1.2
        (purchase_cost_correlations), SYS-04 since 0.0.6 (reproducibility
        packages); all others since 0.0.0 -- including CMP-THM-04 (R4.5:
        chemicals[].included_in_thermo is a base field)."""
        since = {cid: e["representable_since"]
                 for cid, e in self.registry["criteria"].items()}
        self.assertEqual(since["CMP-THM-04"], "0.0.0")
        self.assertEqual(
            {c for c, v in since.items() if v is None},
            {"CMP-THM-05", "CMP-TOP-02",
             "CMP-RXN-04", "CMP-COST-05", "CMP-COST-06", "CMP-COST-07",
             "CMP-UTIL-05", "CMP-SYS-01", "CMP-SYS-02", "CMP-SYS-03"})
        uop5 = self.registry["criteria"]["CMP-UOP-05"]
        self.assertEqual((uop5["weight"], uop5["criticality"],
                          uop5["representable_since"]),
                         (1, "supplementary", "0.0.7"))
        self.assertEqual(since["CMP-COST-04"], "0.1.2")
        self.assertEqual(since["CMP-SYS-04"], "0.0.6")

    def test_revision_weights(self):
        """Revision 1 part B weights / criticality: THM-05 4, UTIL-03 2,
        SYS-01 3, SYS-02 5; new COST-07 and UTIL-05 weigh 1 (costing);
        CMP-INT-03 is gone, INT-01/02/04 keep 2/2/3. Revision 2 part B: new
        CMP-UOP-06 weighs 2 (costing, unit_ops, since 0.0.0); the header
        says the monotonicity guarantee does not cover deleting whole list
        elements."""
        crit = self.registry["criteria"]
        got = {cid: crit[cid]["weight"] for cid in (
            "CMP-THM-05", "CMP-UTIL-03", "CMP-SYS-01", "CMP-SYS-02",
            "CMP-COST-07", "CMP-UTIL-05", "CMP-INT-01", "CMP-INT-02",
            "CMP-INT-04")}
        self.assertEqual(got, {"CMP-THM-05": 4, "CMP-UTIL-03": 2,
                               "CMP-SYS-01": 3, "CMP-SYS-02": 5,
                               "CMP-COST-07": 1, "CMP-UTIL-05": 1,
                               "CMP-INT-01": 2, "CMP-INT-02": 2,
                               "CMP-INT-04": 3})
        self.assertEqual((crit["CMP-COST-07"]["criticality"],
                          crit["CMP-UTIL-05"]["criticality"]),
                         ("costing", "costing"))
        self.assertNotIn("CMP-INT-03", crit)
        # Revision 2 part B (R2.5): CMP-UOP-06 facility bindings, weight 2,
        # costing, representable in every version (free-form specs).
        uop6 = crit["CMP-UOP-06"]
        self.assertEqual((uop6["weight"], uop6["criticality"],
                          uop6["representable_since"], uop6["category"]),
                         (2, "costing", "0.0.0", "unit_ops"))
        # The monotonicity guarantee is stated for field deletions only.
        header = C.CRITERIA_PATH.read_text(encoding="utf-8")
        self.assertIn("does not cover deleting whole list", header)

    def test_exclusions_listed(self):
        """exclusions -> EXC-01..03 (not_capturable), EXC-04 AgileSystem and
        EXC-05 LCA characterization factors (out_of_scope), EXC-06 property
        overrides and EXC-07 non-default phase-equilibrium parameters of
        database chemicals (undetectable_need, R2.11), DER-01..03 (derived);
        the kind vocabulary includes undetectable_need, and THM-05's
        rationale covers group assignments / BIPs of custom chemicals."""
        kinds = {e["id"]: e["kind"] for e in self.registry["exclusions"]}
        self.assertEqual(kinds, {"EXC-01": "not_capturable",
                                 "EXC-02": "not_capturable",
                                 "EXC-03": "not_capturable",
                                 "EXC-04": "out_of_scope",
                                 "EXC-05": "out_of_scope",
                                 "EXC-06": "undetectable_need",
                                 "EXC-07": "undetectable_need",
                                 "DER-01": "derived", "DER-02": "derived",
                                 "DER-03": "derived"})
        titles = {e["id"]: e["title"] for e in self.registry["exclusions"]}
        self.assertIn("AgileSystem", titles["EXC-04"])
        self.assertIn("LCA", titles["EXC-05"])
        self.assertIn("overrides", titles["EXC-06"])
        self.assertIn("phase-equilibrium", titles["EXC-07"])
        self.assertIn("undetectable_need", C._EXCLUSION_KINDS)
        self.assertIn("group assignments",
                      self.registry["criteria"]["CMP-THM-05"]["rationale"])

    def test_parse_minimal_valid_registry(self):
        """_parse_criteria / load_completeness_criteria(path) on a minimal
        file -> one criterion keyed by id, categories and exclusions intact."""
        path = write_yaml(MIN_YAML)
        try:
            data = C.load_completeness_criteria(path)
        finally:
            os.remove(path)
        self.assertEqual(list(data["criteria"]), ["CMP-THM-01"])
        self.assertEqual(data["categories"], {"thermo": "Thermo"})
        self.assertEqual(data["exclusions"][0]["id"], "EXC-01")

    def test_null_representable_since_accepted(self):
        """representable_since: null -> accepted (a schema gap)."""
        path = write_yaml(MIN_YAML.replace('"0.0.0"', "null"))
        try:
            data = C._parse_criteria(path)
        finally:
            os.remove(path)
        self.assertIsNone(data["criteria"]["CMP-THM-01"]["representable_since"])

    def test_rejects_malformed_entries(self):
        """_parse_criteria raises ValueError for: unknown category, weight 0,
        boolean weight, unknown criticality, non-semver since, bad id, missing
        key, bad exclusion kind, bad exclusion id."""
        self._bad("category: thermo", "category: nope")
        self._bad("weight: 1", "weight: 0")
        self._bad("weight: 1", "weight: true")
        self._bad("criticality: essential", "criticality: vital")
        self._bad('"0.0.0"', '"0.1"')
        self._bad("id: CMP-THM-01", "id: THM-1")
        self._bad("    rationale: R\n    sff_paths", "    sff_paths")
        self._bad("kind: not_capturable", "kind: other")
        self._bad("id: EXC-01", "id: X-1")

    def test_rejects_duplicates(self):
        """Duplicate criterion id, duplicate exclusion id, or a duplicated
        YAML key -> ValueError."""
        crit = MIN_YAML.split("criteria:\n")[1].split("exclusions:")[0]
        self._bad("exclusions:", crit.replace("  - id", "  - id", 1) + "exclusions:")
        exc = MIN_YAML.split("exclusions:\n")[1]
        path = write_yaml(MIN_YAML + exc)
        try:
            with self.assertRaises(ValueError):
                C._parse_criteria(path)
        finally:
            os.remove(path)
        self._bad("title: T\n    weight", "title: T\n    title: U\n    weight")

    def test_rejects_unreadable_and_invalid_yaml(self):
        """Missing file or unparseable YAML or wrong top-level keys ->
        ValueError."""
        with self.assertRaises(ValueError):
            C._parse_criteria(Path(tempfile.gettempdir()) / "no_such_criteria.yaml")
        self._bad("categories:", "categories: [unclosed")
        self._bad("exclusions:", "extra: 1\nexclusions:")

    def test_check_criterion_entry_direct(self):
        """_check_criterion_entry: a well-formed entry passes; sff_paths not
        a list -> ValueError."""
        entry = {"id": "CMP-THM-01", "category": "thermo", "title": "T",
                 "weight": 2.5, "criticality": "costing",
                 "representable_since": None, "rationale": "R",
                 "sff_paths": []}
        C._check_criterion_entry(entry, {"thermo": "T"}, "src")
        with self.assertRaises(ValueError):
            C._check_criterion_entry(dict(entry, sff_paths="x"),
                                     {"thermo": "T"}, "src")

    def test_check_exclusion_entry_direct(self):
        """_check_exclusion_entry: DER-01/derived passes; empty title ->
        ValueError."""
        entry = {"id": "DER-01", "kind": "derived", "title": "T",
                 "rationale": "R"}
        C._check_exclusion_entry(entry, "src")
        with self.assertRaises(ValueError):
            C._check_exclusion_entry(dict(entry, title=" "), "src")

    def test_yaml_load_no_duplicates(self):
        """_yaml_load_no_duplicates: nested duplicate key -> ValueError naming
        the key; clean mapping -> parsed dict."""
        self.assertEqual(C._yaml_load_no_duplicates("a: {b: 1}", "s"),
                         {"a": {"b": 1}})
        with self.assertRaisesRegex(ValueError, "'b'"):
            C._yaml_load_no_duplicates("a: {b: 1, b: 2}", "s")


# -------------------------------------------------- shape-tolerant readers

class TestReaders(unittest.TestCase):
    def test_num(self):
        """_num: int/float -> float; bool, nan, str -> None; legacy inline
        {value, units} -> float; nested dict value -> None."""
        self.assertEqual(C._num(3), 3.0)
        self.assertEqual(C._num(2.5), 2.5)
        self.assertIsNone(C._num(True))
        self.assertIsNone(C._num(float("nan")))
        self.assertIsNone(C._num("3"))
        self.assertEqual(C._num({"value": 7, "units": "K"}), 7.0)
        self.assertIsNone(C._num({"value": {"value": 1}}))

    def test_containers_and_strings(self):
        """_as_dict/_as_list/_dicts coerce non-containers to empty;
        _nonempty_str/_filled reject blanks and empty collections; _clip cuts
        long text with '...'."""
        self.assertEqual(C._as_dict([1]), {})
        self.assertEqual(C._as_list({"a": 1}), [])
        self.assertEqual(C._dicts([{"a": 1}, 2, None]), [{"a": 1}])
        self.assertTrue(C._nonempty_str("x"))
        self.assertFalse(C._nonempty_str("  "))
        self.assertFalse(C._filled(None))
        self.assertFalse(C._filled([]))
        self.assertTrue(C._filled(0))
        self.assertEqual(C._clip("abcdefgh", 6), "abc...")
        self.assertEqual(C._clip("abc", 6), "abc")

    def test_boundary_and_label(self):
        """_is_boundary: only the sentinel 'None' -> True; null (a missing
        key reads as None), '', blanks and a unit id -> False (R5.8).
        _is_dangling: None, '', '  ', a non-string -> True (not stated);
        'None' and a unit id -> False. _label: id -> kind[id]; no id ->
        kind[#pos]."""
        self.assertTrue(C._is_boundary("None"))
        for b in (None, "", "U1"):
            self.assertFalse(C._is_boundary(b))
        for e in (None, "", "  ", 3):
            self.assertTrue(C._is_dangling(e))
        for e in ("None", "U1"):
            self.assertFalse(C._is_dangling(e))
        self.assertEqual(C._label("units", {"id": "U1"}, 0), "units[U1]")
        self.assertEqual(C._label("units", {}, 3), "units[#3]")

    def test_fractions_and_compositions(self):
        """_entry_fraction: valid mol_fraction, else valid mass_fraction,
        else None (1.5 is no fraction). _composition_ok (R2.0): named entries
        whose mol -- or else mass -- fractions lie in [0, 1] and sum to 1
        within FRACTION_SUM_TOL -> True; an unnamed entry, a sum of 0.5, an
        out-of-range fraction or [] -> False. _phases_ok: a zero-flow empty
        phase beside a populated one is fine; a missing or negative phase
        molar flow is not, nor are phases that all carry zero flow."""
        self.assertEqual(C._entry_fraction({"mol_fraction": 0.2}), 0.2)
        self.assertEqual(C._entry_fraction({"mass_fraction": 0.3}), 0.3)
        self.assertEqual(C._entry_fraction({"mol_fraction": 1.5,
                                            "mass_fraction": 0.3}), 0.3)
        self.assertIsNone(C._entry_fraction({"component_name": "A"}))
        a = {"component_name": "A", "mol_fraction": 1.0}
        self.assertTrue(C._composition_ok([a]))
        self.assertTrue(C._composition_ok([
            {"component_name": "A", "mol_fraction": 0.5, "mass_fraction": 0.4},
            {"component_name": "B", "mass_fraction": 0.6}]))
        self.assertFalse(C._composition_ok([{"mol_fraction": 1.0}]))
        self.assertFalse(C._composition_ok([dict(a, mol_fraction=0.5)]))
        self.assertFalse(C._composition_ok([dict(a, mol_fraction=1.2)]))
        self.assertTrue(C._composition_ok([
            dict(a, mol_fraction=0.4995),
            {"component_name": "B", "mol_fraction": 0.5}]))
        self.assertFalse(C._composition_ok([]))
        # legacy flat shape: fractions sum to 1 within each phase tag
        two_phase = [dict(a, phase="l"), {"phase": "s", "component_name": "B",
                                          "mol_fraction": 1.0}]
        self.assertTrue(C._composition_ok(two_phase))
        self.assertFalse(C._composition_ok(two_phase + [dict(a, phase="g",
                                                             mol_fraction=0.5)]))
        self.assertIsNone(C._species_flows({"stream_properties": {
            "total_molar_flow": 2.0, "composition": two_phase}}))
        good = {"total_molar_flow": 1.0, "composition": [a]}
        empty = {"total_molar_flow": 0.0, "composition": []}
        self.assertTrue(C._phases_ok({"l": good, "g": empty}))
        self.assertFalse(C._phases_ok({"g": empty}))
        self.assertFalse(C._phases_ok({"l": dict(good, total_molar_flow=-1.0)}))
        self.assertFalse(C._phases_ok({"l": {"composition": [a]}}))
        self.assertFalse(C._phases_ok({}))

    def test_validity_predicates(self):
        """R2.0 shared predicates: _valid_number bounds (open / closed, None
        when outside or non-numeric); _valid_T in (0, T_MAX) K; _valid_P > 0;
        _valid_flow >= 0; _valid_fraction in [0, 1]; _valid_cost >= 0;
        _nonempty_value rejects None, '', [], {} and NaN but keeps False and
        0; _nonempty_scalar accepts a non-blank string or a finite number
        only (a version '2.54' or 2, never ['x'])."""
        self.assertTrue(C._nonempty_scalar("2.54") and C._nonempty_scalar(2))
        for value in (["x"], "", None, True):
            self.assertFalse(C._nonempty_scalar(value), value)
        self.assertEqual(C._valid_number(5, 0, 10), 5.0)
        self.assertIsNone(C._valid_number(0, 0, lo_open=True))
        self.assertIsNone(C._valid_number(10, 0, 10, hi_open=True))
        self.assertIsNone(C._valid_number("5", 0, 10))
        self.assertIsNone(C._valid_T(-50.0))
        self.assertIsNone(C._valid_T(0.0))
        self.assertIsNone(C._valid_T(C.T_MAX))
        self.assertEqual(C._valid_T(300), 300.0)
        self.assertIsNone(C._valid_P(0.0))
        self.assertEqual(C._valid_P({"value": 101325, "units": "Pa"}), 101325.0)
        self.assertIsNone(C._valid_flow(-3.0))
        self.assertEqual(C._valid_flow(0.0), 0.0)
        self.assertIsNone(C._valid_fraction(1.01))
        self.assertIsNone(C._valid_cost(-1.0))
        self.assertEqual(C._valid_cost(0), 0.0)
        for value in (None, "", " ", [], {}, float("nan")):
            self.assertFalse(C._nonempty_value(value), value)
        for value in (False, 0, "x", [0]):
            self.assertTrue(C._nonempty_value(value), value)

    def test_spec_predicates(self):
        """_valid_spec by parameter role: temperatures (_spec_temperature),
        pressures or effect-pressure lists (_spec_pressure), positive
        (_spec_positive: tau, N, k, ...), non-negative (_spec_nonnegative:
        V, tau_0), positive integers (_spec_positive_int: N_stages),
        efficiencies in (0, 1] (_spec_efficiency: V_wf, eta, Lr, ...),
        fractions (_spec_fraction), splits (_spec_split: number, list or
        object of fractions), LHK pairs (_spec_lhk), finite numbers
        (_spec_finite: Q, H), booleans for flags (_spec_flag); any other key
        needs a non-empty value. Expected: plausible values True, the
        reviewer's '' and out-of-range values False."""
        good = {"T": 300.0, "P": [101325.0, 5e4], "tau": 4, "V": 0.0,
                "tau_0": 0, "N_stages": 12, "V_wf": 0.9, "eta": 1.0,
                "moisture_content": 0.0, "split": {"A": 0.2},
                "LHK": ["Ethanol", "Water"], "Q": -5.0, "rigorous": False,
                "material": "Carbon steel"}
        for key, value in good.items():
            self.assertTrue(C._valid_spec(key, value), (key, value))
        bad = {"T": "", "tau": "", "P": [], "V": -1.0, "N_stages": 2.5,
               "V_wf": 0.0, "eta": 1.2, "split": [0.5, 2.0], "LHK": ["A"],
               "Q": "x", "rigorous": "yes", "material": ""}
        for key, value in bad.items():
            self.assertFalse(C._valid_spec(key, value), (key, value))
        self.assertTrue(C._spec_temperature(305.0))
        self.assertTrue(C._spec_pressure(1e5) and not C._spec_pressure(-1.0))
        self.assertTrue(C._spec_positive(1e-3) and not C._spec_positive(0))
        self.assertTrue(C._spec_nonnegative(0) and not C._spec_nonnegative(-1))
        self.assertTrue(C._spec_positive_int(3.0))
        self.assertTrue(C._spec_efficiency(0.5) and not C._spec_efficiency(0))
        self.assertTrue(C._spec_fraction(0) and not C._spec_fraction(1.5))
        self.assertTrue(C._spec_split(0.9) and not C._spec_split({}))
        self.assertTrue(C._spec_lhk(("A", "B")) and not C._spec_lhk(["A", ""]))
        self.assertTrue(C._spec_finite(-1e9) and not C._spec_finite(None))
        self.assertTrue(C._spec_flag(True) and not C._spec_flag(1))

    def test_formula_and_registry_id_predicates(self):
        """_formula_counts parses plain formulas into element counts (None for
        names, non-element tokens, parentheses); _formula_like marks
        formula-looking ids ('C2H6O', 'HP', 'CO2' yes; 'Water', 'Ash',
        'Cellulose' no); _isomer_ambiguous only those with >= 2 carbon atoms
        ('C2H6O' yes; 'CO2', 'CH4', 'H2SO4', 'P4O10', 'CaO' no);
        _valid_formula also accepts what the validator's
        parser reads. _valid_cas checks format and check digit; _smiles_like
        needs SMILES tokens, an atom, balanced branches and paired ring
        labels ('CCO' yes; 'x', 'HP', 'CO2' no); _registry_query (R3.2)
        returns (kind, database query) for a CAS number (as is), an InChI
        whose formula layer parses (as is), an InChIKey ('InChIKey='
        prefixed, a prefixed one accepted too) and SMILES ('SMILES='
        prefixed, so 'C' is queried as methane), and (None, None) for 'x',
        'InChI=1S/x' (formula layer 'x' does not parse), a bad check digit,
        '' and non-strings."""
        self.assertEqual(C._formula_counts("C2H6O"), {"C": 2, "H": 6, "O": 1})
        self.assertEqual(C._formula_counts("CH1.61O0.56"),
                         {"C": 1, "H": 1.61, "O": 0.56})
        for text in ("Water", "Ash", "Cellulose", "Ca(OH)2", "Xy2", None):
            self.assertIsNone(C._formula_counts(text), text)
        for cid in ("C2H6O", "HP", "CO2", "NaOH"):
            self.assertTrue(C._formula_like(cid), cid)
        for cid in ("Water", "Ethanol", "TriOlein", "Yeast"):
            self.assertFalse(C._formula_like(cid), cid)
        # _isomer_ambiguous (R2.3 corrected): a formula id with >= 2 carbon
        # atoms only; one-carbon or carbon-free formulas name one compound.
        for cid in ("C2H6O", "C6H12O6", "CH3CH2OH"):
            self.assertTrue(C._isomer_ambiguous(cid), cid)
        for cid in ("CO2", "O2", "CH4", "N2", "NH3", "SO2", "CaO", "H2SO4",
                    "H3PO4", "P4O10", "HP", "Water", None):
            self.assertFalse(C._isomer_ambiguous(cid), cid)
        self.assertTrue(C._valid_formula("H2O"))
        self.assertFalse(C._valid_formula("x"))
        self.assertTrue(C._valid_cas("7732-18-5"))
        self.assertFalse(C._valid_cas("7732-18-4"))
        self.assertFalse(C._valid_cas("x"))
        self.assertTrue(C._smiles_like("CCO"))
        self.assertTrue(C._smiles_like("c1ccccc1"))
        self.assertTrue(C._smiles_like("[Na+].[Cl-]"))
        for text in ("x", "HP", "CO2", "C(C", "12", 5):
            self.assertFalse(C._smiles_like(text), text)
        inchi = "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"
        key = "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
        for rid, expected in (
                ("64-17-5", ("CAS", "64-17-5")), (inchi, ("InChI", inchi)),
                (key, ("InChIKey", f"InChIKey={key}")),
                (f"InChIKey={key}", ("InChIKey", f"InChIKey={key}")),
                ("CCO", ("SMILES", "SMILES=CCO")),
                ("SMILES=CCO", ("SMILES", "SMILES=CCO")),
                ("C", ("SMILES", "SMILES=C")),
                (" 64-17-5 ", ("CAS", "64-17-5"))):
            self.assertEqual(C._registry_query(rid), expected, rid)
        for rid in ("x", "", ["64-17-5"], "64-17-4", "InChI=1S/x", None):
            self.assertEqual(C._registry_query(rid), (None, None), rid)

    def test_total_flow_and_stream_state(self):
        """_total_flow: mass else molar; _stream_state: valid feed -> all
        True; no stream_properties -> all False; zero flow satisfies
        composition; legacy inline + flat composition -> all True."""
        self.assertEqual(C._total_flow({"total_molar_flow": 2.0}), 2.0)
        self.assertEqual(C._stream_state(valid_doc()["streams"][0]),
                         {"T": True, "P": True, "flow": True, "composition": True})
        self.assertEqual(set(C._stream_state({"id": "s"}).values()), {False})
        zero = {"stream_properties": {"total_mass_flow": 0.0}}
        self.assertTrue(C._stream_state(zero)["composition"])
        self.assertTrue(all(C._stream_state(legacy_doc()["streams"][0]).values()))

    def test_phase_entries_and_species_flows(self):
        """_phase_entries yields (phase, entry) for per-phase and legacy flat
        shapes; _species_flows: phase molar flow x fraction; legacy basis;
        zero stream -> {}; incomplete -> None."""
        feed = valid_doc()["streams"][0]
        self.assertIn(("l", {"component_name": "Ethanol", "mol_fraction": 1.0}),
                      list(C._phase_entries(feed)))
        self.assertEqual(C._species_flows(feed), {"Ethanol": 1.0})
        self.assertEqual(C._species_flows(legacy_doc()["streams"][0]),
                         {"Water": 1.0})
        self.assertEqual(C._species_flows(
            {"stream_properties": {"total_molar_flow": 0.0}}), {})
        self.assertIsNone(C._species_flows({"stream_properties": {}}))

    def test_equation_species(self):
        """_equation_species: 'Glucose -> 2 Ethanol + 2 CO2' -> three names;
        '3 HP' keeps HP; glued '2H2O' -> H2O only when H2O is declared."""
        self.assertEqual(C._equation_species("Glucose -> 2 Ethanol + 2 CO2"),
                         ["Glucose", "Ethanol", "CO2"])
        self.assertEqual(C._equation_species("3 HP -> A"), ["HP", "A"])
        self.assertEqual(C._equation_species("2H2O -> A", {"H2O"}), ["H2O", "A"])
        self.assertEqual(C._equation_species("3HP -> A"), ["3HP", "A"])
        self.assertEqual(C._equation_species("no arrow"), [])
        # _PHASE_TAG_RE: a thermosteam phase tag is not part of the name
        self.assertEqual(C._equation_species("Glucose,l -> 2 CO2, g"),
                         ["Glucose", "CO2"])

    def test_equation_and_stoichiometry_sides(self):
        """_equation_sides splits consumed / produced species ('A + 2 B ->
        C' -> ([A, B], [C])); a bare number names no species; no arrow ->
        ([], []). _stoichiometry_sides splits by coefficient sign; a species
        resolving to no name is dropped."""
        self.assertEqual(C._equation_sides("A + 2 B -> C"), (["A", "B"], ["C"]))
        self.assertEqual(C._equation_sides("2 -> C"), ([], ["C"]))
        self.assertEqual(C._equation_sides("x"), ([], []))
        self.assertEqual(C._stoichiometry_sides({"A": -1, "B": 2, "C": 0},
                                                {"A", "B"}, {}), (["A"], ["B"]))
        self.assertEqual(C._stoichiometry_sides([-1, 1], set(), {0: ["x"]}),
                         ([], ["index 1"]))

    def test_stoichiometry_species(self):
        """_stoichiometry_species: id keys, index keys resolved by index,
        index arrays; zero coefficients skipped; unknown index -> 'index n';
        _int_or_none parses ints only."""
        by_index = {0: "A", 1: "B"}
        self.assertEqual(C._stoichiometry_species({"A": -1, "1": 1, "B": 0},
                                                  {"A", "B"}, by_index), ["A", "B"])
        self.assertEqual(C._stoichiometry_species([-1, 0, 2], {"A"}, by_index),
                         ["A", "index 2"])
        self.assertEqual(C._stoichiometry_species("x", set(), {}), [])
        self.assertEqual(C._int_or_none("4"), 4)
        self.assertIsNone(C._int_or_none(True))
        self.assertIsNone(C._int_or_none("x"))

    def test_inline_quantities(self):
        """_inline_quantities: yields path + dict of every legacy {value,
        units} quantity, and nothing for a current-shape document."""
        paths = [p for p, _q in C._inline_quantities(legacy_doc())]
        self.assertIn("streams[0].stream_properties.temperature", paths)
        self.assertIn("utilities.power_utilities[0].price", paths)
        self.assertEqual(list(C._inline_quantities(valid_doc())), [])

    def test_versions(self):
        """_version_tuple: '0.1.2' -> (0, 1, 2); '0.1' -> None.
        _is_representable: None since -> False; unknown file version -> True;
        otherwise a tuple comparison."""
        self.assertEqual(C._version_tuple("0.1.2"), (0, 1, 2))
        self.assertIsNone(C._version_tuple("0.1"))
        self.assertFalse(C._is_representable(None, (9, 9, 9)))
        self.assertTrue(C._is_representable("0.1.2", None))
        self.assertTrue(C._is_representable("0.1.2", (0, 2, 0)))
        self.assertFalse(C._is_representable("0.1.2", (0, 0, 3)))

    def test_constants_mirror_validator(self):
        """ZERO_FLOW and BOUNDARY equal the validator's constants (duplicated
        only to keep the scorer import-light)."""
        self.assertEqual(C.ZERO_FLOW, V.ZERO_FLOW)
        self.assertEqual(C.BOUNDARY, V.BOUNDARY)


# ---------------------------------------------------- unit class resolution

CORPUS_DIR = (Path(__file__).resolve().parents[2] / "pisces_sff" / "export"
              / "exported_flowsheets" / "bioindustrial_park")
BIP_URL = ("https://github.com/BioSTEAMDevelopmentGroup/Bioindustrial-Park/"
           "blob/master/biorefineries/corn/units.py")
BST_URL = ("https://github.com/BioSTEAMDevelopmentGroup/biosteam/blob/master/"
           "biosteam/units/tank.py")
# The Bioindustrial-Park repository itself (a pinned model package's url).
BIP_REPO = "https://github.com/BioSTEAMDevelopmentGroup/Bioindustrial-Park"
LIQ = {"l": {"total_molar_flow": 1.0, "composition": [
    {"component_name": "Ethanol", "mol_fraction": 1.0}]}}
VAP = {"g": {"total_molar_flow": 1.0, "composition": [
    {"component_name": "Ethanol", "mol_fraction": 1.0}]}}


def stream(sid, source, sink, T=300.0, P=101325.0, phases=None, mass=46.07,
           enthalpy=None):
    """One stream; T/P None drop the field, phases defaults to liquid."""
    sp = {"total_mass_flow": mass, "total_molar_flow": 1.0,
          "phases": copy.deepcopy(LIQ if phases is None else phases)}
    if T is not None:
        sp["temperature"] = T
    if P is not None:
        sp["pressure"] = P
    if enthalpy is not None:
        sp["enthalpy_flow"] = enthalpy
    return {"id": sid, "source_unit_id": source, "sink_unit_id": sink,
            "stream_properties": sp}


def one_unit_doc(unit, ins=({},), outs=({},), pinned=False):
    """valid_doc with a single unit U1 (fields `unit`) wired to boundary feeds
    `ins` and products `outs` (each a dict of stream() keyword arguments).
    Balanced (R2.2): every product not given its own `mass` carries
    len(ins) / len(outs) times the default flows (mass, molar and each
    phase's molar flow), so the default one-species balances close."""
    doc = valid_doc()
    doc["units"] = [dict(unit, id="U1")]
    products = [stream(f"out{k}", "U1", "None", **kw)
                for k, kw in enumerate(outs)]
    factor = len(ins) / len(outs) if outs else 1.0
    for kw, product in zip(outs, products):
        if "mass" in kw:
            continue
        sp = product["stream_properties"]
        for key in ("total_mass_flow", "total_molar_flow"):
            sp[key] *= factor
        for phase in sp["phases"].values():
            phase["total_molar_flow"] *= factor
    doc["streams"] = ([stream(f"in{k}", "None", "U1", **kw)
                       for k, kw in enumerate(ins)] + products)
    if pinned:  # R3.6: the Bioindustrial-Park repository, commit pinned
        doc["metadata"]["reproducibility"] = {
            "flowsheet_model_package": {"url": BIP_REPO, "commit": "abc123"}}
    return doc


def spec_unit(name, specs=None, results=None, url=BST_URL):
    """Unit fields naming class `name` on `url` with the given specs."""
    unit = {"design_simulation_method": f"{name} on {url}",
            "design_input_specs": dict(specs or {})}
    if results is not None:
        unit["design_results"] = results
    return unit


def spec_score(name, specs=None, ins=({},), outs=({},), results=None,
               url=BST_URL, pinned=False):
    """_unit_spec_score of a one-unit doc of class `name`."""
    doc = one_unit_doc(spec_unit(name, specs, results, url), ins, outs, pinned)
    return C._unit_spec_score(ctx(doc), 0)


def corpus_classes():
    """Every class named by a design_simulation_method in the corpus."""
    names = set()
    for path in sorted(CORPUS_DIR.glob("*.json")):
        for unit in json.loads(path.read_text(encoding="utf-8")).get("units", []):
            name = C._dsm_class(unit)[0]
            if name:
                names.add(name)
    return names


class TestUnitClass(unittest.TestCase):
    def test_normalized_texts(self):
        """_normalized_texts('GrainHandling') -> ('grainhandling', 'grain
        handling'); underscores become spaces."""
        self.assertEqual(C._normalized_texts("GrainHandling"),
                         ("grainhandling", "grain handling"))
        self.assertEqual(C._normalized_texts("A_B")[1], "a b")

    def test_keyword_family(self):
        """_keyword_family on unit_type text / CamelCase class names -> the
        expected family under the precedence order (SteamMixer and JetCooker
        -> steam_injection_heater, MixerSettler -> extraction, not mixer;
        BoilerTurbogenerator -> boiler_turbogenerator, not turbine; ScrewPress
        and CrushingMill -> splitter, ScrewFeeder and HammerMill ->
        solids_handling; MESH/stripper -> staged_column before distillation;
        'Mix tank' -> tank; R2.10: neutralizer, hydrotreater, gasifier,
        pyrolysis -> reactor, 'Neutralization tank' -> reactor, 'Wastewater
        treatment' -> reactor but the wastewater cost lump and plant-air/CIP
        facilities -> facility, a plain 'Boiler' -> boiler; R4.4 head nouns:
        'Mix tank' -> tank, 'LLE centrifuge' / 'Adsorption column' keep
        their compound family; R5.7: 'Fire water tank' -> facility); unknown
        -> None."""
        cases = {
            "SteamMixer": "steam_injection_heater",
            "Jet cooker": "steam_injection_heater",
            "MixerSettler": "extraction", "Mixer": "mixer",
            "MultiStageMixerSettlers": "extraction",
            "BoilerTurbogenerator": "boiler_turbogenerator",
            "Isentropic turbine": "turbine", "Reboiler": "hx_utility",
            "ScrewPress": "splitter", "ScrewFeeder": "solids_handling",
            "CrushingMill": "splitter", "HammerMill": "solids_handling",
            "MESHDistillation": "staged_column", "Stripper": "staged_column",
            "AdiabaticMultiStageVLEColumn": "staged_column",
            "Distillation Column": "distillation",
            "Heat exchanger network": "heat_exchanger_network",
            "Heat exchanger": "hx_utility", "HXprocess": "hx_process",
            "HydrolysateHeatExchanger": "hx_utility",
            "Fire water tank": "facility", "Mix tank": "tank",
            "LLE centrifuge": "extraction", "Adsorption column": "splitter",
            "Flash tank": "flash", "Thermal oxidizer": "combustor",
            "Drum dryer": "dryer", "Gibbs equilibrium reactor":
            "equilibrium_reactor", "AeratedBioreactor": "aerated_bioreactor",
            "Anaerobic bioreactor": "reactor", "SSF": "batch_bioreactor",
            "BatchCoFermentation": "batch_bioreactor",
            "Fermentation": "reactor", "TALCrystallizer": "crystallizer",
            "Mock splitter": "junction", "HPPump": "pump", "AnMBR": "reactor",
            "CIPpackage": "facility", "Multi-effect evaporator": "evaporator",
            "Compressor": "compressor", "Valve": "valve",
            "Molecular sieve": "splitter", "Storage tank": "tank",
            # R2.10: reactor keywords; neutralizer beats tank; wastewater
            # treatment scores as a reactor (the digesters), while named
            # facility lumps stay facilities; a plain boiler is a boiler.
            "Neutralization tank": "reactor", "Hydrotreater": "reactor",
            "Gasifier": "reactor", "Pyrolysis reactor": "reactor",
            "Wastewater treatment": "reactor",
            "Wastewater system cost": "facility",
            "Plant air CIP waste water facilities": "facility",
            "Boiler": "boiler", "Boiler turbogenerator":
            "boiler_turbogenerator", "Utility facilities": "facility"}
        for text, family in cases.items():
            self.assertEqual(C._keyword_family(text), family, text)
        self.assertIsNone(C._keyword_family("Widget"))
        self.assertIsNone(C._keyword_family(None))

    def test_class_family(self):
        """_class_family: curated table first (JetCooker ->
        steam_injection_heater, EnzymeHydrolysateMixer -> mixer, the bare
        'Unit' -> None with no keyword fallback), then keywords (FooTank ->
        tank)."""
        self.assertEqual(C._class_family("JetCooker"), "steam_injection_heater")
        self.assertEqual(C._class_family("EnzymeHydrolysateMixer"), "mixer")
        self.assertIsNone(C._class_family("Unit"))
        self.assertEqual(C._class_family("FooTank"), "tank")

    def test_class_table_covers_registry_and_corpus(self):
        """_CLASS_TABLE covers every design-spec registry key and every class
        in the committed corpus; every family is in FAMILIES (or None for
        'Unit'); every registry_class is a registry key; _NONCORE_CLASSES
        and COST_FREE_CLASSES are table entries."""
        registry, error = C._design_spec_registry()
        self.assertIsNone(error)
        self.assertEqual(sorted(set(registry) - set(C._CLASS_TABLE)), [])
        self.assertEqual(sorted(corpus_classes() - set(C._CLASS_TABLE)), [])
        for name, (family, registry_class) in C._CLASS_TABLE.items():
            self.assertTrue(family in C.FAMILIES or name == "Unit", name)
            self.assertTrue(registry_class is None or registry_class in registry,
                            name)
        self.assertLessEqual(C._NONCORE_CLASSES | C.COST_FREE_CLASSES
                             | C._COSTED_CLASSES, set(C._CLASS_TABLE))
        self.assertEqual(C._CORE_CLASSES & C._NONCORE_CLASSES, frozenset())

    def test_family_sets(self):
        """Shared family constants are subsets of FAMILIES; the utility-port
        families are class-implied-chemistry families; UTILITY_USING_FAMILIES
        includes the power-drawing tank / solids_handling / extraction
        families and contains HEAT_UTILITY_FAMILIES (which excludes pumps and
        tanks); there are no family weights any more (R4.12: per-unit
        averages are unweighted); POWER_UTILITY_FAMILIES holds pumps,
        compressors and solids handling but no utility exchanger;
        FACILITY_FAMILIES is the four facility families, each with no UOP-02
        rule."""
        families = set(C.FAMILIES)
        for group in (C.REACTOR_FAMILIES, C.UTILITY_USING_FAMILIES,
                      C.HEAT_UTILITY_FAMILIES, C.AGENT_REGENERATOR_FAMILIES,
                      C.CLASS_IMPLIED_CHEMISTRY_FAMILIES,
                      C.UTILITY_PORT_FAMILIES, C.COST_FREE_FAMILIES):
            self.assertLessEqual(group, families)
        self.assertLessEqual(C.UTILITY_PORT_FAMILIES,
                             C.CLASS_IMPLIED_CHEMISTRY_FAMILIES)
        self.assertLessEqual({"tank", "solids_handling", "extraction"},
                             C.UTILITY_USING_FAMILIES)
        self.assertLessEqual(C.HEAT_UTILITY_FAMILIES, C.UTILITY_USING_FAMILIES)
        self.assertFalse({"pump", "tank"} & C.HEAT_UTILITY_FAMILIES)
        for name in ("FAMILY_WEIGHTS", "UNKNOWN_FAMILY_WEIGHT",
                     "_family_weight", "_unit_weight", "_wmean"):
            self.assertFalse(hasattr(C, name), name)
        # Revision 2 part B: the power need (R2.7) and the facility families
        # of CMP-UOP-06 (R2.5), which UOP-02 no longer scores.
        for group in (C.POWER_UTILITY_FAMILIES, C.FACILITY_FAMILIES):
            self.assertLessEqual(group, families)
        self.assertEqual(C.FACILITY_FAMILIES, {
            "facility", "boiler", "boiler_turbogenerator",
            "heat_exchanger_network"})
        self.assertLessEqual({"pump", "compressor", "solids_handling"},
                             C.POWER_UTILITY_FAMILIES)
        self.assertNotIn("hx_utility", C.POWER_UTILITY_FAMILIES)
        for family in C.FACILITY_FAMILIES:
            self.assertEqual(C._FAMILY_RULES[family], (), family)

    def test_dsm_class(self):
        """_dsm_class: 'Pump on <url>' -> ('Pump', url); an empty URL is
        allowed -> ('Pump', ''); prose or non-strings -> (None, None)."""
        self.assertEqual(C._dsm_class({"design_simulation_method":
                                       "Pump on https://x/p.py"}),
                         ("Pump", "https://x/p.py"))
        self.assertEqual(C._dsm_class({"design_simulation_method": "Pump on "}),
                         ("Pump", ""))
        self.assertEqual(C._dsm_class({"design_simulation_method": "a pump"}),
                         (None, None))
        self.assertEqual(C._dsm_class("garbage"), (None, None))

    def test_is_core_class(self):
        """_is_core_class (R2.6): a core-list name is core without a URL or
        with a biosteam / hensmith URL; a biosteam URL alone does NOT make a
        class core (CornStorage, a tank_factory class); another
        repository's URL makes even a core-list name custom (Bioindustrial-
        Park's own HammerMill); abstract classes are never core; empty ->
        False. The static core list and the abstract list are disjoint and
        hold the biosteam classes the review names."""
        self.assertFalse(C._is_core_class("CornStorage", BST_URL))
        self.assertFalse(C._is_core_class("GrainHandling", BIP_URL))
        self.assertTrue(C._is_core_class("Pump"))
        self.assertTrue(C._is_core_class("Pump", BST_URL))
        self.assertTrue(C._is_core_class(
            "HeatExchangerNetwork",
            "https://github.com/BioSTEAMDevelopmentGroup/hensmith/blob/main/x.py"))
        self.assertFalse(C._is_core_class("HammerMill", BIP_URL))
        self.assertFalse(C._is_core_class("JetCooker"))
        for name in ("Unit", "Tank", "Compressor", "Distillation", "HX"):
            self.assertFalse(C._is_core_class(name, BST_URL), name)
        self.assertFalse(C._is_core_class("Foo", "https://github.com/"
                                          "BioSTEAMDevelopmentGroup/biosteamx"))
        self.assertFalse(C._is_core_class(None))
        self.assertEqual(C._CORE_CLASSES & C._ABSTRACT_CLASSES, frozenset())
        self.assertLessEqual({"Unit", "Compressor", "Turbine", "Valve", "Tank",
                              "Distillation", "HX", "Facility"},
                             C._ABSTRACT_CLASSES)
        self.assertLessEqual({"Pump", "HXutility", "BoilerTurbogenerator",
                              "Boiler", "ProcessWaterCenter", "CoolingTower",
                              "ChilledWaterPackage", "HeatExchangerNetwork"},
                             C._CORE_CLASSES)

    def test_repo_key_and_pinned_repository(self):
        """_repo_key: https / pip 'git+...@sha#egg' / scp-like 'git@host:'
        URLs of one repository -> the same lowercase (host, owner, name),
        '.git', 'www.' and any path dropped; no repository -> None.
        _pinned_repository (R3.6): the key of flowsheet_model_package.url
        when that package pins a commit or version, else None (no url, or
        no pin)."""
        key = ("github.com", "biosteamdevelopmentgroup", "bioindustrial-park")
        for url in (BIP_REPO, BIP_URL, BIP_REPO + ".git", BIP_REPO + "/",
                    "git+" + BIP_REPO + ".git@584232#egg=biorefineries",
                    "git@github.com:BioSTEAMDevelopmentGroup/"
                    "Bioindustrial-Park.git",
                    "https://www.github.com/biosteamdevelopmentgroup/"
                    "bioindustrial-park?tab=readme"):
            self.assertEqual(C._repo_key(url), key, url)
        self.assertEqual(C._repo_key("https://gitlab.com/Org/Repo/-/blob/x.py"),
                         ("gitlab.com", "org", "repo"))
        # one path segment (https://example.org/units.py): no owner / name
        self.assertIsNone(C._repo_key(CUSTOM_URL))
        for bad in (None, "", "not a url", "https://github.com/", 5):
            self.assertIsNone(C._repo_key(bad), bad)
        meta = lambda package: {"reproducibility": {
            "flowsheet_model_package": package}}
        self.assertEqual(C._pinned_repository(meta(
            {"url": BIP_REPO, "commit": "abc"})), key)
        self.assertEqual(C._pinned_repository(meta(
            {"url": BIP_REPO, "version": "1.0"})), key)
        self.assertIsNone(C._pinned_repository(meta({"url": BIP_REPO})))
        self.assertIsNone(C._pinned_repository(meta({"commit": "abc"})))
        self.assertIsNone(C._pinned_repository({}))
        self.assertIsNone(C._pinned_repository("garbage"))

    def test_class_confidence(self):
        """_class_confidence (R2.6, R3.6): concrete core class -> 1.0;
        abstract -> None (family only) even with a biosteam URL; an unknown
        class a biosteam URL claims -> None; a custom class -> 1.0 only when
        its URL's repository equals the pinned model-package repository
        (Bioindustrial-Park's GrainHandling / HammerMill with the BIP
        repository pinned), else 0.75: unpinned, a URL in another
        repository (tank_factory's CornStorage on a biosteam URL, an
        example.org class), or no URL."""
        bip = C._repo_key(BIP_REPO)
        self.assertEqual(C._class_confidence("Pump", BST_URL, None), 1.0)
        self.assertIsNone(C._class_confidence("Unit", BST_URL, bip))
        self.assertIsNone(C._class_confidence("Compressor", None, bip))
        self.assertIsNone(C._class_confidence("Frobnicator", BST_URL, bip))
        self.assertEqual(C._class_confidence("CornStorage", BST_URL, None), 0.75)
        self.assertEqual(C._class_confidence("CornStorage", BST_URL, bip), 0.75)
        self.assertEqual(C._class_confidence("GrainHandling", BIP_URL, None), 0.75)
        self.assertEqual(C._class_confidence("GrainHandling", BIP_URL, bip), 1.0)
        self.assertEqual(C._class_confidence("GrainHandling", None, bip), 0.75)
        self.assertEqual(C._class_confidence("Frobnicator", CUSTOM_URL, bip), 0.75)
        self.assertEqual(C._class_confidence("HammerMill", BIP_URL, None), 0.75)
        self.assertEqual(C._class_confidence("HammerMill", BIP_URL, bip), 1.0)

    def test_infer_class_from_specs(self):
        """_infer_class_from_specs (R3.1): only when EVERY registry entry
        containing the valid key set is of one family -> (hint, family),
        hint the unique tightest entry else None; a unique tightest entry
        whose family another containing entry contradicts ({P}: Valve vs
        the looser Pump) -> None, as do differing families, no superset, no
        valid spec (an implausible P = -1 counts as unstated) or no specs.
        Deleting a key can only add containing entries (P, dP_design ->
        Pump; delete dP_design -> ambiguous)."""
        reg = {"Valve": {"design_input_spec_params": {"P": {}}},
               "Pump": {"design_input_spec_params": {"P": {}, "dP_design": {}}},
               "StorageTank": {"design_input_spec_params": {"tau": {}}},
               "Tank": {"design_input_spec_params": {"tau": {}}},
               "Mixer": {"design_input_spec_params": {"rigorous": {}}},
               "HXutility": {"design_input_spec_params": {"rigorous": {}}}}
        unit = lambda specs: {"design_input_specs": specs}
        infer = C._infer_class_from_specs
        self.assertIsNone(infer(unit({"P": 1e5}), reg))
        self.assertEqual(infer(unit({"P": 1e5, "dP_design": 1}), reg),
                         ("Pump", "pump"))
        self.assertEqual(infer(unit({"dP_design": 1}), reg), ("Pump", "pump"))
        self.assertEqual(infer(unit({"tau": 1}), reg), (None, "tank"))
        self.assertIsNone(infer(unit({"rigorous": True}), reg))
        self.assertIsNone(infer(unit({"zz": 1}), reg))
        self.assertIsNone(infer(unit({"P": -1}), reg))
        self.assertIsNone(infer(unit({}), reg))
        valves = {"Valve": {"design_input_spec_params": {"P": {}}},
                  "IsenthalpicValve": {"design_input_spec_params":
                                       {"P": {}, "vle": {}}}}
        self.assertEqual(infer(unit({"P": 1e5}), valves), ("Valve", "valve"))

    def test_unit_class_paths(self):
        """_unit_class: core 'Class on <url>' -> 1.0; a custom class -> 0.75,
        or 1.0 when its URL lies in the pinned repository (R3.6); R4.3: a
        unit_type naming a class ('Pump', 'JetCooker', even with the model
        package pinned) -> only its keyword family at 0.5, never the class.
        R3.1: an inferred class is never
        returned -- an unambiguous key-set family with no stated family ->
        (None, family, 0.75); a stated family (unit_type keyword, or a named
        abstract / unverifiable class's family) wins, 0.75 when the key set
        unambiguously confirms it, else 0.5 (a contradicting key set never
        swaps it); an unknown custom class takes its family from unit_type,
        else the key set; R2.6: an abstract class (unit_type 'Compressor' /
        'Unit', 'Unit on <biosteam url>') or an unknown class on a biosteam
        URL resolves as if unnamed; nothing -> (None, None, 0.0)."""
        reg = {"Pump": {"design_input_spec_params": {"P": {}, "dP_design": {}}},
               "StorageTank": {"design_input_spec_params": {"tau": {}}},
               "Tank": {"design_input_spec_params": {"tau": {}}}}
        U = C._unit_class
        bip = C._repo_key(BIP_REPO)
        self.assertEqual(U(spec_unit("Pump"), reg), ("Pump", "pump", 1.0))
        custom = spec_unit("GrainHandling", url=BIP_URL)
        self.assertEqual(U(custom, reg), ("GrainHandling", "solids_handling", 0.75))
        self.assertEqual(U(custom, reg, pinned_repo=bip)[2], 1.0)
        self.assertEqual(U(spec_unit("GrainHandling", url=CUSTOM_URL), reg,
                           pinned_repo=bip)[2], 0.75)
        self.assertEqual(U({"unit_type": "Pump"}, reg), (None, "pump", 0.5))
        self.assertEqual(U({"unit_type": "JetCooker"}, reg, pinned_repo=bip),
                         (None, "steam_injection_heater", 0.5))
        self.assertEqual(U({"design_input_specs": {"dP_design": 1.0}}, reg),
                         (None, "pump", 0.75))
        self.assertEqual(U({"unit_type": "Heat exchanger"}, reg),
                         (None, "hx_utility", 0.5))
        self.assertEqual(U({"design_input_specs": {"tau": 1.0}}, reg),
                         (None, "tank", 0.75))
        # a stated family the key set contradicts is kept, at 0.5
        self.assertEqual(U({"unit_type": "Heat exchanger",
                            "design_input_specs": {"dP_design": 1.0}}, reg),
                         (None, "hx_utility", 0.5))
        self.assertEqual(U({"unit_type": "Storage tank",
                            "design_input_specs": {"tau": 1.0}}, reg),
                         (None, "tank", 0.75))
        # R2.6: an unknown class claiming a biosteam URL names nothing; its
        # family comes from the key set (Tank / StorageTank: one family).
        self.assertEqual(U(spec_unit("Frobnicator", {"tau": 1.0}), reg),
                         (None, "tank", 0.75))
        self.assertEqual(U(spec_unit("Frobnicator", {"tau": 1.0},
                                     url=CUSTOM_URL), reg),
                         ("Frobnicator", "tank", 0.75))
        self.assertEqual(U(dict(spec_unit("Frobnicator", {"tau": 1.0},
                                          url=CUSTOM_URL), unit_type="Pump"),
                           reg), ("Frobnicator", "pump", 0.75))
        # Abstract classes identify a family at most, never confidence 1:
        # "Unit on <biosteam url>" and unit_type "Unit" -> nothing; unit_type
        # "Compressor" -> its keyword family; "Compressor on <biosteam url>"
        # -> the class's family as the stated family.
        self.assertEqual(U(spec_unit("Unit"), reg), (None, None, 0.0))
        self.assertEqual(U({"unit_type": "Unit"}, reg), (None, None, 0.0))
        self.assertEqual(U({"unit_type": "Compressor"}, reg),
                         (None, "compressor", 0.5))
        self.assertEqual(U(spec_unit("Compressor"), reg),
                         (None, "compressor", 0.5))
        self.assertEqual(U(spec_unit("Tank", {"tau": 1.0}), reg),
                         (None, "tank", 0.75))
        self.assertEqual(U(spec_unit("Compressor", {"dP_design": 1.0}), reg),
                         (None, "compressor", 0.5))
        # ... and fall through to what the rest of the unit states: the
        # unit_type keyword family (as if no class had been named).
        unit = dict(spec_unit("Unit"), unit_type="Pump")
        self.assertEqual(U(unit, reg), (None, "pump", 0.5))
        self.assertEqual(U({"unit_type": "Widget"}, reg), (None, None, 0.0))
        self.assertEqual(U("garbage"), (None, None, 0.0))

    def test_context_registry_classes_never_inferred(self):
        """R3.1: _ScoringContext.unit_registry_classes holds the registry
        class of the NAMED class only (_declared_registry_class: CornStorage
        -> its curated ancestor StorageTank); a unit whose class would only
        be inferred from its spec keys (dP_design -> Pump) gets None, so no
        class-level rule ever reaches an inferred class (its rules are its
        family's: pump)."""
        doc = one_unit_doc(spec_unit("CornStorage", {"tau": 1.0}))
        self.assertEqual(ctx(doc).unit_registry_classes, ["StorageTank"])
        doc = one_unit_doc({"unit_type": "?",
                            "design_input_specs": {"dP_design": 1.0}})
        sc = ctx(doc)
        self.assertEqual(sc.unit_classes[0], (None, "pump", 0.75))
        self.assertEqual(sc.unit_registry_classes, [None])
        self.assertIs(C._unit_rules(sc, 0), C._FAMILY_RULES["pump"])

    def test_unweighted_unit_averages(self):
        """R4.12: per-unit averages are unweighted. A two-unit doc -- a
        fully costed SinglePhaseReactor beside an uncosted Pump -- scores
        CMP-COST-01 = 1/2 (a family-weighted mean gave 3/4), and deleting
        the pump's design_simulation_method (its family then unknown)
        leaves it at 1/2 instead of moving the pump's weight."""
        doc = one_unit_doc(dict(spec_unit("SinglePhaseReactor"),
                                purchase_costs={"Reactor": 1.0e5}))
        doc["units"].append(dict(spec_unit("Pump"), id="U2"))
        self.assertEqual(run(C._score_cost_01, doc)[0], 0.5)
        del doc["units"][1]["design_simulation_method"]
        self.assertIsNone(ctx(doc).unit_classes[1][1])
        self.assertEqual(run(C._score_cost_01, doc)[0], 0.5)
        self.assertIsNone(C._mean([]))

    def test_is_cost_free(self):
        """_is_cost_free: Mixer / Splitter / Separator / MockSplitter
        (junction) and an unlisted mixer subclass -> True; R4.3: no class
        (a mixer / junction family alone, from unit_type keywords or spec
        keys) -> False; @cost mixer subclasses (AmmoniaMixer) and costed
        splitter subclasses (CleaningSystem) -> False."""
        self.assertTrue(C._is_cost_free("Mixer", "mixer"))
        self.assertTrue(C._is_cost_free("Splitter", "splitter"))
        self.assertTrue(C._is_cost_free("Separator", "splitter"))
        self.assertTrue(C._is_cost_free("MockSplitter", "junction"))
        self.assertTrue(C._is_cost_free("SugarMixer", "mixer"))
        self.assertFalse(C._is_cost_free(None, "mixer"))
        self.assertFalse(C._is_cost_free(None, "junction"))
        self.assertFalse(C._is_cost_free("AmmoniaMixer", "mixer"))
        self.assertFalse(C._is_cost_free("CleaningSystem", "splitter"))
        self.assertFalse(C._is_cost_free("Pump", "pump"))

    def test_core_class_known(self):
        """_core_class_known: a core class at confidence 1 -> True; a pinned
        custom class (confidence 1, not core) -> False; an inferred class
        (0.75) -> False."""
        self.assertTrue(C._core_class_known(ctx(one_unit_doc(spec_unit("Pump"))), 0))
        doc = one_unit_doc(spec_unit("GrainHandling", url=BIP_URL), pinned=True)
        self.assertFalse(C._core_class_known(ctx(doc), 0))
        doc = one_unit_doc({"design_input_specs": {"dP_design": 1.0},
                            "unit_type": "?"})
        sc = ctx(doc)
        self.assertEqual(sc.unit_classes[0][2], 0.75)
        self.assertFalse(C._core_class_known(sc, 0))


# -------------------------------------------------------- scoring context

class TestContext(unittest.TestCase):
    def test_topology(self):
        """_topology: feed/outlet/isolated/internal classification and
        per-unit inlet/outlet indexes; R5.8: a stream with an endpoint not
        stated (null, '', a missing key) is 'dangling' -- in no other
        class -- while still indexed under its stated unit endpoint."""
        streams = [{"source_unit_id": "None", "sink_unit_id": "A"},
                   {"source_unit_id": "A", "sink_unit_id": "B"},
                   {"source_unit_id": "B", "sink_unit_id": "None"},
                   {"source_unit_id": "None", "sink_unit_id": "None"},
                   {"source_unit_id": "B", "sink_unit_id": None},
                   {"source_unit_id": "", "sink_unit_id": "None"},
                   {"sink_unit_id": "A"}]
        topo = C._topology(streams, {"A", "B"})
        self.assertEqual((topo["feeds"], topo["internal"], topo["outlets"],
                          topo["isolated"], topo["dangling"]),
                         ([0], [1], [2], [3], [4, 5, 6]))
        self.assertEqual(topo["inlets"]["B"], [1])
        self.assertEqual(topo["inlets"]["A"], [0, 6])
        self.assertEqual(topo["unit_outlets"]["A"], [1])
        self.assertEqual(topo["unit_outlets"]["B"], [2, 4])
        self.assertNotIn("", topo["inlets"])

    def test_context_on_valid_doc(self):
        """_ScoringContext(valid_doc): one feed, one outlet, labels by id;
        R4.3 / R4.14: U1's bare unit_type 'Mixer' could give the mixer
        family only, and with one inlet it is no plausible mixer claim, so
        U1's family is unknown ((None, None, 0.0); not core, no registry
        class) -- with a second inlet it is (None, 'mixer', 0.5); named by
        design_simulation_method (exact_mixer_doc) U1 resolves to the core
        registry class Mixer (confidence 1.0, unit_core True, registry class
        Mixer). unit_ports holds U1's (non-empty inlets, outlets) = (1, 1)."""
        sc = ctx(valid_doc())
        self.assertEqual((sc.feeds, sc.outlets), ([0], [1]))
        self.assertEqual(sc.unit_classes[0], (None, None, 0.0))
        self.assertEqual(ctx(label_mixer_doc()).unit_classes[0],
                         (None, "mixer", 0.5))
        self.assertEqual((sc.unit_core, sc.unit_registry_classes),
                         ([False], [None]))
        self.assertEqual(sc.unit_ports, [(1, 1)])
        self.assertEqual(sc.stream_labels, ["streams[feed]", "streams[prod]"])
        self.assertEqual(sc.chem_by_index, {0: "Ethanol"})
        sc = ctx(exact_mixer_doc())
        self.assertEqual(sc.unit_classes[0], ("Mixer", "mixer", 1.0))
        self.assertEqual((sc.unit_core, sc.unit_registry_classes),
                         ([True], ["Mixer"]))

    def test_context_positions_costed_and_vctx(self):
        """_ScoringContext: unit_positions maps id -> first position; costed
        lists the non-cost-free units (an exactly named Mixer is cost-free, a
        Pump is not; R4.3: valid_doc's unit_type-only 'Mixer' is costed);
        the vctx property is the validator's _Context over the same doc,
        built once (molar_mass('Ethanol') -> 46.07)."""
        doc = exact_mixer_doc()
        doc["units"].append(dict(spec_unit("Pump"), id="P1"))
        sc = ctx(doc)
        self.assertEqual(sc.unit_positions, {"U1": 0, "P1": 1})
        self.assertEqual(sc.costed, [1])
        self.assertEqual(ctx(valid_doc()).costed, [0])
        self.assertIs(sc.vctx, sc.vctx)
        self.assertAlmostEqual(sc.vctx.molar_mass("Ethanol"), 46.07)

    def test_context_tolerates_garbage(self):
        """_ScoringContext on a non-dict doc or malformed sections -> empty
        indexes, no exception."""
        for doc in ([], None, {"units": "x", "streams": [1, None],
                               "chemicals": {"a": 1}, "utilities": []}):
            sc = ctx(doc)
            self.assertEqual((sc.units, sc.streams, sc.chemicals), ([], [], []))

    def test_check_result(self):
        """check_result: first result with the id, or None."""
        sc = ctx(valid_doc(), [check("SCHEMA", "pass"), check("QU-01", "fail")])
        self.assertEqual(sc.check_result("QU-01").status, "fail")
        self.assertIsNone(sc.check_result("MET-07"))

    def test_all_outlets_have_and_all_ports_full(self):
        """all_outlets_have(U1, 'T') True with a stated outlet T, False once
        removed; all_ports_full(U1) needs every connected stream complete."""
        sc = ctx(valid_doc())
        self.assertTrue(sc.all_outlets_have("U1", "T"))
        self.assertTrue(sc.all_ports_full("U1"))
        self.assertFalse(sc.all_outlets_have("nope", "T"))
        sc = ctx(unit_doc(out_T=None))
        self.assertFalse(sc.all_outlets_have("U1", "T"))
        self.assertFalse(sc.all_ports_full("U1"))
        self.assertFalse(sc.all_ports_full(None))

    def test_all_ports_have_enthalpy_and_all_outlets_phased(self):
        """all_ports_have_enthalpy(U1): True only when every connected stream
        states enthalpy_flow; all_outlets_phased(U1): True with per-phase
        outlets, False once the outlet's phases are removed or for an unknown
        unit."""
        doc = one_unit_doc({}, ins=({"enthalpy": 1.0},), outs=({"enthalpy": 2.0},))
        sc = ctx(doc)
        self.assertTrue(sc.all_ports_have_enthalpy("U1"))
        self.assertTrue(sc.all_outlets_phased("U1"))
        self.assertFalse(sc.all_ports_have_enthalpy(None))
        del doc["streams"][0]["stream_properties"]["enthalpy_flow"]
        del doc["streams"][1]["stream_properties"]["phases"]
        sc = ctx(doc)
        self.assertFalse(sc.all_ports_have_enthalpy("U1"))
        self.assertFalse(sc.all_outlets_phased("U1"))
        self.assertFalse(sc.all_outlets_phased("nope"))


# ------------------------------------------------ operating-spec rules

class TestSpecRules(unittest.TestCase):
    def test_explicit_keys(self):
        """_explicit_keys: non-null design_input_specs keys only."""
        self.assertEqual(C._explicit_keys(
            {"design_input_specs": {"T": 300, "V": None}}), {"T"})
        self.assertEqual(C._explicit_keys({}), set())

    def test_unit_rules_and_spec_population(self):
        """_unit_rules: class-level rules first (SteamMixer -> its P rule),
        then the family's (Pump); MixerSettler and Mixer -> (); the bare
        'Unit' (unknown family) -> None.
        _spec_population drops mixers and ()-rule classes, keeps unknowns."""
        doc = valid_doc()
        doc["units"] = [dict(spec_unit(n), id=f"U{i}") for i, n in enumerate(
            ("SteamMixer", "MixerSettler", "Mixer", "Unit", "Pump"))]
        sc = ctx(doc)
        self.assertIs(C._unit_rules(sc, 0), C._CLASS_RULES["SteamMixer"])
        self.assertEqual(C._unit_rules(sc, 1), ())
        self.assertEqual(C._unit_rules(sc, 2), ())
        self.assertIsNone(C._unit_rules(sc, 3))
        self.assertIs(C._unit_rules(sc, 4), C._FAMILY_RULES["pump"])
        self.assertEqual(C._spec_population(sc), [0, 3, 4])

    def test_stream_value_and_phase_set(self):
        """_stream_value reads T/P; _phase_set: phases with flow, a zero-flow
        stream -> empty set, legacy phase-tagged flat composition, no phase
        data -> None."""
        s = stream("a", "None", "U1", T=310.0, phases={**LIQ, **VAP})
        self.assertEqual((C._stream_value(s, "T"), C._stream_value(s, "P")),
                         (310.0, 101325.0))
        self.assertEqual(C._phase_set(s), frozenset({"l", "g"}))
        s["stream_properties"]["phases"]["g"]["total_molar_flow"] = 0.0
        self.assertEqual(C._phase_set(s), frozenset({"l"}))
        empty = stream("b", "None", "U1", mass=0.0)
        self.assertEqual(C._phase_set(empty), frozenset())
        legacy = legacy_doc()["streams"][0]
        self.assertEqual(C._phase_set(legacy), frozenset({"l"}))
        del legacy["stream_properties"]["composition"][0]["phase"]
        self.assertIsNone(C._phase_set(legacy))

    def test_phase_change_and_pressure_rise(self):
        """_phase_change: liquid in / vapor out -> True, same phases -> False,
        missing phase data -> None; _pressure_rise: out 2e5 - in 1e5 = 1e5,
        a missing pressure -> None."""
        sc = ctx(one_unit_doc({}, outs=({"phases": VAP, "P": 2e5},),
                              ins=({"P": 1e5},)))
        self.assertTrue(C._phase_change(sc, "U1"))
        self.assertEqual(C._pressure_rise(sc, "U1"), 1e5)
        self.assertFalse(C._phase_change(ctx(one_unit_doc({})), "U1"))
        doc = one_unit_doc({}, ins=({"P": None},))
        del doc["streams"][0]["stream_properties"]["phases"]
        sc = ctx(doc)
        self.assertIsNone(C._phase_change(sc, "U1"))
        self.assertIsNone(C._pressure_rise(sc, "U1"))
        self.assertIsNone(C._phase_change(sc, None))

    def test_condition_holds(self):
        """_condition_holds: outlet_T/outlet_P/all_ports_state/enthalpy/
        outlet_phases read streams; class_default (R2.8) holds for a key
        that is NOT a constructor parameter of a confidence-1 class (Pump:
        'tau_0' yes, its constructor argument 'P' no, no key no); rigorous
        reads the spec; explicit:<k> reads specs; design_result:<n> reads
        results; served_agent reads utility results against the declared
        heat agents; unknown -> ValueError."""
        doc = one_unit_doc(spec_unit("Pump", {"rigorous": True},
                                     {"Reflux": 2.0}),
                           ins=({"enthalpy": 1.0},), outs=({"enthalpy": 2.0},))
        sc = ctx(doc)
        for cond in ("outlet_T", "outlet_P", "all_ports_state", "enthalpy",
                     "outlet_phases", "rigorous", "design_result:Reflux"):
            self.assertTrue(C._condition_holds(cond, sc, 0, set()), cond)
        self.assertTrue(C._condition_holds("class_default", sc, 0, set(), "tau_0"))
        self.assertFalse(C._condition_holds("class_default", sc, 0, set(), "P"))
        self.assertFalse(C._condition_holds("class_default", sc, 0, set()))
        self.assertFalse(C._condition_holds("served_agent", sc, 0, set()))
        self.assertTrue(C._condition_holds("explicit:LHK", sc, 0, {"LHK"}))
        self.assertFalse(C._condition_holds("explicit:LHK", sc, 0, set()))
        sc = ctx(one_unit_doc({"unit_type": "Heat exchanger"}))
        for cond in ("enthalpy", "class_default", "rigorous"):
            self.assertFalse(C._condition_holds(cond, sc, 0, set(), "tau_0"), cond)
        with self.assertRaises(ValueError):
            C._condition_holds("bogus", sc, 0, set())

    def test_registry_params_and_class_attribute_default(self):
        """_registry_params: the constructor parameters of the named class's
        registry entry (SinglePhaseReactor has 'batch' and 'tau_0'; the corn
        SSF resolves through its NRELBatchBioreactor ancestor, which lacks
        tau_0); None for an unknown or unlisted class.
        _class_attribute_default (R2.8): the SSF's tau_0 is a class
        attribute default only at confidence 1 (pinned; 0.75 unpinned ->
        False); NRELEthanolFermentation (core) -> True; a constructor
        argument (SinglePhaseReactor tau_0, AeratedBioreactor theta_O2) ->
        False; an abstract class (NRELAnaerobicBatchBioreactor) -> False."""
        sc = ctx(one_unit_doc(spec_unit("SinglePhaseReactor")))
        self.assertLessEqual({"batch", "tau_0"}, C._registry_params(sc, 0))
        self.assertFalse(C._class_attribute_default(sc, 0, "tau_0"))
        ssf = "SimultaneousSaccharificationFermentation"
        sc = ctx(one_unit_doc(spec_unit(ssf, url=BIP_URL), pinned=True))
        self.assertNotIn("tau_0", C._registry_params(sc, 0))
        self.assertTrue(C._class_attribute_default(sc, 0, "tau_0"))
        sc = ctx(one_unit_doc(spec_unit(ssf, url=BIP_URL)))
        self.assertFalse(C._class_attribute_default(sc, 0, "tau_0"))
        sc = ctx(one_unit_doc(spec_unit("NRELEthanolFermentation")))
        self.assertTrue(C._class_attribute_default(sc, 0, "tau_0"))
        sc = ctx(one_unit_doc(spec_unit("AeratedBioreactor")))
        self.assertFalse(C._class_attribute_default(sc, 0, "theta_O2"))
        sc = ctx(one_unit_doc(spec_unit("NRELAnaerobicBatchBioreactor")))
        self.assertIsNone(C._registry_params(sc, 0))
        self.assertFalse(C._class_attribute_default(sc, 0, "tau_0"))
        sc = ctx(one_unit_doc(spec_unit("Frobnicator", url=CUSTOM_URL)))
        self.assertIsNone(C._registry_params(sc, 0))

    def test_requirement_applies(self):
        """_requirement_applies (R2.13: waived only on affirmative evidence):
        unconditional -> True; the HXutility V requirement is waived when
        rigorous or when inlet and outlet phase sets are known and equal;
        the HXprocess phase0|phase1 requirement when they are known and
        equal (rigorous or not); the pump dP_design requirement when inlet
        and outlet P are known with a rise >= 1 Pa; an unevaluable condition
        (an outlet without P or without phase data) APPLIES the requirement;
        the stirred-tank batch flag applies unless the unit names a class
        whose registry entry lacks 'batch' (or is absent); unknown ->
        ValueError."""
        v_req = C._FAMILY_RULES["hx_utility"][1]
        phase_req = C._FAMILY_RULES["hx_process"][1]
        dp_req = C._FAMILY_RULES["pump"][1]
        vap = ctx(one_unit_doc(spec_unit("HXutility"), outs=({"phases": VAP},)))
        self.assertTrue(C._requirement_applies(C._TAU, vap, 0))
        self.assertTrue(C._requirement_applies(v_req, vap, 0))
        self.assertTrue(C._requirement_applies(phase_req, vap, 0))
        rig = ctx(one_unit_doc(spec_unit("HXutility", {"rigorous": True}),
                               outs=({"phases": VAP},)))
        self.assertFalse(C._requirement_applies(v_req, rig, 0))
        self.assertTrue(C._requirement_applies(phase_req, rig, 0))
        flat = ctx(one_unit_doc(spec_unit("Pump")))
        self.assertFalse(C._requirement_applies(v_req, flat, 0))
        self.assertFalse(C._requirement_applies(phase_req, flat, 0))
        self.assertTrue(C._requirement_applies(dp_req, flat, 0))
        rise = ctx(one_unit_doc(spec_unit("Pump"), outs=({"P": 3e5},)))
        self.assertFalse(C._requirement_applies(dp_req, rise, 0))
        nop = ctx(one_unit_doc(spec_unit("Pump"), outs=({"P": None},)))
        self.assertTrue(C._requirement_applies(dp_req, nop, 0))
        unphased = ctx(one_unit_doc(spec_unit("HXutility"),
                                    outs=({"phases": {}},)))
        self.assertTrue(C._requirement_applies(v_req, unphased, 0))
        self.assertTrue(C._requirement_applies(phase_req, unphased, 0))
        # batch_param (R2.8): applies to a named class whose registry entry
        # has 'batch' and to a reactor of unknown (or only inferred) class;
        # waived for a named class without it or absent from the registry.
        batch = C._BATCH
        for unit, applies in (
                (spec_unit("SinglePhaseReactor"), True),
                (spec_unit("AcidulationReactor", url=BIP_URL), True),
                ({"unit_type": "Fermentation"}, True),
                (spec_unit("EnzymeTreatment"), False),
                (spec_unit("CoFermentation", url=BIP_URL), False)):
            sc = ctx(one_unit_doc(unit))
            self.assertEqual(C._requirement_applies(batch, sc, 0), applies, unit)
        with self.assertRaises(ValueError):
            C._requirement_applies(C._Req(1, ("x",), {}, when="bogus"), flat, 0)

    def test_counted_keys(self):
        """_counted_keys: HXutility V counts toward the T|H|V requirement only
        when rigorous is True."""
        req = C._FAMILY_RULES["hx_utility"][0]
        sc = ctx(one_unit_doc(spec_unit("HXutility", {"V": 1})))
        self.assertEqual(C._counted_keys(req, sc, 0, {"V"}), set())
        sc = ctx(one_unit_doc(spec_unit("HXutility", {"V": 1, "rigorous": True})))
        self.assertEqual(C._counted_keys(req, sc, 0, {"V", "rigorous"}), {"V"})

    def test_inconsistent_keys(self):
        """_inconsistent_keys: explicit T 2 K off the outlet -> ['T'], 0.5 K
        off -> []; P 2 % off -> ['P'], 0.5 % -> []; a zero P and an empty
        outlet are ignored; unguarded requirement -> []."""
        req_t, req_p = C._T_REQ, C._P_REQ
        def bad(req, specs, **out):
            sc = ctx(one_unit_doc(spec_unit("Pump", specs), outs=(out,)))
            return C._inconsistent_keys(req, sc, 0, set(specs))
        self.assertEqual(bad(req_t, {"T": 302.0}), ["T"])
        self.assertEqual(bad(req_t, {"T": 300.5}), [])
        self.assertEqual(bad(req_p, {"P": 101325.0 * 1.02}), ["P"])
        self.assertEqual(bad(req_p, {"P": 101325.0 * 1.005}), [])
        self.assertEqual(bad(req_p, {"P": 0}), [])
        self.assertEqual(bad(req_t, {"T": 400.0}, mass=0.0), [])
        self.assertEqual(bad(C._TAU, {"tau": 1.0}), [])
        # 'T_main' (reactor T): only the largest-flow outlet is checked, so a
        # small ambient catalyst purge beside the effluent at T is fine, while
        # a plain 'T' guard (flash) checks every outlet.
        doc = one_unit_doc(spec_unit("SinglePhaseReactor", {"T": 350.0}),
                           outs=({"T": 350.0, "mass": 100.0},
                                 {"T": 298.15, "mass": 1.0}))
        sc = ctx(doc)
        self.assertEqual(C._inconsistent_keys(req_t, sc, 0, {"T"}), [])
        flash_t = C._Req(1, ("T",), {}, guard={"T": "T"})
        self.assertEqual(C._inconsistent_keys(flash_t, sc, 0, {"T"}), ["T"])

    def test_main_outlet(self):
        """_main_outlet: the stream with the largest |total flow| (mass, else
        molar); streams without a flow rank last; [] -> []."""
        a = stream("a", "U1", "None", mass=1.0)
        b = stream("b", "U1", "None", mass=5.0)
        c = stream("c", "U1", "None")
        del c["stream_properties"]["total_mass_flow"]
        del c["stream_properties"]["total_molar_flow"]
        self.assertEqual(C._main_outlet([a, b, c]), [b])
        self.assertEqual(C._main_outlet([c, a]), [a])
        self.assertEqual(C._main_outlet([]), [])

    def test_requirement_score(self):
        """_requirement_score: explicit -> 1; derivable only -> 0.75; flash
        n_of(2) with 1 explicit + T derivable -> (1 + 0.75) / 2; an
        inconsistent guarded key -> 0."""
        sc = ctx(one_unit_doc(spec_unit("HXutility")))
        hx = C._FAMILY_RULES["hx_utility"][0]
        self.assertEqual(C._requirement_score(hx, sc, 0, {"H"}), 1.0)
        self.assertEqual(C._requirement_score(hx, sc, 0, set()), C.DERIVED_CREDIT)
        flash = C._FAMILY_RULES["flash"][0]
        self.assertAlmostEqual(C._requirement_score(flash, sc, 0, {"Q"}),
                               (1 + C.DERIVED_CREDIT) / 2)
        sc = ctx(one_unit_doc(spec_unit("HXutility", {"T": 350.0})))
        self.assertEqual(C._requirement_score(hx, sc, 0, {"T"}), 0.0)

    def test_requirement_locator(self):
        """_requirement_locator -> 'units[F1].design_input_specs: 2 of
        T|P|V|Q|y|x', with a derivable note when partially credited and an
        inconsistency note naming the key."""
        flash = C._FAMILY_RULES["flash"][0]
        self.assertEqual(C._requirement_locator("units[F1]", flash, 0.0),
                         "units[F1].design_input_specs: 2 of T|P|V|Q|y|x")
        self.assertIn("only derivable",
                      C._requirement_locator("units[F1]", flash, 0.5))
        self.assertIn("inconsistent: T disagrees",
                      C._requirement_locator("units[F1]", flash, 0.0, ["T"]))

    def test_unit_spec_score(self):
        """_unit_spec_score: HXutility with T -> (1.0, []); unknown family ->
        (0.0, ['...unit family unknown...']); no requirement -> (None, [])."""
        self.assertEqual(spec_score("HXutility", {"T": 300.0}), (1.0, []))
        score, missing = spec_score("Unit")
        self.assertEqual(score, 0.0)
        self.assertIn("unit family unknown", missing[0])
        self.assertEqual(spec_score("Mixer"), (None, []))

    def test_excused_params(self):
        """_excused_params on a one-unit context: HXutility with T -> {H, V};
        a non-rigorous V alone does not count -> nothing, a rigorous V ->
        {T, H}; flash with 2 of 6 -> the other 4, with 1 of 6 -> nothing;
        the bare 'Unit' (no rules) -> nothing."""
        def excused(name, specs):
            sc = ctx(one_unit_doc(spec_unit(name, specs)))
            return C._excused_params(sc, 0, C._explicit_keys(sc.units[0]))
        self.assertEqual(excused("HXutility", {"T": 300.0}), {"V", "H"})
        self.assertEqual(excused("HXutility", {"V": 1}), set())
        self.assertEqual(excused("HXutility", {"V": 1, "rigorous": True}),
                         {"T", "H"})
        self.assertEqual(excused("Flash", {"T": 300.0, "P": 101325.0}),
                         {"V", "Q", "y", "x"})
        self.assertEqual(excused("Flash", {"T": 300.0}), set())
        self.assertEqual(excused("Unit", {"zz": 300.0}), set())


class TestFamilyRules(unittest.TestCase):
    """Each family / class rule of Revision 1 R1.4 on a one-unit document
    (feed -> U1 -> product, liquid at 300 K and 101325 Pa unless stated)."""

    def check(self, expected, *args, **kwargs):
        self.assertAlmostEqual(spec_score(*args, **kwargs)[0], expected,
                               msg=args[0])

    def test_splitter_dryer_extraction(self):
        """splitter/dryer: explicit split 1, derivable from full ports 0.75,
        not derivable 0 -- a plain Splitter's moisture requirement is waived
        (its exact class lacks moisture_content) and counts as met (R5.9),
        so the unit scores 1, 0.875 and 0.5; VentScrubber's split is its
        'gas' list;
        MultiStageMixerSettlers: N_stages without feed_stages (a constructor
        default, R2.8: must be stated) -> 0.5, with it 1; MixerSettler has
        no requirement."""
        two = ({}, {})
        self.check(1.0, "Splitter", {"split": [0.5]}, outs=two)
        self.check(0.875, "Splitter", outs=two)
        self.check(0.5, "Splitter", outs=({}, {"T": None}))
        self.check(0.75, "DrumDryer", outs=two)
        self.check(1.0, "VentScrubber", {"gas": ["CO2"]}, outs=two)
        self.check(0.5, "MultiStageMixerSettlers", {"N_stages": 4})
        self.check(1.0, "MultiStageMixerSettlers",
                   {"N_stages": 4, "feed_stages": [0, -1]})
        self.assertIsNone(spec_score("MixerSettler")[0])

    def test_flash(self):
        """flash n_of(2): T explicit + P derivable -> 0.875; nothing explicit
        but T, P, V, Q derivable -> 0.75; an inconsistent explicit T -> 0."""
        self.check(0.875, "Flash", {"T": 300.0})
        self.check(0.75, "Flash", ins=({"enthalpy": 1.0},),
                   outs=({"enthalpy": 1.0},))
        self.check(0.0, "Flash", {"T": 305.0, "P": 101325.0})

    def test_stage_classes(self):
        """StageEquilibrium: phases + one stage spec (T derivable from the
        outlet; the adiabatic Q default is a constructor default, R2.8, so
        it earns nothing) -> 1.75/2; phases + a consistent
        T -> 1; an inconsistent T -> 0.5. PhasePartition: phases -> 1,
        nothing -> 0."""
        self.check(0.875, "StageEquilibrium", {"phases": "lg"})
        self.check(1.0, "StageEquilibrium", {"phases": "lg", "T": 300.0})
        self.check(0.5, "StageEquilibrium", {"phases": "lg", "T": 350.0})
        self.check(1.0, "PhasePartition", {"phases": "lg"})
        self.check(0.0, "PhasePartition")

    def test_hx_utility(self):
        """HXutility: T -> 1; non-rigorous V alone on liquid in / liquid out
        -> 0.875 (T derived 0.75; the phase-change V requirement waived, so
        met, R5.9); a rigorous V alone -> 1; T with a non-rigorous l->g
        change -> 0.875 (V derivable from outlet phases); T + V on the
        change -> 1; an inconsistent T -> 0.5 (0 for T|V|H, the waived V
        requirement met)."""
        vap = ({"phases": VAP},)
        self.check(1.0, "HXutility", {"T": 300.0})
        self.check(0.875, "HXutility", {"V": 1})
        self.check(1.0, "HXutility", {"V": 1, "rigorous": True}, outs=vap)
        self.check(0.875, "HXutility", {"T": 300.0}, outs=vap)
        self.check(1.0, "HXutility", {"T": 300.0, "V": 1}, outs=vap)
        self.check(0.5, "HXutility", {"T": 350.0})

    def test_hx_process_and_steam_injection(self):
        """HXprocess dT -> 1; with a phase change also phase0|phase1 (derivable
        from phased outlets -> 1.75/2, explicit -> 1); JetCooker T -> 1;
        SteamMixer needs P (outlet P derivable -> 0.75, explicit -> 1)."""
        self.check(1.0, "HXprocess", {"dT": 5.0})
        two_in = ({}, {"phases": VAP})
        two_out = ({}, {})
        self.check(0.875, "HXprocess", {"dT": 5.0}, ins=two_in, outs=two_out)
        self.check(1.0, "HXprocess", {"dT": 5.0, "phase1": "l"}, ins=two_in,
                   outs=two_out)
        self.check(1.0, "JetCooker", {"T": 300.0})
        self.check(0.75, "SteamMixer", {"T": 300.0})
        self.check(1.0, "SteamMixer", {"P": 101325.0})

    def test_pump_valve_compressor(self):
        """Pump: P with no pressure rise also needs dP_design (0.5, with it
        1); P with a rise -> 1; an inconsistent P over a known rise -> 0.5
        (0 for P, the waived dP_design requirement met, R5.9). Valve P -> 1;
        IsentropicCompressor P without eta -> 0.5; the abstract Compressor
        (R2.6) names no class but STATES its family (R3.1): P alone (which
        the tightest registry entry, a valve, also holds) never swaps it ->
        ('compressor', 0.5), scored by the compressor rules (P, eta -> 0.5;
        with eta 1.0)."""
        self.check(0.5, "Pump", {"P": 101325.0})
        self.check(1.0, "Pump", {"P": 101325.0, "dP_design": 101325.0})
        self.check(1.0, "Pump", {"P": 2e5}, outs=({"P": 2e5},))
        self.check(0.5, "Pump", {"P": 3e5}, outs=({"P": 2e5},))
        self.check(1.0, "IsenthalpicValve", {"P": 101325.0})
        self.check(0.5, "IsentropicCompressor", {"P": 101325.0})
        sc = ctx(one_unit_doc(spec_unit("Compressor", {"P": 101325.0})))
        self.assertEqual(sc.unit_classes[0][1:], ("compressor", 0.5))
        self.assertEqual(C._unit_spec_score(sc, 0)[0], 0.5)
        self.check(1.0, "Compressor", {"P": 101325.0, "eta": 0.8})

    def test_distillation_and_staged_columns(self):
        """BinaryDistillation: 5 explicit + partial_condenser derivable from
        phased outlets -> 5.75/6; k from Reflux/Minimum reflux results ->
        5.5/6. MESHDistillation fully stated -> 1;
        AdiabaticMultiStageVLEColumn with N_stages only -> (1 + 0 + 0 +
        0.75) / 4 (feed_stages is a constructor default, R2.8)."""
        base = {"LHK": ["A", "B"], "P": 101325.0, "y_top": 0.9, "x_bot": 0.01}
        self.check(5.75 / 6, "BinaryDistillation", dict(base, k=1.2))
        self.check(5.5 / 6, "BinaryDistillation", base,
                   results={"Reflux": 2.0, "Minimum reflux": 1.5})
        self.check(1.0, "MESHDistillation", {
            "N_stages": 10, "feed_stages": [5], "reflux": 2.0, "boilup": 3.0,
            "P": 101325.0})
        self.check(1.75 / 4, "AdiabaticMultiStageVLEColumn", {"N_stages": 10})

    def test_evaporator(self):
        """MultiEffectEvaporator: P list + V -> 1; nothing -> (0 + 0.75)/2."""
        self.check(1.0, "MultiEffectEvaporator", {"P": [1e5, 5e4], "V": 0.5})
        self.check(0.375, "MultiEffectEvaporator")

    def test_reactor_families(self):
        """reactor T/tau/V_wf/batch (R2.8: the stirred-tank batch flag of
        SinglePhaseReactor; without it 3/4; tau + batch only -> 2.75/4;
        inconsistent T -> 3/4); EnzymeTreatment takes no batch -> T/tau/V_wf
        -> 1; batch bioreactor N from 'Number of reactors' and the
        class-attribute tau_0 -> 3.5/4 (NRELEthanolFermentation, core, and
        the legacy NRELBatchBioreactor on a URL in the pinned repository;
        unpinned, or pinned but on a biosteam URL (R3.6), it is custom at
        0.75 and tau_0 needs a confidence-1 class -> 2.75/4), also when T (the
        heat-utility temperature) differs from the adiabatically mixed
        outlets (no guard); the corn SSF (V explicit) 3.75/4 when pinned,
        3/4 unpinned; aerated theta_O2 is a constructor argument (R2.8) ->
        3/4 without it (a Q_O2_consumption duty override does not replace
        it), 1 with it; GasFedBioreactor gas_substrates without theta ->
        4/5, neither -> 3/5; Gibbs T and P from outlets -> 0.75;
        BatchCrystallizer T/tau/N -> 1; corn Liquefaction tau + V_wf -> 1."""
        full = {"T": 300.0, "tau": 2, "V_wf": 0.8}
        self.check(0.75, "SinglePhaseReactor", full)
        self.check(1.0, "SinglePhaseReactor", dict(full, batch=False))
        self.check(2.75 / 4, "SinglePhaseReactor", {"tau": 2, "batch": True})
        self.check(0.75, "SinglePhaseReactor", dict(full, T=310.0, batch=False))
        self.check(1.0, "EnzymeTreatment", full)
        results = {"Number of reactors": 4}
        self.check(0.875, "NRELEthanolFermentation", {"T": 300.0, "tau": 24},
                   results=results)
        self.check(0.875, "NRELBatchBioreactor", {"T": 305.15, "tau": 24},
                   results=results, url=BIP_URL, pinned=True)
        self.check(2.75 / 4, "NRELBatchBioreactor", {"T": 305.15, "tau": 24},
                   results=results, pinned=True)
        self.check(2.75 / 4, "NRELBatchBioreactor", {"T": 300.0, "tau": 24},
                   results=results)
        ssf = "SimultaneousSaccharificationFermentation"
        self.check(3.75 / 4, ssf, {"T": 300.0, "tau": 24, "V": 1900.0},
                   url=BIP_URL, pinned=True)
        self.check(3.0 / 4, ssf, {"T": 300.0, "tau": 24, "V": 1900.0},
                   url=BIP_URL)
        aerated = {"T": 300.0, "tau": 24, "V_wf": 0.8}
        self.check(0.75, "AeratedBioreactor", aerated)
        self.check(0.75, "AeratedBioreactor",
                   dict(aerated, Q_O2_consumption=-460e3))
        self.check(1.0, "AeratedBioreactor", dict(aerated, theta_O2=0.4))
        self.check(0.8, "GasFedBioreactor",
                   dict(aerated, gas_substrates=["H2"]))
        self.check(0.6, "GasFedBioreactor", aerated)
        self.check(0.75, "GibbsEquilibriumReactor")
        self.check(1.0, "BatchCrystallizer", {"T": 300.0, "tau": 2, "N": 2})
        self.check(1.0, "Liquefaction", {"tau": 1.0, "V_wf": 0.9}, url=BIP_URL)

    def test_wastewater_classes(self):
        """biosteam.wastewater class rules; every default is a constructor
        argument (R2.8), so it must be stated: AnMBR T without reactor_type
        -> 0.5, with it 1; PolishingFilter T without OLR|HLR -> 0.5;
        InternalCirculationRx T only -> 1/3; Anaerobic/AerobicDigestion
        without sludge_split / evaporation -> 0; MembraneBioreactor is a
        splitter (split explicit -> 1)."""
        self.check(0.5, "AnMBR", {"T": 308.15})
        self.check(1.0, "AnMBR", {"T": 308.15, "reactor_type": "CSTR"})
        self.check(0.5, "PolishingFilter", {"T": 303.15})
        self.check(1 / 3, "InternalCirculationRx", {"T": 308.15})
        self.check(0.0, "AnaerobicDigestion")
        self.check(1.0, "AerobicDigestion", {"evaporation": 0.0113})
        self.assertEqual(C._class_family("MembraneBioreactor"), "splitter")
        self.check(1.0, "MembraneBioreactor", {"split": [0.5]}, outs=({}, {}))

    def test_tank_facilities_and_excluded(self):
        """tank tau (1 / 0); ThermalOxidizer (combustor, R3.5) needs
        duty_per_kg (0 without, 1 with); BoilerTurbogenerator, Boiler and
        HeatExchangerNetwork are no longer UOP-02 units (R2.5: scored by
        CMP-UOP-06) -> None, whatever they state (the port-less
        HeatExchangerNetwork given no streams, as R4.8 requires)."""
        self.check(1.0, "StorageTank", {"tau": 2.0})
        self.check(0.0, "StorageTank")
        self.check(0.0, "ThermalOxidizer")
        self.check(1.0, "ThermalOxidizer", {"duty_per_kg": 61.0})
        for name, specs, ports in (
                ("BoilerTurbogenerator", {"boiler_efficiency": 0.8}, ({},)),
                ("Boiler", {}, ({},)),
                ("HeatExchangerNetwork", {"T_min_app": 5.0}, ())):
            self.assertEqual(spec_score(name, specs, ins=ports, outs=ports),
                             (None, []), name)


# ------------------------------------------------------- thermo criteria

class TestThermoCriteria(unittest.TestCase):
    def test_thm_01_coverage(self):
        """_score_thm_01 (CMP-THM-01): valid_doc -> 1.0; an undeclared stream
        component -> 1/2 naming it; no chemicals but referenced -> 0; declared
        but unreferenced -> 1; nothing at all -> 0."""
        self.assertEqual(run(C._score_thm_01, valid_doc())[0], 1.0)
        doc = valid_doc()
        doc["streams"][0]["stream_properties"]["phases"]["l"]["composition"].append(
            {"component_name": "Water", "mol_fraction": 0.0})
        score, _m, missing = run(C._score_thm_01, doc)
        self.assertEqual(score, 0.5)
        self.assertIn("'Water' (referenced by streams[feed])", missing[0])
        doc = valid_doc()
        remove(doc, "chemicals")
        self.assertEqual(run(C._score_thm_01, doc)[0], 0.0)
        empty = {"chemicals": [{"id": "A"}]}
        self.assertEqual(run(C._score_thm_01, empty)[0], 1.0)
        self.assertEqual(run(C._score_thm_01, {})[0], 0.0)

    def test_chemical_references_include_reactions_and_utilities(self):
        """_chemical_references: reaction reactant/equation/stoichiometry and
        utility compositions are references, each with a locator."""
        doc = valid_doc()
        doc["units"][0]["reactions"] = [{"equation": "Ethanol -> 2 CO2",
                                         "reactant": "Ethanol",
                                         "stoichiometry": {"O2": -1}}]
        doc["utilities"]["heat_utilities"] = [
            {"id": "steam", "composition": [{"component_name": "Water",
                                             "mol_fraction": 1}]}]
        refs = C._chemical_references(ctx(doc))
        self.assertEqual(refs["CO2"], "units[U1].reactions[0]")
        self.assertEqual(refs["O2"], "units[U1].reactions[0]")
        self.assertEqual(refs["Water"], "utilities.heat_utilities[steam]")

    def test_chemical_population(self):
        """_chemical_population (R1.1): every declared chemical (with its id,
        or None when it has none) plus every referenced but undeclared id
        (chemical None, labelled with its first reference)."""
        doc = valid_doc()
        doc["streams"][0]["stream_properties"]["phases"]["l"]["composition"].append(
            {"component_name": "Water", "mol_fraction": 0.0})
        doc["chemicals"].append({"formula": "H2"})
        population = C._chemical_population(ctx(doc))
        self.assertEqual([(c is None, cid) for c, _l, cid in population],
                         [(False, "Ethanol"), (False, None), (True, "Water")])
        self.assertEqual(population[2][1], "chemicals['Water'] (undeclared; "
                                           "referenced by streams[feed])")

    def test_empty_chemical_population(self):
        """_empty_chemical_population: streams or units present -> 0 with a
        'chemicals: empty' locator; nothing at all -> None."""
        doc = valid_doc()
        remove(doc, "chemicals")
        for s in doc["streams"]:
            del s["stream_properties"]["phases"], s["stream_properties"]["composition"]
        self.assertEqual(C._empty_chemical_population(ctx(doc))[::2],
                         (0.0, ["chemicals: empty"]))
        self.assertIsNone(C._empty_chemical_population(ctx({}))[0])

    def test_thm_02_credit(self):
        """_thm_02_credit (R3.2: the MAX over the tiers reached) -> (credit,
        note, tier): a registry id the database resolves -> (1, None,
        'registry'); a well-formed id it cannot resolve -> 0.5 'unresolved'
        (note: unverifiable); a junk one ('x') counts as absent -> 0 with a
        'not a well-formed' note; id resolvable by name with a matching
        molar mass -> NAME_RESOLVED_CREDIT (0.75, 'name'); resolvable with
        nothing to verify it -> NAME_UNVERIFIED_CREDIT (0.5, 'unverified');
        resolvable but of another molar mass -> the formula tier
        FORMULA_ONLY_CREDIT (0.25); an isomer-ambiguous formula id (>= 2
        carbon atoms, 'C2H6O') never earns the name tier (0.25 via the id),
        while a one-carbon or carbon-free formula id resolves like a name
        (R2.3 corrected: 'HP' mismatches hematoporphyrin -> 0.25 with the
        molar-mass note; 'CO2' with a matching molar mass -> verified 0.75);
        nothing -> 0; each below 1 with a registry_id locator suffix."""
        with name_lookup({"Ethanol", "Starch", "HP", "C2H6O", "64-17-5"}):
            self.assertEqual(C._thm_02_credit({"registry_id": "64-17-5"}, "X"),
                             (1.0, None, "registry"))
            credit, note, tier = C._thm_02_credit({"registry_id": "50-00-0"}, "X")
            self.assertEqual((credit, tier), (C.REGISTRY_UNRESOLVED_CREDIT,
                                              "unresolved"))
            self.assertIn("does not resolve it", note)
            credit, note, tier = C._thm_02_credit({"registry_id": "x"}, "X")
            self.assertEqual((credit, tier), (0.0, "none"))
            self.assertIn("not a well-formed", note)
            self.assertEqual(C._thm_02_credit({"formula": "C2H6O"}, "Ethanol")[0],
                             C.NAME_RESOLVED_CREDIT)
            self.assertEqual(C._thm_02_credit({}, "Ethanol")[0],
                             C.NAME_UNVERIFIED_CREDIT)
            self.assertEqual(C._thm_02_credit({"molar_mass": 162.14}, "Starch")[0],
                             C.NAME_UNVERIFIED_CREDIT)
            credit, note, _tier = C._thm_02_credit({"molar_mass": 90.0}, "Ethanol")
            self.assertEqual(credit, 0.0)
            self.assertIn("another molar mass", note)
            credit, note, _tier = C._thm_02_credit({"formula": "C2H6O"},
                                                   "SolubleProtein")
            self.assertEqual((credit, note), (C.FORMULA_ONLY_CREDIT,
                                              ".registry_id (formula only)"))
            credit, note, _tier = C._thm_02_credit({"molar_mass": 46.07}, "C2H6O")
            self.assertEqual(credit, C.FORMULA_ONLY_CREDIT)
            self.assertIn("isomer-ambiguous", note)
        with name_lookup({"HP": (598.68872, "C34H38N4O6"),
                          "CO2": (44.0095, "CO2")}):
            credit, note, _tier = C._thm_02_credit({"molar_mass": 90.08}, "HP")
            self.assertEqual(credit, C.FORMULA_ONLY_CREDIT)
            self.assertIn("another molar mass", note)
            self.assertEqual(C._thm_02_credit({"molar_mass": 44.01}, "CO2")[0],
                             C.NAME_RESOLVED_CREDIT)
            self.assertEqual(C._thm_02_credit({}, None)[0], 0.0)
        # MAX over tiers: a verified name (0.75) beats an unresolvable id.
        with name_lookup({"Ethanol"}):
            self.assertEqual(C._thm_02_credit(
                {"registry_id": "50-00-0", "formula": "C2H6O"}, "Ethanol")[::2],
                (C.NAME_RESOLVED_CREDIT, "name"))
        # Database unavailable: a well-formed id 0.5, and no name tier.
        with name_lookup(search=None):
            credit, note, tier = C._thm_02_credit(
                {"registry_id": "64-17-5", "formula": "C2H6O"}, "Ethanol")
            self.assertEqual((credit, tier), (0.5, "unresolved"))
            self.assertIn("unavailable", note)
        self.assertEqual((C.REGISTRY_UNRESOLVED_CREDIT, C.NAME_RESOLVED_CREDIT,
                          C.NAME_UNVERIFIED_CREDIT, C.FORMULA_ONLY_CREDIT),
                         (0.5, 0.75, 0.5, 0.25))

    def test_name_tier_and_record_agreement(self):
        """_name_tier: None for a non-string, formula-like or unresolvable id,
        else _record_agreement of the record: 'verified' when the molar
        masses agree within NAME_MW_RTOL (declared, or derived by
        _chemical_molar_mass from the formula; the record's by
        _record_molar_mass) or the formulas match; 'unverified' when either
        side has nothing comparable; 'mismatch' otherwise. _name_record
        returns the record itself (cached); 'SolubleProtein' resolves
        neither in the (fake) database nor as a built-in."""
        with name_lookup({"Ethanol"}):
            self.assertEqual(C._name_record("Ethanol").MW, 46.06844)
            self.assertIsNone(C._name_record("SolubleProtein"))
            self.assertEqual(C._name_tier({"molar_mass": 46.07}, "Ethanol"),
                             "verified")
            self.assertEqual(C._name_tier({"formula": "C2H6O"}, "Ethanol"),
                             "verified")
            self.assertEqual(C._name_tier(None, "Ethanol"), "unverified")
            self.assertEqual(C._name_tier({"molar_mass": 50.0}, "Ethanol"),
                             "mismatch")
            self.assertIsNone(C._name_tier({}, "C2H6O"))
            self.assertIsNone(C._name_tier({}, ["Ethanol"]))
        record = types.SimpleNamespace(MW=None, formula="H2O")
        self.assertAlmostEqual(C._record_molar_mass(record), 18.015, places=2)
        self.assertEqual(C._record_agreement(record, {"formula": "H2O"}),
                         "verified")
        self.assertEqual(C._record_agreement(types.SimpleNamespace(),
                                             {"molar_mass": 5.0}), "unverified")
        self.assertAlmostEqual(C._chemical_molar_mass({"formula": "H2O"}),
                               18.015, places=2)
        self.assertEqual(C._chemical_molar_mass({"molar_mass": -1.0,
                                                 "formula": "x"}), None)

    def test_registry_status(self):
        """_registry_status (R3.2), with a fake database resolving CAS
        64-17-5, 'SMILES=C' (methane) and an ethanol InChIKey: 'absent' (no
        id, blank); 'malformed' ('x', 'InChI=1S/x', a list); 'resolved'
        (64-17-5 with an agreeing molar mass within REGISTRY_MW_RTOL = 2 %,
        with a matching formula, or with nothing to compare; the InChIKey
        with or without its prefix); 'contradicted' (64-17-5 with molar
        mass 90; 'C' = methane for a 162 g/mol starch; an InChI whose
        formula layer C2H6O disagrees with a declared C6H10O5 -- even with
        the database unavailable); 'unresolved' (the invented CAS
        98765-43-6, a fake InChIKey, cellulose's real CAS 9004-34-6);
        'unavailable' (any well-formed id when the database cannot be
        imported). _database_resolved: True only for 'resolved' or a
        verified/unverified name -- a thermosteam built-in name included
        (R4.1: 'Cellulose' resolves even beside cellulose's unresolvable
        CAS, 'SolubleProtein' does not); with the database unavailable only
        a built-in resolves."""
        key = "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
        inchi = "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"
        records = {"64-17-5": (46.06844, "C2H6O"), "SMILES=C": (16.04, "CH4"),
                   f"InChIKey={key}": (46.06844, "C2H6O"), inchi:
                   (46.06844, "C2H6O"), "Ethanol": (46.06844, "C2H6O")}
        status = lambda **chem: C._registry_status(chem)
        with name_lookup(records):
            self.assertEqual(status(), "absent")
            self.assertEqual(status(registry_id="  "), "absent")
            for rid in ("x", "InChI=1S/x", ["64-17-5"]):
                self.assertEqual(status(registry_id=rid), "malformed", rid)
            for chem in ({"molar_mass": 46.5}, {"formula": "C2H6O"}, {}):
                self.assertEqual(status(registry_id="64-17-5", **chem),
                                 "resolved", chem)
            self.assertEqual(status(registry_id=key, molar_mass=46.07),
                             "resolved")
            self.assertEqual(status(registry_id=f"InChIKey={key}"), "resolved")
            self.assertEqual(status(registry_id=inchi, formula="C2H6O"),
                             "resolved")
            self.assertEqual(status(registry_id="64-17-5", molar_mass=90.0),
                             "contradicted")
            self.assertEqual(status(registry_id="C", formula="C6H10O5",
                                    molar_mass=162.14), "contradicted")
            self.assertEqual(status(registry_id=inchi, formula="C6H10O5"),
                             "contradicted")
            for rid in ("98765-43-6", "ABCDEFGHIJKLMN-OPQRSTUVWX-Y",
                        "9004-34-6"):
                self.assertEqual(status(registry_id=rid, molar_mass=162.14),
                                 "unresolved", rid)
            self.assertTrue(C._database_resolved(
                {"registry_id": "64-17-5"}, "X"))
            self.assertTrue(C._database_resolved({}, "Ethanol"))
            self.assertFalse(C._database_resolved(
                {"registry_id": "9004-34-6"}, "SolubleProtein"))
            self.assertFalse(C._database_resolved(None, "SolubleProtein"))
            self.assertTrue(C._database_resolved(
                {"registry_id": "9004-34-6", "molar_mass": 162.14},
                "Cellulose"))
            self.assertTrue(C._database_resolved(None, "Cellulose"))
            self.assertFalse(C._database_resolved({"molar_mass": 180.16},
                                                  "Cellulose"))
        with name_lookup(search=None):
            self.assertEqual(status(registry_id="64-17-5"), "unavailable")
            self.assertEqual(status(registry_id=inchi, formula="C6H10O5"),
                             "contradicted")
            self.assertFalse(C._database_resolved(
                {"registry_id": "64-17-5"}, "Ethanol"))
            self.assertTrue(C._database_resolved({}, "Cellulose"))
        self.assertEqual(C.REGISTRY_MW_RTOL, 0.02)

    def test_inchi_formula_layer(self):
        """_inchi_formula: the formula layer of an InChI as element counts
        (ethanol {C:2, H:6, O:1}; components summed with multipliers,
        'InChI=1S/2Na.H2O4S/...' -> {Na:2, H:2, O:4, S:1}; a mobile-proton
        layer '/p+1' adds an H: ammonium from 'InChI=1S/H3N/h1H3/p+1' ->
        {H:4, N:1}); None for a non-InChI, an unparseable layer ('x') or a
        non-string. _same_counts compares count maps (1e-9);
        _counts_molar_mass gives {C:2, H:6, O:1} ~46.07 and None for {}.
        _inchi_contradicts: layer vs the declared formula ('C2H6O' agrees,
        'C6H10O5' contradicts); with no formula, vs the declared molar mass
        within REGISTRY_MW_RTOL (46.5 agrees, 90 contradicts); nothing
        comparable -> False."""
        inchi = "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"
        self.assertEqual(C._inchi_formula(inchi), {"C": 2, "H": 6, "O": 1})
        self.assertEqual(C._inchi_formula("InChI=1S/2Na.H2O4S/c;;1-5(2,3)4"),
                         {"Na": 2, "H": 2, "O": 4, "S": 1})
        self.assertEqual(C._inchi_formula("InChI=1S/H3N/h1H3/p+1"),
                         {"H": 4, "N": 1})
        for text in ("x", "InChI=1S/x", "64-17-5", None):
            self.assertIsNone(C._inchi_formula(text), text)
        self.assertTrue(C._same_counts({"C": 2, "H": 6}, {"H": 6.0, "C": 2}))
        self.assertFalse(C._same_counts({"C": 2}, {"C": 2, "H": 1}))
        self.assertFalse(C._same_counts(None, {"C": 2}))
        self.assertAlmostEqual(C._counts_molar_mass({"C": 2, "H": 6, "O": 1}),
                               46.07, places=1)
        self.assertIsNone(C._counts_molar_mass({}))
        self.assertFalse(C._inchi_contradicts(inchi, {"formula": "C2H6O"}))
        self.assertTrue(C._inchi_contradicts(inchi, {"formula": "C6H10O5"}))
        self.assertFalse(C._inchi_contradicts(inchi, {"molar_mass": 46.5}))
        self.assertTrue(C._inchi_contradicts(inchi, {"molar_mass": 90.0}))
        self.assertFalse(C._inchi_contradicts(inchi, {}))
        self.assertFalse(C._inchi_contradicts("x", {"formula": "C2H6O"}))

    def test_registry_status_real_database(self):
        """R3.2 against the installed `chemicals` database (skipped when it
        cannot be imported): CAS 64-17-5, the ethanol InChI, InChIKey and
        SMILES 'CCO' resolve for ethanol (C2H6O, 46.07); 'C' resolves to
        methane and so contradicts a 162 g/mol starch; the invented CAS
        98765-43-6, a fake InChIKey and the real but unlisted CAS of
        cellulose (9004-34-6) and starch (9005-25-8) are 'unresolved'."""
        with name_lookup(search=C._chemicals_search()):
            if C._chemicals_search() is None:
                self.skipTest("chemicals package not importable")
            ethanol = {"formula": "C2H6O", "molar_mass": 46.07}
            for rid in ("64-17-5", "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3",
                        "LFQSCWFLJHTTHZ-UHFFFAOYSA-N", "CCO"):
                self.assertEqual(C._registry_status(
                    dict(ethanol, registry_id=rid)), "resolved", rid)
            starch = {"formula": "C6H10O5", "molar_mass": 162.1406}
            self.assertEqual(C._registry_status(dict(starch, registry_id="C")),
                             "contradicted")
            for rid in ("98765-43-6", "ABCDEFGHIJKLMN-OPQRSTUVWX-Y",
                        "9004-34-6", "9005-25-8"):
                self.assertEqual(C._registry_status(
                    dict(starch, registry_id=rid)), "unresolved", rid)

    def test_thm_02_identity(self):
        """_score_thm_02 (CMP-THM-02): Ethanol with formula, name tier
        unavailable -> 0.25; name-resolvable -> 0.75; a database-resolved
        registry_id -> 1.0 (message counts 1 resolved id); an undeclared
        referenced id counts as 0 (-> 0.5 beside a registry id); streams
        but no chemicals at all -> 0; empty doc -> None; with the database
        unavailable, a registry id -> 0.5 (counted as unresolvable)."""
        with name_lookup(search=None):
            self.assertEqual(run(C._score_thm_02, valid_doc())[0], 0.25)
            doc = valid_doc()
            mutate(doc, "chemicals/0/registry_id", "64-17-5")
            score, message, _missing = run(C._score_thm_02, doc)
            self.assertEqual(score, 0.5)
            self.assertIn("1 carry an unresolvable id", message)
        with name_lookup({"Ethanol", "64-17-5"}):
            self.assertEqual(run(C._score_thm_02, valid_doc())[0], 0.75)
            doc = valid_doc()
            mutate(doc, "chemicals/0/registry_id", "64-17-5")
            score, message, _missing = run(C._score_thm_02, doc)
            self.assertEqual(score, 1.0)
            self.assertIn("1 of 1 chemical(s) carry a database-resolved", message)
            doc["streams"][0]["stream_properties"]["phases"]["l"][
                "composition"].append({"component_name": "Water",
                                       "mol_fraction": 0.0})
            score, _m, missing = run(C._score_thm_02, doc)
            self.assertEqual(score, 0.5)
            self.assertIn("chemicals['Water'] (undeclared", missing[0])
            doc = valid_doc()
            remove(doc, "chemicals")
            self.assertEqual(run(C._score_thm_02, doc)[0], 0.0)
            self.assertIsNone(run(C._score_thm_02, {})[0])

    def test_thm_03_molar_mass(self):
        """_score_thm_03 (CMP-THM-03): declared -> 1; formula-derived only ->
        1; neither, with the id resolving by name to nothing -> 0 with a
        molar_mass locator; neither, with 'Ethanol' resolving by name to a
        database record nothing stated verifies -> 0.5 (R6.8); an
        undeclared referenced id -> 0 for it."""
        doc = valid_doc()
        self.assertEqual(run(C._score_thm_03, doc)[0], 1.0)
        remove(doc, "chemicals/0/molar_mass")
        self.assertEqual(run(C._score_thm_03, doc)[0], 1.0)
        remove(doc, "chemicals/0/formula")
        with name_lookup(set()):
            score, _m, missing = run(C._score_thm_03, doc)
        self.assertEqual(score, 0.0)
        self.assertIn("chemicals[Ethanol].molar_mass", missing[0])
        with name_lookup({"Ethanol"}):
            score, _m, missing = run(C._score_thm_03, doc)
        self.assertEqual(score, C.NAME_UNVERIFIED_CREDIT)
        self.assertIn("unverified", missing[0])
        doc = valid_doc()
        doc["streams"][0]["stream_properties"]["phases"]["l"]["composition"].append(
            {"component_name": "Water", "mol_fraction": 0.0})
        self.assertEqual(run(C._score_thm_03, doc)[0], 0.5)

    def test_thm_04_phase_behaviour(self):
        """_score_thm_04 / _chemical_phases / _thm_04_credit (CMP-THM-04,
        R4.5), each chemical the MAX over its tiers: valid_doc's Ethanol
        (included_in_thermo false, seen only in 'l': the flag names its
        locked phase) -> 1; flag deleted -> 0 (one phase proves nothing);
        also seen in 'g' -> the false flag is contradicted (ignored, noted)
        and the stream evidence gives DERIVED_CREDIT, while a true flag
        there -> 1; only ever 's' -> false flag 1, a true flag is
        contradicted -> DERIVED_CREDIT; a non-boolean flag counts as absent;
        a false flag on a chemical no stream shows -> FLAG_UNOBSERVED_CREDIT
        (0.5); no chemicals but streams -> 0."""
        with name_lookup():
            doc = valid_doc()
            self.assertEqual(C._chemical_phases(ctx(doc)), {"Ethanol": {"l"}})
            self.assertEqual(run(C._score_thm_04, doc)[0], 1.0)
            unflagged = copy.deepcopy(doc)
            remove(unflagged, "chemicals/0/included_in_thermo")
            self.assertEqual(run(C._score_thm_04, unflagged)[0], 0.0)
            mutate(unflagged, "chemicals/0/included_in_thermo", "yes")
            self.assertEqual(run(C._score_thm_04, unflagged)[0], 0.0)
            doc["streams"][1]["stream_properties"]["phases"]["g"] = {
                "total_molar_flow": 0.5,
                "composition": [{"component_name": "Ethanol",
                                 "mol_fraction": 1.0}]}
            score, _m, missing = run(C._score_thm_04, doc)
            self.assertEqual(score, C.DERIVED_CREDIT)
            self.assertIn("included_in_thermo false contradicts", missing[0])
            mutate(doc, "chemicals/0/included_in_thermo", True)
            self.assertEqual(run(C._score_thm_04, doc)[0], 1.0)
            solid = valid_doc()
            for s in solid["streams"]:
                s["stream_properties"]["phases"] = {
                    "s": s["stream_properties"]["phases"]["l"]}
            self.assertEqual(run(C._score_thm_04, solid)[0], 1.0)
            mutate(solid, "chemicals/0/included_in_thermo", True)
            self.assertEqual(run(C._score_thm_04, solid)[0], C.DERIVED_CREDIT)
            unseen = valid_doc()
            unseen["chemicals"].append({"id": "Glycerol", "molar_mass": 92.09,
                                        "included_in_thermo": False})
            score, _m, missing = run(C._score_thm_04, unseen)
            self.assertEqual(score, (1.0 + C.FLAG_UNOBSERVED_CREDIT) / 2)
            self.assertIn("no stream shows the phase", missing[0])
            remove(solid, "chemicals")
            self.assertEqual(run(C._score_thm_04, solid)[0], 0.0)
        self.assertEqual(C.FLAG_UNOBSERVED_CREDIT, 0.5)

    def test_thm_05_non_database_property_data(self):
        """_score_thm_05 (CMP-THM-05, R3.2): a chemical the database
        resolves neither by registry id nor by name -> 0; name-resolvable
        or with a database-resolved registry id -> None; a well-formed but
        unresolvable registry id (invented CAS 98765-43-6) exempts nothing
        -> 0; database unavailable -> 0 even with a registry id, the message
        saying so; an undeclared referenced id -> 0; no chemicals at all but
        streams -> 0; empty doc -> None."""
        with name_lookup():
            self.assertEqual(run(C._score_thm_05, valid_doc())[0], 0.0)
        with name_lookup({"Ethanol"}):
            self.assertIsNone(run(C._score_thm_05, valid_doc())[0])
            doc = valid_doc()
            doc["streams"][0]["stream_properties"]["phases"]["l"][
                "composition"].append({"component_name": "Water",
                                       "mol_fraction": 0.0})
            score, _m, missing = run(C._score_thm_05, doc)
            self.assertEqual((score, len(missing)), (0.0, 1))
            self.assertIn("chemicals['Water']", missing[0])
        with name_lookup({"64-17-5"}):
            doc = valid_doc()
            mutate(doc, "chemicals/0/registry_id", "64-17-5")
            self.assertIsNone(run(C._score_thm_05, doc)[0])
            invented = valid_doc()
            mutate(invented, "chemicals/0/registry_id", "98765-43-6")
            self.assertEqual(run(C._score_thm_05, invented)[0], 0.0)
            remove(doc, "chemicals")
            self.assertEqual(run(C._score_thm_05, doc)[0], 0.0)
            self.assertIsNone(run(C._score_thm_05, {})[0])
        with name_lookup(search=None):
            doc = valid_doc()
            mutate(doc, "chemicals/0/registry_id", "64-17-5")
            score, message, _missing = run(C._score_thm_05, doc)
            self.assertEqual(score, 0.0)
            self.assertIn("database is unavailable", message)

    def test_thm_06_property_package(self):
        """_score_thm_06 (CMP-THM-06): no package -> 0 with 4 locators; half
        stated -> 0.5; full -> 1; descriptors that name no thermosteam model
        class of their role ('x', 'NRTL', a fugacity class as gamma) count
        as absent -> 0; no units -> None."""
        score, _m, missing = run(C._score_thm_06, valid_doc())
        self.assertEqual((score, len(missing)), (0.0, 4))
        doc = valid_doc()
        package = doc["units"][0]["thermo_property_package"] = {
            k: FULL_PKG[k] for k in ("mixture", "gamma")}
        self.assertEqual(run(C._score_thm_06, doc)[0], 0.5)
        package.update(phi=FULL_PKG["phi"], PCF=FULL_PKG["PCF"])
        self.assertEqual(run(C._score_thm_06, doc)[0], 1.0)
        package.update(mixture="x", gamma="NRTL", phi="x",
                       PCF="IdealFugacityCoefficients")
        self.assertEqual(run(C._score_thm_06, doc)[0], 0.0)
        self.assertIsNone(run(C._score_thm_06, {})[0])

    def test_valid_package_descriptor(self):
        """_valid_package_descriptor: the leading identifier must be a
        thermosteam model class of the key's role (_THERMO_MODEL_CLASSES):
        'IdealMixture(...)' as mixture, 'PRActivityCoefficients' as gamma,
        '<PRFugacityCoefficients([...])>' as phi, both Poynting spellings as
        PCF -> True; 'x', 'NRTL', a gamma class as phi, a non-string or an
        unknown key -> False."""
        for key, value in (("mixture", FULL_PKG["mixture"]),
                           ("gamma", "PRActivityCoefficients"),
                           ("phi", "<PRFugacityCoefficients([Water])>"),
                           ("PCF", "MockPoyntingCorrectionFactors"),
                           ("PCF", "MockPoyintingCorrectionFactors")):
            self.assertTrue(C._valid_package_descriptor(key, value), value)
        for key, value in (("mixture", "x"), ("gamma", "NRTL"),
                           ("phi", "DortmundActivityCoefficients"),
                           ("gamma", ["DortmundActivityCoefficients"]),
                           ("id", "IdealMixture")):
            self.assertFalse(C._valid_package_descriptor(key, value), value)

    def test_thm_06_unweighted(self):
        """_score_thm_06 (CMP-THM-06) is unweighted (R4.12): a reactor with
        a full package beside a pump with none -> 1/2."""
        full = dict(FULL_PKG)
        doc = valid_doc()
        doc["units"] = [dict(spec_unit("SinglePhaseReactor"), id="R1",
                             thermo_property_package=full),
                        dict(spec_unit("Pump"), id="P1")]
        self.assertEqual(run(C._score_thm_06, doc)[0], 0.5)

    def test_single_full_package(self):
        """_single_full_package: every package full (mixture, gamma, phi,
        PCF) and identical -> True; a differing descriptor, a blank or
        missing one, or no packages -> False; ids are ignored."""
        full = dict(FULL_PKG)
        self.assertTrue(C._single_full_package([full, dict(full, id="a")]))
        self.assertFalse(C._single_full_package([full, dict(UNIFAC_PKG)]))
        self.assertFalse(C._single_full_package([full, dict(full, PCF=" ")]))
        partial = dict(full)
        del partial["phi"]
        self.assertFalse(C._single_full_package([partial, partial]))
        self.assertFalse(C._single_full_package([]))

    def test_thm_07_chemical_set_identity(self):
        """_score_thm_07 (CMP-THM-07), applicable whenever units exist: no
        units -> None; two pumps sharing one full package without ids ->
        DERIVED_CREDIT each (single thermo implied, 2 locators); P1 naming
        its id -> (1 + 0.75) / 2; a descriptor deleted from P2 (single-thermo
        evidence gone) -> (1 + 0) / 2 with P2's id as the only locator; two
        distinct full packages, neither with an id -> 0 (not N/A); a unit
        without any package -> 0."""
        self.assertIsNone(run(C._score_thm_07, {})[0])
        full = dict(FULL_PKG)
        doc = valid_doc()
        doc["units"] = [dict(spec_unit("Pump"), id=uid,
                             thermo_property_package=dict(full))
                        for uid in ("P1", "P2")]
        score, message, missing = run(C._score_thm_07, doc)
        self.assertEqual((score, len(missing)), (C.DERIVED_CREDIT, 2))
        self.assertIn("single thermo implied", message)
        doc["units"][0]["thermo_property_package"]["id"] = "set1"
        self.assertEqual(run(C._score_thm_07, doc)[0], 0.875)
        del doc["units"][1]["thermo_property_package"]["phi"]
        score, _m, missing = run(C._score_thm_07, doc)
        self.assertEqual((score, missing),
                         (0.5, ["units[P2].thermo_property_package.id"]))
        doc["units"][0]["thermo_property_package"] = dict(full)
        doc["units"][1]["thermo_property_package"] = dict(UNIFAC_PKG)
        self.assertEqual(run(C._score_thm_07, doc)[0], 0.0)
        self.assertEqual(run(C._score_thm_07, valid_doc())[0], 0.0)


# ----------------------------------------------- topology + feed criteria

class TestTopologyFeedCriteria(unittest.TestCase):
    def test_top_01_connectivity(self):
        """_score_top_01 (CMP-TOP-01): valid -> 1; one dangling endpoint of 4
        -> (0.75 + 1) / 2; units but no streams -> 0; empty -> 0."""
        self.assertEqual(run(C._score_top_01, valid_doc())[0], 1.0)
        doc = valid_doc()
        mutate(doc, "streams/1/sink_unit_id", "GHOST")
        score, _m, missing = run(C._score_top_01, doc)
        self.assertAlmostEqual(score, 0.875)
        self.assertIn("'GHOST' is not a unit id", missing[0])
        self.assertEqual(run(C._score_top_01, {"units": [{"id": "U"}]})[0], 0.0)
        self.assertEqual(run(C._score_top_01, {})[0], 0.0)

    def test_top_02_port_order(self):
        """_score_top_02 (CMP-TOP-02): mixer sides -> 1 even with a second
        feed (mixer ins are order-free) while its balance closes; the same
        second feed unbalanced (the product not carrying it) -> the outs'
        single-port convention rests on the stream count, so it needs
        closure (R2.2): (1 + 0) / 2 with a 'mass balance does not close'
        locator; a reactor's single-stream sides -> DERIVED_CREDIT
        (unexported ports may exist); a pump with an empty second inlet ->
        1 (one material-carrying port); R4.8: a 'Pump' with 2
        material-carrying inlets is contradicted by its ports, so its family
        is unknown: ins 0 (order of 2 streams), outs DERIVED_CREDIT -> 0.375;
        no units -> None. R4.14: valid_doc's one-inlet 'Mixer' label is no
        plausible mixer claim, so its family is unknown and each
        single-stream side earns DERIVED_CREDIT only."""
        self.assertEqual(run(C._score_top_02, valid_doc())[0], C.DERIVED_CREDIT)
        doc = one_unit_doc(cls("Mixer"), ins=({}, {}))
        self.assertEqual(run(C._score_top_02, doc), (
            1.0, "not representable; 2 of 2 unit port side(s) unambiguous by "
                 "convention", []))
        doc = valid_doc()
        doc["streams"].append(dict(copy.deepcopy(doc["streams"][0]), id="feed2"))
        score, _m, missing = run(C._score_top_02, doc)
        self.assertEqual(score, 0.5)
        self.assertEqual(missing, [
            "units[U1].outs: port index not representable (no port convention "
            "applies: mass balance does not close (in 92.14, out 46.07))"])
        score, _m, missing = run(C._score_top_02, one_unit_doc(
            spec_unit("SinglePhaseReactor")))
        self.assertEqual(score, C.DERIVED_CREDIT)
        self.assertIn("port index not representable", missing[0])
        doc = one_unit_doc(spec_unit("Pump"), ins=({}, {}))
        self.assertEqual(run(C._score_top_02, doc)[0], 0.375)
        self.assertEqual(run(C._score_top_02, doc)[2][0],
                         "units[U1].ins: order of 2 streams not representable")
        doc = one_unit_doc(spec_unit("Pump"), ins=({}, {}),
                           outs=({"mass": 46.07},))
        doc["streams"][1]["stream_properties"].update(
            total_mass_flow=0.0, total_molar_flow=0.0, phases={})
        self.assertEqual(run(C._score_top_02, doc), (
            1.0, "not representable; 2 of 2 unit port side(s) unambiguous by "
                 "convention", []))
        self.assertIsNone(run(C._score_top_02, {})[0])

    def test_top_02_side_rules(self):
        """_side_streams / _port_order_derivable / _side_credit: flash outs
        with one vapor + one liquid outlet -> DERIVED_CREDIT, two liquids ->
        0; splitter outs need an explicit split + full ports; distillation
        outs need LHK + outlet compositions; HXprocess 2x2 with compositions
        -> DERIVED_CREDIT on both sides; single-port pump side -> 1."""
        vl = ({"phases": VAP}, {})
        sc = ctx(one_unit_doc(spec_unit("Flash"), outs=vl))
        self.assertEqual(C._side_streams(sc, 0, "outs"), [1, 2])
        self.assertTrue(C._port_order_derivable(sc, 0, "outs"))
        self.assertEqual(C._side_credit(sc, 0, "outs")[0], C.DERIVED_CREDIT)
        self.assertEqual(C._side_credit(sc, 0, "ins"), (1.0, None))
        sc = ctx(one_unit_doc(spec_unit("Flash"), outs=({}, {})))
        self.assertEqual(C._side_credit(sc, 0, "outs")[0], 0.0)
        sc = ctx(one_unit_doc(spec_unit("Splitter", {"split": [0.5]}),
                              outs=({}, {})))
        self.assertTrue(C._port_order_derivable(sc, 0, "outs"))
        sc = ctx(one_unit_doc(spec_unit("Splitter"), outs=({}, {})))
        self.assertFalse(C._port_order_derivable(sc, 0, "outs"))
        sc = ctx(one_unit_doc(spec_unit("BinaryDistillation", {"LHK": ["a", "b"]}),
                              outs=vl))
        self.assertTrue(C._port_order_derivable(sc, 0, "outs"))
        sc = ctx(one_unit_doc(spec_unit("HXprocess"), ins=({}, {}), outs=({}, {})))
        self.assertTrue(C._port_order_derivable(sc, 0, "ins"))
        self.assertEqual(C._side_credit(sc, 0, "outs")[0], C.DERIVED_CREDIT)
        sc = ctx(one_unit_doc(spec_unit("Pump")))
        self.assertFalse(C._port_order_derivable(sc, 0, "outs"))
        self.assertEqual(C._side_credit(sc, 0, "outs"), (1.0, None))

    def test_state_missing(self):
        """_state_missing: mean state score and one locator per unstated
        part over the given streams."""
        doc = valid_doc()
        remove(doc, "streams/0/stream_properties/pressure")
        score, missing = C._state_missing(ctx(doc), [0, 1])
        self.assertAlmostEqual(score, (0.75 + 1) / 2)
        self.assertEqual(missing, ["streams[feed].stream_properties.pressure"])

    def test_feed_01_state(self):
        """_score_feed_01 (CMP-FEED-01): full feed -> 1; no temperature ->
        0.75 with its locator; no feed at all -> 0."""
        self.assertEqual(run(C._score_feed_01, valid_doc())[0], 1.0)
        doc = valid_doc()
        remove(doc, "streams/0/stream_properties/temperature")
        score, _m, missing = run(C._score_feed_01, doc)
        self.assertEqual(score, 0.75)
        self.assertEqual(missing, ["streams[feed].stream_properties.temperature"])
        doc = valid_doc()
        mutate(doc, "streams/0/source_unit_id", "U1")
        self.assertEqual(run(C._score_feed_01, doc)[0], 0.0)

    def test_states_flow(self):
        """_states_flow: a non-zero total mass (else molar) flow -> True; a
        zero flow, no flow or no stream_properties -> False."""
        self.assertTrue(C._states_flow(stream("s", "None", "U1")))
        self.assertFalse(C._states_flow(stream("s", "None", "U1", mass=0.0)))
        self.assertFalse(C._states_flow({"stream_properties": {}}))
        self.assertFalse(C._states_flow({}))

    def test_feed_01_equal_weights_and_empty_feeds(self):
        """_score_feed_01 (CMP-FEED-01): feeds weigh equally whatever their
        flow (90 and 10 kg/h, the small one missing T -> 0.875); deleting the
        big feed's mass flow keeps the molar flow, so the score stays 0.875
        (the old mass-share weighting rose here); an EMPTY feed (total flow
        0) into an exactly known HXprocess (two fixed inlets, one free; its
        mass balance closes) stays in the population and scores 1 even
        without T -> 1.0, the message counting '1 empty feed(s) ... 1
        credited as placeholder port(s)' (R6.3); beside a half-stated
        feed it counts as 1 -> (0.5 + 1) / 2; deleting its flows makes it an
        unknown-flow feed scored by its state (lower, never higher). R2.12:
        with no feed stating a non-zero flow a stated zero counts as a
        missing flow with no composition exemption, so the lone zero feed
        (T missing, P and a liquid phase stated) scores 0.5 -- exactly what
        it scores with its flows unstated -- and deleting the only non-zero
        flow lowers the doc from 1.0 to (0.75 + 0.5) / 2."""
        doc = one_unit_doc(spec_unit("Mixer"), ins=({"mass": 90.0},
                                                    {"mass": 10.0, "T": None}))
        self.assertAlmostEqual(run(C._score_feed_01, doc)[0], 0.875)
        del doc["streams"][0]["stream_properties"]["total_mass_flow"]
        self.assertAlmostEqual(run(C._score_feed_01, doc)[0], 0.875)
        empty = one_unit_doc(spec_unit("HXprocess"),
                             ins=({}, {"mass": 0.0, "T": None}),
                             outs=({"mass": 46.07},))
        empty["streams"][1]["stream_properties"]["total_molar_flow"] = 0.0
        score, message, _missing = run(C._score_feed_01, empty)
        self.assertEqual(score, 1.0)
        self.assertIn("1 empty feed(s) (zero flow), 1 credited as placeholder "
                      "port(s)", message)
        half = copy.deepcopy(empty)
        for key in ("temperature", "pressure"):
            del half["streams"][0]["stream_properties"][key]
        self.assertEqual(run(C._score_feed_01, half)[0], 0.75)
        unknown = copy.deepcopy(half)
        for key in ("total_mass_flow", "total_molar_flow"):
            del unknown["streams"][1]["stream_properties"][key]
        self.assertLess(run(C._score_feed_01, unknown)[0], 0.75)
        unanchored = copy.deepcopy(empty)
        for key in ("total_mass_flow", "total_molar_flow"):
            del unanchored["streams"][0]["stream_properties"][key]
        self.assertEqual(run(C._score_feed_01, unanchored)[0], 0.625)
        del empty["streams"][0]
        score, message, missing = run(C._score_feed_01, empty)
        self.assertEqual(score, 0.5)
        self.assertIn("no feed states a non-zero flow", message)
        self.assertIn("(a zero flow, while no feed states a non-zero one)",
                      " ".join(missing))
        for key in ("total_mass_flow", "total_molar_flow"):
            del empty["streams"][0]["stream_properties"][key]
        self.assertEqual(run(C._score_feed_01, empty)[0], 0.5)

    def test_utility_port_feed(self):
        """_utility_port_feed / _port_materials / _mole_fractions (R2.9): a
        natural-gas feed (>= 50 mol% CH4) into a DrumDryer -> (the dryer's
        label, 'natural gas'); an ethanol or air feed into it -> None (scored
        normally); a feed of unknown composition -> may be the port's
        material (label, 'natural gas?'); lime into a BoilerTurbogenerator
        or a plain Boiler -> 'lime'; water into a ProcessWaterCenter (or a
        facility of unknown kind) -> 'makeup water', but not into a
        CoolingTower; a feed into a Pump -> None. Each feed passes through
        to the product unchanged, so no species converts across the unit
        (R6.1: a conversion its class cannot explain would contradict the
        class claim)."""
        ch4 = {"g": {"total_molar_flow": 1.0, "composition": [
            {"component_name": "CH4", "mol_fraction": 0.9},
            {"component_name": "CO2", "mol_fraction": 0.1}]}}
        air = {"g": {"total_molar_flow": 1.0, "composition": [
            {"component_name": "O2", "mol_fraction": 0.21},
            {"component_name": "N2", "mol_fraction": 0.79}]}}
        def port(name, phases=None, url=BST_URL, unit=None):
            side = ({"phases": phases} if phases else {},)
            doc = one_unit_doc(unit or spec_unit(name, url=url), ins=side,
                               outs=side)
            return C._utility_port_feed(ctx(doc), 0)
        self.assertEqual(port("DrumDryer", ch4), ("units[U1]", "natural gas"))
        self.assertIsNone(port("DrumDryer"))
        self.assertIsNone(port("DrumDryer", air))
        doc = one_unit_doc(spec_unit("DrumDryer"))
        del doc["streams"][0]["stream_properties"]["phases"]
        self.assertEqual(C._utility_port_feed(ctx(doc), 0),
                         ("units[U1]", "natural gas?"))
        self.assertIsNone(C._mole_fractions(doc["streams"][0]))
        lime = {"s": {"total_molar_flow": 1.0, "composition": [
            {"component_name": "CaO", "mol_fraction": 1.0}]}}
        self.assertEqual(port("BoilerTurbogenerator", lime)[1], "lime")
        self.assertEqual(port("Boiler", lime)[1], "lime")
        water = {"l": {"total_molar_flow": 1.0, "composition": [
            {"component_name": "Water", "mol_fraction": 1.0}]}}
        self.assertEqual(port("ProcessWaterCenter", water)[1], "makeup water")
        self.assertEqual(port(None, water, unit={"unit_type": "Utility facility"})[1],
                         "makeup water")
        self.assertIsNone(port("CoolingTower", water))
        self.assertIsNone(port("Pump"))
        sc = ctx(one_unit_doc(spec_unit("BoilerTurbogenerator")))
        self.assertEqual(C._port_materials(sc, 0),
                         ("natural gas", "lime", "boiler chemicals"))
        self.assertEqual(C._mole_fractions(stream("s", "None", "U1", phases=air)),
                         {"o2": 0.21, "n2": 0.79})

    def test_feed_02_prices(self):
        """_score_feed_02 / _is_empty_stream (CMP-FEED-02): unpriced boundary
        streams -> 0; both priced (0 counts) -> 1; a 0 price on a natural-gas
        feed into a utility port (DrumDryer) -> 0.5 for it (0.75 overall),
        while a 0 price on a product of that dryer stays 1, and so does a 0
        price on an ethanol feed into it (R2.9: not its port material). R6.3:
        an EMPTY (zero-flow) feed into valid_doc's label-only 'Mixer' earns
        no placeholder credit, so it scores on its (absent) price -> 0, its
        locator saying the class is not known exactly; into a free fixed
        inlet of an exactly known HXprocess (mass balance closing) it scores
        1 without a price (beside two unpriced streams -> 1/3, the message
        counting '1 empty placeholder port(s)'); deleting its
        stream_properties makes it an unpriced stream of unknown flow -> 0
        (lower, never higher); no boundary streams -> None."""
        doc = valid_doc()
        self.assertEqual(run(C._score_feed_02, doc)[0], 0.0)
        mutate(doc, "streams/0/price", 0.5)
        mutate(doc, "streams/1/price", 0)
        self.assertEqual(run(C._score_feed_02, doc)[0], 1.0)
        dryer = copy.deepcopy(doc)
        dryer["units"][0] = dict(spec_unit("DrumDryer"), id="U1")
        mutate(dryer, "streams/0/price", 0)
        self.assertEqual(run(C._score_feed_02, dryer)[0], 1.0)
        # The methane passes through (R6.1: methane burnt to ethanol would
        # contradict the DrumDryer's built-in chemistry).
        for s in (0, 1):
            mutate(dryer, f"streams/{s}/stream_properties/phases/l/"
                          f"composition/0/component_name", "Methane")
        score, _m, missing = run(C._score_feed_02, dryer)
        self.assertEqual(score, 0.75)
        self.assertIn("streams[feed].price: 0 on the natural gas port of "
                      "units[U1]", missing[0])
        empty = valid_doc()
        empty["streams"][0]["stream_properties"]["total_mass_flow"] = 0.0
        self.assertTrue(C._is_empty_stream(empty["streams"][0]))
        score, message, missing = run(C._score_feed_02, empty)
        self.assertEqual(score, 0.0)
        self.assertIn("streams[feed].price (a zero flow at units[U1], whose "
                      "class is not known exactly", missing[0])
        empty = one_unit_doc(spec_unit("HXprocess"),
                             ins=({}, {"mass": 0.0}), outs=({"mass": 46.07},))
        empty["streams"][1]["stream_properties"]["total_molar_flow"] = 0.0
        score, message, missing = run(C._score_feed_02, empty)
        self.assertAlmostEqual(score, 1 / 3)
        self.assertEqual(missing, ["streams[in0].price", "streams[out0].price"])
        self.assertIn("1 empty placeholder port(s), needing no price", message)
        del empty["streams"][1]["stream_properties"]
        self.assertEqual(run(C._score_feed_02, empty)[0], 0.0)
        mutate(doc, "streams/0/source_unit_id", "U1")
        mutate(doc, "streams/1/sink_unit_id", "U1")
        self.assertIsNone(run(C._score_feed_02, doc)[0])


# -------------------------------------------------------- unit-op criteria

class TestUnitOpCriteria(unittest.TestCase):
    def test_uop_01_class_identity(self):
        """_score_uop_01 (CMP-UOP-01): a core class named by
        design_simulation_method -> 1; R4.3: the same class name as bare
        unit_type ('Mixer', two inlets) -> 0.5 (keyword family only), with
        one inlet -> 0 (R4.14: no plausible mixer claim); free-text
        unit_type -> 0.5; unknown -> 0; no units -> None; R2.6 abstract
        classes: unit_type 'Compressor' -> 0.5 (family only), unit_type
        'Unit' and 'Unit on <biosteam url>' -> 0, 'Tank on <biosteam url>'
        -> 0.5."""
        self.assertEqual(run(C._score_uop_01, exact_mixer_doc())[0], 1.0)
        self.assertEqual(run(C._score_uop_01, label_mixer_doc())[0], 0.5)
        self.assertEqual(run(C._score_uop_01, valid_doc())[0], 0.0)
        doc = unit_doc({"unit_type": "Heat exchanger"})
        score, _m, missing = run(C._score_uop_01, doc)
        self.assertEqual(score, 0.5)
        self.assertIn("family 'hx_utility' from unit_type", missing[0])
        self.assertEqual(run(C._score_uop_01, unit_doc({"unit_type": "Widget"}))[0], 0.0)
        self.assertIsNone(run(C._score_uop_01, {})[0])
        # R2.6: abstract classes never reach confidence 1.
        for fields, expected in (({"unit_type": "Compressor"}, 0.5),
                                 ({"unit_type": "Unit"}, 0.0),
                                 (dict(spec_unit("Unit"),
                                       unit_type="Background unit"), 0.0),
                                 (dict(spec_unit("Tank"), unit_type="Tank"),
                                  0.5)):
            self.assertEqual(run(C._score_uop_01, unit_doc(fields))[0],
                             expected, fields)

    def test_class_note(self):
        """_class_note: confidence 1 -> None; a custom class named by
        design_simulation_method not located in the pinned repository
        (R3.6) -> 'custom class ... is not in the repository ... pins';
        R4.3: a class name in unit_type ('JetCooker') is a keyword family
        only -> "family 'steam_injection_heater' from unit_type keywords"; a
        family confirmed by the spec keys (R3.1) -> "family 'pump' confirmed
        by every design-spec registry class ...; tightest 'Pump'"; a keyword
        family -> "family 'hx_utility' from unit_type"; nothing -> 'class
        unresolved'; an abstract class -> 'is abstract' with its family (or
        none); an unknown class on a biosteam URL -> '... claims biosteam';
        R4.8: a Pump with two material-carrying inlets -> "class 'Pump' is
        contradicted by its ports ... class 'Pump' (2 non-empty inlet(s), at
        most 1 allowed)" and the unit_type 'Pump' family likewise."""
        def note(unit, ins=({},)):
            return C._class_note(ctx(one_unit_doc(unit, ins=ins)), 0)
        self.assertIsNone(note(spec_unit("Pump")))
        self.assertIn("custom class 'GrainHandling' is not core BioSTEAM "
                      "and its source URL is not in the repository",
                      note(spec_unit("GrainHandling", url=BIP_URL)))
        self.assertIn("family 'steam_injection_heater' from unit_type keywords",
                      note({"unit_type": "JetCooker"}))
        contradicted = note(dict(spec_unit("Pump"), unit_type="Pump"),
                            ins=({}, {}))
        self.assertIn("class 'Pump' is contradicted by its ports (no family "
                      "either); ignored as contradicted by its ports (R4.8): "
                      "class 'Pump' (2 non-empty inlet(s), at most 1 "
                      "allowed); unit_type family 'pump' (2 non-empty "
                      "inlet(s), at most 1 allowed)", contradicted)
        self.assertIn("family 'pump' confirmed by every design-spec registry "
                      "class holding its design_input_specs keys; tightest "
                      "'Pump'",
                      note({"design_input_specs": {"dP_design": 1.0},
                            "unit_type": "?"}))
        self.assertIn("family 'hx_utility' from unit_type",
                      note({"unit_type": "Heat exchanger"}))
        self.assertIn("class unresolved (unit_type 'Widget'",
                      note({"unit_type": "Widget"}))
        self.assertIn("class 'Unit' is abstract (no family either)",
                      note(spec_unit("Unit")))
        self.assertIn("class 'Tank' is abstract (family 'tank' only)",
                      note(dict(spec_unit("Tank"), unit_type="Storage tank")))
        self.assertIn("claims biosteam", note(spec_unit("Frobnicator")))

    def test_uop_02_essential_specs(self):
        """_score_uop_02 (CMP-UOP-02): mixer only -> None; HXutility with a T
        matching its outlet -> 1; the same T against a 300 K outlet -> 0.5
        (T|H|V 0 with an 'inconsistent' locator, the consistency guard, which
        -- R6.6 -- suggests a quantity_units_global alias, since T states no
        quantity unit; the phase-change V requirement waived on liquid in /
        liquid out and so met, R5.9); HXutility with only an outlet T -> (DERIVED_CREDIT + 1)
        / 2; unknown family (R4.14: valid_doc's one-inlet 'Mixer' label) ->
        0."""
        self.assertIsNone(run(C._score_uop_02, label_mixer_doc())[0])
        self.assertEqual(run(C._score_uop_02, valid_doc())[0], 0.0)
        self.assertEqual(run(C._score_uop_02, unit_doc(
            cls("HXutility"), {"T": 350.0}, out_T=350.0))[0], 1.0)
        score, _m, missing = run(C._score_uop_02,
                                 unit_doc(cls("HXutility"), {"T": 350.0}))
        self.assertEqual(score, 0.5)
        self.assertIn("(inconsistent: T disagrees with the outlet stream; T "
                      "states no quantity unit", missing[0])
        self.assertIn("declare a quantity_units_global alias for it",
                      missing[0])
        score, _m, missing = run(C._score_uop_02, unit_doc(cls("HXutility"), {}))
        self.assertEqual(score, (C.DERIVED_CREDIT + 1.0) / 2)
        self.assertIn("one of T|H|V (only derivable", missing[0])
        self.assertEqual(run(C._score_uop_02, unit_doc({"unit_type": "Widget"}))[0], 0.0)

    def test_declared_registry_class(self):
        """_declared_registry_class: a listed class -> itself; a curated
        table class -> its registry_class (CornStorage -> StorageTank); an
        unlisted class, None or '' -> None (never key-set inference)."""
        registry = C._design_spec_registry()[0]
        self.assertEqual(C._declared_registry_class("HXutility", registry),
                         "HXutility")
        self.assertEqual(C._declared_registry_class("CornStorage", registry),
                         "StorageTank")
        for name in ("NoSuchClass", None, ""):
            self.assertIsNone(C._declared_registry_class(name, registry), name)

    def test_uop_03_full_coverage(self):
        """_score_uop_03 (CMP-UOP-03): a core HXutility (confidence 1) against
        registry HXutility {T, V, H, U} with T set -> V and H excused, U
        missing -> 0.5; a class known exactly but unlisted (Mixer here) ->
        left out, so None when it is the only unit; the same unit with its
        class NOT known exactly -> 0 with a 'parameter universe unknown'
        locator, whether the class is inferred from the spec keys (the
        tightest-superset entry is never used), only the family is known, or
        nothing is; a registry load error -> RuntimeError (driver turns it
        into score 0)."""
        registry = {"HXutility": {"line": "HX", "design_input_spec_params": {
            p: {"accessors": [p]} for p in ("T", "V", "H", "U")}}}
        doc = unit_doc(cls("HXutility"), {"T": 350.0})
        score, _m, missing = run(C._score_uop_03, doc, registry=registry)
        self.assertEqual(score, 0.5)
        self.assertEqual(missing, ["units[U1].design_input_specs.U"])
        self.assertIsNone(run(C._score_uop_03, unit_doc(cls("Mixer")),
                              registry=registry)[0])
        for unit_type, specs in (("Heat exchanger", {"T": 350.0, "U": 1.0}),
                                 ("Heat exchanger", {}), ("Gizmo", {})):
            doc = unit_doc({"unit_type": unit_type}, specs)
            confidence = ctx(doc, registry=registry).unit_classes[0][2]
            self.assertLess(confidence, 1.0)
            score, _m, missing = run(C._score_uop_03, doc, registry=registry)
            self.assertEqual(score, 0.0, (unit_type, specs))
            self.assertIn("parameter universe is unknown", missing[0])
        sc = ctx(unit_doc(cls("HXutility")), registry=registry)
        sc.registry_error = "boom"
        with self.assertRaises(RuntimeError):
            C._score_uop_03(sc)

    def test_uop_04_computed_states(self):
        """_score_uop_04 (CMP-UOP-04): full product state -> 1; product
        without pressure -> 0.75; feeds only -> None; R2.4 consistency
        guard: CO2 appearing across the (physical-only) Mixer without
        reactions -> its product's composition does not count (0.75, with
        an 'inconsistent at units[U1]' note); the same with a declared
        reaction -> 1."""
        self.assertEqual(run(C._score_uop_04, valid_doc())[0], 1.0)
        self.assertEqual(run(C._score_uop_04, unit_doc(out_P=None))[0], 0.75)
        doc = unit_doc()
        doc["streams"][1]["stream_properties"]["phases"]["l"]["composition"] = [
            {"component_name": "Ethanol", "mol_fraction": 0.5},
            {"component_name": "CO2", "mol_fraction": 0.5}]
        score, message, missing = run(C._score_uop_04, doc)
        self.assertEqual(score, 0.75)
        self.assertIn("inconsistent at units[U1]: CO2 appears", missing[0])
        self.assertIn("species-consistency guard", message)
        doc["units"][0]["reactions"] = [reaction(equation="Ethanol -> CO2")]
        self.assertEqual(run(C._score_uop_04, doc)[0], 1.0)
        doc = valid_doc()
        del doc["streams"][1]
        self.assertIsNone(run(C._score_uop_04, doc)[0])

    def test_uop_05_spec_units(self):
        """_score_uop_05 (CMP-UOP-05, R5.2): mixer only -> None; a core
        class at confidence 1 whose need is unmet (HXutility, no specs) ->
        DERIVED_CREDIT with a 'one of T|H|V unstated (class conventions
        imply it)' locator; T stated without a quantity unit -> still
        DERIVED_CREDIT ('T stated without a recognised quantity unit'); T
        aliased in quantity_units_global (K) -> 1 with no locator; an
        unpinned custom class (JetCooker) -> 0, pinned -> DERIVED_CREDIT;
        the bare 'Unit' (abstract: no class, no family, needs unknown) -> 0
        without the convention note; unweighted (R4.12): reactor 0.75 + bare
        Unit 0 -> 0.375; the population also holds the CMP-UOP-06
        facilities (a ProcessWaterCenter -> DERIVED_CREDIT), but not
        cost-only ones (CIPpackage -> None)."""
        self.assertIsNone(run(C._score_uop_05, label_mixer_doc())[0])
        score, message, missing = run(C._score_uop_05,
                                      one_unit_doc(spec_unit("HXutility")))
        self.assertEqual(score, C.DERIVED_CREDIT)
        self.assertIn("0 of 1 unit(s) state every dimensional", message)
        self.assertEqual(missing, ["units[U1].design_input_specs: one of "
                                   "T|H|V unstated (class conventions imply "
                                   "it)"])
        doc = one_unit_doc(spec_unit("HXutility", {"T": 300.0}))
        score, _m, missing = run(C._score_uop_05, doc)
        self.assertEqual(score, C.DERIVED_CREDIT)
        self.assertEqual(missing, ["units[U1].design_input_specs: T stated "
                                   "without a recognised quantity unit "
                                   "(class conventions imply it)"])
        doc["quantity_units_global"]["temperature"]["aliases"].append("T")
        self.assertEqual(run(C._score_uop_05, doc), (
            1.0, "1 of 1 unit(s) state every dimensional specification they "
            "need with a recognised quantity unit (quantity_units_global "
            "aliases)", []))
        jet = spec_unit("JetCooker", url=BIP_URL)
        self.assertEqual(run(C._score_uop_05, one_unit_doc(jet))[0], 0.0)
        self.assertEqual(run(C._score_uop_05, one_unit_doc(jet, pinned=True))[0],
                         C.DERIVED_CREDIT)
        bare = one_unit_doc(spec_unit("Unit"))
        score, _m, missing = run(C._score_uop_05, bare)
        self.assertEqual(score, 0.0)
        self.assertNotIn("class conventions", missing[0])
        self.assertIn("its needs are unknown", missing[0])
        doc = one_unit_doc(spec_unit("SinglePhaseReactor"))
        doc["units"].append(dict(spec_unit("Unit"), id="U2"))
        self.assertAlmostEqual(run(C._score_uop_05, doc)[0], 0.375)
        self.assertEqual(run(C._score_uop_05, one_unit_doc(
            spec_unit("ProcessWaterCenter")))[0], C.DERIVED_CREDIT)
        self.assertIsNone(run(C._score_uop_05, one_unit_doc(
            spec_unit("CIPpackage")))[0])


# -------------------------------------------------------- reaction criteria

def reaction(**fields):
    r = {"index": 0, "equation": "Ethanol -> Ethanol", "reactant": "Ethanol",
         "conversion": 0.5}
    r.update(fields)
    return r


GLUCOSE_EQ = "Glucose -> 2 Ethanol + 2 CO2"


def rxn_doc(reactions):
    """unit_doc with U1 a SinglePhaseReactor (reactor family) carrying
    `reactions` (None: the key is absent), and Glucose / CO2 declared beside
    Ethanol."""
    fields = spec_unit("SinglePhaseReactor")
    if reactions is not None:
        fields["reactions"] = reactions
    doc = unit_doc(fields)
    doc["chemicals"] += [{"id": "Glucose", "formula": "C6H12O6",
                          "molar_mass": 180.156},
                         {"id": "CO2", "formula": "CO2", "molar_mass": 44.01}]
    return doc


class TestReactionCriteria(unittest.TestCase):
    def _appearing_doc(self, unit_fields=None):
        """valid_doc whose product carries CO2 the feed lacks; U1 is the
        exactly named core Mixer unless `unit_fields` say otherwise (R4.14:
        valid_doc's one-inlet 'Mixer' label alone has unknown family)."""
        doc = unit_doc(cls("Mixer") if unit_fields is None else unit_fields)
        doc["streams"][1]["stream_properties"]["phases"]["l"]["composition"] = [
            {"component_name": "Ethanol", "mol_fraction": 0.5},
            {"component_name": "CO2", "mol_fraction": 0.5}]
        return doc

    def test_species_change(self):
        """_species_change: CO2 in the outlet, absent from the inlet ->
        ['CO2 appears']; the valid mixer -> []; a port whose species flows
        are not computable (phases deleted) -> None (a change cannot be
        ruled out); no unit id -> []."""
        self.assertEqual(C._species_change(ctx(self._appearing_doc()), "U1"),
                         ["CO2 appears"])
        self.assertEqual(C._species_change(ctx(valid_doc()), "U1"), [])
        self.assertEqual(C._species_change(ctx(valid_doc()), None), [])
        doc = valid_doc()
        del doc["streams"][1]["stream_properties"]["phases"]
        del doc["streams"][1]["stream_properties"]["composition"]
        self.assertIsNone(C._species_change(ctx(doc), "U1"))

    def test_species_guard(self):
        """_species_guarded / _species_consistency (R2.4): a Mixer (physical
        family) without reactions is guarded; with a well-formed reaction,
        or as a reactor, it is not; a core Splitter and a splitter of
        unknown class are guarded, a custom splitter class (CleaningSystem)
        located in the pinned model package (confidence 1) is not. R6.1: CO2
        appearing contradicts the unpinned CleaningSystem (a curated
        biorefinery class without chemistry) and a ProcessWaterCenter
        (whose chemistry only discards species), so both units' family is
        unknown and they are guarded. Consistency: the valid mixer -> (True,
        ''); CO2 appearing -> (False, 'CO2 appears across ...'); an
        uncomputable port -> (False, 'species flows not computable ...');
        unguarded -> (True, ''); results are cached on the context."""
        sc = ctx(self._appearing_doc())
        self.assertTrue(C._species_guarded(sc, 0))
        ok, why = C._species_consistency(sc, 0)
        self.assertFalse(ok)
        self.assertIn("CO2 appears across a physical-only unit", why)
        self.assertIn(0, sc.consistency)
        self.assertEqual(C._species_consistency(ctx(valid_doc()), 0), (True, ''))
        reacting = ctx(self._appearing_doc({"reactions": [
            reaction(equation="Ethanol -> CO2")]}))
        self.assertFalse(C._species_guarded(reacting, 0))
        self.assertEqual(C._species_consistency(reacting, 0), (True, ''))
        for fields, guarded, pinned in (
                (spec_unit("Splitter"), True, False),
                ({"unit_type": "Splitter unit"}, True, False),
                (spec_unit("CleaningSystem", url=BIP_URL), False, True),
                (spec_unit("CleaningSystem", url=BIP_URL), True, False),
                (spec_unit("SinglePhaseReactor"), False, False),
                (spec_unit("ProcessWaterCenter"), True, False)):
            doc = self._appearing_doc(fields)
            if pinned:
                doc["metadata"]["reproducibility"] = {
                    "flowsheet_model_package": {"url": BIP_REPO,
                                                "commit": "abc123"}}
            sc = ctx(doc)
            self.assertEqual(C._species_guarded(sc, 0), guarded,
                             (fields, pinned))
        doc = valid_doc()
        del doc["streams"][1]["stream_properties"]["phases"]
        del doc["streams"][1]["stream_properties"]["composition"]
        ok, why = C._species_consistency(ctx(doc), 0)
        self.assertFalse(ok)
        self.assertIn("not computable", why)

    def test_reactive_reason(self):
        """_reactive_reason (R2.4, need only): a reactor family (incl. the
        keyword reactors 'Neutralization tank', 'Hydrotreater', and the
        R6.1(d) steps 'Ammonia addition tank', 'Overliming tank', 'Mash
        tank') -> 'reactor family'; declared reactions on a mixer, a
        facility, a Gibbs reactor -> None. R4.13: a unit of unknown family
        (valid_doc's one-inlet 'Mixer' label, R4.14; a 'Widget'; R5.1 / R6.1:
        an exact Mixer, HXutility or ProcessWaterCenter across which CO2
        appears, which contradicts each claim) -> 'family unknown' (it could
        be a reactor). R7.1: a label-only 'Holding tank' across which CO2
        appears -> None (a hosting claim never enters individually); the
        flowsheet-level pseudo-member covers it instead (_unexplained_hosts
        -> [0]: CO2 is produced across the flowsheet without a declared
        reaction)."""
        for doc in (valid_doc(), unit_doc({"unit_type": "Widget"})):
            self.assertEqual(C._reactive_reason(ctx(doc), 0), "family unknown")
        self.assertEqual(C._reactive_reason(ctx(unit_doc(cls("NRELFermentation"))), 0),
                         "reactor family")
        for unit_type in ("Neutralization tank", "Hydrotreater",
                          "Ammonia addition tank", "Overliming tank",
                          "Mash tank"):
            self.assertEqual(C._reactive_reason(
                ctx(unit_doc({"unit_type": unit_type})), 0), "reactor family")
        self.assertIsNone(C._reactive_reason(
            ctx(unit_doc({**cls("Mixer"), "reactions": [reaction()]})), 0))
        # R5.1 / R6.1(a): species appearing across an exact Mixer or
        # HXutility contradicts the claim, leaving the family unknown; across
        # a ProcessWaterCenter too (its chemistry only discards species).
        for fields in (cls("Mixer"), cls("HXutility"),
                       cls("ProcessWaterCenter")):
            self.assertEqual(C._reactive_reason(
                ctx(self._appearing_doc(fields)), 0), "family unknown")
        self.assertIsNone(C._reactive_reason(
            ctx(unit_doc(cls("ProcessWaterCenter"))), 0))
        self.assertIsNone(C._reactive_reason(
            ctx(unit_doc(cls("GibbsEquilibriumReactor"))), 0))
        sc = ctx(self._appearing_doc({"unit_type": "Holding tank"}))
        self.assertIsNone(C._reactive_reason(sc, 0))
        self.assertEqual(C._unexplained_hosts(sc), [0])

    def test_rxn_01_reactive_units(self):
        """_score_rxn_01 (CMP-RXN-01): no reactive unit -> None; species
        appearing across an exact Mixer (R5.1) or HXutility (R6.1(a))
        contradict the claim, so the unit of unknown family is reactive ->
        0; a reactor without reactions -> 0 ('none well-formed declared
        (reactor family)'); with a reaction -> 1; with only malformed ones
        -> 0."""
        self.assertIsNone(run(C._score_rxn_01, exact_mixer_doc())[0])
        self.assertEqual(run(C._score_rxn_01,
                             self._appearing_doc(cls("HXutility")))[0], 0.0)
        self.assertEqual(run(C._score_rxn_01, self._appearing_doc())[0], 0.0)
        reactor = spec_unit("SinglePhaseReactor")
        score, _m, missing = run(C._score_rxn_01, self._appearing_doc(reactor))
        self.assertEqual(score, 0.0)
        self.assertIn("none well-formed declared (reactor family)", missing[0])
        doc = self._appearing_doc(dict(reactor, reactions=[reaction()]))
        self.assertEqual(run(C._score_rxn_01, doc)[0], 1.0)
        doc = self._appearing_doc(dict(reactor, reactions=[
            {}, reaction(equation="x")]))
        self.assertEqual(run(C._score_rxn_01, doc)[0], 0.0)

    def test_reaction_units(self):
        """_reaction_units (R2.4): the reactive units plus every other unit
        declaring reaction objects ('declares reactions'); a mixer without
        reactions -> []; RXN-02 scores a mixer's declared reaction (complete
        -> 1, conversion missing -> 2/3) while RXN-01 stays not applicable;
        a Boiler's declared combustion set is scored too."""
        self.assertEqual(C._reaction_units(ctx(exact_mixer_doc())), [])
        self.assertEqual(C._reaction_units(ctx(rxn_doc(None))),
                         [(0, "reactor family")])
        doc = unit_doc({**cls("Mixer"), "reactions": [reaction()]})
        self.assertEqual(C._reaction_units(ctx(doc)), [(0, "declares reactions")])
        self.assertEqual(run(C._score_rxn_02, doc)[0], 1.0)
        self.assertIsNone(run(C._score_rxn_01, doc)[0])
        doc["units"][0]["reactions"][0].pop("conversion")
        self.assertAlmostEqual(run(C._score_rxn_02, doc)[0], 2 / 3)
        boiler = unit_doc(dict(spec_unit("Boiler"), reactions=[reaction()]))
        self.assertEqual(C._reaction_units(ctx(boiler)),
                         [(0, "declares reactions")])

    def test_reaction_sides_and_well_formed(self):
        """_reaction_sides: union of the equation's and the stoichiometry's
        (consumed, produced) sets, each counted only when both of its sides
        name a species; _well_formed (and the context's well_formed /
        reaction_sides, which resolve against the document's chemicals):
        'A -> B' or {A: -1, B: 1} -> True; 'x', 'A ->', {A: -1}, {} ->
        False. unit_reacts flags units declaring a well-formed reaction."""
        self.assertEqual(C._reaction_sides({"equation": "A + B -> C",
                                            "stoichiometry": {"D": -1, "C": 1}},
                                           {"D"}),
                         ({"A", "B", "D"}, {"C"}))
        self.assertEqual(C._reaction_sides({"equation": "A ->"}), (set(), set()))
        self.assertTrue(C._well_formed({"equation": "A -> B"}))
        self.assertTrue(C._well_formed({"stoichiometry": {"A": -1, "B": 1}},
                                       {"A", "B"}))
        for bad in ({"equation": "x"}, {"equation": "A ->"},
                    {"stoichiometry": {"A": -1}}, {}, "junk"):
            self.assertFalse(C._well_formed(bad), bad)
        sc = ctx(rxn_doc([{"stoichiometry": [-1, 1]}]))
        self.assertTrue(sc.well_formed(sc.units[0]["reactions"][0]))
        self.assertEqual(sc.reaction_sides({"equation": "Glucose -> CO2"}),
                         ({"Glucose"}, {"CO2"}))
        self.assertEqual(sc.unit_reacts, [True])
        self.assertEqual(ctx(rxn_doc([{}])).unit_reacts, [False])

    def test_reactive_units(self):
        """_reactive_units: an exact mixer -> [] (with declared reactions
        too: R2.4); a reactor-family unit -> [(0, 'reactor family')]."""
        self.assertEqual(C._reactive_units(ctx(exact_mixer_doc())), [])
        self.assertEqual(C._reactive_units(ctx(unit_doc(
            {**cls("Mixer"), "reactions": [reaction()]}))), [])
        self.assertEqual(C._reactive_units(ctx(rxn_doc(None))),
                         [(0, "reactor family")])

    def test_dryer_in_corn_corpus_is_not_reactive(self):
        """_reactive_reason on SF_BST_01: the DrumDryer D610 (dryer family,
        class-implied chemistry) is not reactive although species appear in
        its outlets; exactly the two reactors are."""
        doc = json.loads((CORPUS_DIR / "SF_BST_01.json").read_text(encoding="utf-8"))
        sc = ctx(doc)
        d610 = sc.unit_positions["D610"]
        self.assertEqual(sc.unit_classes[d610][1], "dryer")
        self.assertIsNone(C._reactive_reason(sc, d610))
        self.assertEqual(len(C._reactive_units(sc)), 2)

    def test_reaction_completeness(self):
        """_reaction_completeness (R2.0): complete -> (1.0, []); conversion
        1.5 -> 2/3 with a conversion locator under the given prefix; a
        reactant that is not among the consumed species -> 2/3; a reaction
        that is not well-formed (equation 'x', or none at all) -> 0 whatever
        its reactant and conversion, with a 'not well-formed' locator."""
        sc = ctx(valid_doc())
        self.assertEqual(C._reaction_completeness(sc, reaction(), "r"), (1.0, []))
        score, missing = C._reaction_completeness(sc, reaction(conversion=1.5),
                                                  "r")
        self.assertAlmostEqual(score, 2 / 3)
        self.assertEqual(missing, ["r.conversion (0..1)"])
        score, missing = C._reaction_completeness(
            sc, reaction(equation="A -> Ethanol"), "r")
        self.assertAlmostEqual(score, 2 / 3)
        self.assertEqual(missing, ["r.reactant (a consumed species)"])
        for bad in (reaction(equation="x"), {"reactant": "Ethanol",
                                             "conversion": 0.5}):
            score, missing = C._reaction_completeness(sc, bad, "r")
            self.assertEqual(score, 0.0)
            self.assertIn("not well-formed", missing[0])

    def test_per_reactive_unit(self):
        """_per_reactive_unit: a reactor with one reaction scored 0.5 -> (0.5,
        its missing, 0 full, 1 unit); a reactor without reactions -> (0.0,
        ['...reactions: none declared (reactor family)'], 0, 1); no reactive
        unit -> (None, [], 0, 0)."""
        half = lambda sc, p, ri, r: (0.5, [f"gap{ri}"])
        self.assertEqual(C._per_reactive_unit(ctx(rxn_doc([reaction()])), half),
                         (0.5, ["gap0"], 0, 1))
        self.assertEqual(C._per_reactive_unit(ctx(rxn_doc(None)), half),
                         (0.0, ["units[U1].reactions: none declared "
                                "(reactor family)"], 0, 1))
        self.assertEqual(C._per_reactive_unit(ctx(exact_mixer_doc()), half),
                         (None, [], 0, 0))

    def test_rxn_02_definitions(self):
        """_score_rxn_02 (CMP-RXN-02): complete -> 1; conversion 1.5 -> 2/3
        with a conversion locator; a reactive unit without reactions -> 0
        (population = reactive units, not reactions); no reactive unit ->
        None."""
        self.assertEqual(run(C._score_rxn_02, unit_doc({**cls("Mixer"), "reactions": [reaction()]}))[0], 1.0)
        score, _m, missing = run(C._score_rxn_02, unit_doc(
            {"reactions": [reaction(conversion=1.5)]}))
        self.assertAlmostEqual(score, 2 / 3)
        self.assertEqual(missing, ["units[U1].reactions[0].conversion (0..1)"])
        self.assertEqual(run(C._score_rxn_02, rxn_doc(None))[0], 0.0)
        self.assertIsNone(run(C._score_rxn_02, exact_mixer_doc())[0])

    def test_rxn_03_series_parallel(self):
        """_score_rxn_03 (CMP-RXN-03) over the reactive units: two indexed
        reactions -> 1; one index missing -> 0; a single (unindexed) reaction
        -> 1 (no order to state); a reactor-family unit whose reactions are
        deleted -> 0 with a "none well-formed declared" locator (applicable,
        not N/A); a unit carrying only a malformed reaction ([{}]) -> 0;
        no reactive unit -> None."""
        two = [reaction(index=0), reaction(index=1)]
        self.assertEqual(run(C._score_rxn_03, unit_doc({**cls("Mixer"), "reactions": two}))[0], 1.0)
        del two[1]["index"]
        score, _m, missing = run(C._score_rxn_03, unit_doc({**cls("Mixer"), "reactions": two}))
        self.assertEqual((score, missing), (0.0, ["units[U1].reactions[1].index"]))
        self.assertEqual(run(C._score_rxn_03, unit_doc({**cls("Mixer"), "reactions": [reaction()]}))[0], 1.0)
        doc = unit_doc(spec_unit("SinglePhaseReactor"))
        score, _m, missing = run(C._score_rxn_03, doc)
        self.assertEqual(score, 0.0)
        self.assertIn("units[U1].reactions: none well-formed declared "
                      "(reactor family)", missing[0])
        doc["units"][0]["reactions"] = [{}]
        self.assertEqual(run(C._score_rxn_03, doc)[0], 0.0)
        self.assertIsNone(run(C._score_rxn_03, exact_mixer_doc())[0])

    def test_reaction_coefficients(self):
        """_reaction_coefficients: stoichiometry by id -> its coefficients;
        no stoichiometry -> the equation, phase tags stripped; an
        unresolvable species or garbage -> None."""
        sc = ctx(rxn_doc(None))
        self.assertEqual(C._reaction_coefficients(sc, {"stoichiometry": {
            "Glucose": -1, "CO2": 2}}), {"Glucose": -1.0, "CO2": 2.0})
        self.assertEqual(C._reaction_coefficients(sc, {
            "equation": "Glucose,l -> 2 Ethanol,l + 2 CO2,g"}),
            {"Glucose": -1.0, "Ethanol": 2.0, "CO2": 2.0})
        self.assertIsNone(C._reaction_coefficients(sc, {"equation": "Nope -> CO2"}))
        self.assertIsNone(C._reaction_coefficients(sc, {"stoichiometry": {
            "Glucose": "x"}}))
        self.assertIsNone(C._reaction_coefficients(sc, {}))

    def test_has_phase_tags(self):
        """_has_phase_tags: every species tagged -> True; one untagged ->
        False; no arrow / non-string -> False."""
        self.assertTrue(C._has_phase_tags("Glucose,l -> 2 Ethanol,l + 2 CO2,g"))
        self.assertFalse(C._has_phase_tags("Glucose,l -> 2 Ethanol + 2 CO2,g"))
        self.assertFalse(C._has_phase_tags("Glucose"))
        self.assertFalse(C._has_phase_tags(None))

    def test_basis_derivable(self):
        """_basis_derivable (BASIS_RTOL 1e-3): Glucose -> 2 Ethanol + 2 CO2
        conserves mass (molar basis) with sum(nu) != 0 -> True; a mass-basis
        stoichiometry (sum 0, mass not conserved) -> True; Glucose -> 3
        Ethanol (neither) -> False; an isomerization A -> B of equal molar
        mass (both hold, R2.10: basis immaterial) -> True; a species without
        molar mass -> False."""
        sc = ctx(rxn_doc(None))
        self.assertEqual(C.BASIS_RTOL, 1e-3)
        self.assertTrue(C._basis_derivable(sc, {"equation": GLUCOSE_EQ}))
        self.assertTrue(C._basis_derivable(sc, {"stoichiometry": {
            "Glucose": -1.0, "Ethanol": 0.5114, "CO2": 0.4886}}))
        self.assertFalse(C._basis_derivable(sc, {"equation": "Glucose -> 3 Ethanol"}))
        iso = rxn_doc(None)
        iso["chemicals"] += [{"id": "A", "molar_mass": 10.0},
                             {"id": "B", "molar_mass": 10.0}, {"id": "M"}]
        self.assertTrue(C._basis_derivable(ctx(iso), {"equation": "A -> B"}))
        self.assertFalse(C._basis_derivable(ctx(iso), {"equation": "A -> M"}))

    def test_single_phase_ports(self):
        """_single_phase_ports: liquid-only ports -> True; an outlet with a
        vapor phase beside the liquid -> False; a unit with no streams ->
        False."""
        doc = rxn_doc(None)
        self.assertTrue(C._single_phase_ports(ctx(doc), 0))
        doc["streams"][1]["stream_properties"]["phases"].update(copy.deepcopy(VAP))
        self.assertFalse(C._single_phase_ports(ctx(doc), 0))
        doc["streams"] = []
        self.assertFalse(C._single_phase_ports(ctx(doc), 0))

    def test_reaction_basis_phase(self):
        """_reaction_basis_phase: basis and phase both derivable ->
        DERIVED_CREDIT with two '(derivable...)' locators; basis not
        derivable (mass closes within RXN_CLOSURE_RTOL, R6.2, but not
        within BASIS_RTOL: 'Glucose -> 2 Ethanol + 2.02 CO2', 0.5 % off),
        phase derivable -> DERIVED_CREDIT / 2; a reaction conserving mass on
        neither basis ('Glucose -> 3 Ethanol', 23 % off) is not well-formed
        -> 0 with the R6.2 locator."""
        sc = ctx(rxn_doc(None))
        score, missing = C._reaction_basis_phase(sc, 0, 0, {"equation": GLUCOSE_EQ})
        self.assertEqual(score, C.DERIVED_CREDIT)
        self.assertEqual(len(missing), 2)
        self.assertIn("(derivable from the mass balance)", missing[0])
        score, _m = C._reaction_basis_phase(
            sc, 0, 0, {"equation": "Glucose -> 2 Ethanol + 2.02 CO2"})
        self.assertEqual(score, C.DERIVED_CREDIT / 2)
        score, missing = C._reaction_basis_phase(
            sc, 0, 0, {"equation": "Glucose -> 3 Ethanol"})
        self.assertEqual(score, 0.0)
        self.assertIn("conserve mass on neither a mol nor a wt basis",
                      missing[0])

    def test_rxn_04_basis(self):
        """_score_rxn_04 (CMP-RXN-04): a reactor whose Glucose reaction has a
        derivable basis and single-phase ports -> DERIVED_CREDIT; a reactor
        without reactions -> 0; no reactive unit -> None."""
        doc = rxn_doc([reaction(equation=GLUCOSE_EQ, reactant="Glucose")])
        self.assertEqual(run(C._score_rxn_04, doc)[0], C.DERIVED_CREDIT)
        self.assertEqual(run(C._score_rxn_04, rxn_doc(None))[0], 0.0)
        self.assertIsNone(run(C._score_rxn_04, exact_mixer_doc())[0])


# ---------------------------------------------------------- costing criteria

CUSTOM_URL = "https://example.org/units.py"


def costed(corr=None, results=None, units_map=None, installed=True,
           name="StorageTank", url=BST_URL, costs=None):
    """unit_doc with U1 a costed unit of class `name` on `url` (default: the
    core StorageTank) with purchase_costs `costs` (default one 'Tank'
    item), its installed cost, correlation, design results and units."""
    unit = dict(spec_unit(name, url=url))
    unit["purchase_costs"] = {"Tank": 1000.0} if costs is None else costs
    if installed:
        unit["installed_costs"] = {"Tank": 2000.0}
    if corr is not None:
        unit["purchase_cost_correlations"] = {"Tank": corr}
    if results is not None:
        unit["design_results"] = results
    if units_map is not None:
        unit["quantity_units_for_design_results"] = units_map
    return unit_doc(unit)


def exact_mixer_doc():
    """valid_doc with U1 named exactly as the core Mixer by
    design_simulation_method (confidence 1, so cost-free; R4.3: valid_doc's
    bare unit_type 'Mixer' is a family claim only, and costed)."""
    return unit_doc(cls("Mixer"))


POWER_LAW = {"correlation_type": "power_law", "basis": "Volume",
             "reference_size": 10.0, "reference_cost": 900.0, "exponent": 0.6,
             "reference_CE_index": 500.0}


class TestCostingCriteria(unittest.TestCase):
    def test_cost_items_and_correlation(self):
        """_cost_items -> (unit, label, item, cost) per purchase item;
        _correlation -> that item's correlation dict or {}."""
        sc = ctx(costed(POWER_LAW))
        (unit, label, item, cost), = C._cost_items(sc)
        self.assertEqual((label, item, cost), ("units[U1]", "Tank", 1000.0))
        self.assertEqual(C._correlation(unit, item)["exponent"], 0.6)
        self.assertEqual(C._correlation(unit, "Other"), {})

    def test_unit_cost_items_union(self):
        """_unit_cost_items: the union of purchase_costs, installed_costs and
        purchase_cost_correlations keys, first occurrence first; nothing ->
        []. So deleting a poorly correlated item's purchase cost keeps it in
        CMP-COST-04's per-item population (0.75 stays 0.75, it does not rise
        to 1), and deleting its correlation instead drops the unit to 0.5
        on COST-04 and to 0 on COST-07 (the other item keeps its
        lifetime)."""
        self.assertEqual(C._unit_cost_items({}), [])
        unit = {"purchase_costs": {"A": 1.0}, "installed_costs": {"B": 2.0},
                "purchase_cost_correlations": {"C": {}, "A": {}}}
        self.assertEqual(C._unit_cost_items(unit), ["A", "B", "C"])
        doc = costed({"correlation_type": "custom_function", "lifetime": 5},
                     name="CustomTank", url=CUSTOM_URL,
                     costs={"Tank": 1000.0, "Agitator": 50.0})
        unit = doc["units"][0]
        unit["installed_costs"]["Agitator"] = 100.0
        unit["purchase_cost_correlations"]["Agitator"] = dict(POWER_LAW,
                                                              lifetime=5)
        self.assertEqual(run(C._score_cost_04, doc)[0], 0.75)
        self.assertEqual(run(C._score_cost_07, doc)[0], 1.0)
        dropped = copy.deepcopy(doc)
        del dropped["units"][0]["purchase_costs"]["Tank"]
        self.assertEqual(run(C._score_cost_04, dropped)[0], 0.75)
        del unit["purchase_cost_correlations"]["Tank"]
        self.assertEqual(run(C._score_cost_04, doc)[0], 0.5)
        self.assertEqual(run(C._score_cost_07, doc)[0], 0.0)

    def test_per_costed_unit(self):
        """_per_costed_unit: unweighted over sc.costed (R4.12: a reactor
        scoring 1 beside a pump scoring 0 -> 0.5, one full); explicit
        positions restrict the population; none -> (None, [], 0)."""
        doc = valid_doc()
        doc["units"] = [dict(spec_unit("SinglePhaseReactor"), id="R1"),
                        dict(spec_unit("Pump"), id="P1")]
        sc = ctx(doc)
        score_of = lambda sc, p: ((1.0, []) if p == 0 else (0.0, ["gap"]))
        self.assertEqual(C._per_costed_unit(sc, score_of), (0.5, ["gap"], 1))
        self.assertEqual(C._per_costed_unit(sc, score_of, [1]), (0.0, ["gap"], 0))
        self.assertEqual(C._per_costed_unit(ctx(exact_mixer_doc()), score_of),
                         (None, [], 0))

    def test_cost_01_purchase_costs(self):
        """_cost_01_unit / _score_cost_01 (CMP-COST-01): only a cost-free
        Mixer (named exactly by design_simulation_method) -> None; R4.3: a
        unit_type-only 'Mixer' (family claim, no class) is costed -> 0; a
        costed unit without purchase_costs -> 0; with {} -> 0 ('(empty)');
        non-empty -> 1."""
        self.assertIsNone(run(C._score_cost_01, exact_mixer_doc())[0])
        self.assertEqual(run(C._score_cost_01, valid_doc())[0], 0.0)
        doc = costed()
        del doc["units"][0]["purchase_costs"]
        self.assertEqual(run(C._score_cost_01, doc)[0], 0.0)
        score, _m, missing = run(C._score_cost_01, costed(costs={}))
        self.assertEqual((score, missing), (0.0, ["units[U1].purchase_costs (empty)"]))
        self.assertEqual(run(C._score_cost_01, costed())[0], 1.0)
        self.assertEqual(C._cost_01_unit(ctx(costed()), 0), (1.0, []))

    def test_cost_02_installed(self):
        """_cost_02_unit / _score_cost_02 (CMP-COST-02): installed counterpart
        -> 1; missing -> 0 with its locator; a costed unit with no items ->
        0; no costed units -> None."""
        self.assertEqual(run(C._score_cost_02, costed())[0], 1.0)
        score, _m, missing = run(C._score_cost_02, costed(installed=False))
        self.assertEqual((score, missing), (0.0, ["units[U1].installed_costs['Tank']"]))
        self.assertEqual(C._cost_02_unit(ctx(costed(costs={}, installed=False)), 0)[0], 0.0)
        self.assertIsNone(run(C._score_cost_02, exact_mixer_doc())[0])

    def test_cost_03_design_result_units(self):
        """_cost_03_unit / _score_cost_03 / _has_quantity_unit (CMP-COST-03):
        no results -> 0; empty results -> 0 ('(empty)'); unit declared -> 1;
        unitless numeric -> 0.5; legacy inline units -> 1; a string result
        needs no unit; no costed units -> None."""
        self.assertEqual(run(C._score_cost_03, costed())[0], 0.0)
        self.assertEqual(C._cost_03_unit(ctx(costed(results={})), 0),
                         (0.0, ["units[U1].design_results (empty)"]))
        self.assertEqual(run(C._score_cost_03, costed(results={"Volume": 5},
                                                      units_map={"Volume": "m3"}))[0], 1.0)
        self.assertEqual(run(C._score_cost_03, costed(results={"Volume": 5}))[0], 0.5)
        self.assertEqual(run(C._score_cost_03, costed(
            results={"Volume": {"value": 5, "units": "m3"}, "Type": "x"}))[0], 1.0)
        self.assertTrue(C._has_quantity_unit(5.0, ""))
        self.assertFalse(C._has_quantity_unit(5.0, None))
        self.assertIsNone(run(C._score_cost_03, exact_mixer_doc())[0])

    def test_cost_04_correlations(self):
        """_cost_04_unit / _score_cost_04 (CMP-COST-04) over EVERY costed
        unit: a custom class scores its items -- power_law -> 1,
        custom_function -> 0.5, none -> 0, no cost items -> 0; a core
        StorageTank identified at confidence 1 stays in the population and
        scores 1 without any correlation (BioSTEAM re-derives its cost); the
        same tank with a descriptive unit_type and no
        design_simulation_method (class no longer known) -> scored by its
        items, 0 without correlations (so that deletion lowers, never
        raises); no costed units -> None."""
        custom = dict(name="CustomTank", url=CUSTOM_URL)
        self.assertEqual(run(C._score_cost_04, costed(POWER_LAW, **custom))[0], 1.0)
        self.assertEqual(run(C._score_cost_04, costed(
            {"correlation_type": "custom_function"}, **custom))[0], 0.5)
        self.assertEqual(run(C._score_cost_04, costed(**custom))[0], 0.0)
        bare = costed(costs={}, installed=False, **custom)
        self.assertEqual(C._cost_04_unit(ctx(bare), 0),
                         (0.0, ["units[U1].purchase_costs: no items to correlate"]))
        self.assertEqual(C._cost_04_unit(ctx(costed()), 0), (1.0, []))
        score, message, missing = run(C._score_cost_04, costed())
        self.assertEqual((score, missing), (1.0, []))
        self.assertIn("1 core BioSTEAM class", message)
        doc = costed()
        del doc["units"][0]["design_simulation_method"]
        doc["units"][0]["unit_type"] = "Storage tank"
        score, _m, missing = run(C._score_cost_04, doc)
        self.assertEqual((score, missing),
                         (0.0, ["units[U1].purchase_cost_correlations['Tank']"]))
        self.assertIsNone(run(C._score_cost_04, exact_mixer_doc())[0])

    def test_cost_05_cepci_back_derivation(self):
        """_score_cost_05 / _back_derivable (CMP-COST-05): complete power-law
        item + sized basis -> DERIVED_CREDIT; no exponent -> 0; a costed
        unit without correlations -> 0 (applicable); no costed units ->
        None."""
        doc = costed(POWER_LAW, results={"Volume": 12.0})
        self.assertEqual(run(C._score_cost_05, doc)[0], C.DERIVED_CREDIT)
        unit = doc["units"][0]
        self.assertTrue(C._back_derivable(unit, "Tank", 1000.0))
        self.assertFalse(C._back_derivable(unit, "Tank", 0.0))
        corr = dict(POWER_LAW)
        del corr["exponent"]
        self.assertEqual(run(C._score_cost_05, costed(corr, {"Volume": 12.0}))[0], 0.0)
        self.assertEqual(run(C._score_cost_05, costed())[0], 0.0)
        self.assertIsNone(run(C._score_cost_05, exact_mixer_doc())[0])

    def test_cost_06_parallel_count(self):
        """_count_result_key / _cost_06_unit / _score_cost_06 (CMP-COST-06):
        a 'Number of tanks' design result -> DERIVED_CREDIT (locator names
        it); no count result -> 0; no costed units -> None."""
        doc = costed(results={"Number of tanks": 2, "Volume": 5.0})
        self.assertEqual(C._count_result_key(doc["units"][0]), "Number of tanks")
        score, _m, missing = run(C._score_cost_06, doc)
        self.assertEqual(score, C.DERIVED_CREDIT)
        self.assertIn("design_results['Number of tanks']", missing[0])
        self.assertEqual(C._cost_06_unit(ctx(costed()), 0)[0], 0.0)
        self.assertIsNone(C._count_result_key({}))
        self.assertIsNone(run(C._score_cost_06, exact_mixer_doc())[0])

    def test_cost_07_lifetimes(self):
        """_cost_07_unit / _score_cost_07 (CMP-COST-07): every correlation
        item states a lifetime -> 1; a core class without -> DERIVED_CREDIT;
        a custom class without (or with no correlation items) -> 0; no
        costed units -> None."""
        self.assertEqual(run(C._score_cost_07, costed(dict(POWER_LAW, lifetime=20)))[0], 1.0)
        self.assertEqual(run(C._score_cost_07, costed(POWER_LAW))[0], C.DERIVED_CREDIT)
        custom = dict(name="CustomTank", url=CUSTOM_URL)
        self.assertEqual(run(C._score_cost_07, costed(POWER_LAW, **custom))[0], 0.0)
        self.assertEqual(C._cost_07_unit(ctx(costed(**custom)), 0)[0], 0.0)
        self.assertIsNone(run(C._score_cost_07, exact_mixer_doc())[0])


# ---------------------------------------------------------- utility criteria

STEAM = {"id": "steam", "temperature": 400.0, "pressure": 3e5,
         "regeneration_price": 0.2, "heat_transfer_efficiency": 0.95,
         "composition": [{"phase": "g", "component_name": "Water",
                          "mol_fraction": 1.0}]}


def with_agents(heat=(), power=(), other=(), consumption=None):
    doc = label_mixer_doc()
    doc["utilities"] = {"heat_utilities": list(heat),
                        "power_utilities": list(power),
                        "other_utilities": list(other)}
    if consumption is not None:
        doc["units"][0]["utility_consumption_results"] = consumption
    return doc


class TestUtilityCriteria(unittest.TestCase):
    def test_utility_refs_and_agent_gaps(self):
        """_utility_refs -> {agent id: first referencing unit}; _agent_gaps:
        heat agent without composition -> ['composition', its phase (R4.2)];
        an other agent without composition -> ['composition'] only (no phase
        part); power -> []."""
        sc = ctx(with_agents(consumption={"steam": 5.0}))
        self.assertEqual(C._utility_refs(sc), {"steam": "units[U1]"})
        gaps = C._agent_gaps("heat_utilities", dict(STEAM, composition=[]))
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0], "composition")
        self.assertTrue(gaps[1].startswith("composition[*].phase"))
        self.assertEqual(C._agent_gaps("other_utilities",
                                       dict(STEAM, composition=[])),
                         ["composition"])
        self.assertEqual(C._agent_gaps("power_utilities", {"id": "grid"}), [])

    def test_agent_phase_ok(self):
        """_agent_phase_ok (R4.2): every entry 'g' (steam) or every entry
        'l' (cooling water) -> True; an entry without a phase, mixed l/g, a
        solid 's', an empty or non-list composition -> False."""
        water = {"component_name": "Water", "mol_fraction": 1.0}
        self.assertTrue(C._agent_phase_ok([dict(water, phase="g")]))
        self.assertTrue(C._agent_phase_ok([dict(water, phase="l", mol_fraction=0.5),
                                           dict(water, phase="l", mol_fraction=0.5)]))
        for composition in ([water], [dict(water, phase="l"), dict(water, phase="g")],
                            [dict(water, phase="s")], [], None, "g"):
            self.assertFalse(C._agent_phase_ok(composition), composition)

    def test_utility_and_heat_need(self):
        """_utility_need / _heat_need (R1.9): a lone Mixer, nothing declared
        -> neither; a Pump -> utility need only; an HXutility -> both; a
        declared heat agent -> both; a unit utility result -> utility need.
        R4.13: a unit of unknown family (valid_doc's one-inlet 'Mixer'
        label, R4.14) -> both."""
        self.assertEqual((C._utility_need(ctx(label_mixer_doc())),
                          C._heat_need(ctx(label_mixer_doc()))), (False, False))
        self.assertEqual((C._utility_need(ctx(valid_doc())),
                          C._heat_need(ctx(valid_doc()))), (True, True))
        pump = ctx(one_unit_doc(spec_unit("Pump")))
        self.assertEqual((C._utility_need(pump), C._heat_need(pump)), (True, False))
        hx = ctx(one_unit_doc(spec_unit("HXutility")))
        self.assertEqual((C._utility_need(hx), C._heat_need(hx)), (True, True))
        agent = ctx(with_agents([STEAM]))
        self.assertEqual((C._utility_need(agent), C._heat_need(agent)), (True, True))
        self.assertTrue(C._utility_need(ctx(with_agents(consumption={"x": 1.0}))))

    def test_util_01_resolution(self):
        """_score_util_01 (CMP-UTIL-01): no need -> None; a Pump and no agent
        -> 0; referenced but undeclared -> 0; declared complete + referenced
        -> 1 (R5.1: the steam duty U1 records contradicts its mixer label,
        so U1 of unknown family needs a power agent too -- only steam
        declared -> 1/2, with a grid agent -> 1); a heat agent without
        composition -> 2/4 (its phase goes too, R4.2); only its phase
        deleted -> 3/4, the missing entry naming the phase; an other agent
        without composition -> 2/3."""
        self.assertIsNone(run(C._score_util_01, label_mixer_doc())[0])
        score, _m, missing = run(C._score_util_01, one_unit_doc(spec_unit("Pump")))
        self.assertEqual((score, missing), (0.0, ["utilities: no agents declared"]))
        score, _m, missing = run(C._score_util_01, with_agents(consumption={"steam": 1}))
        self.assertEqual(score, 0.0)
        self.assertIn("no agent 'steam' (referenced by units[U1])", missing[0])
        self.assertEqual(run(C._score_util_01, with_agents(
            [STEAM], consumption={"steam": 1}))[0], 0.5)
        self.assertEqual(run(C._score_util_01, with_agents(
            [STEAM], [{"id": "grid", "price": 0.07}],
            consumption={"steam": 1}))[0], 1.0)
        self.assertAlmostEqual(run(C._score_util_01, with_agents(
            [dict(STEAM, composition=[])]))[0], 2 / 4)
        untagged = dict(STEAM, composition=[{"component_name": "Water",
                                             "mol_fraction": 1.0}])
        score, _m, missing = run(C._score_util_01, with_agents([untagged]))
        self.assertAlmostEqual(score, 3 / 4)
        self.assertEqual(missing, ["utilities.heat_utilities[steam]."
                                   "composition[*].phase (one phase, l or g, "
                                   "on every entry)"])
        self.assertAlmostEqual(run(C._score_util_01, with_agents(
            [], [], [dict(STEAM, id="gas", composition=[])]))[0], 2 / 3)

    def test_util_02_prices(self):
        """_score_util_02 (CMP-UTIL-02): priced heat agent and legacy-priced
        power agent -> 1; unpriced other agent -> 2/3 overall when the mixer
        references all three; R6.4: with no unit referencing any agent the
        score is the lowest agent's (the unpriced one) -> 0; a referenced
        but undeclared agent counts 0 (-> 1/3 beside priced, referenced
        steam: R5.1, the duty U1 records on it contradicts U1's mixer label,
        so U1 of unknown family also needs a power agent, missing -> 0;
        with steam referenced by no unit -> 0, R6.4); a Pump
        with no agent -> 0; no need -> None."""
        doc = with_agents([STEAM], [{"id": "grid", "price": 0.07}],
                          [{"id": "gas"}], consumption={
                              "steam": 5.0, "grid": 1.0, "gas": 1.0})
        score, _m, missing = run(C._score_util_02, doc)
        self.assertAlmostEqual(score, 2 / 3)
        self.assertEqual(missing, ["utilities.other_utilities[gas].price"])
        del doc["units"][0]["utility_consumption_results"]
        self.assertEqual(run(C._score_util_02, doc)[0], 0.0)
        score, _m, missing = run(C._score_util_02, with_agents(
            [STEAM], consumption={"power": 3.0, "steam": 5.0}))
        self.assertAlmostEqual(score, 1 / 3)
        self.assertIn("no agent 'power' to price", missing[0])
        self.assertEqual(run(C._score_util_02, with_agents(
            [STEAM], consumption={"power": 3.0}))[0], 0.0)
        self.assertEqual(run(C._score_util_02, one_unit_doc(spec_unit("Pump")))[0], 0.0)
        self.assertIsNone(run(C._score_util_02, label_mixer_doc())[0])

    def test_agent_roles(self):
        """_agent_roles: a positive recorded duty -> heating, a negative one
        -> cooling (whatever the bucket); zero -> no evidence; a facility's
        (reversed) duties are ignored."""
        doc = with_agents(consumption={"steam": 5.0, "idle": 0.0})
        doc["units"][0]["utility_production_results"] = {"cw": -3.0}
        doc["units"].append({"id": "CT", "unit_type": "CoolingTower",
                             "utility_consumption_results": {"cw": 9.0}})
        self.assertEqual(C._agent_roles(ctx(doc)),
                         {"steam": {"heating"}, "cw": {"cooling"}})

    def test_water_dominated(self):
        """_water_dominated: water at >= 0.5 fraction -> True (either name,
        any case); a propane refrigerant -> False; empty -> False."""
        self.assertTrue(C._water_dominated([{"component_name": "Water",
                                             "mol_fraction": 0.8}]))
        self.assertTrue(C._water_dominated([{"component_name": "h2o",
                                             "mass_fraction": 0.5}]))
        self.assertFalse(C._water_dominated([{"component_name": "Propane",
                                              "mol_fraction": 1.0}]))
        self.assertFalse(C._water_dominated([]))

    def test_phase_change_agent(self):
        """_phase_change_agent (R2.13: exempt only on affirmative evidence):
        all-vapor steam -> True only when recorded as heating only (positive
        duty); unrecorded, mixed or cooling roles -> False; an all-liquid
        refrigerant -> True only when recorded as cooling; liquid water used
        for cooling -> False (sensible); no composition -> False."""
        propane = dict(STEAM, composition=[{"phase": "l", "component_name":
                                            "Propane", "mol_fraction": 1.0}])
        water = dict(STEAM, composition=[{"phase": "l", "component_name":
                                          "Water", "mol_fraction": 1.0}])
        self.assertTrue(C._phase_change_agent(STEAM, {"heating"}))
        self.assertFalse(C._phase_change_agent(STEAM, set()))
        self.assertFalse(C._phase_change_agent(STEAM, {"heating", "cooling"}))
        self.assertFalse(C._phase_change_agent(STEAM, {"cooling"}))
        self.assertTrue(C._phase_change_agent(propane, {"cooling"}))
        self.assertFalse(C._phase_change_agent(propane, set()))
        self.assertFalse(C._phase_change_agent(propane, {"heating"}))
        self.assertFalse(C._phase_change_agent(water, {"cooling"}))
        self.assertFalse(C._phase_change_agent({"composition": []}, set()))

    def test_util_03_heat_agent_limits(self):
        """_score_util_03 (CMP-UTIL-03): condensing steam recorded as heating
        + efficiency -> 1; the same steam no unit records a duty for -> 0.5
        (the limit exemption needs affirmative evidence, R2.13); an
        efficiency outside (0, 1] or a junk temperature_limit counts as
        absent; liquid cooling water without temperature_limit -> 0.5; a
        propane refrigerant recorded as cooling -> 1; an HXutility with no
        heat agent -> 0; no heat need -> None; a list-valued agent id does
        not raise."""
        heating = with_agents([STEAM], consumption={"steam": 5.0})
        self.assertEqual(run(C._score_util_03, heating)[0], 1.0)
        self.assertEqual(run(C._score_util_03, with_agents([STEAM]))[0], 0.5)
        junk = with_agents([dict(STEAM, heat_transfer_efficiency=1.5,
                                 temperature_limit=-1.0)],
                           consumption={"steam": 5.0})
        self.assertEqual(run(C._score_util_03, junk)[0], 0.5)
        listed = with_agents([dict(STEAM, id=["steam"])])
        self.assertEqual(run(C._score_util_03, listed)[0], 0.5)
        water = dict(STEAM, composition=[{"phase": "l", "component_name": "Water",
                                          "mol_fraction": 1.0}])
        score, _m, missing = run(C._score_util_03, with_agents([water]))
        self.assertEqual(score, 0.5)
        self.assertIn("temperature_limit (sensible-heat agent)", missing[0])
        propane = dict(STEAM, id="propane", composition=[
            {"phase": "l", "component_name": "Propane", "mol_fraction": 1.0}])
        doc = with_agents([propane])
        doc["units"][0]["utility_production_results"] = {"propane": -2.0}
        self.assertEqual(run(C._score_util_03, doc)[0], 1.0)
        self.assertEqual(run(C._score_util_03, one_unit_doc(spec_unit("HXutility")))[0], 0.0)
        self.assertIsNone(run(C._score_util_03, label_mixer_doc())[0])

    def test_util_04_results(self):
        """_score_util_04 / _util_04_unit (CMP-UTIL-04): no result objects ->
        0; both -> 1 (an exactly named Mixer); unweighted (R4.12: a reactor
        with both beside a pump with neither -> 1/2). R2.0: a unit that
        always draws a utility (_draws_utility: pump, HXutility, ...) with
        an empty consumption + production pair -> 0, and non-numeric entries
        ({'a': 'b'}) count as absent; one numeric entry -> 1. R4.13: so does
        a unit of unknown family (valid_doc's one-inlet 'Mixer' label, R4.14:
        'may draw'). Waived only on affirmative evidence: an idle unit
        (every connected stream states a zero flow), or a flash stated
        adiabatic (Q = 0)."""
        self.assertEqual(run(C._score_util_04, valid_doc())[0], 0.0)
        empty = {"utility_consumption_results": {},
                 "utility_production_results": {}}
        doc = unit_doc(dict(cls("Mixer"), **empty))
        self.assertEqual(run(C._score_util_04, doc)[0], 1.0)
        score, _m, missing = run(C._score_util_04, unit_doc(dict(empty)))
        self.assertEqual(score, 0.0)
        self.assertIn("a unit of unknown family may draw a utility", missing[0])
        doc["units"] = [dict(spec_unit("SinglePhaseReactor"), id="R1",
                             utility_consumption_results={},
                             utility_production_results={}),
                        dict(spec_unit("Pump"), id="P1")]
        self.assertEqual(run(C._score_util_04, doc)[0], 0.5)
        pump = dict(spec_unit("Pump"), id="P1", utility_consumption_results={},
                    utility_production_results={"a": "b"})
        doc["units"] = [pump]
        score, _m, missing = run(C._score_util_04, doc)
        self.assertEqual(score, 0.0)
        self.assertIn("always draws a utility", missing[0])
        pump["utility_consumption_results"] = {"grid": 2.0}
        self.assertEqual(C._util_04_unit(ctx(doc), 0), (1.0, []))
        flash = ctx(unit_doc(spec_unit("Flash", {"Q": 0.0, "P": 1e5})))
        self.assertFalse(C._draws_utility(flash, 0))
        self.assertTrue(C._draws_utility(ctx(unit_doc(spec_unit("Flash"))), 0))
        self.assertFalse(C._draws_utility(ctx(exact_mixer_doc()), 0))
        self.assertTrue(C._draws_utility(ctx(valid_doc()), 0))
        idle = one_unit_doc(spec_unit("Pump"), ins=({"mass": 0.0},),
                            outs=({"mass": 0.0},))
        for s in idle["streams"]:
            s["stream_properties"]["total_molar_flow"] = 0.0
        self.assertFalse(C._draws_utility(ctx(idle), 0))
        del idle["streams"][1]["stream_properties"]["total_mass_flow"]
        del idle["streams"][1]["stream_properties"]["total_molar_flow"]
        self.assertTrue(C._draws_utility(ctx(idle), 0))

    def test_util_05_selection_parameters(self):
        """_score_util_05 (CMP-UTIL-05): heat agents needed (an HXutility) ->
        0 with a locator; a Pump only -> None."""
        score, _m, missing = run(C._score_util_05, one_unit_doc(spec_unit("HXutility")))
        self.assertEqual(score, 0.0)
        self.assertIn("HeatUtility.dT", missing[0])
        self.assertIsNone(run(C._score_util_05, one_unit_doc(spec_unit("Pump")))[0])


# ------------------------------------------------ system + integrity criteria

class TestSystemCriteria(unittest.TestCase):
    def test_sys_01_and_03_not_representable(self):
        """_score_sys_01 / _score_sys_03 (CMP-SYS-01/03): always 0 with a
        locator."""
        for scorer in (C._score_sys_01, C._score_sys_03):
            score, _m, missing = run(scorer, valid_doc())
            self.assertEqual(score, 0.0)
            self.assertTrue(missing)

    def test_sys_02_tea_parameters(self):
        """_score_sys_02 / _target_products_designated (CMP-SYS-02, R3.10):
        14 groups incl. the dollar year and the target product; valid_doc
        (currency, TEA_year 2020, products -> the boundary outlet 'prod') ->
        3/14; without currency -> 2/14 with a metadata.TEA_currency locator
        first; a product naming the FEED (not an outlet), an unknown stream,
        an empty list or an entry without stream_id -> not designated
        (2/14, 'metadata.products' locator); a TEA_year of 0 or a string
        counts as unstated, and so (R4.9) do TEA_year 99999, 1.5, 1899 or
        2101 and TEA_currency 'x', 'dollars', 'usd' or 'US$' (each -> 2/14);
        TEA_year 2020.0, 1900 or 2100 and TEA_currency 'EUR' count; the price
        book and the plant start year are listed missing (not
        representable)."""
        self.assertEqual(len(C._TEA_ITEMS), 14)
        self.assertEqual(C._TEA_ITEMS[:3], ("currency", "dollar year (cost basis)",
                                            "target product"))
        self.assertAlmostEqual(run(C._score_sys_02, valid_doc())[0], 3 / 14)
        self.assertTrue(C._target_products_designated(ctx(valid_doc())))
        doc = valid_doc()
        remove(doc, "metadata/TEA_currency")
        score, _m, missing = run(C._score_sys_02, doc)
        self.assertAlmostEqual(score, 2 / 14)
        self.assertTrue(missing[0].startswith("metadata.TEA_currency"))
        self.assertTrue(any("stream_prices" in m for m in missing))
        self.assertTrue(any("plant start year" in m for m in missing))
        for products in ([{"stream_id": "feed"}], [{"stream_id": "nope"}], [],
                         [{"display_name": "P"}], {"stream_id": "prod"}):
            doc = valid_doc()
            doc["metadata"]["products"] = products
            self.assertFalse(C._target_products_designated(ctx(doc)), products)
            score, _m, missing = run(C._score_sys_02, doc)
            self.assertAlmostEqual(score, 2 / 14)
            self.assertTrue(any(m.startswith("metadata.products") for m in missing))
        for year in (0, "2020", 99999, 1.5, 1899, 2101, True):
            doc = valid_doc()
            doc["metadata"]["TEA_year"] = year
            self.assertAlmostEqual(run(C._score_sys_02, doc)[0], 2 / 14, msg=year)
        for currency in ("x", "dollars", "usd", "US$", 840):
            doc = valid_doc()
            doc["metadata"]["TEA_currency"] = currency
            self.assertAlmostEqual(run(C._score_sys_02, doc)[0], 2 / 14,
                                   msg=currency)
        for field, value in (("TEA_year", 2020.0), ("TEA_year", 1900),
                             ("TEA_year", 2100), ("TEA_currency", "EUR")):
            doc = valid_doc()
            doc["metadata"][field] = value
            self.assertAlmostEqual(run(C._score_sys_02, doc)[0], 3 / 14,
                                   msg=value)

    def test_valid_tea_currency_and_year(self):
        """_valid_tea_currency / _valid_tea_year (R4.9): 'USD' / 'EUR' and
        integral years 1900..2100 (int, integral float, legacy inline
        {value, units}) -> True; 'x', 'dollars', 'usd', None, 99999, 1.5,
        1899, 2101, a string year or a bool -> False. The bounds are
        TEA_YEAR_MIN/MAX = 1900/2100."""
        self.assertTrue(C._valid_tea_currency("USD"))
        self.assertTrue(C._valid_tea_currency("EUR"))
        for bad in ("x", "dollars", "usd", "USDX", None, 840):
            self.assertFalse(C._valid_tea_currency(bad), bad)
        for good in (2018, 2020.0, 1900, 2100, {"value": 2019, "units": ""}):
            self.assertTrue(C._valid_tea_year(good), good)
        for bad in (99999, 1.5, 1899, 2101, "2020", True, None, float("nan")):
            self.assertFalse(C._valid_tea_year(bad), bad)
        self.assertEqual((C.TEA_YEAR_MIN, C.TEA_YEAR_MAX), (1900, 2100))

    def test_sys_04_provenance(self):
        """_score_sys_04 / _pinned (CMP-SYS-04): simulator named, nothing
        pinned -> 0.5; plus a pinned package commit -> 1."""
        doc = valid_doc()
        self.assertEqual(run(C._score_sys_04, doc)[0], 0.5)
        doc["metadata"]["reproducibility"] = {
            "simulator_package": {"name": "biosteam", "commit": "abc"}}
        self.assertEqual(run(C._score_sys_04, doc)[0], 1.0)
        self.assertFalse(C._pinned({"name": "x"}))


class TestIntegrityCriteria(unittest.TestCase):
    def test_gate_score(self):
        """_gate_score: pass -> 1; fail -> 0 with the '; '-split message as
        locators; absent -> None."""
        sc = ctx(valid_doc(), [check("SCHEMA", "fail", message="a: x; b: y")])
        self.assertEqual(C._gate_score(sc, "SCHEMA"),
                         (0.0, "SCHEMA fails", ["a: x", "b: y"]))
        self.assertIsNone(C._gate_score(sc, "XREF-01")[0])

    def test_int_01_schema(self):
        """_score_int_01 (CMP-INT-01): SCHEMA pass -> 1; no results -> None."""
        self.assertEqual(run(C._score_int_01, valid_doc(), [check("SCHEMA", "pass")])[0], 1.0)
        self.assertIsNone(run(C._score_int_01, valid_doc())[0])

    def test_int_02_xref(self):
        """_score_int_02 (CMP-INT-02): XREF-01 fail -> 0, locators name the
        failing referential checks (STR-02), not the aggregate."""
        results = [check("STR-02", "fail", message="dangling"),
                   check("XREF-01", "fail", message="a referential check failed")]
        score, _m, missing = run(C._score_int_02, valid_doc(), results)
        self.assertEqual((score, missing), (0.0, ["STR-02: dangling"]))
        self.assertEqual(run(C._score_int_02, valid_doc(),
                             [check("XREF-01", "pass")])[0], 1.0)

    def test_validation_summary(self):
        """_validation_summary (replaces the dropped CMP-INT-03): counts by
        severity then status, canonical order error/warning/info and
        pass/fail/skip, unknown values after; no results -> {}."""
        results = [check("STR-03", "skip"), check("SCHEMA", "fail"),
                   check("UNIT-01", "pass"), check("STR-08", "fail", "warning"),
                   check("CHEM-05", "fail", "info"), check("X", "odd", "weird")]
        summary = C._validation_summary(results)
        self.assertEqual(summary, {"error": {"pass": 1, "fail": 1, "skip": 1},
                                   "warning": {"fail": 1},
                                   "info": {"fail": 1}, "weird": {"odd": 1}})
        self.assertEqual(list(summary["error"]), ["pass", "fail", "skip"])
        self.assertEqual(C._validation_summary([]), {})
        self.assertNotIn("CMP-INT-03", C._CRITERIA)

    def test_int_04_quantity_units(self):
        """_score_int_04 (CMP-INT-04): legacy inline -> fraction with units:
        6 inline quantities (1 with empty units) plus U1's bare design result
        'Flow rate' (_bare_design_results: a unitless quantity, R2.10) ->
        5/7. Current shape (R4.11): QU-01 and QU-02 both pass -> 1; exactly
        one passes -> 0.5, the locator naming the other; both fail -> 0; a
        QU-02 result missing (validator not run for it) earns nothing ->
        0.5 beside a passing QU-01; no QU-01 result -> None."""
        self.assertEqual(C._bare_design_results(ctx(legacy_doc())),
                         ["units[U1].design_results.Flow rate"])
        score, _m, missing = run(C._score_int_04, legacy_doc())
        self.assertAlmostEqual(score, 5 / 7)
        self.assertEqual(missing, ["streams[0].stream_properties.pressure.units",
                                   "units[U1].design_results.Flow rate (bare "
                                   "number, no units)"])
        qu = lambda a, b: [check("QU-01", a), check("QU-02", b,
                                                    message="bad unit 'kgg'")]
        self.assertEqual(run(C._score_int_04, valid_doc(), qu("pass", "pass"))[0], 1.0)
        score, _m, missing = run(C._score_int_04, valid_doc(), qu("pass", "fail"))
        self.assertEqual(score, 0.5)
        self.assertEqual(len(missing), 1)
        self.assertTrue(missing[0].startswith("QU-02 fails"))
        self.assertIn("bad unit 'kgg'", missing[0])
        self.assertEqual(run(C._score_int_04, valid_doc(), qu("fail", "pass"))[0], 0.5)
        self.assertEqual(run(C._score_int_04, valid_doc(), qu("fail", "fail"))[0], 0.0)
        score, _m, missing = run(C._score_int_04, valid_doc(),
                                 [check("QU-01", "pass")])
        self.assertEqual(score, 0.5)
        self.assertTrue(missing[0].startswith("QU-02 not evaluated"))
        self.assertIsNone(run(C._score_int_04, valid_doc())[0])


# ----------------------------------------------------- driver + aggregation

class TestDriver(unittest.TestCase):
    def test_run_criterion_isolates_failures(self):
        """_run_criterion: unregistered id -> 0 'no scorer registered'; a
        raising scorer -> 0 'criterion raised ...'; out-of-range scores are
        clamped to [0, 1]; None stays None."""
        sc = ctx(valid_doc())
        self.assertEqual(C._run_criterion("CMP-NOPE-01", sc)[:2],
                         (0.0, "no scorer registered for CMP-NOPE-01"))
        saved = dict(C._CRITERIA)
        try:
            C._CRITERIA["CMP-X-01"] = lambda sc: 1 / 0
            C._CRITERIA["CMP-X-02"] = lambda sc: (7, "big", ())
            C._CRITERIA["CMP-X-03"] = lambda sc: (None, "na", [])
            score, message, _m = C._run_criterion("CMP-X-01", sc)
            self.assertEqual(score, 0.0)
            self.assertIn("criterion raised ZeroDivisionError", message)
            self.assertEqual(C._run_criterion("CMP-X-02", sc), (1.0, "big", []))
            self.assertIsNone(C._run_criterion("CMP-X-03", sc)[0])
        finally:
            C._CRITERIA.clear()
            C._CRITERIA.update(saved)

    def test_evaluate_criteria_representable_filtering(self):
        """_evaluate_criteria at file version 0.0.3: CMP-COST-04 (since
        0.1.2) and CMP-THM-05 (null) not representable, CMP-THM-01 and
        CMP-THM-04 (since 0.0.0, R4.5) are; an inapplicable criterion ->
        status not_applicable, score None."""
        registry = C.load_completeness_criteria()["criteria"]
        results = {r.criterion_id: r for r in
                   C._evaluate_criteria(ctx(label_mixer_doc()), registry,
                                        (0, 0, 3))}
        self.assertFalse(results["CMP-COST-04"].representable)
        self.assertFalse(results["CMP-THM-05"].representable)
        self.assertTrue(results["CMP-THM-04"].representable)
        self.assertTrue(results["CMP-THM-01"].representable)
        self.assertEqual((results["CMP-RXN-02"].status, results["CMP-RXN-02"].score),
                         ("not_applicable", None))
        self.assertEqual(results["CMP-THM-01"].status, "scored")

    def test_weighted_mean_and_group_scores(self):
        """_weighted_mean: sum(w*s)/sum(w) over scored only; nothing scored ->
        None. _group_scores: per-key means, None for empty groups. _mean:
        plain mean, None when empty."""
        mk = lambda cat, w, s, st="scored": C.CriterionResult(
            "id", cat, "essential", w, True, st, s, "", [])
        results = [mk("a", 1, 1.0), mk("a", 3, 0.0), mk("b", 5, None, "not_applicable")]
        self.assertAlmostEqual(C._weighted_mean(results), 0.25)
        self.assertIsNone(C._weighted_mean(results[2:]))
        self.assertEqual(C._group_scores(results, "category", ("a", "b")),
                         {"a": 0.25, "b": None})
        self.assertEqual(C._mean([1, 0]), 0.5)
        self.assertIsNone(C._mean([]))

    def test_recipe_route(self):
        """_recipe_route: no reproducibility -> present False, digests None;
        env + load script content, MET-07 pass, 'reproducible' tag -> all
        True."""
        self.assertEqual(C._recipe_route(ctx(valid_doc())),
                         {"present": False, "digests_valid": None,
                          "reproducible_declared": False})
        doc = valid_doc()
        doc["metadata"]["reproducibility"] = {"environment": {"content": "x"},
                                              "load_script": {"content": "y"}}
        doc["metadata"]["tags"] = ["reproducible"]
        self.assertEqual(C._recipe_route(ctx(doc, [check("MET-07", "pass")])),
                         {"present": True, "digests_valid": True,
                          "reproducible_declared": True})

    def test_run_validator(self):
        """_run_validator: a normal document -> SCHEMA first, TAG-01 last; a
        validator post-pass that raises -> [SCHEMA gate, 'VALIDATOR' error
        fail naming the exception] instead of an exception."""
        schema = json.loads(Path(C._validator()._SCHEMA_FILE).read_text(
            encoding="utf-8"))
        results = C._run_validator(unitless_doc(), schema)
        self.assertEqual((results[0].check_id, results[-1].check_id),
                         ("SCHEMA", "TAG-01"))
        validator = C._validator()
        saved = validator._tag_gate
        validator._tag_gate = lambda ctx, results: 1 / 0
        try:
            results = C._run_validator(unitless_doc(), schema)
        finally:
            validator._tag_gate = saved
        self.assertEqual([r.check_id for r in results], ["SCHEMA", "VALIDATOR"])
        self.assertIn("ZeroDivisionError", results[1].message)

    def test_read_flowsheet(self):
        """_read_flowsheet: dict -> the same object (not copied/mutated); a
        path -> parsed JSON; anything else -> TypeError."""
        doc = valid_doc()
        self.assertIs(C._read_flowsheet(doc), doc)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "f.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(C._read_flowsheet(path), doc)
            self.assertEqual(C._read_flowsheet(str(path)), doc)
        with self.assertRaises(TypeError):
            C._read_flowsheet(42)


class TestScoreFlowsheet(unittest.TestCase):
    """score_flowsheet_completeness end to end on documents that carry no
    quantity-unit strings (see the module header): structure only."""

    def test_report_structure_and_dict_path_parity(self):
        """score_flowsheet_completeness(dict) == (path) scores; one
        CriterionResult per registry criterion; scores in [0, 1];
        representable_score excludes non-representable criteria."""
        doc = unitless_doc()
        before = copy.deepcopy(doc)
        report = C.score_flowsheet_completeness(doc)
        self.assertEqual(doc, before)  # input never mutated
        self.assertEqual([c.criterion_id for c in report.criteria],
                         list(C.load_completeness_criteria()["criteria"]))
        self.assertTrue(0.0 <= report.score <= 1.0)
        self.assertGreater(report.representable_score, report.score)
        self.assertEqual(set(report.criticality_scores), set(C.CRITICALITIES))
        self.assertEqual(report.sff_version, "0.0.12")
        self.assertEqual(report.validation_summary,
                         C._validation_summary(report.validation_results))
        self.assertIn("error", report.validation_summary)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "f.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            again = C.score_flowsheet_completeness(path)
        self.assertEqual(again.score, report.score)
        self.assertEqual(again.representable_score, report.representable_score)

    def test_version_drives_representability(self):
        """sff_version 0.0.3 -> CMP-COST-04 not representable; an unparseable
        version -> treated as the schema version (representable)."""
        doc = unitless_doc()
        mutate(doc, "metadata/sff_version", "0.0.3")
        rep = {c.criterion_id: c.representable
               for c in C.score_flowsheet_completeness(doc).criteria}
        self.assertFalse(rep["CMP-COST-04"])
        mutate(doc, "metadata/sff_version", "garbage")
        rep = {c.criterion_id: c.representable
               for c in C.score_flowsheet_completeness(doc).criteria}
        self.assertTrue(rep["CMP-COST-04"])

    def test_never_raises_on_malformed_content(self):
        """Malformed SFF content (wrong section types, junk elements) ->
        a report whose feed criterion scores 0; no exception."""
        report = C.score_flowsheet_completeness(
            {"metadata": 5, "units": "x", "streams": [1, None], "chemicals": {}})
        by_id = {c.criterion_id: c for c in report.criteria}
        self.assertEqual(by_id["CMP-FEED-01"].score, 0.0)
        self.assertFalse(report.is_valid)
        self.assertEqual(C.score_flowsheet_completeness({}).recipe["present"], False)


class TestFormattingAndCli(unittest.TestCase):
    def setUp(self):
        self.report = C.score_flowsheet_completeness(unitless_doc())

    def test_fmt_helpers(self):
        """_fmt: None -> 'n/a', 0.5 -> '0.500'. _fmt_recipe: absent recipe ->
        'absent ...'; present -> mentions digests and the tag."""
        self.assertEqual(C._fmt(None), "n/a")
        self.assertEqual(C._fmt(0.5), "0.500")
        self.assertTrue(C._fmt_recipe({"present": False}).startswith("absent"))
        text = C._fmt_recipe({"present": True, "digests_valid": True,
                              "reproducible_declared": True})
        self.assertIn("digests valid", text)
        self.assertIn("'reproducible' declared", text)
        # _fmt_validation: severity groups joined by '; ', statuses by ' / '
        self.assertEqual(C._fmt_validation({"error": {"pass": 3, "fail": 1},
                                            "info": {"fail": 2}}),
                         "error 3 pass / 1 fail; info 2 fail")
        self.assertEqual(C._fmt_validation({}), "no validator results")

    def test_format_completeness_report(self):
        """format_completeness_report: header, every criterion id, category
        titles; max_missing=1 -> '... and N more' tails; max_missing=0 lists
        no locators."""
        text = C.format_completeness_report(self.report, max_missing=1)
        self.assertIn("SFF completeness report", text)
        self.assertIn("validator checks    : error ", text)
        for cid in C._CRITERIA:
            self.assertIn(cid, text)
        self.assertIn("Thermodynamic basis", text)
        self.assertIn("more", text)
        self.assertNotIn("      - ", C.format_completeness_report(self.report, 0))

    def test_report_to_jsonable(self):
        """_report_to_jsonable -> json.dumps-able dict with criteria as dicts
        and the validation_summary as {severity: {status: count}}."""
        data = json.loads(json.dumps(C._report_to_jsonable(self.report)))
        self.assertEqual(data["criteria"][0]["criterion_id"], "CMP-THM-01")
        self.assertIn("check_id", data["validation_results"][0])
        self.assertEqual(data["validation_summary"], self.report.validation_summary)

    def test_main_text_and_json(self):
        """main([file]) -> 0 and the text report; main([file, '--json']) ->
        parseable JSON with a 'score'."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "f.json"
            path.write_text(json.dumps(unitless_doc()), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(C.main([str(path)]), 0)
            self.assertIn("SFF completeness report", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                C.main([str(path), "--json"])
            data = json.loads(out.getvalue())
            self.assertIn("score", data)
            self.assertIn("validation_summary", data)


# ------------------------------------ docs rendering (criteria reference)

DOCS_PAGE = (Path(__file__).resolve().parents[2] / "docs" / "validation"
             / "completeness.md")
BEGIN_MARKER = "<!-- BEGIN GENERATED: completeness criteria -->"
END_MARKER = "<!-- END GENERATED: completeness criteria -->"
REGENERATE = ("regenerate with: python pisces_sff/completeness/_completeness.py "
              "--render-docs, and paste the output between the GENERATED "
              "markers of docs/validation/completeness.md")


def _criterion_entry(cid, category, since, title="T", rationale="R", weight=1):
    return {"id": cid, "category": category, "title": title, "weight": weight,
            "criticality": "essential", "representable_since": since,
            "rationale": rationale, "sff_paths": ["units[]"]}


class TestDocsRendering(unittest.TestCase):
    def test_md_text(self):
        """_md_text: whitespace runs (newlines included) collapse to one space;
        markup characters are backslash-escaped; plain prose is untouched."""
        self.assertEqual(C._md_text("a  b\n c\t"), "a b c")
        self.assertEqual(C._md_text("Plain prose, 1.5 kg/hr (K or degC)."),
                         "Plain prose, 1.5 kg/hr (K or degC).")
        self.assertEqual(
            C._md_text("x|y *z* design_input_specs <T> [a] `c` {r} $ ~ \\"),
            r"x\|y \*z\* design\_input\_specs \<T\> \[a\] \`c\` \{r\} \$ \~ \\")

    def test_render_committed_registry(self):
        """_render_criteria_markdown() on the committed registry: deterministic,
        LF-only, ends in exactly one newline; one table row and one rationale
        definition per criterion, one ### heading per category, one table row
        per exclusion, and the summary total equals the sum of the weights."""
        registry = C.load_completeness_criteria()
        text = C._render_criteria_markdown()
        self.assertEqual(text, C._render_criteria_markdown(registry))
        self.assertNotIn("\r", text)
        self.assertTrue(text.endswith("|\n") and not text.endswith("\n\n"))
        total = sum(c["weight"] for c in registry["criteria"].values())
        self.assertTrue(text.startswith(
            f"{len(registry['criteria'])} criteria in "
            f"{len(registry['categories'])} categories, total weight {total:g}."))
        for title in registry["categories"].values():
            self.assertEqual(text.count(f"\n### {C._md_text(title)}\n"), 1)
        for cid, entry in registry["criteria"].items():
            self.assertEqual(text.count(f"| {cid} | "), 1, cid)
            self.assertIn(f"\n{cid} -- {C._md_text(entry['title'])}\n: "
                          f"{C._md_text(entry['rationale'])}\n", text)
        for entry in registry["exclusions"]:
            self.assertIn(f"| {entry['id']} | `{entry['kind']}` | "
                          f"{C._md_text(entry['title'])} | "
                          f"{C._md_text(entry['rationale'])} |", text)

    def test_render_synthetic_registry(self):
        """A synthetic registry: representable_since null -> 'never', '0.0.0'
        -> 'all versions', any other version verbatim; a '|' or newline in a
        title/rationale can add neither a column nor a row; an empty category
        is summarized (0 criteria) but gets no heading; shares use the total
        weight."""
        registry = {
            "categories": {"a": "Alpha", "b": "Beta | gamma", "c": "Empty"},
            "criteria": {
                "CMP-A-01": _criterion_entry("CMP-A-01", "a", None, weight=3,
                                             rationale="Line one\nline | two"),
                "CMP-A-02": _criterion_entry("CMP-A-02", "a", "0.0.0"),
                "CMP-B-01": _criterion_entry("CMP-B-01", "b", "0.1.2",
                                             title="Pipe | title")},
            "exclusions": [{"id": "EXC-01", "kind": "derived", "title": "X",
                            "rationale": "Why | not\nscored"}]}
        text = C._render_criteria_markdown(registry)
        self.assertIn("| CMP-A-01 | T | 3 | essential | never |", text)
        self.assertIn("| CMP-A-02 | T | 1 | essential | all versions |", text)
        self.assertIn(r"| CMP-B-01 | Pipe \| title | 1 | essential | 0.1.2 |",
                      text)
        self.assertIn(": Line one line \\| two\n", text)
        self.assertIn(r"| EXC-01 | `derived` | X | Why \| not scored |", text)
        self.assertIn("| `a` | Alpha | 2 | 4 | 80.0 % |", text)
        self.assertIn(r"| `b` | Beta \| gamma | 1 | 1 | 20.0 % |", text)
        self.assertIn("| `c` | Empty | 0 | 0 | 0.0 % |", text)
        self.assertNotIn("### Empty", text)
        # every table row keeps its column count (escaped pipes do not split)
        for line in text.splitlines():
            if line.startswith("| CMP-") or line.startswith("| EXC-"):
                cells = line.replace(r"\|", "").count("|")
                self.assertEqual(cells, 6 if line.startswith("| CMP-") else 5,
                                 line)

    def test_docs_page_block_in_sync(self):
        """The committed docs/validation/completeness.md carries exactly one
        GENERATED block, LF-only, and the text between its markers (one blank
        line after BEGIN, the block, one blank line before END) byte-matches a
        fresh _render_criteria_markdown() of the committed registry."""
        page = DOCS_PAGE.read_bytes().decode("utf-8")
        self.assertNotIn("\r", page)
        self.assertEqual(page.count(BEGIN_MARKER), 1)
        self.assertEqual(page.count(END_MARKER), 1)
        block = page.split(BEGIN_MARKER + "\n\n", 1)[1]
        block = block.split("\n" + END_MARKER, 1)[0]
        self.assertEqual(block, C._render_criteria_markdown(),
                         f"{DOCS_PAGE.name} is stale -- {REGENERATE}")

    def test_main_render_docs(self):
        """main(['--render-docs']) prints exactly the rendered block and
        returns 0 (no FILE needed); main([]) is a usage error (SystemExit)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(C.main(["--render-docs"]), 0)
        self.assertEqual(out.getvalue(), C._render_criteria_markdown())
        with contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit):
            C.main([])


# ------------------------------------ need-based applicability (R1.0)

PASSING = (check("SCHEMA", "pass"), check("XREF-01", "pass"), check("QU-01", "pass"),
           check("QU-02", "pass"))
RICH_NAMES = {"Water", "Glucose", "Ethanol", "CO2"}


def overall(doc):
    """Overall score of `doc` from the criterion functions (validator
    replaced by passing SCHEMA / XREF-01 / QU-01 / QU-02 results)."""
    criteria = C.load_completeness_criteria()["criteria"]
    return C._weighted_mean(C._evaluate_criteria(ctx(doc, PASSING), criteria, None))


def criterion_results(doc):
    """{criterion id: CriterionResult} of `doc`, validator replaced as in
    overall()."""
    criteria = C.load_completeness_criteria()["criteria"]
    return {r.criterion_id: r for r in
            C._evaluate_criteria(ctx(doc, PASSING), criteria, None)}


# Descriptive unit_type of each rich_doc() class (corpus style).
UNIT_TYPES = {"CleaningSystem": "Cleaning system", "Mixer": "Mixer",
              "Pump": "Pump", "HXutility": "Heat exchanger",
              "SinglePhaseReactor": "Fermentation"}


RICH_MW = {"Water": 18.015, "Glucose": 180.156, "Ethanol": 46.07, "CO2": 44.01}


def _liquid(flows):
    """Stream properties of a single-liquid-phase stream with the given
    {component: kmol/h} flows (mass flow from RICH_MW, so balances close)."""
    total = sum(flows.values())
    comp = [{"component_name": n, "mol_fraction": f / total}
            for n, f in flows.items()]
    mass = sum(f * RICH_MW[n] for n, f in flows.items())
    return {"total_molar_flow": total, "total_mass_flow": mass,
            "enthalpy_flow": -1.0e5, "phases": {"l": {
                "total_molar_flow": total, "composition": comp}}}


def rich_doc(single_thermo=False):
    """A consistent, richly stated flowsheet: feedA -> C1 (CleaningSystem,
    pinned Bioindustrial-Park class: custom, non-core) -> s0 + waste
    (unpriced); s0 + feedB -> M1 (Mixer) -> P1 (Pump) -> H1 (HXutility,
    steam) -> R1 (SinglePhaseReactor, cooling water, two indexed series
    passes of Glucose -> 2 Ethanol + 2 CO2; its stirred-tank batch flag
    stated, R2.8) -> prod. unit_types are
    DESCRIPTIVE ('Cleaning system', 'Heat exchanger',
    'Fermentation', ...), as in the corpus, so the class lives only in
    design_simulation_method ('<Class> on <url>'; 'Mixer' and 'Pump' happen
    to name their class exactly). Costed units carry costs, power-law
    (@cost-style) correlations with lifetimes and unit-annotated design
    results; the custom C1 has a second, custom_function item (so its
    CMP-COST-04 credit is 0.75 and a core unit leaving the core-class path
    with a power-law item would out-score it). An empty, priced placeholder
    feed ('spare', zero flow) enters M1. Thermo: C1, M1, P1 and H1 share one
    full package with an id; R1 alone runs a second one without an id;
    `single_thermo` instead gives every unit the same full package WITHOUT
    an id (the CMP-THM-07 derived-credit case). Package descriptors are real
    thermosteam class names (R2.0), and every unit's mass balance -- and
    each species' for the physical C1, M1, P1, H1 -- closes (R2.2), so the
    derivations from stream data are available when an explicit spec is
    deleted."""
    wg = {"Water": 90.0, "Glucose": 10.0}
    mixed = {"Water": 140.0, "Glucose": 10.0}
    # R1: 10 Glucose, conversions 0.9 then 0.5 -> 0.5 left, 19 Ethanol + 19 CO2
    sugar = {"Water": 140.0, "Glucose": 0.5, "Ethanol": 19.0, "CO2": 19.0}
    edges = [("feedA", "None", "C1", {"Water": 180.0, "Glucose": 20.0}, 300.0,
              101325.0, 0.05),
             ("waste", "C1", "None", wg, 300.0, 101325.0, None),
             ("s0", "C1", "M1", wg, 300.0, 101325.0, None),
             ("feedB", "None", "M1", {"Water": 50.0}, 300.0, 101325.0, 0.001),
             ("spare", "None", "M1", {}, 300.0, 101325.0, 0.02),
             ("s1", "M1", "P1", mixed, 300.0, 101325.0, None),
             ("s2", "P1", "H1", mixed, 300.0, 300000.0, None),
             ("s3", "H1", "R1", mixed, 305.0, 300000.0, None),
             ("prod", "R1", "None", sugar, 305.0, 300000.0, 0.7)]
    streams = []
    for sid, source, sink, flows, T, P, price in edges:
        sp = (_liquid(flows) if flows else
              {"total_molar_flow": 0.0, "total_mass_flow": 0.0,
               "enthalpy_flow": 0.0, "phases": {}})
        sp.update(temperature=T, pressure=P)
        stream = {"id": sid, "source_unit_id": source, "sink_unit_id": sink,
                  "stream_properties": sp}
        if price is not None:
            stream["price"] = price
        streams.append(stream)

    pkg_a = dict(FULL_PKG, id="set_a")
    pkg_b = dict(UNIFAC_PKG)
    if single_thermo:
        pkg_a = {k: v for k, v in pkg_a.items() if k != "id"}
        pkg_b = pkg_a

    def unit(uid, name, url, specs, item, results, pkg, cons=None, prod=None):
        corr = dict(POWER_LAW, basis="Volume", lifetime=20)
        return {"id": uid, "unit_type": UNIT_TYPES[name],
                "design_simulation_method": f"{name} on {url}",
                "design_input_specs": specs,
                "design_results": dict(results, Volume=12.0),
                "quantity_units_for_design_results": {
                    k: "m3" if k == "Volume" else "" for k in
                    dict(results, Volume=12.0)},
                "purchase_costs": {item: 1000.0} if item else {},
                "installed_costs": {item: 2000.0} if item else {},
                "purchase_cost_correlations": {item: corr} if item else {},
                "thermo_property_package": dict(pkg),
                "utility_consumption_results": cons or {},
                "utility_production_results": prod or {}}

    units = [
        unit("C1", "CleaningSystem", BIP_URL, {"split": 0.9}, "System",
             {"Flow": 5.0}, pkg_a, cons={"grid": 1.0}),
        unit("M1", "Mixer", BST_URL, {}, None, {}, pkg_a),
        unit("P1", "Pump", BST_URL, {"P": 300000.0}, "Pump",
             {"Power": 2.0}, pkg_a, cons={"grid": 2.0}),
        unit("H1", "HXutility", BST_URL, {"T": 305.0}, "Double pipe",
             {"Area": 3.0}, pkg_a, cons={"steam": 500.0}),
        unit("R1", "SinglePhaseReactor", BST_URL,
             {"T": 305.0, "tau": 10.0, "V_wf": 0.8, "batch": False}, "Reactor",
             {"Number of reactors": 2, "Residence time": 10.0}, pkg_b,
             prod={"cooling_water": -300.0}),
    ]
    units[4]["reactions"] = [{
        "index": 0, "equation": "Glucose -> 2 Ethanol + 2 CO2",
        "stoichiometry": {"Glucose": -1.0, "Ethanol": 2.0, "CO2": 2.0},
        "reactant": "Glucose", "conversion": 0.9}]
    units[4]["reactions"].append(dict(units[4]["reactions"][0], index=1,
                                      conversion=0.5))
    c1 = units[0]
    c1["purchase_costs"]["Conveyor"] = 400.0
    c1["installed_costs"]["Conveyor"] = 800.0
    c1["purchase_cost_correlations"]["Conveyor"] = {
        "correlation_type": "custom_function", "lifetime": 10}

    chemicals = [{"id": n, "registry_id": cas, "formula": f, "molar_mass": mw,
                  "included_in_thermo": True}
                 for n, cas, f, mw in (("Water", "7732-18-5", "H2O", 18.015),
                                       ("Glucose", "50-99-7", "C6H12O6", 180.156),
                                       ("Ethanol", "64-17-5", "C2H6O", 46.07),
                                       ("CO2", "124-38-9", "CO2", 44.01))]
    water = lambda phase: [{"phase": phase, "component_name": "Water",
                            "mol_fraction": 1.0}]
    utilities = {
        "heat_utilities": [
            {"id": "steam", "temperature": 412.0, "pressure": 344738.0,
             "composition": water("g"), "regeneration_price": 0.2,
             "heat_transfer_efficiency": 0.95,
             "quantity_units_for_utility_results": "kJ/hr"},
            {"id": "cooling_water", "temperature": 305.0, "pressure": 101325.0,
             "composition": water("l"), "regeneration_price": 5e-4,
             "heat_transfer_efficiency": 1.0, "temperature_limit": 324.0,
             "quantity_units_for_utility_results": "kJ/hr"}],
        "power_utilities": [{"id": "grid", "electrical_energy_price": 0.07,
                             "quantity_units_for_utility_results": "kW"}],
        "other_utilities": []}
    metadata = {
        "sff_version": "0.2.1", "TEA_currency": "USD", "TEA_year": 2020,
        "process_simulator": {"name": "BioSTEAM", "version": "2.54.0"},
        "feedstocks": [{"stream_id": "feedA"}], "products": [{"stream_id": "prod"}],
        "reproducibility": {
            "simulator_package": {"name": "biosteam", "commit": "abc"},
            "flowsheet_model_package": {"name": "biorefineries",
                                        "url": BIP_REPO, "commit": "def"}}}
    return {"metadata": metadata, "units": units, "streams": streams,
            "chemicals": chemicals, "utilities": utilities}


# Keys that carry identity or references rather than values: deleting them
# rewires the document (a stream's endpoints, a composition's species, an
# element's id) instead of removing one piece of stated information.
_STRUCTURAL_KEYS = frozenset({"id", "source_unit_id", "sink_unit_id",
                              "component_name", "stream_id"})


def deletable_paths(node, path=(), skip=_STRUCTURAL_KEYS):
    """Yield (path, kind) for every value held under a dict key, skipping
    the keys in `skip` (default _STRUCTURAL_KEYS; frozenset() keeps ids,
    stream endpoints and component names too, R3.8): kind 'leaf' for a
    scalar, 'sub' for a whole dict or
    list (a unit's design_input_specs or thermo_property_package, a stream's
    stream_properties, one phase, a composition list, a correlation, ...,
    up to a top-level section). List elements are recursed into, never
    deleted themselves (that removes a unit, stream or reaction)."""
    items = (node.items() if isinstance(node, dict)
             else enumerate(node) if isinstance(node, list) else ())
    for key, value in items:
        if isinstance(node, dict) and key in skip:
            continue
        if isinstance(value, (dict, list)):
            if isinstance(node, dict):
                yield path + (key,), "sub"
            yield from deletable_paths(value, path + (key,), skip)
        elif isinstance(node, dict):
            yield path + (key,), "leaf"


def deleted(doc, path):
    """Deep copy of `doc` without the leaf at `path`."""
    doc = copy.deepcopy(doc)
    node = doc
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return doc


class TestNeedBasedApplicability(unittest.TestCase):
    """R1.0: populations follow structural need, so adding information never
    lowers the score and deleting it never raises the score."""

    def test_names_only_chemicals_never_lower_score(self):
        """overall(): a doc whose stream compositions name Ethanol but that
        declares no chemicals scores <= the same doc with a names-only
        chemicals list (THM-02..05 score the undeclared id 0 instead of
        going not-applicable), with and without the name tier."""
        for resolvable in (set(), {"Ethanol"}):
            with name_lookup(resolvable):
                doc = valid_doc()
                remove(doc, "chemicals")
                before = overall(doc)
                doc["chemicals"] = [{"id": "Ethanol"}]
                self.assertGreaterEqual(overall(doc), before, resolvable)

    def test_purchase_costs_alone_never_lower_score(self):
        """overall(): adding only purchase_costs to a costed unit (core Pump,
        or a custom tank that COST-04 applies to) never lowers the score
        (COST-02/04/05/06 no longer turn applicable on the new items)."""
        with name_lookup(RICH_NAMES):
            for fields in (spec_unit("Pump"), spec_unit("CustomTank", url=CUSTOM_URL)):
                doc = unit_doc(fields)
                before = overall(doc)
                doc["units"][0]["purchase_costs"] = {"Item": 1000.0}
                self.assertGreaterEqual(overall(doc), before, fields)

    def test_rich_doc_is_rich(self):
        """rich_doc(): every need-based criterion applies (only THM-05 is
        not_applicable: every chemical resolves; and CMP-UOP-06: the
        document has no facility), and the overall score is high but below
        1 (non-representable criteria); every unit is fully specified
        (CMP-UOP-02 = 1, R1's batch flag included). R6.3: the empty 'spare'
        feed enters the Mixer M1, whose inlet count is variable, so it is
        no placeholder port: CMP-FEED-01 = (1 + 1 + 0.5) / 3 (spare: T and P
        only) and its locator says why."""
        with name_lookup(RICH_NAMES):
            results = {r.criterion_id: r for r in C._evaluate_criteria(
                ctx(rich_doc(), PASSING),
                C.load_completeness_criteria()["criteria"], None)}
            score = overall(rich_doc())
        na = {cid for cid, r in results.items() if r.status == "not_applicable"}
        self.assertEqual(na, {"CMP-THM-05", "CMP-UOP-06"})
        self.assertEqual(results["CMP-UOP-02"].score, 1.0)
        self.assertAlmostEqual(results["CMP-FEED-01"].score, 2.5 / 3)
        self.assertIn("whose Mixer inlet count is not fixed",
                      " ".join(results["CMP-FEED-01"].missing))
        # 'waste' unpriced; the empty 'spare' feed is priced (4 of 5).
        self.assertEqual(results["CMP-FEED-02"].score, 0.8)
        self.assertEqual(results["CMP-RXN-03"].score, 1.0)
        # C1 (custom) 0.75; P1, H1, R1 core -> 1 (unweighted, R4.12).
        self.assertAlmostEqual(results["CMP-COST-04"].score, 3.75 / 4)
        self.assertEqual(results["CMP-COST-07"].score, 1.0)
        # R1 names no chemical set; 4 of 5 units do (unweighted, R4.12).
        self.assertAlmostEqual(results["CMP-THM-07"].score, 4 / 5)
        self.assertTrue(0.8 < score < 1.0, score)

    def test_deleting_any_value_never_raises_score(self):
        """Deletion monotonicity: for every value of rich_doc() -- each
        scalar leaf AND each whole sub-object (bar ids and references, see
        _STRUCTURAL_KEYS) -- in both thermo variants, deleting it leaves the
        overall score <= the full document's, AND leaves every scored
        criterion scored with a score <= its full-document score (so a
        per-criterion rise cannot hide behind a compensating drop
        elsewhere). A whole top-level section (all units, all streams) is
        held to the overall bound only: removing it removes the structure
        the populations are defined on. Exercised on purpose: every unit's
        design_simulation_method (descriptive unit_types, so the class is
        lost or only inferred -- CMP-COST-04 / CMP-UOP-03), C1's @cost and
        custom_function correlations, core units' correlations, package
        descriptors, ids and whole packages (CMP-THM-07), the empty priced
        feed's flow and stream_properties (CMP-FEED-01/02), R1's reactions
        (CMP-RXN-03), whole design_input_specs / stream_properties /
        phases. Also: >150 paths, and more than half of the deletions lower
        the overall score (the test has teeth). Expected: no violation."""
        with name_lookup(RICH_NAMES):
            for single_thermo in (False, True):
                doc = rich_doc(single_thermo)
                base = criterion_results(doc)
                base_score = C._weighted_mean(list(base.values()))
                raised, lowered = [], 0
                paths = [p for p, _kind in deletable_paths(doc)]
                for path in paths:
                    after = criterion_results(deleted(doc, path))
                    score = C._weighted_mean(list(after.values()))
                    if score > base_score + 1e-12:
                        raised.append((path, "overall", score - base_score))
                    lowered += score < base_score - 1e-12
                    if len(path) == 1:
                        continue
                    for cid, before in base.items():
                        now = after[cid]
                        if before.status == "scored" and (
                                now.status != "scored"
                                or now.score > before.score + 1e-12):
                            raised.append((path, cid, now.status, now.score))
                with self.subTest(single_thermo=single_thermo):
                    self.assertGreater(len(paths), 150)
                    self.assertEqual(raised, [])
                    self.assertGreater(lowered, len(paths) // 2)

    def test_deletion_paths_cover_the_known_defects(self):
        """deletable_paths(rich_doc()) includes the deletions behind the
        fixed monotonicity defects (a core and the custom unit's
        design_simulation_method, C1's correlations, a package descriptor,
        R1's id-less package and its reactions, the empty feed's flow and
        stream_properties) and whole sub-objects (design_input_specs,
        thermo_property_package, stream_properties, a phase) but no id;
        and deleting H1's or R1's design_simulation_method really loses the
        exact class (confidence < 1), so the monotonicity test exercises the
        class-unknown branches."""
        doc = rich_doc()
        paths = dict(deletable_paths(doc))
        for path, kind in (
                (("units", 4, "design_simulation_method"), "leaf"),
                (("units", 0, "design_simulation_method"), "leaf"),
                (("units", 0, "purchase_cost_correlations", "System"), "sub"),
                (("units", 0, "purchase_cost_correlations", "Conveyor"), "sub"),
                (("units", 1, "thermo_property_package", "gamma"), "leaf"),
                (("units", 4, "thermo_property_package"), "sub"),
                (("units", 4, "reactions"), "sub"),
                (("units", 3, "design_input_specs"), "sub"),
                (("streams", 4, "stream_properties", "total_mass_flow"), "leaf"),
                (("streams", 4, "stream_properties"), "sub"),
                (("streams", 7, "stream_properties", "phases", "l"), "sub")):
            self.assertEqual(paths.get(path), kind, path)
        self.assertNotIn(("units", 0, "id"), paths)
        for position in (3, 4):
            reduced = deleted(doc, ("units", position, "design_simulation_method"))
            self.assertLess(ctx(reduced).unit_classes[position][2], 1.0)

    def test_junk_never_beats_absence(self):
        """R2.0 INVALID == MISSING: for every scalar leaf of rich_doc() (both
        thermo variants) replaced by junk -- a blank string, or an
        unhashable list ['x'] -- no criterion raises, and every criterion
        scores <= its score with that leaf deleted (junk earns nothing
        absence does not). A quantity unit '' is not junk (the schema's
        documented dimensionless sentinel), so only ['x'] replaces those.
        Expected: no violation over >150 leaves."""
        with name_lookup(RICH_NAMES):
            for single_thermo in (False, True):
                doc = rich_doc(single_thermo)
                leaves = [p for p, kind in deletable_paths(doc) if kind == "leaf"]
                violations = []
                for path in leaves:
                    absent = criterion_results(deleted(doc, path))
                    sentinel = "quantity_units_for_design_results" in path
                    for junk in ((["x"],) if sentinel else ("", ["x"])):
                        spoiled = copy.deepcopy(doc)
                        node = spoiled
                        for key in path[:-1]:
                            node = node[key]
                        node[path[-1]] = junk
                        for cid, r in criterion_results(spoiled).items():
                            if "raised" in r.message:
                                violations.append((path, junk, cid, r.message))
                            elif r.score is not None and (
                                    absent[cid].score is None
                                    or r.score > absent[cid].score + 1e-12):
                                violations.append((path, junk, cid, r.score,
                                                   absent[cid].score))
                with self.subTest(single_thermo=single_thermo):
                    self.assertGreater(len(leaves), 150)
                    self.assertEqual(violations, [])


class TestValidityGating(unittest.TestCase):
    """Revision 2 part A: duplicates (R2.1), derivation closure (R2.2) and
    robustness to malformed document values (R2.0)."""

    def test_first_by_id(self):
        """_first_by_id keeps the first element per non-empty string id and
        every element without one (None, '', a list)."""
        elements = [{"id": "a", "n": 1}, {"id": "a", "n": 2}, {"n": 3},
                    {"id": ""}, {"id": ["a"]}, {"id": "b"}]
        self.assertEqual(C._first_by_id(elements),
                         [elements[0], elements[2], elements[3], elements[4],
                          elements[5]])

    def test_duplicates_collapse(self):
        """R2.1: a context over rich_doc() plus 20 copies of H1 (and of
        stream s2, and of the steam agent, across groups) holds each id
        once, and every criterion scores exactly as on rich_doc()."""
        with name_lookup(RICH_NAMES):
            doc = rich_doc()
            base = criterion_results(doc)
            doc["units"] += [copy.deepcopy(doc["units"][3]) for _ in range(20)]
            doc["streams"] += [copy.deepcopy(doc["streams"][6])
                               for _ in range(20)]
            doc["utilities"]["other_utilities"] = [
                copy.deepcopy(doc["utilities"]["heat_utilities"][0])]
            sc = ctx(doc)
            self.assertEqual(len(sc.units), 5)
            self.assertEqual(len(sc.streams), 9)
            self.assertEqual(len(sc.agents), 3)
            after = criterion_results(doc)
        self.assertEqual({k: r.score for k, r in after.items()},
                         {k: r.score for k, r in base.items()})

    def test_closure_and_physical_unit(self):
        """_closure (R2.2): a balanced one-unit doc closes; outlets carrying
        10x the inlet mass -> (False, 'mass balance does not close ...'); a
        stream without total_mass_flow -> False; a physical unit
        (_physical_unit: PHYSICAL_FAMILIES, no well-formed reaction) whose
        species change although the mass closes -> False; a reactor with the
        same streams closes (no per-species test). Results are cached in
        sc.closures."""
        sc = ctx(one_unit_doc(spec_unit("Splitter"), outs=({}, {})))
        self.assertEqual(C._closure(sc, 0), (True, ""))
        self.assertIn(0, sc.closures)
        self.assertTrue(C._physical_unit(sc, 0))
        tenfold = ctx(one_unit_doc(spec_unit("Splitter"),
                                   outs=({"mass": 460.7}, {"mass": 460.7})))
        closes, why = C._closure(tenfold, 0)
        self.assertFalse(closes)
        self.assertIn("mass balance does not close", why)
        doc = one_unit_doc(spec_unit("Pump"))
        del doc["streams"][1]["stream_properties"]["total_mass_flow"]
        self.assertFalse(C._closure(ctx(doc), 0)[0])
        swapped = {"l": {"total_molar_flow": 1.0, "composition": [
            {"component_name": "Water", "mol_fraction": 1.0}]}}
        pump = ctx(one_unit_doc(spec_unit("Pump"), outs=({"phases": swapped},)))
        self.assertIn("species balance does not close",
                      C._closure(pump, 0)[1])
        reactor = ctx(one_unit_doc(spec_unit("SinglePhaseReactor"),
                                   outs=({"phases": swapped},)))
        self.assertFalse(C._physical_unit(reactor, 0))
        self.assertEqual(C._closure(reactor, 0), (True, ""))

    def test_derivations_need_closure(self):
        """R2.2 end to end: the Splitter's split and the HXutility's outlet T
        are derivable (0.75) on balanced docs, unavailable (0) when the
        outlets carry 10x the inlet, with the reason in the locator; TOP-02
        port-order derivation (_port_order_derivable) is blocked too. Each
        unit's second requirement (the plain Splitter's moisture, waived by
        class; the HXutility's phase-change V, waived by equal phase sets on
        balanced ports only) counts as met while waived (R5.9): Splitter
        0.875 / 0.5, HXutility 0.875 / 0 (unbalanced ports un-waive V)."""
        tenfold = ({"mass": 460.7}, {"mass": 460.7})
        self.assertEqual(spec_score("Splitter", outs=({}, {}))[0], 0.875)
        score, missing = spec_score("Splitter", outs=tenfold)
        self.assertEqual(score, 0.5)
        self.assertIn("derivation from stream data unavailable: mass balance",
                      missing[0])
        self.assertEqual(spec_score("HXutility")[0], 0.875)
        self.assertEqual(spec_score("HXutility", outs=({"mass": 460.7},))[0], 0.0)
        sc = ctx(one_unit_doc(spec_unit("Splitter", {"split": [0.5]}),
                              outs=tenfold))
        self.assertFalse(C._port_order_derivable(sc, 0, "outs"))

    def test_malformed_values_never_raise(self):
        """Robustness (R2.0): list-valued ids, endpoints, component names,
        reactants, registry ids, unit types, specs keys' values, result
        values and agent ids across rich_doc() -> every criterion is scored
        or not applicable without any 'criterion raised' message."""
        with name_lookup(RICH_NAMES):
            doc = rich_doc()
            doc["units"][0]["unit_type"] = ["x"]
            doc["units"][1]["design_simulation_method"] = ["x"]
            doc["units"][4]["reactions"][0]["reactant"] = ["Glucose"]
            doc["units"][4]["reactions"][1]["stoichiometry"] = {"Glucose": [1]}
            doc["units"][2]["design_input_specs"] = {"P": {"a": 1}, "x": [[]]}
            doc["units"][2]["utility_consumption_results"] = {"grid": ["x"]}
            doc["units"].append({"id": ["U9"], "reactions": [{"equation": ["x"]}]})
            doc["streams"][0]["sink_unit_id"] = ["C1"]
            doc["streams"][1]["id"] = ["waste"]
            doc["streams"][2]["stream_properties"]["phases"]["l"][
                "composition"][0]["component_name"] = ["Water"]
            doc["chemicals"][0]["registry_id"] = ["7732-18-5"]
            doc["chemicals"].append({"id": ["X"], "index": [0]})
            doc["utilities"]["heat_utilities"][0]["id"] = ["steam"]
            doc["utilities"]["power_utilities"].append({"id": {"a": 1}})
            raised = [(cid, r.message) for cid, r in criterion_results(doc).items()
                      if "raised" in r.message]
        self.assertEqual(raised, [])


class TestAdversarialGaming(unittest.TestCase):
    """The review's gaming cases (R2.0-R2.3): each junk value earns NO
    credit -- the criterion scores exactly as with the junk removed (or 0)."""

    def assert_no_credit(self, doc, junked, criteria):
        """Each criterion scores the same on `junked` as on `doc`."""
        before, after = criterion_results(doc), criterion_results(junked)
        for cid in criteria:
            self.assertEqual(after[cid].score, before[cid].score, cid)

    def test_blank_specs(self):
        """design_input_specs {'T': '', 'tau': ''} on a reactor: no explicit
        key; UOP-01/02/03 score as with no specs at all."""
        doc = unit_doc(spec_unit("SinglePhaseReactor"))
        junked = unit_doc(spec_unit("SinglePhaseReactor", {"T": "", "tau": ""}))
        self.assertEqual(C._explicit_keys(junked["units"][0]), set())
        self.assert_no_credit(doc, junked, ("CMP-UOP-01", "CMP-UOP-02",
                                            "CMP-UOP-03"))

    def test_junk_registry_id(self):
        """registry_id 'x': THM-02 credits the formula tier only (0.25), as
        with no registry_id; THM-05 still needs property data."""
        with name_lookup():
            doc = valid_doc()
            junked = copy.deepcopy(doc)
            junked["chemicals"][0]["registry_id"] = "x"
            self.assertEqual(run(C._score_thm_02, junked)[0],
                             C.FORMULA_ONLY_CREDIT)
            self.assert_no_credit(doc, junked, ("CMP-THM-02", "CMP-THM-05"))

    def test_junk_thermo_package(self):
        """thermo_property_package values 'x' and 'NRTL' (every descriptor):
        THM-06 0 and THM-07 no single-thermo evidence, as with no package."""
        doc = unit_doc(spec_unit("Pump"))
        for junk in ("x", "NRTL"):
            junked = unit_doc(dict(spec_unit("Pump"), thermo_property_package={
                k: junk for k in C._TPP_KEYS}))
            self.assertEqual(run(C._score_thm_06, junked)[0], 0.0)
            self.assert_no_credit(doc, junked, ("CMP-THM-06", "CMP-THM-07"))

    def test_empty_and_junk_reactions(self):
        """reactions [{}] and [{'equation': 'x', 'reactant': 'y',
        'conversion': 0.5}] on a reactor: RXN-01..04 all 0, as with no
        reactions; on an exact mixer they do not even make it reactive
        (N/A)."""
        for reactions in ([{}], [{"equation": "x", "reactant": "y",
                                  "conversion": 0.5}]):
            junked = rxn_doc(reactions)
            for cid in ("CMP-RXN-01", "CMP-RXN-02", "CMP-RXN-03", "CMP-RXN-04"):
                self.assertEqual(criterion_results(junked)[cid].score, 0.0, cid)
            self.assert_no_credit(rxn_doc(None), junked, (
                "CMP-RXN-01", "CMP-RXN-02", "CMP-RXN-03", "CMP-RXN-04"))
            mixer = unit_doc({**cls("Mixer"), "reactions": reactions})
            self.assertIsNone(criterion_results(mixer)["CMP-RXN-01"].score)

    def test_null_purchase_cost_and_text_design_result(self):
        """purchase_costs {'a': null} -> COST-01 0; design_results {'a': 'b'}
        -> COST-03 0 (as with none)."""
        junked = unit_doc(dict(spec_unit("Pump"), purchase_costs={"a": None},
                               design_results={"a": "b"}))
        self.assertEqual(run(C._score_cost_01, junked)[0], 0.0)
        self.assertEqual(run(C._score_cost_03, junked)[0], 0.0)
        self.assert_no_credit(unit_doc(spec_unit("Pump")), junked,
                              ("CMP-COST-01", "CMP-COST-03"))

    def test_implausible_feed(self):
        """A feed at T = -50 K with flow -3 and an unnamed component of
        fraction 0.01 (no pressure): FEED-01 0 -- no part of its state is
        plausible."""
        doc = one_unit_doc(spec_unit("Mixer"), ins=({"T": -50.0, "P": None,
                                                     "mass": -3.0},))
        sp = doc["streams"][0]["stream_properties"]
        sp["total_molar_flow"] = -3.0
        sp["phases"] = {"l": {"total_molar_flow": -3.0,
                              "composition": [{"mol_fraction": 0.01}]}}
        self.assertEqual(run(C._score_feed_01, doc)[0], 0.0)
        self.assertEqual(C._stream_state(doc["streams"][0]),
                         {"T": False, "P": False, "flow": False,
                          "composition": False})

    def test_duplicate_units(self):
        """20 duplicate copies of one unit id never raise the score: rich_doc
        plus 20 copies of its best-specified unit scores exactly the same
        overall."""
        with name_lookup(RICH_NAMES):
            doc = rich_doc()
            base = overall(doc)
            doc["units"] += [copy.deepcopy(doc["units"][4]) for _ in range(20)]
            self.assertEqual(overall(doc), base)

    def test_tenfold_splitter(self):
        """A Splitter whose outlets carry 10x its inlet earns no `split`
        derivation (UOP-02 0.5 for the unit: split 0, the class-waived
        moisture requirement met, R5.9), where a balanced one earns
        DERIVED_CREDIT for split ((DERIVED_CREDIT + 1) / 2 for the unit)."""
        self.assertEqual(spec_score("Splitter", outs=(
            {"mass": 460.7}, {"mass": 460.7}))[0], 0.5)
        self.assertEqual(spec_score("Splitter", outs=({}, {}))[0],
                         (C.DERIVED_CREDIT + 1.0) / 2)

    def test_formula_ids_get_no_verified_name_credit(self):
        """ids 'HP' (declared MW 90.08; the database's 'HP' is
        hematoporphyrin, MW 598.69) and 'C2H6O' (resolves to dimethyl ether)
        get no verified name credit even though the name search resolves
        them: formula tier (0.25) at most, and both stay in THM-05 -- 'HP'
        because the resolved molar mass mismatches (name tier 'mismatch'),
        'C2H6O' because a formula with >= 2 carbon atoms is isomer-ambiguous
        (name tier None). R2.3 corrected: the one-carbon / carbon-free
        formula ids of SF_BST_01 (CO2, O2, CH4, N2, NH3, SO2, CaO, H2SO4,
        H3PO4, P4O10) resolve like names -- verified 0.75 on a matching
        molar mass -- and leave THM-05 (not applicable when all do)."""
        with name_lookup({"HP": (598.68872, "C34H38N4O6"),
                          "C2H6O": (46.06844, "C2H6O")}):
            for chem in ({"id": "HP", "molar_mass": 90.08},
                         {"id": "C2H6O", "formula": "C2H6O"}):
                credit, _note, _tier = C._thm_02_credit(chem, chem["id"])
                self.assertLessEqual(credit, C.FORMULA_ONLY_CREDIT, chem)
            self.assertEqual(C._name_tier({"id": "HP", "molar_mass": 90.08},
                                          "HP"), "mismatch")
            self.assertIsNone(C._name_tier({"id": "C2H6O", "formula": "C2H6O"},
                                           "C2H6O"))
            doc = valid_doc()
            doc["chemicals"] = [{"id": "HP", "molar_mass": 90.08},
                                {"id": "C2H6O", "formula": "C2H6O"}]
            self.assertEqual(run(C._score_thm_05, doc)[0], 0.0)
        small = {"CO2": (44.0095, "CO2"), "O2": (31.9988, "O2"),
                 "CH4": (16.04246, "CH4"), "N2": (28.0134, "N2"),
                 "NH3": (17.03052, "NH3"), "SO2": (64.0638, "O2S"),
                 "CaO": (56.0774, "CaO"), "H2SO4": (98.07848, "H2O4S"),
                 "H3PO4": (97.995182, "H3O4P"), "P4O10": (283.889048, "O10P4")}
        # R3.2: Ethanol's CAS must resolve in the database to exempt it.
        with name_lookup(dict(small, **{"64-17-5": (46.06844, "C2H6O")})):
            chems = [{"id": cid, "molar_mass": mw} for cid, (mw, _f) in small.items()]
            for chem in chems:
                self.assertEqual(C._thm_02_credit(chem, chem["id"])[0],
                                 C.NAME_RESOLVED_CREDIT, chem["id"])
            doc = valid_doc()
            doc["chemicals"] = chems + [{"id": "Ethanol",
                                         "registry_id": "64-17-5",
                                         "molar_mass": 46.07}]
            self.assertIsNone(run(C._score_thm_05, doc)[0])


# ------------------------------------------------- Revision 2 part B helpers

def _phase(symbol, flows):
    """One-phase {symbol: {total_molar_flow, composition}} of the given
    {component: kmol/h} flows."""
    total = sum(flows.values())
    return {symbol: {"total_molar_flow": total, "composition": [
        {"component_name": n, "mol_fraction": f / total}
        for n, f in flows.items()]}}


FACILITY_MW = {"CH4": 16.04, "CaO": 56.08, "Water": 18.015, "CO2": 44.01}


def facility_doc():
    """A consistent flowsheet of facilities: BT1 (BoilerTurbogenerator,
    every setting stated, raising steam: served agent derivable) fed
    natural gas (zero price: a utility port) and lime; PWC1
    (ProcessWaterCenter) binding the declared streams 'pw' / 'mw'; HXN1
    (HeatExchangerNetwork, T_min_app and integrated units) and CT1
    (CoolingTower, agent stated) without ports; CIP1 (CIPpackage,
    cost-only). Every unit names its core class on a biosteam URL."""
    def sp(symbol, flows, T=300.0, P=101325.0):
        return {"temperature": T, "pressure": P,
                "total_molar_flow": sum(flows.values()),
                "total_mass_flow": sum(f * FACILITY_MW[n]
                                       for n, f in flows.items()),
                "phases": _phase(symbol, flows)}
    edges = (("ng", "None", "BT1", "g", {"CH4": 1.0}, 0.0),
             ("lime", "None", "BT1", "s", {"CaO": 1.0}, 0.2),
             ("emis", "BT1", "None", "g", {"CO2": 1.0, "CaO": 0.6364}, 0.0),
             ("mw", "None", "PWC1", "l", {"Water": 10.0}, 0.0005),
             ("pw", "PWC1", "None", "l", {"Water": 10.0}, 0.0))
    streams = [{"id": sid, "source_unit_id": src, "sink_unit_id": snk,
                "stream_properties": sp(symbol, flows), "price": price}
               for sid, src, snk, symbol, flows, price in edges]

    def unit(uid, name, specs, cons=None, prod=None):
        return {"id": uid, "unit_type": name,
                "design_simulation_method": f"{name} on {BST_URL}",
                "design_input_specs": specs,
                "utility_consumption_results": cons or {},
                "utility_production_results": prod or {}}
    units = [
        unit("BT1", "BoilerTurbogenerator",
             {"boiler_efficiency": 0.8, "turbogenerator_efficiency": 0.85,
              "satisfy_system_electricity_demand": True, "fuel_source": "CH4",
              "agent": "steam"},
             cons={"grid": 5.0}, prod={"steam": -1000.0}),
        unit("PWC1", "ProcessWaterCenter",
             {"process_water_streams": ["pw"], "makeup_water_streams": ["mw"]},
             cons={"grid": 1.0}),
        unit("HXN1", "HeatExchangerNetwork",
             {"T_min_app": 5.0, "units": ["BT1", "PWC1"]}),
        unit("CT1", "CoolingTower", {"agent": "cooling_water"},
             cons={"cooling_water": 100.0, "grid": 1.0}),
        unit("CIP1", "CIPpackage", {})]
    water = lambda phase: [{"phase": phase, "component_name": "Water",
                            "mol_fraction": 1.0}]
    utilities = {
        "heat_utilities": [
            dict(STEAM, composition=water("g")),
            {"id": "cooling_water", "temperature": 305.0, "pressure": 101325.0,
             "composition": water("l"), "regeneration_price": 5e-4,
             "heat_transfer_efficiency": 1.0, "temperature_limit": 324.0}],
        "power_utilities": [{"id": "grid", "electrical_energy_price": 0.07}],
        "other_utilities": []}
    chemicals = [{"id": n, "registry_id": cas, "molar_mass": FACILITY_MW[n]}
                 for n, cas in (("CH4", "74-82-8"), ("CaO", "1305-78-8"),
                                ("Water", "7732-18-5"), ("CO2", "124-38-9"))]
    metadata = {"sff_version": "0.2.1", "TEA_currency": "USD", "TEA_year": 2020,
                "process_simulator": {"name": "BioSTEAM", "version": "2.54.0"}}
    return {"metadata": metadata, "units": units, "streams": streams,
            "chemicals": chemicals, "utilities": utilities}


def splitter_doc(class_name="Splitter"):
    """feed (Water 90 + Glucose 10 kmol/h) -> S1, a core splitter-family
    class `class_name` (default the pure Splitter) with an explicit split,
    -> outA + outB; outA also carries 0.01 kmol/h of CO2 that no inlet has,
    so CO2 appears across a physical-only unit without reactions (R2.4
    consistency guard; R5.1: it also contradicts a PURE splitter claim).
    Mass and species balances still close within 1 %, so only the
    appearance evidence trips the guard."""
    mw = {"Water": 18.015, "Glucose": 180.156, "CO2": 44.01}
    def sp(flows):
        return {"temperature": 300.0, "pressure": 101325.0,
                "total_molar_flow": sum(flows.values()),
                "total_mass_flow": sum(f * mw[n] for n, f in flows.items()),
                "phases": _phase("l", flows)}
    edges = (("feed", "None", "S1", {"Water": 90.0, "Glucose": 10.0}),
             ("outA", "S1", "None", {"Water": 45.0, "Glucose": 5.0,
                                     "CO2": 0.01}),
             ("outB", "S1", "None", {"Water": 45.0, "Glucose": 5.0}))
    streams = [{"id": sid, "source_unit_id": src, "sink_unit_id": snk,
                "stream_properties": sp(flows), "price": 0.1}
               for sid, src, snk, flows in edges]
    unit = {"id": "S1", "unit_type": class_name,
            "design_simulation_method": f"{class_name} on {BST_URL}",
            "design_input_specs": {"split": [0.5, 0.5, 1.0]}}
    # R5.4: indexed 0..2, so the 3-element split vector is readable.
    chemicals = [{"id": n, "index": i, "registry_id": cas, "molar_mass": mw[n]}
                 for i, (n, cas) in enumerate((("Water", "7732-18-5"),
                                               ("Glucose", "50-99-7"),
                                               ("CO2", "124-38-9")))]
    return {"metadata": {"sff_version": "0.2.1", "TEA_currency": "USD"},
            "units": [unit], "streams": streams, "chemicals": chemicals,
            "utilities": {"heat_utilities": [], "power_utilities": [],
                          "other_utilities": []}}


def deletion_violations(doc, skip=_STRUCTURAL_KEYS):
    """(raised, lowered, n): every deletable path of `doc` (deletable_paths,
    skipping the keys in `skip`) below the top level whose deletion raises
    the overall score or turns a scored criterion not-applicable or higher,
    the number of deletions that lower the overall score, and the path
    count. Whole top-level sections (all units, all streams) are left to
    rich_doc's overall-only check: removing them removes the structure the
    populations are defined on."""
    base = criterion_results(doc)
    base_score = C._weighted_mean(list(base.values()))
    raised, lowered = [], 0
    paths = [p for p, _kind in deletable_paths(doc, skip=skip) if len(p) > 1]
    for path in paths:
        after = criterion_results(deleted(doc, path))
        score = C._weighted_mean(list(after.values()))
        if score > base_score + 1e-12:
            raised.append((path, "overall", score - base_score))
        lowered += score < base_score - 1e-12
        for cid, before in base.items():
            now = after[cid]
            if before.status == "scored" and (
                    now.status != "scored" or now.score > before.score + 1e-12):
                raised.append((path, cid, now.status, now.score))
    return raised, lowered, len(paths)


class TestRevision2PartB(unittest.TestCase):
    """Revision 2 part B: facility bindings (CMP-UOP-06, R2.5), per-kind
    utility need (R2.7), and deletion monotonicity of the new branches
    (facilities, utility ports by composition, the R2.4 species guard)."""

    def test_facility_kind_and_population(self):
        """_facility_kind: a listed class -> itself (BoilerTurbogenerator,
        ProcessWaterCenter); a cost-only class (CIPpackage) -> 'cost_only';
        a class-less 'Cooling tower' / 'Boiler' unit_type -> CoolingTower /
        Boiler by keyword; an unknown facility ('Utility facility') ->
        None. _facility_population keeps every facility-family unit except
        the cost-only CIP1 (BT1, PWC1, HXN1, CT1 of facility_doc)."""
        sc = ctx(facility_doc())
        kinds = [C._facility_kind(sc, p) for p in range(len(sc.units))]
        self.assertEqual(kinds, ["BoilerTurbogenerator", "ProcessWaterCenter",
                                 "HeatExchangerNetwork", "CoolingTower",
                                 "cost_only"])
        self.assertEqual(C._facility_population(sc), [0, 1, 2, 3])
        for unit_type, kind in (("Cooling tower", "CoolingTower"),
                                ("Boiler", "Boiler"),
                                ("Utility facility", None)):
            sc = ctx(one_unit_doc({"unit_type": unit_type}))
            self.assertEqual(C._facility_kind(sc, 0), kind, unit_type)

    def test_valid_id_list_and_facility_explicit(self):
        """_valid_id_list: a non-empty list of declared ids -> True; empty,
        an undeclared id, a non-list or a non-string id -> False.
        _facility_explicit (R2.5): PWC stream lists count only when every id
        is a declared stream ('in0'/'out0' yes, 'nope' no, [] no); HXN
        'units' only declared unit ids; 'agent' only a declared heat agent;
        _heat_agent_ids lists the declared heat agents; plausible scalar
        settings count as for UOP-02 (a boiler efficiency of 1.5 does
        not)."""
        self.assertTrue(C._valid_id_list(["a"], {"a", "b"}))
        for value in ([], ["c"], "a", [1], None):
            self.assertFalse(C._valid_id_list(value, {"a", "b"}), value)
        def explicit(name, specs, agents=()):
            doc = one_unit_doc(spec_unit(name, specs))
            doc["utilities"]["heat_utilities"] = list(agents)
            sc = ctx(doc)
            return C._facility_explicit(sc, 0), C._heat_agent_ids(sc)
        self.assertEqual(explicit("ProcessWaterCenter", {
            "process_water_streams": ["out0"],
            "makeup_water_streams": ["in0"]})[0],
            {"process_water_streams", "makeup_water_streams"})
        self.assertEqual(explicit("ProcessWaterCenter", {
            "process_water_streams": ["nope"], "makeup_water_streams": []})[0],
            set())
        self.assertEqual(explicit("HeatExchangerNetwork",
                                  {"T_min_app": 5.0, "units": ["U1"]})[0],
                         {"T_min_app", "units"})
        self.assertEqual(explicit("HeatExchangerNetwork",
                                  {"T_min_app": -5.0, "units": ["U9"]})[0],
                         set())
        got, agents = explicit("CoolingTower", {"agent": "steam"}, [STEAM])
        self.assertEqual((got, agents), ({"agent"}, {"steam"}))
        self.assertEqual(explicit("CoolingTower", {"agent": "brine"},
                                  [STEAM])[0], set())
        self.assertEqual(explicit("BoilerTurbogenerator", {
            "boiler_efficiency": 1.5, "fuel_source": "",
            "satisfy_system_electricity_demand": "yes"})[0], set())

    def test_facility_unit_score_and_uop_06(self):
        """_facility_unit_score / _score_uop_06 (CMP-UOP-06): every facility
        of facility_doc fully stated -> 1 (CIP1 excluded); BT1 without its
        agent key but raising steam -> the served agent is derivable
        (_serves_agent) -> (4 + 0.75) / 5, and without the steam result as
        well -> 4/5; BT1 without fuel_source -> 4/5; CT1 with no specs ->
        its cooling-water agent derived (0.75); PWC1 bound to undeclared stream
        ids -> 0 with 'one of process_water_streams' locators; a plain
        Boiler needs boiler_efficiency only; a RefrigerationPackage its
        agent and a plausible efficiency (1.5 counts as unstated -> 0; 0.35
        -> 1/2 without the agent); an unknown facility -> 0
        ('facility kind unknown'); unweighted over the population; no
        facility (the valid mixer, or only a cost-only CIP) -> None."""
        doc = facility_doc()
        score, message, missing = run(C._score_uop_06, doc)
        self.assertEqual((score, missing), (1.0, []))
        self.assertIn("4 of 4 facility unit(s)", message)
        sc = ctx(doc)
        self.assertTrue(C._serves_agent(sc, 0))
        self.assertFalse(C._serves_agent(sc, 2))
        bad = copy.deepcopy(doc)
        bad["units"][1]["design_input_specs"] = {
            "process_water_streams": ["nope"], "makeup_water_streams": ["mw", "x"]}
        score, missing = C._facility_unit_score(ctx(bad), 1)
        self.assertEqual(score, 0.0)
        self.assertIn("units[PWC1].design_input_specs: one of "
                      "process_water_streams", missing)
        self.assertAlmostEqual(run(C._score_uop_06, bad)[0], 6 / 8)
        self.assertEqual(C._facility_unit_score(ctx(doc), 0), (1.0, []))
        boiler = one_unit_doc(spec_unit("Boiler", {"boiler_efficiency": 0.8}))
        self.assertEqual(run(C._score_uop_06, boiler)[0], 1.0)
        fridge = one_unit_doc(spec_unit("RefrigerationPackage",
                                        {"efficiency": 1.5}))
        self.assertEqual(run(C._score_uop_06, fridge)[0], 0.0)
        fridge["units"][0]["design_input_specs"]["efficiency"] = 0.35
        self.assertEqual(run(C._score_uop_06, fridge)[0], 0.5)
        score, missing = C._facility_unit_score(
            ctx(one_unit_doc({"unit_type": "Utility facility"})), 0)
        self.assertEqual(score, 0.0)
        self.assertIn("facility kind unknown", missing[0])
        self.assertIsNone(run(C._score_uop_06, valid_doc())[0])
        self.assertIsNone(run(C._score_uop_06,
                              one_unit_doc(spec_unit("CIPpackage")))[0])
        derived = copy.deepcopy(doc)
        del derived["units"][0]["design_input_specs"]["agent"]
        self.assertAlmostEqual(C._facility_unit_score(ctx(derived), 0)[0],
                               (4 + C.DERIVED_CREDIT) / 5)
        del derived["units"][0]["utility_production_results"]["steam"]
        self.assertAlmostEqual(C._facility_unit_score(ctx(derived), 0)[0], 0.8)
        derived = copy.deepcopy(doc)
        del derived["units"][0]["design_input_specs"]["fuel_source"]
        self.assertAlmostEqual(C._facility_unit_score(ctx(derived), 0)[0], 0.8)
        derived["units"][3]["design_input_specs"] = {}
        self.assertEqual(C._facility_unit_score(ctx(derived), 3)[0],
                         C.DERIVED_CREDIT)

    def test_missing_agent_kinds_per_kind_need(self):
        """_missing_agent_kinds (R2.7): an HXutility with only a power
        utility declared -> one synthetic 'heat agent' entry, so
        CMP-UTIL-01 and CMP-UTIL-02 score 0 instead of 1 (the missing heat
        agent 0; R6.4: the power utility, which no unit references, can
        only lower the score); a Pump with only a heat agent ->
        a 'power utility' entry; both kinds declared -> none; the valid
        mixer -> none (no need)."""
        grid = {"id": "grid", "electrical_energy_price": 0.07}
        doc = one_unit_doc(spec_unit("HXutility"))
        doc["utilities"]["power_utilities"] = [grid]
        sc = ctx(doc)
        kinds = C._missing_agent_kinds(sc)
        self.assertEqual(len(kinds), 1)
        self.assertIn("no heat agent declared", kinds[0])
        score, message, missing = run(C._score_util_01, doc)
        self.assertEqual(score, 0.0)
        self.assertIn("1 needed kind(s) missing", message)
        self.assertIn(kinds[0], missing)
        self.assertEqual(run(C._score_util_02, doc)[0], 0.0)
        pump = one_unit_doc(spec_unit("Pump"))
        pump["utilities"]["heat_utilities"] = [STEAM]
        kinds = C._missing_agent_kinds(ctx(pump))
        self.assertEqual(len(kinds), 1)
        self.assertIn("no power utility declared", kinds[0])
        both = copy.deepcopy(doc)
        both["utilities"]["heat_utilities"] = [STEAM]
        self.assertEqual(C._missing_agent_kinds(ctx(both)), [])
        self.assertEqual(run(C._score_util_01, both)[0], 1.0)
        self.assertEqual(C._missing_agent_kinds(ctx(label_mixer_doc())), [])
        # R4.13: a unit of unknown family (valid_doc's one-inlet 'Mixer'
        # label, R4.14) needs both kinds.
        self.assertEqual(C._missing_agent_kinds(ctx(valid_doc())), [
            "utilities.heat_utilities: no heat agent declared, although "
            "units of unknown family (R4.13) may exchange heat",
            "utilities.power_utilities: no power utility declared, although "
            "units of unknown family (R4.13) may draw or deliver electricity"])

    def test_facility_doc_deletions_never_raise(self):
        """Deletion monotonicity (R1.0) over facility_doc: deleting any value
        or sub-object -- a facility setting, a stream-id list, T_min_app,
        a unit's utility results (the served-agent derivation), a feed's
        composition (the natural-gas port becomes 'may be one'), an
        agent -- never raises the overall score nor any scored criterion,
        and more than a third of the deletions lower it."""
        with name_lookup(set()):
            raised, lowered, n = deletion_violations(facility_doc())
        self.assertGreater(n, 80)
        self.assertEqual(raised, [])
        self.assertGreater(lowered, n // 3)

    def test_splitter_guard_deletions_never_raise(self):
        """The R2.4 consistency guard is monotone: in
        splitter_doc('MolecularSieve') CO2 appears across the core
        splitter-family S1 without reactions (not a PURE splitter, but R6.1(a)
        now contradicts every exact core class without chemistry across
        which a species appears: S1's family is unknown, (None, None, 0.0),
        and an unknown family is guarded too),
        so both outlets' compositions do not count (CMP-UOP-04 = 0.75, the
        missing entries saying 'inconsistent') and the split derivation is
        blocked; deleting outA's phases (the stream carrying the evidence)
        leaves the guard tripped (species flows no longer computable: the
        UOP-04 entry now says 'unverified'), so no score rises -- nor does
        any other deletion. The claim-standing case is S1 relabelled a
        label-only 'Holding tank' (R7.1: a hosting claim keeps its family;
        not reactive itself, the flowsheet-level pseudo-member covers it);
        the guard trips on it as above, and the deletion sweep runs on it (the
        contradicted sieve is an inconsistent document, outside the
        guarantee). With the pure Splitter class (the default
        splitter_doc) the appearance contradicts the claim too (R5.1).
        A custom splitter class located in the pinned model package
        (CleaningSystem, confidence 1) is not guarded (UOP-04 = 1)."""
        sieve = splitter_doc("MolecularSieve")
        sc = ctx(sieve)
        self.assertEqual(sc.unit_classes[0], (None, None, 0.0))
        self.assertIn("implausible for a unit that hosts no chemistry: CO2 "
                      "appears", C._class_note(sc, 0))
        self.assertFalse(C._species_consistency(sc, 0)[0])
        # A claim that stands while the guard trips: a label-only 'Holding
        # tank' (R7.1: a tank may host chemistry, so the claim keeps its
        # family -- the flowsheet-level pseudo-member covers it instead).
        doc = copy.deepcopy(sieve)
        doc["units"][0] = {"id": "S1", "unit_type": "Holding tank",
                           "design_input_specs": {"tau": 2.0}}
        sc = ctx(doc)
        self.assertEqual(sc.unit_classes[0], (None, "tank", 0.5))
        self.assertIsNone(C._reactive_reason(sc, 0))
        self.assertEqual(C._unexplained_hosts(sc), [0])
        self.assertFalse(C._species_consistency(sc, 0)[0])
        score, _message, missing = run(C._score_uop_04, doc)
        self.assertEqual(score, 0.75)
        self.assertIn("(stated, but inconsistent at units[S1]: CO2 appears",
                      missing[0])
        self.assertFalse(C._closure(sc, 0)[0])
        base = overall(doc)
        stripped = deleted(doc, ("streams", 1, "stream_properties", "phases"))
        self.assertLessEqual(overall(stripped), base)
        score, message, missing = run(C._score_uop_04, stripped)
        self.assertLessEqual(score, 0.75)
        self.assertIn("left unverified by the species-consistency guard",
                      message)
        self.assertIn("(stated, but unverified at units[S1]: species flows "
                      "not computable", "\n".join(missing))
        self.assertIn("not computable",
                      C._species_consistency(ctx(stripped), 0)[1])
        with name_lookup(set()):
            raised, lowered, n = deletion_violations(doc)
        self.assertGreater(n, 40)
        self.assertEqual(raised, [])
        self.assertGreater(lowered, n // 3)
        pure = ctx(splitter_doc())
        self.assertEqual(pure.unit_classes[0], (None, None, 0.0))
        self.assertIn("implausible for a pure splitter: CO2 appears without "
                      "declared reactions", C._class_note(pure, 0))
        custom = copy.deepcopy(sieve)
        custom["units"][0]["design_simulation_method"] = \
            f"CleaningSystem on {BIP_URL}"
        custom["metadata"]["reproducibility"] = {
            "flowsheet_model_package": {"url": BIP_REPO, "commit": "abc123"}}
        self.assertEqual(run(C._score_uop_04, custom)[0], 1.0)


# ------------------------------- Revision 3, part A (unit identity cluster)

COMPRESSOR_URL = ("https://github.com/BioSTEAMDevelopmentGroup/biosteam/blob/"
                  "master/biosteam/units/compressor.py")
DESC_MW = {"Water": 18.015, "Ethanol": 46.07}
DESC_X = {"Water": 0.9, "Ethanol": 0.1}


def _desc_sp(flow, phase, T, P):
    """Stream properties of `flow` kmol/h of the fixed 90/10 Water/Ethanol
    mixture in one phase (every stream of descriptive_doc() shares the
    composition, so every species balance closes with the total)."""
    mass = flow * sum(x * DESC_MW[n] for n, x in DESC_X.items())
    return {"temperature": T, "pressure": P, "total_molar_flow": flow,
            "total_mass_flow": mass, "enthalpy_flow": -1e5 * flow,
            "phases": {phase: {"total_molar_flow": flow, "composition": [
                {"component_name": n, "mol_fraction": x}
                for n, x in DESC_X.items()]}}}


def descriptive_doc():
    """A consistent flowsheet whose units carry ONLY a descriptive unit_type
    and design_input_specs (no design_simulation_method; R3.1 deletion
    test), plus property packages and utility results: feed -> P1 'Feed
    pump' -> H1 'Heat exchanger' -> F1 'Flash drum' -> vapor to K1 'Gas
    compressor' (product 'gas') and liquid to S1 'Filter press' (moisture
    stated) -> cake to DR1 'Drum dryer' (dried product + humid gas to X1
    'Thermal oxidizer' -> flue) and filtrate to T1 'Storage tank' -> D1
    'Distillation column' -> distillate + bottoms to M1 'Mixer' -> 'mixed'.
    Every stream carries the same 90/10 Water/Ethanol mixture with full
    state and enthalpy, and every unit's mass balance closes; boundary
    streams are priced; the model package is not pinned."""
    edges = (  # id, source, sink, kmol/h, phase, T, P, price
        ("feed", "None", "P1", 100, "l", 300.0, 101325.0, 0.1),
        ("s1", "P1", "H1", 100, "l", 300.0, 3e5, None),
        ("s2", "H1", "F1", 100, "l", 360.0, 3e5, None),
        ("vap", "F1", "K1", 10, "g", 360.0, 3e5, None),
        ("gas", "K1", "None", 10, "g", 400.0, 5e5, 0.2),
        ("liq", "F1", "S1", 90, "l", 360.0, 3e5, None),
        ("cake", "S1", "DR1", 45, "l", 360.0, 3e5, None),
        ("filtrate", "S1", "T1", 45, "l", 360.0, 3e5, None),
        ("dried", "DR1", "None", 40, "l", 343.15, 101325.0, 0.3),
        ("humid", "DR1", "X1", 5, "g", 343.15, 101325.0, None),
        ("flue", "X1", "None", 5, "g", 900.0, 101325.0, 0.0),
        ("t_out", "T1", "D1", 45, "l", 360.0, 3e5, None),
        ("dist", "D1", "M1", 5, "l", 351.0, 101325.0, None),
        ("bottoms", "D1", "M1", 40, "l", 373.0, 101325.0, None),
        ("mixed", "M1", "None", 45, "l", 370.0, 101325.0, 0.5))
    streams = []
    for sid, source, sink, flow, phase, T, P, price in edges:
        stream = {"id": sid, "source_unit_id": source, "sink_unit_id": sink,
                  "stream_properties": _desc_sp(flow, phase, T, P)}
        if price is not None:
            stream["price"] = price
        streams.append(stream)

    def unit(uid, unit_type, specs, cons=None):
        return {"id": uid, "unit_type": unit_type, "design_input_specs": specs,
                "thermo_property_package": dict(FULL_PKG),
                "utility_consumption_results": cons or {},
                "utility_production_results": {}}
    units = [
        unit("P1", "Feed pump", {"P": 3e5, "dP_design": 2e5}, {"grid": 1.0}),
        unit("H1", "Heat exchanger", {"T": 360.0}, {"steam": 100.0}),
        unit("F1", "Flash drum", {"T": 360.0, "P": 3e5}, {"steam": 10.0}),
        unit("K1", "Gas compressor", {"P": 5e5, "eta": 0.8}, {"grid": 2.0}),
        unit("S1", "Filter press", {"split": 0.5, "moisture_content": 0.3}),
        unit("DR1", "Drum dryer", {"split": 0.1, "moisture_content": 0.1,
                                   "T": 343.15}, {"natural_gas": 1.0}),
        unit("X1", "Thermal oxidizer", {"duty_per_kg": 61.0}),
        unit("T1", "Storage tank", {"tau": 2.0}),
        unit("D1", "Distillation column",
             {"LHK": ["Ethanol", "Water"], "P": 101325.0, "Lr": 0.99,
              "Hr": 0.99, "k": 1.2, "partial_condenser": False},
             {"steam": 50.0, "cooling_water": -40.0}),
        unit("M1", "Mixer", {})]
    chemicals = [{"id": "Water", "registry_id": "7732-18-5", "formula": "H2O",
                  "molar_mass": 18.015},
                 {"id": "Ethanol", "registry_id": "64-17-5",
                  "formula": "C2H6O", "molar_mass": 46.07}]
    water = lambda phase: [{"phase": phase, "component_name": "Water",
                            "mol_fraction": 1.0}]
    utilities = {
        "heat_utilities": [
            {"id": "steam", "temperature": 412.0, "pressure": 344738.0,
             "composition": water("g"), "regeneration_price": 0.2,
             "heat_transfer_efficiency": 0.95},
            {"id": "cooling_water", "temperature": 305.0, "pressure": 101325.0,
             "composition": water("l"), "regeneration_price": 5e-4,
             "heat_transfer_efficiency": 1.0, "temperature_limit": 324.0}],
        "power_utilities": [{"id": "grid", "electrical_energy_price": 0.07}],
        "other_utilities": [{"id": "natural_gas", "price": 0.2}]}
    metadata = {"sff_version": "0.2.1", "TEA_currency": "USD", "TEA_year": 2020,
                "process_simulator": {"name": "BioSTEAM", "version": "2.54.0"}}
    return {"metadata": metadata, "units": units, "streams": streams,
            "chemicals": chemicals, "utilities": utilities}


class TestRevision3PartA(unittest.TestCase):
    """Revision 3 part A: class-inference precedence (R3.1), solids
    separators / dryers / combustors (R3.5), custom-class identity (R3.6)
    and referenced-but-undeclared unit ids (R3.8), with the deletion test
    extended to ids and stream endpoints."""

    def test_review_inference_failures_fixed(self):
        """R3.1, the review's failures on one-unit documents: 'Distillation
        column' {LHK, P} -> distillation (was IsenthalpicValve once LHK was
        deleted), also with LHK deleted; 'Storage tank' {tau} -> tank (was
        ThermalOxidizer); 'Heat exchanger' {T} -> hx_utility (was
        JetCooker); 'Flash drum' {T, P} -> flash (was SinglePhaseStage);
        'Compressor on <biosteam url>' (abstract) {P, eta} -> compressor,
        also with eta deleted. Each resolves to (None, stated family, 0.5):
        no class is inferred, as the key sets confirm no family
        unambiguously; deleting LHK / eta raises neither the overall score
        nor CMP-UOP-01 (was 0.2552 -> 0.2641 and 0.594 -> 0.643)."""
        cases = (
            ({"unit_type": "Distillation column"},
             {"LHK": ["Ethanol", "Water"], "P": 101325.0}, "distillation",
             "LHK"),
            ({"unit_type": "Storage tank"}, {"tau": 10.0}, "tank", None),
            ({"unit_type": "Heat exchanger"}, {"T": 300.0}, "hx_utility", None),
            ({"unit_type": "Flash drum"}, {"T": 300.0, "P": 101325.0}, "flash",
             None),
            ({"unit_type": "Compressor",
              "design_simulation_method": f"Compressor on {COMPRESSOR_URL}"},
             {"P": 101325.0, "eta": 0.8}, "compressor", "eta"))
        for fields, specs, family, drop in cases:
            doc = one_unit_doc(dict(fields, design_input_specs=dict(specs)))
            with self.subTest(unit_type=fields["unit_type"]):
                self.assertEqual(ctx(doc).unit_classes[0], (None, family, 0.5))
                if drop is None:
                    continue
                reduced = deleted(doc, ("units", 0, "design_input_specs", drop))
                self.assertEqual(ctx(reduced).unit_classes[0],
                                 (None, family, 0.5))
                self.assertLessEqual(overall(reduced), overall(doc))
                self.assertLessEqual(
                    criterion_results(reduced)["CMP-UOP-01"].score,
                    criterion_results(doc)["CMP-UOP-01"].score)

    def test_inferred_family_gets_family_rules_only(self):
        """R3.1: a key set only one registry class holds (DrumDryer's split,
        moisture_content, T) confirms the stated 'Drum dryer' family (0.75)
        and, with no unit_type, gives that family (0.75) -- but never the
        class: no class rule, registry class, class-default credit or
        CMP-UOP-03 measurement reaches it (CMP-UOP-03 scores it 0), and a
        confidence-1 class knowledge waiver (R3.5) is never granted."""
        specs = {"split": 0.1, "moisture_content": 0.1, "T": 300.0}
        for fields in ({"unit_type": "Drum dryer"}, {}):
            doc = one_unit_doc(dict(fields, design_input_specs=dict(specs)))
            sc = ctx(doc)
            with self.subTest(fields=fields):
                self.assertEqual(sc.unit_classes[0], (None, "dryer", 0.75))
                self.assertEqual(sc.unit_registry_classes, [None])
                self.assertIs(C._unit_rules(sc, 0), C._FAMILY_RULES["dryer"])
                self.assertFalse(C._class_lacks_param(sc, 0, ("T",)))
                self.assertEqual(run(C._score_uop_03, doc)[0], 0.0)

    def test_descriptive_doc_deletion_sweep(self):
        """R3.1 / R3.8 deletion test: every deletable path of
        descriptive_doc() -- units carrying ONLY a descriptive unit_type +
        design_input_specs -- INCLUDING unit and stream ids, stream
        endpoints and component names (deletion_violations with skip =
        frozenset()), deleted one at a time: no deletion raises the overall
        score or any criterion's score, or turns a scored criterion
        not-applicable. The document exercises confirmed stated families
        (0.75: pump, dryer, combustor, distillation), unconfirmed ones (0.5:
        heat exchanger, flash, compressor, filter press, tank), a unit-id
        deletion (an undeclared referenced unit, R3.8) and endpoint
        deletions of multi-port sides (the dryer's outs). Expected: > 400
        paths, no violation, most deletions lower the score."""
        doc = descriptive_doc()
        sc = ctx(doc)
        classes = {u["id"]: c for u, c in zip(doc["units"], sc.unit_classes)}
        for uid, family in (("P1", "pump"), ("DR1", "dryer"),
                            ("X1", "combustor"), ("D1", "distillation")):
            self.assertEqual(classes[uid], (None, family, 0.75), uid)
        for uid, family in (("H1", "hx_utility"), ("F1", "flash"),
                            ("K1", "compressor"), ("S1", "splitter"),
                            ("T1", "tank")):
            self.assertEqual(classes[uid], (None, family, 0.5), uid)
        self.assertEqual(ctx(deleted(doc, ("units", 0, "id"))).phantom_units,
                         ["P1"])
        paths = dict(deletable_paths(doc, skip=frozenset()))
        for path in (("units", 0, "id"), ("streams", 8, "source_unit_id"),
                     ("streams", 3, "sink_unit_id"),
                     ("streams", 0, "stream_properties", "phases", "l",
                      "composition", 0, "component_name")):
            self.assertEqual(paths.get(path), "leaf", path)
        with name_lookup(set()):
            raised, lowered, n = deletion_violations(doc, skip=frozenset())
        self.assertGreater(n, 400)
        self.assertEqual(raised, [])
        self.assertGreater(lowered, n // 2)

    def test_phantom_units(self):
        """R3.8: a stream endpoint naming no declared unit ('Ghost') makes
        an undeclared referenced unit (_ScoringContext.phantom_units; the
        boundary 'None', non-strings and declared ids never do). It joins
        CMP-UOP-01, CMP-UOP-02 and CMP-TOP-02 (both port sides) at 0,
        counting once like every unit (R4.12): a fully specified pump
        beside it -> UOP-01 1/2, UOP-02 1/2; _phantom_scores gives the zero
        scores and the 'referenced by a stream endpoint but not declared'
        locators,
        _phantom_note the message suffix. With no units at all, streams
        into 'A' still score those criteria 0 (not not-applicable)."""
        doc = one_unit_doc(spec_unit("Pump", {"P": 101325.0, "dP_design": 1.0}))
        doc["streams"].append(stream("ghost", "Ghost", "None"))
        doc["streams"].append(stream("odd", 5, "None"))
        sc = ctx(doc)
        self.assertEqual(sc.phantom_units, ["Ghost"])
        self.assertEqual(C._phantom_scores(sc, "x"), (
            [0.0],
            ["units[Ghost]: referenced by a stream endpoint but not declared, "
             "so x"]))
        self.assertEqual(C._phantom_note(sc),
                         "; 1 referenced but undeclared unit id(s) score 0")
        self.assertEqual(C._phantom_note(ctx(one_unit_doc({}))), "")
        score, message, missing = run(C._score_uop_01, doc)
        self.assertAlmostEqual(score, 1 / 2)
        self.assertIn("1 referenced but undeclared unit id(s) score 0", message)
        self.assertIn("units[Ghost]: referenced by a stream endpoint but not "
                      "declared, so its class is unknown", missing)
        self.assertAlmostEqual(run(C._score_uop_02, doc)[0], 1 / 2)
        score, _m, missing = run(C._score_top_02, doc)
        self.assertLess(score, run(C._score_top_02, one_unit_doc(
            spec_unit("Pump", {"P": 101325.0, "dP_design": 1.0})))[0])
        self.assertEqual(sum("units[Ghost]" in m for m in missing), 1)
        bare = {"streams": [stream("s", "None", "A")]}
        for scorer in (C._score_uop_01, C._score_uop_02, C._score_top_02):
            self.assertEqual(run(scorer, bare)[0], 0.0, scorer.__name__)

    def test_unit_id_deletion_keeps_unit_needs(self):
        """R3.8 on descriptive_doc(): deleting unit K1's id leaves its
        streams naming 'K1', an undeclared referenced unit scored 0, while
        the id-less unit's own port sides score 0 ('no unit id, so no
        stream can state its ports'): CMP-UOP-01, CMP-UOP-02 and CMP-TOP-02
        each strictly drop."""
        doc = descriptive_doc()
        reduced = deleted(doc, ("units", 3, "id"))
        before, after = criterion_results(doc), criterion_results(reduced)
        for cid in ("CMP-UOP-01", "CMP-UOP-02", "CMP-TOP-02"):
            self.assertLess(after[cid].score, before[cid].score, cid)
        sc = ctx(reduced)
        self.assertEqual(sc.phantom_units, ["K1"])
        self.assertEqual(C._side_credit(sc, 3, "ins"), (
            0.0, "units[#3].ins: no unit id, so no stream can state its ports"))

    def test_side_credit_material_streams_and_closure(self):
        """_side_credit: the stream-count conventions need closure (R2.2)
        and count only material-carrying streams (stated-empty ones are
        placeholders, like unexported ports). A Splitter's outs [product,
        stated-empty] -> DERIVED_CREDIT, unchanged when the empty stream's
        endpoint is deleted; the product carrying only 20 of 46.07 kg/h
        (balance open) -> 0 with a 'no port convention applies' locator;
        a stated-empty stream stating only a zero molar flow counts as no
        mass in _closure."""
        doc = one_unit_doc(spec_unit("Splitter"),
                           outs=({"mass": 46.07}, {"mass": 0.0}))
        sc = ctx(doc)
        self.assertTrue(C._closure(sc, 0)[0])
        self.assertEqual(C._side_credit(sc, 0, "outs"), (
            C.DERIVED_CREDIT, "units[U1].outs: port index not representable "
            "(unexported or empty ports may exist)"))
        detached = deleted(doc, ("streams", 2, "source_unit_id"))
        self.assertEqual(C._side_credit(ctx(detached), 0, "outs")[0],
                         C.DERIVED_CREDIT)
        molar_only = copy.deepcopy(doc)
        sp = molar_only["streams"][2]["stream_properties"]
        del sp["total_mass_flow"]
        sp.update(total_molar_flow=0.0, phases={})
        self.assertTrue(C._closure(ctx(molar_only), 0)[0])
        open_doc = one_unit_doc(spec_unit("Splitter"),
                                outs=({"mass": 20.0}, {"mass": 0.0}))
        credit, locator = C._side_credit(ctx(open_doc), 0, "outs")
        self.assertEqual(credit, 0.0)
        self.assertIn("no port convention applies: mass balance does not "
                      "close", locator)

    def test_port_evidence_waivers_need_closure(self):
        """_requirement_applies: phase-change and pressure-rise waivers rest
        on port evidence, affirmative only when the unit's balances close
        (a missing port could change the verdict): a balanced HXprocess
        (liquid in and out) waives phase0|phase1, an unbalanced one applies
        it; a Pump with a rise waives dP_design only while it balances."""
        phase_req = C._FAMILY_RULES["hx_process"][1]
        doc = one_unit_doc(spec_unit("HXprocess"), ins=({}, {}), outs=({}, {}))
        self.assertFalse(C._requirement_applies(phase_req, ctx(doc), 0))
        doc["streams"][2]["stream_properties"]["total_mass_flow"] *= 3
        self.assertTrue(C._requirement_applies(phase_req, ctx(doc), 0))
        dp_req = C._FAMILY_RULES["pump"][1]
        doc = one_unit_doc(spec_unit("Pump"), outs=({"P": 3e5},))
        self.assertFalse(C._requirement_applies(dp_req, ctx(doc), 0))
        doc["streams"][1]["stream_properties"]["total_mass_flow"] = 10.0
        self.assertTrue(C._requirement_applies(dp_req, ctx(doc), 0))

    def test_class_lacks_param(self):
        """_class_lacks_param (R3.5): an exactly known class whose registry
        entry (or curated ancestor) lists none of the keys -> True
        (Splitter, MolecularSieve: no moisture_content; CleaningSystem via
        Splitter, located in the pinned repository); one that takes a key
        -> False (SolidsCentrifuge, ThermalOxidizer's duty_per_kg); an
        unlisted core class recorded in _UNLISTED_CLASS_LACKS
        (ReverseOsmosis) -> True; an unlisted class without a record
        (BeltThickener), the same CleaningSystem unpinned (0.75), or a
        family-only unit -> False (the requirement applies)."""
        moisture = ("moisture_content", "sludge_moisture")
        def lacks(unit, keys=moisture, pinned=False):
            return C._class_lacks_param(ctx(one_unit_doc(unit, pinned=pinned)),
                                        0, keys)
        self.assertTrue(lacks(spec_unit("Splitter")))
        self.assertTrue(lacks(spec_unit("MolecularSieve")))
        self.assertTrue(lacks(spec_unit("CleaningSystem", url=BIP_URL),
                              pinned=True))
        self.assertFalse(lacks(spec_unit("CleaningSystem", url=BIP_URL)))
        self.assertFalse(lacks(spec_unit("SolidsCentrifuge")))
        self.assertFalse(lacks(spec_unit("ThermalOxidizer"), ("duty_per_kg",)))
        self.assertTrue(lacks(spec_unit("ReverseOsmosis")))
        self.assertIn("ReverseOsmosis", C._UNLISTED_CLASS_LACKS)
        self.assertFalse(lacks(spec_unit("BeltThickener")))
        self.assertFalse(lacks({"unit_type": "Filter press"}))
        self.assertEqual(C._class_family("FakeSplitter"), "junction")

    def test_solids_dryer_combustor_rules(self):
        """R3.5 rules (one-unit docs, full balanced ports): SolidsCentrifuge
        split only -> moisture derived (1 + 0.75) / 2, both stated -> 1;
        BeltThickener's sludge_moisture counts; Splitter / MolecularSieve
        split -> 1 (moisture waived); CleaningSystem pinned -> 1, unpinned
        -> 0.875; a family-only 'Filter press' split -> 0.875; DrumDryer
        split + moisture_content + T -> 1, without T (outlet T derivable)
        -> 2.75/3, split only -> 2.5/3; SprayDryer keeps its class rule;
        SLLECentrifuge solids_split -> (1 + 0.75) / 2 (moisture_content, a
        constructor default); an unknown-class 'Furnace' (combustor) ->
        duty_per_kg required (0, then 1)."""
        two = ({}, {})
        split = {"split": [0.5]}
        def score(unit, outs=two, pinned=False):
            doc = one_unit_doc(unit, outs=outs, pinned=pinned)
            return C._unit_spec_score(ctx(doc), 0)[0]
        self.assertAlmostEqual(score(spec_unit("SolidsCentrifuge", split)), 0.875)
        self.assertEqual(score(spec_unit("SolidsCentrifuge",
                                         dict(split, moisture_content=0.4))), 1.0)
        self.assertEqual(score(spec_unit("BeltThickener",
                                         dict(split, sludge_moisture=0.96))), 1.0)
        self.assertEqual(score(spec_unit("Splitter", split)), 1.0)
        self.assertEqual(score(spec_unit("MolecularSieve", split)), 1.0)
        cleaning = spec_unit("CleaningSystem", split, url=BIP_URL)
        self.assertEqual(score(cleaning, pinned=True), 1.0)
        self.assertAlmostEqual(score(cleaning), 0.875)
        self.assertAlmostEqual(score({"unit_type": "Filter press",
                                      "design_input_specs": split}), 0.875)
        dryer = {"split": [0.1], "moisture_content": 0.1, "T": 300.0}
        self.assertEqual(score(spec_unit("DrumDryer", dryer), outs=({},)), 1.0)
        no_t = {k: v for k, v in dryer.items() if k != "T"}
        self.assertAlmostEqual(score(spec_unit("DrumDryer", no_t), outs=({},)),
                               2.75 / 3)
        self.assertAlmostEqual(score(spec_unit("DrumDryer", split), outs=({},)),
                               2.5 / 3)
        self.assertIs(C._unit_rules(ctx(one_unit_doc(spec_unit("SprayDryer"))),
                                    0), C._CLASS_RULES["SprayDryer"])
        self.assertAlmostEqual(score(spec_unit("SLLECentrifuge",
                                               {"solids_split": [0.5]})), 0.875)
        furnace = {"unit_type": "Furnace", "design_input_specs": {}}
        self.assertEqual(score(furnace, outs=({},)), 0.0)
        furnace["design_input_specs"]["duty_per_kg"] = 61.0
        self.assertEqual(score(furnace, outs=({},)), 1.0)

    def test_uop_03_custom_class_without_registry_entry(self):
        """R3.6 / CMP-UOP-03: a core class known exactly that the registry
        does not list (Mixer here) is left out; an exact CUSTOM class the
        registry does not list, nor a curated ancestor (GrainHandling
        located in the pinned repository) scores 0 with a 'has no
        design-spec registry entry' locator -- the file states none of its
        parameters; unpinned it is not exact (0, 'parameter universe')."""
        registry = {"HXutility": {"line": "HX", "design_input_spec_params": {
            "T": {"accessors": ["T"]}}}}
        self.assertIsNone(run(C._score_uop_03, unit_doc(cls("Mixer")),
                              registry=registry)[0])
        doc = one_unit_doc(spec_unit("GrainHandling", url=BIP_URL), pinned=True)
        score, message, missing = run(C._score_uop_03, doc, registry=registry)
        self.assertEqual(score, 0.0)
        self.assertEqual(missing, ["units[U1]: custom class 'GrainHandling' has "
                                   "no design-spec registry entry, so the file "
                                   "states none of its parameters"])
        self.assertIn("or a custom class the registry does not list", message)
        doc = one_unit_doc(spec_unit("GrainHandling", url=BIP_URL))
        score, _m, missing = run(C._score_uop_03, doc, registry=registry)
        self.assertEqual(score, 0.0)
        self.assertIn("parameter universe is unknown", missing[0])


# ------------------------------------------------ Revision 3, part B

# Fake `chemicals` database for the R3 documents: rich_doc's names plus the
# CAS numbers rich_doc states (so the registry tier resolves), each with the
# KNOWN_RECORDS molar mass and formula. Cellulose's CAS (9004-34-6) is
# deliberately absent: well-formed but unresolvable.
R3_RECORDS = dict({n: KNOWN_RECORDS[n] for n in RICH_NAMES},
                  **{"7732-18-5": KNOWN_RECORDS["Water"],
                     "50-99-7": KNOWN_RECORDS["Glucose"],
                     "64-17-5": KNOWN_RECORDS["Ethanol"],
                     "124-38-9": KNOWN_RECORDS["CO2"]})

FIBER = {"id": "Fiber", "registry_id": "9004-34-6", "formula": "C6H10O5",
         "molar_mass": 162.14, "included_in_thermo": True}


def r3_rich_doc(single_thermo=False, convert=True):
    """rich_doc() plus a declared pseudo-component 'Fiber' whose real but
    unlisted CAS (cellulose's) the database cannot resolve (R3.2; the id
    itself resolves as a thermosteam built-in since R4.1), and a
    quantity_units_global. With `convert`, every stream and agent quantity
    is restated in non-internal units (R3.4): temperatures (the
    cooling-water temperature_limit included) in degC, pressures in bar,
    mass flows in kg/s; otherwise the same document in K, Pa and kg/hr.
    design_input_specs stay BioSTEAM-native (K, Pa) either way."""
    doc = rich_doc(single_thermo)
    doc["chemicals"].append(dict(FIBER))
    units = ("degC", "bar", "kg/s") if convert else ("K", "Pa", "kg/hr")
    doc["quantity_units_global"] = {
        "temperature": {"aliases": ["temperature", "temperature_limit"],
                        "quantity_units": units[0]},
        "pressure": {"aliases": ["pressure"], "quantity_units": units[1]},
        "mass_flow": {"aliases": ["total_mass_flow"], "quantity_units": units[2]},
        "molar_flow": {"aliases": ["total_molar_flow"],
                       "quantity_units": "kmol/hr"}}
    if convert:
        for s in doc["streams"]:
            sp = s["stream_properties"]
            sp["temperature"] -= 273.15
            sp["pressure"] /= 1e5
            sp["total_mass_flow"] /= 3600.0
        for agent in doc["utilities"]["heat_utilities"]:
            agent["temperature"] -= 273.15
            agent["pressure"] /= 1e5
            if "temperature_limit" in agent:
                agent["temperature_limit"] -= 273.15
    return doc


def padded_doc(sink_fields):
    """R3.7 review case: one feed stating only its flow (T, P and
    composition missing: 0.25) into U1 (`sink_fields`), plus six zero-flow
    feeds (total mass and molar flow 0, nothing else) padding U1."""
    doc = one_unit_doc(sink_fields, ins=({"T": None, "P": None, "phases": {}},))
    doc["streams"] += [
        {"id": f"pad{k}", "source_unit_id": "None", "sink_unit_id": "U1",
         "stream_properties": {"total_mass_flow": 0.0, "total_molar_flow": 0.0}}
        for k in range(6)]
    return doc


class TestRevision3PartB(unittest.TestCase):
    """Revision 3 part B: registry ids must resolve (R3.2), utility-agent
    plausibility (R3.3), quantity-unit conversion (R3.4), empty-feed padding
    (R3.7), fuel prices (R3.9), TEA items (R3.10) and the minor fixes of
    R3.11, with the deletion test extended to the new branches."""

    def test_registry_id_gaming_synthetic(self):
        """R3.2 review case on a synthetic pseudo-component (fake database:
        'SMILES=C' resolves to methane, nothing else): SolubleProtein
        (C6H10O5, 162.14; no thermosteam built-in, unlike Starch since R4.1)
        scores the formula tier 0.25 with no registry_id; 'C' (methane,
        contradicted) and 'InChI=1S/x' (malformed) leave it at 0.25; an
        invented valid-check-digit CAS 98765-43-6 and a fake InChIKey reach
        only the unresolved tier 0.5 -- never 1 -- and THM-05 stays
        applicable at 0 in every case. Being unreferenced and below the
        referenced mean, it lowers CMP-THM-02 (R4.7)."""
        doc = valid_doc()
        doc["chemicals"].append({"id": "SolubleProtein", "formula": "C6H10O5",
                                 "molar_mass": 162.14})
        with name_lookup({"Ethanol": KNOWN_RECORDS["Ethanol"],
                          "SMILES=C": (16.04246, "CH4")}):
            base = run(C._score_thm_02, doc)[0]
            self.assertAlmostEqual(base, (0.75 + 0.25) / 2)
            for rid, expected in (("C", 0.25), ("InChI=1S/x", 0.25),
                                  ("98765-43-6", 0.5),
                                  ("ABCDEFGHIJKLMN-OPQRSTUVWX-Y", 0.5)):
                gamed = copy.deepcopy(doc)
                gamed["chemicals"][1]["registry_id"] = rid
                self.assertAlmostEqual(run(C._score_thm_02, gamed)[0],
                                       (0.75 + expected) / 2, msg=rid)
                score, _m, missing = run(C._score_thm_05, gamed)
                self.assertEqual(score, 0.0, rid)
                self.assertIn("chemicals[SolubleProtein]", missing[0])

    def test_util_01_agent_plausibility(self):
        """R3.3 review case: rich_doc's cooling_water with temperature -50,
        pressure -1 and composition [{}] -> _agent_gaps names all three plus
        the phase (R4.2: no entry carries one) and CMP-UTIL-01 drops from
        1.0 to 2/3 (steam and grid still full); a power utility only needs
        its id."""
        with name_lookup(RICH_NAMES):
            doc = rich_doc()
            self.assertEqual(run(C._score_util_01, doc)[0], 1.0)
            water = doc["utilities"]["heat_utilities"][1]
            water.update(temperature=-50.0, pressure=-1.0, composition=[{}])
            self.assertEqual(C._agent_gaps("heat_utilities", water)[:3],
                             ["temperature", "pressure", "composition"])
            self.assertEqual(len(C._agent_gaps("heat_utilities", water)), 4)
            score, _m, missing = run(C._score_util_01, doc)
        self.assertAlmostEqual(score, 2 / 3)
        self.assertIn("utilities.heat_utilities[cooling_water].temperature",
                      missing)
        self.assertEqual(C._agent_gaps("power_utilities", {"id": "grid"}), [])

    def test_util_02_prices_and_fuels(self):
        """R3.3 / R3.9: _valid_price -- a heat agent's price must be finite
        and >= 0 (-1 and NaN -> None, 0 -> 0); a power or other price only
        finite (-0.01 kept). _fuel_agent: an id naming natural gas or fuel,
        a >= 50 mol% CH4 composition, or an unknown (implausible)
        composition -> True; air (O2/N2) with a neutral id -> False.
        CMP-UTIL-02 on rich_doc plus a natural-gas other utility priced 0
        -> (3 + 0.5) / 4 with a 'fuel agent' locator and message; priced
        0.2 -> 1.0; air priced 0 -> 1.0; a heat agent priced -1 -> that
        agent scores 0, and the score falls to 2 / 3 (R6.4: the unreferenced
        air agent, at 1, cannot raise the referenced agents' mean)."""
        self.assertIsNone(C._valid_price("heat_utilities", -1.0))
        self.assertIsNone(C._valid_price("heat_utilities", float("nan")))
        self.assertEqual(C._valid_price("heat_utilities", 0), 0.0)
        self.assertEqual(C._valid_price("power_utilities", -0.01), -0.01)
        ch4 = [{"phase": "g", "component_name": "CH4", "mol_fraction": 1.0}]
        air = [{"phase": "g", "component_name": "O2", "mol_fraction": 0.21},
               {"phase": "g", "component_name": "N2", "mol_fraction": 0.79}]
        self.assertTrue(C._fuel_agent({"id": "natural_gas", "composition": air}))
        self.assertTrue(C._fuel_agent({"id": "Fuel oil", "composition": air}))
        self.assertTrue(C._fuel_agent({"id": "s78", "composition": ch4}))
        self.assertTrue(C._fuel_agent({"id": "s78", "composition": [{}]}))
        self.assertTrue(C._fuel_agent({"id": ["x"]}))
        self.assertFalse(C._fuel_agent({"id": "air", "composition": air}))
        with name_lookup(RICH_NAMES):
            doc = rich_doc()
            gas = {"id": "natural_gas", "temperature": 298.15,
                   "pressure": 101325.0, "price": 0.0, "composition": ch4}
            doc["utilities"]["other_utilities"] = [gas]
            score, message, missing = run(C._score_util_02, doc)
            self.assertAlmostEqual(score, 3.5 / 4)
            self.assertIn("1 fuel agent(s) priced 0", message)
            self.assertIn("fuel agent", missing[0])
            gas["price"] = 0.2
            self.assertEqual(run(C._score_util_02, doc)[0], 1.0)
            doc["utilities"]["other_utilities"] = [dict(gas, id="air", price=0.0,
                                                        composition=air)]
            self.assertEqual(run(C._score_util_02, doc)[0], 1.0)
            doc["utilities"]["heat_utilities"][0]["regeneration_price"] = -1.0
            # R6.4: the air agent no unit references (1) now lies above the
            # referenced agents' mean (2 / 3), so it cannot raise it.
            self.assertAlmostEqual(run(C._score_util_02, doc)[0], 2 / 3)

    def test_quantity_unit_helpers(self):
        """R3.4 helpers: _unit_key strips blanks, '^' and '**' ('m^3 / hr'
        -> 'm3/hr'; blank / non-string -> None); _quantity_unit_index maps
        each alias to its entry's unit, '' when two entries disagree, and
        skips entries without a string unit; _to_internal converts degC,
        degF, R, bar, psi, kPa, kg/s, lb/hr, mol/s, L/min and gal/min to K,
        Pa, kg/hr, kmol/hr, m3/hr, honours a legacy inline pair's own units,
        passes a number through when no unit is stated, wraps a number in
        an unrecognised or ambiguous unit as _Unconverted, and returns
        non-numbers unchanged; _converted converts only quantity fields;
        _internal_stream converts stream_properties and every phase without
        modifying the input."""
        self.assertEqual(C._unit_key(" m^3 / hr "), "m3/hr")
        self.assertEqual(C._unit_key("m**3/hr"), "m3/hr")
        self.assertIsNone(C._unit_key("  "))
        self.assertIsNone(C._unit_key(["K"]))
        index = C._quantity_unit_index({"quantity_units_global": {
            "temperature": {"aliases": ["temperature", "T"], "quantity_units": "degC"},
            "t2": {"aliases": ["T"], "quantity_units": "K"},
            "pressure": {"aliases": ["pressure"], "quantity_units": ["bar"]}}})
        self.assertEqual(index, {"temperature": "degC", "T": ""})
        convert = lambda value, field, unit: C._to_internal(value, field,
                                                            {field: unit})
        for value, field, unit, expected in (
                (25.0, "temperature", "degC", 298.15),
                (77.0, "temperature", "degF", 298.15),
                (536.67, "temperature_limit", "R", 298.15),
                (1.01325, "pressure", "bar", 101325.0),
                (14.6959488, "pressure", "psi", 101325.0),
                (101.325, "pressure", "kPa", 101325.0),
                (1.0, "total_mass_flow", "kg/s", 3600.0),
                (1.0, "total_mass_flow", "lb/hr", 0.45359237),
                (1.0, "total_molar_flow", "mol/s", 3.6),
                (1.0, "total_volumetric_flow", "L/min", 0.06),
                (1.0, "total_volumetric_flow", "gal/min", 0.2271247),
                (300.0, "temperature", None, 300.0)):
            self.assertAlmostEqual(convert(value, field, unit), expected,
                                   places=2, msg=(field, unit))
        self.assertEqual(C._to_internal({"value": 25.0, "units": "degC"},
                                        "temperature", {}), 298.15)
        self.assertEqual(C._to_internal({"value": 300.0, "units": ""},
                                        "temperature", {}), 300.0)
        odd = convert(-40.0, "temperature", "furlongs")
        self.assertIsInstance(odd, C._Unconverted)
        self.assertEqual(float(odd), -40.0)
        self.assertIsInstance(convert(5.0, "temperature", ""), C._Unconverted)
        for junk in ("hot", None, [1.0], True):
            self.assertIs(convert(junk, "temperature", "degC"), junk)
        self.assertEqual(C._converted({"temperature": 25.0, "price": 3.0},
                                      {"temperature": "degC"}),
                         {"temperature": 298.15, "price": 3.0})
        self.assertEqual(C._converted("x", {}), "x")
        stream = {"id": "s", "stream_properties": {
            "temperature": 25.0, "phases": {"l": {"total_mass_flow": 1.0}}}}
        out = C._internal_stream(stream, {"temperature": "degC",
                                          "total_mass_flow": "kg/s"})
        self.assertEqual(out["stream_properties"]["temperature"], 298.15)
        self.assertEqual(out["stream_properties"]["phases"]["l"]
                         ["total_mass_flow"], 3600.0)
        self.assertEqual(stream["stream_properties"]["temperature"], 25.0)
        bare = {"id": "x"}  # no stream_properties object: returned as is
        self.assertIs(C._internal_stream(bare, {}), bare)

    def test_unconverted_quantities(self):
        """R3.4: an _Unconverted quantity (unrecognised unit) skips the
        plausibility bounds -- _valid_T(-40) and _valid_P(-1) accept it --
        while a flow must stay >= 0 (_valid_flow(-1) None); _stream_value
        never returns it (no guard or pressure-rise test compares it), and
        a one-unit doc whose outlet temperature is stated in 'furlongs'
        keeps the HXutility's explicit T at full credit (guard skipped)
        instead of failing the guard."""
        self.assertEqual(C._valid_T(C._Unconverted(-40.0)), -40.0)
        self.assertEqual(C._valid_P(C._Unconverted(-1.0)), -1.0)
        self.assertIsNone(C._valid_flow(C._Unconverted(-1.0)))
        self.assertEqual(C._valid_flow(C._Unconverted(2.0)), 2.0)
        stream = {"stream_properties": {"temperature": C._Unconverted(300.0),
                                        "pressure": 101325.0}}
        self.assertIsNone(C._stream_value(stream, "T"))
        self.assertEqual(C._stream_value(stream, "P"), 101325.0)
        doc = one_unit_doc(spec_unit("HXutility", {"T": 350.0}),
                           outs=({"T": 20.0},))
        self.assertLess(C._unit_spec_score(ctx(doc), 0)[0], 1.0)
        doc["quantity_units_global"]["temperature"]["quantity_units"] = "furlongs"
        self.assertEqual(C._unit_spec_score(ctx(doc), 0)[0], 1.0)

    def test_unit_conversion_invariance(self):
        """R3.4 review case on a synthetic flowsheet: r3_rich_doc in degC,
        bar and kg/s scores every criterion exactly like the same document
        in K, Pa and kg/hr (within 1e-9); deleting the temperature or the
        pressure unit (the numbers then read as K / Pa) lowers CMP-UOP-02
        (the guards of H1's T, R1's T and P1's P fire), so the test has
        teeth."""
        with name_lookup(R3_RECORDS):
            for single_thermo in (False, True):
                plain = criterion_results(r3_rich_doc(single_thermo, False))
                converted_doc = r3_rich_doc(single_thermo)
                converted = criterion_results(converted_doc)
                for cid, result in plain.items():
                    other = converted[cid].score
                    with self.subTest(single_thermo=single_thermo, cid=cid):
                        if result.score is None:
                            self.assertIsNone(other)
                        else:
                            self.assertAlmostEqual(other, result.score,
                                                   delta=1e-9)
                for entry in ("temperature", "pressure"):
                    reduced = deleted(converted_doc, ("quantity_units_global",
                                                      entry, "quantity_units"))
                    self.assertLess(criterion_results(reduced)["CMP-UOP-02"].score,
                                    converted["CMP-UOP-02"].score, entry)

    def test_empty_feed_padding(self):
        """R3.7 / R6.3 review case: one feed stating only its flow (0.25)
        plus six zero-flow feeds padding U1. Into a unit of unknown class
        ('Widget') -> each pad scores as missing its flow, so CMP-FEED-01 =
        0.25 / 7 (it was (0.25 + 6) / 7 = 0.893), the message counting '6
        empty feed(s) (zero flow), 0 credited' and each pad's flow locator
        explaining why; R6.3: into an exactly known Mixer (variable inlet
        count) -> still 0.25 / 7, the locator saying the inlet count is not
        fixed; into an exact Pump (one fixed inlet, taken by the real feed)
        -> 0.25 / 7, 'beyond the free fixed inlet ports'; into an exact
        HXprocess (two fixed inlets, its balance closing) -> exactly one pad
        credited, (0.25 + 1) / 7. _fixed_port_counts: (2, 2) for the
        HXprocess, (None, 1) for the Mixer, (None, None) for the Widget;
        _placeholder_ports credits one HXprocess pad and names why for the
        other five; a pad whose sink is an undeclared 'Ghost' unit is never
        credited."""
        widget = padded_doc({"unit_type": "Widget"})
        score, message, missing = run(C._score_feed_01, widget)
        self.assertAlmostEqual(score, 0.25 / 7)
        self.assertIn("6 empty feed(s) (zero flow), 0 credited", message)
        self.assertIn("class is not known exactly", " ".join(missing))
        self.assertEqual(C._fixed_port_counts(ctx(widget), 0), (None, None))
        mixer = padded_doc(spec_unit("Mixer"))
        score, message, missing = run(C._score_feed_01, mixer)
        self.assertAlmostEqual(score, 0.25 / 7)
        self.assertIn("0 credited", message)
        self.assertIn("whose Mixer inlet count is not fixed", " ".join(missing))
        self.assertEqual(C._fixed_port_counts(ctx(mixer), 0), (None, 1))
        pump = padded_doc(spec_unit("Pump"))
        score, message, missing = run(C._score_feed_01, pump)
        self.assertAlmostEqual(score, 0.25 / 7)
        self.assertIn("beyond the free fixed inlet ports of units[U1]: Pump "
                      "has 1, 1 taken", " ".join(missing))
        hxp = padded_doc(spec_unit("HXprocess"))
        score, message, _missing = run(C._score_feed_01, hxp)
        self.assertAlmostEqual(score, (0.25 + 1) / 7)
        self.assertIn("6 empty feed(s) (zero flow), 1 credited", message)
        sc = ctx(hxp)
        self.assertEqual(C._fixed_port_counts(sc, 0), (2, 2))
        pads = list(range(2, 8))
        credited, why = C._placeholder_ports(sc, pads, 0, lambda i: 0.0)
        self.assertEqual(credited, {2})
        self.assertEqual(sorted(why), pads[1:])
        hxp["streams"][2]["sink_unit_id"] = "Ghost"
        credited, why = C._placeholder_ports(ctx(hxp), pads, 0, lambda i: 0.0)
        self.assertNotIn(2, credited)
        self.assertIn("no declared unit", why[2])

    def test_isolated_streams_stay_in_feed_populations(self):
        """R3.8 gap found by the extended deletion test: deleting the
        source_unit_id of rich_doc's unpriced 'waste' product dropped it
        from CMP-FEED-02 (0.8 -> 1.0). R5.8: a missing endpoint key makes
        the stream dangling (never a boundary), and dangling streams stay
        in the CMP-FEED-01 and CMP-FEED-02 populations scoring 0: the
        orphaned waste stream is dangling, not isolated, and FEED-02 stays
        0.8; a feed whose sink_unit_id is deleted drops from 0.75 to 0 in
        FEED-01 (0.875 -> 0.5); an explicitly isolated stream ('None' at
        both ends) is still a feed of FEED-01."""
        with name_lookup(RICH_NAMES):
            doc = rich_doc()
            self.assertAlmostEqual(run(C._score_feed_02, doc)[0], 0.8)
            orphan = deleted(doc, ("streams", 1, "source_unit_id"))
            self.assertEqual((ctx(orphan).isolated, ctx(orphan).dangling),
                             ([], [1]))
            self.assertAlmostEqual(run(C._score_feed_02, orphan)[0], 0.8)
            half = one_unit_doc(spec_unit("Mixer"), ins=({}, {"T": None}))
            before = run(C._score_feed_01, half)[0]
            self.assertAlmostEqual(before, 0.875)
            lost = deleted(half, ("streams", 1, "sink_unit_id"))
            score, message, missing = run(C._score_feed_01, lost)
            self.assertAlmostEqual(score, 0.5)
            self.assertIn("1 dangling stream(s)", message)
            self.assertIn("sink_unit_id: not stated (dangling", missing[0])
            isolated = copy.deepcopy(half)
            isolated["streams"][1]["sink_unit_id"] = "None"
            self.assertEqual(ctx(isolated).isolated, [1])
            self.assertAlmostEqual(run(C._score_feed_01, isolated)[0], 0.875)

    def test_distillation_spec_plausibility(self):
        """R3.11: _spec_above_one / _spec_open_fraction; _valid_spec('k')
        needs > 1 for the distillation family (1.0 -> False, 1.2 -> True)
        but only > 0 elsewhere (an adsorption column's k, 0.5 -> True); Lr,
        Hr, y_top, x_bot must lie strictly inside (0, 1) (0, 1 -> False;
        0.99 -> True); _explicit_keys(unit, 'distillation') drops k = 1 and
        Lr = 1 and keeps LHK."""
        self.assertTrue(C._spec_above_one(1.2))
        self.assertFalse(C._spec_above_one(1.0))
        self.assertTrue(C._spec_open_fraction(0.5))
        self.assertFalse(C._spec_open_fraction(1.0))
        self.assertFalse(C._valid_spec("k", 1.0, "distillation"))
        self.assertTrue(C._valid_spec("k", 1.2, "distillation"))
        self.assertTrue(C._valid_spec("k", 0.5, None))
        for key in ("Lr", "Hr", "y_top", "x_bot"):
            self.assertFalse(C._valid_spec(key, 0.0), key)
            self.assertFalse(C._valid_spec(key, 1.0), key)
            self.assertTrue(C._valid_spec(key, 0.99), key)
        unit = {"design_input_specs": {"k": 1.0, "Lr": 1.0, "Hr": 0.99,
                                       "LHK": ["Ethanol", "Water"]}}
        self.assertEqual(C._explicit_keys(unit, "distillation"), {"Hr", "LHK"})
        self.assertEqual(C._explicit_keys(unit), {"k", "Hr", "LHK"})

    def test_integrity_not_applicable_without_flowsheet(self):
        """R3.11: CMP-INT-02 and CMP-INT-04 are not applicable to a document
        with neither units nor streams (even with XREF-01 / QU-01 results),
        and scored again as soon as either exists."""
        results = (check("XREF-01", "pass"), check("QU-01", "pass"),
                   check("QU-02", "pass"))
        for doc in ({}, {"metadata": {"sff_version": "0.2.0"}}):
            for scorer in (C._score_int_02, C._score_int_04):
                score, message, _missing = run(scorer, doc, results)
                self.assertIsNone(score)
                self.assertIn("no units and no streams", message)
        doc = {"units": [{"id": "U1"}]}
        self.assertEqual(run(C._score_int_02, doc, results)[0], 1.0)
        self.assertEqual(run(C._score_int_04, doc, results)[0], 1.0)

    def test_cost_06_size_upper_bound(self):
        """R3.11: _bounded_item names a power-law item stating a positive
        size_upper_bound whose basis ('Volume') has a numeric design result
        (N = ceil(S / ub) is derivable), else None (no result, ub 0, a
        custom_function item); _count_source prefers a 'Number of ...'
        design result; _cost_06_unit -> DERIVED_CREDIT with the source in
        the locator, else 0."""
        bounded = dict(POWER_LAW, size_upper_bound=10.0)
        unit = {"purchase_cost_correlations": {"Tank": bounded},
                "design_results": {"Volume": 25.0}}
        self.assertEqual(C._bounded_item(unit), "Tank")
        self.assertEqual(C._count_source(unit),
                         "purchase_cost_correlations['Tank'].size_upper_bound")
        for corr, results in ((bounded, {}), (dict(bounded, size_upper_bound=0),
                                             {"Volume": 25.0}),
                              (dict(bounded, correlation_type="custom_function"),
                               {"Volume": 25.0})):
            self.assertIsNone(C._bounded_item(
                {"purchase_cost_correlations": {"Tank": corr},
                 "design_results": results}))
        counted = dict(unit, design_results={"Volume": 25.0, "Number of tanks": 3})
        self.assertEqual(C._count_source(counted),
                         "design_results['Number of tanks']")
        self.assertIsNone(C._count_source({}))
        doc = costed(bounded, {"Volume": 25.0})
        score, gaps = C._cost_06_unit(ctx(doc), 0)
        self.assertEqual(score, C.DERIVED_CREDIT)
        self.assertIn("size_upper_bound", gaps[0])
        self.assertEqual(C._cost_06_unit(ctx(costed(POWER_LAW, {"Volume": 25.0})),
                                         0)[0], 0.0)

    def test_cost_07_message_explains_derived_credit(self):
        """R3.11: the CMP-COST-07 message counts the units earning derived
        credit as exactly known core classes (class-default lifetime); a
        core Pump without lifetimes -> '0 of 1 ... 1 more earn derived
        credit (0.75)'."""
        doc = unit_doc(spec_unit("Pump", {"P": 2e5}))
        doc["units"][0]["purchase_costs"] = {"Pump": 1000.0}
        score, message, _missing = run(C._score_cost_07, doc)
        self.assertEqual(score, C.DERIVED_CREDIT)
        self.assertIn("1 more earn derived credit (0.75)", message)
        self.assertIn("class-default lifetime", message)

    def test_deleting_any_value_never_raises_score_r3(self):
        """Deletion monotonicity (R1.0) over r3_rich_doc (degC / bar / kg/s
        quantity units, database-resolved CAS numbers, an unresolvable
        registry id on Fiber, an empty placeholder feed into the exactly
        known Mixer), both thermo variants, ids and stream endpoints
        included (R3.8): deleting any value below the top level raises
        neither the overall score nor any scored criterion; deleting a unit
        string, Fiber's registry id, a CAS number or the Mixer's id is
        among the paths; more than half the deletions lower the score."""
        with name_lookup(R3_RECORDS):
            for single_thermo in (False, True):
                doc = r3_rich_doc(single_thermo)
                paths = dict(deletable_paths(doc, skip=frozenset()))
                for path in (("quantity_units_global", "temperature",
                              "quantity_units"),
                             ("chemicals", 4, "registry_id"),
                             ("chemicals", 0, "registry_id"),
                             ("units", 1, "id")):
                    self.assertEqual(paths.get(path), "leaf", path)
                raised, lowered, n = deletion_violations(doc, skip=frozenset())
                with self.subTest(single_thermo=single_thermo):
                    self.assertGreater(n, 200)
                    self.assertEqual(raised, [])
                    self.assertGreater(lowered, n // 2)

    def test_junk_never_beats_absence_r3(self):
        """R2.0 on r3_rich_doc: every scalar leaf replaced by '' or ['x']
        (a quantity-unit string included: junk units state nothing, like an
        absent one) scores no criterion above its deletion. Expected: no
        violation."""
        with name_lookup(R3_RECORDS):
            doc = r3_rich_doc()
            leaves = [p for p, kind in deletable_paths(doc) if kind == "leaf"]
            violations = []
            for path in leaves:
                absent = criterion_results(deleted(doc, path))
                sentinel = "quantity_units_for_design_results" in path
                for junk in ((["x"],) if sentinel else ("", ["x"])):
                    spoiled = copy.deepcopy(doc)
                    node = spoiled
                    for key in path[:-1]:
                        node = node[key]
                    node[path[-1]] = junk
                    for cid, r in criterion_results(spoiled).items():
                        if r.score is not None and (
                                absent[cid].score is None
                                or r.score > absent[cid].score + 1e-12):
                            violations.append((path, junk, cid, r.score,
                                               absent[cid].score))
        self.assertIn(("quantity_units_global", "pressure", "quantity_units"),
                      leaves)
        self.assertEqual(violations, [])


# ------------------------------------------------ Revision 4 (part A)

def _metadata_line(pubchem, cas, formula, mw, smiles, names):
    """One identifier-file line in the `chemicals` TSV layout: pubchem id,
    CAS, formula, MW, SMILES, InChI, InChIKey, iupac name, common name,
    synonyms (`names[0]` is both iupac and common name)."""
    return "\t".join([str(pubchem), cas, formula, str(mw), smiles,
                      f"{formula}/c", f"KEY{pubchem}", names[0], names[0],
                      *names[1:]]) + "\n"


def metadata_db(folder):
    """A _MetadataDB over tiny identifier files written into `folder`: the
    small file knows acetic acid (synonym 'acetate'), the anion file the
    acetate anion (also 'acetate'), the large file carbon monoxide (formula
    'CO') and methanol (SMILES 'CO'); one fake element, oxygen (number 8,
    a homonuclear gas). CAS checking, CAS-to-int and formula serialisation
    are simple stand-ins for the `chemicals` functions."""
    files = dict.fromkeys(C._METADATA_FILES, "")
    files["chemical identifiers pubchem small.tsv"] = _metadata_line(
        176, "64-19-7", "C2H4O2", 60.05, "CC(=O)O",
        ["acetic acid", "acetate;ethanoic acid"])
    files["Anion db.tsv"] = _metadata_line(
        175, "71-50-1", "C2H3O2-", 59.04, "CC(=O)[O-]", ["acetate"])
    files["chemical identifiers pubchem large.tsv"] = (
        _metadata_line(281, "630-08-0", "CO", 28.01, "[C-]#[O+]",
                       ["carbon monoxide"])
        + _metadata_line(887, "67-56-1", "CH4O", 32.04, "CO", ["methanol"]))
    for name, text in files.items():
        Path(folder, name).write_text(text, encoding="utf-8")
    oxygen = types.SimpleNamespace(
        name="Oxygen", symbol="O", MW=15.999, CAS="17778-80-2", PubChem=977,
        smiles="[O]", InChI="O", InChI_key="QVGXLLKOCUKJST", number=8)

    def serialize(formula):
        if not formula[:1].isupper():
            raise ValueError(formula)
        return formula

    return C._MetadataDB(folder, [oxygen], {8},
                         lambda text: bool(C._CAS_RE.match(text)),
                         lambda text: int(text.replace("-", "")), serialize)


# Molar masses thermosteam's Chemical(ID) gives the review's CamelCase ids
# (observed with the thermosteam clone this revision was written against).
THERMOSTEAM_MW = {"AceticAcid": 60.05196, "CarbonMonoxide": 28.0101,
                  "SuccinicAcid": 118.08804, "SulfuricAcid": 98.07848,
                  "AmmoniumSulfate": 132.13952, "CalciumDihydroxide": 74.09268}


def r4_rich_doc():
    """r3_rich_doc() in internal units, made to exercise the Revision 4
    tiers while staying CONSISTENT (every included_in_thermo agrees with
    the stream phases -- all streams are liquid): Glucose flagged false
    (locked, seen only in 'l': the flag names the phase); an unreferenced
    thermosteam built-in 'Cellulose' (false, never shown: 0.5 by its flag,
    0.75 by the built-in's locked 's'); an unreferenced, database-resolved
    'Methanol' (CAS 67-56-1) and an unreferenced formula-only 'X1'
    (C5H8O2, lowering CMP-THM-02 through R4.7); feedB given as a legacy
    flat composition tagging its entry 'l' (R4.6)."""
    doc = r3_rich_doc(convert=False)
    for chem in doc["chemicals"]:
        if chem["id"] == "Glucose":
            chem["included_in_thermo"] = False
    doc["chemicals"] += [
        {"id": "Cellulose", "formula": "C6H10O5", "molar_mass": 162.14,
         "included_in_thermo": False},
        {"id": "Methanol", "registry_id": "67-56-1", "formula": "CH4O",
         "molar_mass": 32.04, "included_in_thermo": False},
        {"id": "X1", "formula": "C5H8O2", "molar_mass": 100.12}]
    feed_b = next(s for s in doc["streams"] if s["id"] == "feedB")
    sp = feed_b["stream_properties"]
    del sp["phases"]
    sp["composition"] = [{"phase": "l", "component_name": "Water",
                          "mol_fraction": 1.0, "mass_fraction": 1.0}]
    return doc


R4_RECORDS = dict(R3_RECORDS, **{"67-56-1": (32.04186, "CH4O"),
                                 "Methanol": (32.04186, "CH4O")})


class TestRevision4PartA(unittest.TestCase):
    """Revision 4 part A: thermosteam's Chemical(ID) name lookup and
    built-in pseudo-components (R4.1), the heat-agent phase (R4.2),
    included_in_thermo in CMP-THM-04 (R4.5), the feed phase (R4.6),
    unreferenced-chemical padding (R4.7), TEA validity (R4.9) and QU-02 in
    CMP-INT-04 (R4.11), with deletion monotonicity over the new tiers."""

    def test_spaceout_words(self):
        """_spaceout_words (thermosteam's spaceout_words): a space before
        each capital not starting a word -> 'AceticAcid' -> 'Acetic Acid',
        'CalciumDihydroxide' -> 'Calcium Dihydroxide', 'CO2' -> 'C O2';
        'water' and 'acetic acid' unchanged."""
        for text, expected in (("AceticAcid", "Acetic Acid"),
                               ("CalciumDihydroxide", "Calcium Dihydroxide"),
                               ("CO2", "C O2"), ("water", "water"),
                               ("acetic acid", "acetic acid")):
            self.assertEqual(C._spaceout_words(text), expected)

    def test_builtin_table(self):
        """_builtin_key / _builtin_record / _BUILTIN_CHEMICALS (R4.1): the
        normalised key drops spaces, '-' and '_' and lower-cases
        ('Dry_Yeast' -> 'dryyeast'); 'Cellulose' -> Glucan (162.1406,
        C6H10O5, locked 's'); 'Dry Yeast' -> Yeast; 'SolubleLignin' locked
        'l'; 'CarbonBlack' formula 'C'; Rubber has no molecular formula;
        names the database resolves first (Glucan, Lignin, Xylitol,
        Cellobiose, H3PO4, Glucose) and 'SolubleProtein' are not keys;
        blank / non-string -> None. Every entry's molar mass agrees with its
        formula within 1e-4 (relative) where it has one."""
        self.assertEqual(C._builtin_key(" Dry_Yeast "), "dryyeast")
        self.assertEqual(C._builtin_record("Cellulose"),
                         C._Builtin("Glucan", 162.1406, "C6H10O5", "s"))
        self.assertEqual(C._builtin_record("Dry Yeast").name, "Yeast")
        self.assertEqual(C._builtin_record("SolubleLignin").phase, "l")
        self.assertEqual(C._builtin_record("CarbonBlack").formula, "C")
        self.assertIsNone(C._builtin_record("Rubber").formula)
        for name in ("Glucan", "Lignin", "Xylitol", "Cellobiose", "H3PO4",
                     "Glucose", "SolubleProtein", "", None, ["Ash"]):
            self.assertIsNone(C._builtin_record(name), name)
        for key, record in C._BUILTIN_CHEMICALS.items():
            self.assertEqual(key, C._builtin_key(key))
            self.assertIn(record.phase, ("s", "l"))
            if record.formula:
                derived = C._validator()._molar_mass_from_formula(record.formula)
                self.assertAlmostEqual(derived / record.MW, 1.0, places=4,
                                       msg=key)

    def test_metadata_db_mirrors_thermosteam_search(self):
        """_MetadataDB (thermosteam's ChemicalMetadataDB over identifier
        files; load / _get / _search / search): starts with the elements
        only ('monatomic oxygen'), and a miss loads the next file from the
        END of _METADATA_FILES -- 'AceticAcid' (CamelCase split to 'acetic
        acid') loads just the small file; 'acetate' keeps the FIRST record
        claiming it (acetic acid, not the anion loaded later); 'CO' is
        carbon monoxide by formula after the names miss (not methanol by
        SMILES); 'Carbon_Monoxide' reads '_' as a space; '64-19-7' by CAS;
        'SMILES=CO' is methanol; 'PubChem=abc' and an unknown name -> None
        after every file is loaded; _search never loads a file."""
        with tempfile.TemporaryDirectory() as folder:
            db = metadata_db(folder)
            self.assertEqual(len(db.unloaded), 6)
            self.assertEqual(db._get("name", "monatomic oxygen").formula, "O")
            self.assertIsNone(db._search("AceticAcid"))
            self.assertEqual(db.search("AceticAcid").MW, 60.05)
            self.assertEqual(len(db.unloaded), 5)
            self.assertIsNone(db.search("Nothing Known"))
            self.assertEqual(db.unloaded, [])
            self.assertEqual(db.search("Acetate").MW, 60.05)
            self.assertEqual(db.search("CO").name, "carbon monoxide")
            self.assertEqual(db.search("Carbon_Monoxide").MW, 28.01)
            self.assertEqual(db.search("64-19-7").name, "acetic acid")
            self.assertEqual(db.search("SMILES=CO").name, "methanol")
            self.assertIsNone(db.search("PubChem=abc"))
            db.load(Path(folder, "Anion db.tsv"))
            self.assertEqual(db._get("CAS", 64197).name, "acetic acid")

    def test_chemicals_search_real_database(self):
        """_chemicals_search / _database_record / _name_record against the
        installed `chemicals` identifier files (skipped when unavailable),
        mirroring thermosteam's Chemical(ID): the review's CamelCase ids
        AceticAcid, CarbonMonoxide, SuccinicAcid, SulfuricAcid,
        AmmoniumSulfate and CalciumDihydroxide resolve with thermosteam's
        molar masses; 'CO' is carbon monoxide (28.01), 'Acetate' acetic acid
        (60.05, first file wins); 'Lignin' resolves in the database to a
        ~1514 g/mol record (so a C8H8O3 lignin mismatches); 'Fiber' and
        'Dry_Yeast' miss the database and resolve as built-ins."""
        with name_lookup(search=C._chemicals_search()):
            if C._chemicals_search() is None:
                self.skipTest("chemicals package not importable")
            for name, mw in THERMOSTEAM_MW.items():
                self.assertAlmostEqual(C._database_record(name).MW, mw,
                                       places=3, msg=name)
            self.assertAlmostEqual(C._database_record("CO").MW, 28.0101, places=3)
            self.assertAlmostEqual(C._database_record("Acetate").MW, 60.05196,
                                   places=3)
            self.assertGreater(C._database_record("Lignin").MW, 1500)
            self.assertEqual(C._name_tier({"formula": "C8H8O3",
                                           "molar_mass": 152.15}, "Lignin"),
                             "mismatch")
            for name, builtin in (("Fiber", "Fiber"), ("Dry_Yeast", "Yeast")):
                self.assertIsNone(C._database_record(name))
                self.assertEqual(C._name_record(name).name, builtin)

    def test_chemicals_search_ignores_thermosteam_state(self):
        """Determinism (R4.1 follow-up): _chemicals_search always searches
        its own import-light _MetadataDB replica, so the same ids resolve
        to the same records whether or not thermosteam is in sys.modules
        and has replaced chemicals.identifiers.pubchem_db with its own
        database (skipped when `chemicals` is unavailable). With a fake
        thermosteam module and a fake 'unloaded_files' database installed,
        the records of Water (also padded with spaces), AceticAcid (the
        CamelCase-split name) and Ethanol are unchanged, SolubleProtein
        still misses, _database_record (fresh cache) agrees, and the fake
        database is never called."""
        search = C._chemicals_search()
        if search is None:
            self.skipTest("chemicals package not importable")
        from chemicals import identifiers
        names = ("Water", "  Water ", "AceticAcid", "Ethanol", "SolubleProtein")
        baseline = [search(n) for n in names]
        self.assertEqual([r is None for r in baseline],
                         [False, False, False, False, True])
        with name_lookup(search=search):
            records = [C._database_record(n) for n in names]
        self.assertEqual(records, baseline)
        calls = []
        fake_db = types.SimpleNamespace(
            unloaded_files=[], search=lambda ID: calls.append(ID) or "bogus")
        saved_db = identifiers.__dict__.get("pubchem_db", C)
        saved_module = sys.modules.get("thermosteam")
        identifiers.pubchem_db = fake_db
        sys.modules["thermosteam"] = types.ModuleType("thermosteam")
        try:
            self.assertEqual([search(n) for n in names], baseline)
            with name_lookup(search=search):
                self.assertEqual([C._database_record(n) for n in names],
                                 baseline)
            self.assertEqual(calls, [])
        finally:
            if saved_db is C:
                del identifiers.pubchem_db
            else:
                identifiers.pubchem_db = saved_db
            if saved_module is None:
                del sys.modules["thermosteam"]
            else:
                sys.modules["thermosteam"] = saved_module
        self.assertEqual([search(n) for n in names], baseline)

    def test_review_builtins_on_synthetic_doc(self):
        """R4.1 review case (real database; skipped when unavailable): a doc
        whose streams carry Water (CAS 7732-18-5), Cellulose and AceticAcid,
        each stating molar mass and formula, scored CMP-THM-05 = 0 and
        CMP-THM-02 = 0.5 (Cellulose and AceticAcid 'formula only') before;
        now Cellulose resolves as the built-in Glucan and AceticAcid by its
        CamelCase-split name, both verified -> CMP-THM-02 = (1 + 0.75 +
        0.75) / 3, CMP-THM-05 not applicable, and Cellulose's built-in
        locked 's' gives CMP-THM-04 derived credit."""
        if C._chemicals_search() is None:
            self.skipTest("chemicals package not importable")
        doc = valid_doc()
        doc["chemicals"] = [
            {"id": "Water", "registry_id": "7732-18-5", "formula": "H2O",
             "molar_mass": 18.01528},
            {"id": "Cellulose", "formula": "C6H10O5", "molar_mass": 162.1406},
            {"id": "AceticAcid", "formula": "C2H4O2", "molar_mass": 60.05196}]
        for stream in doc["streams"]:
            stream["stream_properties"].pop("composition", None)
            stream["stream_properties"]["phases"]["l"]["composition"] = [
                {"component_name": n, "mol_fraction": x} for n, x in
                (("Water", 0.5), ("Cellulose", 0.25), ("AceticAcid", 0.25))]
        with name_lookup(search=C._chemicals_search()):
            thm02, _m, missing = run(C._score_thm_02, doc)
            self.assertAlmostEqual(thm02, (1 + 0.75 + 0.75) / 3)
            self.assertIn("built-in pseudo-component (Glucan)",
                          " ".join(missing))
            self.assertIsNone(run(C._score_thm_05, doc)[0])
            self.assertEqual(C._builtin_phase(doc["chemicals"][1], "Cellulose"),
                             "s")
            self.assertIsNone(C._builtin_phase(doc["chemicals"][2], "AceticAcid"))
            self.assertIsNone(C._builtin_phase({"molar_mass": 180.16},
                                               "Cellulose"))

    def test_thm_04_builtin_locked_phase(self):
        """_thm_04_credit / _builtin_phase (R4.5 tier c): a built-in
        'Cellulose' (fake database resolving nothing) with no flag and no
        stream phases -> DERIVED_CREDIT from its locked 's', noted; with a
        molar mass contradicting the built-in -> 0; a false flag and no
        streams -> max(0.5, 0.75) = 0.75; a non-built-in 'Mystery' with a
        false flag -> FLAG_UNOBSERVED_CREDIT; a true flag -> 1."""
        with name_lookup():
            credit, note = C._thm_04_credit({"molar_mass": 162.14}, "Cellulose",
                                            set())
            self.assertEqual(credit, C.DERIVED_CREDIT)
            self.assertIn("built-in's locked phase 's'", note)
            self.assertEqual(C._thm_04_credit({"molar_mass": 180.16},
                                              "Cellulose", set())[0], 0.0)
            self.assertEqual(C._thm_04_credit(
                {"included_in_thermo": False}, "Cellulose", set())[0],
                C.DERIVED_CREDIT)
            self.assertEqual(C._thm_04_credit(
                {"included_in_thermo": False}, "Mystery", set())[0],
                C.FLAG_UNOBSERVED_CREDIT)
            self.assertEqual(C._thm_04_credit(
                {"included_in_thermo": True}, "Mystery", set()), (1.0, None))

    def test_referenced_mean(self):
        """_referenced_mean (R4.7) over [(score, referenced)]: nothing
        referenced -> lowest single score ([1, 0] -> 0), so deleting the
        last reference never raises it ([0.2 ref, 1, 1] -> 0.2 before and
        0.2 after the reference goes; the plain mean would give 0.733);
        unreferenced scores at or above the
        referenced mean are left out ([1 ref, 1 unref] -> 1; [0.25 ref,
        1 x 12 unref] -> 0.25); lower ones join in ascending order while
        they lower it ([1 ref, 0 unref, 1 unref] -> 0.5); never above
        min(referenced mean, overall mean); and a low-scoring chemical
        losing its reference never raises it: [1, 0 ref; 1, 1 unref] -> 0.5
        before, 0.5 after 0 becomes unreferenced (the literal min(ref, all)
        would rise from 0.5 to 0.75)."""
        mean = C._referenced_mean
        self.assertEqual(mean([(1.0, False), (0.0, False)]), 0.0)
        self.assertAlmostEqual(
            mean([(0.2, True), (1.0, False), (1.0, False)]), 0.2)
        self.assertAlmostEqual(
            mean([(0.2, False), (1.0, False), (1.0, False)]), 0.2)
        self.assertEqual(mean([(1.0, True), (1.0, False)]), 1.0)
        self.assertEqual(mean([(0.25, True)] + [(1.0, False)] * 12), 0.25)
        self.assertAlmostEqual(mean([(1.0, True), (0.0, False), (1.0, False)]),
                               0.5)
        before = [(1.0, True), (0.0, True), (1.0, False), (1.0, False)]
        after = [(1.0, True), (0.0, False), (1.0, False), (1.0, False)]
        self.assertAlmostEqual(mean(before), 0.5)
        self.assertAlmostEqual(mean(after), 0.5)
        for scored in (before, after, [(0.5, True), (0.2, False), (0.9, False),
                                       (0.1, False)]):
            ref = [s for s, r in scored if r]
            bound = min(sum(ref) / len(ref),
                        sum(s for s, _r in scored) / len(scored))
            self.assertLessEqual(mean(scored), bound + 1e-12, scored)

    def test_padding_unreferenced_chemicals_never_raises(self):
        """R4.7 review case: a sparse doc (valid_doc's Ethanol reduced to
        its id; the fake database resolving only the padding's CAS numbers)
        padded with 12 unreferenced chemicals, each with a resolvable CAS
        and a molar mass: CMP-THM-02 stays 0, CMP-THM-03 stays 0, and
        the overall score does not rise (the message counts the 12
        unreferenced chemicals)."""
        cas = [next(c for c in (f"{100 + k}-00-{d}" for d in range(10))
                    if C._valid_cas(c)) for k in range(12)]
        with name_lookup({c: (30.0 + k, None) for k, c in enumerate(cas)}):
            sparse = valid_doc()
            for key in ("formula", "molar_mass", "included_in_thermo", "index"):
                remove(sparse, f"chemicals/0/{key}")
            before = (run(C._score_thm_02, sparse)[0],
                      run(C._score_thm_03, sparse)[0], overall(sparse))
            padded = copy.deepcopy(sparse)
            padded["chemicals"] += [
                {"id": f"Pad{k}", "registry_id": c, "molar_mass": 30.0 + k}
                for k, c in enumerate(cas)]
            thm02, message, _m = run(C._score_thm_02, padded)
            after = (thm02, run(C._score_thm_03, padded)[0], overall(padded))
        self.assertEqual(before[:2], (0.0, 0.0))
        self.assertEqual(after[:2], (0.0, 0.0))
        self.assertLessEqual(after[2], before[2] + 1e-12)
        self.assertIn("12 unreferenced chemical(s) can only lower", message)

    def test_feed_phases_stated(self):
        """_feed_phases_stated / CMP-FEED-01 (R4.6): a per-phase feed or a
        legacy flat composition tagging every entry with a thermosteam phase
        -> stated (composition part 1); an entry untagged (or tagged 'x', or
        with a list as tag) -> not stated, and legacy_doc's feed with its tag
        deleted scores its composition part UNTAGGED_COMPOSITION_CREDIT
        (0.75) -> (3 + 0.75) / 4 with a locator naming the phase."""
        self.assertTrue(C._feed_phases_stated(valid_doc()["streams"][0]))
        doc = legacy_doc()
        self.assertTrue(C._feed_phases_stated(doc["streams"][0]))
        full = run(C._score_feed_01, doc)[0]
        self.assertEqual(full, 1.0)
        entry = doc["streams"][0]["stream_properties"]["composition"][0]
        for tag in ("x", ["l"]):
            entry["phase"] = tag
            self.assertFalse(C._feed_phases_stated(doc["streams"][0]), tag)
        del entry["phase"]
        score, _m, missing = run(C._score_feed_01, doc)
        self.assertAlmostEqual(score, (3 + C.UNTAGGED_COMPOSITION_CREDIT) / 4)
        self.assertEqual(missing, ["streams[feed].stream_properties."
                                   "composition[*].phase (untagged: BioSTEAM "
                                   "would assume liquid)"])
        self.assertEqual(C.UNTAGGED_COMPOSITION_CREDIT, 0.75)

    def test_r4_rich_doc_scores(self):
        """r4_rich_doc() is consistent and exercises the new tiers:
        CMP-THM-04 = (4 x 1 [Water/Ethanol/CO2 true, Glucose false in 'l'] +
        1 [Fiber true] + 0.75 [Cellulose built-in] + 0.5 [Methanol false,
        unseen] + 0 [X1]) / 8; CMP-THM-02 below the referenced mean (X1's
        0.25 is let in, Methanol's 1 is not); CMP-FEED-01 = 2.5 / 3 (feedB's
        flat composition is tagged; R6.3: the empty 'spare' feed into the
        variable-inlet Mixer M1 is no placeholder port, 0.5); CMP-UTIL-01 = 1 (both heat agents state one
        phase); CMP-INT-04 = 1 (QU-01 and QU-02 pass); CMP-SYS-02 = 3/14."""
        with name_lookup(R4_RECORDS):
            results = criterion_results(r4_rich_doc())
        self.assertAlmostEqual(results["CMP-THM-04"].score,
                               (5 + 0.75 + 0.5 + 0) / 8)
        referenced = (4 * 1.0) / 4
        self.assertLess(results["CMP-THM-02"].score, referenced)
        for cid, expected in (("CMP-FEED-01", 2.5 / 3), ("CMP-UTIL-01", 1.0),
                              ("CMP-INT-04", 1.0), ("CMP-SYS-02", 3 / 14)):
            self.assertAlmostEqual(results[cid].score, expected, msg=cid)

    def test_deleting_any_value_never_raises_score_r4(self):
        """Deletion monotonicity (R1.0) over r4_rich_doc -- consistent flags,
        a built-in, unreferenced chemicals above and below the referenced
        mean, a tagged legacy feed composition, heat agents with phases,
        TEA currency/year, QU-02 among the passing checks -- ids and stream
        endpoints included: deleting any value below the top level raises
        neither the overall score nor any scored criterion; the new fields
        (every included_in_thermo, feedB's phase tag, each heat agent's
        phase, TEA_currency, TEA_year) are among the paths; more than half
        the deletions lower the score. Replacing any of those new fields by
        '' or ['x'] scores no criterion above its deletion (R2.0)."""
        with name_lookup(R4_RECORDS):
            doc = r4_rich_doc()
            paths = dict(deletable_paths(doc, skip=frozenset()))
            feed_b = [i for i, s in enumerate(doc["streams"])
                      if s["id"] == "feedB"][0]
            new_fields = [("chemicals", i, "included_in_thermo")
                          for i, c in enumerate(doc["chemicals"])
                          if "included_in_thermo" in c]
            new_fields += [("streams", feed_b, "stream_properties",
                            "composition", 0, "phase"),
                           ("utilities", "heat_utilities", 0, "composition", 0,
                            "phase"),
                           ("utilities", "heat_utilities", 1, "composition", 0,
                            "phase"),
                           ("metadata", "TEA_currency"),
                           ("metadata", "TEA_year")]
            for path in new_fields:
                self.assertEqual(paths.get(path), "leaf", path)
            raised, lowered, n = deletion_violations(doc, skip=frozenset())
            self.assertEqual(raised, [])
            self.assertGreater(lowered, n // 2)
            violations = []
            for path in new_fields:
                absent = criterion_results(deleted(doc, path))
                for junk in ("", ["x"], "dollars", 99999):
                    spoiled = copy.deepcopy(doc)
                    node = spoiled
                    for key in path[:-1]:
                        node = node[key]
                    node[path[-1]] = junk
                    for cid, r in criterion_results(spoiled).items():
                        if r.score is not None and (
                                absent[cid].score is None
                                or r.score > absent[cid].score + 1e-12):
                            violations.append((path, junk, cid))
        self.assertEqual(violations, [])


# ------------------------------------------- Revision 4 part B (R4.3-R4.10)

def sparse_doc(unit_types, edges):
    """A sparse extracted flowsheet: units carry only id + unit_type
    ({id: unit_type}), streams only id and endpoints (edges: (id, source,
    sink)) -- no states, specs, costs or chemicals."""
    return {"metadata": {"sff_version": "0.2.1", "TEA_currency": "USD"},
            "units": [{"id": uid, "unit_type": t} for uid, t in unit_types.items()],
            "streams": [{"id": sid, "source_unit_id": a, "sink_unit_id": b}
                        for sid, a, b in edges]}


# rich_doc's topology plus a vent from R1 (so R1 and C1 have 2 outlets).
SPARSE_EDGES = (("feedA", "None", "C1"), ("waste", "C1", "None"),
                ("s0", "C1", "M1"), ("feedB", "None", "M1"),
                ("s1", "M1", "P1"), ("s2", "P1", "H1"), ("s3", "H1", "R1"),
                ("prod", "R1", "None"), ("vent", "R1", "None"))
SPARSE_TYPES = {"C1": "Cleaning system", "M1": "Mixer", "P1": "Pump",
                "H1": "Heat exchanger", "R1": "Fermentation"}


class TestRevision4PartB(unittest.TestCase):
    """Revision 4 part B: unit_type never confers exact identity (R4.3),
    head-noun keyword matching (R4.4), port-count plausibility (R4.8) and
    the UOP-04 mass-balance guard (R4.10)."""

    def test_equipment_matches_and_phrase_family(self):
        """_equipment_matches('wastewater pump') -> the pump match
        (end 15, -start 11) is the max (the head); _phrase_family on
        normalized phrases -> R4.4 head-noun families: a generic head
        ('system') lets a service modifier decide ('chilled water system' ->
        facility) and falls back to the equipment otherwise ('pump system'
        -> pump); a junction marker wins ('mock mixer'); nothing -> None."""
        matches = C._equipment_matches("wastewater pump")
        self.assertEqual(max(matches), (15, -11, "pump"))
        self.assertEqual(C._equipment_matches("widget"), [])
        for text, family in (("chilled water system", "facility"),
                             ("pump system", "pump"),
                             ("mock mixer", "junction"),
                             ("flash drum", "flash"),
                             ("economizer heat exchanger", "hx_process"),
                             ("widget", None)):
            self.assertEqual(C._phrase_family(text), family, text)

    def test_keyword_family_head_nouns(self):
        """R4.4 review cases through _keyword_family: 'Wastewater pump' ->
        pump, 'Waste water cooler' -> hx_utility, 'Boiler feed water pump'
        -> pump, 'Chilled water pump' -> pump, 'Process water tank' -> tank,
        'Feed/effluent heat exchanger' -> hx_process, 'Wastewater treatment'
        -> the wastewater rules (the reactor family, R2.10), 'Neutralization
        tank' and 'Fermentation tank' -> reactor, 'Boiler' -> boiler; also
        'Recuperator' -> hx_process and 'Chilled water package' ->
        facility."""
        cases = {"Wastewater pump": "pump", "Waste water cooler": "hx_utility",
                 "Boiler feed water pump": "pump", "Chilled water pump": "pump",
                 "Process water tank": "tank",
                 "Feed/effluent heat exchanger": "hx_process",
                 "Wastewater treatment": "reactor",
                 "Neutralization tank": "reactor",
                 "Fermentation tank": "reactor", "Boiler": "boiler",
                 "Recuperator": "hx_process",
                 "Chilled water package": "facility"}
        for text, family in cases.items():
            self.assertEqual(C._keyword_family(text), family, text)

    def test_port_limits_and_contradiction(self):
        """_port_limits: an exact core class -> its _PORT_LIMITS entry
        (Mixer (None, 1), HXprocess (2, 2)); not core -> the family
        convention (hx_utility (1, 1)); no convention (splitter) -> (None,
        None). _port_contradiction: Mixer with 2 non-empty outlets -> '2
        non-empty outlet(s), at most 1 allowed'; a pump family with 2 inlets
        -> the inlet message; within limits, or ports None -> None;
        _ports_plausible is its negation. Every core class has an entry."""
        self.assertEqual(C._port_limits("Mixer", "mixer", True), (None, 1))
        self.assertEqual(C._port_limits("HXprocess", "hx_process", True), (2, 2))
        self.assertEqual(C._port_limits("HydrolysateHeatExchanger",
                                        "hx_utility", False), (1, 1))
        self.assertEqual(C._port_limits(None, "splitter", False), (None, None))
        self.assertEqual(C._port_contradiction("Mixer", "mixer", True, (3, 2)),
                         "2 non-empty outlet(s), at most 1 allowed")
        self.assertEqual(C._port_contradiction(None, "pump", False, (2, 1)),
                         "2 non-empty inlet(s), at most 1 allowed")
        self.assertIsNone(C._port_contradiction("Mixer", "mixer", True, (5, 1)))
        self.assertIsNone(C._port_contradiction("Mixer", "mixer", True, None))
        self.assertTrue(C._ports_plausible(None, "splitter", False, (4, 3)))
        self.assertFalse(C._ports_plausible(None, "valve", False, (1, 2)))
        self.assertEqual(set(C._CORE_CLASSES) - set(C._PORT_LIMITS), set())

    def test_unit_class_port_plausibility(self):
        """R4.8 through _unit_class(ports=...): a DSM 'Mixer' (core) with 2
        non-empty outlets is contradicted -> its class and family are
        dropped and the unit_type 'Separator' keyword family is used
        (None, 'splitter', 0.5); a unit_type-only 'Mixer' with 2 outlets ->
        (None, None, 0.0); the same with 1 outlet -> (None, 'mixer', 0.5);
        the custom HydrolysateHeatExchanger (hx_utility) with 2 inlets and
        2 outlets -> nothing; a contradicted unit_type family falls back to
        the key-set inference ('Pump' with dP_design and 2 outlets ->
        nothing, since the inferred pump family is contradicted too)."""
        U = C._unit_class
        mixer = dict(cls("Mixer"), unit_type="Separator")
        self.assertEqual(U(mixer, ports=(1, 1)), ("Mixer", "mixer", 1.0))
        self.assertEqual(U(mixer, ports=(1, 2)), (None, "splitter", 0.5))
        self.assertEqual(U({"unit_type": "Mixer"}, ports=(1, 2)),
                         (None, None, 0.0))
        self.assertEqual(U({"unit_type": "Mixer"}, ports=(3, 1)),
                         (None, "mixer", 0.5))
        custom = spec_unit("HydrolysateHeatExchanger", url=BIP_URL)
        self.assertEqual(U(custom, ports=(2, 2)), (None, None, 0.0))
        reg = {"Pump": {"design_input_spec_params": {"P": {}, "dP_design": {}}}}
        pump = {"unit_type": "Pump", "design_input_specs": {"dP_design": 1.0}}
        self.assertEqual(U(pump, reg, ports=(1, 1)), (None, "pump", 0.75))
        self.assertEqual(U(pump, reg, ports=(1, 2)), (None, None, 0.0))
        # a bare 'Heat exchanger' with 2 inlets / outlets is a process-
        # process exchanger (BioSTEAM labels both kinds so); 'Heater' is not
        hx = {"unit_type": "Heat exchanger"}
        self.assertEqual(U(hx, ports=(1, 1)), (None, "hx_utility", 0.5))
        self.assertEqual(U(hx, ports=(2, 2)), (None, "hx_process", 0.5))
        self.assertEqual(U(hx, ports=(3, 2)), (None, None, 0.0))
        self.assertEqual(U({"unit_type": "Heater"}, ports=(2, 2)),
                         (None, None, 0.0))

    def test_generic_exchanger(self):
        """_generic_exchanger: a bare heat-exchanger head noun ('Heat
        exchanger', 'Hydrolysate heat exchanger', 'HX') -> True; 'Heater',
        'Condenser', 'HXutility', 'Heat exchanger network', '' and None ->
        False."""
        for text in ("Heat exchanger", "Hydrolysate heat exchanger", "HX"):
            self.assertTrue(C._generic_exchanger(text), text)
        for text in ("Heater", "Condenser", "HXutility", "Heat exchanger network",
                     "", None):
            self.assertFalse(C._generic_exchanger(text), text)

    def test_nonempty_ports_and_port_notes(self):
        """_ScoringContext.nonempty_ports: stated-empty streams are not
        counted, streams of unknown flow are (a Mixer with a zero-flow and
        a flowless second outlet -> (1, 2)); no id -> (0, 0).
        _port_notes names each contradicted claim: the DSM class 'Mixer'
        and the unit_type family 'mixer'."""
        doc = one_unit_doc(dict(cls("Mixer"), unit_type="Mixer"),
                           outs=({}, {}, {}))
        doc["streams"][2]["stream_properties"].update(
            total_mass_flow=0.0, total_molar_flow=0.0)
        del doc["streams"][3]["stream_properties"]["total_mass_flow"]
        del doc["streams"][3]["stream_properties"]["total_molar_flow"]
        sc = ctx(doc)
        self.assertEqual(sc.nonempty_ports("U1"), (1, 2))
        self.assertEqual(sc.nonempty_ports(None), (0, 0))
        self.assertEqual(C._port_notes(sc, 0), [
            "class 'Mixer' (2 non-empty outlet(s), at most 1 allowed)",
            "unit_type family 'mixer' (2 non-empty outlet(s), at most 1 "
            "allowed)"])
        self.assertEqual(sc.unit_classes[0], (None, None, 0.0))

    def test_unit_type_labels_confer_no_identity(self):
        """R4.3 sparse-document labels: a one-unit sparse doc labelled
        'Separator' scores exactly like 'Solid-liquid separator' (both
        (None, 'splitter', 0.5), every criterion equal), and 'Evaporator'
        exactly like 'Multiple-effect evaporator' ((None, 'evaporator',
        0.5)) -- a class name in unit_type confers no exact identity,
        cost-free status or class waiver."""
        edges = (("feed", "None", "U1"), ("out", "U1", "None"),
                 ("out2", "U1", "None"))
        for a, b, family in (("Separator", "Solid-liquid separator", "splitter"),
                             ("Evaporator", "Multiple-effect evaporator",
                              "evaporator")):
            da, db = sparse_doc({"U1": a}, edges), sparse_doc({"U1": b}, edges)
            with self.subTest(labels=(a, b)):
                self.assertEqual(ctx(da).unit_classes, [(None, family, 0.5)])
                self.assertEqual(ctx(db).unit_classes, [(None, family, 0.5)])
                ra, rb = criterion_results(da), criterion_results(db)
                self.assertEqual({k: r.score for k, r in ra.items()},
                                 {k: r.score for k, r in rb.items()})

    def test_mixer_relabel_does_not_pay(self):
        """R4.8 / R4.13 / R4.14 review case on a sparse doc (SPARSE_EDGES:
        R1 and C1 have 2 outlets, H1 one inlet and one outlet): relabelling
        R1, C1 and H1 'Mixer' used to raise the overall score (mixer family:
        no operating specifications, cost-free, order-free inlets, no
        reaction or utility needs). Now every relabelled claim is
        implausible -- R1's and C1's by their outlets (R4.8), H1's by its
        single inlet (R4.14: a label-only mixer needs >= 2 inlets) -- so each
        unit is (None, None, 0.0) and scores exactly as if its unit_type
        were deleted (INVALID == MISSING: every criterion equal); an unknown
        family carries every family-dependent need (R4.13), so R1 keeps its
        reaction and heat-utility needs. Neither the combined relabel nor
        any single one raises the overall score or any criterion; M1 (two
        inlets) stays a plausible label-only mixer (None, 'mixer', 0.5)."""
        base = sparse_doc(SPARSE_TYPES, SPARSE_EDGES)
        base_results = criterion_results(base)
        relabelled = sparse_doc(dict(SPARSE_TYPES, R1="Mixer", C1="Mixer",
                                     H1="Mixer"), SPARSE_EDGES)
        classes = dict(zip(SPARSE_TYPES, ctx(relabelled).unit_classes))
        for uid in ("R1", "C1", "H1"):
            self.assertEqual(classes[uid], (None, None, 0.0), uid)
        self.assertEqual(classes["M1"], (None, "mixer", 0.5))
        self.assertIn(3, ctx(relabelled).costed)
        self.assertLessEqual(overall(relabelled), overall(base))
        for uid in ("R1", "C1", "H1"):
            as_mixer = sparse_doc(dict(SPARSE_TYPES, **{uid: "Mixer"}),
                                  SPARSE_EDGES)
            unlabelled = sparse_doc(dict(SPARSE_TYPES, **{uid: ""}),
                                    SPARSE_EDGES)
            after = criterion_results(as_mixer)
            with self.subTest(unit=uid):
                self.assertEqual(
                    {k: r.score for k, r in after.items()},
                    {k: r.score for k, r in
                     criterion_results(unlabelled).items()})
                self.assertLessEqual(overall(as_mixer), overall(base))
                for cid, before in base_results.items():
                    if before.status == "scored":
                        self.assertEqual(after[cid].status, "scored", cid)
                        self.assertLessEqual(after[cid].score,
                                             before.score + 1e-12, cid)
        # R1 keeps its reactive and heat-utility needs once relabelled.
        results = criterion_results(relabelled)
        for cid in ("CMP-RXN-01", "CMP-RXN-02", "CMP-RXN-03", "CMP-RXN-04",
                    "CMP-UTIL-03", "CMP-UTIL-05"):
            self.assertEqual(results[cid].status, "scored", cid)

    def test_mass_balance(self):
        """_mass_balance on one_unit_doc: balanced -> (True, True, ''); the
        product's mass x1.1 -> (True, False, 'mass balance does not close
        (in 46.07, out 50.677)'); a stream without total_mass_flow ->
        (False, False, ...); no streams -> (False, False, 'no connected
        streams')."""
        doc = one_unit_doc(spec_unit("Pump"))
        self.assertEqual(C._mass_balance(ctx(doc), 0), (True, True, ""))
        doc["streams"][1]["stream_properties"]["total_mass_flow"] *= 1.1
        self.assertEqual(C._mass_balance(ctx(doc), 0), (
            True, False, "mass balance does not close (in 46.07, out 50.677)"))
        del doc["streams"][1]["stream_properties"]["total_mass_flow"]
        self.assertEqual(C._mass_balance(ctx(doc), 0)[:2], (False, False))
        doc["streams"] = []
        self.assertEqual(C._mass_balance(ctx(doc), 0),
                         (False, False, "no connected streams"))

    def test_uop_04_unbalanced_outlets(self):
        """R4.10 review case: rich_doc scores CMP-UOP-04 = 1; with every
        internal stream's total_mass_flow x1.1, C1 (feedA in; waste, s0
        out), M1 (s0, feedB in; s1 out) and R1 (s3 in, prod out) no longer
        close, so waste, s0, s1 and prod earn no flow credit (3 of 4 parts
        each) among the 6 internal / product streams: UOP-04 = (2 x 1 + 4 x
        0.75) / 6 with 'stated, but unbalanced at units[C1]' locators; P1
        and H1 (both sides scaled) still close. A class-implied-chemistry
        family (a DrumDryer burning natural gas without drawing O2) is
        exempt: its unbalanced product keeps full credit."""
        doc = rich_doc()
        self.assertEqual(run(C._score_uop_04, doc)[0], 1.0)
        for s in doc["streams"]:
            if "None" not in (s["source_unit_id"], s["sink_unit_id"]):
                s["stream_properties"]["total_mass_flow"] *= 1.1
        score, message, missing = run(C._score_uop_04, doc)
        self.assertAlmostEqual(score, (2 + 4 * 0.75) / 6)
        self.assertIn("4 outlet(s) of units whose mass balance does not "
                      "close earn no flow credit", message)
        self.assertTrue(any(m.startswith("streams[s0].stream_properties."
                                         "total_mass_flow") and
                            "unbalanced at units[C1]" in m for m in missing))
        dryer = one_unit_doc(spec_unit("DrumDryer"), outs=({"mass": 60.0},))
        self.assertEqual(run(C._score_uop_04, dryer)[0], 1.0)

    def test_revision_4_part_b_deletions_on_descriptive_doc(self):
        """R4.3 deletions: descriptive_doc() with its Mixer M1 also named
        exactly ('Mixer on <biosteam url>': cost-free, confidence 1) and its
        pump P1 as 'Pump on <biosteam url>'. Deleting M1's
        design_simulation_method makes it a costed mixer family (0.5),
        deleting P1's leaves the key-set-confirmed pump family (0.75), and
        deleting their unit_types changes nothing: no deletion raises the
        overall score or any criterion, and the two class deletions lower
        the overall score."""
        doc = descriptive_doc()
        doc["units"][-1].update(cls("Mixer"))
        doc["units"][0].update(cls("Pump"))
        base = criterion_results(doc)
        base_score = overall(doc)
        for position in (0, len(doc["units"]) - 1):
            for key in ("design_simulation_method", "unit_type"):
                reduced = deleted(doc, ("units", position, key))
                after = criterion_results(reduced)
                with self.subTest(unit=position, key=key):
                    for cid, before in base.items():
                        if before.status == "scored":
                            self.assertEqual(after[cid].status, "scored", cid)
                            self.assertLessEqual(after[cid].score,
                                                 before.score + 1e-12, cid)
                    if key == "design_simulation_method":
                        self.assertLess(overall(reduced), base_score)
                    else:
                        self.assertEqual(overall(reduced), base_score)



# ------------------------------------ Revision 4 (R4.12-R4.14) sweeps

SWEEP_MW = {"Water": 18.015, "Glucose": 180.156, "Ethanol": 46.07,
            "CO2": 44.01}


def _sweep_sp(flows, phase, T=None, P=None):
    """Stream properties of {component: kmol/h} `flows` in one phase (mass
    flow from SWEEP_MW, so every balance closes); T / P only when given."""
    total = sum(flows.values())
    sp = {"total_molar_flow": total,
          "total_mass_flow": sum(f * SWEEP_MW[n] for n, f in flows.items()),
          "phases": {phase: {"total_molar_flow": total, "composition": [
              {"component_name": n, "mol_fraction": f / total}
              for n, f in flows.items()]}}}
    if T is not None:
        sp["temperature"] = T
    if P is not None:
        sp["pressure"] = P
    return sp


# id, source, sink, flows (kmol/h), phase, T, P, price: feeds A + B -> M1
# 'Mixer' -> P1 -> H1 -> R1 (Glucose -> 2 Ethanol + 2 CO2, X = 0.9; CO2
# vented) -> P2 -> F1 (vapor / liquid) -> S1 (split 0.5) -> two products.
SWEEP_EDGES = (
    ("feedA", "None", "M1", {"Water": 90.0, "Glucose": 10.0}, "l", 300.0,
     101325.0, 0.1),
    ("feedB", "None", "M1", {"Water": 50.0}, "l", 300.0, 101325.0, 0.0),
    ("s1", "M1", "P1", {"Water": 140.0, "Glucose": 10.0}, "l", None, None,
     None),
    ("s2", "P1", "H1", {"Water": 140.0, "Glucose": 10.0}, "l", None, None,
     None),
    ("s3", "H1", "R1", {"Water": 140.0, "Glucose": 10.0}, "l", None, None,
     None),
    ("vent", "R1", "None", {"CO2": 18.0}, "g", 310.0, 101325.0, 0.0),
    ("s4", "R1", "P2", {"Water": 140.0, "Glucose": 1.0, "Ethanol": 18.0},
     "l", None, None, None),
    ("s5", "P2", "F1", {"Water": 140.0, "Glucose": 1.0, "Ethanol": 18.0},
     "l", None, None, None),
    ("vap", "F1", "None", {"Water": 10.0, "Ethanol": 8.0}, "g", 350.0,
     101325.0, 0.5),
    ("liq", "F1", "S1", {"Water": 130.0, "Glucose": 1.0, "Ethanol": 10.0},
     "l", None, None, None),
    ("out1", "S1", "None", {"Water": 65.0, "Glucose": 0.5, "Ethanol": 5.0},
     "l", 350.0, 101325.0, 0.05),
    ("out2", "S1", "None", {"Water": 65.0, "Glucose": 0.5, "Ethanol": 5.0},
     "l", 350.0, 101325.0, 0.05))
SWEEP_TYPES = {"M1": "Mixer", "P1": "Feed pump", "H1": "Heat exchanger",
               "R1": "Fermentation reactor", "P2": "Transfer pump",
               "F1": "Flash drum", "S1": "Splitter"}
SWEEP_CLASSES = {"M1": "Mixer", "P1": "Pump", "H1": "HXutility",
                 "R1": "AnaerobicBioreactor", "P2": "Pump", "F1": "Flash",
                 "S1": "Splitter"}
SWEEP_SPECS = {"P1": {"P": 2.0e5}, "H1": {"T": 310.0}, "R1": {"tau": 10.0},
               "P2": {"P": 1.5e5}, "F1": {"T": 350.0, "P": 101325.0},
               "S1": {"split": 0.5}}
SWEEP_REACTION = {"index": 0, "equation": "Glucose -> 2 Ethanol + 2 CO2",
                  "reactant": "Glucose", "conversion": 0.9}


def sweep_doc(identity="unit_type"):
    """A sparse, consistent flowsheet for the R4.12-R4.14 deletion sweeps
    (SWEEP_EDGES): a mixer with two feeds, two pumps, a heat exchanger, a
    fermentation reactor with one well-formed reaction and a vent (2
    outlets), a flash and a splitter. Each unit states its identity ONLY
    through `identity` -- 'unit_type' (SWEEP_TYPES, descriptive labels, no
    design_simulation_method) or 'design_simulation_method'
    (SWEEP_CLASSES on the biosteam URL, no unit_type) -- plus a few spec
    keys (SWEEP_SPECS); P1 and H1 record utility results. Feeds and
    products state T and P, internal streams only flows and one-phase
    compositions (every mass and species balance closes); chemicals carry
    formula + molar mass but no registry id; the heat and power agents are
    declared, no property package, costs or TEA year."""
    streams = []
    for sid, source, sink, flows, phase, T, P, price in SWEEP_EDGES:
        stream_ = {"id": sid, "source_unit_id": source, "sink_unit_id": sink,
                   "stream_properties": _sweep_sp(flows, phase, T, P)}
        if price is not None:
            stream_["price"] = price
        streams.append(stream_)
    units = []
    for uid in SWEEP_TYPES:
        unit = {"id": uid}
        if identity == "unit_type":
            unit["unit_type"] = SWEEP_TYPES[uid]
        else:
            unit["design_simulation_method"] = (f"{SWEEP_CLASSES[uid]} on "
                                                f"{BST_URL}")
        if uid in SWEEP_SPECS:
            unit["design_input_specs"] = dict(SWEEP_SPECS[uid])
        units.append(unit)
    by_id = {u["id"]: u for u in units}
    by_id["R1"]["reactions"] = [dict(SWEEP_REACTION)]
    by_id["P1"]["utility_consumption_results"] = {"grid": 1.0}
    by_id["H1"]["utility_consumption_results"] = {"steam": 50.0}
    formulas = {"Water": "H2O", "Glucose": "C6H12O6", "Ethanol": "C2H6O",
                "CO2": "CO2"}
    chemicals = [{"id": n, "formula": formulas[n], "molar_mass": mw}
                 for n, mw in SWEEP_MW.items()]
    utilities = {
        "heat_utilities": [{"id": "steam", "temperature": 412.0,
                            "pressure": 344738.0, "composition": [
                                {"phase": "g", "component_name": "Water",
                                 "mol_fraction": 1.0}]}],
        "power_utilities": [{"id": "grid", "electrical_energy_price": 0.07}]}
    return {"metadata": {"sff_version": "0.2.1", "TEA_currency": "USD"},
            "units": units, "streams": streams, "chemicals": chemicals,
            "utilities": utilities}


class TestRevision4PartC(unittest.TestCase):
    """Revision 4 part C: unweighted per-unit averages (R4.12), unknown-
    family units carrying every family-dependent need (R4.13), label-only
    mixer plausibility (R4.14), and the deletion sweeps over sparse
    documents that pin all three.

    The sweeps are STRICT: a scored criterion turning not-applicable is a
    violation even from a score of exactly 1. Dropping a criterion at the
    maximum cannot raise the overall mean, but it is exactly how a shed
    need shows while the need happens to be met (deleting a fermenter's
    unit_type used to take CMP-RXN-01 from 1 to not-applicable), so
    allowing it would let applicability follow data unseen."""

    def assert_sweep_clean(self, doc):
        with name_lookup(set()):
            raised, lowered, n = deletion_violations(doc, skip=frozenset())
        paths = dict(deletable_paths(doc, skip=frozenset()))
        for path in (("units", 0, "id"),
                     ("units", 3, "reactions", 0, "equation"),
                     ("units", 5, "design_input_specs", "T"),
                     ("streams", 2, "source_unit_id"),
                     ("streams", 6, "sink_unit_id"),
                     ("streams", 4, "stream_properties", "phases", "l",
                      "composition", 1, "component_name")):
            self.assertEqual(paths.get(path), "leaf", path)
        self.assertGreater(n, 240)
        self.assertEqual(raised, [])
        self.assertGreater(lowered, n // 2)

    def test_sparse_descriptive_doc_deletion_sweep(self):
        """sweep_doc('unit_type'): units identified ONLY by a descriptive
        unit_type (every family at 0.5: mixer, two pumps, heat exchanger,
        reactor with a well-formed reaction and a vent, flash, splitter);
        deleting EVERY field one at a time -- unit_type, spec keys,
        reaction fields, utility results, unit and stream ids, stream
        endpoints, component names, stream states -- raises neither the
        overall score nor any criterion, and turns no scored criterion
        not-applicable (strict). Before R4.12/R4.13 (and the unsourced-
        stream rule of CMP-UOP-04) this found, e.g., P1's unit_type raising
        CMP-UTIL-04 (weight 1 -> 2), R1's raising CMP-TOP-02 and turning
        CMP-RXN-01 not-applicable, and s1's source_unit_id raising
        CMP-UOP-04."""
        doc = sweep_doc("unit_type")
        families = {u["id"]: c for u, c in zip(doc["units"],
                                               ctx(doc).unit_classes)}
        self.assertEqual(families, {
            "M1": (None, "mixer", 0.5), "P1": (None, "pump", 0.5),
            "H1": (None, "hx_utility", 0.5), "R1": (None, "reactor", 0.5),
            "P2": (None, "pump", 0.5), "F1": (None, "flash", 0.5),
            "S1": (None, "splitter", 0.5)})
        self.assertNotIn("design_simulation_method",
                         {k for u in doc["units"] for k in u})
        self.assert_sweep_clean(doc)

    def test_sparse_dsm_doc_deletion_sweep(self):
        """sweep_doc('design_simulation_method'): the same flowsheet with
        units identified ONLY by 'Class on <biosteam url>' (every class
        exact, confidence 1: the mixer and splitter cost-free, the
        AnaerobicBioreactor with its two outlets); the strict sweep over
        every field -- each design_simulation_method included -- finds no
        violation."""
        doc = sweep_doc("design_simulation_method")
        sc = ctx(doc)
        self.assertEqual(sc.unit_classes,
                         [(SWEEP_CLASSES[u["id"]],
                           C._class_family(SWEEP_CLASSES[u["id"]]), 1.0)
                          for u in doc["units"]])
        self.assertNotIn("unit_type", {k for u in doc["units"] for k in u})
        self.assertEqual(sc.costed, [1, 2, 3, 4, 5])
        self.assert_sweep_clean(doc)

    def test_unknown_family_carries_every_need(self):
        """R4.13 on one-unit sparse docs (feed -> U1 -> product): deleting
        U1's only family evidence (unit_type 'Fermentation reactor' with a
        reaction, 'Heat exchanger', 'Pump') leaves every scored criterion
        scored and no higher; the unknown-family unit is reactive ('family
        unknown'; CMP-RXN-01..04 stay scored), needs heat and power agents
        (CMP-UTIL-01/02/03/05 scored, both missing kinds listed), always
        draws a utility (CMP-UTIL-04) and is species-guarded / physical
        unless it declares a well-formed reaction; an identified custom
        class of unknown family waives the guard (its code may transform
        species), as for a custom splitter."""
        edges = (("feed", "None", "U1"), ("prod", "U1", "None"))
        for unit_type in ("Fermentation reactor", "Heat exchanger", "Pump"):
            doc = sparse_doc({"U1": unit_type}, edges)
            if unit_type.startswith("Fermentation"):
                doc["units"][0]["reactions"] = [dict(SWEEP_REACTION)]
            base = criterion_results(doc)
            reduced = deleted(doc, ("units", 0, "unit_type"))
            after = criterion_results(reduced)
            sc = ctx(reduced)
            with self.subTest(unit_type=unit_type):
                self.assertEqual(sc.unit_classes, [(None, None, 0.0)])
                for cid, before in base.items():
                    if before.status == "scored":
                        self.assertEqual(after[cid].status, "scored", cid)
                        self.assertLessEqual(after[cid].score,
                                             before.score + 1e-12, cid)
                for cid in ("CMP-RXN-01", "CMP-RXN-02", "CMP-RXN-03",
                            "CMP-RXN-04", "CMP-UTIL-01", "CMP-UTIL-02",
                            "CMP-UTIL-03", "CMP-UTIL-05"):
                    self.assertEqual(after[cid].status, "scored", cid)
                self.assertEqual(C._reactive_reason(sc, 0), "family unknown")
                self.assertEqual(len(C._missing_agent_kinds(sc)), 2)
                self.assertTrue(C._draws_utility(sc, 0))
                reacts = "reactions" in reduced["units"][0]
                self.assertEqual(C._species_guarded(sc, 0), not reacts)
                self.assertEqual(C._physical_unit(sc, 0), not reacts)
                self.assertLessEqual(overall(reduced), overall(doc))
        custom = sparse_doc({"U1": ""}, edges)
        custom["units"][0]["design_simulation_method"] = (
            f"Frobnicator on {CUSTOM_URL}")
        sc = ctx(custom)
        self.assertEqual(sc.unit_classes[0][:2], ("Frobnicator", None))
        self.assertFalse(C._species_guarded(sc, 0))

    def test_unit_families_and_needs_family(self):
        """_unit_families: the set of declared units' families, None for
        each unit of unknown family (valid_doc's one-inlet 'Mixer' label,
        R4.14); _needs_family: True when a family of the set is in the
        needing set, or when None is in it (R4.13), else False."""
        self.assertEqual(C._unit_families(ctx(label_mixer_doc())), {"mixer"})
        self.assertEqual(C._unit_families(ctx(valid_doc())), {None})
        self.assertEqual(C._unit_families(ctx({})), set())
        heat = C.HEAT_UTILITY_FAMILIES
        self.assertTrue(C._needs_family({"flash", "mixer"}, heat))
        self.assertTrue(C._needs_family({None, "mixer"}, heat))
        self.assertFalse(C._needs_family({"mixer", "pump"}, heat))
        self.assertFalse(C._needs_family(set(), heat))

    def test_label_only_mixer_needs_two_inlets(self):
        """R4.14: _port_contradiction -- a mixer family claim without an
        exact class (class_name None) with < 2 non-empty-or-unknown-flow
        inlets is contradicted ('1 non-empty inlet(s), a mixer claim
        without an exact class needs at least 2'), with 2 it is not; the
        exactly named core Mixer uses its static port table only (1 inlet
        fine); unknown ports (None) contradict nothing. Through
        _unit_class: unit_type 'Mixer' with ports (1, 1) -> (None, None,
        0.0), (2, 1) -> (None, 'mixer', 0.5), None -> (None, 'mixer', 0.5);
        'Mixer on <biosteam url>' with (1, 1) -> ('Mixer', 'mixer', 1.0).
        On a document a second inlet of unknown flow counts, a
        stated-empty one does not (_ScoringContext.nonempty_ports), and
        _class_note names the contradicted label."""
        self.assertEqual(C._port_contradiction(None, "mixer", False, (1, 1)),
                         "1 non-empty inlet(s), a mixer claim without an "
                         "exact class needs at least 2")
        self.assertIsNone(C._port_contradiction(None, "mixer", False, (2, 1)))
        self.assertIsNone(C._port_contradiction("Mixer", "mixer", True, (1, 1)))
        self.assertIsNone(C._port_contradiction(None, "mixer", False, None))
        self.assertIsNone(C._port_contradiction(None, "pump", False, (1, 1)))
        label = {"id": "U1", "unit_type": "Mixer"}
        self.assertEqual(C._unit_class(label, ports=(1, 1)), (None, None, 0.0))
        self.assertEqual(C._unit_class(label, ports=(2, 1)),
                         (None, "mixer", 0.5))
        self.assertEqual(C._unit_class(label), (None, "mixer", 0.5))
        self.assertEqual(C._unit_class(dict(label, **cls("Mixer")),
                                       ports=(1, 1)), ("Mixer", "mixer", 1.0))
        doc = label_mixer_doc()
        self.assertEqual(ctx(doc).unit_classes[0], (None, "mixer", 0.5))
        sp = doc["streams"][2]["stream_properties"]
        for key in ("total_mass_flow", "total_molar_flow", "phases",
                    "composition"):
            del sp[key]
        self.assertEqual(ctx(doc).unit_classes[0], (None, "mixer", 0.5))
        sp.update(total_mass_flow=0.0, total_molar_flow=0.0)
        self.assertEqual(ctx(doc).unit_classes[0], (None, None, 0.0))
        note = C._class_note(ctx(doc), 0)
        self.assertIn("unit_type family 'mixer' (1 non-empty inlet(s)", note)

    def test_uop_04_keeps_unsourced_streams(self):
        """CMP-UOP-04: a stream without a stated source_unit_id (missing,
        not a string, empty) stays in the population and scores 0 (its
        state is no unit outlet state), so deleting an internal stream's
        source cannot drop a partly stated stream (sweep_doc's s1 raised
        0.70 -> 0.72) and deleting a feed's or a product's source cannot
        add a fully stated one or drop one."""
        doc = sweep_doc()
        base = run(C._score_uop_04, doc)[0]
        for position in (0, 2, 10):  # feedA, s1, out1
            sid = doc["streams"][position]["id"]
            reduced = deleted(doc, ("streams", position, "source_unit_id"))
            score, message, missing = run(C._score_uop_04, reduced)
            with self.subTest(stream=sid):
                self.assertLess(score, base)
                self.assertIn("1 stream(s) without a stated source score 0",
                              message)
                self.assertIn(f"streams[{sid}].source_unit_id (not stated",
                              "\n".join(missing))


# ------------------------------------------------------------- Revision 5

R5_MW = {"Water": 18.01528, "Glucose": 180.15588, "Ethanol": 46.06844,
         "CO2": 44.0095}
# unit id -> (unit_type, design_simulation_method class)
R5_HONEST = {"P1": ("Pump", "Pump"), "H1": ("Heater", "HXutility"),
             "R1": ("Fermentation reactor", "AnaerobicBioreactor"),
             "F1": ("Flash drum", "Flash")}
R5_LIE = {"P1": ("Mixer", "Mixer"), "H1": ("Mixer", "Mixer"),
          "R1": ("Mixer", "Mixer"), "F1": ("Splitter", "Splitter")}


def _r5_stream(sid, source, sink, T, P, phases):
    """A stream in degC / bar / t/h (quantity_units_global of r5_doc) whose
    {phase: {component: kmol/h}} `phases` are stated per phase with BOTH
    bases: total_molar_flow + mol_fraction and total_mass_flow (t/h) +
    mass_fraction; stream totals of both."""
    out, mass, moles = {}, 0.0, 0.0
    for symbol, flows in phases.items():
        n = sum(flows.values())
        m = sum(f * R5_MW[c] for c, f in flows.items())
        mass, moles = mass + m, moles + n
        out[symbol] = {"total_molar_flow": n, "total_mass_flow": m / 1000,
                       "composition": [
                           {"component_name": c, "mol_fraction": f / n,
                            "mass_fraction": f * R5_MW[c] / m}
                           for c, f in flows.items()]}
    return {"id": sid, "source_unit_id": source, "sink_unit_id": sink,
            "stream_properties": {"temperature": T, "pressure": P,
                                  "total_mass_flow": mass / 1000,
                                  "total_molar_flow": moles, "phases": out}}


def r5_doc(labels=None):
    """The Revision 5 reviewer flowsheet: feed -> P1 (pump, 1 -> 3 bar) ->
    H1 (heater, 25.1 -> 80 degC) -> R1 (fermenter: Glucose -> Ethanol +
    CO2, no reaction declared) -> F1 (flash to 1 bar: vapor + liquid), in
    degC / bar / t/h (quantity_units_global), every stream stating both
    bases (_r5_stream); each unit named by unit_type and 'Class on
    <biosteam url>' from `labels` (default R5_HONEST); chemicals carry
    molar masses; no utilities, costs or specs."""
    labels = labels or R5_HONEST
    water, glucose = 1600.0, 40.0
    streams = [
        _r5_stream("feed", "None", "P1", 25.0, 1.0,
                   {"l": {"Water": water, "Glucose": glucose}}),
        _r5_stream("s1", "P1", "H1", 25.1, 3.0,
                   {"l": {"Water": water, "Glucose": glucose}}),
        _r5_stream("s2", "H1", "R1", 80.0, 3.0,
                   {"l": {"Water": water, "Glucose": glucose}}),
        _r5_stream("s3", "R1", "F1", 80.0, 3.0,
                   {"l": {"Water": water, "Glucose": 2.0, "Ethanol": 76.0,
                          "CO2": 76.0}}),
        _r5_stream("vap", "F1", "None", 80.0, 1.0,
                   {"g": {"CO2": 76.0, "Ethanol": 6.0, "Water": 20.0}}),
        _r5_stream("liq", "F1", "None", 80.0, 1.0,
                   {"l": {"Water": water - 20.0, "Glucose": 2.0,
                          "Ethanol": 70.0}})]
    units = [{"id": uid, "unit_type": unit_type,
              "design_simulation_method": f"{name} on {BST_URL}"}
             for uid, (unit_type, name) in labels.items()]
    qug = {"temperature": {"aliases": ["temperature", "T"],
                           "quantity_units": "degC"},
           "pressure": {"aliases": ["pressure", "P"], "quantity_units": "bar"},
           "mass_flow": {"aliases": ["mass_flow", "total_mass_flow"],
                         "quantity_units": "t/h"},
           "molar_flow": {"aliases": ["molar_flow", "total_molar_flow"],
                          "quantity_units": "kmol/hr"}}
    return {"metadata": {"sff_version": "0.2.1", "TEA_currency": "USD",
                         "TEA_year": 2020},
            "quantity_units_global": qug, "units": units, "streams": streams,
            "chemicals": [{"id": c, "molar_mass": mw}
                          for c, mw in R5_MW.items()],
            "utilities": {}}


def mass_basis(doc):
    """Copy of `doc` whose streams keep only mass-basis phase data (R5.3):
    every phase's total_molar_flow and every composition entry's
    mol_fraction are dropped; a phase without total_mass_flow gets it from
    its molar flow and SWEEP_MW / R5_MW (mass_fraction likewise); stream
    totals (mass and molar) and the chemicals' molar masses are kept."""
    doc = copy.deepcopy(doc)
    mw = dict(SWEEP_MW, **R5_MW)
    for stream in doc["streams"]:
        for phase in stream["stream_properties"].get("phases", {}).values():
            n = phase.pop("total_molar_flow", None)
            entries = phase.get("composition", [])
            if "total_mass_flow" not in phase:
                masses = [n * e["mol_fraction"] * mw[e["component_name"]]
                          for e in entries]
                phase["total_mass_flow"] = sum(masses)
                for e, m in zip(entries, masses):
                    e["mass_fraction"] = m / phase["total_mass_flow"]
            for e in entries:
                e.pop("mol_fraction", None)
    return doc


class TestRevision5(unittest.TestCase):
    """Revision 5: physics plausibility of need-removing mixer / pure-
    splitter claims (R5.1), mass-basis stream data (R5.3), and missing
    endpoint keys as dangling streams (R5.8)."""

    # ---- R5.3 mass-basis stream data -------------------------------------

    def test_phase_flow_and_mass_basis_phases(self):
        """_phase_flow: a phase's own valid flow of the key, else -- for the
        only phase of a stream -- the stream-level total, else None.
        _phases_ok (R5.3): a phase stating only total_mass_flow with mass
        fractions is stated; a single phase with no flow of its own is
        stated from the stream totals `sp`, but not without them, nor when
        the stream has two phases."""
        sp = {"total_mass_flow": 5.0}
        self.assertEqual(C._phase_flow({"total_mass_flow": 2.0}, sp, True,
                                       "total_mass_flow"), 2.0)
        self.assertEqual(C._phase_flow({}, sp, True, "total_mass_flow"), 5.0)
        self.assertIsNone(C._phase_flow({}, sp, False, "total_mass_flow"))
        self.assertIsNone(C._phase_flow({}, sp, True, "total_molar_flow"))
        entry = {"component_name": "A", "mass_fraction": 1.0}
        mass_phase = {"total_mass_flow": 2.0, "composition": [entry]}
        self.assertTrue(C._phases_ok({"l": mass_phase}))
        bare = {"composition": [entry]}
        self.assertTrue(C._phases_ok({"l": bare}, sp))
        self.assertFalse(C._phases_ok({"l": bare}))
        self.assertFalse(C._phases_ok({"l": bare, "g": mass_phase}, sp))

    def test_species_flows_on_both_bases(self):
        """_species_flows(stream, 'mass') -> {component: kg/h} from phase
        total_mass_flow x mass_fraction (the molar default needs mol data
        and returns None on a mass-only stream); _basis_flows: molar when
        every stream has mol data, else mass when every one has mass data
        (never a mix), else None; _side_species over a unit's ports
        reports the basis used and the per-side sums."""
        doc = r5_doc()
        feed = C._internal_stream(doc["streams"][0],
                                  C._quantity_unit_index(doc))
        molar = C._species_flows(feed)
        self.assertAlmostEqual(molar["Glucose"], 40.0)
        mass = mass_basis(doc)
        mfeed = C._internal_stream(mass["streams"][0],
                                   C._quantity_unit_index(mass))
        self.assertIsNone(C._species_flows(mfeed))
        by_mass = C._species_flows(mfeed, "mass")
        self.assertAlmostEqual(by_mass["Glucose"], 40.0 * R5_MW["Glucose"])
        self.assertEqual(C._basis_flows([feed, feed])[0], "molar")
        self.assertEqual(C._basis_flows([feed, mfeed])[0], "mass")
        self.assertIsNone(C._basis_flows([feed, {"stream_properties": {}}]))
        sc = ctx(mass)
        basis, flows_in, flows_out = C._side_species(sc, "R1")
        self.assertEqual(basis, "mass")
        self.assertNotIn("CO2", flows_in)
        self.assertGreater(flows_out["CO2"], 0.0)
        self.assertEqual(C._side_species(ctx(doc), "P1")[0], "molar")
        del doc["streams"][0]["stream_properties"]["phases"]
        self.assertIsNone(C._side_species(ctx(doc), "P1"))

    def test_mole_fractions_from_mass_basis(self):
        """_mole_fractions (CMP-FEED-02 port materials): a mass-only feed
        -> None without molar masses; with _ScoringContext.molar_masses
        (declared, else from the formula) the mass flows convert to mol
        fractions equal to the molar document's; a component without a
        molar mass leaves them None."""
        doc = mass_basis(r5_doc())
        sc = ctx(doc)
        self.assertEqual(sc.molar_masses, R5_MW)
        feed = sc.streams[0]
        self.assertIsNone(C._mole_fractions(feed))
        fractions = C._mole_fractions(feed, sc.molar_masses)
        expected = C._mole_fractions(ctx(r5_doc()).streams[0])
        for name, x in expected.items():
            self.assertAlmostEqual(fractions[name], x)
        self.assertIsNone(C._mole_fractions(feed, {"Water": 18.0}))
        formula_only = ctx({"chemicals": [{"id": "W", "formula": "H2O"}]})
        self.assertAlmostEqual(formula_only.molar_masses["W"], 18.015, 2)

    def test_mass_basis_twin_scores_like_the_molar_original(self):
        """R5.3 reviewer case: r5_doc with every phase's molar flow and mol
        fractions removed (stream totals and molar masses kept) scored
        0.3599 against the molar original's 0.4565 (CMP-FEED-01 1 -> 0.75,
        CMP-UOP-02 0.61 -> 0.05, CMP-UOP-04 1 -> 0.75 citing the
        species-consistency guard, CMP-TOP-02 0.91 -> 0.19). Now every
        criterion scores exactly as on the original; the pump P1 keeps its
        single-port CMP-TOP-02 credit (1 on both sides: its TOTAL mass
        closes, _mass_closure) and its full closure holds on the mass
        basis; the fermenter's CO2 appearance is still found (mass
        basis), so its message says inconsistent, not uncomputable."""
        doc = r5_doc()
        twin = mass_basis(doc)
        before, after = criterion_results(doc), criterion_results(twin)
        for cid, result in before.items():
            self.assertEqual(after[cid].status, result.status, cid)
            if result.status == "scored":
                self.assertAlmostEqual(after[cid].score, result.score,
                                       msg=cid)
        self.assertAlmostEqual(overall(twin), overall(doc))
        sc = ctx(twin)
        pump = sc.unit_positions["P1"]
        self.assertEqual(C._side_credit(sc, pump, "ins"), (1.0, None))
        self.assertEqual(C._side_credit(sc, pump, "outs"), (1.0, None))
        self.assertEqual(C._mass_closure(sc, pump), (True, ""))
        self.assertEqual(C._closure(sc, pump), (True, ""))
        self.assertEqual(C._species_change(sc, "R1"),
                         ["CO2 appears", "Ethanol appears"])

    def test_uop_04_tells_unverified_from_inconsistent(self):
        """CMP-UOP-04 messages (R5.3): an exact Flash whose outlet carries a
        species its inlet lacks -> 'fail the species-consistency guard
        (species appear or vanish)' and 'inconsistent at' entries; the
        same Flash whose inlet phases are deleted (species flows not
        computable on any basis) -> 'left unverified by the
        species-consistency guard (species flows not computable)' and
        'unverified at' entries, never 'inconsistent'."""
        doc = r5_doc()
        doc["units"] = [u for u in doc["units"] if u["id"] == "F1"]
        doc["streams"] = doc["streams"][3:]
        doc["streams"][0]["source_unit_id"] = "None"
        doc["streams"][1]["stream_properties"]["phases"]["g"][
            "composition"][0]["component_name"] = "Glucose"
        _score, message, missing = run(C._score_uop_04, doc)
        self.assertIn("fail the species-consistency guard (species appear "
                      "or vanish)", message)
        self.assertIn("inconsistent at units[F1]", "\n".join(missing))
        del doc["streams"][0]["stream_properties"]["phases"]
        _score, message, missing = run(C._score_uop_04, doc)
        self.assertIn("left unverified by the species-consistency guard "
                      "(species flows not computable)", message)
        self.assertIn("unverified at units[F1]: species flows not "
                      "computable", "\n".join(missing))
        self.assertNotIn("inconsistent", "\n".join(missing))

    def test_mass_basis_sweeps(self):
        """The strict deletion sweeps (TestRevision4PartC.assert_sweep_clean)
        over the mass-basis twins of both sweep_doc variants (every phase
        stating only total_mass_flow + mass fractions, R5.3) find no
        violation: switching a unit's ports to the mass basis, or losing
        it, never raises a criterion."""
        for identity in ("unit_type", "design_simulation_method"):
            doc = mass_basis(sweep_doc(identity))
            self.assertNotIn("total_molar_flow", doc["streams"][2][
                "stream_properties"]["phases"]["l"])
            with self.subTest(identity=identity):
                TestRevision4PartC.assert_sweep_clean(self, doc)

    # ---- R5.1 physics plausibility ---------------------------------------

    def test_recorded_duty(self):
        """_recorded_duty: the first non-zero result per kind -- a
        power_utilities agent -> 'power', a heat_utilities agent or an
        undeclared one -> 'heat' -- while other_utilities agents (material
        flows) and zero values are ignored."""
        doc = r5_doc()
        doc["utilities"] = {"heat_utilities": [{"id": "steam"}],
                            "power_utilities": [{"id": "grid"}],
                            "other_utilities": [{"id": "gas"}]}
        unit = doc["units"][0]
        unit["utility_consumption_results"] = {"gas": 5.0, "grid": 0.0}
        self.assertEqual(C._recorded_duty(ctx(doc), 0), {})
        unit["utility_consumption_results"]["grid"] = 2.0
        unit["utility_production_results"] = {"steam": -3.0, "x": 1.0}
        self.assertEqual(C._recorded_duty(ctx(doc), 0), {
            "power": "utility_consumption_results.grid",
            "heat": "utility_production_results.steam"})

    def test_physics_contradictions(self):
        """_physics_contradictions on r5_doc (degC / bar converted): P1 ->
        'mixer' (outlet P 3e5 above the highest inlet 1e5) and 'splitter'
        (differs); H1 -> T above / differs; R1 (no reaction) -> 'species'
        (CO2 and Ethanol appear; Glucose only drops); F1 -> 'splitter' P
        differs, but no 'mixer' entry (an outlet may be colder / at a
        lower P). A well-formed reaction on R1 removes 'species'; a unit
        without an id -> {}; deleting s1's temperature (P1's outlet, H1's
        inlet) removes both units' temperature evidence (a deletion never
        creates one)."""
        sc = ctx(r5_doc())
        physics = dict(zip((u["id"] for u in sc.units), sc.unit_physics))
        self.assertEqual(set(physics["P1"]), {"mixer", "splitter"})
        self.assertIn("outlet pressure 300000 above the highest inlet "
                      "pressure 100000", physics["P1"]["mixer"])
        self.assertIn("outlet temperature 353.15 differs from the inlet "
                      "temperature 298.25", physics["H1"]["splitter"])
        self.assertEqual(physics["R1"]["species"],
                         "CO2 appears, Ethanol appears without declared "
                         "reactions")
        self.assertEqual(set(physics["F1"]), {"splitter"})
        doc = r5_doc()
        doc["units"][2]["reactions"] = [{
            "index": 0, "equation": "Glucose -> 2 Ethanol + 2 CO2",
            "reactant": "Glucose", "conversion": 0.95}]
        self.assertNotIn("species", ctx(doc).unit_physics[2])
        self.assertEqual(C._physics_contradictions(ctx({"units": [{}]}), 0),
                         {})
        doc = r5_doc()
        del doc["streams"][1]["stream_properties"]["temperature"]
        for position in (0, 1):
            self.assertNotIn("temperature", str(ctx(doc).unit_physics[position]))

    def test_physics_contradiction_by_claim(self):
        """_physics_contradiction: a mixer-family claim is contradicted by
        'heat', 'power', 'species' or 'mixer' evidence -- except 'power'
        for the @cost-decorated Mixer subclasses (_COSTED_CLASSES, agitator
        kW); a PURE_SPLITTER_CLASSES class, or a label-only splitter
        family claim, by 'heat', 'power', 'species' or 'splitter'; a
        splitter-family class that is not pure (SolidsCentrifuge), and any
        other family, never; a MOCK_SPLITTER_CLASSES class not by
        'splitter' (T / P) evidence; no evidence -> None. _ports_plausible
        joins it with the port contradiction."""
        heat = {"heat": "records a non-zero heat duty (x)"}
        power = {"power": "records a non-zero power duty (grid)"}
        self.assertEqual(C._physics_contradiction("Mixer", "mixer", heat),
                         "implausible for a mixer: records a non-zero heat "
                         "duty (x)")
        self.assertIsNone(C._physics_contradiction(
            "EnzymeHydrolysateMixer", "mixer", power))
        self.assertIsNotNone(C._physics_contradiction(
            "EnzymeHydrolysateMixer", "mixer", heat))
        self.assertIsNotNone(C._physics_contradiction(None, "mixer", power))
        split = {"splitter": "outlet pressure 1 differs from the inlet"}
        self.assertIsNone(C._physics_contradiction("Mixer", "mixer", split))
        for name in sorted(C.PURE_SPLITTER_CLASSES
                           - C.MOCK_SPLITTER_CLASSES) + [None]:
            family = C._class_family(name) if name else "splitter"
            self.assertIn("implausible for a pure splitter",
                          C._physics_contradiction(name, family, split))
        # A MockSplitter does nothing when simulated: exempt from the T / P
        # bounds only (tested in TestRevision5PartB).
        for name in sorted(C.MOCK_SPLITTER_CLASSES):
            self.assertIsNone(C._physics_contradiction(name, "junction", split))
        self.assertIsNone(C._physics_contradiction(
            "SolidsCentrifuge", "splitter", split))
        self.assertIsNone(C._physics_contradiction("Pump", "pump", heat))
        self.assertIsNone(C._physics_contradiction("Mixer", "mixer", None))
        self.assertFalse(C._ports_plausible("Mixer", "mixer", True, (1, 1),
                                            heat))
        self.assertTrue(C._ports_plausible("Mixer", "mixer", True, (1, 1)))

    def test_relabelled_units_never_pay_off(self):
        """R5.1 reviewer cases on r5_doc: relabelling the fermenter R1 an
        exact core Mixer raised the score 0.4565 -> 0.5335, and relabelling
        every unit Mixer / Splitter (R5_LIE) by +0.17 (representable 0.503
        -> 0.696). Each relabelled claim is now physically implausible
        (P1: pressure rise; H1: temperature rise; R1: CO2 and Ethanol
        appear; F1: outlet pressure differs from the inlet's), so the unit
        is of unknown family (None, None, 0.0) and carries every need: no
        relabelling raises the overall or representable score, and each
        _class_note names the contradiction (R5.1)."""
        honest = C.score_flowsheet_completeness(r5_doc())
        cases = {uid: dict(R5_HONEST, **{uid: ("Mixer", "Mixer")})
                 for uid in ("P1", "H1", "R1")}
        cases["all"] = R5_LIE
        for case, labels in cases.items():
            doc = r5_doc(labels)
            report = C.score_flowsheet_completeness(doc)
            sc = ctx(doc)
            with self.subTest(case=case):
                self.assertLess(report.score, honest.score)
                self.assertLess(report.representable_score,
                                honest.representable_score)
                for position, unit in enumerate(sc.units):
                    if labels[unit["id"]] == R5_HONEST[unit["id"]]:
                        continue
                    self.assertEqual(sc.unit_classes[position],
                                     (None, None, 0.0), unit["id"])
                    self.assertIn("is physically implausible",
                                  C._class_note(sc, position))
                    self.assertTrue(C._physics_notes(sc, position))
        note = C._class_note(ctx(r5_doc(cases["R1"])), 2)
        self.assertIn("class 'Mixer' (implausible for a mixer: CO2 appears, "
                      "Ethanol appears without declared reactions; Glucose "
                      "consumed beyond 1% without declared reactions)", note)
        self.assertIn("unit_type family 'mixer'", note)

    def test_plausible_claims_stand(self):
        """No false positive on consistent units: sweep_doc's exact Mixer
        (two feeds at the same T / P) and Splitter (outlets at the inlet's
        state) keep their classes with empty _physics_notes; a mixer
        outlet colder than its hottest inlet is plausible (upper bounds
        only), and an EnzymeHydrolysateMixer recording agitator power keeps
        its mixer family."""
        doc = sweep_doc("design_simulation_method")
        sc = ctx(doc)
        for uid, name in (("M1", "Mixer"), ("S1", "Splitter")):
            position = sc.unit_positions[uid]
            self.assertEqual(sc.unit_classes[position][0], name)
            self.assertEqual(C._physics_notes(sc, position), [])
        doc["streams"][0]["stream_properties"]["temperature"] = 350.0
        doc["streams"][2]["stream_properties"]["temperature"] = 320.0
        self.assertEqual(ctx(doc).unit_classes[0][0], "Mixer")
        doc["units"][0]["design_simulation_method"] = (
            f"EnzymeHydrolysateMixer on {BIP_URL}")
        doc["units"][0]["utility_consumption_results"] = {"grid": 70.0}
        self.assertEqual(ctx(doc).unit_classes[0][:2],
                         ("EnzymeHydrolysateMixer", "mixer"))
        doc["units"][0]["design_simulation_method"] = f"Mixer on {BST_URL}"
        self.assertEqual(ctx(doc).unit_classes[0], (None, None, 0.0))

    # ---- R5.8 missing endpoint keys --------------------------------------

    def test_dangling_streams(self):
        """R5.8 on sweep_doc: _stream_mass -> a stated-empty stream 0.0, a
        valid total_mass_flow, else None. A dangling stream (sink not
        stated) of 1e-3 kg/h may be a missing inlet of any unit whose
        balance closes with it: _dangling_port names it for P2 and
        _mass_closure / _closure fail there; one of 1000 kg/h is ruled out
        ('' , P2 closes); a stated-empty or explicitly bounded one never
        counts; one of unknown mass is never ruled out. _dangling_note
        names the key(s) not stated. CMP-TOP-01 counts the missing key as
        unresolved."""
        empty = {"stream_properties": {"total_molar_flow": 0.0}}
        self.assertEqual(C._stream_mass(empty), 0.0)
        self.assertEqual(C._stream_mass(
            {"stream_properties": {"total_mass_flow": 2.0}}), 2.0)
        self.assertIsNone(C._stream_mass({"stream_properties": {}}))
        base = sweep_doc("design_simulation_method")
        p2 = [u["id"] for u in base["units"]].index("P2")
        self.assertEqual(C._mass_closure(ctx(base), p2), (True, ""))
        for mass, rules_out in ((1e-3, False), (1000.0, True), (0.0, True),
                                (None, False)):
            doc = copy.deepcopy(base)
            sp = {} if mass is None else {"total_mass_flow": mass}
            doc["streams"].append({"id": "tiny", "source_unit_id": "P1",
                                   "stream_properties": sp})
            sc = ctx(doc)
            self.assertEqual(sc.dangling, [len(doc["streams"]) - 1])
            why = C._dangling_port(sc, p2)
            with self.subTest(mass=mass):
                if rules_out:
                    self.assertEqual(why, "")
                    self.assertTrue(C._mass_closure(sc, p2)[0])
                else:
                    self.assertEqual(why, "streams[tiny] (sink_unit_id not "
                                     "stated) may be a missing inlet: the "
                                     "balance cannot rule it out")
                    self.assertEqual(C._mass_closure(sc, p2), (False, why))
                    self.assertFalse(C._closure(sc, p2)[0])
        doc = copy.deepcopy(base)
        doc["streams"].append({"id": "tiny", "sink_unit_id": "",
                               "stream_properties": {}})
        sc = ctx(doc)
        self.assertEqual(C._dangling_note(sc, len(doc["streams"]) - 1),
                         "streams[tiny].source_unit_id|sink_unit_id: not "
                         "stated (dangling: never a boundary stream, "
                         "scores 0)")
        _score, _message, missing = run(C._score_top_01, doc)
        self.assertIn("streams[tiny].source_unit_id: not stated (dangling",
                      "\n".join(missing))

    def test_endpoint_deletions_never_raise(self):
        """R5.8 reviewer case in miniature (SF_BST_01's s35 sink raised
        CMP-TOP-02 0.8732 -> 0.8785): on sweep_doc('unit_type') with a
        small (0.01 kg/h) priced recycle S1 -> R1 beside s3, deleting that
        stream's sink_unit_id (formerly: a priced 'outlet' joining
        CMP-FEED-02, and R1's inlet count dropping from 2 to 1 so its
        count-based CMP-TOP-02 credit rose) or source_unit_id lowers or
        keeps every scored criterion: the stream joins CMP-FEED-01/02 at 0
        and no unit whose balance could hide it keeps a count-based port
        credit."""
        doc = sweep_doc("unit_type")
        doc["streams"].append({
            "id": "trickle", "source_unit_id": "S1", "sink_unit_id": "R1",
            "price": 0.3, "stream_properties": _sweep_sp({"Water": 0.01 /
                                                          18.015}, "l")})
        base = criterion_results(doc)
        position = len(doc["streams"]) - 1
        for key in ("sink_unit_id", "source_unit_id"):
            after = criterion_results(deleted(doc, ("streams", position, key)))
            with self.subTest(key=key):
                for cid, before in base.items():
                    if before.status == "scored":
                        self.assertEqual(after[cid].status, "scored", cid)
                        self.assertLessEqual(after[cid].score,
                                             before.score + 1e-12, cid)
                self.assertLess(after["CMP-FEED-02"].score,
                                base["CMP-FEED-02"].score)


# ------------------------------------------------ Revision 5, part B

def r5_kpa_twin(doc):
    """Copy of an r5_doc restated in K and Pa: the temperature and pressure
    entries of quantity_units_global switch to K / Pa and every stream
    temperature (+273.15), pressure (x 1e5) and design_input_specs T / P
    follows, so the twin states the same physical flowsheet."""
    doc = copy.deepcopy(doc)
    doc["quantity_units_global"]["temperature"]["quantity_units"] = "K"
    doc["quantity_units_global"]["pressure"]["quantity_units"] = "Pa"
    holders = [s["stream_properties"] for s in doc["streams"]]
    holders += [u.get("design_input_specs", {}) for u in doc["units"]]
    for holder in holders:
        for key, scale, offset in (("temperature", 1.0, 273.15),
                                   ("pressure", 1e5, 0.0), ("T", 1.0, 273.15),
                                   ("P", 1e5, 0.0)):
            if key in holder:
                holder[key] = holder[key] * scale + offset
    return doc


def r5_sweep_doc(identity="unit_type"):
    """sweep_doc(identity) restated with R5.2 / R5.4 data: pressures in bar
    (quantity_units_global 'pressure' with aliases pressure and P, so every
    stream, agent and P1 / P2 / F1 spec pressure is in bar), temperatures in
    K with T aliased (H1's and F1's T), R1's tau aliased under a 'time'
    entry in minutes (600 min = 10 h), the chemicals indexed 0..3 and S1's
    split a 4-element vector (one entry per indexed chemical)."""
    doc = sweep_doc(identity)
    doc["quantity_units_global"] = {
        "temperature": {"aliases": ["temperature", "T"], "quantity_units": "K"},
        "pressure": {"aliases": ["pressure", "P"], "quantity_units": "bar"},
        "time": {"aliases": ["tau"], "quantity_units": "min"}}
    holders = [s["stream_properties"] for s in doc["streams"]]
    for holder in holders + doc["utilities"]["heat_utilities"]:
        if "pressure" in holder:
            holder["pressure"] /= 1e5
    for unit in doc["units"]:
        specs = unit.get("design_input_specs", {})
        if "P" in specs:
            specs["P"] /= 1e5
        if "tau" in specs:
            specs["tau"] *= 60.0
    for i, chem in enumerate(doc["chemicals"]):
        chem["index"] = i
    split_unit = next(u for u in doc["units"] if u["id"] == "S1")
    split_unit["design_input_specs"]["split"] = [0.5] * len(doc["chemicals"])
    return doc


class TestRevision5PartB(unittest.TestCase):
    """Revision 5 part B: design-spec units via quantity_units_global
    (R5.2), split vectors (R5.4), a registry id contradicting the name
    (R5.5), CMP-THM-03 via the database (R5.6), keywords (R5.7), and the
    MockSplitter correction to R5.1."""

    # ---- R5.2 design-spec units ----------------------------------------

    def test_spec_dimension_and_stated_unit(self):
        """_spec_dimension: T -> temperature, dT -> temperature_difference,
        tau -> time, OLR -> loading_rate; V dimensionless (None) except in a
        batch bioreactor or crystallizer (volume); split / LHK / N_stages /
        a non-string -> None. _stated_unit: an inline {value, units} pair's
        own unit, else the key's quantity_units_global unit, else None.
        _spec_unit_recognised: degC for T -> True; bar for T, '' (ambiguous),
        None, any unit for OLR (no table) and m3 for a flash's V -> False;
        m3 for a batch bioreactor's V -> True."""
        self.assertEqual(C._spec_dimension("T"), "temperature")
        self.assertEqual(C._spec_dimension("dT"), "temperature_difference")
        self.assertEqual(C._spec_dimension("tau"), "time")
        self.assertEqual(C._spec_dimension("OLR"), "loading_rate")
        self.assertIsNone(C._spec_dimension("V"))
        self.assertEqual(C._spec_dimension("V", "batch_bioreactor"), "volume")
        self.assertEqual(C._spec_dimension("V", "crystallizer"), "volume")
        for key in ("split", "LHK", "N_stages", 3):
            self.assertIsNone(C._spec_dimension(key), key)
        self.assertEqual(C._stated_unit({"value": 1.0, "units": "bar"}, "P",
                                        {"P": "Pa"}), "bar")
        self.assertEqual(C._stated_unit(1.0, "P", {"P": "kPa"}), "kPa")
        self.assertIsNone(C._stated_unit(1.0, "P", {}))
        self.assertTrue(C._spec_unit_recognised("T", "degC"))
        for key, unit, family in (("T", "bar", None), ("T", "", None),
                                  ("T", None, None), ("OLR", "kg/m3/d", None),
                                  ("V", "m3", "flash")):
            self.assertFalse(C._spec_unit_recognised(key, unit, family), key)
        self.assertTrue(C._spec_unit_recognised("V", "m3", "batch_bioreactor"))

    def test_to_internal_with_spec_dimensions(self):
        """_to_internal with a spec dimension (R5.2): T 80 degC -> 353.15 K;
        a temperature DIFFERENCE dT of 5 degC -> 5 (no offset), of 9 degF ->
        5; tau 30 min -> 0.5 h; Q 1 kW -> 3600 kJ/hr; duty_per_kg 1 MJ/kg ->
        1000 kJ/kg; V_max 500 L -> 0.5 m3; P 3 furlongs -> _Unconverted(3)
        (unrecognised: no bounds, no guard); no unit -> the number as is."""
        cases = (("T", 80.0, "degC", "temperature", 353.15),
                 ("dT", 5.0, "degC", "temperature_difference", 5.0),
                 ("dT", 9.0, "degF", "temperature_difference", 5.0),
                 ("tau", 30.0, "min", "time", 0.5),
                 ("Q", 1.0, "kW", "heat_flow", 3600.0),
                 ("duty_per_kg", 1.0, "MJ/kg", "specific_energy", 1000.0),
                 ("V_max", 500.0, "L", "volume", 0.5))
        for key, value, unit, dimension, expected in cases:
            self.assertAlmostEqual(C._to_internal(value, key, {key: unit},
                                                  dimension), expected,
                                   msg=key)
        odd = C._to_internal(3.0, "P", {"P": "furlongs"}, "pressure")
        self.assertIsInstance(odd, C._Unconverted)
        self.assertEqual(float(odd), 3.0)
        self.assertEqual(C._to_internal(3.0, "P", {}, "pressure"), 3.0)

    def test_internal_unit(self):
        """_internal_unit (R5.2 / R5.4) -> (scoring copy, {key: stated
        unit}): T 80 under a degC alias -> 353.15, an evaporator's list of
        pressures in bar -> element-wise Pa, tau without an alias unchanged,
        the non-dimensional 'material' untouched; a 2-element split kept
        when 2 chemicals carry indices 0..1, dropped (INVALID == MISSING)
        when the basis is 3 or None; a dict split kept only when every key
        is a declared chemical id; the stated-unit map records T and P; a
        unit without a specs object comes back as is with {}; the input
        unit is not modified."""
        unit = {"id": "U", "design_input_specs": {
            "T": 80.0, "P": [1.0, 2.0], "tau": 2.0, "material": "SS316",
            "split": [0.5, 0.5]}}
        index = {"T": "degC", "P": "bar"}
        copy_, units = C._internal_unit(unit, index, 2, frozenset())
        specs = copy_["design_input_specs"]
        self.assertAlmostEqual(specs["T"], 353.15)
        self.assertEqual(specs["P"], [1e5, 2e5])
        self.assertEqual((specs["tau"], specs["material"], specs["split"]),
                         (2.0, "SS316", [0.5, 0.5]))
        self.assertEqual(units, {"T": "degC", "P": "bar"})
        self.assertEqual(unit["design_input_specs"]["T"], 80.0)
        for basis in (3, None):
            self.assertNotIn("split", C._internal_unit(
                unit, index, basis, frozenset())[0]["design_input_specs"])
        unit = {"id": "U", "design_input_specs": {"split": {"Water": 0.5}}}
        self.assertIn("split", C._internal_unit(
            unit, {}, None, frozenset({"Water"}))[0]["design_input_specs"])
        self.assertNotIn("split", C._internal_unit(
            unit, {}, None, frozenset({"Ethanol"}))[0]["design_input_specs"])
        bare = {"id": "U"}
        self.assertEqual(C._internal_unit(bare, index, None, frozenset()),
                         (bare, {}))

    def test_degc_bar_specs_score_like_k_pa(self):
        """R5.2 reviewer case: r5_doc (degC / bar, T and P aliased) with P1
        P = 3 and H1 T = 80 -- correct specs -- used to be flagged
        'inconsistent' (CMP-UOP-02 0.23 instead of 0.73, overall 0.431
        instead of 0.4675). Now every criterion equals that of its K / Pa
        twin (r5_kpa_twin: P = 3e5, T = 353.15) within 1e-9, CMP-UOP-02
        names no inconsistency, and CMP-UOP-05 credits P1 and H1 in full
        (their only dimensional specs are alias-covered with recognised
        units). The same doc with T = 353.15 (a K value under the degC
        alias) is inconsistent: the alias is applied."""
        doc = r5_doc()
        specs = {"P1": {"P": 3.0}, "H1": {"T": 80.0}}
        for unit in doc["units"]:
            if unit["id"] in specs:
                unit["design_input_specs"] = dict(specs[unit["id"]])
        twin = r5_kpa_twin(doc)
        self.assertEqual(twin["units"][1]["design_input_specs"], {"T": 353.15})
        a, b = criterion_results(doc), criterion_results(twin)
        for cid, result in a.items():
            with self.subTest(criterion=cid):
                if result.score is None:
                    self.assertIsNone(b[cid].score)
                else:
                    self.assertAlmostEqual(result.score, b[cid].score,
                                           delta=1e-9)
        self.assertAlmostEqual(overall(doc), overall(twin), delta=1e-9)
        self.assertNotIn("inconsistent",
                         "\n".join(a["CMP-UOP-02"].missing))
        sc = ctx(doc)
        for uid in ("P1", "H1"):
            self.assertEqual(C._uop_05_unit(sc, sc.unit_positions[uid],
                                            False), (1.0, []), uid)
        doc["units"][1]["design_input_specs"]["T"] = 353.15
        _score, _m, missing = run(C._score_uop_02, doc)
        self.assertIn("units[H1].design_input_specs: one of T|H|V "
                      "(inconsistent: T disagrees with the outlet stream)",
                      missing)

    def test_uop_05_unit(self):
        """_uop_05_unit (R5.2) -> (credit, missing), the MIN over applying
        requirements of the MAX over their counted keys: a Pump stating P
        and dP_design (no pressure rise, so dP_design is needed) with
        neither aliased -> DERIVED_CREDIT and a locator for each; P aliased
        (Pa) -> still DERIVED_CREDIT (dP_design lacks a unit); dP_design
        aliased too -> 1. A Flash (two of T, P, V, Q, y, x) stating T and P
        with only T aliased -> DERIVED_CREDIT ('P stated without a
        recognised quantity unit'), both -> 1: a two-key requirement needs
        two unit-known keys. A Splitter stating only its (dimensionless) split
        vector -> 1. A ThermalOxidizer whose registry entry lacks
        duty_per_kg (every requirement waived) -> 1. A unit of unknown
        family (one-inlet 'Mixer' label) -> 0 ('needs are unknown'). A
        port-less HeatExchangerNetwork facility with T_min_app 5 and its
        units ->
        DERIVED_CREDIT; T_min_app aliased under a degC entry (a temperature
        difference) -> 1."""
        doc = one_unit_doc(spec_unit("Pump", {"P": 101325.0,
                                              "dP_design": 5e5}))
        credit, missing = C._uop_05_unit(ctx(doc), 0, False)
        self.assertEqual(credit, C.DERIVED_CREDIT)
        self.assertEqual(len(missing), 2)
        doc["quantity_units_global"]["pressure"]["aliases"].append("P")
        credit, missing = C._uop_05_unit(ctx(doc), 0, False)
        self.assertEqual(credit, C.DERIVED_CREDIT)
        self.assertEqual(missing, ["units[U1].design_input_specs: dP_design "
                                   "stated without a recognised quantity "
                                   "unit (class conventions imply it)"])
        doc["quantity_units_global"]["pressure"]["aliases"].append("dP_design")
        self.assertEqual(C._uop_05_unit(ctx(doc), 0, False), (1.0, []))
        doc = one_unit_doc(spec_unit("Splitter", {"split": [0.5]}),
                           outs=({}, {}))
        self.assertEqual(C._uop_05_unit(ctx(doc), 0, False), (1.0, []))
        doc = one_unit_doc(spec_unit("ThermalOxidizer"))
        registry = {"ThermalOxidizer": {"design_input_spec_params": {
            "tau": {}}}}
        self.assertEqual(C._uop_05_unit(ctx(doc, registry=registry), 0,
                                        False), (1.0, []))
        credit, missing = C._uop_05_unit(ctx(valid_doc()), 0, False)
        self.assertEqual(credit, 0.0)
        self.assertIn("its needs are unknown", missing[0])
        flash = one_unit_doc(spec_unit("Flash", {"T": 300.0, "P": 101325.0}),
                             outs=({}, {}))
        flash["quantity_units_global"]["temperature"]["aliases"].append("T")
        credit, missing = C._uop_05_unit(ctx(flash), 0, False)
        self.assertEqual(credit, C.DERIVED_CREDIT)
        self.assertEqual(missing, ["units[U1].design_input_specs: P stated "
                                   "without a recognised quantity unit "
                                   "(class conventions imply it)"])
        flash["quantity_units_global"]["pressure"]["aliases"].append("P")
        self.assertEqual(C._uop_05_unit(ctx(flash), 0, False), (1.0, []))
        doc = one_unit_doc(spec_unit("HeatExchangerNetwork", {
            "T_min_app": 5.0, "units": ["U1"]}), ins=(), outs=())
        sc = ctx(doc)
        self.assertEqual(C._facility_population(sc), [0])
        self.assertEqual(C._uop_05_unit(sc, 0, True)[0], C.DERIVED_CREDIT)
        doc["quantity_units_global"]["approach"] = {
            "aliases": ["T_min_app"], "quantity_units": "degC"}
        self.assertEqual(C._uop_05_unit(ctx(doc), 0, True), (1.0, []))

    # ---- R5.4 split vectors ---------------------------------------------

    def test_split_basis_and_readable(self):
        """_split_basis: the count of chemicals carrying an integer index
        when those indices are exactly 0..n-1 ([0, 1] -> 2; a chemical
        without one ignored -> 1), else None ([1, 2], a duplicate 0, a
        boolean index, none at all). _split_readable: a list is readable
        only at the basis length (2 of 2 yes; 1 of 2, or any with no basis,
        no); a dict only when every key is a declared chemical id; a scalar
        always."""
        self.assertEqual(C._split_basis([{"index": 0}, {"index": 1}]), 2)
        self.assertEqual(C._split_basis([{"index": 0}, {"id": "X"}]), 1)
        for chems in ([{"index": 1}, {"index": 2}],
                      [{"index": 0}, {"index": 0}], [{"index": True}], []):
            self.assertIsNone(C._split_basis(chems), chems)
        self.assertTrue(C._split_readable([0.5, 0.5], 2, frozenset()))
        self.assertFalse(C._split_readable([0.5], 2, frozenset()))
        self.assertFalse(C._split_readable([0.5], None, frozenset()))
        self.assertTrue(C._split_readable({"Water": 0.5}, None,
                                          frozenset({"Water"})))
        self.assertFalse(C._split_readable({"X": 0.5}, 1, frozenset({"Water"})))
        self.assertTrue(C._split_readable(0.5, None, frozenset()))

    def test_unreadable_split_counts_as_missing(self):
        """R5.4 on a one-Splitter doc (valid_doc's Ethanol, index 0): split
        [0.5] -> CMP-UOP-02 1; the chemical's index deleted, or the vector
        lengthened to [0.5, 0.5] -> the split counts as unstated and the
        requirement falls to derived credit (DERIVED_CREDIT, from the full
        port states; the unit scores (DERIVED_CREDIT + 1) / 2 with its
        class-waived moisture requirement met, R5.9); a dict split keyed by
        the declared Ethanol -> 1, by an undeclared Water -> derived."""
        def uop_02(split, drop_index=False):
            doc = one_unit_doc(spec_unit("Splitter", {"split": split}),
                               outs=({}, {}))
            if drop_index:
                del doc["chemicals"][0]["index"]
            return run(C._score_uop_02, doc)[0]
        derived = (C.DERIVED_CREDIT + 1.0) / 2
        self.assertEqual(uop_02([0.5]), 1.0)
        self.assertEqual(uop_02([0.5], drop_index=True), derived)
        self.assertEqual(uop_02([0.5, 0.5]), derived)
        self.assertEqual(uop_02({"Ethanol": 0.5}), 1.0)
        self.assertEqual(uop_02({"Water": 0.5}), derived)

    # ---- R5.5 / R5.6 chemical identity and molar mass --------------------

    R5_RECORDS = {"Ethanol": (46.06844, "C2H6O"),
                  "Glucose": (180.15588, "C6H12O6"),
                  "7732-18-5": (18.01528, "H2O"),
                  "64-17-5": (46.06844, "C2H6O"),
                  "50-99-7": (180.15588, "C6H12O6"),
                  "SMILES=CCO": (46.06844, "C2H6O"),
                  "12345-67-8": (None, None)}

    def test_records_disagree(self):
        """_records_disagree (R5.5): formulas that both parse decide (C2H6O
        vs H2O -> True; same counts -> False, even with other molar
        masses); else molar masses beyond REGISTRY_MW_RTOL (46.07 vs 18 ->
        True; 46.07 vs 46.5 -> False); nothing comparable -> False."""
        ns = types.SimpleNamespace
        self.assertTrue(C._records_disagree(ns(MW=46.07, formula="C2H6O"),
                                            ns(MW=18.0, formula="H2O")))
        self.assertFalse(C._records_disagree(ns(MW=1.0, formula="C2H6O"),
                                             ns(MW=46.07, formula="C2H6O")))
        self.assertTrue(C._records_disagree(ns(MW=46.07), ns(MW=18.0)))
        self.assertFalse(C._records_disagree(ns(MW=46.07), ns(MW=46.5)))
        self.assertFalse(C._records_disagree(ns(), ns(MW=18.0)))

    def test_registry_id_contradicting_the_name(self):
        """R5.5 reviewer cases: {'id': 'Ethanol', 'registry_id':
        '7732-18-5'} (water's CAS) and Glucose with SMILES 'CCO' (ethanol),
        neither stating a molar mass or formula -> _registry_status
        'conflicted' and _thm_02_credit capped at REGISTRY_UNRESOLVED_CREDIT
        (tier 'conflicted', a note naming the conflict) instead of 1; the
        id may be passed as `cid`. Ethanol with its own CAS -> 'resolved',
        1. A molar mass settles it: 46.07 against water's CAS ->
        'contradicted' (the name tier wins, 0.75); 18.02 -> 'resolved' (1).
        _score_thm_02 counts the conflict in its message."""
        with name_lookup(self.R5_RECORDS):
            ethanol = {"id": "Ethanol", "registry_id": "7732-18-5"}
            self.assertEqual(C._registry_status(ethanol), "conflicted")
            self.assertEqual(C._registry_status(
                {"registry_id": "7732-18-5"}, "Ethanol"), "conflicted")
            credit, note, tier = C._thm_02_credit(ethanol, "Ethanol")
            self.assertEqual((credit, tier), (C.REGISTRY_UNRESOLVED_CREDIT,
                                              "conflicted"))
            self.assertIn("resolves to another species than the id names",
                          note)
            glucose = {"id": "Glucose", "registry_id": "CCO"}
            self.assertEqual(C._thm_02_credit(glucose, "Glucose")[::2],
                             (C.REGISTRY_UNRESOLVED_CREDIT, "conflicted"))
            self.assertEqual(C._thm_02_credit(
                {"id": "Ethanol", "registry_id": "64-17-5"}, "Ethanol"),
                (1.0, None, "registry"))
            self.assertEqual(C._registry_status(dict(ethanol,
                                                     molar_mass=46.07)),
                             "contradicted")
            self.assertEqual(C._thm_02_credit(dict(ethanol, molar_mass=46.07),
                                              "Ethanol")[0],
                             C.NAME_RESOLVED_CREDIT)
            self.assertEqual(C._thm_02_credit(dict(ethanol, molar_mass=18.02),
                                              "Ethanol")[0], 1.0)
            doc = valid_doc()
            doc["chemicals"][0] = dict(ethanol, index=0)
            score, message, _missing = run(C._score_thm_02, doc)
            self.assertEqual(score, C.REGISTRY_UNRESOLVED_CREDIT)
            self.assertIn("1 an id naming another species than their name",
                          message)

    def test_thm_03_credit(self):
        """_thm_03_credit (R5.6), the MAX over: a stated molar mass or a
        parseable formula -> 1; a registry id the database resolves to a
        record with a molar mass (CAS-resolved Glucose, no molar mass) -> 1;
        a thermosteam built-in name (Cellulose) -> NAME_RESOLVED_CREDIT with
        a 'built-in' note; R6.8: an unverified database name (Glucose alone,
        or a conflicted id -- Ethanol + water's CAS -- whose name still
        resolves) -> NAME_UNVERIFIED_CREDIT with an 'unverified' note; a
        resolved id whose record has no molar mass on an id no name
        resolves -> 0 with a molar_mass locator. With the database
        unavailable a CAS earns nothing, a built-in still 0.75.
        _score_thm_03 on valid_doc with Ethanol's molar mass and formula
        replaced by its CAS -> 1."""
        with name_lookup(self.R5_RECORDS):
            self.assertEqual(C._thm_03_credit({"molar_mass": 46.07}, "X"),
                             (1.0, None))
            self.assertEqual(C._thm_03_credit({"formula": "H2O"}, "X"),
                             (1.0, None))
            self.assertEqual(C._thm_03_credit(
                {"id": "Glucose", "registry_id": "50-99-7"}, "Glucose"),
                (1.0, None))
            credit, note = C._thm_03_credit({"id": "Cellulose"}, "Cellulose")
            self.assertEqual(credit, C.NAME_RESOLVED_CREDIT)
            self.assertIn("only from the thermosteam built-in", note)
            for chem in ({"id": "Glucose"},
                         {"id": "Ethanol", "registry_id": "7732-18-5"}):
                credit, note = C._thm_03_credit(chem, chem["id"])
                self.assertEqual(credit, C.NAME_UNVERIFIED_CREDIT, chem)
                self.assertIn("unverified: it may name another species", note)
            chem = {"id": "X", "registry_id": "12345-67-8"}
            credit, note = C._thm_03_credit(chem, chem["id"])
            self.assertEqual(credit, 0.0)
            self.assertIn(".molar_mass (or a parseable formula", note)
            doc = valid_doc()
            chem = doc["chemicals"][0]
            del chem["molar_mass"], chem["formula"]
            chem["registry_id"] = "64-17-5"
            score, message, missing = run(C._score_thm_03, doc)
            self.assertEqual((score, missing), (1.0, []))
            self.assertIn("1 of 1 chemical(s) have a molar mass", message)
        with name_lookup(search=None):
            self.assertEqual(C._thm_03_credit(
                {"id": "Glucose", "registry_id": "50-99-7"}, "Glucose")[0], 0.0)
            self.assertEqual(C._thm_03_credit({"id": "Cellulose"},
                                              "Cellulose")[0],
                             C.NAME_RESOLVED_CREDIT)

    # ---- R5.7 keywords ----------------------------------------------------

    def test_r5_7_keywords(self):
        """_keyword_family (R5.7): ion exchange (column / exchanger /
        IonExchange) -> splitter; knock-out / knockout drum, knock-out pot
        -> flash; fire water tank (FireWaterTank) -> facility, but a fire
        water pump stays a pump; (glycol) chiller -> hx_utility; fan,
        induced draft fan, blower -> compressor, 'Fanning' -> None;
        (gas) expander -> turbine; (rotary) kiln -> reactor; sterilizer /
        steriliser / media sterilization -> hx_utility; adsorber, carbon
        adsorber, adsorption column -> splitter."""
        cases = {"Ion exchange": "splitter", "Ion exchange column": "splitter",
                 "Ion exchanger": "splitter", "IonExchange": "splitter",
                 "Knock-out drum": "flash", "Knockout drum": "flash",
                 "Knock out pot": "flash", "KnockOutDrum": "flash",
                 "Fire water tank": "facility", "FireWaterTank": "facility",
                 "Fire water pump": "pump", "Chiller": "hx_utility",
                 "Glycol chiller": "hx_utility", "Fan": "compressor",
                 "Induced draft fan": "compressor", "Blower": "compressor",
                 "Fanning": None, "Expander": "turbine",
                 "Gas expander": "turbine", "Kiln": "reactor",
                 "Rotary kiln": "reactor", "Sterilizer": "hx_utility",
                 "Steriliser": "hx_utility",
                 "Media sterilization": "hx_utility", "Adsorber": "splitter",
                 "Carbon adsorber": "splitter",
                 "Adsorption column": "splitter"}
        for text, family in cases.items():
            self.assertEqual(C._keyword_family(text), family, text)

    # ---- R5.1 correction: MockSplitter -----------------------------------

    def test_mock_splitter_exempt_from_t_p_bounds(self):
        """R5.1 correction: a MockSplitter (FakeSplitter) does nothing when
        simulated, so outlets at 298 K / 1 atm beside a 265 K / 10 atm
        inlet (corpus S408 / S410) leave its claim standing -- class
        MockSplitter, family junction, confidence 1 -- while a Splitter with
        the same streams becomes unknown family; a recorded heat duty
        (_physics_contradiction: 'implausible for a mock splitter') still
        contradicts it."""
        def doc_of(name, results=None):
            unit = spec_unit(name)
            if results is not None:
                unit["utility_consumption_results"] = results
            return one_unit_doc(unit, ins=({"T": 265.0, "P": 1013250.0},),
                                outs=({"T": 298.0}, {"T": 298.0}))
        for name in sorted(C.MOCK_SPLITTER_CLASSES):
            sc = ctx(doc_of(name))
            self.assertIn("splitter", sc.unit_physics[0])
            self.assertEqual(sc.unit_classes[0], (name, "junction", 1.0))
        self.assertIsNone(ctx(doc_of("Splitter")).unit_classes[0][1])
        sc = ctx(doc_of("MockSplitter", {"steam": 5.0}))
        self.assertIsNone(sc.unit_classes[0][1])
        self.assertIn("implausible for a mock splitter: records a non-zero "
                      "heat duty", C._physics_contradiction(
                          "MockSplitter", "junction", sc.unit_physics[0]))

    # ---- deletion sweeps over alias-covered specs and a split vector -----

    def test_r5_sweep_docs_deletion_sweeps(self):
        """r5_sweep_doc (pressures in bar with P aliased, T aliased in K,
        tau aliased in minutes, chemicals indexed, S1's split a 4-element
        vector), both identities: the specs convert (F1's P reads 101325 Pa,
        consistent with its outlets: CMP-UOP-02 names no inconsistency),
        CMP-UOP-05 credits H1 (T) and F1 (T and P) in full, while P1 still
        lacks dP_design (no pressure rise shown); the STRICT sweep over
        every field -- aliases lists, quantity units, chemical indices,
        split and spec keys included -- raises neither the overall score
        nor any criterion. Deleting any one chemical's index lowers
        CMP-UOP-02 (S1's split vector becomes unreadable); dropping P from
        the pressure aliases lowers CMP-UOP-05 and raises nothing."""
        for identity in ("unit_type", "design_simulation_method"):
            with self.subTest(identity=identity):
                doc = r5_sweep_doc(identity)
                sc = ctx(doc)
                f1 = sc.unit_positions["F1"]
                self.assertAlmostEqual(
                    sc.units[f1]["design_input_specs"]["P"], 101325.0)
                self.assertNotIn("inconsistent",
                                 "\n".join(run(C._score_uop_02, doc)[2]))
                for uid in ("H1", "F1"):
                    self.assertEqual(C._uop_05_unit(
                        sc, sc.unit_positions[uid], False), (1.0, []), uid)
                # P1 needs dP_design too (no pressure rise shown), unstated.
                self.assertIn("one of dP_design unstated", C._uop_05_unit(
                    sc, sc.unit_positions["P1"], False)[1][0])
                with name_lookup(set()):
                    raised, lowered, n = deletion_violations(
                        doc, skip=frozenset())
                self.assertGreater(n, 250)
                self.assertEqual(raised, [])
                self.assertGreater(lowered, n // 2)
                base = criterion_results(doc)
                for i in range(len(doc["chemicals"])):
                    after = criterion_results(deleted(
                        doc, ("chemicals", i, "index")))
                    self.assertLess(after["CMP-UOP-02"].score,
                                    base["CMP-UOP-02"].score, i)
                fewer = copy.deepcopy(doc)
                fewer["quantity_units_global"]["pressure"]["aliases"].remove(
                    "P")
                after = criterion_results(fewer)
                self.assertLess(after["CMP-UOP-05"].score,
                                base["CMP-UOP-05"].score)
                for cid, before in base.items():
                    if before.status == "scored":
                        self.assertLessEqual(after[cid].score,
                                             before.score + 1e-12, cid)


# ------------------------------------------ Revision 5, part C (R5.9)

# id, source, sink, flows (kmol/h), phase, T, P, price: feed -> P1 Pump
# (pressure rise 101325 -> 3e5 Pa) -> H1 HXutility (liquid in and out) ->
# X1 HXprocess (second side: hot water feed -> hot_out; liquid throughout)
# -> R1 EnzymeTreatment (no reaction, flows unchanged) -> S1 Splitter -> two
# products. Internal streams state flows and one-phase compositions (every
# balance closes) but no temperature, so no outlet-T derivation is
# available; only s1 and the boundary streams state a pressure.
WAIVER_EDGES = (
    ("feed", "None", "P1", {"Water": 90.0, "Glucose": 10.0}, "l", 300.0,
     101325.0, 0.1),
    ("s1", "P1", "H1", {"Water": 90.0, "Glucose": 10.0}, "l", None, 3.0e5,
     None),
    ("s2", "H1", "X1", {"Water": 90.0, "Glucose": 10.0}, "l", None, None,
     None),
    ("hot", "None", "X1", {"Water": 50.0}, "l", 380.0, 101325.0, 0.0),
    ("s3", "X1", "R1", {"Water": 90.0, "Glucose": 10.0}, "l", None, None,
     None),
    ("hot_out", "X1", "None", {"Water": 50.0}, "l", 330.0, 101325.0, 0.0),
    ("s4", "R1", "S1", {"Water": 90.0, "Glucose": 10.0}, "l", None, None,
     None),
    ("out1", "S1", "None", {"Water": 45.0, "Glucose": 5.0}, "l", 320.0,
     101325.0, 0.05),
    ("out2", "S1", "None", {"Water": 45.0, "Glucose": 5.0}, "l", 320.0,
     101325.0, 0.05))
# Each unit states its class exactly AND a family-bearing unit_type, so
# deleting design_simulation_method keeps the family but loses the class.
WAIVER_UNITS = (
    ("P1", "Pump", "Feed pump", {"dP_design": 2.0e5}),
    ("H1", "HXutility", "Heat exchanger", {"V": 0.0}),
    ("X1", "HXprocess", "Process heat exchanger", {"phase0": "l"}),
    ("R1", "EnzymeTreatment", "Enzyme treatment reactor",
     {"tau": 2.0, "batch": False}),
    ("S1", "Splitter", "Splitter", {"moisture_content": 0.5}))
# unit id -> (the keys of its waived requirement, why it is waived).
WAIVED = {"P1": (("dP_design",), "pressure rise known >= 1 Pa"),
          "H1": (("V",), "phase sets known and equal, non-rigorous"),
          "X1": (("phase0", "phase1"), "phase sets known and equal"),
          "R1": (("batch",), "named class takes no batch"),
          "S1": (("moisture_content", "sludge_moisture"),
                 "exact class takes no moisture_content")}


def waiver_doc():
    """A consistent flowsheet (WAIVER_EDGES / WAIVER_UNITS) in which every
    CMP-UOP-02 waiver path is active on a unit that nonetheless STATES the
    waived key and misses another requirement (so the waiver decides the
    unit score): P1 (Pump, dP_design stated, P only derivable; waived by
    the known pressure rise), H1 (HXutility, non-rigorous V stated, no T;
    waived by equal liquid phase sets), X1 (HXprocess, phase0 stated, no
    dT / T_lim / H_lim and no derivable outlet T; waived by equal phase
    sets), R1 (EnzymeTreatment, batch stated, no T or V_wf; waived by its
    class lacking batch) and S1 (Splitter, moisture_content stated, split
    neither stated nor derivable; waived by its exact class lacking
    moisture_content). P1 and H1 record utility results; chemicals carry
    formula and molar mass; steam and grid agents are declared."""
    streams = []
    for sid, source, sink, flows, phase, T, P, price in WAIVER_EDGES:
        stream_ = {"id": sid, "source_unit_id": source, "sink_unit_id": sink,
                   "stream_properties": _sweep_sp(flows, phase, T, P)}
        if price is not None:
            stream_["price"] = price
        streams.append(stream_)
    units = [{"id": uid, "unit_type": label,
              "design_simulation_method": f"{name} on {BST_URL}",
              "design_input_specs": dict(specs)}
             for uid, name, label, specs in WAIVER_UNITS]
    units[0]["utility_consumption_results"] = {"grid": 1.0}
    units[1]["utility_consumption_results"] = {"steam": 50.0}
    chemicals = [{"id": "Water", "formula": "H2O",
                  "molar_mass": SWEEP_MW["Water"]},
                 {"id": "Glucose", "formula": "C6H12O6",
                  "molar_mass": SWEEP_MW["Glucose"]}]
    utilities = {
        "heat_utilities": [{"id": "steam", "temperature": 412.0,
                            "pressure": 344738.0, "composition": [
                                {"phase": "g", "component_name": "Water",
                                 "mol_fraction": 1.0}]}],
        "power_utilities": [{"id": "grid", "electrical_energy_price": 0.07}]}
    return {"metadata": {"sff_version": "0.2.1", "TEA_currency": "USD"},
            "units": units, "streams": streams, "chemicals": chemicals,
            "utilities": utilities}


class TestRevision5PartC(unittest.TestCase):
    """Revision 5 part C (R5.9): a requirement WAIVED on affirmative
    evidence (equal phase sets, a known pressure rise, an exactly known
    class lacking the parameter) stays in the unit's CMP-UOP-02 mean and
    counts as met. Dropping it instead let deleting the waiving evidence
    add a stated -- hence satisfied -- requirement to the mean and raise
    the unit score on a consistent document."""

    def unit_score(self, doc, uid):
        sc = ctx(doc)
        return C._unit_spec_score(sc, sc.unit_positions[uid])[0]

    def waived_req(self, doc, uid):
        """Whether the requirement of unit `uid` whose keys WAIVED names
        applies (_requirement_applies)."""
        sc = ctx(doc)
        position = sc.unit_positions[uid]
        req = next(r for r in C._unit_rules(sc, position)
                   if r.keys == WAIVED[uid][0])
        return C._requirement_applies(req, sc, position)

    def test_hx_process_phase_waiver_counts_as_met(self):
        """The reported defect: an HXprocess (two liquid inlets, two liquid
        outlets, no outlet T, so T_lim0 is not derivable; none of dT /
        T_lim / H_lim stated -> first requirement 0) stating phase0, its
        phase sets known and unchanged. Expected: phase0|phase1 waived and
        met -> unit 0.5 (was 0.0); deleting every outlet's phase data
        un-waives it (phase0 stated -> 1) -> still 0.5 (was 0.5: a rise
        from 0.0), never higher."""
        doc = one_unit_doc(spec_unit("HXprocess", {"phase0": "l"}),
                           ins=({}, {}), outs=({"T": None}, {"T": None}))
        sc = ctx(doc)
        first, phases = C._unit_rules(sc, 0)
        self.assertEqual(C._requirement_score(first, sc, 0, {"phase0"}), 0.0)
        self.assertFalse(C._requirement_applies(phases, sc, 0))
        self.assertEqual(C._unit_spec_score(sc, 0), (
            0.5, ["units[U1].design_input_specs: one of "
                  "dT|T_lim0|T_lim1|H_lim0|H_lim1"]))
        for stream_ in doc["streams"][2:]:
            del stream_["stream_properties"]["phases"]
        sc = ctx(doc)
        self.assertTrue(C._requirement_applies(phases, sc, 0))
        self.assertEqual(C._unit_spec_score(sc, 0)[0], 0.5)

    def test_pump_dp_design_waiver_counts_as_met(self):
        """A Pump stating only dP_design, inlet 101325 Pa -> outlet 3e5 Pa
        (P derivable from the outlet: 0.75; dP_design waived by the known
        rise and met) -> (0.75 + 1) / 2 = 0.875 (was 0.75). Deleting the
        inlet pressure un-waives dP_design (stated -> 1) -> 0.875 (was a
        rise to 0.875); deleting the outlet pressure instead also loses the
        P derivation -> 0.5. CMP-UOP-05 (a min over requirements, P only
        derivable -> DERIVED_CREDIT for the exact class) stays at
        DERIVED_CREDIT when dP_design is un-waived (stated without a unit):
        the waived requirement's credit 1 is the min's identity."""
        doc = one_unit_doc(spec_unit("Pump", {"dP_design": 2.0e5}),
                           outs=({"P": 3.0e5},))
        sc = ctx(doc)
        self.assertEqual(C._unit_spec_score(sc, 0)[0], 0.875)
        self.assertEqual(C._uop_05_unit(sc, 0, False)[0], C.DERIVED_CREDIT)
        no_inlet_P = deleted(doc, ("streams", 0, "stream_properties",
                                   "pressure"))
        sc = ctx(no_inlet_P)
        self.assertEqual(C._unit_spec_score(sc, 0)[0], 0.875)
        self.assertEqual(C._uop_05_unit(sc, 0, False)[0], C.DERIVED_CREDIT)
        no_outlet_P = deleted(doc, ("streams", 1, "stream_properties",
                                    "pressure"))
        self.assertEqual(C._unit_spec_score(ctx(no_outlet_P), 0)[0], 0.5)

    def test_class_waivers_count_as_met(self):
        """Class-knowledge waivers (R3.5), each on a unit that states the
        waived key: a Splitter (exact class, no moisture_content) stating
        moisture_content, split neither stated nor derivable (an outlet
        without T) -> (0 + 1) / 2 = 0.5 (was 0.0); an EnzymeTreatment
        (named class, no batch) stating tau and batch, its outlet without T
        -> (0 + 1 + 0 + 1) / 4 = 0.5 (was 1/3). Replacing each class by a
        family-only unit_type ('Splitter', 'Enzyme treatment reactor')
        un-waives the requirement (stated -> 1) and leaves the score at 0.5
        (a rise to 0.5 before R5.9)."""
        for name, label, specs, outs in (
                ("Splitter", "Splitter", {"moisture_content": 0.5},
                 ({}, {"T": None})),
                ("EnzymeTreatment", "Enzyme treatment reactor",
                 {"tau": 2.0, "batch": False}, ({"T": None},))):
            with self.subTest(name=name):
                unit = dict(spec_unit(name, specs), unit_type=label)
                doc = one_unit_doc(unit, outs=outs)
                sc = ctx(doc)
                self.assertEqual(sc.unit_classes[0][0::2], (name, 1.0))
                waived = [r for r in C._unit_rules(sc, 0)
                          if not C._requirement_applies(r, sc, 0)]
                self.assertEqual(len(waived), 1)
                self.assertEqual(C._unit_spec_score(sc, 0)[0], 0.5)
                sc = ctx(deleted(doc, ("units", 0,
                                       "design_simulation_method")))
                self.assertNotEqual(sc.unit_classes[0][2], 1.0)
                self.assertTrue(C._requirement_applies(waived[0], sc, 0))
                self.assertEqual(C._unit_spec_score(sc, 0)[0], 0.5)

    def test_waiver_doc_deletion_sweep(self):
        """waiver_doc(): every waiver path active before the sweep (each
        unit's WAIVED requirement does not apply) and un-waived by one
        deletion -- the feed's pressure (P1), s2's phases (H1 and X1: its
        phase set becomes unknown), R1's and S1's design_simulation_method
        (the unit_type family remains, the exact class goes) -- without the
        unit score rising. Unit scores: P1 0.875, H1 0.5, X1 0.5, R1 0.5,
        S1 0.5 -- also on the mass-basis twin (mass_basis, R5.3). The STRICT
        sweep deleting every field (ids, endpoints, unit_type,
        design_simulation_method, spec keys, pressures, phases,
        compositions), over both documents, raises neither the overall
        score nor any criterion and turns no scored criterion
        not-applicable."""
        for basis, doc in (("molar", waiver_doc()),
                           ("mass", mass_basis(waiver_doc()))):
            with self.subTest(basis=basis):
                self.assert_waivers_hold(doc)

    def assert_waivers_hold(self, doc):
        """The waiver_doc assertions of test_waiver_doc_deletion_sweep on
        one basis: exact classes, active waivers, unit scores, one-deletion
        un-waiving without a rise, and a clean strict sweep."""
        sc = ctx(doc)
        self.assertEqual(
            [c[0::2] for c in sc.unit_classes],
            [(name, 1.0) for _u, name, _l, _s in WAIVER_UNITS])
        for uid in WAIVED:
            self.assertFalse(self.waived_req(doc, uid), uid)
        self.assertEqual({uid: self.unit_score(doc, uid) for uid in WAIVED},
                         {"P1": 0.875, "H1": 0.5, "X1": 0.5, "R1": 0.5,
                          "S1": 0.5})
        unwaiving = {"P1": ("streams", 0, "stream_properties", "pressure"),
                     "H1": ("streams", 2, "stream_properties", "phases"),
                     "X1": ("streams", 2, "stream_properties", "phases"),
                     "R1": ("units", 3, "design_simulation_method"),
                     "S1": ("units", 4, "design_simulation_method")}
        for uid, path in unwaiving.items():
            after = deleted(doc, path)
            self.assertTrue(self.waived_req(after, uid), uid)
            self.assertLessEqual(self.unit_score(after, uid),
                                 self.unit_score(doc, uid), uid)
        with name_lookup(set()):
            raised, lowered, n = deletion_violations(doc, skip=frozenset())
        self.assertGreater(n, 180)
        self.assertEqual(raised, [])
        self.assertGreater(lowered, n // 2)


# ----------------------------------------- Revision 6 (R6.1 / R6.2) tests

# The reviewer's reactor-labelled fermenter: 'Fermentation reactor' naming
# CSTR on a biosteam URL (not a core class, so a label-level reactor claim).
R6_REACTOR = ("Fermentation reactor", "CSTR")
# R6.1 reviewer relabels of the fermenter R1: exact core classes (unit_type
# 'Unit'), label-only claims, and honest labels of chemistry-hosting steps.
R6_EXACT = ("Junction", "Scaler", "ConveyingBelt", "DrumDryer",
            "MolecularSieve", "HXutility", "StorageTank", "IsenthalpicValve")
R6_LABELS = ("Junction", "Conveyor", "Heater")
R6_HONEST_LABELS = ("Ammonia addition tank", "Conditioning tank",
                    "Overliming tank", "Liming tank", "Acidification tank",
                    "Mash tank", "Amine absorber", "Esterification column")


def r6_doc(unit_type, class_name=None):
    """r5_doc with the fermenter R1 relabelled: `unit_type`, and
    design_simulation_method '<class_name> on <biosteam url>' only when
    `class_name` is given (else a label-only claim)."""
    doc = r5_doc(dict(R5_HONEST, R1=(unit_type, class_name or "X")))
    if class_name is None:
        del doc["units"][2]["design_simulation_method"]
    return doc


def r6_tank_doc(balanced=True):
    """sweep_doc('unit_type') with the transfer pump P2 (between the
    fermenter R1 and the flash F1) relabelled a label-only 'Holding tank'
    stating tau: a claim that can host chemistry (R6.1(b)). `balanced`:
    its ports s4 / s5 carry equal one-phase compositions (affirmative
    evidence that nothing converts); else both lose their phases (species
    flows not computable, so no evidence)."""
    doc = sweep_doc("unit_type")
    doc["units"][4]["unit_type"] = "Holding tank"
    doc["units"][4]["design_input_specs"] = {"tau": 2.0}
    if not balanced:
        for position in (6, 7):
            del doc["streams"][position]["stream_properties"]["phases"]
    return doc


class TestRevision6(unittest.TestCase):
    """Revision 6: reaction need from stream evidence (R6.1 -- claims that
    cannot host chemistry are contradicted by converted species, claims
    that can keep their family but stay reactive without evidence that
    nothing converts, built-in chemistry is exempt only for its own
    signature, reaction-step keywords name a reactor) and reaction mass
    conservation (R6.2)."""

    # ---- R6.1 helpers ----------------------------------------------------

    def test_species_keys(self):
        """_species_keys: a name gives 'n:<lowercase, no separators>'; a
        name or declared formula that parses as a plain formula adds
        'f:<Hill-sorted counts>' -- 'CO2' -> {'n:co2', 'f:C1O2'};
        'CarbonDioxide' stated with formula CO2 shares 'f:C1O2'; 'MEA'
        (no element M) -> {'n:mea'}; nothing -> an empty set."""
        self.assertEqual(C._species_keys("CO2"), {"n:co2", "f:C1O2"})
        self.assertIn("f:C1O2", C._species_keys("CarbonDioxide", "CO2"))
        self.assertEqual(C._species_keys("MEA"), {"n:mea"})
        self.assertEqual(C._species_keys("Ammonium-Sulfate"),
                         {"n:ammoniumsulfate"})
        self.assertEqual(C._species_keys(None), frozenset())

    def test_chemistry_claim(self):
        """_chemistry_claim over every kind: a reactor family -> 'reactor';
        a _BUILTIN_CHEMISTRY class at any confidence -> 'builtin'; a custom
        class located in the pinned package (1.0) -> 'class_code'; an exact
        core class -> 'none', or 'capable' for one taking optional
        reactions (MESHDistillation); an unpinned curated biorefinery class
        (SeedHoldTank) -> 'none'; an unknown unpinned custom class and a
        label-only claim -> by family (R7.1: 'capable' only for the HOSTING
        tank / distillation / staged column / extraction; 'none' pump /
        junction and, since R7.1, evaporator / crystallizer / solids
        handling / steam-injection heater; 'builtin' combustor / facility
        and, since R7.1, dryer); no family -> 'free'."""
        cases = (((None, "reactor", False, 0.5), "reactor"),
                 (("NRELBatchBioreactor", "batch_bioreactor", False, 0.75),
                  "reactor"),
                 (("DrumDryer", "dryer", True, 1.0), "builtin"),
                 (("AmmoniaAdditionTank", "tank", False, 0.75), "builtin"),
                 (("FooTank", "tank", False, 1.0), "class_code"),
                 (("StorageTank", "tank", True, 1.0), "none"),
                 (("MESHDistillation", "staged_column", True, 1.0),
                  "capable"),
                 (("SeedHoldTank", "tank", False, 0.75), "none"),
                 (("FooTank", "tank", False, 0.75), "capable"),
                 ((None, "tank", False, 0.5), "capable"),
                 ((None, "distillation", False, 0.5), "capable"),
                 ((None, "staged_column", False, 0.75), "capable"),
                 ((None, "extraction", False, 0.5), "capable"),
                 (("FooColumn", "distillation", False, 0.75), "capable"),
                 ((None, "dryer", False, 0.5), "builtin"),
                 (("FooDryer", "dryer", False, 0.75), "builtin"),
                 ((None, "evaporator", False, 0.5), "none"),
                 ((None, "crystallizer", False, 0.5), "none"),
                 ((None, "solids_handling", False, 0.5), "none"),
                 ((None, "steam_injection_heater", False, 0.5), "none"),
                 ((None, "pump", False, 0.5), "none"),
                 ((None, "junction", False, 0.5), "none"),
                 ((None, "combustor", False, 0.5), "builtin"),
                 ((None, "facility", False, 0.5), "builtin"),
                 ((None, None, False, 0.0), "free"))
        for args, expected in cases:
            self.assertEqual(C._chemistry_claim(*args), expected, args)

    def test_builtin_contradiction(self):
        """_builtin_contradiction against physics['changed']: a DrumDryer
        consuming CH4 and producing CO2 and Water -> None (its natural-gas
        firing); producing Ethanol -> 'implausible for DrumDryer: Ethanol
        produced beyond its built-in chemistry'; an AmineAbsorption
        consuming MEA and H2O -> None, producing CO2 -> implausible; a
        ProcessWaterCenter consuming anything -> None; a label-only
        combustor producing Ethanol -> 'implausible for a combustor claim';
        nothing changed, or no signature at all -> None."""
        def changed(produced=(), consumed=()):
            return {"changed": tuple({n: C._species_keys(n) for n in names}
                                     for names in (produced, consumed))}
        self.assertIsNone(C._builtin_contradiction(
            "DrumDryer", "dryer", changed(("CO2", "Water"), ("CH4",))))
        why = C._builtin_contradiction("DrumDryer", "dryer",
                                       changed(("CO2", "Ethanol")))
        self.assertIn("implausible for DrumDryer: Ethanol produced beyond "
                      "its built-in chemistry", why)
        self.assertIsNone(C._builtin_contradiction(
            "AmineAbsorption", "splitter", changed((), ("MEA", "H2O"))))
        self.assertIsNotNone(C._builtin_contradiction(
            "AmineAbsorption", "splitter", changed(("CO2",))))
        self.assertIsNone(C._builtin_contradiction(
            "ProcessWaterCenter", "facility",
            changed((), ("Ethanol", "Glucose", "Ash"))))
        self.assertIn("implausible for a combustor claim: Ethanol produced",
                      C._builtin_contradiction(None, "combustor",
                                               changed(("Ethanol",))))
        self.assertIsNone(C._builtin_contradiction("DrumDryer", "dryer", {}))
        self.assertIsNone(C._builtin_contradiction(None, "tank",
                                                   changed(("X",))))

    def test_species_conversion_and_no_conversion(self):
        """_species_conversion on r5_doc: the fermenter R1 -> (['CO2',
        'Ethanol'] produced, ['Glucose'] consumed: 40 -> 2 kmol/h, beyond
        1 % although it does not vanish); the pump P1 -> ([], []); a
        missing unit id -> ([], []); a port without phases or composition
        -> None. _no_conversion: P1 -> True (mass closes, every species
        balances), R1 -> False; deleting s1's total_mass_flow (P1's outlet)
        -> False (the evidence is gone)."""
        sc = ctx(r5_doc())
        self.assertEqual(C._species_conversion(sc, "R1"),
                         (["CO2", "Ethanol"], ["Glucose"]))
        self.assertEqual(C._species_conversion(sc, "P1"), ([], []))
        self.assertEqual(C._species_conversion(sc, None), ([], []))
        self.assertTrue(C._no_conversion(sc, 0))
        self.assertFalse(C._no_conversion(sc, 2))
        doc = r5_doc()
        sp = doc["streams"][1]["stream_properties"]
        del sp["phases"]
        sp.pop("composition", None)
        self.assertIsNone(C._species_conversion(ctx(doc), "P1"))
        doc = r5_doc()
        del doc["streams"][1]["stream_properties"]["total_mass_flow"]
        self.assertFalse(C._no_conversion(ctx(doc), 0))

    def test_physics_contradictions_r6_kinds(self):
        """_physics_contradictions (R6.1 kinds) on r5_doc: R1 carries
        'conversion' ('Glucose consumed beyond 1% without declared
        reactions': it neither appears nor vanishes) and 'changed' (CO2 and
        Ethanol produced, Glucose consumed, as identity keys); P1 carries
        neither and no 'mass' (its balance closes). With s3's mass flow
        doubled R1's total mass no longer closes: 'mass' appears and the
        species evidence of a balance is withheld ('conversion' / 'changed'
        absent: a missing port could explain it), while 'species'
        (appearing / vanishing) stays."""
        sc = ctx(r5_doc())
        physics = sc.unit_physics[2]
        self.assertEqual(physics["conversion"],
                         "Glucose consumed beyond 1% without declared "
                         "reactions")
        produced, consumed = physics["changed"]
        self.assertEqual(set(produced), {"CO2", "Ethanol"})
        self.assertEqual(set(consumed), {"Glucose"})
        self.assertIn("n:co2", produced["CO2"])
        for kind in ("conversion", "changed", "mass"):
            self.assertNotIn(kind, sc.unit_physics[0])
        doc = r5_doc()
        doc["streams"][3]["stream_properties"]["total_mass_flow"] *= 2
        physics = ctx(doc).unit_physics[2]
        self.assertIn("mass balance does not close", physics["mass"])
        self.assertNotIn("conversion", physics)
        self.assertNotIn("changed", physics)
        self.assertIn("species", physics)

    def test_physics_contradiction_r6_claims(self):
        """_physics_contradiction for R6.1 claims: an exact core Pump, an
        unpinned curated HPPump and a label-only exchanger are contradicted
        by 'species' or 'conversion' ('implausible for a unit that hosts no
        chemistry'); a custom pump class located in the pinned package
        (confidence 1) and a label-only tank are not; the exact Junction by
        'conversion', 'splitter' (T / P) and 'mass'; the Scaler by
        'species' and 'splitter' but not 'conversion' or 'mass' (it scales
        its inlet); a label-only junction by 'species' or a duty only; a
        _BUILTIN_CHEMISTRY class through its signature. _ports_plausible
        passes the class's confidence on."""
        species = {"species": "CO2 appears without declared reactions"}
        conversion = {"conversion": "Glucose consumed beyond 1% without "
                                    "declared reactions"}
        split = {"splitter": "outlet temperature 1 differs from the inlet"}
        mass = {"mass": "mass balance does not close (in 1, out 2)"}
        heat = {"heat": "records a non-zero heat duty (steam)"}
        for physics in (species, conversion):
            self.assertIn("implausible for a unit that hosts no chemistry",
                          C._physics_contradiction("Pump", "pump", physics))
            self.assertIsNotNone(C._physics_contradiction(
                "HPPump", "pump", physics, False, 0.75))
            self.assertIsNotNone(C._physics_contradiction(
                None, "hx_utility", physics, False))
            self.assertIsNone(C._physics_contradiction(
                "FooPump", "pump", physics, False, 1.0))
            self.assertIsNone(C._physics_contradiction(
                None, "tank", physics, False))
        for physics in (conversion, split, mass, heat):
            self.assertIn("implausible for a junction", C._physics_contradiction(
                "Junction", "junction", physics), physics)
        for physics, contradicts in ((species, True), (split, True),
                                     (conversion, False), (mass, False)):
            self.assertEqual(C._physics_contradiction(
                "Scaler", "junction", physics) is not None, contradicts,
                physics)
        for physics, contradicts in ((species, True), (heat, True),
                                     (conversion, False), (split, False)):
            self.assertEqual(C._physics_contradiction(
                None, "junction", physics, False) is not None, contradicts,
                physics)
        dryer = {"changed": ({"Ethanol": C._species_keys("Ethanol")}, {})}
        self.assertIn("implausible for DrumDryer",
                      C._physics_contradiction("DrumDryer", "dryer", dryer))
        self.assertIsNone(C._physics_contradiction("DrumDryer", "dryer",
                                                   species))
        self.assertFalse(C._ports_plausible("HPPump", "pump", False, (1, 1),
                                            species, 0.75))
        self.assertTrue(C._ports_plausible("FooPump", "pump", False, (1, 1),
                                           species, 1.0))

    def test_hosting_claim_and_chemistry_rules(self):
        """_hosting_claim / _no_conversion / _ruled_out / _chemistry_rules /
        _rules_score (R7.1) on the label-only 'Holding tank' P2 of
        r6_tank_doc, a HOSTING claim (the pump P1 is not). Balancing ports
        -> ruled out per unit: no reactor rules. Ports' phases deleted ->
        not ruled out per unit, but ruled out flowsheet-wide (sweep_doc's
        boundary streams close and R1's reaction explains every converted
        species): still no reactor rules, not reactive. The vent's phases
        deleted too (no flowsheet-level evidence) -> the three reactor
        variants (reactor, batch_bioreactor, aerated_bioreactor), each
        scoring 0.25 (tau stated, T not derivable: the tank's species flows
        are not computable), and _unit_spec_score is the LOWER of its own
        rules (1.0) and the best reactor variant (the first, 0.25);
        _reactive_reason stays None (the pseudo-member covers P2)."""
        sc = ctx(r6_tank_doc())
        self.assertEqual(sc.unit_classes[4], (None, "tank", 0.5))
        self.assertTrue(C._hosting_claim(sc, 4))
        self.assertFalse(C._hosting_claim(sc, 1))
        self.assertTrue(C._no_conversion(sc, 4))
        self.assertTrue(C._ruled_out(sc, 4))
        self.assertIsNone(C._chemistry_rules(sc, 4))
        self.assertEqual(C._rules_score(sc, 4, C._unit_rules(sc, 4)),
                         (1.0, []))
        sc = ctx(r6_tank_doc(balanced=False))
        self.assertFalse(C._no_conversion(sc, 4))
        self.assertEqual(C._flowsheet_explained(sc), (True, ""))
        self.assertTrue(C._ruled_out(sc, 4))
        self.assertIsNone(C._chemistry_rules(sc, 4))
        self.assertIsNone(C._reactive_reason(sc, 4))
        doc = r6_tank_doc(balanced=False)
        del doc["streams"][5]["stream_properties"]["phases"]  # the vent
        sc = ctx(doc)
        self.assertFalse(C._ruled_out(sc, 4))
        rules = C._chemistry_rules(sc, 4)
        self.assertEqual([f for f, _r in rules],
                         ["reactor", "batch_bioreactor", "aerated_bioreactor"])
        self.assertIs(rules[0][1], C._FAMILY_RULES["reactor"])
        self.assertIn(4, C._spec_population(sc))
        self.assertIsNone(C._reactive_reason(sc, 4))
        own = C._rules_score(sc, 4, C._unit_rules(sc, 4))
        variants = [C._rules_score(sc, 4, r, f) for f, r in rules]
        self.assertEqual(own[0], 1.0)
        self.assertEqual([v[0] for v in variants], [0.25, 0.25, 0.25])
        self.assertEqual(C._unit_spec_score(sc, 4), variants[0])

    def test_reaction_step_keywords(self):
        """R6.1(d): ammonia addition, (over)liming, conditioning,
        acidification, (trans)esterification and mashing name a reactor --
        before a vessel head too ('Mash tank') -- while a column head keeps
        its family ('Esterification column' a distillation column, R6.1(b)
        instead) and a cooler head its own ('Mash cooler')."""
        for text in ("Ammonia addition tank", "Conditioning tank",
                     "Overliming tank", "Liming tank", "Acidification tank",
                     "Mash tank", "Transesterification", "Ammonia addition"):
            self.assertEqual(C._keyword_family(text), "reactor", text)
        self.assertEqual(C._keyword_family("Esterification column"),
                         "distillation")
        self.assertEqual(C._keyword_family("Mash cooler"), "hx_utility")

    def test_relabelled_fermenter_never_pays_off(self):
        """R6.1 reviewer cases on r5_doc (the syn2 flowsheet): relabelling
        the fermenter R1 -- reactor-labelled R6_REACTOR scored 0.461 in the
        reviewer's environment -- as an exact Junction / Scaler / ConveyingBelt / DrumDryer /
        MolecularSieve / HXutility / StorageTank / IsenthalpicValve used to
        score 0.5284-0.5468, label-only 'Junction' / 'Conveyor' / 'Heater'
        0.5095-0.5297, and the honest labels of chemistry-hosting steps
        0.5095-0.5115. None now scores above the reactor-labelled version
        (overall or representable); every exact relabel is physically
        implausible (unknown family), and every relabelled R1 is in the
        CMP-RXN-01 population -- or, for the HOSTING 'Amine absorber' and
        'Esterification column' (R7.1), covered by the flowsheet-level
        pseudo-member (_unexplained_hosts == [2]: no reaction explains the
        ethanol and CO2 the flowsheet produces). (Chemical names resolve
        against no database here, name_lookup, so the scores do not depend
        on the environment.)"""
        with name_lookup(set()):
            reference = C.score_flowsheet_completeness(r6_doc(*R6_REACTOR))
        cases = ([("Unit", name) for name in R6_EXACT]
                 + [(label, None) for label in R6_LABELS + R6_HONEST_LABELS])
        for unit_type, class_name in cases:
            doc = r6_doc(unit_type, class_name)
            with name_lookup(set()):
                report = C.score_flowsheet_completeness(doc)
            sc = ctx(doc)
            with self.subTest(unit_type=unit_type, class_name=class_name):
                self.assertLessEqual(report.score, reference.score + 1e-12)
                self.assertLessEqual(report.representable_score,
                                     reference.representable_score + 1e-12)
                if C._hosting_claim(sc, 2):
                    self.assertEqual(C._unexplained_hosts(sc), [2])
                else:
                    self.assertIn(2, dict(C._reactive_units(sc)))
                if class_name:
                    self.assertEqual(sc.unit_classes[2], (None, None, 0.0))
                    self.assertIn("is physically implausible",
                                  C._class_note(sc, 2))

    def test_label_only_tank_sweeps(self):
        """Strict deletion sweeps (TestRevision4PartC.assert_sweep_clean)
        over r6_tank_doc: the label-only 'Holding tank' with balancing ports
        (ruled out per unit) and without them (ruled out flowsheet-wide,
        R7.1: deleting any of that evidence only adds the pseudo-member and
        the reactor rules). Neither raises the overall score or any
        criterion, nor turns one not-applicable."""
        TestRevision4PartC.assert_sweep_clean(self, r6_tank_doc())
        with name_lookup(set()):
            raised, lowered, n = deletion_violations(
                r6_tank_doc(balanced=False), skip=frozenset())
        self.assertGreater(n, 220)
        self.assertEqual(raised, [])
        self.assertGreater(lowered, n // 2)

    def test_endpoint_and_id_deletions_keep_guards(self):
        """Monotonicity gaps the label-only tank sweep exposed (present
        before R6): on sweep_doc('unit_type'), deleting s4's source_unit_id
        (R1's liquid outlet) used to make R1's ports all single-phase
        (CMP-RXN-04 phase credit rose) -- _single_phase_ports is now False
        while a stream without a stated endpoint may be a missing port
        (R5.8). On r6_tank_doc(balanced=False), deleting s5's sink_unit_id
        left the flash F1 without inlets, which lifted its species guard --
        _species_consistency now stays False ('may be a missing port');
        deleting F1's id made it an undeclared (phantom) unit whose outlets
        escaped the guard -- CMP-UOP-04 now keeps them unverified ('the
        unit is referenced but not declared'). None of the three raises
        CMP-RXN-04 / CMP-UOP-04."""
        doc = sweep_doc("unit_type")
        self.assertTrue(C._single_phase_ports(ctx(doc), 3))
        cut = deleted(doc, ("streams", 6, "source_unit_id"))
        self.assertFalse(C._single_phase_ports(ctx(cut), 3))
        self.assertLessEqual(run(C._score_rxn_04, cut)[0],
                             run(C._score_rxn_04, doc)[0])
        doc = r6_tank_doc(balanced=False)
        base = run(C._score_uop_04, doc)[0]
        cut = deleted(doc, ("streams", 7, "sink_unit_id"))
        ok, why = C._species_consistency(ctx(cut), 5)
        self.assertFalse(ok)
        self.assertIn("may be a missing port", why)
        self.assertLessEqual(run(C._score_uop_04, cut)[0], base)
        cut = deleted(doc, ("units", 5, "id"))
        score, _message, missing = run(C._score_uop_04, cut)
        self.assertLessEqual(score, base)
        self.assertTrue(any("the unit is referenced but not declared" in m
                            for m in missing))

    # ---- R6.2 reaction mass conservation --------------------------------

    def test_closure_error_and_conserves_mass(self):
        """_coefficient_sources / _closure_error / _conserves_mass (R6.2)
        against rxn_doc's chemicals: 'Glucose -> 2 Ethanol + 2 CO2' closes
        (error < 1e-4, the molar masses rounded); the reviewer's 'Glucose -> 5 Ethanol' is off by
        28 % on a mol basis and 400 % on a wt basis -> not conserving;
        'Glucose -> Ethanol' closes on a wt basis (sum nu = 0); a species
        without a molar mass leaves the reaction untested (None, conserving);
        a reaction with a closing stoichiometry beside a non-closing
        equation has two sources and conserves (the smaller error);
        with the closing source alone broken it does not."""
        sc = ctx(rxn_doc(None))
        self.assertLess(C._closure_error(sc, {"equation": GLUCOSE_EQ}), 1e-4)
        bad = {"equation": "Glucose -> 5 Ethanol"}
        self.assertAlmostEqual(C._closure_error(sc, bad), 0.2786, places=3)
        self.assertFalse(C._conserves_mass(sc, bad))
        self.assertTrue(C._conserves_mass(sc, {"equation": "Glucose -> "
                                                           "Ethanol"}))
        self.assertIsNone(C._closure_error(sc, {"equation": "Glucose -> Q"}))
        self.assertTrue(C._conserves_mass(sc, {"equation": "Glucose -> Q"}))
        both = {"equation": "Glucose -> 5 Ethanol",
                "stoichiometry": {"Glucose": -1, "Ethanol": 2, "CO2": 2}}
        self.assertEqual(len(C._coefficient_sources(sc, both)), 2)
        self.assertTrue(C._conserves_mass(sc, both))
        broken = dict(both, stoichiometry={"Glucose": -1, "Ethanol": 2})
        self.assertFalse(C._conserves_mass(sc, broken))
        self.assertEqual(C._coefficient_sources(sc, {}), [])

    def test_non_conserving_reaction_is_not_well_formed(self):
        """R6.2 through the criteria: sc.well_formed rejects 'Glucose -> 5
        Ethanol' (cached per reaction object), so a reactor carrying only
        it does not carry reactions (unit_reacts False, CMP-RXN-01 0) and
        CMP-RXN-02 scores it 0 with the _NONCONSERVING_REACTION locator;
        _malformed_note -> '' for a well-formed reaction, the
        no-species note for 'x'."""
        doc = rxn_doc([reaction(equation="Glucose -> 5 Ethanol",
                                reactant="Glucose")])
        sc = ctx(doc)
        r = sc.units[0]["reactions"][0]
        self.assertFalse(sc.well_formed(r))
        self.assertIs(sc._well_formed[id(r)][0], r)
        self.assertEqual(sc.unit_reacts, [False])
        self.assertEqual(run(C._score_rxn_01, doc)[0], 0.0)
        score, _m, missing = run(C._score_rxn_02, doc)
        self.assertEqual(score, 0.0)
        self.assertIn(C._NONCONSERVING_REACTION, missing[0])
        self.assertEqual(C._malformed_note(sc, r), C._NONCONSERVING_REACTION)
        self.assertEqual(C._malformed_note(sc, {"equation": GLUCOSE_EQ}), "")
        self.assertEqual(C._malformed_note(sc, {"equation": "x"}),
                         C._MALFORMED_REACTION)

    def test_broken_closure_sweep(self):
        """The fermenter R1 of sweep_doc('unit_type') stating its reaction by
        stoichiometry only ({Glucose: -1, Ethanol: 2, CO2: 2}): deleting the
        CO2 coefficient breaks the closure (R6.2: not well-formed, the
        overall score falls), deleting Glucose leaves nothing consumed; the
        strict deletion sweep over the document raises nothing."""
        doc = sweep_doc("unit_type")
        reaction_ = doc["units"][3]["reactions"][0]
        del reaction_["equation"]
        reaction_["stoichiometry"] = {"Glucose": -1, "Ethanol": 2, "CO2": 2}
        sc = ctx(doc)
        self.assertEqual(sc.unit_reacts[3], True)
        path = ("units", 3, "reactions", 0, "stoichiometry", "CO2")
        cut = deleted(doc, path)
        self.assertFalse(ctx(cut).unit_reacts[3])
        self.assertLess(overall(cut), overall(doc))
        with name_lookup(set()):
            raised, lowered, n = deletion_violations(doc, skip=frozenset())
        self.assertGreater(n, 240)
        self.assertEqual(raised, [])
        self.assertGreater(lowered, n // 2)


# ------------------------------------------ Revision 6, part B (R6.3-R6.9)

def _empty_sp(T=300.0, P=101325.0):
    """Stream properties of a stated-empty stream (zero mass and molar
    flow, an empty liquid phase) at T / P."""
    return {"temperature": T, "pressure": P, "total_mass_flow": 0.0,
            "total_molar_flow": 0.0,
            "phases": {"l": {"total_molar_flow": 0.0, "composition": []}}}


def r6_pad_doc(n, sink="P1"):
    """r5_doc with its feed degraded (no phases, no temperature, no price)
    plus `n` stated-empty feeds into `sink` (the review's padding case,
    probes b15 / b2)."""
    doc = r5_doc()
    sp = doc["streams"][0]["stream_properties"]
    del sp["phases"], sp["temperature"]
    doc["streams"] += [{"id": f"pad{k}", "source_unit_id": "None",
                        "sink_unit_id": sink,
                        "stream_properties": _empty_sp(25.0, 1.0)}
                       for k in range(n)]
    return doc


CHILLED_WATER = {"id": "chilled_water", "temperature": 280.15,
                 "pressure": 101325.0, "regeneration_price": 1e-4,
                 "heat_transfer_efficiency": 1.0, "temperature_limit": 300.15,
                 "composition": [{"phase": "l", "component_name": "Water",
                                  "mol_fraction": 1.0}]}


def r6b_sweep_doc():
    """sweep_doc('design_simulation_method') with feedB routed through an
    exact HXprocess X1 (x_out -> M1) whose second side is a pair of
    stated-empty placeholder streams -- hot_in (a feed into X1's second
    fixed inlet) and hot_out (a product from its second fixed outlet),
    R6.3 -- and a fully specified heat agent 'chilled_water' that no unit
    references (R6.4)."""
    doc = sweep_doc("design_simulation_method")
    doc["units"].append({"id": "X1",
                         "design_simulation_method": f"HXprocess on {BST_URL}"})
    next(s for s in doc["streams"] if s["id"] == "feedB")["sink_unit_id"] = "X1"
    doc["streams"] += [
        {"id": "x_out", "source_unit_id": "X1", "sink_unit_id": "M1",
         "stream_properties": _sweep_sp({"Water": 50.0}, "l")},
        {"id": "hot_in", "source_unit_id": "None", "sink_unit_id": "X1",
         "stream_properties": _empty_sp()},
        {"id": "hot_out", "source_unit_id": "X1", "sink_unit_id": "None",
         "stream_properties": _empty_sp()}]
    doc["utilities"]["heat_utilities"].append(dict(CHILLED_WATER))
    return doc


class TestRevision6PartB(unittest.TestCase):
    """Revision 6, R6.3 - R6.9: the placeholder-port cap, unreferenced
    utility agents, zero prices into unresolved units, gauge units and the
    alias hint, volumetric-only flows and unverified names."""

    def test_placeholder_cap_padding(self):
        """R6.3, probes b15 / b2: 30 stated-empty feeds padded onto r5_doc's
        exact Pump P1 (one fixed inlet, already taken by the degraded feed)
        earn nothing -- CMP-FEED-01 (0.5) and CMP-FEED-02 (0) are unchanged
        (they rose to 0.984 / 0.909) and the overall score does not rise;
        the same 20 pads onto an exact Mixer M0 put in front of P1 (a
        variable inlet count) earn nothing either."""
        def mixer_front(doc):
            doc["units"].insert(0, dict(spec_unit("Mixer"), id="M0"))
            doc["streams"][0]["sink_unit_id"] = "M0"
            doc["streams"].append({
                "id": "m_out", "source_unit_id": "M0", "sink_unit_id": "P1",
                "stream_properties": copy.deepcopy(
                    doc["streams"][1]["stream_properties"])})
            return doc

        for n, sink in ((30, "P1"), (20, "M0")):
            wrap = mixer_front if sink == "M0" else (lambda doc: doc)
            base = criterion_results(wrap(r6_pad_doc(0)))
            after = criterion_results(wrap(r6_pad_doc(n, sink)))
            with self.subTest(sink=sink):
                for cid in ("CMP-FEED-01", "CMP-FEED-02"):
                    self.assertLessEqual(after[cid].score,
                                         base[cid].score + 1e-12, cid)
                self.assertIn(f"{n} empty feed(s) (zero flow), 0 credited",
                              after["CMP-FEED-01"].message)
        doc = r6_pad_doc(30)
        self.assertLessEqual(overall(doc), overall(r6_pad_doc(0)) + 1e-12)
        self.assertEqual(run(C._score_feed_01, doc)[0], 0.5)
        self.assertEqual(run(C._score_feed_02, doc)[0], 0.0)

    def test_r6b_placeholders_and_unreferenced_agent(self):
        """r6b_sweep_doc: X1's hot_in and hot_out are credited as
        placeholder ports (CMP-FEED-01 '1 empty feed(s) (zero flow), 1
        credited'; CMP-FEED-02 '2 empty placeholder port(s)'); the
        unreferenced chilled_water agent changes no CMP-UTIL-01/02/03 score
        (R6.4; the plain mean would raise CMP-UTIL-02 from 0.5 to 2/3 and
        CMP-UTIL-03 from 0.5 to 0.75), each message noting '1 agent(s) no
        unit references can only lower the score'; a half-specified
        unreferenced agent lowers CMP-UTIL-01. The STRICT deletion sweep
        over every field (ids, endpoints, classes included) raises neither
        the overall score nor any criterion."""
        doc = r6b_sweep_doc()
        feed01 = run(C._score_feed_01, doc)[1]
        self.assertIn("1 empty feed(s) (zero flow), 1 credited", feed01)
        self.assertIn("2 empty placeholder port(s)",
                      run(C._score_feed_02, doc)[1])
        plain = copy.deepcopy(doc)
        plain["utilities"]["heat_utilities"].pop()
        for scorer in (C._score_util_01, C._score_util_02, C._score_util_03):
            score, message, _m = run(scorer, doc)
            self.assertEqual(score, run(scorer, plain)[0], scorer.__name__)
            self.assertIn("1 agent(s) no unit references can only lower the "
                          "score", message)
        self.assertEqual(run(C._score_util_02, doc)[0], 0.5)
        self.assertEqual(run(C._score_util_03, doc)[0], 0.5)
        half = copy.deepcopy(doc)
        del half["utilities"]["heat_utilities"][1]["temperature"]
        self.assertLess(run(C._score_util_01, half)[0],
                        run(C._score_util_01, doc)[0])
        with name_lookup(set()):
            raised, lowered, n = deletion_violations(doc, skip=frozenset())
        self.assertGreater(n, 280)
        self.assertEqual(raised, [])
        self.assertGreater(lowered, n // 2)

    def test_agent_mean(self):
        """_agent_mean / _agent_referenced / _unreferenced_note (R6.4): with
        referenced entries, the lowest mean over nested sets
        (_referenced_mean: an unreferenced 1 beside a referenced 0.5 ->
        0.5, an unreferenced 0 -> 0.25); with none referenced, the lowest
        single score ([1, 0.5] -> 0.5, never the plain mean 0.75); empty ->
        None. _agent_referenced: a string id among the refs only (a list id
        -> False). The note counts the unreferenced entries, '' for none."""
        self.assertEqual(C._agent_mean([(0.5, True), (1.0, False)]), 0.5)
        self.assertEqual(C._agent_mean([(0.5, True), (0.0, False)]), 0.25)
        self.assertEqual(C._agent_mean([(1.0, False), (0.5, False)]), 0.5)
        self.assertIsNone(C._agent_mean([]))
        refs = {"steam": "units[H1]"}
        self.assertTrue(C._agent_referenced({"id": "steam"}, refs))
        self.assertFalse(C._agent_referenced({"id": "cw"}, refs))
        self.assertFalse(C._agent_referenced({"id": ["steam"]}, refs))
        self.assertEqual(C._unreferenced_note([(1.0, True)]), "")
        self.assertIn("2 agent(s) no unit references",
                      C._unreferenced_note([(1.0, False), (0.0, False)]))

    def test_zero_price_into_unresolved_sink(self):
        """R6.5, probe del2: _unresolved_sink describes a feed's sink that
        no unit declares ('(referenced, not declared)') or of unknown family
        ('(unknown family)'), None for a known family; _feed_02_entry: a 0
        price there -> 0.5 with a 'may be a utility port' locator, into an
        exact Pump -> 1, unpriced -> 0, a nonzero price -> 1. On SF_BST_01
        deleting the drum dryer D610's id no longer raises CMP-FEED-02 (its
        natural-gas feed kept half credit, its air feed now earns half)."""
        doc = one_unit_doc(spec_unit("Pump"))
        doc["streams"][0]["price"] = 0.0
        sc = ctx(doc)
        self.assertIsNone(C._unresolved_sink(sc, 0))
        self.assertEqual(C._feed_02_entry(sc, 0, True), (1.0, None))
        widget = one_unit_doc({"unit_type": "Widget"})
        widget["streams"][0]["price"] = 0.0
        sc = ctx(widget)
        self.assertEqual(C._unresolved_sink(sc, 0), "units[U1] (unknown family)")
        score, note = C._feed_02_entry(sc, 0, True)
        self.assertEqual(score, 0.5)
        self.assertIn("may be a utility port BioSTEAM prices itself", note)
        self.assertEqual(C._feed_02_entry(sc, 0, False), (1.0, None))
        widget["streams"][0]["sink_unit_id"] = "Ghost"
        sc = ctx(widget)
        self.assertEqual(C._unresolved_sink(sc, 0),
                         "units[Ghost] (referenced, not declared)")
        self.assertEqual(C._feed_02_entry(sc, 0, True)[0], 0.5)
        del widget["streams"][0]["price"]
        self.assertEqual(C._feed_02_entry(ctx(widget), 0, True),
                         (0.0, "streams[in0].price"))
        widget["streams"][0]["price"] = 2.0
        self.assertEqual(C._feed_02_entry(ctx(widget), 0, True), (1.0, None))
        path = CORPUS_DIR / "SF_BST_01.json"
        corn = json.loads(path.read_text(encoding="utf-8"))
        d610 = next(i for i, u in enumerate(corn["units"]) if u["id"] == "D610")
        before = run(C._score_feed_02, corn)[0]
        self.assertLess(run(C._score_feed_02,
                            deleted(corn, ("units", d610, "id")))[0], before)

    def test_gauge_units_and_alias_hint(self):
        """R6.6: gauge units read relative to 1 atm -- 10 psig -> 10 x 6894.76
        + 101325 Pa, 1 barg -> 201325 Pa, 0 kPa(g) -> 101325 Pa; bara reads
        absolute -- while a pressure DIFFERENCE (dP_design, dimension
        'pressure_difference') drops the offset (5 psig of rise -> 5 psi);
        _spec_unit_recognised accepts barg for P and psig for dP_design. A
        barg twin of r5_doc (every pressure restated as gauge) scores every
        criterion within 1e-9 of the original. A guard-tripping spec with no
        alias names the alias remedy; with an alias it does not."""
        index = {"pressure": "psig", "P": "barg", "dP_design": "psig",
                 "q": "kPa(g)", "a": "bara"}
        self.assertAlmostEqual(C._to_internal(10.0, "pressure", index),
                               10 * 6894.757293168 + 101325.0)
        self.assertAlmostEqual(C._to_internal(1.0, "P", index, "pressure"),
                               201325.0)
        self.assertAlmostEqual(C._to_internal(0.0, "q", index, "pressure"),
                               101325.0)
        self.assertAlmostEqual(C._to_internal(2.0, "a", index, "pressure"),
                               2e5)
        self.assertEqual(C._spec_dimension("dP_design"), "pressure_difference")
        self.assertAlmostEqual(
            C._to_internal(5.0, "dP_design", index, "pressure_difference"),
            5 * 6894.757293168)
        self.assertTrue(C._spec_unit_recognised("P", "barg"))
        self.assertTrue(C._spec_unit_recognised("dP_design", "psig"))
        doc = r5_doc()
        doc["units"][0]["design_input_specs"] = {"P": 3.0}
        twin = copy.deepcopy(doc)
        twin["quantity_units_global"]["pressure"]["quantity_units"] = "barg"
        holders = [s["stream_properties"] for s in twin["streams"]]
        for holder in holders + [twin["units"][0]["design_input_specs"]]:
            for key in ("pressure", "P"):
                if key in holder:
                    holder[key] -= 1.01325
        plain, gauge = criterion_results(doc), criterion_results(twin)
        for cid, result in plain.items():
            with self.subTest(cid=cid):
                if result.score is None:
                    self.assertIsNone(gauge[cid].score)
                else:
                    self.assertAlmostEqual(gauge[cid].score, result.score,
                                           delta=1e-9)
        bare = copy.deepcopy(doc)
        bare["quantity_units_global"]["pressure"]["aliases"] = ["pressure"]
        text = "\n".join(run(C._score_uop_02, bare)[2])
        self.assertIn("P states no quantity unit", text)
        self.assertIn("declare a quantity_units_global alias", text)
        bare["units"][0]["design_input_specs"]["P"] = 5.0
        bare["quantity_units_global"]["pressure"]["aliases"] = ["pressure", "P"]
        self.assertNotIn("declare a quantity_units_global alias",
                         "\n".join(run(C._score_uop_02, bare)[2]))

    def test_volumetric_only_flow(self):
        """R6.7: _volumetric_flow_credit -> DERIVED_CREDIT for a valid,
        non-zero total_volumetric_flow with valid T, P and a composition
        fixing the density (per-phase fractions without phase flows count)
        on a stream stating no mass or molar total; 0 without T, with a
        negative or zero volume flow, without a composition, or beside a
        stated total (a stated zero included: an empty stream, so zero-flow
        pads earn nothing from it). A Pump doc whose
        feed and product state only a volumetric flow (and their phase
        molar flows, so the pump's species balance stays checkable):
        _feed_01_entry
        scores the feed (1 + 1 + 0.75 + 1) / 4 with an 'only derivable
        from total_volumetric_flow' locator, CMP-FEED-01 and CMP-UOP-04
        likewise; restoring the mass flow gives 1 (MAX over tiers);
        deleting the volumetric flow or the temperature only lowers it."""
        doc = one_unit_doc(spec_unit("Pump"))
        for s in doc["streams"]:
            sp = s["stream_properties"]
            del sp["total_mass_flow"], sp["total_molar_flow"]
            sp["total_volumetric_flow"] = 0.06
        stream_ = doc["streams"][0]
        self.assertEqual(C._volumetric_flow_credit(stream_), C.DERIVED_CREDIT)
        fractions_only = copy.deepcopy(stream_)
        del fractions_only["stream_properties"]["phases"]["l"][
            "total_molar_flow"]
        self.assertEqual(C._volumetric_flow_credit(fractions_only),
                         C.DERIVED_CREDIT)
        for key, value in (("temperature", None), ("phases", None),
                           ("total_volumetric_flow", -1.0),
                           ("total_volumetric_flow", 0.0),
                           ("total_mass_flow", 0.0)):
            bad = copy.deepcopy(stream_)
            if value is None:
                del bad["stream_properties"][key]
            else:
                bad["stream_properties"][key] = value
            self.assertEqual(C._volumetric_flow_credit(bad), 0.0, key)
        expected = (3 + C.DERIVED_CREDIT) / 4
        score, full, missing = C._feed_01_entry(ctx(doc), 0)
        self.assertEqual((score, full), (expected, False))
        self.assertIn("(only derivable from total_volumetric_flow)", missing[0])
        self.assertEqual(run(C._score_feed_01, doc)[0], expected)
        self.assertEqual(run(C._score_uop_04, doc)[0], expected)
        restored = copy.deepcopy(doc)
        restored["streams"][0]["stream_properties"]["total_mass_flow"] = 46.07
        self.assertEqual(C._feed_01_entry(ctx(restored), 0)[0], 1.0)
        for key in ("total_volumetric_flow", "temperature"):
            self.assertLess(run(C._score_feed_01, deleted(
                doc, ("streams", 0, "stream_properties", key)))[0], expected)

    def test_schema_gap_docs_entries(self):
        """R6.5 / R6.9 docs: the completeness page lists the '<Class> on
        <source URL>' design_simulation_method convention among the schema
        gaps and notes that included_in_thermo flags contradicted by the
        stream data put a document outside the deletion guarantee."""
        page = DOCS_PAGE.read_text(encoding="utf-8")
        for phrase in ("<Class> on <source URL>",
                       "flags contradicted by the stream data"):
            self.assertTrue(phrase in page, phrase)


# ------------------------------------------ Revision 7 (R7.1 / R7.2) tests

# A MESH column's essential keys (MESHDistillation: stages, feed stages,
# reflux, boilup; P).
R7_MESH = {"N_stages": 10, "feed_stages": [5], "reflux": 2.0, "boilup": 1.5,
           "P": 101325.0}
# A BinaryDistillation's essential keys.
R7_BINARY = {"LHK": ["Ethanol", "Water"], "P": 101325.0, "Lr": 0.95,
             "Hr": 0.99, "k": 1.25, "partial_condenser": False}
R7_REACTION = {"index": 0, "equation": "Glucose -> 2 Ethanol + 2 CO2",
               "reactant": "Glucose", "conversion": 0.95}
# The reviewer's R7.2 honest column labels (a MESH column under each).
R7_COLUMN_LABELS = ("Distillation column", "Beer column",
                    "Rectification column")
# R7 reviewer relabels of the r5_doc fermenter R1 (R6.1 lists above too).
R7_RELABELS = ("Holding tank", "Beer column", "Conveyor", "Evaporator",
               "MESH column", "Stripping column", "Extractor", "Crystallizer",
               "Jet cooker", "Belt dryer")


def r7_sweep_doc(evidence=True):
    """sweep_doc('unit_type') with the transfer pump P2 relabelled a
    label-only 'Holding tank' (tau) and the flash F1 a label-only 'Beer
    column' stating a MESH column's keys (R7_MESH: a distillation claim
    whose key set infers the staged-column variant, R7.2). Both are
    HOSTING claims whose ports s4 / s5 lose their phases (species flows
    not computable: neither is ruled out per unit, R7.1). `evidence`: the
    boundary streams keep their compositions (flowsheet-level evidence --
    R1's reaction explains every converted species -- rules both out);
    else the vent loses its phases too, so the pseudo-member appears."""
    doc = sweep_doc("unit_type")
    doc["units"][4]["unit_type"] = "Holding tank"
    doc["units"][4]["design_input_specs"] = {"tau": 2.0}
    doc["units"][5]["unit_type"] = "Beer column"
    doc["units"][5]["design_input_specs"] = dict(R7_MESH)
    for position in (6, 7):  # s4, s5
        del doc["streams"][position]["stream_properties"]["phases"]
    if not evidence:
        del doc["streams"][5]["stream_properties"]["phases"]  # the vent
    return doc


def _r7_full(sid, source, sink, T, flows):
    """A liquid stream of {component: kg/h} `flows` at T K and 1 atm with
    mass fractions (the mass basis, R5.3)."""
    mass = sum(flows.values())
    return {"id": sid, "source_unit_id": source, "sink_unit_id": sink,
            "stream_properties": {
                "temperature": T, "pressure": 101325.0,
                "total_mass_flow": mass,
                "phases": {"l": {"total_mass_flow": mass, "composition": [
                    {"component_name": n, "mass_fraction": f / mass}
                    for n, f in flows.items()]}}}}


def _r7_bare(sid, source, sink, mass, T):
    """An internal stream stating only its mass flow, T and P (species
    flows not computable)."""
    return {"id": sid, "source_unit_id": source, "sink_unit_id": sink,
            "stream_properties": {"total_mass_flow": mass, "temperature": T,
                                  "pressure": 101325.0}}


def r7_sparse_doc(vent_composition=True):
    """The R7 reviewer's honest 7-unit sparse flowsheet (kg/h, K, Pa): a
    'Slurry tank' T1 -> 'Mash heater' H1 -> 'Fermenter' R1 (tau, V_wf,
    batch, one well-formed reaction; a CO2 vent) -> 'Beer storage tank'
    T2 -> 'Beer column' D1 -> 'Rectifier' D2 (binary-distillation keys)
    -> 'Molecular sieve' S1. The feed and every product state their
    composition; internal streams only mass flow, T and P, so no hosting
    unit (T1, T2, D1, D2) is ruled out per unit. `vent_composition`: the
    vent states its composition too (flowsheet-level evidence present:
    the mass balance closes and R1's reaction explains glucose, ethanol
    and CO2); else it states only its flow."""
    binary = dict(R7_BINARY, Lr=0.99, Hr=0.99, k=1.2)
    specs = (("T1", "Slurry tank", {"tau": 4}),
             ("H1", "Mash heater", {"T": 305.15}),
             ("R1", "Fermenter", {"T": 305.15, "tau": 36, "V_wf": 0.9,
                                  "batch": False}),
             ("T2", "Beer storage tank", {"tau": 8}),
             ("D1", "Beer column", dict(binary)),
             ("D2", "Rectifier", dict(binary)),
             ("S1", "Molecular sieve", {"split": {"Ethanol": 0.995,
                                                  "Water": 0.02}}))
    units = [{"id": uid, "unit_type": label, "design_input_specs": spec}
             for uid, label, spec in specs]
    units[2]["reactions"] = [dict(R7_REACTION)]
    streams = [
        _r7_full("feed", "None", "T1", 298.15,
                 {"Water": 28800.0, "Glucose": 7200.0}),
        _r7_bare("s1", "T1", "H1", 36000.0, 298.15),
        _r7_bare("s2", "H1", "R1", 36000.0, 305.15),
        _r7_bare("s3", "R1", "T2", 32660.0, 305.15),
        (_r7_full("vent", "R1", "None", 305.15,
                  {"CO2": 3300.0, "Ethanol": 40.0}) if vent_composition
         else _r7_bare("vent", "R1", "None", 3340.0, 305.15)),
        _r7_bare("s4", "T2", "D1", 32660.0, 305.15),
        _r7_bare("s5", "D1", "D2", 4000.0, 365.0),
        _r7_full("stillage", "D1", "None", 373.0,
                 {"Water": 28300.0, "Glucose": 360.0}),
        _r7_bare("s6", "D2", "S1", 3600.0, 351.5),
        _r7_full("phlegm", "D2", "None", 373.0, {"Water": 400.0}),
        _r7_full("ethanol", "S1", "None", 351.5,
                 {"Ethanol": 3490.0, "Water": 10.0}),
        _r7_full("recyc", "S1", "None", 351.5,
                 {"Ethanol": 10.0, "Water": 90.0})]
    for stream_ in streams:
        stream_["price"] = {"feed": 0.02, "ethanol": 0.7}.get(stream_["id"],
                                                              0.0)
    qug = {"temperature": {"aliases": ["temperature", "T"],
                           "quantity_units": "K"},
           "pressure": {"aliases": ["pressure", "P"], "quantity_units": "Pa"},
           "mass_flow": {"aliases": ["total_mass_flow"],
                         "quantity_units": "kg/hr"},
           "time": {"aliases": ["tau"], "quantity_units": "hr"}}
    return {"metadata": {"sff_version": "0.2.1", "TEA_currency": "USD",
                         "TEA_year": 2018,
                         "products": [{"stream_id": "ethanol"}]},
            "quantity_units_global": qug, "units": units, "streams": streams,
            "chemicals": [{"id": c, "molar_mass": mw}
                          for c, mw in R5_MW.items()],
            "utilities": {}}


def r7_column_doc(unit_type, specs, class_name=None):
    """The R7.2 reviewer's one-column flowsheet: a feed (900 Water + 100
    Ethanol kmol/h, 360 K) into C1, a distillate and a bottoms product
    (every stream stating both bases); C1 labelled `unit_type` with
    design_input_specs `specs`, and 'class_name on <biosteam url>' only
    when `class_name` is given. K / Pa / kg/hr / kmol/hr declared."""
    mw = {"Water": 18.01528, "Ethanol": 46.06844}

    def one(sid, source, sink, T, flows):
        n = sum(flows.values())
        m = sum(v * mw[c] for c, v in flows.items())
        return {"id": sid, "source_unit_id": source, "sink_unit_id": sink,
                "stream_properties": {
                    "temperature": T, "pressure": 101325.0,
                    "total_mass_flow": m, "total_molar_flow": n,
                    "phases": {"l": {
                        "total_molar_flow": n, "total_mass_flow": m,
                        "composition": [
                            {"component_name": c, "mol_fraction": v / n,
                             "mass_fraction": v * mw[c] / m}
                            for c, v in flows.items()]}}}}
    unit = {"id": "C1", "unit_type": unit_type,
            "design_input_specs": dict(specs)}
    if class_name:
        unit["design_simulation_method"] = f"{class_name} on {BST_URL}"
    streams = [one("feed", "None", "C1", 360.0,
                   {"Water": 900.0, "Ethanol": 100.0}),
               one("dist", "C1", "None", 351.5,
                   {"Water": 10.0, "Ethanol": 95.0}),
               one("bot", "C1", "None", 373.0,
                   {"Water": 890.0, "Ethanol": 5.0})]
    for stream_, price in zip(streams, (0.1, 0.8, 0.0)):
        stream_["price"] = price
    qug = {"temperature": {"aliases": ["temperature", "T"],
                           "quantity_units": "K"},
           "pressure": {"aliases": ["pressure", "P"], "quantity_units": "Pa"},
           "mass_flow": {"aliases": ["total_mass_flow"],
                         "quantity_units": "kg/hr"},
           "molar_flow": {"aliases": ["total_molar_flow"],
                          "quantity_units": "kmol/hr"}}
    return {"metadata": {"sff_version": "0.2.1", "TEA_currency": "USD",
                         "TEA_year": 2020,
                         "products": [{"stream_id": "dist"}]},
            "quantity_units_global": qug, "units": [unit],
            "streams": streams,
            "chemicals": [{"id": c, "molar_mass": m,
                           "formula": {"Water": "H2O",
                                       "Ethanol": "C2H6O"}[c]}
                          for c, m in mw.items()],
            "utilities": {}}


def r7_fermenter_doc(unit_type, specs):
    """r6_doc with the fermenter R1 a label-only `unit_type` stating
    design_input_specs `specs` (degC / h) and the well-formed reaction
    R7_REACTION (the rx.py reviewer case)."""
    doc = r6_doc(unit_type)
    doc["units"][2]["design_input_specs"] = dict(specs)
    doc["units"][2]["reactions"] = [dict(R7_REACTION)]
    return doc


class TestRevision7(unittest.TestCase):
    """Revision 7: hidden chemistry as ONE flowsheet-level pseudo-member
    with flowsheet evidence (R7.1: label-only tank / column / extractor
    claims keep their family and never enter the reactive population
    individually; the other label-only families cannot host chemistry;
    reactor rules reach only hosting tanks not ruled out) and label-only
    claims scored by their best legitimate rule-set variant (R7.2)."""

    # ---- R7.2 helpers --------------------------------------------------

    def test_rule_variant_families(self):
        """_rule_variant_families: 'distillation' -> (distillation,
        staged_column) and back; each reactor family -> itself first, then
        the other two of (reactor, batch_bioreactor, aerated_bioreactor);
        any other family (tank, equilibrium_reactor) or None -> itself
        alone."""
        self.assertEqual(C._rule_variant_families("distillation"),
                         ("distillation", "staged_column"))
        self.assertEqual(C._rule_variant_families("staged_column"),
                         ("staged_column", "distillation"))
        self.assertEqual(C._rule_variant_families("reactor"),
                         ("reactor", "batch_bioreactor",
                          "aerated_bioreactor"))
        self.assertEqual(C._rule_variant_families("batch_bioreactor"),
                         ("batch_bioreactor", "reactor",
                          "aerated_bioreactor"))
        for family in ("tank", "equilibrium_reactor", None):
            self.assertEqual(C._rule_variant_families(family), (family,))

    def test_rule_variants(self):
        """_rule_variants: a label-only 'Beer column' -> [(distillation,
        its family rules), (staged_column, ...)]; an exact core
        BinaryDistillation -> its own rules only; an unpinned named custom
        class (NRELBatchBioreactor, 0.75) -> its own rules only (its class
        knowledge describes that class); a class-level rule set (exact
        SteamMixer) -> itself; a unit of unknown family -> []."""
        sc = ctx(r7_column_doc("Beer column", R7_MESH))
        self.assertEqual(C._rule_variants(sc, 0),
                         [("distillation", C._FAMILY_RULES["distillation"]),
                          ("staged_column",
                           C._FAMILY_RULES["staged_column"])])
        sc = ctx(r7_column_doc("Unit", R7_BINARY, "BinaryDistillation"))
        self.assertEqual(sc.unit_classes[0][2], 1.0)
        self.assertEqual(C._rule_variants(sc, 0),
                         [("distillation", C._FAMILY_RULES["distillation"])])
        sc = ctx(unit_doc({"unit_type": "Fermenter",
                           "design_simulation_method":
                               f"NRELBatchBioreactor on {BIP_URL}"}))
        self.assertEqual(sc.unit_classes[0],
                         ("NRELBatchBioreactor", "batch_bioreactor", 0.75))
        self.assertEqual([f for f, _r in C._rule_variants(sc, 0)],
                         ["batch_bioreactor"])
        sc = ctx(unit_doc(cls("SteamMixer")))
        self.assertEqual(C._rule_variants(sc, 0),
                         [("steam_injection_heater",
                           C._CLASS_RULES["SteamMixer"])])
        self.assertEqual(C._rule_variants(ctx(unit_doc(
            {"unit_type": "Widget"})), 0), [])

    def test_best_variant_and_family_predicates(self):
        """_best_variant: the MAX over [(family, rules), ...], the first on
        ties, empty rule sets skipped -- a 'Beer column' stating a MESH
        column's keys scores 1.0 by the staged-column rules although the
        distillation rules leave LHK, Lr, Hr and k unstated. _rules_score
        judges keys by the given family's predicates: k = 1.0 does not
        count for a distillation column (reflux factor above 1, R3.11)
        but does for another family."""
        sc = ctx(r7_column_doc("Beer column", R7_MESH))
        variants = C._rule_variants(sc, 0)
        own = C._rules_score(sc, 0, variants[0][1], "distillation")
        self.assertLess(own[0], 1.0)
        self.assertEqual(C._best_variant(sc, 0, variants), (1.0, []))
        self.assertEqual(C._best_variant(sc, 0, [("mixer", ())] + variants),
                         (1.0, []))
        tie = C._best_variant(sc, 0, [variants[0], variants[0]])
        self.assertEqual(tie, own)
        sc = ctx(r7_column_doc("Beer column", dict(R7_BINARY, k=1.0)))
        rules = C._FAMILY_RULES["distillation"]
        self.assertLess(C._rules_score(sc, 0, rules, "distillation")[0],
                        C._rules_score(sc, 0, rules, "staged_column")[0])

    def test_uop_05_credit(self):
        """_uop_05_credit over one rule set: the staged-column rules of the
        MESH-keyed 'Beer column' -> (1.0, []) (P declared in Pa, the other
        keys dimensionless); the distillation rules -> (0.0, [... 'one of
        LHK unstated', ...]) (base 0: no exact class). _uop_05_unit takes
        the better variant: 1.0."""
        sc = ctx(r7_column_doc("Beer column", R7_MESH))
        explicit = C._explicit_keys(sc.units[0])
        staged = C._uop_05_credit(sc, 0, C._FAMILY_RULES["staged_column"],
                                  "staged_column", 0.0, False, explicit)
        self.assertEqual(staged, (1.0, []))
        binary = C._uop_05_credit(sc, 0, C._FAMILY_RULES["distillation"],
                                  "distillation", 0.0, False, explicit)
        self.assertEqual(binary[0], 0.0)
        self.assertIn("units[C1].design_input_specs: one of LHK unstated",
                      binary[1])
        self.assertEqual(C._uop_05_unit(sc, 0, False), (1.0, []))

    def test_label_only_columns_score_like_a_mesh_column(self):
        """R7.2 reviewer case (col.py): a column stating {N_stages,
        feed_stages, reflux, boilup, P} labelled 'Distillation column' /
        'Beer column' / 'Rectification column' scores exactly like 'MESH
        column' (overall and representable, CMP-UOP-02 = 1): its MESH key
        set confirms the distillation claim as its staged-column variant
        (confidence 0.75, the class note saying so) and its specification
        is scored by the staged-column rules. Symmetrically, 'MESH column'
        stating a binary column's keys scores CMP-UOP-02 = 1."""
        with name_lookup(set()):
            reference = C.score_flowsheet_completeness(
                r7_column_doc("MESH column", R7_MESH))
            for label in R7_COLUMN_LABELS:
                doc = r7_column_doc(label, R7_MESH)
                report = C.score_flowsheet_completeness(doc)
                with self.subTest(label=label):
                    self.assertAlmostEqual(report.score, reference.score,
                                           places=12)
                    self.assertAlmostEqual(report.representable_score,
                                           reference.representable_score,
                                           places=12)
                    sc = ctx(doc)
                    self.assertEqual(sc.unit_classes[0],
                                     (None, "distillation", 0.75))
                    self.assertIn("confirmed as its 'staged_column' "
                                  "rule-set variant (R7.2)",
                                  C._class_note(sc, 0))
                    self.assertEqual(run(C._score_uop_02, doc)[0], 1.0)
            self.assertEqual(run(C._score_uop_02, r7_column_doc(
                "MESH column", R7_BINARY))[0], 1.0)

    def test_label_only_fermenter_batch_keys(self):
        """R7.2 reviewer case (rx.py): a label-only 'Fermenter' stating an
        NREL batch bioreactor's {T, tau, N} is not asked for V_wf or batch
        -- its CMP-UOP-02 locators name only tau_0 (a class default only
        for an exact class, so a label must state it): 0.75, above the
        stirred-tank rules' 0.5 alone -- and scores exactly like 'Batch
        fermenter' (the key set confirms neither claim, 0.5: a
        BatchCrystallizer takes T, tau and N too)."""
        batch = {"T": 80.0, "tau": 36, "N": 4}
        with name_lookup(set()):
            fermenter = C.score_flowsheet_completeness(
                r7_fermenter_doc("Fermenter", batch))
            labelled = C.score_flowsheet_completeness(
                r7_fermenter_doc("Batch fermenter", batch))
        self.assertAlmostEqual(fermenter.score, labelled.score, places=12)
        sc = ctx(r7_fermenter_doc("Fermenter", batch))
        self.assertEqual(sc.unit_classes[2], (None, "reactor", 0.5))
        score, missing = C._unit_spec_score(sc, 2)
        self.assertEqual(missing, ["units[R1].design_input_specs: one of "
                                   "tau_0"])
        self.assertEqual(score, 0.75)
        self.assertEqual(C._rules_score(sc, 2, C._FAMILY_RULES["reactor"])[0],
                         0.5)

    # ---- R7.1 ----------------------------------------------------------

    def test_flowsheet_explained(self):
        """_flowsheet_explained / _flowsheet_evidence (R7.1) on sweep_doc:
        (True, '') -- no dangling stream, every feed and product computable
        with its mass, the overall balance closing, and glucose, ethanol
        and CO2 all in R1's reaction; cached on the context. Evidence is
        absent when: the vent loses its phases ('species flows not
        computable ... for every boundary stream'); s1 loses its source
        ('streams[s1] states no endpoint'); R1's reaction is deleted ('CO2
        produced, Ethanol produced, Glucose consumed across the flowsheet
        without a declared well-formed reaction'); a product loses its
        mass flow ('states no valid total_mass_flow'); or feedB's mass
        flow is doubled ('the overall mass balance does not close')."""
        sc = ctx(sweep_doc())
        self.assertEqual(C._flowsheet_explained(sc), (True, ""))
        self.assertEqual(sc.flowsheet_evidence, (True, ""))
        self.assertEqual(C._flowsheet_evidence(sc), (True, ""))
        doc = sweep_doc()
        cases = (
            (("streams", 5, "stream_properties", "phases"),
             "species flows not computable (molar or mass basis) for every "
             "boundary stream"),
            (("streams", 2, "source_unit_id"),
             "streams[s1] states no endpoint"),
            (("units", 3, "reactions"),
             "CO2 produced, Ethanol produced, Glucose consumed across the "
             "flowsheet without a declared well-formed reaction"),
            (("streams", 8, "stream_properties", "total_mass_flow"),
             "a feed or product states no valid total_mass_flow"))
        for path, why in cases:
            explained, reason = C._flowsheet_explained(
                ctx(deleted(doc, path)))
            self.assertFalse(explained, path)
            self.assertIn(why, reason, path)
        heavy = sweep_doc()
        heavy["streams"][1]["stream_properties"]["total_mass_flow"] *= 2
        self.assertIn("the overall mass balance does not close",
                      C._flowsheet_explained(ctx(heavy))[1])

    def test_unexplained_hosts_and_hidden_chemistry(self):
        """_unexplained_hosts / _hidden_chemistry (R7.1) on r7_sweep_doc:
        with flowsheet evidence -> [] and None (the tank P2 and the column
        F1 are ruled out flowsheet-wide although not per unit); without
        (the vent's phases deleted) -> [4, 5], cached, and ONE locator
        'flowsheet: unexplained conversion possible among units[P2],
        units[F1] (... no flowsheet-level evidence: species flows not
        computable ...), so <what>'. A hosting unit declaring a well-formed
        reaction is no candidate; nor is one ruled out per unit
        (r6_tank_doc's balancing tank, the vent's phases deleted)."""
        sc = ctx(r7_sweep_doc())
        self.assertEqual(C._unexplained_hosts(sc), [])
        self.assertIsNone(C._hidden_chemistry(sc, "x"))
        sc = ctx(r7_sweep_doc(evidence=False))
        self.assertEqual(C._unexplained_hosts(sc), [4, 5])
        self.assertEqual(sc.hidden_hosts, [4, 5])
        locator = C._hidden_chemistry(sc, "its reactions are unknown")
        self.assertTrue(locator.startswith(
            "flowsheet: unexplained conversion possible among units[P2], "
            "units[F1] (tank, column or extractor claims"), locator)
        self.assertIn("no flowsheet-level evidence: species flows not "
                      "computable", locator)
        self.assertTrue(locator.endswith(", so its reactions are unknown"))
        doc = r7_sweep_doc(evidence=False)
        doc["units"][4]["reactions"] = [dict(SWEEP_REACTION)]
        self.assertEqual(C._unexplained_hosts(ctx(doc)), [5])
        doc = r6_tank_doc()
        del doc["streams"][5]["stream_properties"]["phases"]
        self.assertEqual(C._unexplained_hosts(ctx(doc)), [])

    def test_reaction_criteria_pseudo_member(self):
        """CMP-RXN-01..04 with the pseudo-member (R7.1): on r7_sweep_doc
        without evidence each population is R1 plus the pseudo-member
        scoring 0 -- each score half its value with evidence (RXN-01 1.0
        -> 0.5) -- each message noting the pseudo-member (_HIDDEN_NOTE)
        and each missing list carrying its locator. The hosting tank and
        column never enter individually: the reactive units stay {R1}."""
        names = ("_score_rxn_01", "_score_rxn_02", "_score_rxn_03",
                 "_score_rxn_04")
        with_evidence = {n: run(getattr(C, n), r7_sweep_doc()) for n in names}
        doc = r7_sweep_doc(evidence=False)
        for name in names:
            score, message, missing = run(getattr(C, name), doc)
            with self.subTest(name=name):
                self.assertAlmostEqual(score, with_evidence[name][0] / 2)
                self.assertNotIn(C._HIDDEN_NOTE, with_evidence[name][1])
                self.assertIn(C._HIDDEN_NOTE, message)
                self.assertTrue(any(m.startswith("flowsheet: unexplained "
                                                 "conversion possible")
                                    for m in missing))
        self.assertEqual(with_evidence["_score_rxn_01"][0], 1.0)
        self.assertEqual(dict(C._reactive_units(ctx(doc))),
                         {3: "reactor family"})

    def test_reactor_rules_reach_only_hosting_tanks(self):
        """R7.1 on r7_sparse_doc: with the vent's composition the honest
        flowsheet carries no pseudo-member and no unit is held to reactor
        rules (no V_wf / batch locator on the tanks or columns). With only
        the vent's flow stated the pseudo-member covers T1, T2, D1 and D2,
        and _chemistry_rules reaches the tanks T1 / T2 only -- never the
        columns D1 / D2 (they are specified as columns)."""
        sc = ctx(r7_sparse_doc())
        self.assertEqual(C._unexplained_hosts(sc), [])
        self.assertEqual([C._chemistry_rules(sc, p) for p in range(7)],
                         [None] * 7)
        missing = run(C._score_uop_02, r7_sparse_doc())[2]
        self.assertFalse([m for m in missing
                          if "V_wf" in m or "batch" in m], missing)
        sc = ctx(r7_sparse_doc(vent_composition=False))
        self.assertEqual([sc.units[p]["id"] for p in C._unexplained_hosts(sc)],
                         ["T1", "T2", "D1", "D2"])
        self.assertEqual([sc.units[p]["id"] for p in range(7)
                          if C._chemistry_rules(sc, p)], ["T1", "T2"])

    def test_honest_sparse_doc_scores(self):
        """R7 reviewer case (sparse4.py): the honest 7-unit sparse
        flowsheet scored 0.419 in the reviewer's environment with every
        hosting unit charged its own missing reactions and reactor rules
        (0.5315 with the suspicion switched off). It is no longer charged:
        CMP-RXN-01 / 02 / 03 = 1, CMP-UOP-02 = 6/7 and CMP-UOP-05 = 5/7
        (only H1's V and S1's moisture content are missing), which with
        the installed chemical database gives the reviewer's 0.5315. With
        no chemical name resolvable and its unit strings parseable
        (unit_registry: QU-01 fails on molar_mass / price, QU-02 passes,
        CMP-INT-04 = 0.5) the overall is 0.4827 in any process state.
        Without the vent's composition (no flowsheet-level evidence) it
        scores strictly less, with CMP-RXN-01 = 0.5 (R1 plus the
        pseudo-member)."""
        with name_lookup(set()), unit_registry():
            report = C.score_flowsheet_completeness(r7_sparse_doc())
            bare = C.score_flowsheet_completeness(
                r7_sparse_doc(vent_composition=False))
        by_id = {c.criterion_id: c.score for c in report.criteria}
        for cid in ("CMP-RXN-01", "CMP-RXN-02", "CMP-RXN-03"):
            self.assertEqual(by_id[cid], 1.0, cid)
        self.assertAlmostEqual(by_id["CMP-UOP-02"], 6 / 7)
        self.assertAlmostEqual(by_id["CMP-UOP-05"], 5 / 7)
        self.assertEqual(by_id["CMP-INT-04"], 0.5)
        self.assertAlmostEqual(report.score, 0.482663, places=6)
        self.assertLess(bare.score, report.score)
        self.assertEqual({c.criterion_id: c.score for c in bare.criteria}[
            "CMP-RXN-01"], 0.5)

    def test_non_hosting_label_claims(self):
        """R7.1: a label-only evaporator, crystallizer, conveyor, jet
        cooker or dryer claim across which ethanol and CO2 appear (r6_doc,
        the fermenter relabelled) is implausible -- the unit's family is
        unknown and it enters the reactive population. A dryer claim is
        contradicted only beyond its combustion (_physics_contradiction:
        CO2 and water produced, methane consumed -> None; ethanol produced
        -> 'implausible for a dryer claim'); an evaporator claim by any
        converted species ('implausible for a unit that hosts no
        chemistry'); a tank, column or extractor claim never."""
        for label in ("Evaporator", "Crystallizer", "Screw conveyor",
                      "Jet cooker", "Belt dryer"):
            sc = ctx(r6_doc(label))
            with self.subTest(label=label):
                self.assertEqual(sc.unit_classes[2], (None, None, 0.0))
                self.assertIn(2, dict(C._reactive_units(sc)))

        def changed(produced=(), consumed=()):
            return {"changed": tuple({n: C._species_keys(n) for n in names}
                                     for names in (produced, consumed))}
        self.assertIsNone(C._physics_contradiction(
            None, "dryer", changed(("CO2", "Water"), ("CH4",))))
        self.assertIn("implausible for a dryer claim: Ethanol produced",
                      C._physics_contradiction(None, "dryer",
                                               changed(("Ethanol",))))
        species = {"species": "Ethanol appears without declared reactions"}
        self.assertIn("implausible for a unit that hosts no chemistry",
                      C._physics_contradiction(None, "evaporator", species,
                                               False))
        for family in ("tank", "distillation", "staged_column", "extraction"):
            self.assertIsNone(C._physics_contradiction(None, family, species,
                                                       False), family)

    def test_r7_relabels_never_pay_off(self):
        """R7 reviewer cases on r5_doc (the syn2 flowsheet): relabelling
        the fermenter R1 'Holding tank', 'Beer column', 'Conveyor',
        'Evaporator', 'MESH column', 'Stripping column', 'Extractor',
        'Crystallizer', 'Jet cooker' or 'Belt dryer' never scores above the
        reactor-labelled R6_REACTOR (overall or representable): a hosting
        claim meets the same pseudo-member and its stream-derived specs
        are blocked by the species-consistency guard (every hosting family
        is physical-only, R2.2); a non-hosting one is implausible."""
        with name_lookup(set()):
            reference = C.score_flowsheet_completeness(r6_doc(*R6_REACTOR))
            for label in R7_RELABELS:
                report = C.score_flowsheet_completeness(r6_doc(label))
                with self.subTest(label=label):
                    self.assertLessEqual(report.score,
                                         reference.score + 1e-12)
                    self.assertLessEqual(report.representable_score,
                                         reference.representable_score
                                         + 1e-12)

    def test_r7_sweeps(self):
        """Strict deletion sweeps (every value, ids and endpoints included)
        over r7_sweep_doc with and without flowsheet-level evidence (the
        pseudo-member absent / present; a label-only hosting tank and a
        'Beer column' stating MESH keys) and over r7_sparse_doc with and
        without the vent's composition: no deletion raises the overall
        score or any criterion, nor turns a scored one not-applicable, and
        most lower the score."""
        for doc in (r7_sweep_doc(), r7_sweep_doc(evidence=False),
                    r7_sparse_doc(), r7_sparse_doc(vent_composition=False)):
            with name_lookup(set()):
                raised, lowered, n = deletion_violations(doc,
                                                         skip=frozenset())
            self.assertGreater(n, 200)
            self.assertEqual(raised, [])
            self.assertGreater(lowered, n // 2)


if __name__ == "__main__":
    unittest.main()
