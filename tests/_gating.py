# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Per-tier enable/disable gating. A tier is enabled unless its env var
SFF_TEST_TIER<n> is set to a falsy value; unset means enabled. Heavy imports
must live inside setUpClass / cached builders so a disabled tier costs nothing
at collection time."""

import os
import unittest

_FALSY = {"0", "false", "no", "off", ""}


def tier_enabled(n):
    """True unless SFF_TEST_TIER<n> is set to a falsy value (0/false/no/off/'')."""
    raw = os.environ.get(f"SFF_TEST_TIER{n}")
    return raw is None or raw.strip().lower() not in _FALSY


def skip_if_disabled(n):
    """Raise unittest.SkipTest when tier n is disabled. Call from setUpClass."""
    if not tier_enabled(n):
        raise unittest.SkipTest(f"tier {n} disabled via SFF_TEST_TIER{n}")


RUN_TIER1 = tier_enabled(1)
RUN_TIER2 = tier_enabled(2)
RUN_TIER3 = tier_enabled(3)
RUN_TIER4 = tier_enabled(4)
RUN_TIER5 = tier_enabled(5)
RUN_TIER6 = tier_enabled(6)
