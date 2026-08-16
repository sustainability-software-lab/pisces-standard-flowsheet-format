# -*- coding: utf-8 -*-
# Shared Tier-1 helper: make pisces_sff._export importable WITHOUT loading the
# real biosteam/thermosteam (slow, JIT-heavy). We install fake modules into
# sys.modules providing exactly the names _export.py imports at module top, then
# import the real _export as a genuine package submodule so its relative imports
# (._quantity_units, .exceptions) resolve normally.
#
# The fake surface lives in THIS ONE FILE. When a helper under test starts
# touching a new biosteam/thermosteam attribute, extend the fakes here.
#
# The leading underscore keeps pytest from collecting this as a test module.

import importlib
import sys
import types


def install_biosteam_stubs():
    """Install fake biosteam/thermosteam modules into sys.modules (idempotent).

    Safe to call repeatedly. Refuses to overwrite a *real* biosteam already
    imported: Tier 1 must never import the real simulator, so that situation is
    a mistake worth surfacing rather than papering over.
    """
    existing = sys.modules.get("biosteam")
    if existing is not None and getattr(existing, "_SFF_STUB", False):
        return  # stubs already installed
    if existing is not None and not getattr(existing, "_SFF_STUB", False):
        raise RuntimeError(
            "the real biosteam is already imported; Tier 1 must not load it. "
            "Import _export_stub before anything that imports biosteam."
        )

    thermosteam = types.ModuleType("thermosteam")

    class Reaction: ...
    class ReactionSet: ...
    class SeriesReaction: ...
    class ParallelReaction: ...
    class Chemical: ...

    thermosteam.Reaction = Reaction
    thermosteam.ReactionSet = ReactionSet
    thermosteam.SeriesReaction = SeriesReaction
    thermosteam.ParallelReaction = ParallelReaction
    thermosteam.Chemical = Chemical
    thermosteam._SFF_STUB = True

    reaction_pkg = types.ModuleType("thermosteam.reaction")
    reaction_impl = types.ModuleType("thermosteam.reaction._reaction")

    def get_stoichiometric_string(*args, **kwargs):
        raise NotImplementedError("stub get_stoichiometric_string")

    reaction_impl.get_stoichiometric_string = get_stoichiometric_string
    reaction_pkg._reaction = reaction_impl
    thermosteam.reaction = reaction_pkg

    biosteam = types.ModuleType("biosteam")

    class PowerUtility: ...
    class System: ...

    biosteam.PowerUtility = PowerUtility
    biosteam.System = System
    biosteam.__version__ = "0.0-stub"
    biosteam._SFF_STUB = True

    sys.modules["thermosteam"] = thermosteam
    sys.modules["thermosteam.reaction"] = reaction_pkg
    sys.modules["thermosteam.reaction._reaction"] = reaction_impl
    sys.modules["biosteam"] = biosteam


def load_export():
    """Install the stubs, then import and return the real pisces_sff._export."""
    install_biosteam_stubs()
    return importlib.import_module("pisces_sff._export")
