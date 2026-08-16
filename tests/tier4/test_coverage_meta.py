# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def validator_ids():
    """Parse the sff_checks.md Appendix table; return the set of IDs whose
    Enforcement cell mentions 'validator' (includes the dual schema+validator
    UNIT-04/UNIT-05)."""
    rows = []
    text = (REPO / "sff_checks.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("| ") and "validator" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows.append(cells[0].strip("`"))
    return {r for r in rows if r and r != "ID"}


class TestTier4Coverage(unittest.TestCase):
    def test_every_validator_id_has_a_tier4_test(self):
        """Every validator-enforced ID in the sff_checks.md appendix appears in
        tests/tier4/*.py → expected: empty 'missing' set."""
        # Exclude this file itself: its own docstrings name IDs (e.g. the
        # UNIT-04/UNIT-05 note in validator_ids), so counting them here would
        # let the guard "cover" an ID via the meta-test rather than a genuine
        # category test -- deleting the real test would then slip through.
        here = Path(__file__).resolve()
        text = "\n".join(p.read_text(encoding="utf-8")
                         for p in here.parent.glob("test_*.py")
                         if p.resolve() != here)
        missing = sorted(i for i in validator_ids() if i not in text)
        self.assertEqual(missing, [], f"validator IDs with no Tier 4 test: {missing}")


if __name__ == "__main__":
    unittest.main()
