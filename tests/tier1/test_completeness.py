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
    """valid_doc with U1 turned into the given unit (feed -> U1 -> prod)."""
    doc = valid_doc()
    unit = {"id": "U1", "unit_type": "Mixer"}
    unit.update(unit_fields or {})
    if specs is not None:
        unit["design_input_specs"] = specs
    doc["units"][0] = unit
    sp = doc["streams"][1]["stream_properties"]
    if out_T is None:
        del sp["temperature"]
    if out_P is None:
        del sp["pressure"]
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
        COST-04 since 0.1.2 (purchase_cost_correlations), SYS-04 since 0.0.6
        (reproducibility packages); all others since 0.0.0."""
        since = {cid: e["representable_since"]
                 for cid, e in self.registry["criteria"].items()}
        self.assertEqual(
            {c for c, v in since.items() if v is None},
            {"CMP-THM-04", "CMP-THM-05", "CMP-TOP-02", "CMP-RXN-04",
             "CMP-COST-05", "CMP-COST-06", "CMP-SYS-01", "CMP-SYS-02",
             "CMP-SYS-03"})
        self.assertEqual(since["CMP-COST-04"], "0.1.2")
        self.assertEqual(since["CMP-SYS-04"], "0.0.6")

    def test_exclusions_listed(self):
        """exclusions -> EXC-01..03 (not_capturable) + DER-01..03 (derived)."""
        kinds = {e["id"]: e["kind"] for e in self.registry["exclusions"]}
        self.assertEqual(kinds, {"EXC-01": "not_capturable",
                                 "EXC-02": "not_capturable",
                                 "EXC-03": "not_capturable",
                                 "DER-01": "derived", "DER-02": "derived",
                                 "DER-03": "derived"})

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
        """_entry_fraction: mol_fraction, else mass_fraction, else None;
        _composition_ok: non-empty with numeric fractions; _phases_ok: a
        zero-flow empty phase is fine, a missing phase molar flow is not."""
        self.assertEqual(C._entry_fraction({"mol_fraction": 0.2}), 0.2)
        self.assertEqual(C._entry_fraction({"mass_fraction": 0.3}), 0.3)
        self.assertIsNone(C._entry_fraction({"component_name": "A"}))
        self.assertTrue(C._composition_ok([{"mol_fraction": 1.0}]))
        self.assertFalse(C._composition_ok([]))
        good = {"total_molar_flow": 1.0, "composition": [{"mol_fraction": 1.0}]}
        self.assertTrue(C._phases_ok({"l": good,
                                      "g": {"total_molar_flow": 0.0,
                                            "composition": []}}))
        self.assertFalse(C._phases_ok({"l": {"composition": [{"mol_fraction": 1}]}}))
        self.assertFalse(C._phases_ok({}))

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

class TestUnitClass(unittest.TestCase):
    def test_normalized_texts(self):
        """_normalized_texts('GrainHandling') -> ('grainhandling', 'grain
        handling'); underscores become spaces."""
        self.assertEqual(C._normalized_texts("GrainHandling"),
                         ("grainhandling", "grain handling"))
        self.assertEqual(C._normalized_texts("A_B")[1], "a b")

    def test_keyword_family(self):
        """_keyword_family on unit_type text / class names -> the expected
        family; ordering resolves 'Fire water tank' to facility, 'Screw press'
        to splitter, 'Mix tank' to tank; unknown text -> None."""
        cases = {"Heat exchanger": "hx_utility", "HXprocess": "hx_process",
                 "Preheater": "hx_utility", "Fire water tank": "facility",
                 "Screw press": "splitter", "Mix tank": "tank",
                 "Hammer mill": "solids_handling", "Fermentation": "reactor",
                 "SSF": "reactor", "Distillation Column": "distillation",
                 "Vacuum pump": "pump", "Flash tank": "flash",
                 "Boiler turbogenerator": "facility", "Mixer": "mixer",
                 "CIPpackage": "facility", "AnMBR": "reactor",
                 "Isentropic turbine": "turbine", "Valve": "valve",
                 "Multi-effect evaporator": "evaporator",
                 "Compressor": "compressor"}
        for text, family in cases.items():
            self.assertEqual(C._keyword_family(text), family, text)
        self.assertIsNone(C._keyword_family("Widget"))
        self.assertIsNone(C._keyword_family(None))

    def test_class_family(self):
        """_class_family: curated table first (JetCooker -> hx_utility,
        EnzymeHydrolysateMixer -> mixer), then keywords (StorageTank -> tank)."""
        self.assertEqual(C._class_family("JetCooker"), "hx_utility")
        self.assertEqual(C._class_family("EnzymeHydrolysateMixer"), "mixer")
        self.assertEqual(C._class_family("StorageTank"), "tank")
        self.assertIsNone(C._class_family("Unit"))

    def test_unit_class_paths(self):
        """_unit_class: 'Class on <url>' -> (class, family, 1.0); unit_type
        naming a known class -> 1.0; keyword-only unit_type -> (None, family,
        0.5); nothing -> (None, None, 0.0)."""
        self.assertEqual(C._unit_class(
            {"design_simulation_method": "GrainHandling on https://x/units.py"}),
            ("GrainHandling", "solids_handling", 1.0))
        self.assertEqual(C._unit_class({"unit_type": "Pump"}, {"Pump"}),
                         ("Pump", "pump", 1.0))
        self.assertEqual(C._unit_class({"unit_type": "Heat exchanger"}),
                         (None, "hx_utility", 0.5))
        self.assertEqual(C._unit_class({"unit_type": "Widget"}), (None, None, 0.0))
        self.assertEqual(C._unit_class("garbage"), (None, None, 0.0))


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
        the registry class Mixer (confidence 1.0), labels by id."""
        sc = ctx(valid_doc())
        self.assertEqual((sc.feeds, sc.outlets), ([0], [1]))
        self.assertEqual(sc.unit_classes[0], ("Mixer", "mixer", 1.0))
        self.assertEqual(sc.stream_labels, ["streams[feed]", "streams[prod]"])
        self.assertEqual(sc.chem_by_index, {0: "Ethanol"})

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


# ------------------------------------------------ operating-spec rules

class TestSpecRules(unittest.TestCase):
    def test_explicit_keys(self):
        """_explicit_keys: non-null design_input_specs keys only."""
        self.assertEqual(C._explicit_keys(
            {"design_input_specs": {"T": 300, "V": None}}), {"T"})
        self.assertEqual(C._explicit_keys({}), set())

    def test_condition_holds(self):
        """_condition_holds: outlet_T/outlet_P/all_ports_state read stream
        states; explicit:<k> reads specs; design_result:<n> reads results;
        an unknown condition -> ValueError."""
        doc = unit_doc({"design_results": {"Reflux": 2.0}})
        sc = ctx(doc)
        unit = sc.units[0]
        for cond in ("outlet_T", "outlet_P", "all_ports_state",
                     "design_result:Reflux"):
            self.assertTrue(C._condition_holds(cond, sc, unit, set()), cond)
        self.assertTrue(C._condition_holds("explicit:LHK", sc, unit, {"LHK"}))
        self.assertFalse(C._condition_holds("explicit:LHK", sc, unit, set()))
        with self.assertRaises(ValueError):
            C._condition_holds("bogus", sc, unit, set())

    def test_requirement_score(self):
        """_requirement_score: explicit -> 1; derivable only -> 0.75; flash
        n_of(2) with 1 explicit + T derivable -> (1 + 0.75) / 2."""
        sc = ctx(unit_doc())
        unit = sc.units[0]
        hx = C._FAMILY_RULES["hx_utility"][0]
        self.assertEqual(C._requirement_score(hx, sc, unit, {"V"}), 1.0)
        self.assertEqual(C._requirement_score(hx, sc, unit, set()), C.DERIVED_CREDIT)
        flash = C._FAMILY_RULES["flash"][0]
        self.assertAlmostEqual(C._requirement_score(flash, sc, unit, {"Q"}),
                               (1 + C.DERIVED_CREDIT) / 2)

    def test_requirement_locator(self):
        """_requirement_locator -> 'units[F1].design_input_specs: 2 of
        T|P|V|Q|y|x', with a derivable note when partially credited."""
        flash = C._FAMILY_RULES["flash"][0]
        self.assertEqual(C._requirement_locator("units[F1]", flash, 0.0),
                         "units[F1].design_input_specs: 2 of T|P|V|Q|y|x")
        self.assertIn("only derivable",
                      C._requirement_locator("units[F1]", flash, 0.5))

    def test_unit_spec_score(self):
        """_unit_spec_score: HXutility with T -> (1.0, []); unknown family ->
        (0.0, ['...unit family unknown...'])."""
        sc = ctx(unit_doc(cls("HXutility"), {"T": 350.0}))
        self.assertEqual(C._unit_spec_score(sc, 0), (1.0, []))
        sc = ctx(unit_doc({"unit_type": "Widget"}))
        score, missing = C._unit_spec_score(sc, 0)
        self.assertEqual(score, 0.0)
        self.assertIn("unit family unknown", missing[0])

    def test_excused_params(self):
        """_excused_params: HXutility with T -> {V, H}; flash with 2 of 6 ->
        the other 4; unsatisfied requirement -> nothing excused."""
        self.assertEqual(C._excused_params("hx_utility", {"T"}), {"V", "H"})
        self.assertEqual(C._excused_params("flash", {"T", "P"}),
                         {"V", "Q", "y", "x"})
        self.assertEqual(C._excused_params("flash", {"T"}), set())


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

    def test_thm_02_identity(self):
        """_score_thm_02 (CMP-THM-02): formula only -> 0.5; registry_id ->
        1.0; neither -> 0; no chemicals -> None (not applicable)."""
        self.assertEqual(run(C._score_thm_02, valid_doc())[0], 0.5)
        doc = valid_doc()
        mutate(doc, "chemicals/0/registry_id", "64-17-5")
        self.assertEqual(run(C._score_thm_02, doc)[0], 1.0)
        remove(doc, "chemicals/0/registry_id")
        remove(doc, "chemicals/0/formula")
        self.assertEqual(run(C._score_thm_02, doc)[0], 0.0)
        self.assertIsNone(run(C._score_thm_02, {})[0])

    def test_thm_03_molar_mass(self):
        """_score_thm_03 (CMP-THM-03): declared -> 1; formula-derived only ->
        1; neither -> 0 with a molar_mass locator."""
        doc = valid_doc()
        self.assertEqual(run(C._score_thm_03, doc)[0], 1.0)
        remove(doc, "chemicals/0/molar_mass")
        self.assertEqual(run(C._score_thm_03, doc)[0], 1.0)
        remove(doc, "chemicals/0/formula")
        score, _m, missing = run(C._score_thm_03, doc)
        self.assertEqual(score, 0.0)
        self.assertIn("chemicals[Ethanol].molar_mass", missing[0])

    def test_thm_04_phase_behaviour(self):
        """_score_thm_04 / _chemical_phases (CMP-THM-04): Ethanol only in 'l'
        -> 0; also seen in 'g' -> DERIVED_CREDIT; only ever 's' ->
        DERIVED_CREDIT."""
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

    def test_thm_05_non_database_property_data(self):
        """_score_thm_05 (CMP-THM-05): a chemical without registry id -> 0;
        all with registry ids -> None; no chemicals -> None."""
        self.assertEqual(run(C._score_thm_05, valid_doc())[0], 0.0)
        doc = valid_doc()
        mutate(doc, "chemicals/0/registry_id", "64-17-5")
        self.assertIsNone(run(C._score_thm_05, doc)[0])
        self.assertIsNone(run(C._score_thm_05, {})[0])

    def test_thm_06_property_package(self):
        """_score_thm_06 (CMP-THM-06): no package -> 0 with 4 locators; half
        stated -> 0.5; full -> 1; no units -> None."""
        score, _m, missing = run(C._score_thm_06, valid_doc())
        self.assertEqual((score, len(missing)), (0.0, 4))
        doc = valid_doc()
        doc["units"][0]["thermo_property_package"] = {"mixture": "m", "gamma": "g"}
        self.assertEqual(run(C._score_thm_06, doc)[0], 0.5)
        doc["units"][0]["thermo_property_package"].update(phi="p", PCF="c")
        self.assertEqual(run(C._score_thm_06, doc)[0], 1.0)
        self.assertIsNone(run(C._score_thm_06, {})[0])

    def test_thm_07_multi_thermo_identity(self):
        """_score_thm_07 (CMP-THM-07): one package -> None; two distinct
        packages, one unit naming its id -> 0.5."""
        doc = valid_doc()
        doc["units"][0]["thermo_property_package"] = {"mixture": "a"}
        self.assertIsNone(run(C._score_thm_07, doc)[0])
        doc["units"].append({"id": "U2", "unit_type": "Mixer",
                             "thermo_property_package": {"mixture": "b",
                                                         "id": "set2"}})
        score, _m, missing = run(C._score_thm_07, doc)
        self.assertEqual(score, 0.5)
        self.assertEqual(missing, ["units[U1].thermo_property_package.id"])


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
        """_score_top_02 (CMP-TOP-02): single-stream sides -> 1; a second
        feed into U1 -> 0.5 with an 'ins' locator; no units -> None."""
        self.assertEqual(run(C._score_top_02, valid_doc())[0], 1.0)
        doc = valid_doc()
        doc["streams"].append(dict(copy.deepcopy(doc["streams"][0]), id="feed2"))
        score, _m, missing = run(C._score_top_02, doc)
        self.assertEqual(score, 0.5)
        self.assertEqual(missing, ["units[U1].ins: order of 2 streams not "
                                   "representable"])
        self.assertIsNone(run(C._score_top_02, {})[0])

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

    def test_feed_02_prices(self):
        """_score_feed_02 / _is_empty_stream (CMP-FEED-02): unpriced boundary
        streams -> 0; both priced (0 counts) -> 1; zero-flow boundary
        streams only -> None."""
        doc = valid_doc()
        self.assertEqual(run(C._score_feed_02, doc)[0], 0.0)
        mutate(doc, "streams/0/price", 0.5)
        mutate(doc, "streams/1/price", 0)
        self.assertEqual(run(C._score_feed_02, doc)[0], 1.0)
        for s in doc["streams"]:
            s["stream_properties"]["total_mass_flow"] = 0.0
        self.assertTrue(C._is_empty_stream(doc["streams"][0]))
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

    def test_uop_02_essential_specs(self):
        """_score_uop_02 (CMP-UOP-02): mixer only -> None; HXutility with T ->
        1; HXutility with only an outlet T -> DERIVED_CREDIT; unknown family ->
        0."""
        self.assertIsNone(run(C._score_uop_02, valid_doc())[0])
        self.assertEqual(run(C._score_uop_02,
                             unit_doc(cls("HXutility"), {"T": 350.0}))[0], 1.0)
        score, _m, missing = run(C._score_uop_02, unit_doc(cls("HXutility"), {}))
        self.assertEqual(score, C.DERIVED_CREDIT)
        self.assertIn("one of T|V|H (only derivable", missing[0])
        self.assertEqual(run(C._score_uop_02, unit_doc({"unit_type": "Widget"}))[0], 0.0)

    def test_uop_03_full_coverage(self):
        """_score_uop_03 (CMP-UOP-03): registry HXutility {T, V, H, U} with T
        set -> V and H excused, U missing -> 0.5; a class absent from the
        registry -> None; a registry load error -> RuntimeError (driver turns
        it into score 0)."""
        registry = {"HXutility": {"line": "HX", "design_input_spec_params": {
            p: {"accessors": [p]} for p in ("T", "V", "H", "U")}}}
        doc = unit_doc(cls("HXutility"), {"T": 350.0})
        score, _m, missing = run(C._score_uop_03, doc, registry=registry)
        self.assertEqual(score, 0.5)
        self.assertEqual(missing, ["units[U1].design_input_specs.U"])
        self.assertIsNone(run(C._score_uop_03, unit_doc(cls("Other")),
                              registry=registry)[0])
        sc = ctx(doc, registry=registry)
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


# -------------------------------------------------------- reaction criteria

def reaction(**fields):
    r = {"index": 0, "equation": "Ethanol -> Ethanol", "reactant": "Ethanol",
         "conversion": 0.5}
    r.update(fields)
    return r


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
        self.assertIn("none declared (CO2 appears)", missing[0])
        doc = self._appearing_doc({"reactions": [reaction()]})
        self.assertEqual(run(C._score_rxn_01, doc)[0], 1.0)

    def test_rxn_02_definitions(self):
        """_score_rxn_02 (CMP-RXN-02): complete -> 1; conversion 1.5 -> 2/3
        with a conversion locator; no reactions -> None."""
        self.assertEqual(run(C._score_rxn_02, unit_doc({"reactions": [reaction()]}))[0], 1.0)
        score, _m, missing = run(C._score_rxn_02, unit_doc(
            {"reactions": [reaction(conversion=1.5)]}))
        self.assertAlmostEqual(score, 2 / 3)
        self.assertEqual(missing, ["units[U1].reactions[0].conversion (0..1)"])
        self.assertIsNone(run(C._score_rxn_02, valid_doc())[0])

    def test_rxn_03_series_parallel(self):
        """_score_rxn_03 (CMP-RXN-03): two indexed reactions -> 1; one index
        missing -> 0; a single reaction -> None."""
        two = [reaction(index=0), reaction(index=1)]
        self.assertEqual(run(C._score_rxn_03, unit_doc({"reactions": two}))[0], 1.0)
        del two[1]["index"]
        score, _m, missing = run(C._score_rxn_03, unit_doc({"reactions": two}))
        self.assertEqual((score, missing), (0.0, ["units[U1].reactions[1].index"]))
        self.assertIsNone(run(C._score_rxn_03, unit_doc({"reactions": [reaction()]}))[0])

    def test_rxn_04_basis(self):
        """_score_rxn_04 (CMP-RXN-04): reactions present -> 0 (not
        representable); none -> None."""
        self.assertEqual(run(C._score_rxn_04, unit_doc({"reactions": [reaction()]}))[0], 0.0)
        self.assertIsNone(run(C._score_rxn_04, valid_doc())[0])


# ---------------------------------------------------------- costing criteria

def costed(corr=None, results=None, units_map=None, installed=True):
    unit = {"purchase_costs": {"Tank": 1000.0}}
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

    def test_cost_01_purchase_costs(self):
        """_score_cost_01 (CMP-COST-01): no purchase_costs -> 0; {} -> 1."""
        self.assertEqual(run(C._score_cost_01, valid_doc())[0], 0.0)
        self.assertEqual(run(C._score_cost_01, unit_doc({"purchase_costs": {}}))[0], 1.0)

    def test_cost_02_installed(self):
        """_score_cost_02 (CMP-COST-02): installed counterpart -> 1; missing
        -> 0; no items -> None."""
        self.assertEqual(run(C._score_cost_02, costed())[0], 1.0)
        score, _m, missing = run(C._score_cost_02, costed(installed=False))
        self.assertEqual((score, missing), (0.0, ["units[U1].installed_costs['Tank']"]))
        self.assertIsNone(run(C._score_cost_02, valid_doc())[0])

    def test_cost_03_design_result_units(self):
        """_score_cost_03 / _has_quantity_unit (CMP-COST-03): no results -> 0;
        unit declared -> 1; unitless numeric -> 0.5; legacy inline units ->
        1; a string result needs no unit."""
        self.assertEqual(run(C._score_cost_03, valid_doc())[0], 0.0)
        self.assertEqual(run(C._score_cost_03, costed(results={"Volume": 5},
                                                      units_map={"Volume": "m3"}))[0], 1.0)
        self.assertEqual(run(C._score_cost_03, costed(results={"Volume": 5}))[0], 0.5)
        self.assertEqual(run(C._score_cost_03, costed(
            results={"Volume": {"value": 5, "units": "m3"}, "Type": "x"}))[0], 1.0)
        self.assertTrue(C._has_quantity_unit(5.0, ""))
        self.assertFalse(C._has_quantity_unit(5.0, None))

    def test_cost_04_correlations(self):
        """_score_cost_04 (CMP-COST-04): power_law -> 1; custom_function ->
        0.5; none -> 0."""
        self.assertEqual(run(C._score_cost_04, costed(POWER_LAW))[0], 1.0)
        self.assertEqual(run(C._score_cost_04, costed(
            {"correlation_type": "custom_function"}))[0], 0.5)
        self.assertEqual(run(C._score_cost_04, costed())[0], 0.0)

    def test_cost_05_cepci_back_derivation(self):
        """_score_cost_05 / _back_derivable (CMP-COST-05): complete power-law
        item + sized basis -> DERIVED_CREDIT; no exponent -> 0; no items ->
        None."""
        doc = costed(POWER_LAW, results={"Volume": 12.0})
        self.assertEqual(run(C._score_cost_05, doc)[0], C.DERIVED_CREDIT)
        unit = doc["units"][0]
        self.assertTrue(C._back_derivable(unit, "Tank", 1000.0))
        self.assertFalse(C._back_derivable(unit, "Tank", 0.0))
        corr = dict(POWER_LAW)
        del corr["exponent"]
        self.assertEqual(run(C._score_cost_05, costed(corr, {"Volume": 12.0}))[0], 0.0)
        self.assertIsNone(run(C._score_cost_05, valid_doc())[0])

    def test_cost_06_parallel_count(self):
        """_score_cost_06 (CMP-COST-06): a power-law item -> 0; none ->
        None."""
        self.assertEqual(run(C._score_cost_06, costed(POWER_LAW))[0], 0.0)
        self.assertIsNone(run(C._score_cost_06, costed())[0])


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

    def test_util_01_resolution(self):
        """_score_util_01 (CMP-UTIL-01): nothing -> None; referenced but
        undeclared -> 0; declared complete + referenced -> 1; declared
        without composition -> 2/3."""
        self.assertIsNone(run(C._score_util_01, valid_doc())[0])
        score, _m, missing = run(C._score_util_01, with_agents(consumption={"steam": 1}))
        self.assertEqual(score, 0.0)
        self.assertIn("no agent 'steam' (referenced by units[U1])", missing[0])
        self.assertEqual(run(C._score_util_01, with_agents(
            [STEAM], consumption={"steam": 1}))[0], 1.0)
        self.assertAlmostEqual(run(C._score_util_01, with_agents(
            [dict(STEAM, composition=[])]))[0], 2 / 3)

    def test_util_02_prices(self):
        """_score_util_02 (CMP-UTIL-02): priced heat agent and legacy-priced
        power agent -> 1; unpriced other agent -> 1/3 overall."""
        doc = with_agents([STEAM], [{"id": "grid", "price": 0.07}],
                          [{"id": "gas"}])
        score, _m, missing = run(C._score_util_02, doc)
        self.assertAlmostEqual(score, 2 / 3)
        self.assertEqual(missing, ["utilities.other_utilities[gas].price"])
        self.assertIsNone(run(C._score_util_02, valid_doc())[0])

    def test_util_03_heat_agent_limits(self):
        """_score_util_03 (CMP-UTIL-03): condensing (all 'g') + efficiency ->
        1; liquid agent without temperature_limit -> 0.5; none -> None."""
        self.assertEqual(run(C._score_util_03, with_agents([STEAM]))[0], 1.0)
        water = dict(STEAM, composition=[{"phase": "l", "component_name": "Water",
                                          "mol_fraction": 1.0}])
        self.assertEqual(run(C._score_util_03, with_agents([water]))[0], 0.5)
        self.assertIsNone(run(C._score_util_03, valid_doc())[0])

    def test_util_04_results(self):
        """_score_util_04 (CMP-UTIL-04): no result objects -> 0; both -> 1."""
        self.assertEqual(run(C._score_util_04, valid_doc())[0], 0.0)
        doc = unit_doc({"utility_consumption_results": {},
                        "utility_production_results": {}})
        self.assertEqual(run(C._score_util_04, doc)[0], 1.0)


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
        """_score_sys_02 (CMP-SYS-02): currency + year -> 2/12; without
        currency -> 1/12 with a metadata.TEA_currency locator."""
        self.assertAlmostEqual(run(C._score_sys_02, valid_doc())[0], 2 / 12)
        doc = valid_doc()
        remove(doc, "metadata/TEA_currency")
        score, _m, missing = run(C._score_sys_02, doc)
        self.assertAlmostEqual(score, 1 / 12)
        self.assertEqual(missing[0], "metadata.TEA_currency")

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

    def test_int_03_semantic(self):
        """_score_int_03 (CMP-INT-03): error pass (1) + warning fail (0.5),
        info and skips ignored -> 1 / 1.5; nothing executed -> None."""
        results = [check("SCHEMA", "fail"), check("UNIT-01", "pass"),
                   check("STR-08", "fail", "warning", "sum"),
                   check("CHEM-05", "fail", "info"), check("STR-03", "skip")]
        score, _m, missing = run(C._score_int_03, valid_doc(), results)
        self.assertAlmostEqual(score, 1 / 1.5)
        self.assertEqual(missing, ["STR-08 (warning) at <root>: sum"])
        self.assertIsNone(run(C._score_int_03, valid_doc(), [check("SCHEMA", "pass")])[0])

    def test_int_04_quantity_units(self):
        """_score_int_04 (CMP-INT-04): legacy inline -> fraction with units
        (1 empty of 6 -> 5/6); QU-01 pass -> 1, fail -> 0; none -> None."""
        score, _m, missing = run(C._score_int_04, legacy_doc())
        self.assertAlmostEqual(score, 5 / 6)
        self.assertEqual(missing, ["streams[0].stream_properties.pressure.units"])
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

    def test_format_completeness_report(self):
        """format_completeness_report: header, every criterion id, category
        titles; max_missing=1 -> '... and N more' tails; max_missing=0 lists
        no locators."""
        text = C.format_completeness_report(self.report, max_missing=1)
        self.assertIn("SFF completeness report", text)
        for cid in C._CRITERIA:
            self.assertIn(cid, text)
        self.assertIn("Thermodynamic basis", text)
        self.assertIn("more", text)
        self.assertNotIn("      - ", C.format_completeness_report(self.report, 0))

    def test_report_to_jsonable(self):
        """_report_to_jsonable -> json.dumps-able dict with criteria as dicts."""
        data = json.loads(json.dumps(C._report_to_jsonable(self.report)))
        self.assertEqual(data["criteria"][0]["criterion_id"], "CMP-THM-01")
        self.assertIn("check_id", data["validation_results"][0])

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
            self.assertIn("score", json.loads(out.getvalue()))


if __name__ == "__main__":
    unittest.main()
