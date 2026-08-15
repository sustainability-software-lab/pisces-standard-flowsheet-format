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


def _same_reaction_up_to_scale(a, b):
    """True if coeff maps a and b describe one reaction up to a common positive
    scale factor (same component set, equal positive ratios)."""
    if set(a) != set(b):
        return False
    ratio = None
    for k in a:
        if b[k] == 0:
            return False
        r = a[k] / b[k]
        if r <= 0:
            return False
        if ratio is None:
            ratio = r
        elif abs(r - ratio) > 1e-6 * abs(ratio):
            return False
    return True


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


#%% Checks -- streams: referential, roles, zero-flow (sff_checks.md section 3)

BOUNDARY = 'None'  # C-01 system-boundary sentinel written to source/sink_unit_id
TOPOLOGY_ROLES = ('input', 'output', 'internal')
DESIGNATION_ROLES = ('purchased_raw_material', 'feedstock', 'product')


def _stream_flow_scalars(stream):
    """Yield every present numeric flow scalar of a stream: stream-level totals
    and each phase's totals. Non-flow scalars (T, P) are excluded."""
    sp = (stream.get('stream_properties') or {}) if isinstance(stream, dict) else {}
    for name in ('total_mass_flow', 'total_molar_flow', 'total_volumetric_flow'):
        v = sp.get(name)
        if isinstance(v, (int, float)):
            yield v
    for phase in (sp.get('phases') or {}).values():
        for name in ('total_mass_flow', 'total_molar_flow', 'total_volumetric_flow'):
            v = phase.get(name) if isinstance(phase, dict) else None
            if isinstance(v, (int, float)):
                yield v


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


def _check_zero_flow_consistency(ctx):  # STR-13
    bad, any_zero = [], False
    for s in ctx.streams:
        if not isinstance(s, dict):
            continue
        scalars = list(_stream_flow_scalars(s))
        if not any(abs(v) <= ZERO_FLOW for v in scalars):
            continue  # no zero flow present -> not applicable to this stream
        any_zero = True
        if not _stream_is_empty(s):
            bad.append(s.get('id'))
    if not any_zero:
        return [_skipped('STR-13', 'error',
                         'no stream has a zero flow scalar', 'streams')]
    if bad:
        return [_failed('STR-13', 'error',
                        f'stream(s) with a zero flow but nonzero other flow/'
                        f'composition: {bad}', 'streams')]
    return [_passed('STR-13', 'error', 'streams')]


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
