# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
# 
# This module is under the MIT open-source license. See 
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

import json
import re
from collections import namedtuple
from pathlib import Path
from typing import Any, Tuple

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError

__all__ = ('validate_json_against_schema', 'validate_flowsheet_against_SFF',
           'CheckResult')

#%%
def validate_json_against_schema(
    json_file,
    schema_file,
):
    """
    Validate a JSON file against a JSON Schema file.

    Returns:
        (is_valid, errors)
        - is_valid: True if the JSON adheres to the schema
        - errors: list of human-readable validation errors
    """
    json_file = Path(json_file)
    schema_file = Path(schema_file)

    with json_file.open("r", encoding="utf-8") as f:
        data: Any = json.load(f)

    with schema_file.open("r", encoding="utf-8") as f:
        schema: dict[str, Any] = json.load(f)

    try:
        validator = Draft7Validator(schema)
    except SchemaError as e:
        return False, [f"Invalid schema: {e.message}"]

    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))

    if not errors:
        return True, []

    formatted_errors = []
    for err in errors:
        path = ".".join(str(p) for p in err.path) or "<root>"
        formatted_errors.append(f"{path}: {err.message}")

    return False, formatted_errors

#%% Flowsheet-level validation (sff_checks.md)

# The schema file shipped in this package. Defined here (not imported from
# _version) so this module stays free of package-relative top-level imports and
# can be loaded by file path in the fast Tier-1 tests.
_SCHEMA_FILE = Path(__file__).resolve().parent / 'schema' / 'sff_schema.json'

# A single finding. severity is the check's declared level (error | warning |
# info); status is the outcome (pass | fail | skip). is_valid is driven only by
# error-severity fails -- warnings and infos never make a file non-conforming.
CheckResult = namedtuple('CheckResult', 'check_id severity status message path')


def _passed(check_id, severity, path='<root>'):
    return CheckResult(check_id, severity, 'pass', '', path)


def _failed(check_id, severity, message, path='<root>'):
    return CheckResult(check_id, severity, 'fail', message, path)


def _skipped(check_id, severity, message, path='<root>'):
    return CheckResult(check_id, severity, 'skip', message, path)


# Absolute tolerances unless noted; see sff_checks.md "Default tolerances".
TOL_FRACTION = 1e-6      # absolute: fraction sums that should equal 1
TOL_FLOW = 1e-3          # relative: mass<->molar, phase-sum<->total
TOL_MOLAR_MASS = 1e-3    # relative: formula-derived vs declared molar mass
ZERO_FLOW = 1e-12        # absolute: treat a flow as exactly zero

_UTILITY_GROUPS = ('heat_utilities', 'power_utilities', 'other_utilities')


class _Context:
    """Read-only indexes over one SFF document, built once and shared by every
    check. Every accessor tolerates missing/malformed sections (returns empty)
    so a schema-invalid document still runs the structural checks without
    raising -- checks report `skip` when their inputs are absent."""

    def __init__(self, doc):
        self.doc = doc if isinstance(doc, dict) else {}
        self.metadata = self.doc.get('metadata') or {}
        self.units = self.doc.get('units') or []
        self.streams = self.doc.get('streams') or []
        self.chemicals = self.doc.get('chemicals') or []
        self.utilities = self.doc.get('utilities') or {}
        self.qug = self.doc.get('quantity_units_global') or {}

        self.unit_ids = {u.get('id') for u in self.units if isinstance(u, dict)}
        self.stream_ids = {s.get('id') for s in self.streams if isinstance(s, dict)}
        self.chem_by_id = {c.get('id'): c for c in self.chemicals
                           if isinstance(c, dict)}
        self.chem_by_index = {c['index']: c for c in self.chemicals
                              if isinstance(c, dict) and 'index' in c}
        self.utilities_list = [u for g in _UTILITY_GROUPS
                               for u in (self.utilities.get(g) or [])
                               if isinstance(u, dict)]
        self.util_by_id = {u.get('id'): u for u in self.utilities_list}
        self.util_ids = set(self.util_by_id)
        self._mm_cache = {}

    def molar_mass(self, cid):
        """Resolve a component's molar mass (g/mol): declared molar_mass if a
        positive number, else derived from its formula, else None. Cached."""
        if cid in self._mm_cache:
            return self._mm_cache[cid]
        chem = self.chem_by_id.get(cid)
        value = None
        if isinstance(chem, dict):
            declared = chem.get('molar_mass')
            if isinstance(declared, (int, float)) and declared > 0:
                value = float(declared)
            elif chem.get('formula'):
                value = _molar_mass_from_formula(chem['formula'])
        self._mm_cache[cid] = value
        return value


def _molar_mass_from_formula(formula):
    """Molar mass (g/mol) parsed from a chemical formula, or None if unparseable.
    Uses the `chemicals` library (imported lazily to keep this module light).
    A formula that parses to no atoms (or a non-positive mass) is treated as
    unparseable and returns None: `chemicals` 1.2.0's simple_formula_parser
    returns {} for garbage rather than raising, and molecular_weight({}) is 0."""
    try:
        from chemicals.elements import molecular_weight, simple_formula_parser
    except Exception:
        return None
    try:
        parsed = simple_formula_parser(formula)
        if not parsed:
            return None
        mm = molecular_weight(parsed)
        return mm if mm > 0 else None
    except Exception:
        return None


# thermosteam's pint registry, cached at module scope after first use. Unlike a
# bare pint registry it parses SFF unit strings such as 'm3/hr' and 'USD/kg'.
_UREG = None


def _unit_is_parseable(unit_string):
    """True if `unit_string` is a unit the SFF unit system can parse. The empty
    string is treated as parseable: the schema documents '' as the explicit
    dimensionless sentinel for design results."""
    if unit_string == '':
        return True
    if not isinstance(unit_string, str) or not unit_string.strip():
        return False
    global _UREG
    if _UREG is None:
        try:
            from thermosteam.units_of_measure import ureg
        except Exception:
            return False
        _UREG = ureg
    try:
        _UREG.Quantity(1.0, unit_string)
        return True
    except Exception:
        return False


# Field names whose bare-number values resolve to units through
# quantity_units_global. Enumerated from the reference exporter's emitted shape
# (sff_checks.md C-03; QU-01/QU-03/QU-04 resolve against these). Per-object maps
# (quantity_units_for_design_results / _utility_results) are handled separately
# by UNIT-03 / UNIT-02 and are NOT listed here.
GLOBAL_QUANTITY_FIELDS = (
    'total_mass_flow', 'total_molar_flow', 'total_volumetric_flow',
    'temperature', 'pressure', 'enthalpy_flow', 'price', 'molar_mass',
    'regeneration_price', 'heat_transfer_price', 'electrical_energy_price',
    'temperature_limit',
)


def _present_global_quantity_fields(ctx):
    """Return the subset of GLOBAL_QUANTITY_FIELDS actually present as a
    quantity-bearing field somewhere in the document."""
    present = set()

    def scan(obj, names):
        for name in names:
            if isinstance(obj, dict) and isinstance(obj.get(name), (int, float)):
                present.add(name)

    stream_scalar = ('total_mass_flow', 'total_molar_flow',
                     'total_volumetric_flow', 'temperature', 'pressure',
                     'enthalpy_flow')
    for s in ctx.streams:
        if not isinstance(s, dict):
            continue
        scan(s, ('price',))
        sp = s.get('stream_properties') or {}
        scan(sp, stream_scalar)
        for phase in (sp.get('phases') or {}).values():
            scan(phase, ('total_mass_flow', 'total_molar_flow',
                         'total_volumetric_flow'))
    for c in ctx.chemicals:
        scan(c, ('molar_mass',))
    for u in ctx.utilities_list:
        scan(u, ('temperature', 'pressure', 'temperature_limit', 'price',
                 'regeneration_price', 'heat_transfer_price',
                 'electrical_energy_price'))
    return present


# Ordered registry of check(ctx) -> list[CheckResult]. Populated in Tasks 5-11
# and finalized in Task 12; empty here.
_CHECKS = []

# The constituent error-checks XREF-01 aggregates (see Task 11). Kept beside the
# registry so the aggregate and its parts cannot drift.
_REFERENTIAL_IDS = {
    'STR-02', 'STR-07', 'UNIT-02', 'UNIT-04', 'UNIT-06', 'CHEM-04', 'MET-02',
    'UTIL-04',
}


def validate_flowsheet_against_SFF(json_file, schema_file=None):
    """
    Validate an SFF flowsheet file against the full SFF contract.

    Runs the JSON-Schema gate (:func:`validate_json_against_schema`) and then
    every registered semantic check from ``sff_checks.md`` over shared read-only
    indexes. Each result is a :class:`CheckResult` citing its catalogue ID.

    Parameters
    ----------
    json_file : str or pathlib.Path
        Path to the SFF JSON file to validate.
    schema_file : str or pathlib.Path, optional
        Path to the SFF JSON Schema. Defaults to the schema shipped with this
        package.

    Returns
    -------
    (is_valid, results)
        is_valid : bool
            True unless the schema gate failed or any check produced an
            ``error``-severity ``fail``. ``warning`` and ``info`` findings never
            make ``is_valid`` False.
        results : list of CheckResult
            One entry per check outcome, schema gate first.
    """
    if schema_file is None:
        schema_file = _SCHEMA_FILE

    with Path(json_file).open('r', encoding='utf-8') as f:
        doc = json.load(f)

    results = []
    schema_valid, schema_errors = validate_json_against_schema(json_file, schema_file)
    results.append(CheckResult(
        'SCHEMA', 'error', 'pass' if schema_valid else 'fail',
        '' if schema_valid else '; '.join(schema_errors), '<root>'))

    ctx = _Context(doc)
    for check in _CHECKS:
        try:
            results.extend(check(ctx))
        except Exception as exc:  # a broken check must not sink the whole run
            results.append(_failed(
                getattr(check, 'check_id', check.__name__), 'error',
                f'check raised {type(exc).__name__}: {exc}'))

    is_valid = not any(r.status == 'fail' and r.severity == 'error'
                       for r in results)
    return is_valid, results
