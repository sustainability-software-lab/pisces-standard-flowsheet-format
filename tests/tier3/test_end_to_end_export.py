# -*- coding: utf-8 -*-
# Tier 3: the full harness, including conda environment creation, driven through
# the same regenerate_corpus() function the deliberate corpus-regeneration
# command uses -- but writing to a TEMP directory, so running this test never
# touches the committed corpus.
#
# Gated on SFF_TEST_E2E=1 because it builds a conda environment from scratch on
# a cache miss (tens of minutes) and then simulates. Run it with:
#
#     $env:SFF_TEST_E2E = "1"
#     & "C:\Users\saran\anaconda3\envs\HP_2024\python.exe" -m pytest tests/tier3/test_end_to_end_export.py -q
#
# This is the ONLY tier in which the recipe's pins are what actually ran -- the
# export happens inside the environment environment.yaml describes -- and
# therefore the only tier permitted to assert numeric baselines. Those baselines
# are measurements recorded from the first successful run, not targets.
#
# Must not run in parallel with any other simulating test: concurrent
# simulations corrupt the shared numba cache. The harness lock enforces this.

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = (
    REPO_ROOT / "pisces_sff" / "models" / "biosteam_models"
    / "corn_dry_grind_ethanol"
)
SCHEMA_PATH = REPO_ROOT / "pisces_sff" / "schema" / "sff_schema.json"
BASELINE_PATH = REPO_ROOT / "tests" / "baselines" / "corn_dry_grind_ethanol.json"

#: Relative tolerance for numeric baselines. Loose enough to absorb BLAS/LAPACK
#: and platform differences between machines running identical pins, tight
#: enough that a genuine model change fails.
RTOL = 1e-4

RUN_TIER_3 = os.environ.get("SFF_TEST_E2E") == "1"


@unittest.skipUnless(RUN_TIER_3, "set SFF_TEST_E2E=1 to run (creates a conda environment)")
class TestEndToEndExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pisces_sff._regenerate_corpus import regenerate_corpus
        from pisces_sff._validate import validate_json_against_schema

        cls.validate = staticmethod(validate_json_against_schema)
        cls.tmp = tempfile.TemporaryDirectory()
        # Same code path as the committed-corpus command, but to a temp dir.
        cls.written = regenerate_corpus(cls.tmp.name)
        cls.output = Path(cls.tmp.name) / "corn_dry_grind_ethanol.json"
        with cls.output.open("r", encoding="utf-8") as f:
            cls.flowsheet = json.load(f)
        with BASELINE_PATH.open("r", encoding="utf-8") as f:
            cls.baseline = json.load(f)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assertClose(self, actual, expected, label):
        self.assertAlmostEqual(
            actual, expected, delta=abs(expected) * RTOL,
            msg=f"{label}: got {actual!r}, baseline {expected!r}",
        )

    # ------- Every written file validates -------

    def test_corn_output_was_written(self):
        self.assertIn(self.output, self.written)

    def test_all_written_files_validate_against_the_schema(self):
        for path in self.written:
            with self.subTest(file=path.name):
                is_valid, errors = self.validate(str(path), str(SCHEMA_PATH))
                self.assertTrue(is_valid, f"{path.name}: {errors[:5]}")

    # ------- Ran in the pinned environment -------

    def test_export_ran_in_the_pinned_environment(self):
        resolved = self.flowsheet["metadata"]["reproducibility"]["resolved"]
        self.assertEqual(
            resolved["package_versions"]["biosteam"],
            self.baseline["biosteam_version"],
        )
        self.assertEqual(resolved["env_key"], self.baseline["env_key"])

    # ------- Numeric baselines (only this tier may assert these) -------

    def test_graph_size_matches_the_baseline(self):
        self.assertEqual(len(self.flowsheet["units"]), self.baseline["n_units"])
        self.assertEqual(len(self.flowsheet["streams"]), self.baseline["n_streams"])
        self.assertEqual(len(self.flowsheet["chemicals"]), self.baseline["n_chemicals"])

    def test_export_uses_the_current_schema_version(self):
        # The corpus auto-syncs to the schema: regenerate_corpus() with no
        # explicit version resolves through export_model's DEFAULT_SFF_VERSION,
        # which is read_schema_version(). This guards against that pin drifting
        # from the schema on a future bump.
        from pisces_sff._version import read_schema_version
        self.assertEqual(
            self.flowsheet["metadata"]["sff_version"], read_schema_version()
        )

    def test_tea_year_matches_the_baseline(self):
        self.assertEqual(
            self.flowsheet["metadata"]["TEA_year"], self.baseline["TEA_year"]
        )

    def test_stream_mass_flows_match_the_baseline(self):
        flows = {s["id"]: s["stream_properties"]["total_mass_flow"]
                 for s in self.flowsheet["streams"]}
        for stream_id, expected in self.baseline["stream_mass_flows"].items():
            with self.subTest(stream=stream_id):
                self.assertIn(stream_id, flows)
                self.assertClose(flows[stream_id], expected, stream_id)

    def test_stream_enthalpy_flows_match_the_baseline(self):
        # enthalpy_flow (biosteam stream.H, kJ/hr) is a v0.0.11 addition; this is
        # the only tier whose pins actually ran, so it is the only place the
        # numeric value is asserted. Feed streams sit at the reference state
        # (H == 0), for which a relative-tolerance check is degenerate, so the
        # baseline pins only the non-zero product/co-product streams.
        flows = {s["id"]: s["stream_properties"].get("enthalpy_flow")
                 for s in self.flowsheet["streams"]}
        for stream_id, expected in self.baseline["stream_enthalpy_flows"].items():
            with self.subTest(stream=stream_id):
                self.assertIn(stream_id, flows)
                self.assertIsNotNone(
                    flows[stream_id],
                    f"{stream_id}: enthalpy_flow missing from stream_properties")
                self.assertClose(flows[stream_id], expected, stream_id)

    def test_total_installed_cost_matches_the_baseline(self):
        total = sum(sum(u["installed_costs"].values())
                    for u in self.flowsheet["units"])
        self.assertClose(total, self.baseline["total_installed_cost"],
                         "total installed cost")

    # ------- Corn-specific structural assertions (folded in from the removed
    #         Tier-2 corn test) -------

    def test_feedstock_is_corn(self):
        feedstocks = {f["stream_id"] for f in self.flowsheet["metadata"]["feedstocks"]}
        self.assertIn("corn", feedstocks)

    def test_ethanol_is_a_product(self):
        products = {p["stream_id"] for p in self.flowsheet["metadata"]["products"]}
        self.assertIn("ethanol", products)

    def test_microorganism_is_declared(self):
        hosts = self.flowsheet["metadata"]["microorganisms"]
        self.assertEqual(hosts[0]["name"], "Saccharomyces cerevisiae")

    def test_authored_metadata_comes_from_extended_metadata_yaml(self):
        import yaml
        authored = yaml.safe_load(
            (MODEL_DIR / "extended_metadata.yaml").read_text(encoding="utf-8"))
        md = self.flowsheet["metadata"]
        self.assertEqual(md["source_doi"], authored["source_doi"])
        self.assertEqual(md["process_title"], authored["process_title"])
        self.assertEqual(md["flowsheet_designers"],
                         authored["flowsheet_designers"])
        self.assertEqual(md["microorganisms"][0]["name"],
                         authored["microorganisms"][0]["name"])

    def test_graph_is_non_empty(self):
        self.assertTrue(self.flowsheet["units"])
        self.assertTrue(self.flowsheet["streams"])
        self.assertTrue(self.flowsheet["chemicals"])

    def test_streams_reference_declared_units(self):
        # "None" is the exporter's sentinel for a system boundary.
        unit_ids = {u["id"] for u in self.flowsheet["units"]} | {"None"}
        for stream in self.flowsheet["streams"]:
            with self.subTest(stream=stream["id"]):
                self.assertIn(stream["source_unit_id"], unit_ids)
                self.assertIn(stream["sink_unit_id"], unit_ids)

    def test_quantity_units_global_is_present_and_biosteam_native(self):
        reg = self.flowsheet["quantity_units_global"]
        self.assertEqual(reg["temperature"]["quantity_units"], "K")
        self.assertEqual(reg["mass_flow"]["quantity_units"], "kg/hr")
        self.assertEqual(reg["price"]["quantity_units"], "USD/kg")

    # ------- Reproducibility embedding (folded in) -------

    def test_embedded_environment_matches_the_committed_file(self):
        block = self.flowsheet["metadata"]["reproducibility"]["environment"]
        data = (MODEL_DIR / "environment.yaml").read_bytes()
        self.assertEqual(block["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(block["content"], data.decode("utf-8"))
        self.assertEqual(block["filename"], "environment.yaml")

    def test_embedded_load_script_matches_the_committed_file(self):
        block = self.flowsheet["metadata"]["reproducibility"]["load_script"]
        data = (MODEL_DIR / "load.py").read_bytes()
        self.assertEqual(block["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(block["content"], data.decode("utf-8"))
        self.assertEqual(block["entry_point"], "load")

    def test_embedded_extended_metadata_matches_the_committed_file(self):
        block = self.flowsheet["metadata"]["reproducibility"]["extended_metadata"]
        data = (MODEL_DIR / "extended_metadata.yaml").read_bytes()
        self.assertEqual(block["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(block["content"], data.decode("utf-8"))
        self.assertEqual(block["filename"], "extended_metadata.yaml")
        self.assertEqual(block["format"], "yaml")

    def test_package_pins_are_recorded(self):
        block = self.flowsheet["metadata"]["reproducibility"]
        self.assertEqual(
            block["simulator_package"]["commit"],
            "e2d3942dd1076a4516efc91ae194f9e558428551",
        )
        self.assertEqual(
            block["flowsheet_model_package"]["commit"],
            "584232846c999986f108cbd14d53437cd06c8f3d",
        )

    def test_resolved_block_records_the_runtime(self):
        resolved = self.flowsheet["metadata"]["reproducibility"]["resolved"]
        self.assertTrue(resolved["python_version"])
        self.assertTrue(resolved["platform"])
        self.assertEqual(len(resolved["env_key"]), 64)
        self.assertTrue(resolved["exported_at"].endswith("Z"))
        self.assertIn("biosteam", resolved["package_versions"])


if __name__ == "__main__":
    unittest.main()
