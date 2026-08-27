# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
# 
# This module is under the MIT open-source license. See 
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

import datetime
import hashlib
import json
import math
import re
from collections import namedtuple
from pathlib import Path
from typing import Any, Tuple

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError

__all__ = ('validate_flowsheet_against_SFF', 'validate_json_against_schema',
           'CheckResult', 'evaluate_sff_tags', 'verify_reproducible')

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
TOL_STOICH_SIGFIGS = 3   # significant figures an equation coefficient is rounded to

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


#%% Checks -- units (sff_checks.md section 2)

def _duplicates(values):
    """Return the set of values that appear more than once (None ignored)."""
    seen, dup = set(), set()
    for v in values:
        if v is None:
            continue
        if v in seen:
            dup.add(v)
        seen.add(v)
    return dup


def _check_unit_id_uniqueness(ctx):  # UNIT-01
    dup = _duplicates(u.get('id') for u in ctx.units if isinstance(u, dict))
    if dup:
        return [_failed('UNIT-01', 'error',
                        f'duplicate unit id(s): {sorted(dup)}', 'units')]
    return [_passed('UNIT-01', 'error', 'units')]


def _check_utility_result_refs(ctx):  # UNIT-02
    bad, any_results = [], False
    for u in ctx.units:
        if not isinstance(u, dict):
            continue
        for key in ('utility_consumption_results', 'utility_production_results'):
            for uid in (u.get(key) or {}):
                any_results = True
                if uid not in ctx.util_ids:
                    bad.append(f"{u.get('id')}.{key}['{uid}']")
    if not any_results:
        return [_skipped('UNIT-02', 'error',
                         'no unit declares utility results', 'units')]
    if bad:
        return [_failed('UNIT-02', 'error',
                        f'utility-result keys reference no declared utility: {bad}',
                        'units')]
    return [_passed('UNIT-02', 'error', 'units')]


def _check_design_result_units_pairing(ctx):  # UNIT-03
    missing, orphan, any_dr = [], [], False
    for u in ctx.units:
        if not isinstance(u, dict) or not isinstance(u.get('design_results'), dict):
            continue
        any_dr = True
        dr = u['design_results']
        qu = u.get('quantity_units_for_design_results') or {}
        missing += [f"{u.get('id')}['{k}']" for k in dr if k not in qu]
        orphan += [f"{u.get('id')}['{k}']" for k in qu if k not in dr]
    if not any_dr:
        return [_skipped('UNIT-03', 'error',
                         'no unit declares design_results', 'units')]
    out = []
    if missing:
        out.append(_failed('UNIT-03', 'error',
                           f'design result(s) without quantity units: {missing}',
                           'units'))
    if orphan:
        out.append(_failed('UNIT-03', 'warning',
                           f'quantity-unit key(s) with no matching design result: '
                           f'{orphan}', 'units'))
    return out or [_passed('UNIT-03', 'error', 'units')]


def _iter_reactions(ctx):
    """Yield (unit, reaction) for every reaction dict in the document."""
    for u in ctx.units:
        if not isinstance(u, dict):
            continue
        for r in (u.get('reactions') or []):
            if isinstance(r, dict):
                yield u, r


def _has_reactions(ctx):
    """True if any unit declares at least one reaction (for tag skip policy)."""
    for _u, _r in _iter_reactions(ctx):
        return True
    return False


def _check_reaction_reactant_refs(ctx):  # UNIT-04 (validator part; conversion is schema)
    bad, any_reactant = [], False
    for u, r in _iter_reactions(ctx):
        if 'reactant' not in r:
            continue
        any_reactant = True
        if r['reactant'] not in ctx.chem_by_id:
            bad.append(f"{u.get('id')}: reactant '{r['reactant']}'")
    if not any_reactant:
        return [_skipped('UNIT-04', 'error',
                         'no reaction declares a reactant', 'units')]
    if bad:
        return [_failed('UNIT-04', 'error',
                        f'reaction reactant(s) reference no chemical: {bad}',
                        'units')]
    return [_passed('UNIT-04', 'error', 'units')]


def _stoich_to_coeffs(stoich, ctx):
    """Resolve a stoichiometry (array-by-index or object-by-index-or-id) to
    {chem_id: signed coeff} over nonzero entries. Returns (coeffs, None) or
    (None, reason)."""
    coeffs = {}
    if isinstance(stoich, list):
        if len(stoich) != len(ctx.chemicals):
            return None, (f'array length {len(stoich)} != number of chemicals '
                          f'{len(ctx.chemicals)}')
        for pos, val in enumerate(stoich):
            chem = ctx.chem_by_index.get(pos)
            if chem is None:
                return None, f'no chemical has index {pos}'
            if val:
                coeffs[chem.get('id')] = float(val)
        return coeffs, None
    if isinstance(stoich, dict):
        for key, val in stoich.items():
            chem = ctx.chem_by_id.get(key)
            if chem is None:
                try:
                    chem = ctx.chem_by_index.get(int(key))
                except (TypeError, ValueError):
                    chem = None
            if chem is None:
                return None, f"key '{key}' resolves to no chemical"
            if val:
                coeffs[chem.get('id')] = float(val)
        return coeffs, None
    return None, 'stoichiometry is neither array nor object'


def _parse_equation(equation, ctx):
    """Parse 'A + 2 B -> 3 C + D' to {chem_id: signed coeff} (LHS negative, RHS
    positive). Returns None if the arrow is missing or any species does not
    resolve to a chemical id (caller then skips the consistency check)."""
    if not isinstance(equation, str) or '->' not in equation:
        return None
    lhs, rhs = equation.split('->', 1)
    coeffs = {}
    for side, sign in ((lhs, -1.0), (rhs, 1.0)):
        for term in side.split('+'):
            term = term.strip()
            if not term:
                continue
            m = re.match(r'^(\d+(?:\.\d+)?)?\s*(.+?)$', term)
            if not m:
                return None
            coeff = float(m.group(1)) if m.group(1) else 1.0
            species = m.group(2).strip()
            if species not in ctx.chem_by_id:
                return None
            coeffs[species] = coeffs.get(species, 0.0) + sign * coeff
    return {k: v for k, v in coeffs.items() if v}


def _rounded_to_sigfigs(value, reference, sigfigs=None):
    """True if `value` equals `reference` rounded to `sigfigs` significant
    figures of `value` -- i.e. |value - reference| is within half a unit in the
    last significant figure of `value` (plus a floating-point epsilon)."""
    if sigfigs is None:
        sigfigs = TOL_STOICH_SIGFIGS
    if value == 0:
        return abs(reference) <= ZERO_FLOW
    last_place = math.floor(math.log10(abs(value))) - (sigfigs - 1)
    return abs(value - reference) <= 0.5 * 10 ** last_place + 1e-9 * abs(value)


def _same_reaction_up_to_scale(equation, stoichiometry):
    """True if the coefficient maps of an equation string and a stoichiometry
    describe one reaction up to a common positive scale factor: same component
    set, same signs, and each equation coefficient equal to the scaled
    stoichiometric coefficient rounded to TOL_STOICH_SIGFIGS significant
    figures. The scale is taken from a component the equation writes with a
    unit coefficient (the normalized reactant, which carries no rounding) when
    there is one, else from the first component. The rounding tolerance exists
    because exporters print equation coefficients rounded (BioSTEAM: three
    significant figures, 'Xylose -> 1.67 HP' for 1.6667) while the
    stoichiometry carries full precision."""
    a, b = equation, stoichiometry
    if set(a) != set(b):
        return False
    if any(b[k] == 0 or a[k] * b[k] <= 0 for k in a):
        return False
    anchor = next((k for k in a if abs(abs(a[k]) - 1.0) <= 1e-12), next(iter(a)))
    ratio = a[anchor] / b[anchor]
    return all(_rounded_to_sigfigs(a[k], b[k] * ratio) for k in a)


def _check_reaction_equation_stoichiometry_consistency(ctx):  # UNIT-05 (validator part)
    problems, checked = [], False
    for u, r in _iter_reactions(ctx):
        if 'equation' not in r or 'stoichiometry' not in r:
            continue
        eq = _parse_equation(r['equation'], ctx)
        st, err = _stoich_to_coeffs(r['stoichiometry'], ctx)
        if eq is None or err or not eq or not st:
            continue  # cannot verify -> skip (schema anyOf already ensured >=1)
        checked = True
        if not _same_reaction_up_to_scale(eq, st):
            problems.append(f"{u.get('id')}: equation {r['equation']!r} != "
                            f"stoichiometry {r['stoichiometry']}")
    if not checked:
        return [_skipped('UNIT-05', 'error',
                         'no reaction provides both equation and parseable '
                         'stoichiometry', 'units')]
    if problems:
        return [_failed('UNIT-05', 'error',
                        f'equation/stoichiometry disagree: {problems}', 'units')]
    return [_passed('UNIT-05', 'error', 'units')]


def _check_stoichiometry_wellformed(ctx):  # UNIT-06
    problems, any_stoich = [], False
    for u, r in _iter_reactions(ctx):
        if 'stoichiometry' not in r:
            continue
        any_stoich = True
        coeffs, err = _stoich_to_coeffs(r['stoichiometry'], ctx)
        if err:
            problems.append(f"{u.get('id')}: {err}")
            continue
        reactant = r.get('reactant')
        if reactant is not None:
            coeff = coeffs.get(reactant, 0.0)
            if coeff >= 0:
                problems.append(f"{u.get('id')}: reactant '{reactant}' has "
                                f'non-negative coefficient {coeff}')
    if not any_stoich:
        return [_skipped('UNIT-06', 'error',
                         'no reaction declares stoichiometry', 'units')]
    if problems:
        return [_failed('UNIT-06', 'error',
                        f'malformed stoichiometry: {problems}', 'units')]
    return [_passed('UNIT-06', 'error', 'units')]


def _check_unit_connectivity(ctx):  # UNIT-07
    connected = set()
    for s in ctx.streams:
        if isinstance(s, dict):
            connected.add(s.get('source_unit_id'))
            connected.add(s.get('sink_unit_id'))
    orphans = [uid for uid in ctx.unit_ids if uid not in connected]
    if orphans:
        return [_failed('UNIT-07', 'warning',
                        f'unit(s) attached to no stream: {sorted(orphans)}',
                        'units')]
    return [_passed('UNIT-07', 'warning', 'units')]


def _check_cost_correlation_refs(ctx):  # UNIT-08
    bad, any_corr = [], False
    for u in ctx.units:
        if not isinstance(u, dict):
            continue
        corr = u.get('purchase_cost_correlations')
        if not isinstance(corr, dict) or not corr:
            continue
        any_corr = True
        costs = u.get('purchase_costs')
        cost_keys = set(costs) if isinstance(costs, dict) else set()
        for item_id in corr:
            if item_id not in cost_keys:
                bad.append(f"{u.get('id')}: '{item_id}'")
    if not any_corr:
        return [_skipped('UNIT-08', 'warning',
                         'no unit declares purchase_cost_correlations', 'units')]
    if bad:
        return [_failed('UNIT-08', 'warning',
                        f'purchase_cost_correlations key(s) with no matching '
                        f'purchase_costs entry: {bad}', 'units')]
    return [_passed('UNIT-08', 'warning', 'units')]


def _check_cost_correlation_completeness(ctx):  # UNIT-09 (validator part; schema if/then covers power_law)
    bad, any_corr = [], False
    for u in ctx.units:
        if not isinstance(u, dict):
            continue
        corr = u.get('purchase_cost_correlations')
        if not isinstance(corr, dict) or not corr:
            continue
        any_corr = True
        for item_id, item in corr.items():
            if not isinstance(item, dict):
                continue
            ctype = item.get('correlation_type')
            has_cost = 'reference_cost' in item
            has_exp = 'exponent' in item
            if ctype == 'power_law' and not (has_cost and has_exp):
                missing = [k for k, present in
                           (('reference_cost', has_cost), ('exponent', has_exp))
                           if not present]
                bad.append(f"{u.get('id')}: '{item_id}' power_law missing {missing}")
            elif ctype == 'custom_function' and (has_cost or has_exp):
                present = [k for k, p in
                           (('reference_cost', has_cost), ('exponent', has_exp))
                           if p]
                bad.append(f"{u.get('id')}: '{item_id}' custom_function carries "
                           f"{present}")
    if not any_corr:
        return [_skipped('UNIT-09', 'error',
                         'no unit declares purchase_cost_correlations', 'units')]
    if bad:
        return [_failed('UNIT-09', 'error',
                        f'purchase_cost_correlations completeness violation(s): '
                        f'{bad}', 'units')]
    return [_passed('UNIT-09', 'error', 'units')]


def _check_units_present_identified(ctx):  # UNIT-10
    # Never skips: an empty flowsheet is a FAIL, so a tag can deny it.
    if not ctx.units:
        return [_failed('UNIT-10', 'warning',
                        'units is empty or absent', 'units')]
    bad = []
    for u in ctx.units:
        if not isinstance(u, dict):
            bad.append(f'non-object unit entry: {u!r}')
            continue
        if not u.get('id'):
            bad.append(f"unit with empty id (unit_type={u.get('unit_type')!r})")
        if not u.get('unit_type'):
            bad.append(f"unit '{u.get('id')}' has empty unit_type")
    if bad:
        return [_failed('UNIT-10', 'warning',
                        f'units not well-identified: {bad}', 'units')]
    return [_passed('UNIT-10', 'warning', 'units')]


#%% Checks -- streams: referential, roles, zero-flow (sff_checks.md section 3)

BOUNDARY = 'None'  # C-01 system-boundary sentinel written to source/sink_unit_id
TOPOLOGY_ROLES = ('input', 'output', 'internal')
DESIGNATION_ROLES = ('purchased_raw_material', 'feedstock', 'product')


_FLOW_SCALAR_NAMES = ('total_mass_flow', 'total_molar_flow', 'total_volumetric_flow')


def _flow_scalars_of(block):
    """Yield the present numeric flow scalars of one properties block (a
    stream's `stream_properties` or one of its `phases[]` entries)."""
    if not isinstance(block, dict):
        return
    for name in _FLOW_SCALAR_NAMES:
        v = block.get(name)
        if isinstance(v, (int, float)):
            yield v


def _stream_phases(stream):
    """Yield each phase block of a stream, in declaration order."""
    sp = (stream.get('stream_properties') or {}) if isinstance(stream, dict) else {}
    for phase in (sp.get('phases') or {}).values():
        if isinstance(phase, dict):
            yield phase


def _stream_flow_scalars(stream):
    """Yield every present numeric flow scalar of a stream: stream-level totals
    and each phase's totals. Non-flow scalars (T, P) are excluded."""
    sp = (stream.get('stream_properties') or {}) if isinstance(stream, dict) else {}
    yield from _flow_scalars_of(sp)
    for phase in _stream_phases(stream):
        yield from _flow_scalars_of(phase)


def _stream_compositions(stream):
    """Yield each phase composition list of a stream."""
    sp = (stream.get('stream_properties') or {}) if isinstance(stream, dict) else {}
    for phase in (sp.get('phases') or {}).values():
        if isinstance(phase, dict) and isinstance(phase.get('composition'), list):
            yield phase['composition']


def _stream_is_empty(stream):
    """True if every present flow scalar is ~zero and every composition is empty."""
    flows_zero = all(abs(v) <= ZERO_FLOW for v in _stream_flow_scalars(stream))
    comps_empty = all(len(c) == 0 for c in _stream_compositions(stream))
    return flows_zero and comps_empty


def _all_streams_empty(ctx):
    """True if every stream is empty (all flow scalars ~zero, all compositions
    empty). Used by the exported-from-simulator STR-10 tolerated-skip policy."""
    return all(_stream_is_empty(s) for s in ctx.streams if isinstance(s, dict))


def _check_stream_id_uniqueness(ctx):  # STR-01
    dup = _duplicates(s.get('id') for s in ctx.streams if isinstance(s, dict))
    if dup:
        return [_failed('STR-01', 'error',
                        f'duplicate stream id(s): {sorted(dup)}', 'streams')]
    return [_passed('STR-01', 'error', 'streams')]


def _check_stream_endpoint_refs(ctx):  # STR-02
    valid = ctx.unit_ids | {BOUNDARY}
    bad = []
    for s in ctx.streams:
        if not isinstance(s, dict):
            continue
        for end in ('source_unit_id', 'sink_unit_id'):
            if s.get(end) not in valid:
                bad.append(f"{s.get('id')}.{end}={s.get(end)!r}")
    if bad:
        return [_failed('STR-02', 'error',
                        f'stream endpoint(s) resolve to neither a unit nor the '
                        f'boundary: {bad}', 'streams')]
    return [_passed('STR-02', 'error', 'streams')]


def _check_isolated_stream_empty(ctx):  # STR-03
    bad, any_isolated = [], False
    for s in ctx.streams:
        if not isinstance(s, dict):
            continue
        if s.get('source_unit_id') == BOUNDARY and s.get('sink_unit_id') == BOUNDARY:
            any_isolated = True
            if not _stream_is_empty(s):
                bad.append(s.get('id'))
    if not any_isolated:
        return [_skipped('STR-03', 'error',
                         'no doubly-isolated streams', 'streams')]
    if bad:
        return [_failed('STR-03', 'error',
                        f'isolated stream(s) carry material/energy: {bad}',
                        'streams')]
    return [_passed('STR-03', 'error', 'streams')]


def _topology_roles(roles):
    return [r for r in roles if r in TOPOLOGY_ROLES]


def _check_stream_topology_role(ctx):  # STR-04
    bad, any_roles = [], False
    for s in ctx.streams:
        if not isinstance(s, dict) or not isinstance(s.get('roles'), list):
            continue
        any_roles = True
        if len(_topology_roles(s['roles'])) != 1:
            bad.append(f"{s.get('id')}: {s['roles']}")
    if not any_roles:
        return [_skipped('STR-04', 'error',
                         'no stream declares roles (pre-v0.0.10)', 'streams')]
    if bad:
        return [_failed('STR-04', 'error',
                        f'stream(s) without exactly one topology role: {bad}',
                        'streams')]
    return [_passed('STR-04', 'error', 'streams')]


def _expected_topology(stream):
    has_source = stream.get('source_unit_id') != BOUNDARY
    has_sink = stream.get('sink_unit_id') != BOUNDARY
    if has_source and has_sink:
        return 'internal'
    if has_sink:
        return 'input'
    if has_source:
        return 'output'
    return None  # doubly isolated -- STR-03 territory


def _check_stream_role_topology_agreement(ctx):  # STR-05
    bad, any_roles = [], False
    for s in ctx.streams:
        if not isinstance(s, dict) or not isinstance(s.get('roles'), list):
            continue
        topo = _topology_roles(s['roles'])
        if len(topo) != 1:
            continue  # STR-04 owns this
        any_roles = True
        expected = _expected_topology(s)
        if expected is not None and topo[0] != expected:
            bad.append(f"{s.get('id')}: role {topo[0]} but topology {expected}")
    if not any_roles:
        return [_skipped('STR-05', 'warning',
                         'no stream declares a single topology role', 'streams')]
    if bad:
        return [_failed('STR-05', 'warning',
                        f'topology role disagrees with connectivity: {bad}',
                        'streams')]
    return [_passed('STR-05', 'warning', 'streams')]


def _check_stream_designation_roles(ctx):  # STR-06
    bad, any_designation = [], False
    for s in ctx.streams:
        if not isinstance(s, dict) or not isinstance(s.get('roles'), list):
            continue
        roles = s['roles']
        designations = [r for r in roles if r in DESIGNATION_ROLES]
        if not designations:
            continue
        any_designation = True
        for d in designations:
            if d in ('feedstock', 'purchased_raw_material') and 'input' not in roles:
                bad.append(f"{s.get('id')}: {d} without input role")
            if d == 'product' and 'output' not in roles:
                bad.append(f"{s.get('id')}: product without output role")
    if not any_designation:
        return [_skipped('STR-06', 'warning',
                         'no stream carries a designation role', 'streams')]
    if bad:
        return [_failed('STR-06', 'warning',
                        f'designation role illegal for topology: {bad}', 'streams')]
    return [_passed('STR-06', 'warning', 'streams')]


def _check_composition_component_refs(ctx):  # STR-07
    bad, any_component = [], False
    for s in ctx.streams:
        if not isinstance(s, dict):
            continue
        for comp in _stream_compositions(s):
            for entry in comp:
                if not isinstance(entry, dict):
                    continue
                any_component = True
                name = entry.get('component_name')
                if name not in ctx.chem_by_id:
                    bad.append(f"{s.get('id')}: '{name}'")
    if not any_component:
        return [_skipped('STR-07', 'error',
                         'no stream declares composition components', 'streams')]
    if bad:
        return [_failed('STR-07', 'error',
                        f'composition component(s) reference no chemical: {bad}',
                        'streams')]
    return [_passed('STR-07', 'error', 'streams')]


def _zero_flow_inconsistent(scalars, compositions):
    """The STR-13 predicate for one scope (a whole stream, or one phase):
    (None) when no flow scalar is zero -- not applicable; True when some
    flow scalar is zero but another is not, or a composition is non-empty."""
    scalars = list(scalars)
    if not any(abs(v) <= ZERO_FLOW for v in scalars):
        return None
    return (not all(abs(v) <= ZERO_FLOW for v in scalars)
            or any(len(c) for c in compositions))


def _check_zero_flow_consistency(ctx):  # STR-13
    # Evaluated per scope: the stream's own totals against the whole stream
    # (all phases), and each phase against itself only. An empty phase beside
    # a populated one -- e.g. the all-zero 'g' phase of a liquid MultiStream --
    # is not a contradiction and must not fail the stream.
    bad, any_zero = [], False
    for s in ctx.streams:
        if not isinstance(s, dict):
            continue
        sp = s.get('stream_properties') or {}
        phases = list(_stream_phases(s))
        # Stream scope: the stream's own totals, judged against everything the
        # stream carries (its totals, every phase's flows, every composition).
        stream_scalars = list(_flow_scalars_of(sp))
        stream_verdict = _zero_flow_inconsistent(
            stream_scalars
            + [v for phase in phases for v in _flow_scalars_of(phase)],
            [phase.get('composition') for phase in phases
             if isinstance(phase.get('composition'), list)])
        if not any(abs(v) <= ZERO_FLOW for v in stream_scalars):
            stream_verdict = None  # only a zero STREAM total triggers this scope
        verdicts = [stream_verdict]
        for phase in phases:
            comp = phase.get('composition')
            verdicts.append(_zero_flow_inconsistent(
                _flow_scalars_of(phase),
                [comp] if isinstance(comp, list) else []))
        if all(v is None for v in verdicts):
            continue  # no zero flow present -> not applicable to this stream
        any_zero = True
        if any(v for v in verdicts):
            bad.append(s.get('id'))
    if not any_zero:
        return [_skipped('STR-13', 'error',
                         'no stream has a zero flow scalar', 'streams')]
    if bad:
        return [_failed('STR-13', 'error',
                        f'stream(s) with a zero flow but nonzero other flow/'
                        f'composition: {bad}', 'streams')]
    return [_passed('STR-13', 'error', 'streams')]


def _check_streams_present_identified(ctx):  # STR-14
    # Never skips: an empty stream set is a FAIL. Endpoint validity is STR-02.
    if not ctx.streams:
        return [_failed('STR-14', 'warning',
                        'streams is empty or absent', 'streams')]
    bad = []
    for s in ctx.streams:
        if not isinstance(s, dict):
            bad.append(f'non-object stream entry: {s!r}')
            continue
        if not s.get('id'):
            bad.append(f'stream with empty id (source={s.get("source_unit_id")!r})')
    if bad:
        return [_failed('STR-14', 'warning',
                        f'streams not well-identified: {bad}', 'streams')]
    return [_passed('STR-14', 'warning', 'streams')]


#%% Checks -- streams: material balance (sff_checks.md section 3 + 7 (i)/(ii))

def _rel_close(a, b, rel_tol):
    """True if a and b agree to a relative tolerance (scaled by the larger
    magnitude), with an absolute floor of ZERO_FLOW so a value at or below the
    module's zero threshold compared against exact zero counts as agreement
    (a bare relative test is degenerate when one side is exactly 0)."""
    scale = max(abs(a), abs(b))
    if scale == 0:
        return True
    return abs(a - b) <= max(rel_tol * scale, ZERO_FLOW)


def _check_fraction_sums(ctx):  # STR-08 (material balance (i))
    bad, any_comp = [], False
    for s in ctx.streams:
        if not isinstance(s, dict):
            continue
        for phase_symbol, comp in _iter_named_compositions(s):
            entries = [e for e in comp if isinstance(e, dict)]
            if not entries:
                continue
            any_comp = True
            for frac_key in ('mol_fraction', 'mass_fraction'):
                # Sum a fraction field only when EVERY entry declares it
                # numerically; mass_fraction is schema-optional per entry, so a
                # partial sum would false-fail against 1.0.
                values = [e.get(frac_key) for e in entries]
                if not all(isinstance(v, (int, float)) for v in values):
                    continue
                total = sum(values)
                if abs(total - 1.0) > TOL_FRACTION:
                    bad.append(f"{s.get('id')}[{phase_symbol}].{frac_key}={total:.6g}")
    if not any_comp:
        return [_skipped('STR-08', 'warning',
                         'no non-empty composition', 'streams')]
    if bad:
        return [_failed('STR-08', 'warning',
                        f'composition fractions do not sum to 1: {bad}', 'streams')]
    return [_passed('STR-08', 'warning', 'streams')]


def _iter_named_compositions(stream):
    """Yield (phase_symbol, composition_list) for each phase of a stream."""
    sp = (stream.get('stream_properties') or {}) if isinstance(stream, dict) else {}
    for symbol, phase in (sp.get('phases') or {}).items():
        if isinstance(phase, dict) and isinstance(phase.get('composition'), list):
            yield symbol, phase['composition']


def _check_phase_flow_sums(ctx):  # STR-09
    bad, any_check = [], False
    for s in ctx.streams:
        if not isinstance(s, dict):
            continue
        sp = s.get('stream_properties') or {}
        phases = sp.get('phases') or {}
        dict_phases = [p for p in phases.values() if isinstance(p, dict)]
        for name in ('total_mass_flow', 'total_molar_flow', 'total_volumetric_flow'):
            stream_total = sp.get(name)
            if not isinstance(stream_total, (int, float)) or not dict_phases:
                continue
            # Compare only when EVERY phase declares this field numerically;
            # per-phase totals are schema-optional, so a partial sum would
            # false-fail against the (required) stream-level total.
            phase_values = [p.get(name) for p in dict_phases]
            if not all(isinstance(v, (int, float)) for v in phase_values):
                continue
            any_check = True
            if not _rel_close(sum(phase_values), stream_total, TOL_FLOW):
                bad.append(f"{s.get('id')}.{name}: phases sum "
                           f"{sum(phase_values):.6g} != total {stream_total:.6g}")
    if not any_check:
        return [_skipped('STR-09', 'warning',
                         'no field present on all phases alongside the stream total',
                         'streams')]
    if bad:
        return [_failed('STR-09', 'warning',
                        f'phase flows do not sum to the stream total: {bad}',
                        'streams')]
    return [_passed('STR-09', 'warning', 'streams')]


def _mean_molar_mass(composition, ctx):
    """Composition-weighted molar mass (C-05) over a phase composition, or None
    if any present component's molar mass cannot be resolved."""
    total = 0.0
    for entry in composition:
        if not isinstance(entry, dict):
            continue
        x = entry.get('mol_fraction')
        if not isinstance(x, (int, float)):
            return None
        m = ctx.molar_mass(entry.get('component_name'))
        if m is None:
            return None
        total += x * m
    return total


def _check_mass_molar_flow_consistency(ctx):  # STR-10 (material balance (ii))
    bad, any_check = [], False
    for s in ctx.streams:
        if not isinstance(s, dict) or _stream_is_empty(s):
            continue
        sp = s.get('stream_properties') or {}
        # Evaluate per phase (each phase has a single, well-defined composition).
        for symbol, comp in _iter_named_compositions(s):
            phase = sp['phases'][symbol]
            mass = phase.get('total_mass_flow')
            molar = phase.get('total_molar_flow')
            if not comp or not isinstance(mass, (int, float)) \
                    or not isinstance(molar, (int, float)):
                continue
            mbar = _mean_molar_mass(comp, ctx)
            if mbar is None:
                continue  # unresolved molar mass -> skip this phase
            any_check = True
            predicted = molar * mbar  # kmol/hr * g/mol == kg/hr numerically
            if not _rel_close(predicted, mass, TOL_FLOW):
                bad.append(f"{s.get('id')}[{symbol}]: mass {mass:.6g} != "
                           f"molar*M_bar {predicted:.6g}")
    if not any_check:
        return [_skipped('STR-10', 'warning',
                         'no phase with mass, molar, and resolvable molar masses',
                         'streams')]
    if bad:
        return [_failed('STR-10', 'warning',
                        f'mass flow disagrees with molar*molar_mass: {bad}',
                        'streams')]
    return [_passed('STR-10', 'warning', 'streams')]


#%% Checks -- chemicals (sff_checks.md section 4)

def _check_chemical_id_index_uniqueness(ctx):  # CHEM-01
    dup_id = _duplicates(c.get('id') for c in ctx.chemicals if isinstance(c, dict))
    dup_idx = _duplicates(c['index'] for c in ctx.chemicals
                          if isinstance(c, dict) and 'index' in c)
    problems = []
    if dup_id:
        problems.append(f'duplicate id(s): {sorted(dup_id)}')
    if dup_idx:
        problems.append(f'duplicate index(es): {sorted(dup_idx)}')
    if problems:
        return [_failed('CHEM-01', 'error', '; '.join(problems), 'chemicals')]
    return [_passed('CHEM-01', 'error', 'chemicals')]


def _check_molar_mass_positive(ctx):  # CHEM-02
    bad, any_check = [], False
    for c in ctx.chemicals:
        if not isinstance(c, dict):
            continue
        mm = c.get('molar_mass')
        if not isinstance(mm, (int, float)):
            continue
        any_check = True
        if mm <= 0:
            bad.append(f"{c.get('id')}: {mm:.6g}")
    if not any_check:
        return [_skipped('CHEM-02', 'warning',
                         'no chemical declares molar_mass', 'chemicals')]
    if bad:
        return [_failed('CHEM-02', 'warning',
                        f'non-positive molar_mass: {bad}', 'chemicals')]
    return [_passed('CHEM-02', 'warning', 'chemicals')]


def _check_formula_molar_mass_agreement(ctx):  # CHEM-03
    bad, any_check = [], False
    for c in ctx.chemicals:
        if not isinstance(c, dict):
            continue
        formula, declared = c.get('formula'), c.get('molar_mass')
        if not formula or not isinstance(declared, (int, float)):
            continue
        computed = _molar_mass_from_formula(formula)
        if computed is None:
            continue
        any_check = True
        if not _rel_close(computed, declared, TOL_MOLAR_MASS):
            bad.append(f"{c.get('id')}: formula {computed:.6g} != declared "
                       f"{declared:.6g}")
    if not any_check:
        return [_skipped('CHEM-03', 'warning',
                         'no chemical has both a parseable formula and molar_mass',
                         'chemicals')]
    if bad:
        return [_failed('CHEM-03', 'warning',
                        f'formula/molar_mass disagree: {bad}', 'chemicals')]
    return [_passed('CHEM-03', 'warning', 'chemicals')]


def _reaction_uses_index_stoichiometry(reaction, ctx):
    """True if a reaction's stoichiometry is index-based: an array, or an object
    with a key that is not a chemical id (hence an index)."""
    st = reaction.get('stoichiometry')
    if isinstance(st, list):
        return True
    if isinstance(st, dict):
        return any(k not in ctx.chem_by_id for k in st)
    return False


def _check_index_coverage(ctx):  # CHEM-04
    uses_index = any(_reaction_uses_index_stoichiometry(r, ctx)
                     for _, r in _iter_reactions(ctx))
    if not uses_index:
        return [_skipped('CHEM-04', 'error',
                         'no reaction uses index-based stoichiometry', 'chemicals')]
    missing = [c.get('id') for c in ctx.chemicals
               if isinstance(c, dict) and 'index' not in c]
    if missing:
        return [_failed('CHEM-04', 'error',
                        f'index-based stoichiometry present but chemical(s) lack '
                        f'index: {missing}', 'chemicals')]
    indices = sorted(c['index'] for c in ctx.chemicals if isinstance(c, dict))
    if indices != list(range(len(ctx.chemicals))):
        return [_failed('CHEM-04', 'error',
                        f'chemical indices are not a 0..n-1 set: {indices}',
                        'chemicals')]
    return [_passed('CHEM-04', 'error', 'chemicals')]


def _referenced_chemical_ids(ctx):
    """The set of chemical ids referenced by any composition or reaction."""
    refs = set()
    for s in ctx.streams:
        if isinstance(s, dict):
            for comp in _stream_compositions(s):
                for e in comp:
                    if isinstance(e, dict):
                        refs.add(e.get('component_name'))
    for u in ctx.utilities_list:
        for e in (u.get('composition') or []):
            if isinstance(e, dict):
                refs.add(e.get('component_name'))
    for _, r in _iter_reactions(ctx):
        if 'reactant' in r:
            refs.add(r['reactant'])
        eq = _parse_equation(r.get('equation', ''), ctx)
        if eq:
            refs.update(eq)
        if 'stoichiometry' in r:
            coeffs, _err = _stoich_to_coeffs(r['stoichiometry'], ctx)
            if coeffs:
                refs.update(coeffs)
    return refs


def _check_unused_chemicals(ctx):  # CHEM-05
    # Skipped when: never (sff_checks.md). An empty chemicals registry is a
    # vacuous pass -- every one of zero chemicals is trivially referenced.
    refs = _referenced_chemical_ids(ctx)
    unused = [cid for cid in ctx.chem_by_id if cid not in refs]
    if unused:
        return [_failed('CHEM-05', 'info',
                        f'chemical(s) referenced nowhere: {sorted(unused)}',
                        'chemicals')]
    return [_passed('CHEM-05', 'info', 'chemicals')]


#%% Checks -- utilities (sff_checks.md section 5)

def _check_utility_id_uniqueness(ctx):  # UTIL-01
    # Skipped when: never (sff_checks.md). An empty utilities registry has no
    # duplicates, so this is a genuine (vacuous) pass, not a skip.
    dup = _duplicates(u.get('id') for u in ctx.utilities_list)
    if dup:
        return [_failed('UTIL-01', 'error',
                        f'duplicate utility id(s) across groups: {sorted(dup)}',
                        'utilities')]
    return [_passed('UTIL-01', 'error', 'utilities')]


def _check_unused_utilities(ctx):  # UTIL-02
    # Skipped when: never (sff_checks.md). Zero declared utilities are
    # trivially all "referenced", so this is a genuine (vacuous) pass.
    used = set()
    for u in ctx.units:
        if not isinstance(u, dict):
            continue
        for key in ('utility_consumption_results', 'utility_production_results'):
            used.update(u.get(key) or {})
    unused = [uid for uid in ctx.util_by_id if uid not in used]
    if unused:
        return [_failed('UTIL-02', 'info',
                        f'utility(ies) referenced by no unit: {sorted(unused)}',
                        'utilities')]
    return [_passed('UTIL-02', 'info', 'utilities')]


def _check_utility_result_units_parseable(ctx):  # UTIL-03
    # Skipped when: never (sff_checks.md) -- the field is schema-required for
    # each group, so an empty utilities registry is a genuine (vacuous) pass.
    bad = []
    for u in ctx.utilities_list:
        s = u.get('quantity_units_for_utility_results')
        if not isinstance(s, str) or s == '' or not _unit_is_parseable(s):
            bad.append(f"{u.get('id')}: {s!r}")
    if bad:
        return [_failed('UTIL-03', 'warning',
                        f'utility-result quantity units empty/unparseable: {bad}',
                        'utilities')]
    return [_passed('UTIL-03', 'warning', 'utilities')]


def _check_utility_composition(ctx):  # UTIL-04
    ref_bad, sum_bad, any_comp = [], [], False
    for u in ctx.utilities_list:
        comp = u.get('composition')
        if not isinstance(comp, list) or not comp:
            continue
        any_comp = True
        values = []
        for e in comp:
            if not isinstance(e, dict):
                continue
            if e.get('component_name') not in ctx.chem_by_id:
                ref_bad.append(f"{u.get('id')}: '{e.get('component_name')}'")
            if isinstance(e.get('mol_fraction'), (int, float)):
                values.append(e['mol_fraction'])
        if values and abs(sum(values) - 1.0) > TOL_FRACTION:
            sum_bad.append(f"{u.get('id')}: {sum(values):.6g}")
    if not any_comp:
        return [_skipped('UTIL-04', 'error',
                         'no utility declares a composition', 'utilities')]
    out = []
    if ref_bad:
        out.append(_failed('UTIL-04', 'error',
                           f'utility composition component(s) reference no '
                           f'chemical: {ref_bad}', 'utilities'))
    if sum_bad:
        out.append(_failed('UTIL-04', 'warning',
                           f'utility composition fractions do not sum to 1: '
                           f'{sum_bad}', 'utilities'))
    return out or [_passed('UTIL-04', 'error', 'utilities')]


#%% Checks -- quantity units (sff_checks.md section 6)

def _alias_index(ctx):
    """Map each alias -> list of quantity_units_global entry keys declaring it."""
    idx = {}
    for key, entry in ctx.qug.items():
        if not isinstance(entry, dict):
            continue
        for alias in (entry.get('aliases') or []):
            idx.setdefault(alias, []).append(key)
    return idx


def _check_quantity_unit_pairing(ctx):  # QU-01 (global side; per-object maps are
                                        # covered by UNIT-02 / UNIT-03)
    # Skipped when: never (sff_checks.md). Zero present quantity fields is a
    # vacuous pass -- every one of zero fields is trivially resolvable.
    present = _present_global_quantity_fields(ctx)
    idx = _alias_index(ctx)
    unresolved = sorted(f for f in present if f not in idx)
    if unresolved:
        return [_failed('QU-01', 'error',
                        f'quantity field(s) with no quantity_units_global alias: '
                        f'{unresolved}', 'quantity_units_global')]
    return [_passed('QU-01', 'error', 'quantity_units_global')]


def _iter_quantity_unit_strings(ctx):
    """Yield (location, unit_string, empty_allowed) for every quantity-unit
    string. Empty '' is permitted only for design-result entries (schema-
    documented dimensionless sentinel)."""
    for key, entry in ctx.qug.items():
        if isinstance(entry, dict) and 'quantity_units' in entry:
            yield f'quantity_units_global.{key}', entry['quantity_units'], False
    for u in ctx.units:
        if not isinstance(u, dict):
            continue
        for k, v in (u.get('quantity_units_for_design_results') or {}).items():
            yield f"{u.get('id')}.design['{k}']", v, True
    for u in ctx.utilities_list:
        if 'quantity_units_for_utility_results' in u:
            yield (f"{u.get('id')}.utility_results",
                   u['quantity_units_for_utility_results'], False)


def _check_quantity_unit_strings_parseable(ctx):  # QU-02
    # Skipped when: never (sff_checks.md). Zero quantity-unit strings present
    # is a vacuous pass.
    bad = []
    for loc, s, empty_ok in _iter_quantity_unit_strings(ctx):
        if s == '' and empty_ok:
            continue
        if not isinstance(s, str) or s == '' or not _unit_is_parseable(s):
            bad.append(f'{loc}={s!r}')
    if bad:
        return [_failed('QU-02', 'error',
                        f'empty/unparseable quantity-unit string(s): {bad}',
                        'quantity_units_global')]
    return [_passed('QU-02', 'error', 'quantity_units_global')]


def _check_alias_uniqueness(ctx):  # QU-03
    # Skipped when: never (sff_checks.md). An empty registry has no
    # ambiguous aliases, so this is a genuine (vacuous) pass.
    idx = _alias_index(ctx)
    # Ambiguous only when an alias spans more than one DISTINCT entry; a
    # duplicate alias within a single entry's own list (schema allows it --
    # no uniqueItems) is not a cross-entry collision.
    ambiguous = {a: sorted(set(keys)) for a, keys in idx.items()
                 if len(set(keys)) > 1}
    if ambiguous:
        return [_failed('QU-03', 'error',
                        f'alias(es) under multiple quantity_units_global entries: '
                        f'{ambiguous}', 'quantity_units_global')]
    return [_passed('QU-03', 'error', 'quantity_units_global')]


def _check_unused_aliases(ctx):  # QU-04 (entry granularity -- see the plan note)
    # Skipped when: never (sff_checks.md). An empty registry has no unused
    # entries, so this is a genuine (vacuous) pass, not a skip.
    present = _present_global_quantity_fields(ctx)
    unused = []
    for key, entry in ctx.qug.items():
        if not isinstance(entry, dict):
            continue
        aliases = entry.get('aliases') or []
        if not any(a in present for a in aliases):
            unused.append(key)
    if unused:
        return [_failed('QU-04', 'info',
                        f'quantity_units_global entry(ies) whose aliases match no '
                        f'present field: {sorted(unused)}', 'quantity_units_global')]
    return [_passed('QU-04', 'info', 'quantity_units_global')]


#%% Checks -- metadata + cross-object (sff_checks.md sections 1 + 7)

def _check_metadata_stream_refs(ctx):  # MET-02
    bad, any_ref = [], False
    for key in ('feedstocks', 'products'):
        for entry in (ctx.metadata.get(key) or []):
            if not isinstance(entry, dict) or 'stream_id' not in entry:
                continue
            any_ref = True
            if entry['stream_id'] not in ctx.stream_ids:
                bad.append(f"{key}: '{entry['stream_id']}'")
    if not any_ref:
        return [_skipped('MET-02', 'error',
                         'no feedstock/product stream references', 'metadata')]
    if bad:
        return [_failed('MET-02', 'error',
                        f'metadata stream reference(s) resolve to no stream: {bad}',
                        'metadata')]
    return [_passed('MET-02', 'error', 'metadata')]


def _check_metadata_role_agreement(ctx):  # MET-03
    stream_roles = {s.get('id'): s.get('roles') for s in ctx.streams
                    if isinstance(s, dict)}
    bad, any_check = [], False
    for key, role in (('feedstocks', 'feedstock'), ('products', 'product')):
        for entry in (ctx.metadata.get(key) or []):
            if not isinstance(entry, dict):
                continue
            roles = stream_roles.get(entry.get('stream_id'))
            if not isinstance(roles, list):
                continue  # pre-v0.0.10 stream, or reference unresolved (MET-02)
            any_check = True
            if role not in roles:
                bad.append(f"{key} stream '{entry.get('stream_id')}' lacks role "
                           f"'{role}'")
    if not any_check:
        return [_skipped('MET-03', 'warning',
                         'no referenced stream carries a roles array', 'metadata')]
    if bad:
        return [_failed('MET-03', 'warning',
                        f'metadata/stream role disagreement: {bad}', 'metadata')]
    return [_passed('MET-03', 'warning', 'metadata')]


def _check_tea_year_plausible(ctx):  # MET-04
    # Skipped when: TEA_year absent (sff_checks.md). Warning severity -- a wild
    # year is suspicious but does not make the file non-conforming, so this is a
    # validator warning rather than a schema constraint. The upper bound is
    # computed at validation time (current calendar year + 1) so it never needs
    # an annual schema bump.
    year = ctx.metadata.get('TEA_year')
    if not isinstance(year, (int, float)):
        return [_skipped('MET-04', 'warning', 'TEA_year absent', 'metadata')]
    upper = datetime.date.today().year + 1
    if not (1900 <= year <= upper):
        return [_failed('MET-04', 'warning',
                        f'TEA_year {year} outside plausible range [1900, {upper}]',
                        'metadata')]
    return [_passed('MET-04', 'warning', 'metadata')]


def _check_reproducibility_content_digests(ctx):  # MET-07
    # MET-06 (schema) checks each sha256 is 64-hex; MET-07 checks it is CORRECT:
    # sha256 of the content string's UTF-8 bytes, hashed as-is (LF endings are
    # load-bearing for agreement with a Linux/CI export).
    repro = ctx.metadata.get('reproducibility')
    if not isinstance(repro, dict):
        return [_skipped('MET-07', 'error',
                         'no reproducibility block', 'metadata')]
    bad, any_block = [], False
    for name in ('environment', 'load_script', 'extended_metadata'):
        block = repro.get(name)
        if not isinstance(block, dict):
            continue
        content, digest = block.get('content'), block.get('sha256')
        if not isinstance(content, str) or not isinstance(digest, str):
            continue
        any_block = True
        actual = hashlib.sha256(content.encode('utf-8')).hexdigest()
        if actual != digest:
            bad.append(f"{name}: stored {digest[:12]}... != actual {actual[:12]}...")
    if not any_block:
        return [_skipped('MET-07', 'error',
                         'no reproducibility block carries content+sha256',
                         'metadata')]
    if bad:
        return [_failed('MET-07', 'error',
                        f'reproducibility content/digest mismatch: {bad}',
                        'metadata')]
    return [_passed('MET-07', 'error', 'metadata')]


def _check_boundary_streams_exist(ctx):  # GRAPH-01
    # Skipped when: never (sff_checks.md). An empty streams array has neither a
    # boundary input nor output, which is exactly the truncated-export case this
    # check exists to catch -- so it fails (warning), it does not skip.
    has_in = any(isinstance(s, dict) and s.get('source_unit_id') == BOUNDARY
                 and s.get('sink_unit_id') != BOUNDARY for s in ctx.streams)
    has_out = any(isinstance(s, dict) and s.get('sink_unit_id') == BOUNDARY
                  and s.get('source_unit_id') != BOUNDARY for s in ctx.streams)
    missing = []
    if not has_in:
        missing.append('no boundary input (source_unit_id "None")')
    if not has_out:
        missing.append('no boundary output (sink_unit_id "None")')
    if missing:
        return [_failed('GRAPH-01', 'warning', '; '.join(missing), 'streams')]
    return [_passed('GRAPH-01', 'warning', 'streams')]


def _xref_gate(results):  # XREF-01
    """Aggregate: fail if any constituent referential error-check failed."""
    ref_fail = any(r.check_id in _REFERENTIAL_IDS and r.status == 'fail'
                   and r.severity == 'error' for r in results)
    return CheckResult('XREF-01', 'error', 'fail' if ref_fail else 'pass',
                       'a referential check failed' if ref_fail else '', '<root>')


# _CHECKS is defined at the end of this module, after all _check_* functions.

# The constituent error-checks XREF-01 aggregates (see Task 11). Kept beside the
# registry so the aggregate and its parts cannot drift.
_REFERENTIAL_IDS = {
    'STR-02', 'STR-07', 'UNIT-02', 'UNIT-04', 'UNIT-06', 'CHEM-04', 'MET-02',
    'UTIL-04',
}


def _schema_gate(doc, schema):
    """Run the JSON-Schema gate on an in-memory document, returning one
    CheckResult (id 'SCHEMA'). In-memory so both the file-based validator and the
    exporter's tag self-check share one gate without a temp file."""
    try:
        validator = Draft7Validator(schema)
    except SchemaError as e:
        return CheckResult('SCHEMA', 'error', 'fail',
                           f'Invalid schema: {e.message}', '<root>')
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    if not errors:
        return CheckResult('SCHEMA', 'error', 'pass', '', '<root>')
    msgs = ['{}: {}'.format('.'.join(str(p) for p in e.path) or '<root>', e.message)
            for e in errors]
    return CheckResult('SCHEMA', 'error', 'fail', '; '.join(msgs), '<root>')


def _run_all_checks(doc, schema):
    """Run the schema gate and every registered check over one document, returning
    (ctx, results). results is schema-gate first, then _CHECKS, then the XREF-01
    aggregate. TAG-01 is NOT included here -- callers append it (or not) via
    _tag_gate. Shared by validate_flowsheet_against_SFF, evaluate_sff_tags, and
    the exporter's static-tag self-check."""
    results = [_schema_gate(doc, schema)]
    ctx = _Context(doc)
    for check in _CHECKS:
        try:
            results.extend(check(ctx))
        except Exception as exc:  # a broken check must not sink the whole run
            results.append(_failed(
                getattr(check, 'check_id', check.__name__), 'error',
                f'check raised {type(exc).__name__}: {exc}'))
    results.append(_xref_gate(results))
    return ctx, results


#%% Tags (sff_checks.md section 8)

# The committed tag registry: THE single source of truth for tag names, check
# subsets, and tolerated-skip policies (sff_checks.md section 8). The schema's
# metadata.tags enum mirrors the tag names; tests/tier1/test_tag_registry.py
# pins the sync.
_TAGS_YAML = Path(__file__).resolve().parent / 'tags' / 'tags.yaml'

# Condition names a tolerated_skips entry in tags.yaml may reference. The
# registry names the *policy* (which check is tolerated, under what
# circumstance); these predicates are the implementation. An unknown name in
# the YAML is rejected at load time by _load_tag_registry. Predicates read
# only the read-only _Context.
_TOLERATED_SKIP_CONDITIONS = {
    'always': lambda ctx: True,
    'all_streams_empty': lambda ctx: _all_streams_empty(ctx),
    'no_reactions': lambda ctx: not _has_reactions(ctx),
}

_TAG_CLASSES = ('static', 'harness')

#: Cache for the parsed registry (the committed file is immutable at runtime).
#: Tests that repoint _TAGS_YAML must reset this to None and restore both.
_TAG_REGISTRY_CACHE = None


def _yaml_load_no_duplicates(text, source):
    """yaml.safe_load, except a mapping key repeated at any depth raises
    ValueError naming `source` and the key. PyYAML's safe_load silently keeps
    the last occurrence of a duplicated key, which would let a registry edit
    override an earlier entry unnoticed. Deliberately duplicated across
    _validate.py/_registry.py/_design_specs.py: each must stay loadable with
    no package-relative imports (file-path loading, script form), so they
    cannot share one import."""
    import yaml  # lazy: keep the module import-light

    class _NoDuplicateKeyLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            seen = set()
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=deep)
                try:
                    duplicated = key in seen
                    seen.add(key)
                except TypeError:
                    continue  # unhashable key: super() raises its own error
                if duplicated:
                    raise ValueError(
                        f'{source}: duplicate YAML key {key!r}')
            return super().construct_mapping(node, deep=deep)

    return yaml.load(text, Loader=_NoDuplicateKeyLoader)


def _load_tag_registry():
    """Parse, shape-validate, and cache ``pisces_sff/tags/tags.yaml``.

    Returns
    -------
    dict
        ``{tag_name: entry}`` in YAML declaration order (the canonical tag
        order). A ``static`` entry holds ``subset`` (``None`` for the
        "all checks that ran" sentinel, else a frozenset of check ids) and
        ``tolerated_skips`` (``{check_id: condition_name}``, possibly empty);
        a ``harness`` entry holds only ``class``.

    Raises
    ------
    ImportError
        pyyaml is not installed.
    ValueError
        The registry file is missing, unreadable, not valid YAML, repeats a
        mapping key, or is otherwise malformed. Failing fast is deliberate:
        the registry is committed repo infrastructure, not document content,
        so a broken registry must abort tag evaluation rather than silently
        skip the TAG-01 gate (Tier 1 pins the committed file's validity).
    """
    global _TAG_REGISTRY_CACHE
    if _TAG_REGISTRY_CACHE is not None:
        return _TAG_REGISTRY_CACHE
    try:
        import yaml
    except ImportError as e:  # pragma: no cover - env-dependent
        raise ImportError(
            'pyyaml is required for SFF tag evaluation (it reads the tag '
            'registry pisces_sff/tags/tags.yaml)') from e
    try:
        text = _TAGS_YAML.read_text(encoding='utf-8')
    except OSError as e:
        raise ValueError(
            f'tag registry not readable: {_TAGS_YAML}: {e}') from e
    try:
        raw = _yaml_load_no_duplicates(text, _TAGS_YAML)
    except yaml.YAMLError as e:
        raise ValueError(
            f'tag registry is not valid YAML: {_TAGS_YAML}: {e}') from e
    if (not isinstance(raw, dict) or set(raw) != {'tags'}
            or not isinstance(raw['tags'], dict) or not raw['tags']):
        raise ValueError(
            f'{_TAGS_YAML}: expected a single top-level "tags" mapping '
            f'with at least one entry')
    registry = {}
    for tag, entry in raw['tags'].items():
        if not isinstance(entry, dict):
            raise ValueError(
                f'{_TAGS_YAML}: tag {tag!r}: entry must be a mapping')
        unknown = set(entry) - {'class', 'subset', 'tolerated_skips'}
        if unknown:
            raise ValueError(
                f'{_TAGS_YAML}: tag {tag!r}: unknown key(s) {sorted(unknown)}')
        cls = entry.get('class')
        if cls not in _TAG_CLASSES:
            raise ValueError(
                f'{_TAGS_YAML}: tag {tag!r}: "class" must be one of '
                f'{_TAG_CLASSES}, got {cls!r}')
        if cls == 'harness':
            if 'subset' in entry or 'tolerated_skips' in entry:
                raise ValueError(
                    f'{_TAGS_YAML}: tag {tag!r}: a harness tag may not '
                    f'declare "subset" or "tolerated_skips" (its earning '
                    f'rule is code)')
            registry[tag] = {'class': cls}
            continue
        subset = entry.get('subset')
        if subset == 'all':
            subset = None  # sentinel: every check id that ran
        elif (isinstance(subset, list) and subset
                and all(isinstance(c, str) for c in subset)):
            subset = frozenset(subset)
        else:
            raise ValueError(
                f'{_TAGS_YAML}: tag {tag!r}: "subset" must be the string '
                f'"all" or a non-empty list of check ids, got {subset!r}')
        tolerated = entry.get('tolerated_skips') or {}
        if not isinstance(tolerated, dict):
            raise ValueError(
                f'{_TAGS_YAML}: tag {tag!r}: "tolerated_skips" must be a '
                f'mapping of check id to condition name')
        for check_id, cond in tolerated.items():
            if (not isinstance(check_id, str)
                    or cond not in _TOLERATED_SKIP_CONDITIONS):
                raise ValueError(
                    f'{_TAGS_YAML}: tag {tag!r}: tolerated_skips'
                    f'[{check_id!r}] names unknown condition {cond!r}; '
                    f'known: {sorted(_TOLERATED_SKIP_CONDITIONS)}')
        registry[tag] = {'class': cls, 'subset': subset,
                         'tolerated_skips': dict(tolerated)}
    _TAG_REGISTRY_CACHE = registry
    return registry


def _tag_names():
    """The tag names in canonical (registry declaration) order. Mirrors the
    schema's metadata.tags enum; tests/tier1/test_tag_registry.py pins the
    sync."""
    return tuple(_load_tag_registry())


# Result ids never part of any subset: the schema gate and the two aggregates.
_NON_SUBSET_IDS = frozenset({'SCHEMA', 'XREF-01', 'TAG-01'})


def _skip_tolerated(tag, check_id, ctx):
    """True if a `skip` from `check_id` is a legitimate absence-of-construct
    under `tag`'s tolerated-skip policy in the tags.yaml registry
    (sff_checks.md section 8 tolerated-skip table). Condition predicates read
    only the read-only _Context."""
    cond = _load_tag_registry()[tag]['tolerated_skips'].get(check_id)
    return cond is not None and _TOLERATED_SKIP_CONDITIONS[cond](ctx)


def _conformant(results):
    """True if the file is schema-valid and has no error-severity *fail* among the
    semantic checks OTHER than TAG-01 (excluded to avoid circularity). This is the
    tag earning precondition."""
    return not any(r.status == 'fail' and r.severity == 'error'
                   and r.check_id != 'TAG-01' for r in results)


def _reproducible_precondition(ctx, results):
    """Return a list of static-precondition problems for the `reproducible` tag
    (empty ⇒ precondition holds): recipe present + comparison_rtol recorded +
    MET-07 not failing. Never runs the harness."""
    problems = []
    repro = ctx.metadata.get('reproducibility')
    if not isinstance(repro, dict) or not isinstance(repro.get('environment'), dict) \
            or not isinstance(repro.get('load_script'), dict):
        problems.append('no reproducibility recipe (environment + load_script)')
        return problems
    if not isinstance(repro.get('comparison_rtol'), (int, float)):
        problems.append('metadata.reproducibility.comparison_rtol not recorded')
    if any(r.check_id == 'MET-07' and r.status == 'fail' for r in results):
        problems.append('MET-07 digest mismatch')
    return problems


def _earned_tags(ctx, results):
    """Apply the static earning rules from the tags.yaml registry
    (sff_checks.md section 8) to produce a per-tag verdict dict.
    reproducible.earned is None here (static path); its
    blocking holds the precondition problems. Used by evaluate_sff_tags and the
    TAG-01 aggregate.

    `blocking` is uniformly a ``list[str]`` across all registered tags: for the
    static-subset tags it holds the failing/untolerated-skip checks' `check_id`
    strings; for `reproducible` it holds the precondition-problem reason
    strings (unchanged)."""
    declared = set(ctx.metadata.get('tags') or [])
    conformant = _conformant(results)
    verdict = {}
    for tag, entry in _load_tag_registry().items():
        if entry['class'] != 'static':
            continue  # `reproducible` (harness): precondition verdict below
        subset = entry['subset']
        blocking = []
        for r in results:
            if r.check_id in _NON_SUBSET_IDS or r.severity == 'info':
                continue
            if subset is not None and r.check_id not in subset:
                continue
            if r.status == 'fail' and r.severity in ('warning', 'error'):
                blocking.append(r.check_id)
            elif r.status == 'skip' and not _skip_tolerated(tag, r.check_id, ctx):
                blocking.append(r.check_id)
        verdict[tag] = {'earned': conformant and not blocking,
                        'declared': tag in declared, 'blocking': blocking}
    precondition = _reproducible_precondition(ctx, results)
    verdict['reproducible'] = {
        'earned': None, 'declared': 'reproducible' in declared,
        'blocking': ([] if conformant else ['not conformant']) + precondition}
    return verdict


def _tag_gate(ctx, results):  # TAG-01
    """Post-pass aggregate (like XREF-01, NOT in _CHECKS): every tag in
    metadata.tags is earned. Static tags evaluated fully; `reproducible` against
    its cheap precondition only. Never runs the harness."""
    declared = ctx.metadata.get('tags')
    if not declared:
        return _skipped('TAG-01', 'error', 'no metadata.tags declared', 'metadata')
    verdict = _earned_tags(ctx, results)
    violations = []
    for tag in declared:
        info = verdict.get(tag)
        if info is None:
            continue  # unknown tag name -- the schema enum already rejects it
        if tag == 'reproducible':
            if info['blocking']:
                violations.append(f"'reproducible' precondition unmet: "
                                  f"{info['blocking']}")
        elif not info['earned']:
            ids = list(info['blocking'])
            violations.append(f"'{tag}' not earned (blocking: {ids})")
    if violations:
        return _failed('TAG-01', 'error',
                       f'declared tag(s) not earned: {violations}', 'metadata')
    return _passed('TAG-01', 'error', 'metadata')


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
    with Path(schema_file).open('r', encoding='utf-8') as f:
        schema = json.load(f)

    ctx, results = _run_all_checks(doc, schema)
    # TAG-01: declared-tags-earned aggregate, computed from the results above
    # plus the tag policies (like XREF-01). Never runs the harness.
    results.append(_tag_gate(ctx, results))

    is_valid = not any(r.status == 'fail' and r.severity == 'error'
                       for r in results)
    return is_valid, results


def evaluate_sff_tags(json_file, schema_file=None, *, run_harness=False,
                      conda_exe=None, rtol=None, recreate_env=False, export=None):
    """
    Compute the tag verdict for an SFF file.

    Parameters
    ----------
    json_file : str or pathlib.Path
        Path to the SFF JSON file.
    schema_file : str or pathlib.Path, optional
        Path to the SFF JSON Schema. Defaults to the schema shipped with this
        package.
    run_harness : bool, optional
        When False (default), the four static tags are fully evaluated and
        ``reproducible.earned`` is ``None`` ("not evaluated"); fast, no
        simulation. When True, additionally calls :func:`verify_reproducible` to
        set ``reproducible.earned`` to a real bool (heavy; obeys the export lock).
    conda_exe, rtol, recreate_env, export
        Forwarded to :func:`verify_reproducible` when ``run_harness`` is True.

    Returns
    -------
    dict
        ``{tag: {"earned": bool | None, "declared": bool, "blocking": list[str]}}``
        for each of the five tags. ``blocking`` is uniformly a list of strings:
        failing/untolerated-skip check IDs for the four static-subset tags,
        and precondition/diff reason strings for ``reproducible``.
    """
    if schema_file is None:
        schema_file = _SCHEMA_FILE
    with Path(json_file).open('r', encoding='utf-8') as f:
        doc = json.load(f)
    with Path(schema_file).open('r', encoding='utf-8') as f:
        schema = json.load(f)
    ctx, results = _run_all_checks(doc, schema)
    verdict = _earned_tags(ctx, results)
    if run_harness:
        if verdict['reproducible']['blocking']:
            # Static reproducible precondition already fails (not conformant,
            # missing recipe/comparison_rtol, or a MET-07 digest mismatch) --
            # the harness re-export could never earn the tag, so skip the heavy
            # simulation entirely and leave the precondition `blocking` in
            # place as the reason.
            verdict['reproducible']['earned'] = False
        else:
            matches, diffs = verify_reproducible(
                json_file, conda_exe=conda_exe, rtol=rtol,
                recreate_env=recreate_env, export=export)
            verdict['reproducible']['earned'] = bool(matches)
            verdict['reproducible']['blocking'] = [] if matches else list(diffs)
    return verdict


#%% Reproducible tag: harness re-export + deep compare (sff_checks.md section 8)

#: Default relative tolerance when neither an explicit rtol nor the file's
#: metadata.reproducibility.comparison_rtol is available. Mirrors Tier 6's RTOL.
_REPRO_DEFAULT_RTOL = 1e-4

#: Slash-joined document paths excluded from the reproducible deep-compare,
#: because they legitimately vary between two faithful runs or are post-hoc
#: annotations the exporter never re-emits. See sff_checks.md section 8 and the
#: plan's "Spec clarification" note (comparison_rtol is a post-hoc annotation, so
#: it is ignored alongside metadata.tags).
#:
#: The three `.../path` entries cover _runner.py's _file_record: it records a
#: repo-relative `path` only when the original model directory lives under
#: REPO_ROOT (true for every model under pisces_sff/models/), via
#: `path.relative_to(REPO_ROOT)`. _reconstruct_model_dir() below always writes
#: the reconstructed recipe into a tempdir OUTSIDE the repo (by design, for
#: verification isolation), so a re-export's `path` key is always absent
#: regardless of whether the original had one -- an unavoidable, deterministic
#: artifact of how verification itself is performed, not a sign of drift.
#: Without this, no in-repo model could ever earn `reproducible`.
_REPRO_IGNORE_PATHS = frozenset({
    'metadata/tags',
    'metadata/reproducibility/comparison_rtol',
    'metadata/reproducibility/resolved/exported_at',
    'metadata/reproducibility/resolved/platform',
    'metadata/reproducibility/resolved/python_version',
    'metadata/reproducibility/environment/path',
    'metadata/reproducibility/load_script/path',
    'metadata/reproducibility/extended_metadata/path',
})


def _reconstruct_model_dir(doc, dest):
    """Write the embedded reproducibility recipe into `dest` as a model directory:
    environment.yaml, load.py, and extended_metadata.yaml (when present), each
    from its `content` string written verbatim as UTF-8 bytes (LF preserved, no
    newline translation -- a Linux/CI-identical export depends on it). Returns
    `dest`."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    repro = (doc.get('metadata') or {}).get('reproducibility') or {}
    for block_name, default_filename in (('environment', 'environment.yaml'),
                                         ('load_script', 'load.py'),
                                         ('extended_metadata',
                                          'extended_metadata.yaml')):
        block = repro.get(block_name)
        if not isinstance(block, dict) or 'content' not in block:
            continue
        filename = block.get('filename') or default_filename
        # Binary write of the UTF-8 bytes: no universal-newline translation, so
        # LF stays LF on every platform.
        (dest / filename).write_bytes(block['content'].encode('utf-8'))
    return dest


def _deep_compare_reexport(original, reexport, rtol, path=''):
    """Recursively compare a re-export against the original document, returning a
    list of human-readable diffs (empty ⇒ match). Structure is exact (same keys,
    same array lengths, identical non-numeric leaves); numeric leaves compare
    within `rtol` with an absolute floor near zero (via the module's existing
    _rel_close); paths in _REPRO_IGNORE_PATHS are skipped."""
    if path in _REPRO_IGNORE_PATHS:
        return []
    diffs = []
    if isinstance(original, dict) and isinstance(reexport, dict):
        keys = set(original) | set(reexport)
        for k in sorted(keys):
            child = f'{path}/{k}' if path else k
            if child in _REPRO_IGNORE_PATHS:
                continue
            if k not in original or k not in reexport:
                diffs.append(f'{child}: key present in only one document')
                continue
            diffs.extend(_deep_compare_reexport(original[k], reexport[k], rtol,
                                                child))
    elif isinstance(original, list) and isinstance(reexport, list):
        if len(original) != len(reexport):
            diffs.append(f'{path}: array length {len(original)} != '
                         f'{len(reexport)}')
        else:
            for i, (a, b) in enumerate(zip(original, reexport)):
                diffs.extend(_deep_compare_reexport(a, b, rtol, f'{path}/{i}'))
    elif isinstance(original, bool) or isinstance(reexport, bool):
        # bool is an int subclass, so a bare `!=` would coerce True == 1 and
        # False == 0/0.0 as equal. Flag a diff unless BOTH sides are bool and
        # equal -- a bool compared against a non-bool number (or the other way
        # round) is always a type-shape diff, never a numeric near-match.
        both_bool = isinstance(original, bool) and isinstance(reexport, bool)
        if not (both_bool and original == reexport):
            diffs.append(f'{path}: {original!r} != {reexport!r}')
    elif isinstance(original, (int, float)) and isinstance(reexport, (int, float)):
        # Reuses the module's existing _rel_close (identical logic to what would
        # otherwise be a duplicate _rel_close_abs helper -- see Task 4 report).
        if not _rel_close(float(original), float(reexport), rtol):
            diffs.append(f'{path}: {original!r} != {reexport!r} (rtol {rtol})')
    else:
        if original != reexport:
            diffs.append(f'{path}: {original!r} != {reexport!r}')
    return diffs


def verify_reproducible(json_file, *, conda_exe=None, rtol=None,
                        recreate_env=False, export=None):
    """
    Verify the `reproducible` tag: re-export from the embedded recipe and compare.

    Reconstructs a temporary model directory from ``metadata.reproducibility``'s
    embedded bytes, re-runs the export inside the pinned conda environment, and
    deep-compares the result to this file (ignoring ``metadata.tags``,
    ``metadata.reproducibility.comparison_rtol``, and the volatile ``resolved.*``
    fields). Heavy: provisions/reuses a conda environment and simulates, under the
    harness export lock (never concurrent with another simulation).

    Parameters
    ----------
    json_file : str or pathlib.Path
        The SFF file to verify.
    conda_exe : str, optional
        Explicit conda executable; forwarded to the harness.
    rtol : float, optional
        Relative tolerance override. When None, resolves from the file's
        ``metadata.reproducibility.comparison_rtol``; falling back to 1e-4 when the
        file records none. The tag's *meaning* is always the recorded value.
    recreate_env : bool, optional
        Rebuild the conda environment even if one matches.
    export : callable, optional
        ``export(model_dir, output_path, sff_version=..., recreate_env=...,
        conda_exe=...)`` used to re-export. Defaults to
        :func:`pisces_sff._harness.export_model` (the full harness), imported
        lazily so the static path never pulls in biosteam. Tests inject a fake.

    Returns
    -------
    (matches, diffs)
        matches : bool
            True iff the re-export matches within the resolved tolerance.
        diffs : list of str
            Human-readable differences (empty when matches is True), or a single
            reason when the recipe is missing/incomplete.
    """
    import tempfile

    with Path(json_file).open('r', encoding='utf-8') as f:
        original = json.load(f)
    metadata = original.get('metadata') or {}
    repro = metadata.get('reproducibility')
    if not isinstance(repro, dict) or not isinstance(repro.get('environment'), dict) \
            or not isinstance(repro.get('load_script'), dict):
        return False, ['no reproducibility recipe (environment + load_script)']
    if rtol is None:
        recorded = repro.get('comparison_rtol')
        rtol = float(recorded) if isinstance(recorded, (int, float)) \
            else _REPRO_DEFAULT_RTOL
    sff_version = metadata.get('sff_version')

    if export is None:
        from ._harness import export_model as export

    with tempfile.TemporaryDirectory() as tmp:
        model_dir = _reconstruct_model_dir(original, Path(tmp) / 'model')
        output_path = Path(tmp) / 'reexport.json'
        export(model_dir, output_path, sff_version=sff_version,
               recreate_env=recreate_env, conda_exe=conda_exe)
        with output_path.open('r', encoding='utf-8') as f:
            reexport = json.load(f)
    diffs = _deep_compare_reexport(original, reexport, rtol)
    return (not diffs), diffs


# Ordered registry of check(ctx) -> list[CheckResult], in sff_checks.md order.
# The XREF-01 aggregate is NOT here -- it is computed from these results inside
# validate_flowsheet_against_SFF (see _xref_gate).
_CHECKS = [
    # metadata
    _check_metadata_stream_refs,             # MET-02
    _check_metadata_role_agreement,          # MET-03
    _check_tea_year_plausible,               # MET-04
    _check_reproducibility_content_digests,  # MET-07
    # units
    _check_unit_id_uniqueness,               # UNIT-01
    _check_utility_result_refs,              # UNIT-02
    _check_design_result_units_pairing,      # UNIT-03
    _check_reaction_reactant_refs,           # UNIT-04
    _check_reaction_equation_stoichiometry_consistency,  # UNIT-05
    _check_stoichiometry_wellformed,         # UNIT-06
    _check_unit_connectivity,                # UNIT-07
    _check_cost_correlation_refs,            # UNIT-08
    _check_cost_correlation_completeness,    # UNIT-09
    _check_units_present_identified,         # UNIT-10
    # streams: referential / roles / zero-flow
    _check_stream_id_uniqueness,             # STR-01
    _check_stream_endpoint_refs,             # STR-02
    _check_isolated_stream_empty,            # STR-03
    _check_stream_topology_role,             # STR-04
    _check_stream_role_topology_agreement,   # STR-05
    _check_stream_designation_roles,         # STR-06
    _check_composition_component_refs,       # STR-07
    _check_zero_flow_consistency,            # STR-13
    _check_streams_present_identified,       # STR-14
    # streams: material balance
    _check_fraction_sums,                    # STR-08
    _check_phase_flow_sums,                  # STR-09
    _check_mass_molar_flow_consistency,      # STR-10
    # chemicals
    _check_chemical_id_index_uniqueness,     # CHEM-01
    _check_molar_mass_positive,              # CHEM-02
    _check_formula_molar_mass_agreement,     # CHEM-03
    _check_index_coverage,                   # CHEM-04
    _check_unused_chemicals,                 # CHEM-05
    # utilities
    _check_utility_id_uniqueness,            # UTIL-01
    _check_unused_utilities,                 # UTIL-02
    _check_utility_result_units_parseable,   # UTIL-03
    _check_utility_composition,              # UTIL-04
    # quantity units
    _check_quantity_unit_pairing,            # QU-01
    _check_quantity_unit_strings_parseable,  # QU-02
    _check_alias_uniqueness,                 # QU-03
    _check_unused_aliases,                   # QU-04
    # cross-object
    _check_boundary_streams_exist,           # GRAPH-01
]
