# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Tier 1's tests/_fakes.py::install_biosteam_stubs() permanently replaces
sys.modules['thermosteam'] / ['biosteam'] (plus thermosteam.reaction and
thermosteam.reaction._reaction) with fake types.ModuleType objects, and never
restores them. That's fine for Tier 1, which never needs the real simulator -
but when the whole suite runs in one pytest process, the fake lingers in
sys.modules for every test that runs afterward, including Tiers 3/4/5.

pisces_sff/_validate.py's QU-02 check (_unit_is_parseable) lazily does
`from thermosteam.units_of_measure import ureg` the first time it needs to
parse a unit string. If Tier 1 already ran, that import hits the fake (a bare
ModuleType with no units_of_measure submodule and no __path__), raises, and is
swallowed by _unit_is_parseable's `except Exception: return False` - so every
unit string in the document reports as unparseable, and QU-02 emits a false
error-severity fail.

evict_biosteam_stubs() removes the fake sys.modules entries (root module plus
any submodule under it) so the next `import thermosteam` / `import biosteam`
re-loads the real package instead of the stale fake. It never imports
biosteam/thermosteam itself - only sys.modules dict operations - and it never
touches a real (non-stub) module."""

import sys
import unittest


def evict_biosteam_stubs():
    """Remove any Tier-1 fake biosteam/thermosteam stub from sys.modules.

    For each of "biosteam" and "thermosteam": if the module currently
    installed under that name is a fake stub (marked with `_SFF_STUB = True`
    by tests/_fakes.py::install_biosteam_stubs()), delete it - and every
    sys.modules key that is one of its submodules (`f"{root}.something"`) -
    so a later `import` re-loads the real package.

    A no-op when no stub is present: if the root name is absent from
    sys.modules, or resolves to a real (non-stub) module, nothing is deleted
    for that root. Idempotent and cheap (dict operations only); imports
    nothing beyond `sys`.
    """
    for root in ("biosteam", "thermosteam"):
        module = sys.modules.get(root)
        if not getattr(module, "_SFF_STUB", False):
            continue  # absent, or a real (non-stub) module - leave it alone
        prefix = root + "."
        for key in [k for k in sys.modules if k == root or k.startswith(prefix)]:
            del sys.modules[key]


class RealBiosteamTestCase(unittest.TestCase):
    """Base for Tier 3/4/5 test classes whose tests run the REAL validator
    (QU-02 parses unit strings via real thermosteam). Evicts any Tier-1
    _SFF_STUB in setUpClass so the real package re-imports - under BOTH
    `pytest` and `python -m unittest discover` (a conftest autouse fixture
    would only fire under pytest). Subclasses overriding setUpClass MUST call
    super().setUpClass()."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        evict_biosteam_stubs()
