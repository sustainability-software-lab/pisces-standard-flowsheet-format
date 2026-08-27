# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Load pisces_sff/validate/_validate.py by file path, WITHOUT importing pisces_sff
(which would drag in biosteam). _validate.py holds no package-relative top-level
imports, so this works; it is the Tier 1/4 entry point to the validator internals."""

import importlib.util
from pathlib import Path

VALIDATE_PATH = (
    Path(__file__).resolve().parent.parent / "pisces_sff" / "validate" / "_validate.py")


def load_validate_module():
    """Return the _validate module loaded by file path (no pisces_sff import)."""
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_validate_under_test", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = load_validate_module()
