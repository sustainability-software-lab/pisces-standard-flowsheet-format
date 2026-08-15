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
#
# The stubs are installed only for the duration of the _export import and then
# removed from sys.modules again. They must not outlive it: Tier 5 runs in the
# same pytest process and asks the validator to parse quantity units, which
# lazily imports thermosteam. A fake thermosteam left in sys.modules makes every
# unit unparseable and the reference corpus file report as non-conforming, with
# no symptom pointing back here. _export keeps the fakes regardless -- it bound
# them into its own globals at import time -- which is all Tier 1 needs.

import importlib
import sys
import types

#: The sys.modules keys install_biosteam_stubs() owns.
_STUB_MODULE_NAMES = ('biosteam', 'thermosteam', 'thermosteam.reaction',
                      'thermosteam.reaction._reaction')


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
    reaction_pkg._SFF_STUB = True
    reaction_impl._SFF_STUB = True
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


def uninstall_biosteam_stubs():
    """Remove the fake modules from sys.modules (idempotent).

    Only removes entries this module installed -- an entry that is not marked
    _SFF_STUB belongs to someone else and is left alone.
    """
    for name in _STUB_MODULE_NAMES:
        module = sys.modules.get(name)
        if module is not None and getattr(module, '_SFF_STUB', False):
            del sys.modules[name]


def load_export():
    """Import and return the real pisces_sff._export with fake simulator modules.

    The fakes are installed, `_export` is imported against them, and the fakes
    are then removed from sys.modules again. `_export` keeps them (they are bound
    in its globals); nothing imported afterwards sees them. See the note at the
    top of this file for why that matters.
    """
    install_biosteam_stubs()
    try:
        return importlib.import_module("pisces_sff._export")
    finally:
        uninstall_biosteam_stubs()
