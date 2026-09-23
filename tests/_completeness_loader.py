# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Load pisces_sff/completeness/_completeness.py by file path, WITHOUT importing
pisces_sff (which would drag in biosteam). The scorer holds no package-relative
top-level imports and obtains the validator and design-spec registry lazily by
file path, so this works; it is the Tier 1 entry point to the scorer internals
(mirrors tests/_validate_loader.py)."""

import importlib.util
from pathlib import Path

COMPLETENESS_PATH = (Path(__file__).resolve().parent.parent / "pisces_sff"
                     / "completeness" / "_completeness.py")


def load_completeness_module():
    """Return the _completeness module loaded by file path (no pisces_sff
    import)."""
    spec = importlib.util.spec_from_file_location(
        "pisces_sff_completeness_under_test", COMPLETENESS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load_completeness_module()
