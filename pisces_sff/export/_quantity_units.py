# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Quantity-unit vocabulary and version-gated scalar shape for the SFF exporter.

Deliberately import-light — no biosteam/thermosteam — so schema-level tests and
the exporter share one source of truth for units without paying the simulator
import cost. 'units' in SFF always means unit operations; unit-of-measure
information is always called 'quantity units'.
"""

__all__ = (
    "QUANTITY_UNITS_GLOBAL",
    "quantity_units_global_for",
    "scalar",
    "version_tuple",
    "uses_inline_scalar_style",
    "quantity_units_for_design_results",
)

#: First schema version that reports scalars as bare numbers (units resolved via
#: QUANTITY_UNITS_GLOBAL) instead of inline {"value", "units"} pairs.
_BARE_SCALAR_SINCE = (0, 0, 7)

#: Global default quantity units, keyed by canonical quantity name. `aliases`
#: lists every field name the quantity appears under across a flowsheet (so a
#: consumer can resolve, e.g., 'T' or 'total_mass_flow' to its unit); values are
#: BioSTEAM-native unit strings.
QUANTITY_UNITS_GLOBAL = {
    "temperature":             {"aliases": ["temperature", "T", "temperature_limit"], "quantity_units": "K"},
    "pressure":                {"aliases": ["pressure", "P"], "quantity_units": "Pa"},
    "mass_flow":               {"aliases": ["mass_flow", "total_mass_flow", "F_mass"], "quantity_units": "kg/hr"},
    "molar_flow":              {"aliases": ["molar_flow", "total_molar_flow", "F_mol"], "quantity_units": "kmol/hr"},
    "volumetric_flow":         {"aliases": ["volumetric_flow", "total_volumetric_flow", "F_vol"], "quantity_units": "m3/hr"},
    "molar_mass":              {"aliases": ["molar_mass", "MW"], "quantity_units": "g/mol"},
    "price":                   {"aliases": ["price"], "quantity_units": "USD/kg"},
    "electrical_energy_price": {"aliases": ["electrical_energy_price"], "quantity_units": "USD/kWh"},
    "regeneration_price":      {"aliases": ["regeneration_price"], "quantity_units": "USD/kmol"},
    "heat_transfer_price":     {"aliases": ["heat_transfer_price"], "quantity_units": "USD/kJ"},
    "enthalpy_flow":           {"aliases": ["enthalpy_flow", "H"], "quantity_units": "kJ/hr"},
}

#: Schema version at which each quantity_units_global entry was introduced.
#: Entries not listed here have existed since the registry itself (0.0.7) and
#: are emitted by every bare-number exporter; listed entries are filtered out of
#: exports older than their introduction version so those exporters stay
#: byte-stable. See quantity_units_global_for.
_QUANTITY_INTRODUCED_SINCE = {"enthalpy_flow": (0, 0, 11)}


def quantity_units_global_for(version):
    """
    Return the ``quantity_units_global`` registry as of schema `version`.

    Entries introduced in a schema version newer than `version` (per
    ``_QUANTITY_INTRODUCED_SINCE``) are omitted, so an exporter for an older
    version reproduces its historical registry byte-for-byte. Entries not listed
    in ``_QUANTITY_INTRODUCED_SINCE`` are assumed to predate the registry's own
    introduction (``_BARE_SCALAR_SINCE``, 0.0.7) and are always included.

    Parameters
    ----------
    version : str
        Semantic-version string; e.g. ``'0.0.11'``.

    Returns
    -------
    dict
        The registry filtered to entries available at `version`, preserving the
        insertion order of ``QUANTITY_UNITS_GLOBAL``.
    """
    v = version_tuple(version)
    return {name: entry for name, entry in QUANTITY_UNITS_GLOBAL.items()
            if _QUANTITY_INTRODUCED_SINCE.get(name, _BARE_SCALAR_SINCE) <= v}


def scalar(value, units, inline):
    """
    Format a scalar quantity for an SFF document.

    Parameters
    ----------
    value : number
        The scalar value.
    units : str
        Unit string, used only in the inline shape.
    inline : bool
        If True, return the pre-0.0.7 ``{"value", "units"}`` pair; otherwise
        return the bare ``value`` (its units come from ``QUANTITY_UNITS_GLOBAL``).

    Returns
    -------
    dict or number
    """
    return {"value": value, "units": units} if inline else value


def version_tuple(version):
    """
    Parse a semantic-version string into a tuple of ints; e.g. ``'0.0.7'`` ->
    ``(0, 0, 7)``.
    """
    return tuple(int(part) for part in str(version).split("."))


def uses_inline_scalar_style(version):
    """
    Return True if `version` predates the bare-number scalar shape (i.e. is
    older than 0.0.7 and must emit inline ``{"value", "units"}`` pairs).
    """
    return version_tuple(version) < _BARE_SCALAR_SINCE


def quantity_units_for_design_results(unit):
    """
    Map each of a unit operation's ``design_results`` keys to its unit string.

    Sourced from the simulator's per-design-result units (BioSTEAM ``_units``).
    A key present in ``design_results`` but absent from ``_units`` maps to ``''``
    (dimensionless or unspecified).

    Parameters
    ----------
    unit : object
        A unit operation exposing ``design_results`` and ``_units`` mappings.

    Returns
    -------
    dict of str -> str
    """
    units_map = getattr(unit, "_units", {}) or {}
    design = getattr(unit, "design_results", {}) or {}
    return {key: units_map.get(key, "") for key in design}
