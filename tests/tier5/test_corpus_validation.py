# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 5: an encoded outcome table for the committed corpus. Runs the full
# validate_flowsheet_against_SFF (QU-02 imports thermosteam; STR-10/CHEM-03
# import chemicals) over every *.json in exported_flowsheets/bioindustrial_park
# and asserts each file's is_valid matches its recorded expectation, so an
# unexpected flip fails loudly. Per-check semantic assertions live in Tier 4
# (tests/tier4/test_streams_checks.py, test_chemicals_checks.py,
# test_cross_object_checks.py, test_docs_fixture.py) -- Tier 5's single
# responsibility is this corpus outcome table.

import unittest
from pathlib import Path

from tests._gating import skip_if_disabled
from tests._stub_eviction import RealBiosteamTestCase
from tests._validate_loader import V

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "pisces_sff" / "schema" / "sff_schema.json"
CORPUS_DIR = (REPO_ROOT / "pisces_sff" / "exported_flowsheets"
              / "bioindustrial_park")

# Recorded outcomes (see canonical validation ss1 in CLAUDE.md): only
# corn_dry_grind_ethanol.json has been re-exported to the current schema
# shape and validates cleanly; the other 17 remain old-shape and fail the
# schema gate. This is the known, intended state -- not a regression.
EXPECTED = {
    "corn_3HP_acrylic.json": False,
    "corn_TAL.json": False,
    "corn_TAL_KS.json": False,
    "corn_dry_grind_ethanol.json": True,
    "corn_succinic.json": False,
    "cornstover_3HP_acrylic.json": False,
    "cornstover_TAL.json": False,
    "cornstover_TAL_KS.json": False,
    "cornstover_succinic.json": False,
    "dextrose_3HP_acrylic.json": False,
    "dextrose_TAL.json": False,
    "dextrose_TAL_KS.json": False,
    "dextrose_succinic.json": False,
    "sugarcane_3HP_acrylic.json": False,
    "sugarcane_TAL.json": False,
    "sugarcane_TAL_KS.json": False,
    "sugarcane_ethanol.json": False,
    "sugarcane_succinic.json": False,
}


class TestCorpusValidation(RealBiosteamTestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(5)        # gate first (skips before any eviction work)
        super().setUpClass()       # evict the Tier-1 stub so thermosteam is real

    def test_on_disk_set_matches_the_table(self):
        """The set of *.json in exported_flowsheets/bioindustrial_park equals
        EXPECTED's keys -> a newly added/removed corpus file forces a table
        update (fails until then)."""
        on_disk = {p.name for p in CORPUS_DIR.glob("*.json")}
        self.assertEqual(on_disk, set(EXPECTED))

    def test_each_file_matches_its_recorded_outcome(self):
        """Each corpus file's validate_flowsheet_against_SFF is_valid equals its
        recorded EXPECTED value (corn True; the 17 stale files False) -> any
        unexpected flip fails."""
        for name, expected in EXPECTED.items():
            with self.subTest(file=name):
                is_valid, _ = V.validate_flowsheet_against_SFF(
                    str(CORPUS_DIR / name), str(SCHEMA_PATH))
                self.assertEqual(is_valid, expected)


if __name__ == "__main__":
    unittest.main()
