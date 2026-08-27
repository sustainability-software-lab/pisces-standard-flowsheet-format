# -*- coding: utf-8 -*-
# Tier 2: exporter version-dispatch guard. Exports one small REAL System at
# 0.0.6, 0.0.7, 0.0.8, 0.0.9, 0.0.10, 0.0.11, 0.0.12, 0.1.0, 0.1.1, 0.1.2,
# 0.1.3, 0.1.4, 0.1.5, and 0.2.0 and asserts the scalar-shape, results-key,
# required-metadata, stream-roles, enthalpy-flow, tightened-constraint (0.0.12
# shape-identical to 0.0.11), milestone-bump (0.1.0 shape-identical to
# 0.0.12), constraint-loosening (0.1.1 shape-identical to 0.1.0 -- CHEM-02's
# molar_mass constraint moved from the schema to the validator, a
# validation-only change with no output effect), new-field (0.1.2 adds the
# optional per-unit purchase_cost_correlations object, present -- possibly
# empty -- on every unit, otherwise shape-identical to 0.1.1),
# conditional-tag-stamping (0.1.3 adds the optional metadata.tags field,
# stamped ["exported-from-simulator"] only when earned -- the recipe-less
# small fixture earns nothing, so its 0.1.3 export is otherwise
# shape-identical to 0.1.2), and design-spec-semantics (0.1.4 changes
# design_input_specs semantics -- registry-driven, per-type params, ordered
# accessor fallbacks, None omitted; otherwise shape-identical to 0.1.3), and
# validation-vocabulary-only bump (0.1.5 adds the extracted-from-table tag to
# the enum/registry; the exporter never stamps extracted-from-* tags, so the
# 0.1.5 export is shape-identical to 0.1.4), and milestone bump (0.2.0 marks
# the validate//export/ package restructure; no schema shape or constraint
# change, so the 0.2.0 export is shape-identical to 0.1.5)
# differences the schema versions require. This is about exporter version
# dispatch, not the corn model, so it needs no whole-model simulation --
# which is why it lives in Tier 2 rather than Tier 3.
#
# All asserted shapes are verified from a real export run:
#   0.0.9 -> per-phase stream structure (stream_properties.phases keyed by
#            phase symbol, each phase with its own totals + composition); this
#            is the only version whose export validates against the committed
#            (0.0.9) schema.
#   0.0.8 -> like 0.0.7, plus the now-required metadata.TEA_currency ("USD");
#            keeps the flat per-component-phase composition, so it no longer
#            validates against the committed schema.
#   0.0.7 -> bare-number scalars, a quantity_units_global registry, and the
#            renamed quantity_units_for_utility_results key; omits TEA_currency.
#   0.0.6 -> inline {"value","units"} scalars, NO registry, and the legacy
#            units_for_utility_results key; omits TEA_currency.
#
# Gated on RUN_TIER2 (default on).

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests._gating import RUN_TIER2
from tests._real_objects import build_small_system_and_tea
from tests._stub_eviction import RealBiosteamTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "pisces_sff" / "schema" / "sff_schema.json"


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestVersionShapeGuard(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()   # evicts Tier-1 biosteam/thermosteam stubs

        # If Tier 1 already ran in this same pytest process, its collection-time
        # tests._fakes.load_export() call imported the real `pisces_sff.export._export`
        # module WHILE the fake biosteam stub was installed, permanently binding
        # that module's top-level `import biosteam as bst` to the fake object.
        # Evicting sys.modules['biosteam']/['thermosteam'] above does not touch
        # that already-bound name -- Python only re-resolves a module-level
        # import on a fresh import, and 'pisces_sff.export._export' is already cached.
        # Discard the whole pisces_sff package tree so the import below
        # re-executes against the (now real, just-evicted) biosteam/thermosteam;
        # sys.modules['biosteam']/['thermosteam'] themselves are left untouched,
        # so this does not force a second real import of the simulator itself.
        for key in [k for k in sys.modules
                    if k == "pisces_sff" or k.startswith("pisces_sff.")]:
            del sys.modules[key]

        from pisces_sff import _export
        from pisces_sff.validate._validate import validate_json_against_schema

        cls.validate = staticmethod(validate_json_against_schema)
        system, _H1, tea = build_small_system_and_tea()
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)

        cls.path_007 = tmp / "small_007.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_007), sff_version="0.0.7", tea=tea)
        cls.doc_007 = json.loads(cls.path_007.read_text(encoding="utf-8"))

        cls.path_006 = tmp / "small_006.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_006), sff_version="0.0.6", tea=tea)
        cls.doc_006 = json.loads(cls.path_006.read_text(encoding="utf-8"))

        cls.path_008 = tmp / "small_008.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_008), sff_version="0.0.8", tea=tea)
        cls.doc_008 = json.loads(cls.path_008.read_text(encoding="utf-8"))

        cls.path_009 = tmp / "small_009.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_009), sff_version="0.0.9", tea=tea)
        cls.doc_009 = json.loads(cls.path_009.read_text(encoding="utf-8"))

        cls.path_010 = tmp / "small_010.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_010), sff_version="0.0.10", tea=tea,
            source_doi="10.0000/small-fixture",
            process_title="Small fixture process",
            flowsheet_designers="Fixture Author",
            microorganisms=[{"name": "Saccharomyces cerevisiae",
                             "label": "ethanologen"}])
        cls.doc_010 = json.loads(cls.path_010.read_text(encoding="utf-8"))

        cls.path_011 = tmp / "small_011.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_011), sff_version="0.0.11", tea=tea)
        cls.doc_011 = json.loads(cls.path_011.read_text(encoding="utf-8"))

        cls.path_012 = tmp / "small_012.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_012), sff_version="0.0.12", tea=tea)
        cls.doc_012 = json.loads(cls.path_012.read_text(encoding="utf-8"))

        cls.path_100 = tmp / "small_100.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_100), sff_version="0.1.0", tea=tea)
        cls.doc_100 = json.loads(cls.path_100.read_text(encoding="utf-8"))

        cls.path_101 = tmp / "small_101.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_101), sff_version="0.1.1", tea=tea)
        cls.doc_101 = json.loads(cls.path_101.read_text(encoding="utf-8"))

        cls.path_102 = tmp / "small_102.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_102), sff_version="0.1.2", tea=tea)
        cls.doc_102 = json.loads(cls.path_102.read_text(encoding="utf-8"))

        cls.path_103 = tmp / "small_103.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_103), sff_version="0.1.3", tea=tea)
        cls.doc_103 = json.loads(cls.path_103.read_text(encoding="utf-8"))

        cls.path_104 = tmp / "small_104.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_104), sff_version="0.1.4", tea=tea)
        cls.doc_104 = json.loads(cls.path_104.read_text(encoding="utf-8"))

        cls.path_105 = tmp / "small_105.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_105), sff_version="0.1.5", tea=tea)
        cls.doc_105 = json.loads(cls.path_105.read_text(encoding="utf-8"))

        cls.path_200 = tmp / "small_200.json"
        _export.export_biosteam_flowsheet(
            system, str(cls.path_200), sff_version="0.2.0", tea=tea)
        cls.doc_200 = json.loads(cls.path_200.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_0_0_7_uses_bare_number_scalars(self):
        """v0.0.7 export of a real small System -> stream temperature and price are bare int/float scalars, not inline {value, units}."""
        self.assertEqual(self.doc_007["metadata"]["sff_version"], "0.0.7")
        sp = self.doc_007["streams"][0]["stream_properties"]
        self.assertIsInstance(sp["temperature"], (int, float))
        self.assertIsInstance(self.doc_007["streams"][0]["price"], (int, float))

    def test_0_0_7_has_the_global_registry(self):
        """v0.0.7 export -> document carries a top-level quantity_units_global registry."""
        self.assertIn("quantity_units_global", self.doc_007)

    def test_0_0_7_units_carry_design_result_quantity_units(self):
        """v0.0.7 export -> every unit carries a quantity_units_for_design_results map."""
        self.assertTrue(
            all("quantity_units_for_design_results" in u
                for u in self.doc_007["units"])
        )

    def test_0_0_7_heat_utilities_use_the_renamed_results_key(self):
        """v0.0.7 export -> heat utilities use quantity_units_for_utility_results, not the legacy units_for_utility_results key."""
        for hu in self.doc_007["utilities"]["heat_utilities"]:
            self.assertIn("quantity_units_for_utility_results", hu)
            self.assertNotIn("units_for_utility_results", hu)

    def test_0_0_6_uses_inline_scalars(self):
        """v0.0.6 export of the same real small System -> stream temperature is the legacy inline {value, units} pair shape."""
        self.assertEqual(self.doc_006["metadata"]["sff_version"], "0.0.6")
        sp = self.doc_006["streams"][0]["stream_properties"]
        self.assertIn("value", sp["temperature"])
        self.assertIn("units", sp["temperature"])

    def test_0_0_6_has_no_global_registry(self):
        """v0.0.6 export -> document does NOT carry a quantity_units_global registry (0.0.7+ only)."""
        self.assertNotIn("quantity_units_global", self.doc_006)

    def test_0_0_6_heat_utilities_use_the_legacy_results_key(self):
        """v0.0.6 export -> heat utilities use the legacy units_for_utility_results key, not the renamed quantity_units_for_utility_results."""
        for hu in self.doc_006["utilities"]["heat_utilities"]:
            self.assertIn("units_for_utility_results", hu)
            self.assertNotIn("quantity_units_for_utility_results", hu)

    def test_0_0_8_emits_tea_currency_and_flat_composition(self):
        """v0.0.8 export -> metadata.TEA_currency is "USD" and stream_properties still uses flat "composition" (no "phases" yet, that lands at 0.0.9)."""
        # 0.0.8 predates the per-phase stream restructuring, so its export no
        # longer validates against the committed (0.0.9) schema; assert only its
        # own shape here and let the 0.0.9 tests own schema validation.
        self.assertEqual(self.doc_008["metadata"]["sff_version"], "0.0.8")
        self.assertEqual(self.doc_008["metadata"]["TEA_currency"], "USD")
        sp = self.doc_008["streams"][0]["stream_properties"]
        self.assertIn("composition", sp)
        self.assertNotIn("phases", sp)

    def test_0_0_9_validates_against_committed_schema(self):
        """v0.0.9 export of the real small System -> validates against the committed (0.0.9-shaped) schema with no errors."""
        is_valid, errors = self.validate(str(self.path_009), str(SCHEMA_PATH))
        self.assertTrue(is_valid, f"validation errors: {errors[:5]}")
        self.assertEqual(self.doc_009["metadata"]["sff_version"], "0.0.9")

    def test_0_0_9_uses_per_phase_stream_structure(self):
        """v0.0.9 export -> stream_properties drops flat "composition" for a non-empty per-phase "phases" dict (keyed by symbol, e.g. "l"), each phase carrying its own composition without a "phase" field, while whole-stream totals are retained."""
        sp = self.doc_009["streams"][0]["stream_properties"]
        # Flat composition is gone; phases is a non-empty object keyed by symbol.
        self.assertNotIn("composition", sp)
        phases = sp["phases"]
        self.assertIsInstance(phases, dict)
        self.assertTrue(phases)
        # The small fixture is a single liquid-phase system.
        self.assertIn("l", phases)
        for symbol, phase in phases.items():
            with self.subTest(phase=symbol):
                self.assertIn("total_molar_flow", phase)
                self.assertIsInstance(phase["composition"], list)
                for component in phase["composition"]:
                    self.assertNotIn("phase", component)
        # Whole-stream totals are retained alongside the per-phase ones.
        self.assertIn("total_mass_flow", sp)
        self.assertIn("total_molar_flow", sp)

    def test_pre_0_0_8_versions_omit_tea_currency(self):
        """v0.0.6 and v0.0.7 exports -> metadata omits TEA_currency (required only from 0.0.8; older exporters stay byte-stable)."""
        # The field is required only from 0.0.8; older exporters must stay
        # byte-stable and therefore not emit it.
        self.assertNotIn("TEA_currency", self.doc_007["metadata"])
        self.assertNotIn("TEA_currency", self.doc_006["metadata"])

    def test_0_0_10_validates_against_committed_schema(self):
        """v0.0.10 export of the real small System -> validates against the committed schema with no errors."""
        is_valid, errors = self.validate(str(self.path_010), str(SCHEMA_PATH))
        self.assertTrue(is_valid, f"validation errors: {errors[:5]}")
        self.assertEqual(self.doc_010["metadata"]["sff_version"], "0.0.10")

    def test_0_0_10_emits_roles_matching_topology(self):
        """v0.0.10 export -> every stream carries a non-empty "roles" list with exactly one base topology role (input/output/internal) that agrees with its source_unit_id/sink_unit_id."""
        for stream in self.doc_010["streams"]:
            with self.subTest(stream=stream["id"]):
                roles = stream["roles"]
                self.assertIsInstance(roles, list)
                self.assertTrue(roles)
                # Exactly one base topology role, and it agrees with source/sink.
                base = [r for r in roles
                        if r in ("input", "output", "internal")]
                self.assertEqual(len(base), 1)
                has_source = stream["source_unit_id"] != "None"
                has_sink = stream["sink_unit_id"] != "None"
                if has_source and has_sink:
                    self.assertEqual(base[0], "internal")
                elif has_sink:
                    self.assertEqual(base[0], "input")
                else:
                    self.assertEqual(base[0], "output")

    def test_0_0_10_emits_designation_roles(self):
        """v0.0.10 export -> the sole priced feed's roles are ["input", "purchased_raw_material", "feedstock"] and the priced outlet's roles are ["output", "product"], reflecting real topology and pricing."""
        # The small fixture's priced feed (Ethanol-bearing, the sole system feed)
        # is both a purchased raw material and the feedstock; its heated outlet is
        # a priced product. This pins get_stream_roles's designation branches --
        # purchased_raw_material, feedstock, product, and the input co-occurrence
        # of purchased_raw_material with feedstock -- against real exported data,
        # not just the base topology role.
        input_stream = next(s for s in self.doc_010["streams"]
                            if s["source_unit_id"] == "None")
        output_stream = next(s for s in self.doc_010["streams"]
                             if s["sink_unit_id"] == "None")
        self.assertEqual(input_stream["roles"],
                         ["input", "purchased_raw_material", "feedstock"])
        self.assertEqual(output_stream["roles"], ["output", "product"])

    def test_pre_0_0_10_versions_omit_roles(self):
        """v0.0.9 down to v0.0.6 exports -> no stream carries a "roles" field (emitted only from 0.0.10 on; older exporters stay byte-stable)."""
        # roles is emitted only from 0.0.10; older exporters must stay
        # byte-stable and therefore not emit it.
        for doc in (self.doc_009, self.doc_008, self.doc_007, self.doc_006):
            for stream in doc["streams"]:
                self.assertNotIn("roles", stream)

    def test_0_0_10_emits_authored_metadata(self):
        """v0.0.10 export, given source_doi/process_title/flowsheet_designers/microorganisms kwargs -> metadata carries those values verbatim."""
        md = self.doc_010["metadata"]
        self.assertEqual(md["source_doi"], "10.0000/small-fixture")
        self.assertEqual(md["process_title"], "Small fixture process")
        self.assertEqual(md["flowsheet_designers"], "Fixture Author")
        self.assertEqual(md["microorganisms"][0]["name"],
                         "Saccharomyces cerevisiae")

    def test_pre_0_0_10_versions_omit_authored_metadata(self):
        """v0.0.9 down to v0.0.6 exports (never passed the authored-metadata kwargs) -> metadata omits source_doi/process_title/flowsheet_designers."""
        # The three authored fields are passed only to the 0.0.10 export; the
        # shared 0.0.6-0.0.9 exports never receive them (their exporters do not
        # accept them, per design D3), so they must be absent there.
        for doc in (self.doc_009, self.doc_008, self.doc_007, self.doc_006):
            self.assertNotIn("source_doi", doc["metadata"])
            self.assertNotIn("process_title", doc["metadata"])
            self.assertNotIn("flowsheet_designers", doc["metadata"])

    def test_0_0_11_validates_against_committed_schema(self):
        """v0.0.11 export of the real small System -> validates against the committed schema with no errors."""
        is_valid, errors = self.validate(str(self.path_011), str(SCHEMA_PATH))
        self.assertTrue(is_valid, f"validation errors: {errors[:5]}")
        self.assertEqual(self.doc_011["metadata"]["sff_version"], "0.0.11")

    def test_0_0_11_emits_numeric_enthalpy_flow_on_every_stream(self):
        """v0.0.11 export -> every stream's stream_properties carries a numeric "enthalpy_flow" field."""
        for stream in self.doc_011["streams"]:
            with self.subTest(stream=stream["id"]):
                self.assertIn("enthalpy_flow", stream["stream_properties"])
                self.assertIsInstance(
                    stream["stream_properties"]["enthalpy_flow"], (int, float))

    def test_0_0_11_registry_carries_the_enthalpy_flow_entry(self):
        """v0.0.11 export -> quantity_units_global registers an "enthalpy_flow" entry."""
        self.assertIn("enthalpy_flow",
                      self.doc_011["quantity_units_global"])

    def test_pre_0_0_11_versions_omit_enthalpy_flow(self):
        """v0.0.10 down to v0.0.7 exports -> no stream's stream_properties nor quantity_units_global carries "enthalpy_flow" (emitted only from 0.0.11 on)."""
        # enthalpy_flow is emitted only from 0.0.11; older exporters must stay
        # byte-stable and therefore not emit it -- neither on the stream nor in
        # the shared quantity_units_global registry.
        for doc in (self.doc_010, self.doc_009, self.doc_008, self.doc_007):
            self.assertNotIn("enthalpy_flow", doc["quantity_units_global"])
            for stream in doc["streams"]:
                self.assertNotIn(
                    "enthalpy_flow", stream["stream_properties"])

    def test_0_0_12_validates_against_committed_schema(self):
        """v0.0.12 export of the real small System -> validates against the committed (tightened-constraint) schema with no errors."""
        is_valid, errors = self.validate(str(self.path_012), str(SCHEMA_PATH))
        self.assertTrue(is_valid, f"validation errors: {errors[:5]}")
        self.assertEqual(self.doc_012["metadata"]["sff_version"], "0.0.12")

    def test_0_0_12_is_shape_identical_to_0_0_11_except_version(self):
        """v0.0.12 export vs v0.0.11 export, with both metadata.sff_version fields normalized -> the two documents are equal (0.0.12 only tightens schema constraints, emitting no shape change)."""
        # v0.0.12 only tightens schema constraints; the emitted document is
        # byte-identical to the 0.0.11 export apart from metadata.sff_version.
        a = copy.deepcopy(self.doc_011)
        b = copy.deepcopy(self.doc_012)
        a["metadata"]["sff_version"] = b["metadata"]["sff_version"] = "X"
        self.assertEqual(a, b)

    def test_0_1_0_validates_against_committed_schema(self):
        """v0.1.0 export of the real small System -> validates against the committed schema with no errors, and records metadata.sff_version "0.1.0"."""
        is_valid, errors = self.validate(str(self.path_100), str(SCHEMA_PATH))
        self.assertTrue(is_valid, f"validation errors: {errors[:5]}")
        self.assertEqual(self.doc_100["metadata"]["sff_version"], "0.1.0")

    def test_0_1_0_is_shape_identical_to_0_0_12_except_version(self):
        """v0.1.0 export vs v0.0.12 export, with both metadata.sff_version fields normalized -> the two documents are equal (0.1.0 is a milestone bump, emitting no shape change)."""
        # v0.1.0 introduces no schema-shape or constraint changes; the emitted
        # document is byte-identical to the 0.0.12 export apart from
        # metadata.sff_version.
        a = copy.deepcopy(self.doc_012)
        b = copy.deepcopy(self.doc_100)
        a["metadata"]["sff_version"] = b["metadata"]["sff_version"] = "X"
        self.assertEqual(a, b)

    def test_0_1_1_validates_against_committed_schema(self):
        """v0.1.1 export of the real small System -> validates against the committed schema with no errors, and records metadata.sff_version "0.1.1"."""
        is_valid, errors = self.validate(str(self.path_101), str(SCHEMA_PATH))
        self.assertTrue(is_valid, f"validation errors: {errors[:5]}")
        self.assertEqual(self.doc_101["metadata"]["sff_version"], "0.1.1")

    def test_0_1_1_is_shape_identical_to_0_1_0_except_version(self):
        """v0.1.1 export vs v0.1.0 export, with both metadata.sff_version fields normalized -> the two documents are equal (0.1.1 only loosens a schema constraint -- CHEM-02's molar_mass check moved to the validator -- emitting no shape change)."""
        # v0.1.1 drops the schema's exclusiveMinimum:0 on molar_mass (now a
        # validator warning); that is a validation-only change, so the emitted
        # document is byte-identical to the 0.1.0 export apart from
        # metadata.sff_version.
        a = copy.deepcopy(self.doc_100)
        b = copy.deepcopy(self.doc_101)
        a["metadata"]["sff_version"] = b["metadata"]["sff_version"] = "X"
        self.assertEqual(a, b)

    def test_0_1_2_validates_against_committed_schema(self):
        """v0.1.2 export of the real small System -> validates against the
        committed schema with no errors, and records metadata.sff_version
        "0.1.2"."""
        is_valid, errors = self.validate(str(self.path_102), str(SCHEMA_PATH))
        self.assertTrue(is_valid, f"validation errors: {errors[:5]}")
        self.assertEqual(self.doc_102["metadata"]["sff_version"], "0.1.2")

    def test_0_1_2_emits_purchase_cost_correlations_on_every_unit(self):
        """v0.1.2 export -> every unit carries a purchase_cost_correlations dict
        (empty for the procedurally-costed HXutility fixture unit; the key's
        presence is what the gate guarantees)."""
        for unit in self.doc_102["units"]:
            with self.subTest(unit=unit["id"]):
                self.assertIn("purchase_cost_correlations", unit)
                self.assertIsInstance(unit["purchase_cost_correlations"], dict)

    def test_0_1_2_is_shape_identical_to_0_1_1_except_correlations_and_version(self):
        """v0.1.2 export with purchase_cost_correlations stripped from every unit
        and metadata.sff_version normalized -> equals the v0.1.1 export (0.1.2
        adds only that one optional field)."""
        a = copy.deepcopy(self.doc_101)
        b = copy.deepcopy(self.doc_102)
        for unit in b["units"]:
            unit.pop("purchase_cost_correlations", None)
        a["metadata"]["sff_version"] = b["metadata"]["sff_version"] = "X"
        self.assertEqual(a, b)

    def test_pre_0_1_2_versions_omit_purchase_cost_correlations(self):
        """v0.1.1 down to v0.0.6 exports -> no unit carries a
        purchase_cost_correlations field (emitted only from 0.1.2 on; older
        exporters stay byte-stable)."""
        for doc in (self.doc_101, self.doc_100, self.doc_012, self.doc_011,
                    self.doc_010, self.doc_009, self.doc_008, self.doc_007,
                    self.doc_006):
            for unit in doc["units"]:
                self.assertNotIn("purchase_cost_correlations", unit)

    def test_0_1_3_validates_against_committed_schema(self):
        """v0.1.3 export of the real small System -> validates against the
        committed schema; records metadata.sff_version "0.1.3"."""
        is_valid, errors = self.validate(str(self.path_103), str(SCHEMA_PATH))
        self.assertTrue(is_valid, f"validation errors: {errors[:5]}")
        self.assertEqual(self.doc_103["metadata"]["sff_version"], "0.1.3")

    def test_0_1_3_recipeless_fixture_earns_no_tags(self):
        """v0.1.3 export of the small fixture (exported WITHOUT a reproducibility
        recipe) -> no metadata.tags: exported-from-simulator is not earned
        because MET-07 skips (no recipe)."""
        self.assertNotIn("tags", self.doc_103["metadata"])

    def test_0_1_3_is_shape_identical_to_0_1_2_except_version(self):
        """With no tags stamped on the recipe-less fixture, the v0.1.3 export
        equals the v0.1.2 export apart from metadata.sff_version."""
        a = copy.deepcopy(self.doc_102)
        b = copy.deepcopy(self.doc_103)
        a["metadata"]["sff_version"] = b["metadata"]["sff_version"] = "X"
        self.assertEqual(a, b)

    def test_pre_0_1_3_versions_omit_tags(self):
        """v0.1.2 down to v0.0.6 exports -> no metadata.tags (stamped only from
        0.1.3 on; older exporters stay byte-stable)."""
        for doc in (self.doc_102, self.doc_101, self.doc_100, self.doc_012,
                    self.doc_011, self.doc_010, self.doc_009, self.doc_008,
                    self.doc_007, self.doc_006):
            self.assertNotIn("tags", doc["metadata"])

    def test_0_1_4_validates_against_committed_schema(self):
        """v0.1.4 export of the real small System -> validates against the
        committed schema; records metadata.sff_version "0.1.4"."""
        is_valid, errors = self.validate(str(self.path_104), str(SCHEMA_PATH))
        self.assertTrue(is_valid, f"validation errors: {errors[:5]}")
        self.assertEqual(self.doc_104["metadata"]["sff_version"], "0.1.4")

    def test_0_1_4_design_specs_carry_no_nulls_and_keep_the_set_T(self):
        """v0.1.4 design_input_specs are registry-driven: the HXutility
        fixture's set T survives, its unset V is OMITTED (v0.1.3 exported
        "V": null), and no unit carries a null spec value."""
        # The small fixture has exactly one unit (the HXutility H1).
        hx = self.doc_104["units"][0]
        self.assertEqual(hx["design_input_specs"]["T"], 350.0)
        self.assertNotIn("V", hx["design_input_specs"])
        for unit in self.doc_104["units"]:
            with self.subTest(unit=unit["id"]):
                self.assertTrue(
                    all(v is not None
                        for v in unit["design_input_specs"].values()))

    def test_0_1_3_design_specs_still_carry_the_legacy_null(self):
        """The 0.1.3 export is BYTE-STABLE: its HXutility still exports the
        legacy probe's "V": null (proving the registry did not leak into
        older exporters)."""
        hx = self.doc_103["units"][0]
        self.assertIn("V", hx["design_input_specs"])
        self.assertIsNone(hx["design_input_specs"]["V"])

    def test_0_1_4_is_shape_identical_to_0_1_3_except_design_specs_and_version(self):
        """With every unit's design_input_specs normalized away and
        metadata.sff_version normalized, the v0.1.4 export equals the v0.1.3
        export -- design_input_specs content is the ONLY 0.1.4 change."""
        a = copy.deepcopy(self.doc_103)
        b = copy.deepcopy(self.doc_104)
        for doc in (a, b):
            for unit in doc["units"]:
                unit["design_input_specs"] = {}
        a["metadata"]["sff_version"] = b["metadata"]["sff_version"] = "X"
        self.assertEqual(a, b)

    def test_0_1_5_validates_against_committed_schema(self):
        """v0.1.5 export of the real small System -> validates against the
        committed schema; records metadata.sff_version "0.1.5"."""
        is_valid, errors = self.validate(str(self.path_105), str(SCHEMA_PATH))
        self.assertTrue(is_valid, f"validation errors: {errors[:5]}")
        self.assertEqual(self.doc_105["metadata"]["sff_version"], "0.1.5")

    def test_0_1_5_is_shape_identical_to_0_1_4_except_version(self):
        """v0.1.5 is a validation-vocabulary-only bump (the extracted-from-table
        tag); the exporter never stamps extracted-from-* tags, so its export
        equals the v0.1.4 export apart from metadata.sff_version."""
        a = copy.deepcopy(self.doc_104)
        b = copy.deepcopy(self.doc_105)
        a["metadata"]["sff_version"] = b["metadata"]["sff_version"] = "X"
        self.assertEqual(a, b)

    def test_0_2_0_validates_against_committed_schema(self):
        """v0.2.0 export of the real small System -> validates against the
        committed schema; records metadata.sff_version "0.2.0"."""
        is_valid, errors = self.validate(str(self.path_200), str(SCHEMA_PATH))
        self.assertTrue(is_valid, f"validation errors: {errors[:5]}")
        self.assertEqual(self.doc_200["metadata"]["sff_version"], "0.2.0")

    def test_0_2_0_is_shape_identical_to_0_1_5_except_version(self):
        """v0.2.0 is a milestone bump marking the validate//export/ package
        restructure -- no schema shape or constraint change and no exporter
        behavior change, so its export equals the v0.1.5 export apart from
        metadata.sff_version."""
        a = copy.deepcopy(self.doc_105)
        b = copy.deepcopy(self.doc_200)
        a["metadata"]["sff_version"] = b["metadata"]["sff_version"] = "X"
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
