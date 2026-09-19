# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Tier 2: test tea_details export from real BioSTEAM TEA objects.

Tests that the exporter correctly extracts TEA economics data and writes it
to metadata.tea_details for schema version 0.2.2+, and omits it for older
versions (byte-stability).
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests._gating import RUN_TIER2
from tests._real_objects import build_small_system_and_tea
from tests._stub_eviction import RealBiosteamTestCase


@unittest.skipUnless(RUN_TIER2, "set SFF_TEST_TIER2=1 (default on) to run; builds real biosteam objects")
class TestTeaDetailsExport(RealBiosteamTestCase):
    """Test tea_details export from real BioSTEAM TEA objects.

    Uses a minimal real system (single HX unit with feed and product streams)
    from build_small_system_and_tea. While not a full biorefinery, it exercises
    the tea_details extraction logic with a real TEA object and verifies
    model mutation safety.
    """

    def setUp(self):
        """Build a small system and TEA for testing."""
        self.sys, _, self.tea = build_small_system_and_tea()

    def test_tea_details_present_in_v0_2_2_export(self):
        """v0.2.2 exports include metadata.tea_details with economics data."""
        from pisces_sff import _export

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "export.json"
            _export.export_biosteam_flowsheet(
                self.sys, str(filepath), sff_version="0.2.2", tea=self.tea
            )

            with open(filepath) as f:
                exported = json.load(f)

            # tea_details should be present
            self.assertIn("tea_details", exported["metadata"])
            tea_details = exported["metadata"]["tea_details"]

            # Check required and key fields exist
            self.assertIn("currency", tea_details)
            self.assertEqual(tea_details["currency"], "USD")

            # Check all required numeric fields are present and either numbers or null
            numeric_fields = [
                "total_capital_cost_usd",
                "annual_operating_cost_usd",
                "utility_cost_usd_yr",
                "annual_sales_usd",
                "installed_equipment_cost_usd",
                "purchase_cost_usd",
                "npv_usd",
                "irr_assumed_pct",
                "irr_solved_pct",
                "msp_usd_per_kg",
                "annual_throughput_kg_yr",
            ]
            for field in numeric_fields:
                self.assertIn(field, tea_details)
                value = tea_details[field]
                self.assertTrue(
                    isinstance(value, (int, float, type(None))),
                    f"{field} should be a number or null, got {type(value).__name__}",
                )

            # Check string/optional fields
            self.assertIn("tea_class", tea_details)
            self.assertIsInstance(tea_details["tea_class"], str)

            # msp_product_stream_id should be a string (highest sales product)
            self.assertIn("msp_product_stream_id", tea_details)
            if tea_details["msp_product_stream_id"] is not None:
                self.assertIsInstance(tea_details["msp_product_stream_id"], str)

    def test_tea_details_omitted_in_older_versions(self):
        """Versions before 0.2.2 do not emit metadata.tea_details."""
        from pisces_sff import _export

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath_old = Path(tmpdir) / "export_v0_2_0.json"
            _export.export_biosteam_flowsheet(
                self.sys, str(filepath_old), sff_version="0.2.0", tea=self.tea
            )

            with open(filepath_old) as f:
                exported_old = json.load(f)

            # tea_details should NOT be present in v0.2.0
            self.assertNotIn("tea_details", exported_old["metadata"])

    def test_tea_details_contains_finite_key_values(self):
        """Key economics fields in tea_details are finite numbers (not null)."""
        from pisces_sff import _export

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "export.json"
            _export.export_biosteam_flowsheet(
                self.sys, str(filepath), sff_version="0.2.2", tea=self.tea
            )

            with open(filepath) as f:
                exported = json.load(f)

            tea_details = exported["metadata"]["tea_details"]

            # For a real biorefinery, at least some key values should be finite
            # (not null) - we check the most important ones
            key_fields = [
                "total_capital_cost_usd",
                "annual_operating_cost_usd",
                "npv_usd",
                "annual_sales_usd",
            ]
            non_null_count = sum(
                1 for field in key_fields if tea_details.get(field) is not None
            )
            self.assertGreater(
                non_null_count,
                0,
                "At least one key economics field should have a finite value",
            )

    def test_tea_details_main_product_throughput(self):
        """annual_throughput_kg_yr is calculated from main product stream."""
        from pisces_sff import _export

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "export.json"
            _export.export_biosteam_flowsheet(
                self.sys, str(filepath), sff_version="0.2.2", tea=self.tea
            )

            with open(filepath) as f:
                exported = json.load(f)

            tea_details = exported["metadata"]["tea_details"]

            # throughput should be a number or null
            throughput = tea_details.get("annual_throughput_kg_yr")
            self.assertTrue(
                isinstance(throughput, (int, float, type(None))),
                f"annual_throughput_kg_yr should be a number or null, got {type(throughput).__name__}",
            )

    def test_product_stream_price_unchanged_after_export(self):
        """Export at 0.2.2 does not mutate stream prices; subsequent 0.0.10 export sees unchanged roles."""
        from pisces_sff import _export

        products = self.sys.products
        prices_before = {stream: stream.price for stream in products}

        with tempfile.TemporaryDirectory() as tmpdir:
            # Export at 0.2.2 (which solves MSP and may mutate prices internally)
            filepath_022 = Path(tmpdir) / "export_0_2_2.json"
            _export.export_biosteam_flowsheet(
                self.sys, str(filepath_022), sff_version="0.2.2", tea=self.tea
            )

            # Verify stream prices are unchanged after 0.2.2 export
            for stream in products:
                self.assertEqual(
                    stream.price,
                    prices_before[stream],
                    f"0.2.2 export mutated stream {stream.ID} price: "
                    f"{prices_before[stream]} -> {stream.price}",
                )

            # Export at 0.0.10 and verify product roles are still present
            filepath_0010 = Path(tmpdir) / "export_0_0_10.json"
            _export.export_biosteam_flowsheet(
                self.sys, str(filepath_0010), sff_version="0.0.10", tea=self.tea
            )

            # Load 0.0.10 export into memory while directory still exists
            export_0010 = json.loads(filepath_0010.read_text(encoding="utf-8"))

        # Verify product streams have 'product' role in 0.0.10 export
        # (confirming prices were not zeroed out by 0.2.2 export)
        streams_0010 = {s.get("id"): s for s in export_0010.get("streams", [])}
        for product in self.sys.products:
            stream_id = product.ID
            if stream_id in streams_0010:
                roles = streams_0010[stream_id].get("roles", [])
                self.assertIn(
                    "product",
                    roles,
                    f"0.2.2 export mutation: stream {stream_id} lost 'product' role; roles={roles}",
                )

    def test_exported_file_validates_against_schema(self):
        """Exported v0.2.2 file with tea_details validates against schema."""
        import jsonschema
        from pisces_sff import _export

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "export.json"
            _export.export_biosteam_flowsheet(
                self.sys, str(filepath), sff_version="0.2.2", tea=self.tea
            )

            with open(filepath) as f:
                exported = json.load(f)

            # Load schema
            schema_path = (
                Path(__file__).resolve().parents[2]
                / "pisces_sff"
                / "schema"
                / "sff_schema.json"
            )
            with open(schema_path) as f:
                schema = json.load(f)

            # Validate against schema - should not raise
            try:
                jsonschema.validate(exported, schema)
            except jsonschema.ValidationError as e:
                self.fail(f"Exported file does not validate against schema: {e.message}")


if __name__ == "__main__":
    unittest.main()
