# -*- coding: utf-8 -*-
# Unit tests for pisces_sff/_quantity_units.py — the pure quantity-unit helpers.
#
# Import-light by construction: the module under test imports no biosteam, and
# we load it by file path (like tests/tier1/test_version.py loads _version.py) so
# that importing the pisces_sff package — and thus _export/biosteam — never
# happens here.

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "pisces_sff" / "_quantity_units.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_quantity_units_under_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Fake:
    """Stand-in for a BioSTEAM unit: only the attributes the helper reads."""
    def __init__(self, design_results=None, units=None):
        if design_results is not None:
            self.design_results = design_results
        if units is not None:
            self._units = units


class TestScalar(unittest.TestCase):
    def setUp(self):
        self.m = load_module()

    def test_inline_returns_value_units_pair(self):
        self.assertEqual(self.m.scalar(5.0, "K", True),
                         {"value": 5.0, "units": "K"})

    def test_non_inline_returns_bare_value(self):
        self.assertEqual(self.m.scalar(5.0, "K", False), 5.0)


class TestVersionStyle(unittest.TestCase):
    def setUp(self):
        self.m = load_module()

    def test_version_tuple_parses_semver(self):
        self.assertEqual(self.m.version_tuple("0.0.7"), (0, 0, 7))

    def test_pre_0_0_7_is_inline(self):
        self.assertTrue(self.m.uses_inline_scalar_style("0.0.5"))
        self.assertTrue(self.m.uses_inline_scalar_style("0.0.6"))

    def test_0_0_7_and_later_are_not_inline(self):
        self.assertFalse(self.m.uses_inline_scalar_style("0.0.7"))
        self.assertFalse(self.m.uses_inline_scalar_style("0.1.0"))


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.m = load_module()

    def test_every_entry_has_nonempty_aliases_and_a_unit_string(self):
        for key, entry in self.m.QUANTITY_UNITS_GLOBAL.items():
            with self.subTest(quantity=key):
                self.assertIsInstance(entry["aliases"], list)
                self.assertTrue(entry["aliases"])
                self.assertTrue(all(isinstance(a, str) for a in entry["aliases"]))
                self.assertIsInstance(entry["quantity_units"], str)
                self.assertTrue(entry["quantity_units"])

    def test_canonical_units_are_biosteam_native(self):
        reg = self.m.QUANTITY_UNITS_GLOBAL
        self.assertEqual(reg["temperature"]["quantity_units"], "K")
        self.assertEqual(reg["pressure"]["quantity_units"], "Pa")
        self.assertEqual(reg["mass_flow"]["quantity_units"], "kg/hr")
        self.assertEqual(reg["molar_flow"]["quantity_units"], "kmol/hr")
        self.assertEqual(reg["volumetric_flow"]["quantity_units"], "m3/hr")
        self.assertEqual(reg["molar_mass"]["quantity_units"], "g/mol")
        self.assertEqual(reg["price"]["quantity_units"], "USD/kg")
        self.assertEqual(reg["electrical_energy_price"]["quantity_units"], "USD/kWh")
        self.assertEqual(reg["regeneration_price"]["quantity_units"], "USD/kmol")
        self.assertEqual(reg["heat_transfer_price"]["quantity_units"], "USD/kJ")
        self.assertEqual(reg["enthalpy_flow"]["quantity_units"], "kJ/hr")

    def test_aliases_cover_biosteam_attribute_names(self):
        reg = self.m.QUANTITY_UNITS_GLOBAL
        self.assertIn("T", reg["temperature"]["aliases"])
        self.assertIn("temperature_limit", reg["temperature"]["aliases"])
        self.assertIn("total_mass_flow", reg["mass_flow"]["aliases"])
        self.assertIn("F_mass", reg["mass_flow"]["aliases"])
        self.assertIn("total_molar_flow", reg["molar_flow"]["aliases"])
        self.assertIn("total_volumetric_flow", reg["volumetric_flow"]["aliases"])
        self.assertIn("MW", reg["molar_mass"]["aliases"])
        self.assertIn("H", reg["enthalpy_flow"]["aliases"])


class TestRegistryForVersion(unittest.TestCase):
    """quantity_units_global_for gates version-introduced entries so older
    exporters reproduce their historical registry byte-for-byte."""

    def setUp(self):
        self.m = load_module()

    def test_0_0_11_includes_enthalpy_flow(self):
        self.assertIn("enthalpy_flow", self.m.quantity_units_global_for("0.0.11"))

    def test_pre_0_0_11_omits_enthalpy_flow(self):
        for version in ("0.0.7", "0.0.9", "0.0.10"):
            with self.subTest(version=version):
                self.assertNotIn(
                    "enthalpy_flow", self.m.quantity_units_global_for(version))

    def test_always_present_entries_are_kept_at_every_version(self):
        # Entries not listed in _QUANTITY_INTRODUCED_SINCE predate the registry
        # (0.0.7) and must appear regardless of the requested version.
        for version in ("0.0.7", "0.0.11"):
            reg = self.m.quantity_units_global_for(version)
            for key in ("temperature", "pressure", "mass_flow", "price"):
                with self.subTest(version=version, quantity=key):
                    self.assertIn(key, reg)

    def test_preserves_registry_insertion_order(self):
        full = list(self.m.QUANTITY_UNITS_GLOBAL)
        got = list(self.m.quantity_units_global_for("0.0.11"))
        self.assertEqual(got, [k for k in full if k in got])


class TestDesignResultUnits(unittest.TestCase):
    def setUp(self):
        self.m = load_module()

    def test_maps_each_design_key_to_its_unit_or_empty_string(self):
        unit = _Fake(design_results={"Area": 10.0, "Duty": 5.0},
                     units={"Area": "m^2"})
        self.assertEqual(
            self.m.quantity_units_for_design_results(unit),
            {"Area": "m^2", "Duty": ""},
        )

    def test_unit_without_design_results_yields_empty_dict(self):
        self.assertEqual(self.m.quantity_units_for_design_results(_Fake()), {})


if __name__ == "__main__":
    unittest.main()
