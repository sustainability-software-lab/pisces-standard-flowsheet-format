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


if __name__ == "__main__":
    unittest.main()
