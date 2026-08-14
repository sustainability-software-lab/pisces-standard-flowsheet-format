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

    # molar_mass(cid) is added in Task 4 (needs the chemicals library).


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
