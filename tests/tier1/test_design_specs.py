# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: pisces_sff/_design_specs.py -- the design-input-spec registry
# machinery, exercised with FAKE unit classes only. The module is import-light
# by design (no package-relative imports, yaml lazy), so it is loaded here by
# file path -- importing the pisces_sff package would drag in biosteam.
# Real-object re-verification lives in tests/tier2/test_export_helpers_real.py.

import importlib.util
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "pisces_sff"


def load_design_specs():
    spec = importlib.util.spec_from_file_location(
        "sff_design_specs_under_test", PKG / "_design_specs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ds = load_design_specs()


def entry(params):
    """Registry entry shorthand: params = {name: [accessor, ...]}."""
    return {"line": "Fake", "design_input_spec_params":
            {p: {"accessors": list(a)} for p, a in params.items()}}


class FakeStream:
    def __init__(self, P=None):
        self.P = P


class FakeUnitBase:
    pass


class FakePump(FakeUnitBase):
    def __init__(self, P=None, outs=()):
        self.P = P
        self.outs = list(outs)
        self.material = "Cast iron"


class FakePumpSubclass(FakePump):
    pass


class TestParseAccessor(unittest.TestCase):
    def test_plain_attribute(self):
        """'P' parses to a single un-indexed step."""
        self.assertEqual(_ds.parse_accessor("P"), [("P", None)])

    def test_indexed_then_dotted(self):
        """'outs[0].P' parses to an indexed step then a plain step."""
        self.assertEqual(_ds.parse_accessor("outs[0].P"),
                         [("outs", 0), ("P", None)])

    def test_malformed_accessors_raise_value_error(self):
        """Non-path strings (calls, negative/expression indexes, leading dots,
        empty) are rejected -- the resolver must never see them."""
        for bad in ("outs[0].P()", "outs[-1].P", "outs[i].P", ".P", "",
                    "a..b", "a[0", "import os"):
            with self.subTest(accessor=bad):
                with self.assertRaises(ValueError):
                    _ds.parse_accessor(bad)


class TestResolveDesignInputSpecs(unittest.TestCase):
    def test_first_accessor_wins_when_non_none(self):
        """A set P is exported from the first accessor; the fallback is not
        consulted."""
        unit = FakePump(P=2.0e6, outs=[FakeStream(P=101325.0)])
        registry = {"FakePump": entry({"P": ["P", "outs[0].P"]})}
        self.assertEqual(_ds.resolve_design_input_specs(unit, registry),
                         {"P": 2.0e6})

    def test_none_falls_through_to_next_accessor_under_the_param_name(self):
        """A None first accessor falls through; the fallback's value is
        exported under the PARAM name ('P'), not the accessor path."""
        unit = FakePump(P=None, outs=[FakeStream(P=101325.0)])
        registry = {"FakePump": entry({"P": ["P", "outs[0].P"]})}
        self.assertEqual(_ds.resolve_design_input_specs(unit, registry),
                         {"P": 101325.0})

    def test_all_accessors_exhausted_omits_the_param(self):
        """When every accessor yields None the param is omitted entirely --
        no null values in the export."""
        unit = FakePump(P=None, outs=[FakeStream(P=None)])
        registry = {"FakePump": entry({"P": ["P", "outs[0].P"]})}
        self.assertEqual(_ds.resolve_design_input_specs(unit, registry), {})

    def test_missing_attribute_and_index_are_tolerated(self):
        """AttributeError and IndexError mean 'not available' -> next
        accessor / omit, never an exception."""
        unit = FakePump(P=None, outs=[])  # outs[0] -> IndexError
        registry = {"FakePump": entry({"P": ["P", "outs[0].P"],
                                       "tau": ["tau"]})}  # no .tau attr
        self.assertEqual(_ds.resolve_design_input_specs(unit, registry), {})

    def test_mro_walk_finds_nearest_listed_ancestor(self):
        """An unlisted subclass resolves via its listed base class's entry,
        used as-is."""
        unit = FakePumpSubclass(P=None, outs=[FakeStream(P=3.0e5)])
        registry = {"FakePump": entry({"P": ["P", "outs[0].P"]})}
        self.assertEqual(_ds.resolve_design_input_specs(unit, registry),
                         {"P": 3.0e5})

    def test_nearest_ancestor_entry_shadows_farther_ones(self):
        """When both the class and a base are listed, the class's own entry
        wins and the base's is NOT merged in."""
        unit = FakePumpSubclass(P=None, outs=[FakeStream(P=3.0e5)])
        registry = {
            "FakePumpSubclass": entry({"material": ["material"]}),
            "FakePump": entry({"P": ["P", "outs[0].P"]}),
        }
        self.assertEqual(_ds.resolve_design_input_specs(unit, registry),
                         {"material": "Cast iron"})

    def test_unmapped_unit_type_exports_empty(self):
        """No entry via any ancestor -> {} (user decision)."""
        self.assertEqual(
            _ds.resolve_design_input_specs(FakeUnitBase(), {}), {})

    def test_unexpected_read_error_raises_DesignSpecReadError(self):
        """A read failing with anything but AttributeError/IndexError/KeyError
        raises DesignSpecReadError chaining the cause (the exporter wraps it
        in DesignInputSpecError)."""
        class Boom:
            @property
            def P(self):
                raise ValueError("boom")

        registry = {"Boom": entry({"P": ["P"]})}
        with self.assertRaises(_ds.DesignSpecReadError) as caught:
            _ds.resolve_design_input_specs(Boom(), registry)
        self.assertIsInstance(caught.exception.__cause__, ValueError)


class TestLoadDesignSpecRegistry(unittest.TestCase):
    # NOTE: accessors containing [i] must be QUOTED in YAML flow style --
    # '[' inside a plain scalar is invalid in flow context.
    GOOD = (
        "Pump:\n"
        "  line: Pump\n"
        "  design_input_spec_params:\n"
        "    P:\n"
        "      accessors: [P, 'outs[0].P']\n"
        "    material:\n"
        "      accessors: [material]\n"
    )

    def _load(self, text):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "reg.yaml"
            p.write_text(text, encoding="utf-8")
            return _ds.load_design_spec_registry(p)

    def test_well_formed_registry_loads(self):
        """A well-formed file loads into the documented mapping shape."""
        reg = self._load(self.GOOD)
        self.assertEqual(
            reg["Pump"]["design_input_spec_params"]["P"]["accessors"],
            ["P", "outs[0].P"])
        self.assertEqual(reg["Pump"]["line"], "Pump")

    def test_missing_file_raises_value_error(self):
        """A nonexistent path raises ValueError naming the path."""
        with self.assertRaises(ValueError):
            _ds.load_design_spec_registry(
                Path("no_such_dir") / "no_such.yaml")

    def test_invalid_yaml_raises_value_error(self):
        """Unparseable YAML raises ValueError, not a bare yaml error."""
        with self.assertRaises(ValueError):
            self._load("Pump: [unclosed\n")

    def test_missing_line_raises_value_error(self):
        """An entry without 'line' is rejected."""
        with self.assertRaises(ValueError):
            self._load("Pump:\n  design_input_spec_params: {}\n")

    def test_missing_params_mapping_raises_value_error(self):
        """An entry without 'design_input_spec_params' is rejected."""
        with self.assertRaises(ValueError):
            self._load("Pump:\n  line: Pump\n")

    def test_unknown_entry_key_raises_value_error(self):
        """A stray key inside an entry is rejected (typo protection)."""
        with self.assertRaises(ValueError):
            self._load("Pump:\n  line: Pump\n  design_input_spec_params: {}\n"
                       "  extra: 1\n")

    def test_malformed_accessor_raises_value_error(self):
        """A syntactically bad accessor is rejected at LOAD time."""
        with self.assertRaises(ValueError):
            self._load("Pump:\n  line: Pump\n  design_input_spec_params:\n"
                       "    P:\n      accessors: ['outs[-1].P']\n")

    def test_empty_accessor_list_raises_value_error(self):
        """A param with no accessors can never resolve -- rejected."""
        with self.assertRaises(ValueError):
            self._load("Pump:\n  line: Pump\n  design_input_spec_params:\n"
                       "    P:\n      accessors: []\n")

    def test_empty_params_mapping_is_allowed(self):
        """design_input_spec_params: {} is valid (a unit type with no design
        input specs)."""
        reg = self._load("Mixer:\n  line: Mixer\n"
                         "  design_input_spec_params: {}\n")
        self.assertEqual(reg["Mixer"]["design_input_spec_params"], {})


class TestParamsFromClass(unittest.TestCase):
    def test_prefers_init_underscore_over_dunder_init(self):
        """biosteam units declare per-type params on _init (Unit.__init__ is
        generic plumbing); _params_from_class must introspect _init when
        present, excluding self and VAR_* params."""
        class WithInit:
            def __init__(self, ID='', ins=None, outs=(), thermo=None,
                         **kwargs):
                pass

            def _init(self, P=None, material='Cast iron', *args, **kwargs):
                pass

        self.assertEqual(_ds._params_from_class(WithInit), ["P", "material"])

    def test_falls_back_to_dunder_init_excluding_plumbing(self):
        """Without _init, __init__ is swept minus self/ID/ins/outs/thermo."""
        class NoInit:
            def __init__(self, ID='', ins=None, outs=(), thermo=None,
                         tau=8.0, V_wf=0.9):
                pass

        self.assertEqual(_ds._params_from_class(NoInit), ["tau", "V_wf"])


class TestGeneratedEntry(unittest.TestCase):
    def test_entry_records_line_and_default_accessors(self):
        """The generated entry carries the class's line and one same-named
        accessor per param."""
        class Fermenter:
            line = "Fermentation"

            def _init(self, tau=None, T=305.15):
                pass

        self.assertEqual(_ds._generated_entry(Fermenter), {
            "line": "Fermentation",
            "design_input_spec_params": {
                "tau": {"accessors": ["tau"]},
                "T": {"accessors": ["T"]},
            },
        })

    def test_missing_line_falls_back_to_class_name(self):
        """A class without a truthy `line` uses its __name__."""
        class Bare:
            def _init(self, x=1):
                pass

        self.assertEqual(_ds._generated_entry(Bare)["line"], "Bare")


class TestMergeDesignSpecEntries(unittest.TestCase):
    def test_new_class_is_appended(self):
        existing = {"A": {"line": "A", "design_input_spec_params": {}}}
        generated = {"B": {"line": "B", "design_input_spec_params": {}}}
        merged = _ds.merge_design_spec_entries(existing, generated)
        self.assertEqual(list(merged), ["A", "B"])

    def test_existing_entries_are_preserved_verbatim(self):
        """Regeneration must never clobber a hand-curated accessor list,
        overwrite line, or resurrect a hand-pruned param. The merge is
        entry-atomic: it cannot distinguish "the curator pruned this" from
        "the simulator just added this", so a listed class keeps exactly the
        param set the committed file gives it. (Task 4 curation depends on
        this: e.g. BinaryDistillation's condenser_thermo/reboiler_thermo/
        check_LHK and NRELAnaerobicBatchBioreactor's reactions are pruned and
        must stay pruned across regeneration.)"""
        existing = {"Pump": {
            "line": "Pump (curated)",
            "design_input_spec_params": {
                "P": {"accessors": ["P", "outs[0].P"]},
            },
        }}
        generated = {"Pump": {
            "line": "Pump",
            "design_input_spec_params": {
                "P": {"accessors": ["P"]},          # must NOT clobber
                "dP_design": {"accessors": ["dP_design"]},  # pruned by hand
            },
        }}
        merged = _ds.merge_design_spec_entries(existing, generated)
        self.assertEqual(merged["Pump"]["line"], "Pump (curated)")
        self.assertEqual(
            merged["Pump"]["design_input_spec_params"],
            {"P": {"accessors": ["P", "outs[0].P"]}})

    def test_regeneration_is_idempotent_over_a_curated_entry(self):
        """Merging twice changes nothing -- the property the committed
        registry's Step 5 stability check rests on."""
        existing = {"Pump": {
            "line": "Pump",
            "design_input_spec_params": {"P": {"accessors": ["P"]}},
        }}
        generated = {"Pump": {
            "line": "Pump",
            "design_input_spec_params": {
                "P": {"accessors": ["P"]},
                "check_LHK": {"accessors": ["check_LHK"]},
            },
        }}
        once = _ds.merge_design_spec_entries(existing, generated)
        twice = _ds.merge_design_spec_entries(once, generated)
        self.assertEqual(once, existing)
        self.assertEqual(twice, once)

    def test_inputs_are_not_mutated(self):
        """merge returns a new mapping; both inputs are left untouched."""
        existing = {"A": {"line": "A", "design_input_spec_params":
                          {"x": {"accessors": ["x"]}}}}
        generated = {"A": {"line": "A2", "design_input_spec_params":
                           {"y": {"accessors": ["y"]}}}}
        import copy
        existing_before = copy.deepcopy(existing)
        generated_before = copy.deepcopy(generated)
        _ds.merge_design_spec_entries(existing, generated)
        self.assertEqual(existing, existing_before)
        self.assertEqual(generated, generated_before)


class TestGenerateDesignSpecRegistry(unittest.TestCase):
    class FakeMixer:
        line = "Mixer"

        def _init(self, rigorous=False):
            pass

    def test_writes_a_loadable_registry_for_injected_classes(self):
        """generate(classes=[...]) writes a file load_design_spec_registry
        accepts, with LF endings (repo .gitattributes pin)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "biosteam.yaml"
            written = _ds.generate_design_spec_registry(
                path=out, classes=[self.FakeMixer])
            self.assertEqual(written, out)
            raw = out.read_bytes()
            self.assertNotIn(b"\r\n", raw)
            reg = _ds.load_design_spec_registry(out)
            self.assertEqual(
                reg["FakeMixer"]["design_input_spec_params"]["rigorous"],
                {"accessors": ["rigorous"]})

    def test_regeneration_merges_with_an_existing_file(self):
        """Running generate over an existing file preserves curated content
        (merge semantics, exercised through the file-level entry point)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "biosteam.yaml"
            out.write_text(
                "FakeMixer:\n  line: Mixer\n  design_input_spec_params:\n"
                "    rigorous:\n      accessors: [rigorous, 'outs[0].P']\n",
                encoding="utf-8")
            _ds.generate_design_spec_registry(
                path=out, classes=[self.FakeMixer])
            reg = _ds.load_design_spec_registry(out)
            self.assertEqual(
                reg["FakeMixer"]["design_input_spec_params"]["rigorous"]
                ["accessors"],
                ["rigorous", "outs[0].P"])


class TestCommittedRegistry(unittest.TestCase):
    """The committed pisces_sff/design_specs/biosteam.yaml itself."""

    @classmethod
    def setUpClass(cls):
        cls.registry = _ds.load_design_spec_registry()  # committed file

    def test_committed_registry_loads_clean(self):
        """The committed file passes load-time validation and is non-trivial
        (the all-of-biosteam sweep yields far more than the corpus classes)."""
        self.assertGreater(len(self.registry), 25)

    def test_pump_and_molecular_sieve_carry_the_outs0_P_fallback(self):
        """The user-approved curation: a P-unset Pump/MolecularSieve exports
        outs[0].P under the same 'P' key (no new SFF keys)."""
        for class_name in ("Pump", "MolecularSieve"):
            with self.subTest(class_name=class_name):
                accessors = (self.registry[class_name]
                             ["design_input_spec_params"]["P"]["accessors"])
                self.assertEqual(accessors, ["P", "outs[0].P"])

    def test_registry_file_has_lf_endings(self):
        """.gitattributes pins LF; the generated file must comply at rest."""
        self.assertNotIn(b"\r\n", _ds.REGISTRY_PATH.read_bytes())

    def test_hand_pruned_params_stay_absent(self):
        """Deliberate curation protection: the entry-atomic merge in
        merge_design_spec_entries only protects a hand-pruned param against
        REGENERATION (it never re-adds a param to a class that is already
        listed) -- it does nothing to stop a careless hand-edit of the
        committed YAML from re-adding one. Pin the pruned params absent
        directly against the committed file so such an edit fails loudly:
        BinaryDistillation had condenser_thermo/reboiler_thermo/check_LHK
        pruned (it has no condenser/reboiler sub-thermo or LHK-checking
        knobs worth exporting), and NRELAnaerobicBatchBioreactor had
        reactions pruned (the reaction set is a model detail, not a design
        input)."""
        bd_params = (self.registry["BinaryDistillation"]
                     ["design_input_spec_params"])
        for pruned in ("condenser_thermo", "reboiler_thermo", "check_LHK"):
            with self.subTest(param=pruned):
                self.assertNotIn(pruned, bd_params)

        nrel_params = (self.registry["NRELAnaerobicBatchBioreactor"]
                       ["design_input_spec_params"])
        self.assertNotIn("reactions", nrel_params)


if __name__ == "__main__":
    unittest.main()
