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
# biosteam stub) is never consulted for a verdict. The few end-to-end calls of
# score_flowsheet_completeness/main use documents WITHOUT any quantity-unit
# strings, so QU-02 has nothing to parse and never imports thermosteam; they
# assert report structure, never the validator verdict.

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


def cls(name):
    return {"design_simulation_method": f"{name} on https://example.org/units.py"}


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
        """_name_resolvable: a name the search resolves -> True; one it
        rejects (ValueError) -> False, cached so the search runs once per
        name; blank / non-string -> False; search unavailable (None) or
        raising anything -> False, never an exception."""
        calls = []

        def search(name):
            calls.append(name)
            if name == "Ethanol":
                return object()
            raise ValueError("not recognized")

        with name_lookup(search=search):
            self.assertTrue(C._name_resolvable("Ethanol"))
            self.assertFalse(C._name_resolvable("Fiber"))
            self.assertFalse(C._name_resolvable("Fiber"))
            self.assertEqual(calls, ["Ethanol", "Fiber"])
            self.assertFalse(C._name_resolvable(""))
            self.assertFalse(C._name_resolvable(None))
        with name_lookup(search=None):
            self.assertFalse(C._name_resolvable("Ethanol"))
        with name_lookup(search=lambda name: 1 / 0):
            self.assertFalse(C._name_resolvable("Ethanol"))

    def test_name_resolvable_real_database(self):
        """_name_resolvable with the installed `chemicals` package (skipped
        when it cannot be imported): 'Ethanol' and 'Water' resolve, the
        pseudo-component 'Fiber' does not."""
        with name_lookup(search=C._chemicals_search()):
            if C._chemicals_search() is None:
                self.skipTest("chemicals package not importable")
            self.assertTrue(C._name_resolvable("Ethanol"))
            self.assertTrue(C._name_resolvable("Water"))
            self.assertFalse(C._name_resolvable("Fiber"))


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
        """Not-representable (null) criteria are exactly the schema gaps
        (incl. UOP-05, design-spec units); COST-04 since 0.1.2
        (purchase_cost_correlations), SYS-04 since 0.0.6 (reproducibility
        packages); all others since 0.0.0."""
        since = {cid: e["representable_since"]
                 for cid, e in self.registry["criteria"].items()}
        self.assertEqual(
            {c for c, v in since.items() if v is None},
            {"CMP-THM-04", "CMP-THM-05", "CMP-TOP-02", "CMP-UOP-05",
             "CMP-RXN-04", "CMP-COST-05", "CMP-COST-06", "CMP-COST-07",
             "CMP-UTIL-05", "CMP-SYS-01", "CMP-SYS-02", "CMP-SYS-03"})
        uop5 = self.registry["criteria"]["CMP-UOP-05"]
        self.assertEqual((uop5["weight"], uop5["criticality"]),
                         (1, "supplementary"))
        self.assertEqual(since["CMP-COST-04"], "0.1.2")
        self.assertEqual(since["CMP-SYS-04"], "0.0.6")

    def test_revision_weights(self):
        """Revision 1 part B weights / criticality: THM-05 4, UTIL-03 2,
        SYS-01 3, SYS-02 5; new COST-07 and UTIL-05 weigh 1 (costing);
        CMP-INT-03 is gone, INT-01/02/04 keep 2/2/3."""
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

    def test_exclusions_listed(self):
        """exclusions -> EXC-01..03 (not_capturable), EXC-04 AgileSystem and
        EXC-05 LCA characterization factors (out_of_scope), DER-01..03
        (derived)."""
        kinds = {e["id"]: e["kind"] for e in self.registry["exclusions"]}
        self.assertEqual(kinds, {"EXC-01": "not_capturable",
                                 "EXC-02": "not_capturable",
                                 "EXC-03": "not_capturable",
                                 "EXC-04": "out_of_scope",
                                 "EXC-05": "out_of_scope",
                                 "DER-01": "derived", "DER-02": "derived",
                                 "DER-03": "derived"})
        titles = {e["id"]: e["title"] for e in self.registry["exclusions"]}
        self.assertIn("AgileSystem", titles["EXC-04"])
        self.assertIn("LCA", titles["EXC-05"])

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
        """_is_boundary: 'None', null, '' -> True; a unit id -> False.
        _label: id -> kind[id]; no id -> kind[#pos]."""
        for b in ("None", None, ""):
            self.assertTrue(C._is_boundary(b))
        self.assertFalse(C._is_boundary("U1"))
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
        'Cellulose' no); _valid_formula also accepts what the validator's
        parser reads. _valid_cas checks format and check digit; _smiles_like
        needs SMILES tokens, an atom, balanced branches and paired ring
        labels ('CCO' yes; 'x', 'HP', 'CO2' no); _valid_registry_id accepts
        CAS, InChI, InChIKey, SMILES and rejects 'x' and non-strings."""
        self.assertEqual(C._formula_counts("C2H6O"), {"C": 2, "H": 6, "O": 1})
        self.assertEqual(C._formula_counts("CH1.61O0.56"),
                         {"C": 1, "H": 1.61, "O": 0.56})
        for text in ("Water", "Ash", "Cellulose", "Ca(OH)2", "Xy2", None):
            self.assertIsNone(C._formula_counts(text), text)
        for cid in ("C2H6O", "HP", "CO2", "NaOH"):
            self.assertTrue(C._formula_like(cid), cid)
        for cid in ("Water", "Ethanol", "TriOlein", "Yeast"):
            self.assertFalse(C._formula_like(cid), cid)
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
        with name_lookup():
            for rid in ("64-17-5", "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3",
                        "LFQSCWFLJHTTHZ-UHFFFAOYSA-N", "CCO"):
                self.assertTrue(C._valid_registry_id({"registry_id": rid}), rid)
            for rid in ("x", "", ["64-17-5"], "64-17-4"):
                self.assertFalse(C._valid_registry_id({"registry_id": rid}), rid)

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
    if pinned:
        doc["metadata"]["reproducibility"] = {
            "flowsheet_model_package": {"commit": "abc123"}}
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
        'Fire water tank' -> facility; 'Mix tank' -> tank); unknown -> None."""
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
            "Flash tank": "flash", "Thermal oxidizer": "combustor",
            "Drum dryer": "dryer", "Gibbs equilibrium reactor":
            "equilibrium_reactor", "AeratedBioreactor": "aerated_bioreactor",
            "Anaerobic bioreactor": "reactor", "SSF": "batch_bioreactor",
            "BatchCoFermentation": "batch_bioreactor",
            "Fermentation": "reactor", "TALCrystallizer": "crystallizer",
            "Mock splitter": "junction", "HPPump": "pump", "AnMBR": "reactor",
            "CIPpackage": "facility", "Multi-effect evaporator": "evaporator",
            "Compressor": "compressor", "Valve": "valve",
            "Molecular sieve": "splitter", "Storage tank": "tank"}
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
        tanks); FAMILY_WEIGHTS covers exactly FAMILIES (reactor 3, hx_utility
        2, pump 1)."""
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
        self.assertEqual(set(C.FAMILY_WEIGHTS), families)
        self.assertEqual((C.FAMILY_WEIGHTS["reactor"],
                          C.FAMILY_WEIGHTS["hx_utility"],
                          C.FAMILY_WEIGHTS["pump"]), (3, 2, 1))

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
        """_is_core_class: a biosteam-repo URL makes any class core; a
        Bioindustrial-Park URL leaves a non-core name custom; a core-list name
        is core without a URL; empty -> False."""
        self.assertTrue(C._is_core_class("CornStorage", BST_URL))
        self.assertFalse(C._is_core_class("GrainHandling", BIP_URL))
        self.assertTrue(C._is_core_class("Pump"))
        self.assertFalse(C._is_core_class("JetCooker"))
        self.assertFalse(C._is_core_class("Foo", "https://github.com/"
                                          "BioSTEAMDevelopmentGroup/biosteamx"))
        self.assertFalse(C._is_core_class(None))

    def test_infer_class_from_specs(self):
        """_infer_class_from_specs: unique tightest superset -> (class,
        family); equally tight entries of one family -> (None, family);
        differing families, no superset, or no specs -> None."""
        reg = {"Valve": {"design_input_spec_params": {"P": {}}},
               "Pump": {"design_input_spec_params": {"P": {}, "dP_design": {}}},
               "StorageTank": {"design_input_spec_params": {"tau": {}}},
               "Tank": {"design_input_spec_params": {"tau": {}}},
               "Mixer": {"design_input_spec_params": {"rigorous": {}}},
               "HXutility": {"design_input_spec_params": {"rigorous": {}}}}
        unit = lambda specs: {"design_input_specs": specs}
        self.assertEqual(C._infer_class_from_specs(unit({"P": 1}), reg),
                         ("Valve", "valve"))
        self.assertEqual(C._infer_class_from_specs(unit({"dP_design": 1}), reg),
                         ("Pump", "pump"))
        self.assertEqual(C._infer_class_from_specs(unit({"tau": 1}), reg),
                         (None, "tank"))
        self.assertIsNone(C._infer_class_from_specs(unit({"rigorous": 1}), reg))
        self.assertIsNone(C._infer_class_from_specs(unit({"zz": 1}), reg))
        self.assertIsNone(C._infer_class_from_specs(unit({}), reg))

    def test_unit_class_paths(self):
        """_unit_class: core 'Class on <url>' -> 1.0; a custom class -> 0.75,
        or 1.0 when pinned; unit_type naming a known class -> 1.0; key-set
        inference -> (class, family, 0.75); keyword unit_type or a
        family-only key-set match -> (None, family, 0.5); an unknown named
        class takes its family from the key set; 'Unit' has no family;
        nothing -> (None, None, 0.0)."""
        reg = {"Pump": {"design_input_spec_params": {"P": {}, "dP_design": {}}},
               "StorageTank": {"design_input_spec_params": {"tau": {}}},
               "Tank": {"design_input_spec_params": {"tau": {}}}}
        U = C._unit_class
        self.assertEqual(U(spec_unit("Pump"), reg), ("Pump", "pump", 1.0))
        custom = spec_unit("GrainHandling", url=BIP_URL)
        self.assertEqual(U(custom, reg), ("GrainHandling", "solids_handling", 0.75))
        self.assertEqual(U(custom, reg, pinned=True)[2], 1.0)
        self.assertEqual(U({"unit_type": "Pump"}, reg), ("Pump", "pump", 1.0))
        self.assertEqual(U({"unit_type": "JetCooker"}, reg)[2], 0.75)
        self.assertEqual(U({"design_input_specs": {"dP_design": 1.0}}, reg),
                         ("Pump", "pump", 0.75))
        self.assertEqual(U({"unit_type": "Heat exchanger"}, reg),
                         (None, "hx_utility", 0.5))
        self.assertEqual(U({"design_input_specs": {"tau": 1.0}}, reg),
                         (None, "tank", 0.5))
        self.assertEqual(U(spec_unit("Frobnicator", {"tau": 1.0}), reg),
                         ("Frobnicator", "tank", 1.0))
        self.assertEqual(U(spec_unit("Unit"), reg), ("Unit", None, 1.0))
        self.assertEqual(U({"unit_type": "Widget"}, reg), (None, None, 0.0))
        self.assertEqual(U("garbage"), (None, None, 0.0))

    def test_registry_class(self):
        """_registry_class: exact registry key; else the table's
        registry_class (CornStorage -> StorageTank); else a key-set match of
        the same family; a key-set match of another family -> None."""
        reg = {"StorageTank": {"design_input_spec_params": {"tau": {}}},
               "Pump": {"design_input_spec_params": {"P": {}}}}
        R = C._registry_class
        self.assertEqual(R("StorageTank", "tank", {}, reg), "StorageTank")
        self.assertEqual(R("CornStorage", "tank", {}, reg), "StorageTank")
        unit = {"design_input_specs": {"P": 1.0}}
        self.assertEqual(R("Foo", "pump", unit, reg), "Pump")
        self.assertEqual(R("Foo", None, unit, reg), "Pump")
        self.assertIsNone(R("Foo", "reactor", unit, reg))
        self.assertIsNone(R(None, None, {}, reg))

    def test_weights(self):
        """_family_weight (unknown -> 2), _unit_weight on a context (a
        reactor -> 3), _wmean ((1*3 + 0*1) / 4 = 0.75; no weight -> None)."""
        self.assertEqual(C._family_weight("flash"), 3)
        self.assertEqual(C._family_weight(None), C.UNKNOWN_FAMILY_WEIGHT)
        sc = ctx(one_unit_doc(spec_unit("SinglePhaseReactor")))
        self.assertEqual(C._unit_weight(sc, 0), 3)
        self.assertEqual(C._wmean([(1.0, 3), (0.0, 1)]), 0.75)
        self.assertIsNone(C._wmean([]))

    def test_is_cost_free(self):
        """_is_cost_free: Mixer / Splitter / Separator / MockSplitter
        (junction) and an unlisted mixer subclass -> True; @cost mixer
        subclasses (AmmoniaMixer) and costed splitter subclasses
        (CleaningSystem) -> False."""
        self.assertTrue(C._is_cost_free("Mixer", "mixer"))
        self.assertTrue(C._is_cost_free("Splitter", "splitter"))
        self.assertTrue(C._is_cost_free("Separator", "splitter"))
        self.assertTrue(C._is_cost_free("MockSplitter", "junction"))
        self.assertTrue(C._is_cost_free(None, "mixer"))
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
        per-unit inlet/outlet indexes."""
        streams = [{"source_unit_id": "None", "sink_unit_id": "A"},
                   {"source_unit_id": "A", "sink_unit_id": "B"},
                   {"source_unit_id": "B", "sink_unit_id": None},
                   {"source_unit_id": "", "sink_unit_id": "None"}]
        topo = C._topology(streams, {"A", "B"})
        self.assertEqual((topo["feeds"], topo["internal"], topo["outlets"],
                          topo["isolated"]), ([0], [1], [2], [3]))
        self.assertEqual(topo["inlets"]["B"], [1])
        self.assertEqual(topo["unit_outlets"]["A"], [1])

    def test_context_on_valid_doc(self):
        """_ScoringContext(valid_doc): one feed, one outlet, U1 resolved to
        the core registry class Mixer (confidence 1.0, unit_core True,
        registry class Mixer), labels by id."""
        sc = ctx(valid_doc())
        self.assertEqual((sc.feeds, sc.outlets), ([0], [1]))
        self.assertEqual(sc.unit_classes[0], ("Mixer", "mixer", 1.0))
        self.assertEqual((sc.unit_core, sc.unit_registry_classes),
                         ([True], ["Mixer"]))
        self.assertEqual(sc.stream_labels, ["streams[feed]", "streams[prod]"])
        self.assertEqual(sc.chem_by_index, {0: "Ethanol"})

    def test_context_positions_costed_and_vctx(self):
        """_ScoringContext: unit_positions maps id -> first position; costed
        lists the non-cost-free units (a Mixer is cost-free, a Pump is not);
        the vctx property is the validator's _Context over the same doc,
        built once (molar_mass('Ethanol') -> 46.07)."""
        doc = valid_doc()
        doc["units"].append(dict(spec_unit("Pump"), id="P1"))
        sc = ctx(doc)
        self.assertEqual(sc.unit_positions, {"U1": 0, "P1": 1})
        self.assertEqual(sc.costed, [1])
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
        outlet_phases read streams; class_default needs a confidence-1 class;
        rigorous reads the spec; explicit:<k> reads specs;
        design_result:<n> reads results; unknown -> ValueError."""
        doc = one_unit_doc(spec_unit("Pump", {"rigorous": True},
                                     {"Reflux": 2.0}),
                           ins=({"enthalpy": 1.0},), outs=({"enthalpy": 2.0},))
        sc = ctx(doc)
        for cond in ("outlet_T", "outlet_P", "all_ports_state", "enthalpy",
                     "outlet_phases", "class_default", "rigorous",
                     "design_result:Reflux"):
            self.assertTrue(C._condition_holds(cond, sc, 0, set()), cond)
        self.assertTrue(C._condition_holds("explicit:LHK", sc, 0, {"LHK"}))
        self.assertFalse(C._condition_holds("explicit:LHK", sc, 0, set()))
        sc = ctx(one_unit_doc({"unit_type": "Heat exchanger"}))
        for cond in ("enthalpy", "class_default", "rigorous"):
            self.assertFalse(C._condition_holds(cond, sc, 0, set()), cond)
        with self.assertRaises(ValueError):
            C._condition_holds("bogus", sc, 0, set())

    def test_requirement_applies(self):
        """_requirement_applies (R2.13: waived only on affirmative evidence):
        unconditional -> True; the HXutility V requirement is waived when
        rigorous or when inlet and outlet phase sets are known and equal;
        the HXprocess phase0|phase1 requirement when they are known and
        equal (rigorous or not); the pump dP_design requirement when inlet
        and outlet P are known with a rise >= 1 Pa; an unevaluable condition
        (an outlet without P or without phase data) APPLIES the requirement;
        unknown -> ValueError."""
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
        self.assertEqual(excused("Unit", {"T": 300.0}), set())


class TestFamilyRules(unittest.TestCase):
    """Each family / class rule of Revision 1 R1.4 on a one-unit document
    (feed -> U1 -> product, liquid at 300 K and 101325 Pa unless stated)."""

    def check(self, expected, *args, **kwargs):
        self.assertAlmostEqual(spec_score(*args, **kwargs)[0], expected,
                               msg=args[0])

    def test_splitter_dryer_extraction(self):
        """splitter/dryer: explicit split 1, derivable from full ports 0.75,
        not derivable 0; VentScrubber's split is its 'gas' list;
        MultiStageMixerSettlers: N_stages + class-default feed_stages ->
        0.875; MixerSettler has no requirement."""
        two = ({}, {})
        self.check(1.0, "Splitter", {"split": [0.5]}, outs=two)
        self.check(0.75, "Splitter", outs=two)
        self.check(0.0, "Splitter", outs=({}, {"T": None}))
        self.check(0.75, "DrumDryer", outs=two)
        self.check(1.0, "VentScrubber", {"gas": ["CO2"]}, outs=two)
        self.check(0.875, "MultiStageMixerSettlers", {"N_stages": 4})
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
        outlet, adiabatic Q a class default) -> 1.75/2; phases + a consistent
        T -> 1; an inconsistent T -> 0.5. PhasePartition: phases -> 1,
        nothing -> 0."""
        self.check(0.875, "StageEquilibrium", {"phases": "lg"})
        self.check(1.0, "StageEquilibrium", {"phases": "lg", "T": 300.0})
        self.check(0.5, "StageEquilibrium", {"phases": "lg", "T": 350.0})
        self.check(1.0, "PhasePartition", {"phases": "lg"})
        self.check(0.0, "PhasePartition")

    def test_hx_utility(self):
        """HXutility: T -> 1; non-rigorous V alone -> 0.75 (T derived); a
        rigorous V alone -> 1; T with a non-rigorous l->g change -> 0.875 (V
        derivable from outlet phases); T + V on the change -> 1; an
        inconsistent T -> 0."""
        vap = ({"phases": VAP},)
        self.check(1.0, "HXutility", {"T": 300.0})
        self.check(0.75, "HXutility", {"V": 1})
        self.check(1.0, "HXutility", {"V": 1, "rigorous": True}, outs=vap)
        self.check(0.875, "HXutility", {"T": 300.0}, outs=vap)
        self.check(1.0, "HXutility", {"T": 300.0, "V": 1}, outs=vap)
        self.check(0.0, "HXutility", {"T": 350.0})

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
        1); P with a rise -> 1; an inconsistent P -> 0. Valve P -> 1;
        Compressor P without eta -> 0.5."""
        self.check(0.5, "Pump", {"P": 101325.0})
        self.check(1.0, "Pump", {"P": 101325.0, "dP_design": 101325.0})
        self.check(1.0, "Pump", {"P": 2e5}, outs=({"P": 2e5},))
        self.check(0.0, "Pump", {"P": 3e5}, outs=({"P": 2e5},))
        self.check(1.0, "IsenthalpicValve", {"P": 101325.0})
        self.check(0.5, "Compressor", {"P": 101325.0})

    def test_distillation_and_staged_columns(self):
        """BinaryDistillation: 5 explicit + partial_condenser derivable from
        phased outlets -> 5.75/6; k from Reflux/Minimum reflux results ->
        5.5/6. MESHDistillation fully stated -> 1;
        AdiabaticMultiStageVLEColumn with N_stages only -> (1 + 0 + 0.75 +
        0.75) / 4."""
        base = {"LHK": ["A", "B"], "P": 101325.0, "y_top": 0.9, "x_bot": 0.01}
        self.check(5.75 / 6, "BinaryDistillation", dict(base, k=1.2))
        self.check(5.5 / 6, "BinaryDistillation", base,
                   results={"Reflux": 2.0, "Minimum reflux": 1.5})
        self.check(1.0, "MESHDistillation", {
            "N_stages": 10, "feed_stages": [5], "reflux": 2.0, "boilup": 3.0,
            "P": 101325.0})
        self.check(0.625, "AdiabaticMultiStageVLEColumn", {"N_stages": 10})

    def test_evaporator(self):
        """MultiEffectEvaporator: P list + V -> 1; nothing -> (0 + 0.75)/2."""
        self.check(1.0, "MultiEffectEvaporator", {"P": [1e5, 5e4], "V": 0.5})
        self.check(0.375, "MultiEffectEvaporator")

    def test_reactor_families(self):
        """reactor T/tau/V_wf (tau only -> 1.75/3; inconsistent T -> 2/3);
        batch bioreactor N from 'Number of reactors' and class-default tau_0
        -> 3.5/4, also when T (the heat-utility temperature) differs from the
        adiabatically mixed outlets (no guard); the corn SSF (V explicit)
        3.75/4 when pinned, 3/4 unpinned (tau_0 class default needs a
        confidence-1 class); aerated theta_O2 class default -> 3.75/4 (a
        Q_O2_consumption duty override does not replace it; explicit
        theta_O2 -> 1); GasFedBioreactor gas_substrates + theta default ->
        4.75/5, without gas_substrates 3.75/5; Gibbs T and P from outlets ->
        0.75; BatchCrystallizer T/tau/N -> 1; corn Liquefaction tau + V_wf ->
        1."""
        self.check(1.0, "SinglePhaseReactor", {"T": 300.0, "tau": 2, "V_wf": 0.8})
        self.check(1.75 / 3, "SinglePhaseReactor", {"tau": 2})
        self.check(2 / 3, "SinglePhaseReactor", {"T": 310.0, "tau": 2, "V_wf": 0.8})
        results = {"Number of reactors": 4}
        self.check(0.875, "NRELBatchBioreactor", {"T": 300.0, "tau": 24},
                   results=results)
        self.check(0.875, "NRELBatchBioreactor", {"T": 305.15, "tau": 24},
                   results=results)
        ssf = "SimultaneousSaccharificationFermentation"
        self.check(3.75 / 4, ssf, {"T": 300.0, "tau": 24, "V": 1900.0},
                   url=BIP_URL, pinned=True)
        self.check(3.0 / 4, ssf, {"T": 300.0, "tau": 24, "V": 1900.0},
                   url=BIP_URL)
        aerated = {"T": 300.0, "tau": 24, "V_wf": 0.8}
        self.check(3.75 / 4, "AeratedBioreactor", aerated)
        self.check(3.75 / 4, "AeratedBioreactor",
                   dict(aerated, Q_O2_consumption=-460e3))
        self.check(1.0, "AeratedBioreactor", dict(aerated, theta_O2=0.4))
        self.check(4.75 / 5, "GasFedBioreactor",
                   dict(aerated, gas_substrates=["H2"]))
        self.check(3.75 / 5, "GasFedBioreactor", aerated)
        self.check(0.75, "GibbsEquilibriumReactor")
        self.check(1.0, "BatchCrystallizer", {"T": 300.0, "tau": 2, "N": 2})
        self.check(1.0, "Liquefaction", {"tau": 1.0, "V_wf": 0.9}, url=BIP_URL)

    def test_wastewater_classes(self):
        """biosteam.wastewater class rules: AnMBR T + class-default
        reactor_type -> 0.875; PolishingFilter T + OLR default -> 0.875;
        InternalCirculationRx T + OLRall and V_wf defaults -> 2.5/3;
        Anaerobic/AerobicDigestion class-default sludge_split / evaporation
        -> 0.75; MembraneBioreactor is a splitter (split explicit -> 1)."""
        self.check(0.875, "AnMBR", {"T": 308.15})
        self.check(0.875, "PolishingFilter", {"T": 303.15})
        self.check(2.5 / 3, "InternalCirculationRx", {"T": 308.15})
        self.check(0.75, "AnaerobicDigestion")
        self.check(0.75, "AerobicDigestion")
        self.assertEqual(C._class_family("MembraneBioreactor"), "splitter")
        self.check(1.0, "MembraneBioreactor", {"split": [0.5]}, outs=({}, {}))

    def test_tank_facilities_and_excluded(self):
        """tank tau (1 / 0); ThermalOxidizer (combustor) excluded;
        BoilerTurbogenerator one of two efficiencies -> 0.5;
        HeatExchangerNetwork T_min_app -> 1."""
        self.check(1.0, "StorageTank", {"tau": 2.0})
        self.check(0.0, "StorageTank")
        self.assertIsNone(spec_score("ThermalOxidizer")[0])
        self.check(0.5, "BoilerTurbogenerator", {"boiler_efficiency": 0.8})
        self.check(1.0, "HeatExchangerNetwork", {"T_min_app": 5.0})


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
        """_thm_02_credit (R2.0/R2.3): a valid registry_id -> 1; a junk one
        ('x') counts as absent; id resolvable by name with a matching molar
        mass -> NAME_RESOLVED_CREDIT (0.75); resolvable with nothing to
        verify it -> NAME_UNVERIFIED_CREDIT (0.5); resolvable but of another
        molar mass -> the formula tier FORMULA_ONLY_CREDIT (0.25); a
        formula-looking id never earns the name tier (0.25 via the id);
        nothing -> 0; each below 1 with a registry_id locator suffix."""
        with name_lookup({"Ethanol", "Starch", "HP"}):
            self.assertEqual(C._thm_02_credit({"registry_id": "64-17-5"}, "X"),
                             (1.0, None))
            self.assertEqual(C._thm_02_credit({"registry_id": "x"}, "X")[0], 0.0)
            self.assertEqual(C._thm_02_credit({"formula": "C2H6O"}, "Ethanol")[0],
                             C.NAME_RESOLVED_CREDIT)
            self.assertEqual(C._thm_02_credit({}, "Ethanol")[0],
                             C.NAME_UNVERIFIED_CREDIT)
            self.assertEqual(C._thm_02_credit({"molar_mass": 162.14}, "Starch")[0],
                             C.NAME_UNVERIFIED_CREDIT)
            credit, note = C._thm_02_credit({"molar_mass": 90.0}, "Ethanol")
            self.assertEqual(credit, 0.0)
            self.assertIn("another molar mass", note)
            credit, note = C._thm_02_credit({"formula": "C2H6O"}, "Fiber")
            self.assertEqual((credit, note), (C.FORMULA_ONLY_CREDIT,
                                              ".registry_id (formula only)"))
            credit, note = C._thm_02_credit({"molar_mass": 90.08}, "HP")
            self.assertEqual(credit, C.FORMULA_ONLY_CREDIT)
            self.assertIn("isomer-ambiguous", note)
            self.assertEqual(C._thm_02_credit({}, None)[0], 0.0)
        self.assertEqual((C.NAME_RESOLVED_CREDIT, C.NAME_UNVERIFIED_CREDIT,
                          C.FORMULA_ONLY_CREDIT), (0.75, 0.5, 0.25))

    def test_name_tier_and_record_agreement(self):
        """_name_tier: None for a non-string, formula-like or unresolvable id,
        else _record_agreement of the record: 'verified' when the molar
        masses agree within NAME_MW_RTOL (declared, or derived by
        _chemical_molar_mass from the formula; the record's by
        _record_molar_mass) or the formulas match; 'unverified' when either
        side has nothing comparable; 'mismatch' otherwise. _name_record
        returns the record itself (cached)."""
        with name_lookup({"Ethanol"}):
            self.assertEqual(C._name_record("Ethanol").MW, 46.06844)
            self.assertIsNone(C._name_record("Fiber"))
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

    def test_cas_contradicts(self):
        """_cas_contradicts: a CAS whose database record's molar mass differs
        from the chemical's by more than CAS_MW_RTOL -> True (the registry
        id is then invalid: _valid_registry_id False); within tolerance, no
        record, or no molar mass to compare -> False."""
        with name_lookup({"64-17-5": (46.06844, "C2H6O")}):
            self.assertTrue(C._cas_contradicts("64-17-5", {"molar_mass": 90.0}))
            self.assertFalse(C._valid_registry_id(
                {"registry_id": "64-17-5", "molar_mass": 90.0}))
            self.assertFalse(C._cas_contradicts("64-17-5", {"molar_mass": 46.5}))
            self.assertFalse(C._cas_contradicts("64-17-5", {}))
            self.assertFalse(C._cas_contradicts("7732-18-5", {"molar_mass": 1.0}))

    def test_thm_02_identity(self):
        """_score_thm_02 (CMP-THM-02): Ethanol with formula, name tier
        unavailable -> 0.25; name-resolvable -> 0.75; registry_id -> 1.0;
        an undeclared referenced id counts as 0 (-> 0.5 beside a registry
        id); streams but no chemicals at all -> 0; empty doc -> None."""
        with name_lookup(search=None):
            self.assertEqual(run(C._score_thm_02, valid_doc())[0], 0.25)
        with name_lookup({"Ethanol"}):
            self.assertEqual(run(C._score_thm_02, valid_doc())[0], 0.75)
            doc = valid_doc()
            mutate(doc, "chemicals/0/registry_id", "64-17-5")
            self.assertEqual(run(C._score_thm_02, doc)[0], 1.0)
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
        1; neither -> 0 with a molar_mass locator; an undeclared referenced
        id -> 0 for it."""
        doc = valid_doc()
        self.assertEqual(run(C._score_thm_03, doc)[0], 1.0)
        remove(doc, "chemicals/0/molar_mass")
        self.assertEqual(run(C._score_thm_03, doc)[0], 1.0)
        remove(doc, "chemicals/0/formula")
        score, _m, missing = run(C._score_thm_03, doc)
        self.assertEqual(score, 0.0)
        self.assertIn("chemicals[Ethanol].molar_mass", missing[0])
        doc = valid_doc()
        doc["streams"][0]["stream_properties"]["phases"]["l"]["composition"].append(
            {"component_name": "Water", "mol_fraction": 0.0})
        self.assertEqual(run(C._score_thm_03, doc)[0], 0.5)

    def test_thm_04_phase_behaviour(self):
        """_score_thm_04 / _chemical_phases (CMP-THM-04): Ethanol only in 'l'
        -> 0; also seen in 'g' -> DERIVED_CREDIT; only ever 's' ->
        DERIVED_CREDIT; no chemicals but streams -> 0."""
        doc = valid_doc()
        self.assertEqual(C._chemical_phases(ctx(doc)), {"Ethanol": {"l"}})
        self.assertEqual(run(C._score_thm_04, doc)[0], 0.0)
        doc["streams"][1]["stream_properties"]["phases"]["g"] = {
            "total_molar_flow": 0.5,
            "composition": [{"component_name": "Ethanol", "mol_fraction": 1.0}]}
        self.assertEqual(run(C._score_thm_04, doc)[0], C.DERIVED_CREDIT)
        solid = valid_doc()
        for s in solid["streams"]:
            s["stream_properties"]["phases"] = {
                "s": s["stream_properties"]["phases"]["l"]}
        self.assertEqual(run(C._score_thm_04, solid)[0], C.DERIVED_CREDIT)
        remove(solid, "chemicals")
        self.assertEqual(run(C._score_thm_04, solid)[0], 0.0)

    def test_thm_05_non_database_property_data(self):
        """_score_thm_05 (CMP-THM-05): a chemical resolvable neither by
        registry id nor by name -> 0; name-resolvable or with a registry id
        -> None; an undeclared referenced id -> 0; no chemicals at all but
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
        with name_lookup():
            doc = valid_doc()
            mutate(doc, "chemicals/0/registry_id", "64-17-5")
            self.assertIsNone(run(C._score_thm_05, doc)[0])
            remove(doc, "chemicals")
            self.assertEqual(run(C._score_thm_05, doc)[0], 0.0)
            self.assertIsNone(run(C._score_thm_05, {})[0])

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

    def test_thm_06_family_weighted(self):
        """_score_thm_06 (CMP-THM-06) is family-weighted: a reactor (weight 3)
        with a full package beside a pump (weight 1) with none -> 3/4."""
        full = dict(FULL_PKG)
        doc = valid_doc()
        doc["units"] = [dict(spec_unit("SinglePhaseReactor"), id="R1",
                             thermo_property_package=full),
                        dict(spec_unit("Pump"), id="P1")]
        self.assertEqual(run(C._score_thm_06, doc)[0], 0.75)

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
        feed (mixer ins are order-free); a reactor's single-stream sides ->
        DERIVED_CREDIT (unexported ports may exist); a pump's 2 inlets -> (0
        + 1) / 2; no units -> None."""
        self.assertEqual(run(C._score_top_02, valid_doc())[0], 1.0)
        doc = valid_doc()
        doc["streams"].append(dict(copy.deepcopy(doc["streams"][0]), id="feed2"))
        self.assertEqual(run(C._score_top_02, doc), (
            1.0, "not representable; 2 of 2 unit port side(s) unambiguous by "
                 "convention", []))
        score, _m, missing = run(C._score_top_02, one_unit_doc(
            spec_unit("SinglePhaseReactor")))
        self.assertEqual(score, C.DERIVED_CREDIT)
        self.assertIn("port index not representable", missing[0])
        score, _m, missing = run(C._score_top_02, one_unit_doc(
            spec_unit("Pump"), ins=({}, {})))
        self.assertEqual(score, 0.5)
        self.assertEqual(missing, ["units[U1].ins: order of 2 streams not "
                                   "representable"])
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
        0) stays in the population and scores 1 even without T -> 1.0 with
        an 'empty, stated by their zero flow' note; beside a half-stated
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
        empty = one_unit_doc(spec_unit("Mixer"), ins=({}, {"mass": 0.0,
                                                           "T": None}))
        empty["streams"][1]["stream_properties"]["total_molar_flow"] = 0.0
        score, message, _missing = run(C._score_feed_01, empty)
        self.assertEqual(score, 1.0)
        self.assertIn("1 empty, stated by their zero flow", message)
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
        """_utility_port_feed: a feed into a DrumDryer (dryer family) -> the
        dryer's label; a feed into a Pump -> None."""
        doc = one_unit_doc(spec_unit("DrumDryer"))
        self.assertEqual(C._utility_port_feed(ctx(doc), 0), "units[U1]")
        doc = one_unit_doc(spec_unit("Pump"))
        self.assertIsNone(C._utility_port_feed(ctx(doc), 0))

    def test_feed_02_prices(self):
        """_score_feed_02 / _is_empty_stream (CMP-FEED-02): unpriced boundary
        streams -> 0; both priced (0 counts) -> 1; a 0 price on a feed into a
        utility port (DrumDryer) -> 0.5 for it (0.75 overall), while a 0
        price on a product of that dryer stays 1; an EMPTY (zero-flow)
        boundary stream stays in the population and scores 1 without a
        price (unpriced empty feed beside an unpriced product -> 0.5);
        deleting its stream_properties makes it an unpriced non-empty stream
        -> 0 (lower, never higher); no boundary streams -> None."""
        doc = valid_doc()
        self.assertEqual(run(C._score_feed_02, doc)[0], 0.0)
        mutate(doc, "streams/0/price", 0.5)
        mutate(doc, "streams/1/price", 0)
        self.assertEqual(run(C._score_feed_02, doc)[0], 1.0)
        dryer = copy.deepcopy(doc)
        dryer["units"][0] = dict(spec_unit("DrumDryer"), id="U1")
        mutate(dryer, "streams/0/price", 0)
        score, _m, missing = run(C._score_feed_02, dryer)
        self.assertEqual(score, 0.75)
        self.assertIn("streams[feed].price: 0 on a utility port of units[U1]",
                      missing[0])
        empty = valid_doc()
        empty["streams"][0]["stream_properties"]["total_mass_flow"] = 0.0
        self.assertTrue(C._is_empty_stream(empty["streams"][0]))
        score, message, missing = run(C._score_feed_02, empty)
        self.assertEqual((score, missing), (0.5, ["streams[prod].price"]))
        self.assertIn("1 empty, needing no price", message)
        del empty["streams"][0]["stream_properties"]
        self.assertEqual(run(C._score_feed_02, empty)[0], 0.0)
        mutate(doc, "streams/0/source_unit_id", "U1")
        mutate(doc, "streams/1/sink_unit_id", "U1")
        self.assertIsNone(run(C._score_feed_02, doc)[0])


# -------------------------------------------------------- unit-op criteria

class TestUnitOpCriteria(unittest.TestCase):
    def test_uop_01_class_identity(self):
        """_score_uop_01 (CMP-UOP-01): registry class name -> 1; free-text
        unit_type -> 0.5; unknown -> 0; no units -> None."""
        self.assertEqual(run(C._score_uop_01, valid_doc())[0], 1.0)
        doc = unit_doc({"unit_type": "Heat exchanger"})
        score, _m, missing = run(C._score_uop_01, doc)
        self.assertEqual(score, 0.5)
        self.assertIn("family 'hx_utility' inferred", missing[0])
        self.assertEqual(run(C._score_uop_01, unit_doc({"unit_type": "Widget"}))[0], 0.0)
        self.assertIsNone(run(C._score_uop_01, {})[0])

    def test_class_note(self):
        """_class_note: confidence 1 -> None; a stated custom class (by
        design_simulation_method or unit_type) unpinned -> 'custom class ...
        pins no commit|version'; a class inferred from spec keys -> '...
        inferred from the design_input_specs keys'; a keyword family ->
        "family 'hx_utility' inferred"; nothing -> 'class unresolved'."""
        def note(unit):
            return C._class_note(ctx(one_unit_doc(unit)), 0)
        self.assertIsNone(note(spec_unit("Pump")))
        self.assertIn("custom class 'GrainHandling' is not core BioSTEAM",
                      note(spec_unit("GrainHandling", url=BIP_URL)))
        self.assertIn("custom class 'JetCooker'", note({"unit_type": "JetCooker"}))
        self.assertIn("inferred from the design_input_specs keys",
                      note({"design_input_specs": {"dP_design": 1.0},
                            "unit_type": "?"}))
        self.assertIn("family 'hx_utility' inferred",
                      note({"unit_type": "Heat exchanger"}))
        self.assertIn("class unresolved (unit_type 'Widget'",
                      note({"unit_type": "Widget"}))

    def test_uop_02_essential_specs(self):
        """_score_uop_02 (CMP-UOP-02): mixer only -> None; HXutility with a T
        matching its outlet -> 1; the same T against a 300 K outlet -> 0 with
        an 'inconsistent' locator (consistency guard); HXutility with only an
        outlet T -> DERIVED_CREDIT; unknown family -> 0."""
        self.assertIsNone(run(C._score_uop_02, valid_doc())[0])
        self.assertEqual(run(C._score_uop_02, unit_doc(
            cls("HXutility"), {"T": 350.0}, out_T=350.0))[0], 1.0)
        score, _m, missing = run(C._score_uop_02,
                                 unit_doc(cls("HXutility"), {"T": 350.0}))
        self.assertEqual(score, 0.0)
        self.assertIn("(inconsistent: T disagrees with the outlet stream)",
                      missing[0])
        score, _m, missing = run(C._score_uop_02, unit_doc(cls("HXutility"), {}))
        self.assertEqual(score, C.DERIVED_CREDIT)
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
        without pressure -> 0.75; feeds only -> None."""
        self.assertEqual(run(C._score_uop_04, valid_doc())[0], 1.0)
        self.assertEqual(run(C._score_uop_04, unit_doc(out_P=None))[0], 0.75)
        doc = valid_doc()
        del doc["streams"][1]
        self.assertIsNone(run(C._score_uop_04, doc)[0])

    def test_uop_05_spec_units(self):
        """_score_uop_05 (CMP-UOP-05): mixer only -> None; a core class at
        confidence 1 -> DERIVED_CREDIT ('fixed by class conventions'); an
        unpinned custom class (JetCooker) -> 0, pinned -> DERIVED_CREDIT; the
        bare 'Unit' (confidence 1, no family) -> 0; family-weighted: reactor
        (3, 0.75) + bare Unit (2, 0) -> 0.45."""
        self.assertIsNone(run(C._score_uop_05, valid_doc())[0])
        score, _m, missing = run(C._score_uop_05,
                                 one_unit_doc(spec_unit("HXutility")))
        self.assertEqual(score, C.DERIVED_CREDIT)
        self.assertEqual(missing, ["units[U1].design_input_specs: quantity "
                                   "units not representable (fixed by class "
                                   "conventions)"])
        jet = spec_unit("JetCooker", url=BIP_URL)
        self.assertEqual(run(C._score_uop_05, one_unit_doc(jet))[0], 0.0)
        self.assertEqual(run(C._score_uop_05, one_unit_doc(jet, pinned=True))[0],
                         C.DERIVED_CREDIT)
        bare = one_unit_doc(spec_unit("Unit"))
        score, _m, missing = run(C._score_uop_05, bare)
        self.assertEqual(score, 0.0)
        self.assertNotIn("fixed by class conventions", missing[0])
        doc = one_unit_doc(spec_unit("SinglePhaseReactor"))
        doc["units"].append(dict(spec_unit("Unit"), id="U2"))
        self.assertAlmostEqual(run(C._score_uop_05, doc)[0], 0.45)


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
        """valid_doc whose product carries CO2 the feed lacks."""
        doc = unit_doc(unit_fields)
        doc["streams"][1]["stream_properties"]["phases"]["l"]["composition"] = [
            {"component_name": "Ethanol", "mol_fraction": 0.5},
            {"component_name": "CO2", "mol_fraction": 0.5}]
        return doc

    def test_species_change(self):
        """_species_change: CO2 in the outlet, absent from the inlet ->
        ['CO2 appears']; the valid mixer -> []."""
        self.assertEqual(C._species_change(ctx(self._appearing_doc()), "U1"),
                         ["CO2 appears"])
        self.assertEqual(C._species_change(ctx(valid_doc()), "U1"), [])
        self.assertEqual(C._species_change(ctx(valid_doc()), None), [])

    def test_reactive_reason(self):
        """_reactive_reason: reactor family / declared reactions / species
        evidence -> a reason; a facility with species evidence -> None."""
        self.assertEqual(C._reactive_reason(ctx(unit_doc(cls("NRELFermentation"))), 0),
                         "reactor family")
        self.assertEqual(C._reactive_reason(
            ctx(unit_doc({"reactions": [reaction()]})), 0), "declares reactions")
        self.assertEqual(C._reactive_reason(ctx(self._appearing_doc()), 0),
                         "CO2 appears")
        self.assertIsNone(C._reactive_reason(
            ctx(self._appearing_doc(cls("ProcessWaterCenter"))), 0))

    def test_rxn_01_reactive_units(self):
        """_score_rxn_01 (CMP-RXN-01): no reactive unit -> None; species
        appear without reactions -> 0; with a reaction -> 1."""
        self.assertIsNone(run(C._score_rxn_01, valid_doc())[0])
        score, _m, missing = run(C._score_rxn_01, self._appearing_doc())
        self.assertEqual(score, 0.0)
        self.assertIn("none well-formed declared (CO2 appears)", missing[0])
        doc = self._appearing_doc({"reactions": [reaction()]})
        self.assertEqual(run(C._score_rxn_01, doc)[0], 1.0)
        doc = self._appearing_doc({"reactions": [{}, reaction(equation="x")]})
        self.assertEqual(run(C._score_rxn_01, doc)[0], 0.0)

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
        """_reactive_units: the valid mixer -> []; a reactor-family unit ->
        [(0, 'reactor family')]."""
        self.assertEqual(C._reactive_units(ctx(valid_doc())), [])
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
        self.assertEqual(C._per_reactive_unit(ctx(valid_doc()), half),
                         (None, [], 0, 0))

    def test_rxn_02_definitions(self):
        """_score_rxn_02 (CMP-RXN-02): complete -> 1; conversion 1.5 -> 2/3
        with a conversion locator; a reactive unit without reactions -> 0
        (population = reactive units, not reactions); no reactive unit ->
        None."""
        self.assertEqual(run(C._score_rxn_02, unit_doc({"reactions": [reaction()]}))[0], 1.0)
        score, _m, missing = run(C._score_rxn_02, unit_doc(
            {"reactions": [reaction(conversion=1.5)]}))
        self.assertAlmostEqual(score, 2 / 3)
        self.assertEqual(missing, ["units[U1].reactions[0].conversion (0..1)"])
        self.assertEqual(run(C._score_rxn_02, rxn_doc(None))[0], 0.0)
        self.assertIsNone(run(C._score_rxn_02, valid_doc())[0])

    def test_rxn_03_series_parallel(self):
        """_score_rxn_03 (CMP-RXN-03) over the reactive units: two indexed
        reactions -> 1; one index missing -> 0; a single (unindexed) reaction
        -> 1 (no order to state); a reactor-family unit whose reactions are
        deleted -> 0 with a "none well-formed declared" locator (applicable,
        not N/A); a unit carrying only a malformed reaction ([{}]) -> 0;
        no reactive unit -> None."""
        two = [reaction(index=0), reaction(index=1)]
        self.assertEqual(run(C._score_rxn_03, unit_doc({"reactions": two}))[0], 1.0)
        del two[1]["index"]
        score, _m, missing = run(C._score_rxn_03, unit_doc({"reactions": two}))
        self.assertEqual((score, missing), (0.0, ["units[U1].reactions[1].index"]))
        self.assertEqual(run(C._score_rxn_03, unit_doc({"reactions": [reaction()]}))[0], 1.0)
        doc = unit_doc(spec_unit("SinglePhaseReactor"))
        score, _m, missing = run(C._score_rxn_03, doc)
        self.assertEqual(score, 0.0)
        self.assertIn("units[U1].reactions: none well-formed declared "
                      "(reactor family)", missing[0])
        doc["units"][0]["reactions"] = [{}]
        self.assertEqual(run(C._score_rxn_03, doc)[0], 0.0)
        self.assertIsNone(run(C._score_rxn_03, valid_doc())[0])

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
        mass (both) -> False; a species without molar mass -> False."""
        sc = ctx(rxn_doc(None))
        self.assertEqual(C.BASIS_RTOL, 1e-3)
        self.assertTrue(C._basis_derivable(sc, {"equation": GLUCOSE_EQ}))
        self.assertTrue(C._basis_derivable(sc, {"stoichiometry": {
            "Glucose": -1.0, "Ethanol": 0.5114, "CO2": 0.4886}}))
        self.assertFalse(C._basis_derivable(sc, {"equation": "Glucose -> 3 Ethanol"}))
        iso = rxn_doc(None)
        iso["chemicals"] += [{"id": "A", "molar_mass": 10.0},
                             {"id": "B", "molar_mass": 10.0}, {"id": "M"}]
        self.assertFalse(C._basis_derivable(ctx(iso), {"equation": "A -> B"}))
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
        derivable, phase derivable -> DERIVED_CREDIT / 2."""
        sc = ctx(rxn_doc(None))
        score, missing = C._reaction_basis_phase(sc, 0, 0, {"equation": GLUCOSE_EQ})
        self.assertEqual(score, C.DERIVED_CREDIT)
        self.assertEqual(len(missing), 2)
        self.assertIn("(derivable from the mass balance)", missing[0])
        score, _m = C._reaction_basis_phase(sc, 0, 0, {"equation": "Glucose -> 3 Ethanol"})
        self.assertEqual(score, C.DERIVED_CREDIT / 2)

    def test_rxn_04_basis(self):
        """_score_rxn_04 (CMP-RXN-04): a reactor whose Glucose reaction has a
        derivable basis and single-phase ports -> DERIVED_CREDIT; a reactor
        without reactions -> 0; no reactive unit -> None."""
        doc = rxn_doc([reaction(equation=GLUCOSE_EQ, reactant="Glucose")])
        self.assertEqual(run(C._score_rxn_04, doc)[0], C.DERIVED_CREDIT)
        self.assertEqual(run(C._score_rxn_04, rxn_doc(None))[0], 0.0)
        self.assertIsNone(run(C._score_rxn_04, valid_doc())[0])


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
        """_per_costed_unit: family-weighted over sc.costed (a reactor, weight
        3, scoring 1 beside a pump, weight 1, scoring 0 -> 0.75, one full);
        explicit positions restrict the population; none -> (None, [], 0)."""
        doc = valid_doc()
        doc["units"] = [dict(spec_unit("SinglePhaseReactor"), id="R1"),
                        dict(spec_unit("Pump"), id="P1")]
        sc = ctx(doc)
        score_of = lambda sc, p: ((1.0, []) if p == 0 else (0.0, ["gap"]))
        self.assertEqual(C._per_costed_unit(sc, score_of), (0.75, ["gap"], 1))
        self.assertEqual(C._per_costed_unit(sc, score_of, [1]), (0.0, ["gap"], 0))
        self.assertEqual(C._per_costed_unit(ctx(valid_doc()), score_of),
                         (None, [], 0))

    def test_cost_01_purchase_costs(self):
        """_cost_01_unit / _score_cost_01 (CMP-COST-01): only a cost-free
        Mixer -> None; a costed unit without purchase_costs -> 0; with {} ->
        0 ('(empty)'); non-empty -> 1."""
        self.assertIsNone(run(C._score_cost_01, valid_doc())[0])
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
        self.assertIsNone(run(C._score_cost_02, valid_doc())[0])

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
        self.assertIsNone(run(C._score_cost_03, valid_doc())[0])

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
        self.assertIsNone(run(C._score_cost_04, valid_doc())[0])

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
        self.assertIsNone(run(C._score_cost_05, valid_doc())[0])

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
        self.assertIsNone(run(C._score_cost_06, valid_doc())[0])

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
        self.assertIsNone(run(C._score_cost_07, valid_doc())[0])


# ---------------------------------------------------------- utility criteria

STEAM = {"id": "steam", "temperature": 400.0, "pressure": 3e5,
         "regeneration_price": 0.2, "heat_transfer_efficiency": 0.95,
         "composition": [{"phase": "g", "component_name": "Water",
                          "mol_fraction": 1.0}]}


def with_agents(heat=(), power=(), other=(), consumption=None):
    doc = valid_doc()
    doc["utilities"] = {"heat_utilities": list(heat),
                        "power_utilities": list(power),
                        "other_utilities": list(other)}
    if consumption is not None:
        doc["units"][0]["utility_consumption_results"] = consumption
    return doc


class TestUtilityCriteria(unittest.TestCase):
    def test_utility_refs_and_agent_gaps(self):
        """_utility_refs -> {agent id: first referencing unit}; _agent_gaps:
        heat agent without composition -> ['composition']; power -> []."""
        sc = ctx(with_agents(consumption={"steam": 5.0}))
        self.assertEqual(C._utility_refs(sc), {"steam": "units[U1]"})
        self.assertEqual(C._agent_gaps("heat_utilities",
                                       dict(STEAM, composition=[])), ["composition"])
        self.assertEqual(C._agent_gaps("power_utilities", {"id": "grid"}), [])

    def test_utility_and_heat_need(self):
        """_utility_need / _heat_need (R1.9): a lone Mixer, nothing declared
        -> neither; a Pump -> utility need only; an HXutility -> both; a
        declared heat agent -> both; a unit utility result -> utility need."""
        self.assertEqual((C._utility_need(ctx(valid_doc())),
                          C._heat_need(ctx(valid_doc()))), (False, False))
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
        -> 1; declared without composition -> 2/3."""
        self.assertIsNone(run(C._score_util_01, valid_doc())[0])
        score, _m, missing = run(C._score_util_01, one_unit_doc(spec_unit("Pump")))
        self.assertEqual((score, missing), (0.0, ["utilities: no agents declared"]))
        score, _m, missing = run(C._score_util_01, with_agents(consumption={"steam": 1}))
        self.assertEqual(score, 0.0)
        self.assertIn("no agent 'steam' (referenced by units[U1])", missing[0])
        self.assertEqual(run(C._score_util_01, with_agents(
            [STEAM], consumption={"steam": 1}))[0], 1.0)
        self.assertAlmostEqual(run(C._score_util_01, with_agents(
            [dict(STEAM, composition=[])]))[0], 2 / 3)

    def test_util_02_prices(self):
        """_score_util_02 (CMP-UTIL-02): priced heat agent and legacy-priced
        power agent -> 1; unpriced other agent -> 2/3 overall; a referenced
        but undeclared agent counts 0 (-> 1/2 beside priced steam); a Pump
        with no agent -> 0; no need -> None."""
        doc = with_agents([STEAM], [{"id": "grid", "price": 0.07}],
                          [{"id": "gas"}])
        score, _m, missing = run(C._score_util_02, doc)
        self.assertAlmostEqual(score, 2 / 3)
        self.assertEqual(missing, ["utilities.other_utilities[gas].price"])
        score, _m, missing = run(C._score_util_02, with_agents(
            [STEAM], consumption={"power": 3.0}))
        self.assertEqual(score, 0.5)
        self.assertIn("no agent 'power' to price", missing[0])
        self.assertEqual(run(C._score_util_02, one_unit_doc(spec_unit("Pump")))[0], 0.0)
        self.assertIsNone(run(C._score_util_02, valid_doc())[0])

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
        self.assertIsNone(run(C._score_util_03, valid_doc())[0])

    def test_util_04_results(self):
        """_score_util_04 / _util_04_unit (CMP-UTIL-04): no result objects ->
        0; both -> 1; family-weighted (a reactor with both beside a pump with
        neither -> 3/4). R2.0: a unit that always draws a utility
        (_draws_utility: pump, HXutility, ...) with an empty consumption +
        production pair -> 0, and non-numeric entries ({'a': 'b'}) count as
        absent; one numeric entry -> 1. Waived only on affirmative evidence:
        an idle unit (every connected stream states a zero flow), or a flash
        stated adiabatic (Q = 0)."""
        self.assertEqual(run(C._score_util_04, valid_doc())[0], 0.0)
        doc = unit_doc({"utility_consumption_results": {},
                        "utility_production_results": {}})
        self.assertEqual(run(C._score_util_04, doc)[0], 1.0)
        doc["units"] = [dict(spec_unit("SinglePhaseReactor"), id="R1",
                             utility_consumption_results={},
                             utility_production_results={}),
                        dict(spec_unit("Pump"), id="P1")]
        self.assertEqual(run(C._score_util_04, doc)[0], 0.75)
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
        self.assertFalse(C._draws_utility(ctx(valid_doc()), 0))
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
        """_score_sys_02 (CMP-SYS-02): 13 groups incl. the price book;
        currency + year -> 2/13; without currency -> 1/13 with a
        metadata.TEA_currency locator; the price book is listed missing."""
        self.assertEqual(len(C._TEA_ITEMS), 13)
        self.assertAlmostEqual(run(C._score_sys_02, valid_doc())[0], 2 / 13)
        doc = valid_doc()
        remove(doc, "metadata/TEA_currency")
        score, _m, missing = run(C._score_sys_02, doc)
        self.assertAlmostEqual(score, 1 / 13)
        self.assertEqual(missing[0], "metadata.TEA_currency")
        self.assertTrue(any("stream_prices" in m for m in missing))

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
        5/7; QU-01 pass -> 1, fail -> 0; none -> None."""
        self.assertEqual(C._bare_design_results(ctx(legacy_doc())),
                         ["units[U1].design_results.Flow rate"])
        score, _m, missing = run(C._score_int_04, legacy_doc())
        self.assertAlmostEqual(score, 5 / 7)
        self.assertEqual(missing, ["streams[0].stream_properties.pressure.units",
                                   "units[U1].design_results.Flow rate (bare "
                                   "number, no units)"])
        self.assertEqual(run(C._score_int_04, valid_doc(), [check("QU-01", "pass")])[0], 1.0)
        self.assertEqual(run(C._score_int_04, valid_doc(), [check("QU-01", "fail")])[0], 0.0)
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
        0.1.2) and CMP-THM-04 (null) not representable, CMP-THM-01 is; an
        inapplicable criterion -> status not_applicable, score None."""
        registry = C.load_completeness_criteria()["criteria"]
        results = {r.criterion_id: r for r in
                   C._evaluate_criteria(ctx(valid_doc()), registry, (0, 0, 3))}
        self.assertFalse(results["CMP-COST-04"].representable)
        self.assertFalse(results["CMP-THM-04"].representable)
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


# ------------------------------------ need-based applicability (R1.0)

PASSING = (check("SCHEMA", "pass"), check("XREF-01", "pass"), check("QU-01", "pass"))
RICH_NAMES = {"Water", "Glucose", "Ethanol", "CO2"}


def overall(doc):
    """Overall score of `doc` from the criterion functions (validator
    replaced by passing SCHEMA / XREF-01 / QU-01 results)."""
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
    passes of Glucose -> 2 Ethanol + 2 CO2) -> prod. unit_types are
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
             {"T": 305.0, "tau": 10.0, "V_wf": 0.8}, "Reactor",
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
            "flowsheet_model_package": {"name": "biorefineries", "commit": "def"}}}
    return {"metadata": metadata, "units": units, "streams": streams,
            "chemicals": chemicals, "utilities": utilities}


# Keys that carry identity or references rather than values: deleting them
# rewires the document (a stream's endpoints, a composition's species, an
# element's id) instead of removing one piece of stated information.
_STRUCTURAL_KEYS = frozenset({"id", "source_unit_id", "sink_unit_id",
                              "component_name", "stream_id"})


def deletable_paths(node, path=()):
    """Yield (path, kind) for every value held under a dict key, skipping
    _STRUCTURAL_KEYS: kind 'leaf' for a scalar, 'sub' for a whole dict or
    list (a unit's design_input_specs or thermo_property_package, a stream's
    stream_properties, one phase, a composition list, a correlation, ...,
    up to a top-level section). List elements are recursed into, never
    deleted themselves (that removes a unit, stream or reaction)."""
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
        not_applicable: every chemical resolves), and the overall score is
        high but below 1 (non-representable criteria)."""
        with name_lookup(RICH_NAMES):
            results = {r.criterion_id: r for r in C._evaluate_criteria(
                ctx(rich_doc(), PASSING),
                C.load_completeness_criteria()["criteria"], None)}
            score = overall(rich_doc())
        na = {cid for cid, r in results.items() if r.status == "not_applicable"}
        self.assertEqual(na, {"CMP-THM-05"})
        self.assertEqual(results["CMP-FEED-01"].score, 1.0)
        # 'waste' unpriced; the empty 'spare' feed counts as 1 (4 of 5).
        self.assertEqual(results["CMP-FEED-02"].score, 0.8)
        self.assertEqual(results["CMP-RXN-03"].score, 1.0)
        # C1 (custom, splitter weight 2) 0.75; P1, H1, R1 core -> 1.
        self.assertAlmostEqual(results["CMP-COST-04"].score, 7.5 / 8)
        self.assertEqual(results["CMP-COST-07"].score, 1.0)
        # R1 (reactor weight 3) names no chemical set; 6 of 9 weight do.
        self.assertAlmostEqual(results["CMP-THM-07"].score, 6 / 9)
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
        port-order derivation (_port_order_derivable) is blocked too."""
        tenfold = ({"mass": 460.7}, {"mass": 460.7})
        self.assertEqual(spec_score("Splitter", outs=({}, {}))[0], 0.75)
        score, missing = spec_score("Splitter", outs=tenfold)
        self.assertEqual(score, 0.0)
        self.assertIn("derivation from stream data unavailable: mass balance",
                      missing[0])
        self.assertEqual(spec_score("HXutility")[0], 0.75)
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
        reactions; on a mixer they do not even make it reactive (N/A)."""
        for reactions in ([{}], [{"equation": "x", "reactant": "y",
                                  "conversion": 0.5}]):
            junked = rxn_doc(reactions)
            for cid in ("CMP-RXN-01", "CMP-RXN-02", "CMP-RXN-03", "CMP-RXN-04"):
                self.assertEqual(criterion_results(junked)[cid].score, 0.0, cid)
            self.assert_no_credit(rxn_doc(None), junked, (
                "CMP-RXN-01", "CMP-RXN-02", "CMP-RXN-03", "CMP-RXN-04"))
            mixer = unit_doc({"reactions": reactions})
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
        derivation (UOP-02 0 for the unit), where a balanced one earns
        DERIVED_CREDIT."""
        self.assertEqual(spec_score("Splitter", outs=(
            {"mass": 460.7}, {"mass": 460.7}))[0], 0.0)
        self.assertEqual(spec_score("Splitter", outs=({}, {}))[0],
                         C.DERIVED_CREDIT)

    def test_formula_ids_get_no_verified_name_credit(self):
        """ids 'HP' (declared MW 90.08; the database's 'HP' is
        hematoporphyrin, MW 598.69) and 'C2H6O' (resolves to dimethyl ether)
        get no verified name credit even though the name search resolves
        them: formula tier (0.25) at most, and both stay in THM-05."""
        with name_lookup({"HP": (598.68872, "C34H38N4O6"),
                          "C2H6O": (46.06844, "C2H6O")}):
            for chem in ({"id": "HP", "molar_mass": 90.08},
                         {"id": "C2H6O", "formula": "C2H6O"}):
                credit, _note = C._thm_02_credit(chem, chem["id"])
                self.assertLessEqual(credit, C.FORMULA_ONLY_CREDIT, chem)
                self.assertIsNone(C._name_tier(chem, chem["id"]))
            doc = valid_doc()
            doc["chemicals"] = [{"id": "HP", "molar_mass": 90.08},
                                {"id": "C2H6O", "formula": "C2H6O"}]
            self.assertEqual(run(C._score_thm_05, doc)[0], 0.0)


if __name__ == "__main__":
    unittest.main()
