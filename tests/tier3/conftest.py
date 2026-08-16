# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

import pytest

from tests._stub_eviction import evict_biosteam_stubs


@pytest.fixture(autouse=True)
def _use_real_biosteam_not_tier1_stub():
    """Tiers 3/4/5 run the real validator (QU-02 parses unit strings via real
    thermosteam). Evict any Tier-1 _SFF_STUB fake before each test so the real
    package re-imports. No-op when no stub is present."""
    evict_biosteam_stubs()
    yield
