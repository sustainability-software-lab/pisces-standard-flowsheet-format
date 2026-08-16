# -*- coding: utf-8 -*-
# Shared Tier-2 fixture: a small REAL biosteam System plus a minimal REAL TEA,
# built once per process (cached). This is NOT a whole-model simulation -- it is
# a two-stream, one-unit system, the smallest real object that exercises the
# exporter's version-dependent shapes (a heat utility, a priced product stream,
# and a TEA the exporter reads TEA_year from).
#
# bst.TEA is directly instantiable (no abstract methods in this biosteam pin);
# the constructor args below are finance placeholders -- the exporter only reads
# tea.duration[0], so their values do not matter to these tests.
#
# The leading underscore keeps pytest from collecting this as a test module.

_CACHE = {}


def build_small_system_and_tea():
    """Return (system, H1, tea); build once and cache for the process."""
    if "built" in _CACHE:
        return _CACHE["built"]

    import biosteam as bst

    # Guard against the Tier-1 sys.modules biosteam stub bleeding into this
    # process: if Tier 1 (which installs the stub at import time) and this tier
    # are ever collected into one process, `bst` here would be the fake and the
    # real-object calls below would fail with a confusing AttributeError. Fail
    # loudly and legibly instead. The sanctioned workflow runs each simulating
    # tier in its own process (pytest tests/tier2), where this never triggers.
    assert not getattr(bst, "_SFF_STUB", False), (
        "Tier 2 got the Tier-1 biosteam stub; run simulating tiers in separate "
        "processes (e.g. `pytest tests/tier2`), not alongside Tier 1."
    )

    bst.settings.set_thermo(["Water", "Ethanol"])
    feed = bst.Stream("feed", Water=1000, Ethanol=100, units="kg/hr", T=298.15)
    feed.price = 0.5
    H1 = bst.HXutility("H1", ins=feed, outs="hot", T=350)
    system = bst.System("small_sys", path=(H1,))
    system.simulate()
    # Give the product outlet a positive price so is_product (cost > 0) is true.
    H1.outs[0].price = 1.0
    tea = bst.TEA(
        system=system, IRR=0.15, duration=(2020, 2030),
        depreciation="MACRS7", income_tax=0.21, operating_days=330.,
        lang_factor=3., construction_schedule=(0.4, 0.6),
        startup_months=0., startup_FOCfrac=0., startup_VOCfrac=0.,
        startup_salesfrac=0., WC_over_FCI=0.05, finance_interest=0.,
        finance_years=0, finance_fraction=0.,
    )
    _CACHE["built"] = (system, H1, tea)
    return _CACHE["built"]
