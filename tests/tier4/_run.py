# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Tier 4 helper: run the FULL validator on an in-memory SFF doc. Writes doc to a
temp file (the validator takes a path) and indexes results by check id."""

import json
import tempfile
from collections import defaultdict
from pathlib import Path

from tests._validate_loader import V

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "pisces_sff" / "schema" / "sff_schema.json")


def validate_doc(doc):
    """Return (is_valid, {check_id: [CheckResult, ...]}) for the given doc."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "doc.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        is_valid, results = V.validate_flowsheet_against_SFF(
            str(p), str(SCHEMA_PATH))
    by_id = defaultdict(list)
    for r in results:
        by_id[r.check_id].append(r)
    return is_valid, by_id
