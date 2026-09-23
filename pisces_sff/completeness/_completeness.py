# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
The completeness scorer.

Scores how much of the information needed to *theoretically re-import an SFF
flowsheet into BioSTEAM* -- rebuild an equivalent ``biosteam.System`` that can
be simulated, costed and put through TEA -- is actually present in the file,
judged declaratively from the SFF data alone. The criteria (ids, categories,
weights, criticality, the SFF version from which each is representable, and a
rationale) live in the registry ``criteria.yaml`` next to this module; each
criterion id maps to one pure scoring function in :data:`_CRITERIA`.

The score is meaningful for sparse extracted files (no stream states, no
chemicals) as well as for full simulator exports and legacy (<=0.0.6,
inline ``{value, units}``) corpus files. The embedded reproducibility recipe
(the "re-execution route") is reported separately and never folded into the
score, which measures declarative content only.

Import-light by design: top-level imports are stdlib only and nothing is
imported package-relatively, so Tier 1 can load this file by path. The
validator (``../validate/_validate.py``) and the design-spec registry loader
(``../export/_design_specs.py``) are obtained lazily -- the already-imported
package modules when present, else loaded by file path -- and ``yaml`` is
imported inside functions. Run as a script for a plain-text report::

    python pisces_sff/completeness/_completeness.py FILE [--json]
"""

import argparse
import importlib.util
import json
import math
import os
import re
import sys
from collections import namedtuple
from pathlib import Path

__all__ = ('score_flowsheet_completeness', 'load_completeness_criteria',
           'format_completeness_report', 'CompletenessReport',
           'CriterionResult')

#%% Report types, constants, paths

# One scored criterion. status is 'scored' or 'not_applicable'; score is in
# [0, 1], or None when not applicable. representable: the file's sff_version
# can state this criterion explicitly. missing: actionable locators of what is
# absent, e.g. "streams[feed].stream_properties.temperature".
CriterionResult = namedtuple(
    'CriterionResult',
    'criterion_id category criticality weight representable status score '
    'message missing')

# The whole report. score: weighted mean over every scored criterion;
# representable_score: the same restricted to criteria representable at the
# file's sff_version; validation_summary: the validator results counted by
# severity and status, {severity: {status: count}}, reported beside is_valid
# (never folded into the score); recipe: the separate re-execution route
# (embedded load script + pinned environment), deliberately NOT folded into
# the score either.
CompletenessReport = namedtuple(
    'CompletenessReport',
    'score representable_score category_scores criticality_scores criteria '
    'sff_version is_valid validation_summary validation_results recipe')

# Credit for information the file does not state but that is derivable from
# other fields (e.g. an HXutility outlet temperature read off its outlet stream).
DERIVED_CREDIT = 0.75

# CMP-THM-02 identity tiers below a valid registry id (CAS/InChI/InChIKey/
# SMILES, credit 1): the chemical id resolves by name in the `chemicals`
# database (the lookup thermosteam's Chemical(ID) performs) AND the record's
# molar mass (or formula) matches the chemical's own (verified), or resolves
# with nothing stated to verify it against (unverified); or only a formula
# narrows it down (R2.3).
NAME_RESOLVED_CREDIT = 0.75
NAME_UNVERIFIED_CREDIT = 0.5
FORMULA_ONLY_CREDIT = 0.25

# R2.3: a name-resolved record verifies the chemical when their molar masses
# agree to NAME_MW_RTOL; R2.0: a CAS number whose database record's molar
# mass differs from the chemical's by more than CAS_MW_RTOL is invalid.
NAME_MW_RTOL = 0.01
CAS_MW_RTOL = 0.02

# R2.0 stream-state plausibility: temperatures in (0, T_MAX) K; the
# fractions of one composition sum to 1 within FRACTION_SUM_TOL.
T_MAX = 1.0e4
FRACTION_SUM_TOL = 1e-3

# R2.2: a derivation from stream data needs the unit's total mass balance
# (and, for a physical unit, every species' molar balance) to close within
# CLOSURE_RTOL of the throughput.
CLOSURE_RTOL = 0.01

# CMP-RXN-04: relative tolerance of the mass balance sum(nu*MW) (molar basis)
# and of sum(nu) (mass basis) when deriving a reaction's basis.
BASIS_RTOL = 1e-3

# Same absolute zero-flow threshold as the validator's ZERO_FLOW (STR-13);
# duplicated to stay import-light, equality pinned by a Tier 1 test.
ZERO_FLOW = 1e-12

# Relative threshold (of a unit's total molar throughput) above which a species
# that has no inlet (outlet) flow counts as appearing (disappearing): CMP-RXN-01.
SPECIES_RTOL = 1e-6

BOUNDARY = 'None'  # C-01 system-boundary sentinel for source/sink_unit_id

CRITICALITIES = ('essential', 'costing', 'supplementary', 'integrity')

_HERE = Path(__file__).resolve().parent
CRITERIA_PATH = _HERE / 'criteria.yaml'
_VALIDATE_PATH = _HERE.parent / 'validate' / '_validate.py'
_DESIGN_SPECS_PATH = _HERE.parent / 'export' / '_design_specs.py'

_SEMVER_RE = re.compile(r'^\d+\.\d+\.\d+$')

#%% Lazy sibling modules (validator, design-spec registry)

_SIBLINGS = {}


def _sibling_module(qualname, path):
    """Return package module `qualname` if already imported, else load `path`
    by file path (cached). Keeps this module free of package-relative imports
    while reusing the real package modules whenever they are loaded."""
    module = sys.modules.get(qualname)
    if module is not None:
        return module
    if qualname not in _SIBLINGS:
        spec = importlib.util.spec_from_file_location(
            qualname.replace('.', '_') + '_by_path', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SIBLINGS[qualname] = module
    return _SIBLINGS[qualname]


def _validator():
    """The validator module (``pisces_sff/validate/_validate.py``)."""
    return _sibling_module('pisces_sff.validate._validate', _VALIDATE_PATH)


_REGISTRY_CACHE = None


def _design_spec_registry():
    """(registry, error) for the committed design-spec registry, cached.
    On failure the registry is {} and error names the cause; CMP-UOP-03 then
    reports the error instead of scoring."""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        try:
            module = _sibling_module('pisces_sff.export._design_specs',
                                     _DESIGN_SPECS_PATH)
            _REGISTRY_CACHE = (module.load_design_spec_registry(), None)
        except Exception as exc:
            _REGISTRY_CACHE = ({}, f'{type(exc).__name__}: {exc}')
    return _REGISTRY_CACHE


# [search function or None], filled on first use; {name: database record or
# None} of lookups.
_CHEMICALS_SEARCH = []
_NAME_LOOKUP = {}


def _chemicals_search():
    """``chemicals.identifiers.search_chemical`` -- the lookup thermosteam's
    ``Chemical(ID)`` performs (names, synonyms, formulas, CAS, SMILES) --
    imported lazily once; None when the package cannot be imported (the
    CMP-THM-02 name tier is then unavailable)."""
    if not _CHEMICALS_SEARCH:
        try:
            from chemicals.identifiers import search_chemical
        except Exception:
            search_chemical = None
        _CHEMICALS_SEARCH.append(search_chemical)
    return _CHEMICALS_SEARCH[0]


def _name_record(name):
    """The `chemicals` database record `name` (a chemical name, synonym or
    CAS number) resolves to, or None. Cached per name, so a failed lookup
    costs one search (the first also loads the database, ~1 s); never raises
    -- an unavailable package or any lookup error means None."""
    if not _nonempty_str(name):
        return None
    name = name.strip()
    if name not in _NAME_LOOKUP:
        search = _chemicals_search()
        try:
            _NAME_LOOKUP[name] = search(name) if search is not None else None
        except Exception:
            _NAME_LOOKUP[name] = None
    return _NAME_LOOKUP[name]


def _name_resolvable(name):
    """True when chemical id `name` resolves in the `chemicals` database
    (CMP-THM-02 name tier; see _name_record)."""
    return _name_record(name) is not None

#%% Criteria registry (criteria.yaml)

_CRITERIA_CACHE = None
_TOP_KEYS = frozenset({'categories', 'criteria', 'exclusions'})
_CRITERION_KEYS = frozenset({'id', 'category', 'title', 'weight', 'criticality',
                             'representable_since', 'rationale', 'sff_paths'})
_EXCLUSION_KEYS = frozenset({'id', 'kind', 'title', 'rationale'})
_EXCLUSION_KINDS = ('not_capturable', 'derived', 'out_of_scope',
                    'undetectable_need')
_CRITERION_ID_RE = re.compile(r'^CMP-[A-Z]+-\d{2}$')
_EXCLUSION_ID_RE = re.compile(r'^(EXC|DER)-\d{2}$')


def _yaml_load_no_duplicates(text, source):
    """yaml.safe_load, except a mapping key repeated at any depth raises
    ValueError naming `source` and the key. PyYAML's safe_load silently keeps
    the last occurrence of a duplicated key. Deliberately duplicated across the
    registry loaders (_validate.py, _registry.py, _design_specs.py, here): each
    must stay loadable by file path, so they cannot share one import."""
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
                    raise ValueError(f'{source}: duplicate YAML key {key!r}')
            return super().construct_mapping(node, deep=deep)

    return yaml.load(text, Loader=_NoDuplicateKeyLoader)


def _nonempty_str(x):
    return isinstance(x, str) and bool(x.strip())


def _clip(text, limit=240):
    """`text` cut to `limit` characters (validator messages can list hundreds
    of offenders; a locator should stay readable)."""
    text = str(text)
    return text if len(text) <= limit else text[:limit - 3] + '...'


def _check_criterion_entry(entry, categories, source):
    """Raise ValueError unless `entry` is a well-formed criterion."""
    if not isinstance(entry, dict):
        raise ValueError(f'{source}: every criterion must be a mapping')
    cid = entry.get('id')
    if not isinstance(cid, str) or not _CRITERION_ID_RE.match(cid):
        raise ValueError(f'{source}: bad criterion id {cid!r} '
                         f'(expected CMP-<AREA>-<NN>)')
    keys = set(entry)
    if keys != _CRITERION_KEYS:
        raise ValueError(f'{source}: {cid}: missing key(s) '
                         f'{sorted(_CRITERION_KEYS - keys)}, unknown key(s) '
                         f'{sorted(keys - _CRITERION_KEYS)}')
    if entry['category'] not in categories:
        raise ValueError(f"{source}: {cid}: unknown category "
                         f"{entry['category']!r}")
    weight = entry['weight']
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) \
            or not weight > 0:
        raise ValueError(f'{source}: {cid}: weight must be a number > 0')
    if entry['criticality'] not in CRITICALITIES:
        raise ValueError(f"{source}: {cid}: criticality must be one of "
                         f"{list(CRITICALITIES)}")
    since = entry['representable_since']
    if since is not None and not (isinstance(since, str)
                                  and _SEMVER_RE.match(since)):
        raise ValueError(f'{source}: {cid}: representable_since must be a '
                         f'"X.Y.Z" string or null')
    for key in ('title', 'rationale'):
        if not _nonempty_str(entry[key]):
            raise ValueError(f'{source}: {cid}: {key} must be a non-empty '
                             f'string')
    paths = entry['sff_paths']
    if not isinstance(paths, list) or not all(_nonempty_str(p) for p in paths):
        raise ValueError(f'{source}: {cid}: sff_paths must be a list of '
                         f'non-empty strings')


def _check_exclusion_entry(entry, source):
    """Raise ValueError unless `entry` is a well-formed exclusion note."""
    if not isinstance(entry, dict) or set(entry) != _EXCLUSION_KEYS:
        raise ValueError(f'{source}: every exclusion must be a mapping with '
                         f'exactly {sorted(_EXCLUSION_KEYS)}')
    if not isinstance(entry['id'], str) \
            or not _EXCLUSION_ID_RE.match(entry['id']):
        raise ValueError(f"{source}: bad exclusion id {entry['id']!r} "
                         f"(expected EXC-<NN> or DER-<NN>)")
    if entry['kind'] not in _EXCLUSION_KINDS:
        raise ValueError(f"{source}: {entry['id']}: kind must be one of "
                         f"{list(_EXCLUSION_KINDS)}")
    for key in ('title', 'rationale'):
        if not _nonempty_str(entry[key]):
            raise ValueError(f"{source}: {entry['id']}: {key} must be a "
                             f"non-empty string")


def _parse_criteria(path):
    """Parse and shape-validate one criteria YAML file (uncached)."""
    import yaml  # lazy: keep the module import-light
    try:
        text = Path(path).read_text(encoding='utf-8')
    except OSError as e:
        raise ValueError(f'criteria registry not readable: {path}: {e}') from e
    try:
        data = _yaml_load_no_duplicates(text, path)
    except yaml.YAMLError as e:
        raise ValueError(f'criteria registry is not valid YAML: {path}: '
                         f'{e}') from e
    if not isinstance(data, dict) or set(data) != _TOP_KEYS:
        raise ValueError(f'{path}: top level must be a mapping with exactly '
                         f'{sorted(_TOP_KEYS)}')
    categories = data['categories']
    if not isinstance(categories, dict) or not categories or not all(
            _nonempty_str(k) and _nonempty_str(v)
            for k, v in categories.items()):
        raise ValueError(f'{path}: categories must map ids to titles')
    if not isinstance(data['criteria'], list) or not data['criteria']:
        raise ValueError(f'{path}: criteria must be a non-empty list')
    criteria = {}
    for entry in data['criteria']:
        _check_criterion_entry(entry, categories, path)
        if entry['id'] in criteria:
            raise ValueError(f"{path}: duplicate criterion id {entry['id']}")
        criteria[entry['id']] = entry
    if not isinstance(data['exclusions'], list):
        raise ValueError(f'{path}: exclusions must be a list')
    seen = set()
    for entry in data['exclusions']:
        _check_exclusion_entry(entry, path)
        if entry['id'] in seen:
            raise ValueError(f"{path}: duplicate exclusion id {entry['id']}")
        seen.add(entry['id'])
    return {'categories': dict(categories), 'criteria': criteria,
            'exclusions': list(data['exclusions'])}


def load_completeness_criteria(path=None):
    """
    Parse and validate the completeness criteria registry.

    Parameters
    ----------
    path : str or pathlib.Path, optional
        Registry file to load. Defaults to the committed
        ``pisces_sff/completeness/criteria.yaml``; only the default is cached.

    Returns
    -------
    dict
        ``{'categories': {id: title}, 'criteria': {criterion_id: entry},
        'exclusions': [entry, ...]}``. ``criteria`` preserves file order; each
        entry carries ``id, category, title, weight, criticality,
        representable_since, rationale, sff_paths``. Treat it as read-only
        (the default registry is a shared cached object).

    Raises
    ------
    ValueError
        On an unreadable file, malformed YAML, a duplicated mapping key, a
        duplicated criterion/exclusion id, an unknown category, a
        non-positive weight, an unknown criticality, a ``representable_since``
        that is neither an ``"X.Y.Z"`` string nor null, or any missing/unknown
        entry key.
    """
    global _CRITERIA_CACHE
    if path is not None:
        return _parse_criteria(path)
    if _CRITERIA_CACHE is None:
        _CRITERIA_CACHE = _parse_criteria(CRITERIA_PATH)
    return _CRITERIA_CACHE

#%% Shape-tolerant readers (current and legacy <=0.0.6 shapes)

def _num(x):
    """Finite number (not bool) -> float; legacy inline ``{value, units}`` dict
    with a finite numeric value -> float; anything else -> None."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x) if math.isfinite(x) else None
    if isinstance(x, dict) and not isinstance(x.get('value'), dict):
        return _num(x.get('value'))
    return None


def _as_dict(x):
    return x if isinstance(x, dict) else {}


def _as_list(x):
    return x if isinstance(x, list) else []


def _filled(x):
    """True for a stated value: not None, not a blank string, not an empty
    list/dict."""
    if x is None:
        return False
    if isinstance(x, str):
        return bool(x.strip())
    if isinstance(x, (list, dict)):
        return bool(x)
    return True


def _is_boundary(endpoint):
    """The boundary sentinel "None"; JSON null, a missing key and "" are
    tolerated as the same."""
    return endpoint is None or endpoint in ('', BOUNDARY)


def _label(kind, obj, position):
    """Locator of one list element: ``kind[<id>]``, or ``kind[#<pos>]`` when
    the element has no usable string id."""
    oid = obj.get('id') if isinstance(obj, dict) else None
    return f'{kind}[{oid}]' if _nonempty_str(oid) else f'{kind}[#{position}]'


def _entry_fraction(entry):
    """Valid (numeric, in [0, 1]) mol_fraction, else valid mass_fraction, of
    a composition entry; None when neither is valid (R2.0)."""
    if not isinstance(entry, dict):
        return None
    x = _valid_fraction(entry.get('mol_fraction'))
    return x if x is not None else _valid_fraction(entry.get('mass_fraction'))


def _composition_ok(composition):
    """A plausible composition (R2.0): a non-empty list of entries, each
    naming its component (non-empty string), whose mol fractions -- or else
    mass fractions -- are all in [0, 1] and sum to 1 within
    FRACTION_SUM_TOL per phase (entries of the legacy flat shape are
    grouped by their phase tag: each phase's fractions sum to 1). Whether
    the names resolve is CMP-THM-01's and CMP-INT-02's job, not this
    one's."""
    if not isinstance(composition, list) or not composition or not all(
            isinstance(e, dict) and _nonempty_str(e.get('component_name'))
            for e in composition):
        return False
    groups = {}
    for entry in composition:
        tag = entry.get('phase')
        groups.setdefault(tag if isinstance(tag, str) else None, []).append(entry)
    for group in groups.values():
        sums = [[_valid_fraction(e.get(key)) for e in group]
                for key in ('mol_fraction', 'mass_fraction')]
        if not any(all(v is not None for v in values)
                   and abs(sum(values) - 1.0) <= FRACTION_SUM_TOL
                   for values in sums):
            return False
    return True


def _phases_ok(phases):
    """Every phase states a valid (>= 0) total_molar_flow and at least one
    carries flow; each phase with flow has a plausible composition
    (_composition_ok), while a zero-flow phase is exempt and may be empty
    (the exporter serializes a phase with F_mol <= ZERO_FLOW as empty -- see
    STR-13)."""
    if not isinstance(phases, dict) or not phases:
        return False
    populated = False
    for phase in phases.values():
        flow = _valid_flow(phase.get('total_molar_flow')) \
            if isinstance(phase, dict) else None
        if flow is None:
            return False
        if flow > ZERO_FLOW:
            if not _composition_ok(phase.get('composition')):
                return False
            populated = True
    return populated


def _total_flow(sp):
    """First valid (finite, >= 0) total flow -- mass, else molar -- of a
    stream_properties; an invalid one counts as absent (R2.0)."""
    for key in ('total_mass_flow', 'total_molar_flow'):
        value = _valid_flow(_as_dict(sp).get(key))
        if value is not None:
            return value
    return None


def _stream_state(stream, empty_exempt=True):
    """Booleans {T, P, flow, composition}: which parts of a stream's state are
    stated with a plausible value (R2.0: temperature in (0, T_MAX) K,
    pressure > 0, total flow >= 0, composition per _phases_ok -- per-phase
    shape, 0.0.9+ -- or _composition_ok on the legacy flat
    ``stream_properties.composition``). With `empty_exempt`, a stream stating
    a zero total flow satisfies composition. Missing stream_properties -> all
    False."""
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    total = _total_flow(sp)
    composition = ((empty_exempt and total is not None and total <= ZERO_FLOW)
                   or _phases_ok(sp.get('phases'))
                   or _composition_ok(sp.get('composition')))
    return {'T': _valid_T(sp.get('temperature')) is not None,
            'P': _valid_P(sp.get('pressure')) is not None,
            'flow': total is not None,
            'composition': bool(composition)}


# Locator suffix of each stream-state part, for `missing` entries.
_STATE_PATHS = {
    'T': 'stream_properties.temperature',
    'P': 'stream_properties.pressure',
    'flow': 'stream_properties.total_mass_flow|total_molar_flow',
    'composition': 'stream_properties.phases[*].composition',
}


def _phase_entries(stream):
    """Yield (phase_symbol, composition_entry) for every composition entry of
    a stream, per-phase shape and legacy flat shape alike."""
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    for symbol, phase in _as_dict(sp.get('phases')).items():
        for entry in _as_list(_as_dict(phase).get('composition')):
            if isinstance(entry, dict):
                yield symbol, entry
    for entry in _as_list(sp.get('composition')):
        if isinstance(entry, dict):
            yield entry.get('phase'), entry


def _species_flows(stream):
    """{component: molar flow} of a stream, or None when not computable from
    valid data (no valid molar flow basis, an implausible composition, an
    entry without a valid mol fraction, or a legacy flat composition over
    several phases, whose phase split is unknown). A zero-flow stream ->
    {}; so is a per-phase stream whose every phase states a zero flow."""
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    total = _total_flow(sp)
    if total is not None and total <= ZERO_FLOW:
        return {}
    phases = sp.get('phases')
    if isinstance(phases, dict) and phases:
        blocks = []
        for phase in phases.values():
            phase = _as_dict(phase)
            flow = _valid_flow(phase.get('total_molar_flow'))
            if flow is None:
                return None
            if flow > ZERO_FLOW:
                if not _composition_ok(phase.get('composition')):
                    return None
                blocks.append((flow, phase['composition']))
    elif _composition_ok(sp.get('composition')) \
            and _valid_flow(sp.get('total_molar_flow')) is not None \
            and len({e.get('phase') if isinstance(e.get('phase'), str) else None
                     for e in sp['composition']}) == 1:
        blocks = [(_valid_flow(sp.get('total_molar_flow')), sp['composition'])]
    else:
        return None
    flows = {}
    for molar_flow, composition in blocks:
        for entry in composition:
            x = _valid_fraction(entry.get('mol_fraction'))
            if x is None:
                return None
            name = entry['component_name']
            flows[name] = flows.get(name, 0.0) + molar_flow * x
    return flows


# A thermosteam phase tag on a reaction species: 'Glucose,l', 'CO2, g'.
_PHASE_TAG_RE = re.compile(r'\s*,\s*[A-Za-z]\s*$')


def _equation_sides(equation, declared=frozenset()):
    """([consumed], [produced]) species of a reaction equation
    'A + 2 B -> 3 C'; ([], []) when it is not a string with '->'. A leading
    coefficient must be separated by whitespace ('3 HP'); a glued one ('2H2O')
    is stripped only when that yields a declared chemical id, so names that
    start with a digit survive. A thermosteam phase tag ('CO2,g') is not part
    of the name, and a term without a letter (a bare number) names no
    species."""
    if not isinstance(equation, str) or '->' not in equation:
        return [], []
    sides = []
    for side in equation.split('->', 1):
        names = []
        for term in side.split('+'):
            term = _PHASE_TAG_RE.sub('', term.strip())
            if not term:
                continue
            name = re.sub(r'^\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s+', '', term)
            if name not in declared:
                glued = re.sub(r'^\d+(?:\.\d+)?', '', name).strip()
                name = glued if glued in declared else name
            if re.search(r'[A-Za-z]', name):
                names.append(name)
        sides.append(names)
    return sides[0], sides[1]


def _equation_species(equation, declared=frozenset()):
    """Species named in a reaction equation, consumed then produced (see
    _equation_sides)."""
    consumed, produced = _equation_sides(equation, declared)
    return consumed + produced


def _int_or_none(x):
    if isinstance(x, bool):
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _stoichiometry_sides(stoichiometry, declared, chem_by_index):
    """([consumed], [produced]) species of a stoichiometry -- an object keyed
    by chemical id or index, or an index-ordered array -- by the sign of each
    nonzero numeric coefficient. Unresolvable indices come back as
    'index <n>'; a species that resolves to no non-empty name is dropped."""
    if isinstance(stoichiometry, dict):
        items = stoichiometry.items()
    elif isinstance(stoichiometry, list):
        items = enumerate(stoichiometry)
    else:
        return [], []
    consumed, produced = [], []
    for key, value in items:
        nu = _num(value)
        if not nu:
            continue
        if isinstance(key, str) and key in declared:
            name = key
        else:
            index = _int_or_none(key)
            name = (chem_by_index.get(index, f'index {key}')
                    if index is not None else str(key))
        if _nonempty_str(name):
            (consumed if nu < 0 else produced).append(name)
    return consumed, produced


def _stoichiometry_species(stoichiometry, declared, chem_by_index):
    """Species with a nonzero coefficient in a stoichiometry, consumed then
    produced (see _stoichiometry_sides)."""
    consumed, produced = _stoichiometry_sides(stoichiometry, declared,
                                              chem_by_index)
    return consumed + produced


def _inline_quantities(obj, path=''):
    """Yield (path, dict) for every legacy inline quantity ``{value, units}``
    (numeric value, no other keys) anywhere under `obj`."""
    if isinstance(obj, dict):
        if set(obj) <= {'value', 'units'} and 'value' in obj \
                and _num(obj['value']) is not None:
            yield path or '<root>', obj
            return
        for key, value in obj.items():
            yield from _inline_quantities(value, f'{path}.{key}' if path else str(key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from _inline_quantities(value, f'{path}[{i}]')


def _version_tuple(version):
    """'X.Y.Z' -> (X, Y, Z); anything else -> None."""
    if isinstance(version, str) and _SEMVER_RE.match(version.strip()):
        return tuple(int(p) for p in version.strip().split('.'))
    return None


def _is_representable(since, version):
    """Whether a criterion representable since `since` ('X.Y.Z' or None =
    never) is statable at file version tuple `version` (None = unknown, which
    counts as the newest)."""
    if since is None:
        return False
    return version is None or version >= _version_tuple(since)

#%% Validity predicates (R2.0: INVALID == MISSING)

# A value earns credit only if it is plausible for its role; an implausible
# value is scored exactly like an absent one, so replacing junk by nothing
# changes nothing. These predicates are the single shared set every
# criterion uses.

def _valid_number(x, lo=None, hi=None, lo_open=False, hi_open=False):
    """_num(x) when it lies within [lo, hi] (either bound open, or absent),
    else None."""
    value = _num(x)
    if value is None:
        return None
    if lo is not None and (value < lo or (lo_open and value == lo)):
        return None
    if hi is not None and (value > hi or (hi_open and value == hi)):
        return None
    return value


def _valid_T(x):
    """A plausible temperature: finite, in (0, T_MAX) K; else None."""
    return _valid_number(x, 0.0, T_MAX, lo_open=True, hi_open=True)


def _valid_P(x):
    """A plausible pressure: finite, > 0; else None."""
    return _valid_number(x, 0.0, lo_open=True)


def _valid_flow(x):
    """A plausible flow: finite, >= 0; else None."""
    return _valid_number(x, 0.0)


def _valid_fraction(x):
    """A plausible fraction: finite, in [0, 1]; else None."""
    return _valid_number(x, 0.0, 1.0)


def _nonempty_value(x):
    """A stated value of unknown role: not None, '', [], {} or NaN."""
    if isinstance(x, float) and not math.isfinite(x):
        return False
    return _filled(x)


def _nonempty_scalar(x):
    """A non-blank string or a finite number (a version, a commit)."""
    return _nonempty_str(x) or _num(x) is not None


def _spec_positive(x):
    return _valid_number(x, 0.0, lo_open=True) is not None


def _spec_nonnegative(x):
    return _valid_number(x, 0.0) is not None


def _spec_efficiency(x):
    return _valid_number(x, 0.0, 1.0, lo_open=True) is not None


def _spec_fraction(x):
    return _valid_fraction(x) is not None


def _spec_finite(x):
    return _num(x) is not None


def _spec_temperature(x):
    return _valid_T(x) is not None


def _spec_pressure(x):
    """A pressure > 0, or a non-empty list of them (evaporator effects)."""
    if isinstance(x, list):
        return bool(x) and all(_valid_P(p) is not None for p in x)
    return _valid_P(x) is not None


def _spec_positive_int(x):
    value = _valid_number(x, 0.0, lo_open=True)
    return value is not None and value == int(value)


def _spec_split(x):
    """A split: a fraction, or a non-empty list / object of fractions."""
    if isinstance(x, list):
        return bool(x) and all(_valid_fraction(v) is not None for v in x)
    if isinstance(x, dict):
        return bool(x) and all(_valid_fraction(v) is not None
                               for v in x.values())
    return _valid_fraction(x) is not None


def _spec_lhk(x):
    """Light and heavy keys: exactly two non-empty strings."""
    return (isinstance(x, (list, tuple)) and len(x) == 2
            and all(_nonempty_str(k) for k in x))


def _spec_flag(x):
    return isinstance(x, bool)


# Plausibility of each design_input_specs parameter with a known role; any
# other parameter only needs a non-empty value (_nonempty_value). V is >= 0:
# it is a vapor fraction (flash, HXutility, evaporator; 0 = bubble point or
# total condensation) or a volume (batch bioreactors), and the key alone does
# not say which.
_SPEC_PREDICATES = {
    **dict.fromkeys(('T', 'T_lim0', 'T_lim1'), _spec_temperature),
    'P': _spec_pressure,
    **dict.fromkeys(('tau', 'N', 'V_max', 'k', 'dT', 'T_min_app', 'dP_design',
                     'pr', 'reflux', 'boilup'), _spec_positive),
    **dict.fromkeys(('tau_0', 'V'), _spec_nonnegative),
    **dict.fromkeys(('N_stages', 'n_stages'), _spec_positive_int),
    **dict.fromkeys(('V_wf', 'eta', 'efficiency', 'boiler_efficiency',
                     'turbogenerator_efficiency', 'theta_O2', 'theta', 'Lr',
                     'Hr', 'y_top', 'x_bot'), _spec_efficiency),
    'moisture_content': _spec_fraction,
    **dict.fromkeys(('split', 'solids_split', 'aqueous_split'), _spec_split),
    'LHK': _spec_lhk,
    **dict.fromkeys(('Q', 'H', 'H_lim0', 'H_lim1', 'Duty'), _spec_finite),
    **dict.fromkeys(('partial_condenser', 'full_condenser', 'batch',
                     'rigorous', 'approx_duty', 'conserve_phases', 'flash',
                     'heat_only', 'ignore_NPSH', 'is_divided',
                     'satisfy_system_electricity_demand',
                     'satisfy_system_steam_demand'), _spec_flag),
    'fuel_source': _nonempty_str,
}


def _valid_spec(key, value):
    """True when design_input_specs[key] = value is plausible for the
    parameter's role (_SPEC_PREDICATES; else any non-empty value)."""
    predicate = _SPEC_PREDICATES.get(key) if isinstance(key, str) else None
    return (predicate or _nonempty_value)(value)


# The 118 element symbols (molecular-formula recognition, R2.3).
_ELEMENTS = frozenset((
    'H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co '
    'Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb '
    'Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os '
    'Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm '
    'Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og').split())
_FORMULA_RE = re.compile(r'(?:[A-Z][a-z]?(?:\d+(?:\.\d+)?)?)+')
_FORMULA_TOKEN_RE = re.compile(r'([A-Z][a-z]?)(\d+(?:\.\d+)?)?')


def _formula_counts(text):
    """{element: count} of a plain molecular formula ('C2H6O', 'H3O4P',
    'CH1.61O0.56'), or None when `text` is not one (every token must be an
    element symbol; parentheses and charges are not parsed)."""
    if not isinstance(text, str) or not _FORMULA_RE.fullmatch(text.strip()):
        return None
    counts = {}
    for symbol, count in _FORMULA_TOKEN_RE.findall(text.strip()):
        if symbol not in _ELEMENTS:
            return None
        counts[symbol] = counts.get(symbol, 0) + (float(count) if count else 1)
    return counts


def _formula_like(cid):
    """True when a chemical id is itself a molecular formula ('C2H6O', 'HP',
    'CO2'); it then narrows the search to that formula (formula tier)."""
    return _formula_counts(cid) is not None


def _isomer_ambiguous(cid):
    """True when a chemical id is a molecular formula with two or more
    carbon atoms ('C2H6O': ethanol or dimethyl ether), so it earns no
    name-tier credit (R2.3 as corrected). A formula with at most one carbon
    atom ('CO2', 'CH4', 'H2SO4', 'P4O10', 'CaO') names one compound and is
    eligible for name credit like any other name."""
    counts = _formula_counts(cid)
    return counts is not None and counts.get('C', 0) >= 2


def _valid_formula(formula):
    """A formula that parses: plain (_formula_counts) or by the validator's
    `chemicals`-based parser (parentheses, ...)."""
    return _nonempty_str(formula) and (
        _formula_counts(formula) is not None
        or _validator()._molar_mass_from_formula(formula) is not None)


def _chemical_molar_mass(chem):
    """A chemical's declared molar mass (> 0), else the one derived from its
    formula, else None."""
    chem = _as_dict(chem)
    declared = _valid_number(chem.get('molar_mass'), 0.0, lo_open=True)
    if declared is not None:
        return declared
    formula = chem.get('formula')
    return (_validator()._molar_mass_from_formula(formula)
            if _nonempty_str(formula) else None)


def _record_molar_mass(record):
    """Molar mass of a `chemicals` database record: its MW, else derived
    from its formula; None when it carries neither."""
    mw = _valid_number(getattr(record, 'MW', None), 0.0, lo_open=True)
    if mw is not None:
        return mw
    formula = getattr(record, 'formula', None)
    return (_validator()._molar_mass_from_formula(formula)
            if _nonempty_str(formula) else None)


def _record_agreement(record, chem):
    """'verified' when a database record's molar mass matches the chemical's
    (declared or formula-derived) within NAME_MW_RTOL, or their formulas
    match; 'unverified' when either side states nothing comparable;
    'mismatch' otherwise (R2.3)."""
    chem = _as_dict(chem)
    mine_mw, theirs_mw = _chemical_molar_mass(chem), _record_molar_mass(record)
    mine_f = _formula_counts(chem.get('formula'))
    theirs_f = _formula_counts(getattr(record, 'formula', None))
    if mine_mw is not None and theirs_mw is not None \
            and abs(mine_mw - theirs_mw) <= NAME_MW_RTOL * theirs_mw:
        return 'verified'
    if mine_f is not None and mine_f == theirs_f:
        return 'verified'
    comparable = ((mine_mw is not None and theirs_mw is not None)
                  or (mine_f is not None and theirs_f is not None))
    return 'mismatch' if comparable else 'unverified'


def _name_tier(chem, cid):
    """CMP-THM-02 name tier of chemical id `cid` (chem: its declared entry,
    or None): None when the id is not a string, is an isomer-ambiguous
    formula (_isomer_ambiguous), or does not resolve by name; else the
    _record_agreement of the resolved record with the chemical."""
    if not _nonempty_str(cid) or _isomer_ambiguous(cid):
        return None
    record = _name_record(cid)
    return None if record is None else _record_agreement(record, chem)


_CAS_RE = re.compile(r'^(\d{2,7})-(\d{2})-(\d)$')
_INCHI_RE = re.compile(r'^InChI=1S?/\S+$')
_INCHIKEY_RE = re.compile(r'^[A-Z]{14}-[A-Z]{10}-[A-Z]$')
# SMILES tokens: organic-subset atoms (aliphatic, aromatic), bracket atoms,
# bonds, branches, ring-closure labels.
_SMILES_TOKEN = (r'Cl|Br|[BCNOPSFI]|[bcnops]|\[[^\[\]]*[A-Za-z][^\[\]]*\]'
                 r'|[-=#$:/\\.()]|%\d{2}|\d')
_SMILES_RE = re.compile(rf'(?:{_SMILES_TOKEN})+')
_SMILES_ATOM_RE = re.compile(r'Cl|Br|[BCNOPSFI]|[bcnops]|\[')


def _valid_cas(text):
    """A CAS registry number: 2-7, 2 and 1 digits with a correct check
    digit."""
    match = _CAS_RE.match(text) if isinstance(text, str) else None
    if not match:
        return False
    digits = (match.group(1) + match.group(2))[::-1]
    check = sum((i + 1) * int(d) for i, d in enumerate(digits)) % 10
    return check == int(match.group(3))


def _smiles_like(text):
    """SMILES-shaped text: only SMILES tokens, at least one atom, balanced
    branches and paired ring-closure labels."""
    if not isinstance(text, str) or not _SMILES_RE.fullmatch(text):
        return False
    if not _SMILES_ATOM_RE.search(text):
        return False
    bare = re.sub(r'\[[^\]]*\]', '', text)
    depth = 0
    for char in bare:
        depth += (char == '(') - (char == ')')
        if depth < 0:
            return False
    labels = re.findall(r'%\d{2}|\d', bare)
    return depth == 0 and all(labels.count(label) % 2 == 0 for label in labels)


def _cas_contradicts(cas, chem):
    """True when a CAS number resolves in the `chemicals` database to a
    record whose molar mass differs from the chemical's (declared or
    formula-derived) by more than CAS_MW_RTOL."""
    record = _name_record(cas)
    if record is None:
        return False
    mine, theirs = _chemical_molar_mass(chem), _record_molar_mass(record)
    return (mine is not None and theirs is not None
            and abs(mine - theirs) > CAS_MW_RTOL * theirs)


def _valid_registry_id(chem):
    """True when a chemical's registry_id is a plausible identifier: a CAS
    number (format and check digit, not contradicted by the database's
    molar mass), an InChI, an InChIKey or SMILES-like text (R2.0)."""
    rid = _as_dict(chem).get('registry_id')
    if not isinstance(rid, str):
        return False
    rid = rid.strip()
    if _valid_cas(rid):
        return not _cas_contradicts(rid, chem)
    return bool(_INCHI_RE.match(rid) or _INCHIKEY_RE.match(rid)
                or _smiles_like(rid))


# thermosteam model class names per property-package descriptor (enumerated
# from the thermosteam source: thermosteam.mixture, and the activity-,
# fugacity- and Poynting-correction modules of thermosteam.equilibrium,
# including their EOS-generated subclasses). The exporter writes the Poynting
# classes with the corrected spelling 'Poynting'; both spellings are accepted.
_THERMO_MODEL_CLASSES = {
    'mixture': frozenset((
        'Mixture', 'IdealMixture', 'EOSMixture', 'APISRKMixture', 'IGMixture',
        'MSRKTranslatedMixture', 'PR78Mixture', 'PRMixture', 'PRSV2Mixture',
        'PRSVMixture', 'PRTranslatedConsistentMixture', 'PRTranslatedMixture',
        'PRTranslatedPPJPMixture', 'PSRKMixture', 'RKMixture', 'SRKMixture',
        'SRKTranslatedConsistentMixture', 'SRKTranslatedMixture',
        'TWUPRMixture', 'TWUSRKMixture', 'VDWMixture')),
    'gamma': frozenset((
        'ActivityCoefficients', 'IdealActivityCoefficients',
        'GroupActivityCoefficients', 'UNIFACActivityCoefficients',
        'DortmundActivityCoefficients', 'NISTActivityCoefficients',
        'GCEOSActivityCoefficients', 'APISRKActivityCoefficients',
        'IGActivityCoefficients', 'MSRKMIXTranslaActivityCoefficients',
        'PActivityCoefficients', 'PR78ActivityCoefficients',
        'PRActivityCoefficients', 'PRMIXTranslaActivityCoefficients',
        'PRMIXTranslatedConsistActivityCoefficients',
        'PRMIXTranslatedPActivityCoefficients', 'PRSV2ActivityCoefficients',
        'PRSVActivityCoefficients', 'RKActivityCoefficients',
        'SRKActivityCoefficients', 'SRKMIXTranslaActivityCoefficients',
        'SRKMIXTranslatedConsistActivityCoefficients',
        'TWUPRActivityCoefficients', 'TWUSRKActivityCoefficients',
        'VDWActivityCoefficients')),
    'phi': frozenset((
        'FugacityCoefficients', 'IdealFugacityCoefficients',
        'GCEOSFugacityCoefficients', 'APISRKFugacityCoefficients',
        'IGFugacityCoefficients', 'MSRKTranslatedFugacityCoefficients',
        'PR78FugacityCoefficients', 'PRFugacityCoefficients',
        'PRSV2FugacityCoefficients', 'PRSVFugacityCoefficients',
        'PRTranslatedConsistentFugacityCoefficients',
        'PRTranslatedFugacityCoefficients',
        'PRTranslatedPPJPFugacityCoefficients', 'PSRKFugacityCoefficients',
        'RKFugacityCoefficients', 'SRKFugacityCoefficients',
        'SRKTranslatedConsistentFugacityCoefficients',
        'SRKTranslatedFugacityCoefficients', 'TWUPRFugacityCoefficients',
        'TWUSRKFugacityCoefficients', 'VDWFugacityCoefficients')),
    'PCF': frozenset((
        'PoyintingCorrectionFactors', 'MockPoyintingCorrectionFactors',
        'IdealGasPoyintingCorrectionFactors')),
}
_LEADING_IDENTIFIER_RE = re.compile(r'^\s*<?\s*([A-Za-z_]\w*)')


def _valid_package_descriptor(key, value):
    """True when thermo_property_package[key] names, by its leading
    identifier ('IdealMixture(include_excess_energies=False)' ->
    'IdealMixture'), a thermosteam model class of that role
    (_THERMO_MODEL_CLASSES)."""
    names = _THERMO_MODEL_CLASSES.get(key) if isinstance(key, str) else None
    match = _LEADING_IDENTIFIER_RE.match(value) \
        if names and isinstance(value, str) else None
    return bool(match) and match.group(1).replace(
        'Poynting', 'Poyinting') in names


def _reaction_sides(reaction, declared=frozenset(), chem_by_index=None):
    """(consumed, produced) species sets of a reaction: the union over its
    equation and its stoichiometry, each counted only when it names >= 1
    consumed and >= 1 produced species (both empty when neither does)."""
    reaction = _as_dict(reaction)
    consumed, produced = set(), set()
    for left, right in (
            _equation_sides(reaction.get('equation'), declared),
            _stoichiometry_sides(reaction.get('stoichiometry'), declared,
                                 chem_by_index or {})):
        if left and right:
            consumed.update(left)
            produced.update(right)
    return consumed, produced


def _well_formed(reaction, declared=frozenset(), chem_by_index=None):
    """True when a reaction's equation or stoichiometry names >= 1 consumed
    and >= 1 produced species with non-empty names (R2.0)."""
    return bool(_reaction_sides(reaction, declared, chem_by_index)[0])


def _valid_cost(x):
    """A plausible cost: finite, >= 0; else None."""
    return _valid_number(x, 0.0)

#%% Unit class resolution

# "Class on <source URL>": the exporter's design_simulation_method convention.
# The URL may be empty (the exporter writes none for a class defined outside
# biosteam and Bioindustrial-Park).
_DSM_CLASS_RE = re.compile(r'^\s*([A-Za-z_]\w*)\s+on\b\s*(\S*)')

# A source URL inside the core BioSTEAM repository (not Bioindustrial-Park),
# or the hensmith repository that now defines HeatExchangerNetwork.
_BIOSTEAM_URL_RE = re.compile(
    r'github\.com/BioSTEAMDevelopmentGroup/(?:biosteam|hensmith)'
    r'(?:[/#?]|\.git|$)', re.I)

FAMILIES = ('mixer', 'splitter', 'flash', 'hx_utility', 'hx_process',
            'steam_injection_heater', 'pump', 'valve', 'compressor', 'turbine',
            'distillation', 'staged_column', 'extraction', 'evaporator',
            'reactor', 'batch_bioreactor', 'aerated_bioreactor',
            'equilibrium_reactor', 'crystallizer', 'tank', 'combustor',
            'dryer', 'boiler', 'boiler_turbogenerator',
            'heat_exchanger_network', 'facility', 'solids_handling',
            'junction')

# ---- family sets shared with the costing / utility / reaction criteria ----
REACTOR_FAMILIES = frozenset({'reactor', 'batch_bioreactor',
                              'aerated_bioreactor', 'equilibrium_reactor'})
# Families that consume or produce utilities in BioSTEAM (CMP-UTIL-01/02):
# heat agents, electricity (pumps, compressors, agitated tanks, conveyors,
# mixer-settlers) or fuel.
UTILITY_USING_FAMILIES = REACTOR_FAMILIES | frozenset({
    'hx_utility', 'flash', 'distillation', 'staged_column', 'evaporator',
    'pump', 'compressor', 'turbine', 'boiler', 'boiler_turbogenerator',
    'steam_injection_heater', 'dryer', 'crystallizer', 'tank',
    'solids_handling', 'extraction'})
# The subset that exchanges heat with a heat-utility agent (steam, cooling
# water, refrigerants): the need behind CMP-UTIL-03 and CMP-UTIL-05, and
# behind the synthetic missing "heat agent" entry of CMP-UTIL-01/02 (R2.7).
HEAT_UTILITY_FAMILIES = REACTOR_FAMILIES | frozenset({
    'hx_utility', 'flash', 'distillation', 'staged_column', 'evaporator',
    'boiler', 'boiler_turbogenerator', 'steam_injection_heater',
    'crystallizer'})
# Families whose units always draw (or, for turbines and boiler-
# turbogenerators, deliver) electricity: the need behind the synthetic
# missing "power utility" entry of CMP-UTIL-01/02 (R2.7).
POWER_UTILITY_FAMILIES = frozenset({'pump', 'compressor', 'turbine',
                                    'solids_handling',
                                    'boiler_turbogenerator'})
# Families that regenerate agents for the rest of the plant (a boiler raising
# steam, a cooling tower, a heat-exchanger network): BioSTEAM records their
# heat utilities with reversed duty, so they are no evidence of whether an
# agent heats or cools (CMP-UTIL-03).
AGENT_REGENERATOR_FAMILIES = frozenset({'facility', 'boiler',
                                        'boiler_turbogenerator',
                                        'heat_exchanger_network'})
# Families whose chemistry is fixed by the class itself (combustion, a
# facility's internal make-up, Gibbs minimization, drying): never in the
# reactive population of CMP-RXN-01, and exempt from the species-appearance
# consistency guard.
CLASS_IMPLIED_CHEMISTRY_FAMILIES = frozenset({
    'combustor', 'facility', 'boiler', 'boiler_turbogenerator',
    'equilibrium_reactor', 'dryer'})
# Physical-only families (R2.2): absent declared reactions, every species
# balances one by one across such a unit, so a derivation from its stream
# data also needs every species' balance to close.
PHYSICAL_FAMILIES = frozenset({
    'mixer', 'hx_utility', 'hx_process', 'pump', 'valve', 'compressor',
    'turbine', 'flash', 'distillation', 'evaporator',
    'steam_injection_heater', 'tank', 'splitter'})
# Families whose units always draw a utility (CMP-UTIL-04, R2.0): power for
# pumps and compressors, heat for utility exchangers, columns, evaporators
# and flashes (a flash is waived only when stated adiabatic, Q = 0).
ALWAYS_DRAW_FAMILIES = frozenset({'pump', 'hx_utility', 'compressor',
                                  'distillation', 'evaporator', 'flash'})
# Families with a fuel port BioSTEAM prices from bst.stream_utility_prices
# (natural gas) or from class defaults (a boiler's lime and boiler
# chemicals) when the stream's own price is 0 (CMP-FEED-02, R2.9).
UTILITY_PORT_FAMILIES = frozenset({'combustor', 'boiler',
                                   'boiler_turbogenerator', 'dryer'})
# Families whose units carry plant-wide facility bindings and settings
# (CMP-UOP-06, R2.5) instead of operating specifications (CMP-UOP-02).
FACILITY_FAMILIES = frozenset({'facility', 'boiler', 'boiler_turbogenerator',
                               'heat_exchanger_network'})
# Families whose units carry no purchase cost by default. The class-level
# sets refine it: COST_FREE_CLASSES are cost-free whatever their family
# (Splitter-type splitters), _COSTED_CLASSES are costed although their family
# is cost-free (@cost-decorated Mixer subclasses). Use _is_cost_free.
COST_FREE_FAMILIES = frozenset({'mixer', 'junction'})
COST_FREE_CLASSES = frozenset({'Mixer', 'MockMixer', 'BlowdownMixer',
                               'Splitter', 'FakeSplitter', 'PhaseSplitter',
                               'Separator'})
_COSTED_CLASSES = frozenset({'AmmoniaMixer', 'EnzymeHydrolysateMixer',
                             'SulfuricAcidMixer'})

# Per-unit weight in every per-unit average (Revision 1, R1.6): units whose
# specification decides most of the flowsheet weigh most.
FAMILY_WEIGHTS = {
    **dict.fromkeys(('reactor', 'batch_bioreactor', 'aerated_bioreactor',
                     'equilibrium_reactor', 'distillation', 'staged_column',
                     'evaporator', 'flash', 'extraction', 'crystallizer'),
                    3),
    **dict.fromkeys(('hx_utility', 'hx_process', 'splitter', 'dryer',
                     'steam_injection_heater', 'facility', 'combustor',
                     'boiler', 'boiler_turbogenerator',
                     'heat_exchanger_network',
                     'compressor', 'turbine'), 2),
    **dict.fromkeys(('pump', 'valve', 'tank', 'mixer', 'solids_handling',
                     'junction'), 1),
}
UNKNOWN_FAMILY_WEIGHT = 2

# Curated class -> (family, registry_class). registry_class names the nearest
# design-spec-registry ancestor of a class the registry does not list (used by
# CMP-UOP-03). Covers every key of export/design_specs/biosteam.yaml (pinned by
# a Tier 1 test), the BioSTEAM facilities, and every class in the
# Bioindustrial-Park corpus. 'Unit' (the bare base class) has no family.
_CLASS_TABLE = {
    # -- core biosteam units: every design-spec registry key --
    'AbstractStirredTankReactor': ('reactor', None),
    'AdiabaticMultiStageVLEColumn': ('staged_column', None),
    'AeratedBioreactor': ('aerated_bioreactor', None),
    'AmineAbsorption': ('splitter', None),
    'AnaerobicBioreactor': ('reactor', None),
    'BagassePelletMill': ('solids_handling', None),
    'BatchCrystallizer': ('crystallizer', None),
    'BinaryDistillation': ('distillation', None),
    'CO2Compression': ('compressor', None),
    'Clarifier': ('splitter', None),
    'Compressor': ('compressor', None),
    'ConveyingBelt': ('solids_handling', None),
    'CrushingMill': ('splitter', None),
    'DiagramOnlyStreamUnit': ('junction', None),
    'DiagramOnlySystemUnit': ('junction', None),
    'DiagramOnlyUnit': ('junction', None),
    'Distillation': ('distillation', None),
    'DrumDryer': ('dryer', None),
    'Duplicator': ('junction', None),
    'EnzymeTreatment': ('reactor', None),
    'Flash': ('flash', None),
    'FluidizedCatalyticCracking': ('reactor', None),
    'GasFedBioreactor': ('aerated_bioreactor', None),
    'GibbsEquilibriumReactor': ('equilibrium_reactor', None),
    'HX': ('hx_utility', None),
    'HXprocess': ('hx_process', None),
    'HXutilities': ('hx_utility', None),
    'HXutility': ('hx_utility', None),
    'HammerMill': ('solids_handling', None),
    'IsenthalpicValve': ('valve', None),
    'IsentropicCompressor': ('compressor', None),
    'IsentropicTurbine': ('turbine', None),
    'IsothermalCompressor': ('compressor', None),
    'JetCooker': ('steam_injection_heater', None),  # corn; registry-listed
    'Junction': ('junction', None),
    'LLECentrifuge': ('extraction', None),
    'LLESettler': ('extraction', None),
    'LLEUnit': ('extraction', None),
    'LiquidsCentrifuge': ('splitter', None),
    'LiquidsMixingTank': ('tank', None),
    'LiquidsPartitionSettler': ('splitter', None),
    'LiquidsRatioCentrifuge': ('splitter', None),
    'LiquidsSettler': ('splitter', None),
    'LiquidsSplitCentrifuge': ('splitter', None),
    'LiquidsSplitSettler': ('splitter', None),
    'MESHDistillation': ('staged_column', None),
    'MagneticSeparator': ('splitter', None),
    'MassBalance': ('junction', None),
    'MixTank': ('tank', None),
    'Mixer': ('mixer', None),
    'MixerSettler': ('extraction', None),
    'MockMixer': ('mixer', None),
    'MockSplitter': ('junction', None),
    'MolecularSieve': ('splitter', None),
    'MultiEffectEvaporator': ('evaporator', None),
    'MultiStageEquilibrium': ('staged_column', None),
    'MultiStageMixerSettlers': ('extraction', None),
    'MultistageCompressor': ('compressor', None),
    'NRELAnaerobicBatchBioreactor': ('batch_bioreactor', None),
    'NRELBatchBioreactor': ('batch_bioreactor', None),
    'NRELEthanolFermentation': ('batch_bioreactor', None),
    'PelletMill': ('solids_handling', None),
    'PhasePartition': ('flash', None),
    'PhaseSplitter': ('splitter', None),
    'PolytropicCompressor': ('compressor', None),
    'PressureFilter': ('splitter', None),
    'Pump': ('pump', None),
    'ReactivePhaseStage': ('reactor', None),
    'ReversedSplitter': ('junction', None),
    'RotaryVacuumFilter': ('splitter', None),
    'SLLECentrifuge': ('splitter', None),
    'Scaler': ('junction', None),
    'ScrewFeeder': ('solids_handling', None),
    'ScrewPress': ('splitter', None),
    'Separator': ('splitter', None),
    'ShortcutColumn': ('distillation', None),
    'Shredder': ('solids_handling', None),
    'SingleComponentAdsorptionColumn': ('splitter', None),
    'SinglePhaseReactor': ('reactor', None),
    'SinglePhaseStage': ('hx_utility', None),
    'SolidLiquidsSplitCentrifuge': ('splitter', None),
    'SolidsCentrifuge': ('splitter', None),
    'SolidsSeparator': ('splitter', None),
    'SplitFlash': ('flash', None),
    'Splitter': ('splitter', None),
    'SprayDryer': ('dryer', None),
    'StageEquilibrium': ('flash', None),
    'SteamMixer': ('steam_injection_heater', None),
    'StorageTank': ('tank', None),
    'Tank': ('tank', None),
    'ThermalOxidizer': ('combustor', None),
    'Turbine': ('turbine', None),
    'Unit': (None, None),
    'Valve': ('valve', None),
    'VentScrubber': ('splitter', None),
    'VibratingScreen': ('splitter', None),
    # -- core biosteam facilities and helpers (not in the registry) --
    'AirDistributionPackage': ('facility', None),
    'BlowdownMixer': ('mixer', 'Mixer'),
    'Boiler': ('boiler', None),
    'BoilerTurbogenerator': ('boiler_turbogenerator', None),
    'CIPpackage': ('facility', None),
    'ChemicalCapitalInvestment': ('facility', None),
    'ChilledWaterPackage': ('facility', None),
    'CoolingTower': ('facility', None),
    'Facility': ('facility', None),
    'FakeSplitter': ('splitter', None),
    'FireWaterTank': ('facility', None),
    'HeatExchangerNetwork': ('heat_exchanger_network', None),
    'ProcessWaterCenter': ('facility', None),
    'RefrigerationPackage': ('facility', None),
    # -- core biosteam.wastewater and older biosteam names in the corpus --
    'AdsorptionColumnTSA': ('splitter', None),
    'AerobicDigestion': ('reactor', None),
    'AnMBR': ('reactor', None),
    'AnaerobicDigestion': ('reactor', None),
    'BeltThickener': ('splitter', None),
    'BiogasUpgrading': ('splitter', None),       # CO2 removal from biogas
    'InternalCirculationRx': ('reactor', 'MixTank'),
    'MembraneBioreactor': ('splitter', 'Splitter'),  # a Splitter subclass
    'NRELFermentation': ('batch_bioreactor', None),
    'PolishingFilter': ('reactor', None),        # anaerobic polishing filter
    'ReverseOsmosis': ('splitter', None),
    'SludgeCentrifuge': ('splitter', None),
    'WastewaterSystemCost': ('facility', None),
    # -- Bioindustrial-Park corn (tank_factory tanks, Splitter subclasses) --
    'AlphaAmylaseTank': ('tank', 'StorageTank'),
    'AmmoniaTank': ('tank', 'StorageTank'),
    'CleaningSystem': ('splitter', 'Splitter'),
    'CookedSlurrySurgeTank': ('tank', 'StorageTank'),
    'CornStorage': ('tank', 'StorageTank'),
    'DDGSCentrifuge': ('splitter', 'SolidsCentrifuge'),
    'DDGSHandling': ('solids_handling', None),
    'GrainHandling': ('solids_handling', None),
    'LimeHopper': ('tank', 'StorageTank'),
    'Liquefaction': ('reactor', 'MixTank'),     # MixTank + class-fixed reaction
    'LiquefactionTank': ('tank', 'MixTank'),
    'MilledCornHopper': ('tank', 'StorageTank'),
    'MilledCornSurgeTank': ('tank', 'StorageTank'),
    'MilledCornWeighTank': ('tank', 'StorageTank'),
    'PlantAir_CIP_WasteWater_Facilities': ('facility', None),
    'SimultaneousSaccharificationFermentation': ('batch_bioreactor',
                                                 'NRELBatchBioreactor'),
    'SlurryMixTank': ('tank', 'MixTank'),
    'WetDDGSConveyor': ('solids_handling', None),
    'YeastTank': ('tank', 'StorageTank'),
    # -- other Bioindustrial-Park biorefineries (cellulosic, HP, TAL, ...) --
    'AcidulationReactor': ('reactor', 'AbstractStirredTankReactor'),
    'AmmoniaAdditionTank': ('tank', 'MixTank'),
    'AmmoniaMixer': ('mixer', 'Mixer'),
    'AmmoniaStorageTank': ('tank', None),
    'BatchCoFermentation': ('batch_bioreactor', 'NRELAnaerobicBatchBioreactor'),
    'BlowdownDischargePump': ('pump', None),
    'CSLstorageTank': ('tank', None),
    'CoFermentation': ('reactor', None),
    'ContinuousPresaccharification': ('reactor', None),
    'DehydrationReactor': ('reactor', 'AbstractStirredTankReactor'),
    'EnzymeHydrolysateMixer': ('mixer', 'Mixer'),
    'GypsumFilter': ('splitter', 'SolidsSeparator'),
    'HPPump': ('pump', 'Pump'),
    'HPStorageTank': ('tank', 'StorageTank'),
    'HydrogenationReactor': ('reactor', 'AbstractStirredTankReactor'),
    'HydrolysateHeatExchanger': ('hx_utility', 'HXutility'),
    'HydrolyzatePump': ('pump', None),
    'LimeStorage': ('tank', None),
    'LimeStorageBin': ('tank', None),
    'OligomerConversionTank': ('reactor', None),
    'PretreatmentFlash': ('flash', 'Flash'),
    'PretreatmentReactorSystem': ('reactor', None),
    'RingOpeningHydrolysisReactor': ('reactor', 'AbstractStirredTankReactor'),
    'Saccharification': ('batch_bioreactor', None),
    'SeedHoldTank': ('tank', None),
    'SeedTrain': ('reactor', None),
    'SuccinicAcidCrystallizer': ('crystallizer', 'BatchCrystallizer'),
    'SulfuricAcidMixer': ('mixer', 'Mixer'),
    'SulfuricAcidStorage': ('tank', None),
    'SulfuricAcidStorageTank': ('tank', None),
    'SulfuricAcidTank': ('tank', None),
    'TALCrystallizer': ('crystallizer', 'BatchCrystallizer'),
    'WasteVaporCondenser': ('hx_utility', 'HXutility'),
}

# Table entries defined in Bioindustrial-Park rather than in biosteam; every
# other entry is a core biosteam class (the curated core list).
_NONCORE_CLASSES = frozenset({
    'JetCooker', 'AlphaAmylaseTank', 'AmmoniaTank', 'CleaningSystem',
    'CookedSlurrySurgeTank', 'CornStorage', 'DDGSCentrifuge', 'DDGSHandling',
    'GrainHandling', 'LimeHopper', 'Liquefaction', 'LiquefactionTank',
    'MilledCornHopper', 'MilledCornSurgeTank', 'MilledCornWeighTank',
    'PlantAir_CIP_WasteWater_Facilities',
    'SimultaneousSaccharificationFermentation', 'SlurryMixTank',
    'WetDDGSConveyor', 'YeastTank', 'AcidulationReactor',
    'AmmoniaAdditionTank', 'AmmoniaMixer', 'AmmoniaStorageTank',
    'BatchCoFermentation', 'BlowdownDischargePump', 'CSLstorageTank',
    'CoFermentation', 'ContinuousPresaccharification', 'DehydrationReactor',
    'EnzymeHydrolysateMixer', 'GypsumFilter', 'HPPump', 'HPStorageTank',
    'HydrogenationReactor', 'HydrolysateHeatExchanger', 'HydrolyzatePump',
    'LimeStorage', 'LimeStorageBin', 'OligomerConversionTank',
    'PretreatmentFlash', 'PretreatmentReactorSystem',
    'RingOpeningHydrolysisReactor', 'Saccharification', 'SeedHoldTank',
    'SeedTrain', 'SuccinicAcidCrystallizer', 'SulfuricAcidMixer',
    'SulfuricAcidStorage', 'SulfuricAcidStorageTank', 'SulfuricAcidTank',
    'TALCrystallizer', 'WasteVaporCondenser'})

# The curated STATIC core list (R2.6): every concrete (not isabstract=True)
# Unit subclass defined in biosteam -- biosteam.units, biosteam.facilities,
# biosteam.wastewater -- plus HeatExchangerNetwork (now defined in the
# hensmith package and re-exported by biosteam), enumerated once from the
# biosteam 2.54 source tree and committed here, so the scorer needs no
# biosteam import. Only these classes identify a unit at confidence 1 without
# a pinned model package. Table entries outside it (Bioindustrial-Park
# classes, classes made by biosteam's tank_factory inside a biorefinery, and
# older biosteam names the corpus still carries: AdsorptionColumnTSA,
# NRELFermentation, NRELBatchBioreactor) are custom classes.
_CORE_CLASSES = frozenset({
    'AdiabaticMultiStageVLEColumn', 'AeratedBioreactor',
    'AerobicDigestion', 'AirDistributionPackage', 'AmineAbsorption',
    'AnMBR', 'AnaerobicBioreactor', 'AnaerobicDigestion',
    'BagassePelletMill', 'BatchCrystallizer', 'BeltThickener',
    'BinaryDistillation', 'BiogasUpgrading', 'BlowdownMixer', 'Boiler',
    'BoilerTurbogenerator', 'CHP', 'CIPpackage', 'CO2Compression',
    'ChemicalCapitalInvestment', 'ChilledWaterPackage', 'Clarifier',
    'ConveyingBelt', 'CoolingTower', 'CrushingMill', 'DrumDryer',
    'EnzymeTreatment', 'Evaporator', 'FacilityModule', 'FireWaterTank',
    'Flash', 'FluidizedCatalyticCracking', 'GasFedBioreactor',
    'GibbsEquilibriumReactor', 'GroupedUnit', 'HXprocess', 'HXutilities',
    'HXutility', 'HammerMill', 'HeatExchangerNetwork',
    'InternalCirculationRx', 'IsenthalpicValve', 'IsentropicCompressor',
    'IsentropicTurbine', 'IsothermalCompressor', 'Junction',
    'LLECentrifuge', 'LLESettler', 'LiquidsMixingTank',
    'LiquidsPartitionSettler', 'LiquidsRatioCentrifuge',
    'LiquidsSplitCentrifuge', 'LiquidsSplitSettler', 'MESHDistillation',
    'MagneticSeparator', 'MassBalance', 'MembraneBioreactor', 'MixTank',
    'Mixer', 'MixerSettler', 'MockMixer', 'MockSplitter', 'Module',
    'MolecularSieve', 'MultiEffectEvaporator', 'MultiStageEquilibrium',
    'MultiStageMixerSettlers', 'MultistageCompressor',
    'NRELEthanolFermentation', 'PelletMill', 'PhasePartition',
    'PhaseSplitter', 'PolishingFilter', 'PolytropicCompressor',
    'PressureFilter', 'ProcessWaterCenter', 'Pump', 'ReactivePhaseStage',
    'RefluxDrum', 'RefrigerationPackage', 'ReverseOsmosis',
    'ReversedSplitter', 'RotaryVacuumFilter', 'SLLECentrifuge', 'Scaler',
    'ScrewFeeder', 'ScrewPress', 'Separator', 'ShortcutColumn', 'Shredder',
    'SingleComponentAdsorptionColumn', 'SinglePhaseReactor',
    'SinglePhaseStage', 'Skipped', 'SludgeCentrifuge', 'SludgeHandling',
    'SolidLiquidsSplitCentrifuge', 'SolidsCentrifuge', 'SolidsSeparator',
    'SplitFlash', 'Splitter', 'SprayDryer', 'StageEquilibrium',
    'SteamMixer', 'StorageTank', 'ThermalOxidizer', 'VentScrubber',
    'VibratingScreen', 'WWTpump', 'WastewaterSystemCost'})

# Abstract BioSTEAM classes (R2.6): declared isabstract=True in the biosteam
# source, plus the Unit base class itself. They cannot be instantiated, so
# naming one identifies a family at most (confidence 0.5) and earns no
# class-default or core-class credit.
_ABSTRACT_CLASSES = frozenset({
    'AbstractStirredTankReactor', 'Compressor', 'DiagramOnlyStreamUnit',
    'DiagramOnlySystemUnit', 'DiagramOnlyUnit', 'Distillation',
    'Duplicator', 'Facility', 'HX', 'LLEUnit', 'LiquidsCentrifuge',
    'LiquidsSettler', 'NRELAnaerobicBatchBioreactor', 'Tank', 'Turbine',
    'Unit', 'Valve'})

# Keyword fallback, in precedence order: the first family whose pattern
# matches the (raw or CamelCase-split) lowercase text wins, so specific
# families precede generic ones -- junction-like before everything;
# turbogenerator before a plain boiler (R2.10), both before turbine; heat
# exchanger network before heat exchanger; named plant facilities (fire
# water tank, plant air / CIP, the wastewater cost lump) before wastewater
# treatment, which scores as the reactor family of the biosteam.wastewater
# digesters (R2.10), before a generic 'facility'; mixer settler and steam
# mixer before mixer; MESH/multistage/adiabatic columns and absorbers before
# distillation; combustor/dryer/equilibrium/aerated/batch reactors and
# crystallizers before reactor; neutralizers, hydrotreaters, gasifiers and
# pyrolysis units are reactors, and reactor precedes tank ('Neutralization
# tank'); screw press and crushing mill (splitter) before screw and mill
# (solids handling); mix tank (tank) before mix (mixer).
_FAMILY_KEYWORDS = tuple((family, re.compile(pattern)) for family, pattern in (
    ('junction', r'\bjunction|\bscaler\b|duplicator|mass balance|'
                 r'diagram only|\bmock\b'),
    ('boiler_turbogenerator', r'turbo ?generator'),
    ('boiler', r'\bboiler\b'),
    ('heat_exchanger_network', r'heat exchanger network|\bhxn\b'),
    ('facility', r'cooling tower|chilled water|process water|'
                 r'\bcip\b|cip ?package|fire ?water|air distribution|'
                 r'plant air|refrigeration package|waste ?water system cost'),
    ('reactor', r'waste ?water'),
    ('facility', r'facilit'),
    ('extraction', r'mixer ?settler|extract|liquid ?liquid|\blle\b'),
    ('steam_injection_heater', r'steam ?mixer|jet ?cooker|steam injection|'
                               r'direct steam'),
    ('staged_column', r'\bmesh\b|multi ?stage (?:equilibrium|vle)|'
                      r'adiabatic multi|absor(?:ber|ption column)|stripper'),
    ('combustor', r'combust|oxidi[sz]er|furnace|incinerat|burner'),
    ('dryer', r'dryer|drier\b|drying'),
    ('equilibrium_reactor', r'equilibrium reactor|gibbs'),
    ('aerated_bioreactor', r'\baerated|gas ?fed'),
    ('batch_bioreactor', r'batch ?(?:bio)?reactor|batch ?(?:co ?)?ferment|'
                         r'\bssf\b|simultaneous saccharification'),
    ('hx_process', r'hx ?process|interchanger|economi[sz]er'),
    ('hx_utility', r'hx ?utilit|heat exchanger|heater|cooler|condenser|'
                   r'reboiler|\bhx\b'),
    ('pump', r'pump'),
    ('valve', r'valve'),
    ('compressor', r'compress|blower'),
    ('turbine', r'turbine'),
    ('evaporator', r'evaporat'),
    ('flash', r'flash'),
    ('crystallizer', r'crystalli'),
    ('reactor', r'reactor|ferment|saccharif|digest|hydroly|liquefaction|'
                r'\brx\b|\bmbr\b|seed train|neutrali[sz]|hydrotreat|gasif|'
                r'pyroly'),
    ('splitter', r'splitter|\bsplit|centrifuge|filter|\bpress\b|screen|'
                 r'separator|decanter|settler|sieve|scrubber|clarifier|'
                 r'adsorp|absorp|membrane|cyclone|osmosis|thickener|'
                 r'crushing mill'),
    ('distillation', r'distillation|column|rectifier'),
    ('tank', r'tank|storage|vessel|hopper|\bsilo|\bbin\b'),
    ('solids_handling', r'convey|\bmill|crusher|grinder|screw|grain handling|'
                        r'hammer|handling|shredder|feeder'),
    ('mixer', r'mixer|\bmix\b|blend'),
))


def _normalized_texts(text):
    """(raw lowercase, CamelCase-split spaced lowercase) variants of a name,
    so 'HXutility' and 'GrainHandling' both match their keywords."""
    spaced = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ',
                    text)
    spaced = re.sub(r'[\s_\-/]+', ' ', spaced).strip().lower()
    return text.lower(), spaced


def _keyword_family(text):
    """Family whose keyword pattern (in _FAMILY_KEYWORDS precedence order)
    matches `text` (a unit_type or a class name), or None."""
    if not _nonempty_str(text):
        return None
    variants = _normalized_texts(text)
    for family, pattern in _FAMILY_KEYWORDS:
        if any(pattern.search(v) for v in variants):
            return family
    return None


def _class_family(class_name):
    """Family of a known class: the curated table (whose 'Unit' entry
    deliberately has none), else keywords on the class name."""
    if class_name in _CLASS_TABLE:
        return _CLASS_TABLE[class_name][0]
    return _keyword_family(class_name)


def _dsm_class(unit):
    """(class name, source URL) from design_simulation_method "Class on
    <URL>" (URL may be ''), else (None, None)."""
    dsm = _as_dict(unit).get('design_simulation_method')
    match = _DSM_CLASS_RE.match(dsm) if isinstance(dsm, str) else None
    return (match.group(1), match.group(2)) if match else (None, None)


def _is_core_class(class_name, source=None):
    """True for a concrete core BioSTEAM class (R2.6): its name is on the
    static core list (_CORE_CLASSES) and no source URL attributes it to
    another repository (none, empty, or under the biosteam / hensmith
    repositories). A biosteam URL alone never makes a class core."""
    if not _nonempty_str(class_name) or class_name not in _CORE_CLASSES:
        return False
    return not _nonempty_str(source) or bool(_BIOSTEAM_URL_RE.search(source))


def _class_confidence(class_name, source, pinned):
    """Identity confidence of a class NAMED by a unit (R2.6): 1.0 for a
    concrete core class (_is_core_class); None -- identifies a family at
    most -- for an abstract class (_ABSTRACT_CLASSES) or a class a biosteam
    URL claims but that is neither on the core list nor a curated table
    entry (tank_factory and legacy biosteam classes are table entries);
    else a custom class: 1.0 when the model package is `pinned`, 0.75."""
    if class_name in _ABSTRACT_CLASSES:
        return None
    if _is_core_class(class_name, source):
        return 1.0
    if (_nonempty_str(source) and _BIOSTEAM_URL_RE.search(source)
            and class_name not in _CLASS_TABLE):
        return None
    return 1.0 if pinned else 0.75


def _infer_class_from_specs(unit, registry):
    """(class name or None, family) of the design-spec registry entry whose
    parameter set is the tightest superset of the unit's stated
    design_input_specs keys. Equally tight entries sharing one family yield
    that family with class None; None when the unit states no spec, nothing
    contains the key set, or tied entries disagree on the family."""
    keys = _explicit_keys(unit)
    if not keys:
        return None
    best, tied = None, []
    for name, entry in _as_dict(registry).items():
        params = set(_as_dict(_as_dict(entry).get('design_input_spec_params')))
        if not keys <= params:
            continue
        if best is None or len(params) < best:
            best, tied = len(params), [name]
        elif len(params) == best:
            tied.append(name)
    if len(tied) == 1:
        return tied[0], _class_family(tied[0])
    families = {_class_family(name) for name in tied}
    return (None, families.pop()) if len(families) == 1 else None


def _unit_class(unit, registry=None, pinned=False):
    """(class_name or None, family or None, confidence) for one unit.

    A class named by design_simulation_method "Class on <URL>", or by a
    unit_type that exactly names a registry/table/core class, scores
    _class_confidence: 1.0 for a concrete core BioSTEAM class, 1.0 or 0.75
    for a custom class (model package `pinned` to a commit or version, or
    not). An abstract class, or an unverifiable biosteam-URL claim, names no
    class (R2.6): the unit resolves exactly as if it were not named, with
    the named class's family as the last-resort family (so naming such a
    class never scores above, nor below, leaving it out). Without a named
    class, a unique tightest-superset match of the design_input_specs key
    set against the registry infers the class (0.75); a unit_type keyword
    (or a family-only key-set match) gives the family alone (0.5); else
    (None, None, 0.0)."""
    unit = _as_dict(unit)
    registry = _as_dict(registry)
    name, source = _dsm_class(unit)
    unit_type = unit.get('unit_type')
    unit_type = unit_type.strip() if isinstance(unit_type, str) else None
    typed = unit_type if unit_type and (
        unit_type in registry or unit_type in _CLASS_TABLE
        or unit_type in _CORE_CLASSES) else None
    inferred = _infer_class_from_specs(unit, registry)
    for candidate, url in ((name, source), (typed, None)):
        if not candidate:
            continue
        confidence = _class_confidence(candidate, url, pinned)
        if confidence is None:
            continue
        family = _class_family(candidate)
        if family is None and candidate not in _CLASS_TABLE and inferred:
            family = inferred[1]
        return candidate, family, confidence
    if inferred and inferred[0]:
        return inferred[0], inferred[1], 0.75
    family = (_keyword_family(unit_type) or (inferred[1] if inferred else None)
              or (_class_family(name) if name else None)
              or (_class_family(typed) if typed else None))
    return (None, family, 0.5) if family else (None, None, 0.0)


def _declared_registry_class(class_name, registry):
    """Design-spec registry entry of a named class: the class itself if
    listed, else the curated table's registry_class if listed; or None. Uses
    no key-set inference, so it is what CMP-UOP-03 measures a unit whose
    class is known exactly against."""
    registry = _as_dict(registry)
    if not _nonempty_str(class_name):
        return None
    if class_name in registry:
        return class_name
    ancestor = _CLASS_TABLE.get(class_name, (None, None))[1]
    return ancestor if ancestor in registry else None


def _registry_class(class_name, family, unit, registry):
    """Design-spec registry class a unit's essential rules resolve through:
    the exact class if listed, else the curated table's registry_class, else
    the key-set inference when it agrees with the unit's family; or None."""
    registry = _as_dict(registry)
    declared = _declared_registry_class(class_name, registry)
    if declared:
        return declared
    inferred = _infer_class_from_specs(unit, registry)
    if inferred and inferred[0] and (family is None or inferred[1] == family):
        return inferred[0]
    return None


def _family_weight(family):
    """Per-unit weight of a family (UNKNOWN_FAMILY_WEIGHT when unknown)."""
    return FAMILY_WEIGHTS.get(family, UNKNOWN_FAMILY_WEIGHT)


def _unit_weight(sc, position):
    """Weight of unit `position` in every per-unit average (R1.6); shared by
    the unit-operation, topology, thermo and costing criteria."""
    return _family_weight(sc.unit_classes[position][1])


def _wmean(pairs):
    """Weighted mean of (value, weight) pairs; None when the weights sum to
    zero (or there are no pairs)."""
    pairs = list(pairs)
    total = sum(w for _v, w in pairs)
    return sum(v * w for v, w in pairs) / total if total else None


def _is_cost_free(class_name, family):
    """True when a unit of this class/family carries no purchase cost by
    default: a COST_FREE_CLASSES class, or a COST_FREE_FAMILIES family unless
    the class is a known @cost-decorated subclass."""
    if class_name in COST_FREE_CLASSES:
        return True
    return family in COST_FREE_FAMILIES and class_name not in _COSTED_CLASSES


def _core_class_known(sc, position):
    """True when unit `position` is a core BioSTEAM class identified at
    confidence 1 (BioSTEAM then supplies the class's own defaults, design and
    cost methods)."""
    name, _family, confidence = sc.unit_classes[position]
    return bool(name) and confidence == 1.0 and sc.unit_core[position]

#%% Essential operating-specification rules (CMP-UOP-02 / CMP-UOP-03)

# One requirement: `n` of `keys` must be explicit design_input_specs.
#   derive    -- key -> conditions (all must hold) under which its value is
#                derivable from other data; see _condition_holds.
#   when      -- a data condition gating the whole requirement (None: always);
#                a condition that cannot be evaluated does not apply it.
#   guard     -- key -> 'T'|'P'|'T_main': an explicit value must agree with
#                every non-empty outlet stream ('T_main': with the
#                largest-flow outlet only) -- T within GUARD_T_ABS K, P within
#                GUARD_P_RTOL relative -- or the requirement scores 0.
#   counts_if -- key -> condition: the explicit key counts only if it holds.
_Req = namedtuple('_Req', 'n keys derive when guard counts_if',
                  defaults=(None, None, None))

GUARD_T_ABS = 1.0    # K
GUARD_P_RTOL = 0.01  # relative

# Reactor T is checked against the main (largest-flow) outlet only: auxiliary
# outlets such as a spent-catalyst purge are not set to T by BioSTEAM.
_T_REQ = _Req(1, ('T',), {'T': ('outlet_T',)}, guard={'T': 'T_main'})
# Unguarded T: BioSTEAM's NREL-type batch bioreactors mix their inlets
# adiabatically (effluent.mix_from(ins)), so the outlets sit at the mixed-inlet
# temperature while T only sets the heat-utility temperature.
_T_FREE = _Req(1, ('T',), {'T': ('outlet_T',)})
_P_REQ = _Req(1, ('P',), {'P': ('outlet_P',)}, guard={'P': 'P'})
_P_FREE = _Req(1, ('P',), {'P': ('outlet_P',)})
_TAU = _Req(1, ('tau',), {})
_V_WF = _Req(1, ('V_wf',), {})
_SPLIT = _Req(1, ('split',), {'split': ('all_ports_state',)})
_ETA = _Req(1, ('eta',), {})
_T_OR_Q = _Req(1, ('T', 'Q'), {'T': ('outlet_T',), 'Q': ('enthalpy',)},
               guard={'T': 'T'})
# feed_stages defaults to (0, -1) in the constructor: a constructor argument,
# so it must be stated (R2.8).
_FEED_STAGES = _Req(1, ('feed_stages',), {})
# A stirred-tank reactor's `batch` flag (R2.8): continuous by default, a
# constructor argument. Applies unless the unit names a class whose
# design-spec registry entry lacks `batch` (or that the registry does not
# list); a unit of unknown class must state it (R2.13), so identifying the
# class can only waive it.
_BATCH = _Req(1, ('batch',), {}, when='batch_param')

# Family rules (Revision 1, R1.4), checked against the biosteam source.
_FAMILY_RULES = {
    'mixer': (), 'solids_handling': (), 'facility': (), 'junction': (),
    'combustor': (),
    'splitter': (_SPLIT,),
    'dryer': (_SPLIT,),
    # MultiStageMixerSettlers: N_stages required, feed_stages default (0, -1);
    # the LLE split itself is computed from the thermo (or partition_data).
    'extraction': (_Req(1, ('N_stages',), {}), _FEED_STAGES),
    'flash': (_Req(2, ('T', 'P', 'V', 'Q', 'y', 'x'),
                   {'T': ('outlet_T',), 'P': ('outlet_P',),
                    'V': ('all_ports_state',), 'Q': ('enthalpy',)},
                   guard={'T': 'T', 'P': 'P'}),),
    # HXutility: one of T/H (V alone is a full spec only when rigorous);
    # non-rigorous, T does not move the phase, so a phase change needs V.
    'hx_utility': (_Req(1, ('T', 'H', 'V'), {'T': ('outlet_T',)},
                        guard={'T': 'T'}, counts_if={'V': 'rigorous'}),
                   _Req(1, ('V',), {'V': ('outlet_phases',)},
                        when='phase_change_nonrigorous')),
    # HXprocess: one of dT / T_lim / H_lim; when the phases change, the
    # enforced outlet phases phase0 / phase1 (else a VLE decides them).
    'hx_process': (_Req(1, ('dT', 'T_lim0', 'T_lim1', 'H_lim0', 'H_lim1'),
                        {'T_lim0': ('outlet_T',)}),
                   _Req(1, ('phase0', 'phase1'), {'phase0': ('outlet_phases',)},
                        when='phase_change')),
    'steam_injection_heater': (_Req(1, ('T', 'P'), {'T': ('outlet_T',)}),),
    # Pump: P None -> outlet P = inlet P, and dP_design then sizes the pump.
    'pump': (_P_REQ, _Req(1, ('dP_design',), {}, when='no_pressure_rise')),
    'valve': (_P_REQ,),
    'compressor': (_P_REQ, _ETA),
    'turbine': (_P_REQ, _ETA),
    'distillation': (
        _Req(1, ('LHK',), {}),
        _P_FREE,
        _Req(1, ('Lr', 'y_top'), {'Lr': ('all_ports_state', 'explicit:LHK')}),
        _Req(1, ('Hr', 'x_bot'), {'Hr': ('all_ports_state', 'explicit:LHK')}),
        _Req(1, ('k',), {'k': ('design_result:Reflux',
                               'design_result:Minimum reflux')}),
        _Req(1, ('partial_condenser',),
             {'partial_condenser': ('outlet_phases',)})),
    # MESHDistillation: N_stages and feed_stages required; the top needs
    # reflux, the bottom boilup or bottoms_product_to_feed (or explicit
    # stage_specifications for either).
    'staged_column': (
        _Req(1, ('N_stages',), {}),
        _Req(1, ('feed_stages',), {}),
        _Req(1, ('reflux', 'stage_specifications'), {}),
        _Req(1, ('boilup', 'bottoms_product_to_feed', 'stage_specifications'),
             {}),
        _P_FREE),
    'evaporator': (_Req(1, ('P',), {}),
                   _Req(1, ('V',), {'V': ('all_ports_state',)})),
    'reactor': (_T_REQ, _TAU, _V_WF, _BATCH),
    # NREL batch bioreactors: either N or V_max (V in older biosteam); tau_0
    # (cleaning and unloading time) is a class attribute -- a class default
    # only where the constructor does not take it (R2.8: NRELBatchBioreactor
    # and NRELEthanolFermentation; NRELAnaerobicBatchBioreactor takes it);
    # T is the heat-utility temperature, not the (adiabatic-mix) outlet
    # temperature.
    'batch_bioreactor': (
        _T_FREE, _TAU,
        _Req(1, ('N', 'V', 'V_max'),
             {'N': ('design_result:Number of reactors',),
              'V': ('design_result:Reactor volume',)}),
        _Req(1, ('tau_0',), {'tau_0': ('class_default',)})),
    # AeratedBioreactor: the O2 transfer (and so the air flow and compressor)
    # is solved from the reactions' O2 uptake against the dissolved-O2 target
    # theta_O2, a fraction of saturation defaulting to 0.5 in the constructor
    # (_init), so it must be stated (R2.8). Q_O2_consumption only forces the
    # duty per O2 consumed, so it is not an alternative.
    'aerated_bioreactor': (_T_REQ, _TAU, _V_WF, _Req(1, ('theta_O2',), {})),
    'equilibrium_reactor': (_T_REQ, _P_FREE),
    # BatchCrystallizer: T (default 305.15 K), tau, and N or V.
    'crystallizer': (_T_REQ, _TAU, _Req(1, ('N', 'V'), {})),
    'tank': (_TAU,),
    # Boilers, boiler-turbogenerators and heat-exchanger networks carry
    # plant-wide settings, scored by CMP-UOP-06 (R2.5), not here.
    'boiler': (), 'boiler_turbogenerator': (), 'heat_exchanger_network': (),
}

# Class-level rules where a class's real degrees of freedom differ from its
# family's (looked up by class name, then by its registry class).
_CLASS_RULES = {
    'AdiabaticMultiStageVLEColumn': (_Req(1, ('N_stages',), {}),
                                     _Req(1, ('solute',), {}),
                                     _FEED_STAGES, _P_FREE),
    'MultiStageEquilibrium': (_Req(1, ('N_stages', 'stages'), {}),
                              _FEED_STAGES, _P_FREE),
    # single-stage LLE: the split is computed from the thermo
    'MixerSettler': (), 'LLEUnit': (), 'LLECentrifuge': (), 'LLESettler': (),
    'LiquidsSettler': (), 'MagneticSeparator': (), 'PhaseSplitter': (),
    'CO2Compression': (), 'FluidizedCatalyticCracking': (),
    'LiquidsPartitionSettler': (_Req(1, ('partition_coefficients',), {}),
                                _Req(1, ('partition_IDs',), {})),
    'LiquidsRatioCentrifuge': (_Req(1, ('K_chemicals',), {}),
                               _Req(1, ('Ks',), {})),
    'SLLECentrifuge': (_Req(1, ('solids_split',),
                            {'solids_split': ('all_ports_state',)}),),
    'SolidLiquidsSplitCentrifuge': (
        _Req(1, ('solids_split',), {'solids_split': ('all_ports_state',)}),
        _Req(1, ('aqueous_split',), {'aqueous_split': ('all_ports_state',)})),
    'VentScrubber': (_Req(1, ('gas',), {'gas': ('all_ports_state',)}),),
    'AmineAbsorption': (_Req(1, ('CO2_recovery',), {}),),
    'SingleComponentAdsorptionColumn': (
        _Req(1, ('cycle_time',), {}), _Req(1, ('adsorbate',), {}),
        _Req(1, ('isotherm_model',), {}), _Req(1, ('isotherm_args',), {})),
    # GasFedBioreactor: gas_substrates is a required argument; the dissolved
    # gas target theta (fraction of saturation) defaults to 0.5 in the
    # constructor, so it must be stated (R2.8).
    'GasFedBioreactor': (_T_REQ, _TAU, _V_WF,
                         _Req(1, ('gas_substrates',), {}),
                         _Req(1, ('theta',), {})),
    'SplitFlash': (_SPLIT, _T_OR_Q, _P_REQ),
    'SinglePhaseStage': (_T_OR_Q,),
    'ReactivePhaseStage': (_T_OR_Q,),
    # StageEquilibrium: phases is required; one stage specification (T, Q/Duty,
    # B/Boilup, Reflux or F/Flow) passed as a keyword, adiabatic (Q = 0) by
    # constructor default, so it must be stated (R2.8). PhasePartition is its
    # internal partition: phases only.
    'StageEquilibrium': (
        _Req(1, ('phases',), {}),
        _Req(1, ('T', 'Temperature', 'Q', 'Duty', 'B', 'Boilup', 'Reflux', 'F',
                 'Flow'), {'T': ('outlet_T',)},
             guard={'T': 'T'})),
    'PhasePartition': (_Req(1, ('phases',), {}),),
    'MultistageCompressor': (
        _Req(1, ('pr', 'compressors'), {'pr': ('outlet_P',)}),
        _Req(1, ('n_stages', 'compressors'), {}),
        _Req(1, ('eta', 'compressors'), {})),
    # SteamMixer: P required (it picks the steam agent); T optional.
    'SteamMixer': (_Req(1, ('P',), {'P': ('outlet_P',)}),),
    'SprayDryer': (_Req(1, ('moisture_content',),
                        {'moisture_content': ('all_ports_state',)}),),
    # corn Liquefaction: a MixTank with a class-fixed reaction; T is the
    # mixed-inlet temperature, not a specification.
    'Liquefaction': (_TAU, _V_WF),
    # biosteam.wastewater: sized by loading rates, not tau / V_wf. Their
    # reactor type, loading rates, working volume, sludge split and
    # evaporation all default in the constructor (_init), so they must be
    # stated (R2.8).
    'AnMBR': (_Req(1, ('T',), {'T': ('outlet_T',)}),
              _Req(1, ('reactor_type',), {})),
    'PolishingFilter': (_Req(1, ('T',), {'T': ('outlet_T',)}),
                        _Req(1, ('OLR', 'HLR'), {})),
    'InternalCirculationRx': (
        _Req(1, ('T',), {'T': ('outlet_T',)}),
        _Req(1, ('OLRall',), {}),
        _Req(1, ('V_wf',), {})),
    'AnaerobicDigestion': (_Req(1, ('sludge_split',), {}),),
    'AerobicDigestion': (_Req(1, ('evaporation',), {}),),
}


def _unit_rules(sc, position):
    """Essential requirements of unit `position`: class-level rules (by class,
    then registry class), else its family's; None when the family is
    unknown."""
    name, family, _conf = sc.unit_classes[position]
    for key in (name, sc.unit_registry_classes[position]):
        if key in _CLASS_RULES:
            return _CLASS_RULES[key]
    return _FAMILY_RULES.get(family)


def _spec_population(sc):
    """Positions of units that need operating specifications (CMP-UOP-02;
    CMP-UOP-05 adds the CMP-UOP-06 facilities): unknown family, or a
    non-empty rule set (facility families have none: R2.5)."""
    return [p for p in range(len(sc.units)) if _unit_rules(sc, p) != ()]


def _explicit_keys(unit):
    """design_input_specs keys stated with a value plausible for their role
    (_valid_spec; R2.0: an implausible value counts as unstated)."""
    specs = _as_dict(_as_dict(unit).get('design_input_specs'))
    return {k for k, v in specs.items() if _valid_spec(k, v)}


def _stream_value(stream, part):
    """Valid temperature ('T', in (0, T_MAX) K) or pressure ('P', > 0) of a
    stream, or None."""
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    if part == 'T':
        return _valid_T(sp.get('temperature'))
    return _valid_P(sp.get('pressure'))


def _phase_set(stream):
    """Phases carrying flow in a stream (per-phase shape, or legacy flat
    composition entries tagged with a phase); a zero-flow stream -> empty
    set; None when the stream has no phase data."""
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    total = _total_flow(sp)
    if total is not None and abs(total) <= ZERO_FLOW:
        return frozenset()
    phases = sp.get('phases')
    if isinstance(phases, dict) and phases:
        found = set()
        for symbol, phase in phases.items():
            phase = _as_dict(phase)
            flow = _total_flow(phase)
            if (flow is None and _as_list(phase.get('composition'))) \
                    or (flow is not None and abs(flow) > ZERO_FLOW):
                found.add(symbol)
        return frozenset(found)
    entries = _dicts(sp.get('composition'))
    if entries and all(_nonempty_str(e.get('phase')) for e in entries):
        return frozenset(e['phase'] for e in entries
                         if (_entry_fraction(e) or 0.0) > 0.0)
    return None


def _phase_change(sc, unit_id):
    """True/False: the phases present over a unit's outlets differ from those
    over its inlets; None when a port lacks phase data or a side is empty."""
    sides = []
    for index in (sc.unit_inlets, sc.unit_outlets):
        streams = index.get(unit_id, []) if isinstance(unit_id, str) else []
        sets = [_phase_set(sc.streams[i]) for i in streams]
        if not sets or any(s is None for s in sets):
            return None
        sides.append(frozenset().union(*sets))
    return sides[0] != sides[1]


def _pressure_rise(sc, unit_id):
    """min outlet P - max inlet P of a unit, or None when a port states no
    pressure or a side is empty."""
    values = []
    for index in (sc.unit_inlets, sc.unit_outlets):
        streams = index.get(unit_id, []) if isinstance(unit_id, str) else []
        ps = [_stream_value(sc.streams[i], 'P') for i in streams]
        if not ps or any(p is None for p in ps):
            return None
        values.append(ps)
    return min(values[1]) - max(values[0])


def _physical_unit(sc, position):
    """True when unit `position` is in a physical-only family
    (PHYSICAL_FAMILIES) and declares no well-formed reaction: its species
    must then balance one by one (R2.2)."""
    return (sc.unit_classes[position][1] in PHYSICAL_FAMILIES
            and not sc.unit_reacts[position])


def _closure(sc, position):
    """(closes, reason) of unit `position`'s balances (R2.2): the total
    mass flow in and out (every connected stream stating a valid
    total_mass_flow) agree within CLOSURE_RTOL, and, for a physical unit
    (_physical_unit), so does every species' molar flow (relative to the
    total molar throughput), and the R2.4 species-appearance guard finds it
    consistent (_species_consistency). reason says why not ('' when it
    closes). Cached per context."""
    cache = sc.closures
    if position in cache:
        return cache[position]
    uid = sc.units[position].get('id')
    ins = sc.unit_inlets.get(uid, []) if isinstance(uid, str) else []
    outs = sc.unit_outlets.get(uid, []) if isinstance(uid, str) else []
    result = True, ''
    masses = [_valid_flow(_as_dict(sc.streams[i].get('stream_properties'))
                          .get('total_mass_flow')) for i in ins + outs]
    if not ins + outs:
        result = False, 'no connected streams'
    elif any(m is None for m in masses):
        result = False, 'a connected stream states no valid total_mass_flow'
    else:
        m_in, m_out = sum(masses[:len(ins)]), sum(masses[len(ins):])
        if abs(m_in - m_out) > CLOSURE_RTOL * max(m_in, m_out):
            result = False, (f'mass balance does not close (in {m_in:.6g}, '
                             f'out {m_out:.6g})')
        elif _physical_unit(sc, position):
            sides = []
            for streams in (ins, outs):
                acc = {}
                for i in streams:
                    flows = _species_flows(sc.streams[i])
                    if flows is None:
                        result = False, ('species flows not computable for a '
                                         'physical unit')
                        break
                    for name, value in flows.items():
                        acc[name] = acc.get(name, 0.0) + value
                sides.append(acc)
            if result[0]:
                total = max(sum(sides[0].values()), sum(sides[1].values()))
                off = sorted(n for n in set(sides[0]) | set(sides[1])
                             if abs(sides[0].get(n, 0.0) - sides[1].get(n, 0.0))
                             > CLOSURE_RTOL * total)
                if off:
                    result = False, (f"species balance does not close for "
                                     f"{', '.join(off[:3])}")
        if result[0]:  # R2.4: species evidence blocks derivations too
            result = _species_consistency(sc, position)
    cache[position] = result
    return result


# Derivation conditions that read stream data (R2.2: need _closure).
_STREAM_CONDITIONS = frozenset({'outlet_T', 'outlet_P', 'all_ports_state',
                                'enthalpy', 'outlet_phases'})


def _registry_params(sc, position):
    """Constructor parameters of unit `position`'s named class: the keys of
    its declared design-spec registry entry (_declared_registry_class: the
    class itself, else its curated registry ancestor), or None when the
    class is unknown or the registry lists neither."""
    name = sc.unit_classes[position][0]
    declared = _declared_registry_class(name, sc.registry)
    if not declared:
        return None
    return set(_as_dict(_as_dict(sc.registry.get(declared))
                        .get('design_input_spec_params')))


def _class_attribute_default(sc, position, key):
    """True when `key` of unit `position` is a class-level attribute default
    (R2.8): the class is concrete and identified at confidence 1, and `key`
    is NOT one of its constructor parameters (_registry_params; an
    unlisted class gives no such evidence). A constructor argument with a
    default must be stated."""
    name, _family, confidence = sc.unit_classes[position]
    params = _registry_params(sc, position)
    return (bool(name) and confidence == 1.0 and params is not None
            and key is not None and key not in params)


def _condition_holds(condition, sc, position, explicit, key=None):
    """Evaluate one derivation / counts_if condition for unit `position`
    (see _Req); `key` is the specification the condition derives (needed by
    'class_default'). A condition reading stream data (_STREAM_CONDITIONS)
    also needs the unit's balances to close (_closure)."""
    unit = sc.units[position]
    uid = unit.get('id')
    if condition in _STREAM_CONDITIONS and not _closure(sc, position)[0]:
        return False
    if condition == 'outlet_T':
        return sc.all_outlets_have(uid, 'T')
    if condition == 'outlet_P':
        return sc.all_outlets_have(uid, 'P')
    if condition == 'all_ports_state':
        return sc.all_ports_full(uid)
    if condition == 'enthalpy':
        return sc.all_ports_have_enthalpy(uid)
    if condition == 'outlet_phases':
        return sc.all_outlets_phased(uid)
    if condition == 'class_default':
        return _class_attribute_default(sc, position, key)
    if condition == 'rigorous':
        return _as_dict(unit.get('design_input_specs')).get('rigorous') is True
    if condition.startswith('explicit:'):
        return condition.split(':', 1)[1] in explicit
    if condition.startswith('design_result:'):
        name = condition.split(':', 1)[1]
        return _num(_as_dict(unit.get('design_results')).get(name)) is not None
    if condition == 'served_agent':
        return _serves_agent(sc, position)
    raise ValueError(f'unknown derivation condition {condition!r}')


def _requirement_applies(req, sc, position):
    """Whether a (possibly conditional) requirement applies to unit
    `position` (R2.13): it is waived only when the data affirmatively show
    its condition false -- inlet and outlet phase sets both known and equal
    (phase_change), rigorous = true or no phase change
    (phase_change_nonrigorous), inlet and outlet pressures all known with a
    rise >= 1 Pa (no_pressure_rise), the unit naming a class whose
    registry entry lacks `batch` or is absent (batch_param). An unevaluable
    condition applies it, so deleting data can only add requirements."""
    if req.when is None:
        return True
    unit = sc.units[position]
    uid = unit.get('id')
    if req.when == 'phase_change':
        return _phase_change(sc, uid) is not False
    if req.when == 'phase_change_nonrigorous':
        if _as_dict(unit.get('design_input_specs')).get('rigorous') is True:
            return False
        return _phase_change(sc, uid) is not False
    if req.when == 'no_pressure_rise':
        rise = _pressure_rise(sc, uid)
        return rise is None or rise < 1.0
    if req.when == 'batch_param':
        # Waived only for a class the unit itself names (not one inferred
        # from its spec keys) whose constructor takes no `batch`.
        name = sc.unit_classes[position][0]
        unit_type = unit.get('unit_type')
        stated = (_dsm_class(unit)[0],
                  unit_type.strip() if isinstance(unit_type, str) else None)
        if name is None or name not in stated:
            return True
        params = _registry_params(sc, position)
        return params is not None and 'batch' in params
    raise ValueError(f'unknown requirement condition {req.when!r}')


def _counted_keys(req, sc, position, explicit):
    """Explicit keys of `req` that count (counts_if conditions hold)."""
    gates = req.counts_if or {}
    return {k for k in req.keys if k in explicit
            and (k not in gates
                 or _condition_holds(gates[k], sc, position, explicit, k))}


def _main_outlet(streams):
    """[the stream with the largest numeric total flow] (first on ties;
    streams without a flow rank last), or [] for no streams."""
    if not streams:
        return []
    flow = lambda s: _total_flow(_as_dict(s.get('stream_properties')))
    return [max(streams, key=lambda s: (flow(s) is not None,
                                        abs(flow(s) or 0.0)))]


def _inconsistent_keys(req, sc, position, explicit):
    """Guarded explicit keys whose value disagrees with a non-empty outlet
    stream's T (> GUARD_T_ABS K) or P (> GUARD_P_RTOL relative); a 'T_main'
    guard checks the largest-flow outlet only."""
    if not req.guard:
        return []
    unit = sc.units[position]
    specs = _as_dict(unit.get('design_input_specs'))
    uid = unit.get('id')
    outs = [sc.streams[i] for i in
            (sc.unit_outlets.get(uid, []) if isinstance(uid, str) else [])
            if not _is_empty_stream(sc.streams[i])]
    bad = []
    for key, part in req.guard.items():
        value = _num(specs.get(key)) if key in explicit else None
        if value is None or (part == 'P' and value <= 0):
            continue
        streams = outs
        if part == 'T_main':
            streams, part = _main_outlet(outs), 'T'
        for stream in streams:
            actual = _stream_value(stream, part)
            if actual is None:
                continue
            off = abs(actual - value)
            if off > (GUARD_T_ABS if part == 'T' else GUARD_P_RTOL * abs(value)):
                bad.append(key)
                break
    return bad


def _requirement_score(req, sc, position, explicit):
    """0 when a guarded explicit key is inconsistent with the outlets; else
    explicit keys earn 1 each, derivable keys DERIVED_CREDIT, up to n:
    (min(e, n) + DERIVED_CREDIT * min(d, n - min(e, n))) / n."""
    if _inconsistent_keys(req, sc, position, explicit):
        return 0.0
    counted = _counted_keys(req, sc, position, explicit)
    e = min(len(counted), req.n)
    d = sum(1 for key, conditions in req.derive.items()
            if key not in counted
            and all(_condition_holds(c, sc, position, explicit, key)
                    for c in conditions))
    return (e + DERIVED_CREDIT * min(d, req.n - e)) / req.n


def _requirement_locator(label, req, score, inconsistent=(), blocked=''):
    """Missing locator of one unmet requirement; `blocked` names why a
    derivation from stream data was unavailable (_closure)."""
    count = 'one' if req.n == 1 else str(req.n)
    if inconsistent:
        note = (f" (inconsistent: {', '.join(inconsistent)} disagrees with "
                f"the outlet stream)")
    elif score > 0:
        note = ' (only derivable)'
    else:
        note = ''
    if blocked:
        note += f' (derivation from stream data unavailable: {blocked})'
    return f"{label}.design_input_specs: {count} of {'|'.join(req.keys)}{note}"


def _unit_spec_score(sc, position):
    """(score, missing) of one unit against its essential rules; an unknown
    family scores 0 (no way to know what to specify); (None, []) when no
    requirement applies."""
    unit, label = sc.units[position], sc.unit_labels[position]
    rules = _unit_rules(sc, position)
    if rules is None:
        return 0.0, [f'{label}: unit family unknown, so its essential '
                     f'specifications cannot be determined']
    explicit = _explicit_keys(unit)
    scores, missing = [], []
    for req in rules:
        if not _requirement_applies(req, sc, position):
            continue
        score = _requirement_score(req, sc, position, explicit)
        scores.append(score)
        if score < 1.0:
            bad = _inconsistent_keys(req, sc, position, explicit)
            closes, why = _closure(sc, position)
            streamed = any(set(conditions) & _STREAM_CONDITIONS
                           for conditions in req.derive.values())
            missing.append(_requirement_locator(
                label, req, score, bad,
                blocked='' if closes or not streamed else why))
    return _mean(scores), missing


def _excused_params(sc, position, explicit):
    """Registry params excused for CMP-UOP-03: the unstated alternatives of
    every essential requirement of unit `position` already satisfied by
    explicit keys that count (an HXutility with T set excuses V and H; a
    non-rigorous V alone excuses nothing)."""
    excused = set()
    for req in _unit_rules(sc, position) or ():
        if len(_counted_keys(req, sc, position, explicit)) >= req.n:
            excused.update(k for k in req.keys if k not in explicit)
    return excused

#%% Scoring context

def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def _dicts(x):
    return [e for e in _as_list(x) if isinstance(e, dict)]


def _first_by_id(elements):
    """`elements` without every later element whose non-empty string id an
    earlier one already carries (R2.1); elements without such an id are all
    kept."""
    kept, seen = [], set()
    for element in elements:
        eid = element.get('id')
        if _nonempty_str(eid):
            if eid in seen:
                continue
            seen.add(eid)
        kept.append(element)
    return kept


def _topology(streams, unit_ids):
    """Classify streams and index unit ports. Returns a dict with lists of
    stream positions -- feeds (boundary source, real sink), outlets (real
    source, boundary sink), isolated (both boundary), internal -- and
    inlets/outlets: {unit_id: [stream positions]}."""
    topo = {'feeds': [], 'outlets': [], 'isolated': [], 'internal': [],
            'inlets': {uid: [] for uid in unit_ids},
            'unit_outlets': {uid: [] for uid in unit_ids}}
    for i, stream in enumerate(streams):
        source, sink = stream.get('source_unit_id'), stream.get('sink_unit_id')
        source_b, sink_b = _is_boundary(source), _is_boundary(sink)
        kind = ('isolated' if source_b and sink_b else 'feeds' if source_b
                else 'outlets' if sink_b else 'internal')
        topo[kind].append(i)
        if not sink_b and isinstance(sink, str):
            topo['inlets'].setdefault(sink, []).append(i)
        if not source_b and isinstance(source, str):
            topo['unit_outlets'].setdefault(source, []).append(i)
    return topo


class _ScoringContext:
    """Read-only indexes over one SFF document, built once and shared by every
    criterion (the scorer's analogue of the validator's _Context). Tolerates
    missing/malformed sections: every list holds only dict elements and every
    accessor returns empty rather than raising."""

    def __init__(self, doc, results=(), registry=None):
        self.doc = doc if isinstance(doc, dict) else {}
        self.metadata = _as_dict(self.doc.get('metadata'))
        # R2.1: elements sharing an id are collapsed to the first occurrence
        # (UNIT-01 / STR-01 flag the defect); no criterion is inflated by
        # repetition.
        self.units = _first_by_id(_dicts(self.doc.get('units')))
        self.streams = _first_by_id(_dicts(self.doc.get('streams')))
        self.chemicals = _first_by_id(_dicts(self.doc.get('chemicals')))
        self.results = list(results)
        if registry is None:
            self.registry, self.registry_error = _design_spec_registry()
        else:
            self.registry, self.registry_error = registry, None

        self.unit_labels = [_label('units', u, i) for i, u in enumerate(self.units)]
        self.stream_labels = [_label('streams', s, i)
                              for i, s in enumerate(self.streams)]
        self.chem_labels = [_label('chemicals', c, i)
                            for i, c in enumerate(self.chemicals)]
        self.unit_ids = {u['id'] for u in self.units if _nonempty_str(u.get('id'))}
        self.chem_ids = frozenset(c['id'] for c in self.chemicals
                                  if _nonempty_str(c.get('id')))
        self.chem_by_index = {c['index']: c.get('id') for c in self.chemicals
                              if isinstance(c.get('index'), int)
                              and not isinstance(c.get('index'), bool)}

        self.stream_states = [_stream_state(s) for s in self.streams]
        topo = _topology(self.streams, self.unit_ids)
        self.feeds, self.outlets = topo['feeds'], topo['outlets']
        self.isolated, self.internal = topo['isolated'], topo['internal']
        self.unit_inlets, self.unit_outlets = topo['inlets'], topo['unit_outlets']

        # Unit class identity: (class, family, confidence) per unit, whether
        # the class is core BioSTEAM, and its design-spec registry class.
        pinned = _pinned(_as_dict(self.metadata.get('reproducibility'))
                         .get('flowsheet_model_package'))
        self.unit_classes = [_unit_class(u, self.registry, pinned)
                             for u in self.units]
        # The URL counts only when design_simulation_method named the class
        # the unit resolved to (not when an abstract one fell through to it).
        self.unit_core = [
            _is_core_class(name, _dsm_class(u)[1]
                           if _dsm_class(u)[0] == name else None)
            for u, (name, _f, _c) in zip(self.units, self.unit_classes)]
        self.unit_registry_classes = [
            _registry_class(name, family, u, self.registry)
            for u, (name, family, _c) in zip(self.units, self.unit_classes)]
        self.reactions = [(i, j, r) for i, u in enumerate(self.units)
                          for j, r in enumerate(_as_list(u.get('reactions')))
                          if isinstance(r, dict)]
        # Per unit: declares >= 1 well-formed reaction (R2.0).
        self.unit_reacts = [any(self.well_formed(r)
                                for r in _dicts(u.get('reactions')))
                            for u in self.units]
        self.closures = {}  # _closure cache: position -> (closes, reason)
        self.consistency = {}  # _species_consistency cache (R2.4)
        self.unit_positions = {}
        for i, u in enumerate(self.units):
            if _nonempty_str(u.get('id')):
                self.unit_positions.setdefault(u['id'], i)
        # Costed population (R1.8): units whose class/family carries a
        # purchase cost by default.
        self.costed = [i for i, (name, family, _c) in enumerate(self.unit_classes)
                       if not _is_cost_free(name, family)]

        utilities = _as_dict(self.doc.get('utilities'))
        agents, seen = [], set()
        for group in ('heat_utilities', 'power_utilities', 'other_utilities'):
            for i, agent in enumerate(_dicts(utilities.get(group))):
                aid = agent.get('id')
                if _nonempty_str(aid):
                    if aid in seen:  # R2.1: first occurrence across groups
                        continue
                    seen.add(aid)
                agents.append((group, agent,
                               _label(f'utilities.{group}', agent, i)))
        self.agents = agents
        self._vctx = None

    def well_formed(self, reaction):
        """_well_formed against this document's chemical ids and indices."""
        return _well_formed(reaction, self.chem_ids, self.chem_by_index)

    def reaction_sides(self, reaction):
        """_reaction_sides against this document's chemical ids and
        indices."""
        return _reaction_sides(reaction, self.chem_ids, self.chem_by_index)

    @property
    def vctx(self):
        """The validator's _Context over the same document (molar masses,
        stoichiometry resolution), built on first use."""
        if self._vctx is None:
            self._vctx = _validator()._Context(self.doc)
        return self._vctx

    def check_result(self, check_id):
        """The first validator CheckResult with this id, or None."""
        for result in self.results:
            if getattr(result, 'check_id', None) == check_id:
                return result
        return None

    def all_outlets_have(self, unit_id, part):
        """Unit has >=1 outlet stream and each states `part` (T/P/flow/...)."""
        outs = self.unit_outlets.get(unit_id, []) if isinstance(unit_id, str) else []
        return bool(outs) and all(self.stream_states[i][part] for i in outs)

    def all_ports_full(self, unit_id):
        """Unit has >=1 connected stream and each has a full state (T, P, flow
        and composition)."""
        if not isinstance(unit_id, str):
            return False
        ports = self.unit_inlets.get(unit_id, []) + self.unit_outlets.get(unit_id, [])
        return bool(ports) and all(all(self.stream_states[i].values())
                                   for i in ports)

    def all_ports_have_enthalpy(self, unit_id):
        """Unit has >=1 connected stream and each states a numeric
        stream_properties.enthalpy_flow."""
        if not isinstance(unit_id, str):
            return False
        ports = self.unit_inlets.get(unit_id, []) + self.unit_outlets.get(unit_id, [])
        return bool(ports) and all(
            _num(_as_dict(self.streams[i].get('stream_properties'))
                 .get('enthalpy_flow')) is not None for i in ports)

    def all_outlets_phased(self, unit_id):
        """Unit has >=1 outlet stream and each carries phase data."""
        outs = self.unit_outlets.get(unit_id, []) if isinstance(unit_id, str) else []
        return bool(outs) and all(_phase_set(self.streams[i]) is not None
                                  for i in outs)

#%% Criteria -- thermo

def _chemical_references(sc):
    """{chemical name: first locator referencing it} over stream and utility
    compositions and reaction reactants, equations and stoichiometries."""
    refs = {}

    def add(name, where):
        if _nonempty_str(name):
            refs.setdefault(name, where)

    for i, stream in enumerate(sc.streams):
        for _phase, entry in _phase_entries(stream):
            add(entry.get('component_name'), sc.stream_labels[i])
    for _group, agent, label in sc.agents:
        for entry in _dicts(agent.get('composition')):
            add(entry.get('component_name'), label)
    for ui, ri, reaction in sc.reactions:
        where = f'{sc.unit_labels[ui]}.reactions[{ri}]'
        add(reaction.get('reactant'), where)
        for name in _equation_species(reaction.get('equation'), sc.chem_ids):
            add(name, where)
        for name in _stoichiometry_species(reaction.get('stoichiometry'),
                                           sc.chem_ids, sc.chem_by_index):
            add(name, where)
    return refs


def _chemical_phases(sc):
    """{chemical name: set of phase symbols it occurs in with a positive
    fraction} across all stream compositions."""
    phases = {}
    for stream in sc.streams:
        for symbol, entry in _phase_entries(stream):
            name, x = entry.get('component_name'), _entry_fraction(entry)
            if _nonempty_str(name) and _nonempty_str(symbol) and x and x > 0:
                phases.setdefault(name, set()).add(symbol)
    return phases


def _score_thm_01(sc):  # CMP-THM-01
    refs = _chemical_references(sc)
    if not refs:
        if sc.chem_ids:
            return 1.0, f'{len(sc.chem_ids)} chemical(s) declared, none referenced', []
        return 0.0, 'no chemicals declared or referenced', ['chemicals: empty']
    undeclared = sorted(n for n in refs if n not in sc.chem_ids)
    covered = len(refs) - len(undeclared)
    missing = [f"chemicals: no entry for '{n}' (referenced by {refs[n]})"
               for n in undeclared]
    return (covered / len(refs),
            f'{covered} of {len(refs)} referenced chemical(s) declared', missing)


def _chemical_population(sc):
    """[(chemical dict or None, label, id or None)]: the CMP-THM-02..05
    population (R1.1) -- every declared chemical, then every referenced but
    undeclared id (chemical None; it scores 0 on every per-chemical
    criterion)."""
    population = [(chem, label, chem['id'] if _nonempty_str(chem.get('id'))
                   else None)
                  for chem, label in zip(sc.chemicals, sc.chem_labels)]
    population += [(None, f"chemicals['{name}'] (undeclared; referenced by "
                          f"{where})", name)
                   for name, where in _chemical_references(sc).items()
                   if name not in sc.chem_ids]
    return population


def _empty_chemical_population(sc):
    """(score, message, missing) of a CMP-THM-02..05 criterion whose
    population is empty: 0 when the flowsheet has streams or units (they
    need chemicals), else not applicable."""
    if sc.streams or sc.units:
        return (0.0, 'no chemicals declared or referenced although the '
                'flowsheet has streams/units', ['chemicals: empty'])
    return None, 'no chemicals declared or referenced', []


def _thm_02_credit(chem, cid):
    """(credit, missing suffix or None) of one declared chemical's identity
    (R2.0/R2.3): a valid registry id 1; else an id that resolves by name
    (never a formula with >= 2 carbon atoms) NAME_RESOLVED_CREDIT when
    the record's molar mass or formula matches the chemical's,
    NAME_UNVERIFIED_CREDIT when the chemical states neither; else (no name,
    or a mismatching one) FORMULA_ONLY_CREDIT when a parseable formula (or
    the formula-like id itself) narrows it down; else 0."""
    if _valid_registry_id(chem):
        return 1.0, None
    tier = _name_tier(chem, cid)
    if tier == 'verified':
        return NAME_RESOLVED_CREDIT, ('.registry_id (id resolvable by name '
                                      'only; molar mass verified)')
    if tier == 'unverified':
        return NAME_UNVERIFIED_CREDIT, ('.registry_id (id resolvable by name '
                                        'only; no molar mass or formula to '
                                        'verify the match)')
    why = (' (the name resolves to a compound of another molar mass)'
           if tier == 'mismatch' else
           ' (id is a molecular formula with >= 2 carbon atoms: '
           'isomer-ambiguous)' if _isomer_ambiguous(cid) else '')
    if _valid_formula(chem.get('formula')) or _formula_like(cid):
        return FORMULA_ONLY_CREDIT, f'.registry_id (formula only){why}'
    return 0.0, f'.registry_id (no formula either; id not resolvable by name){why}'


def _score_thm_02(sc):  # CMP-THM-02
    population = _chemical_population(sc)
    if not population:
        return _empty_chemical_population(sc)
    scores, missing = [], []
    for chem, label, cid in population:
        if chem is None:
            scores.append(0.0)
            missing.append(f'{label}: not declared')
            continue
        credit, note = _thm_02_credit(chem, cid)
        scores.append(credit)
        if note:
            missing.append(label + note)
    n_id = scores.count(1.0)
    n_name = scores.count(NAME_RESOLVED_CREDIT)
    n_unverified = scores.count(NAME_UNVERIFIED_CREDIT)
    return (_mean(scores), f'{n_id} of {len(scores)} chemical(s) carry a '
            f'valid registry id, {n_name} more resolve by a verified name, '
            f'{n_unverified} by an unverified one', missing)


def _score_thm_03(sc):  # CMP-THM-03
    population = _chemical_population(sc)
    if not population:
        return _empty_chemical_population(sc)
    derive = _validator()._molar_mass_from_formula  # same rule as _Context.molar_mass
    ok, missing = 0, []
    for chem, label, _cid in population:
        if chem is None:
            missing.append(f'{label}: not declared')
            continue
        declared = _num(chem.get('molar_mass'))
        formula = chem.get('formula')
        if (declared is not None and declared > 0) or (
                _nonempty_str(formula) and derive(formula) is not None):
            ok += 1
        else:
            missing.append(f'{label}.molar_mass (or a parseable formula)')
    return (ok / len(population),
            f'{ok} of {len(population)} chemical(s) have a molar mass', missing)


def _score_thm_04(sc):  # CMP-THM-04
    population = _chemical_population(sc)
    if not population:
        return _empty_chemical_population(sc)
    phases = _chemical_phases(sc)
    derived, missing = 0, []
    for chem, label, cid in population:
        if chem is None:
            missing.append(f'{label}: not declared')
            continue
        seen = phases.get(cid, set()) if cid else set()
        if len(seen) >= 2 or seen == {'s'}:
            derived += 1
        else:
            shown = ','.join(sorted(seen)) or 'none'
            missing.append(f'{label}: phase behaviour (locked state / '
                           f'phase_ref) not stated; stream phases seen: {shown}')
    return (DERIVED_CREDIT * derived / len(population),
            f'not representable; derivable from stream data for {derived} of '
            f'{len(population)} chemical(s)', missing)


def _score_thm_05(sc):  # CMP-THM-05
    # Population = declared + referenced ids (R2.3): a declared, unreferenced
    # chemical still needs property data (Chemicals.compile() requires key
    # properties for every member). A chemical is database-resolvable by a
    # valid registry id or a verified/unverified name (not a mismatching or
    # isomer-ambiguous one); an undeclared referenced id is judged by its
    # name.
    population = _chemical_population(sc)
    if not population:
        return _empty_chemical_population(sc)
    lacking = [label for chem, label, cid in population
               if not ((chem is not None and _valid_registry_id(chem))
                       or _name_tier(chem, cid) in ('verified', 'unverified'))]
    if not lacking:
        return (None, 'every chemical resolves against the database (registry '
                'id or name)', [])
    missing = [f'{label}: property data (V, Cn, H/S reference, Hf, '
               f'Psat/Tb/Hvap, LHV) not representable' for label in lacking]
    return (0.0, f'{len(lacking)} chemical(s) resolvable neither by registry '
            f'id nor by name need property data no SFF field can state',
            missing)


_TPP_KEYS = ('mixture', 'gamma', 'phi', 'PCF')


def _score_thm_06(sc):  # CMP-THM-06
    if not sc.units:
        return None, 'no units', []
    # A descriptor counts only when it names a thermosteam model class of its
    # role (R2.0; _valid_package_descriptor).
    pairs, missing = [], []
    for position, (unit, label) in enumerate(zip(sc.units, sc.unit_labels)):
        tpp = _as_dict(unit.get('thermo_property_package'))
        absent = [k for k in _TPP_KEYS
                  if not _valid_package_descriptor(k, tpp.get(k))]
        pairs.append((1.0 - len(absent) / len(_TPP_KEYS),
                      _unit_weight(sc, position)))
        missing.extend(f'{label}.thermo_property_package.{k}' for k in absent)
    full = sum(1 for score, _w in pairs if score == 1.0)
    return (_wmean(pairs), f'{full} of {len(pairs)} unit(s) state a full '
            f'property package (family-weighted)', missing)


def _single_full_package(packages):
    """True when every package states all of _TPP_KEYS validly
    (_valid_package_descriptor) and all of them agree on those descriptors:
    single-thermo evidence, so one chemical set is implied even where no
    package names it (CMP-THM-07)."""
    return bool(packages) and all(
        all(_valid_package_descriptor(k, p.get(k)) for k in _TPP_KEYS)
        for p in packages) and len(
        {tuple(str(p.get(k)) for k in _TPP_KEYS) for p in packages}) == 1


def _score_thm_07(sc):  # CMP-THM-07
    # Applicable whenever units exist (R1.0): whether the flowsheet is
    # multi-thermo is itself data a deletion could remove, so it only decides
    # the derived credit, never the population.
    if not sc.units:
        return None, 'no units', []
    packages = [_as_dict(u.get('thermo_property_package')) for u in sc.units]
    single = _single_full_package(packages)
    pairs, missing = [], []
    for position, (package, label) in enumerate(zip(packages, sc.unit_labels)):
        if _nonempty_str(package.get('id')):
            credit = 1.0
        else:
            credit = DERIVED_CREDIT if single else 0.0
            missing.append(f'{label}.thermo_property_package.id'
                           + (' (one full package shared by every unit: '
                              'a single chemical set is implied)'
                              if single else ''))
        pairs.append((credit, _unit_weight(sc, position)))
    named = sum(1 for p in packages if _nonempty_str(p.get('id')))
    return (_wmean(pairs), f'{named} of {len(packages)} unit(s) name their '
            f'chemical set' + ('; every unit shares one full property package '
                               '(single thermo implied)' if single else '')
            + ' (family-weighted)', missing)

#%% Criteria -- topology

def _score_top_01(sc):  # CMP-TOP-01
    if not sc.units and not sc.streams:
        return 0.0, 'no units and no streams', ['units: empty', 'streams: empty']
    resolved, total, missing = 0, 0, []
    for stream, label in zip(sc.streams, sc.stream_labels):
        for key in ('source_unit_id', 'sink_unit_id'):
            endpoint = stream.get(key)
            total += 1
            if _is_boundary(endpoint) or (isinstance(endpoint, str)
                                          and endpoint in sc.unit_ids):
                resolved += 1
            else:
                missing.append(f'{label}.{key}: {endpoint!r} is not a unit id')
    attached = 0
    for unit, label in zip(sc.units, sc.unit_labels):
        uid = unit.get('id')
        if isinstance(uid, str) and (sc.unit_inlets.get(uid) or sc.unit_outlets.get(uid)):
            attached += 1
        else:
            missing.append(f'{label}: attached to no stream')
    endpoint_frac = resolved / total if total else 0.0
    unit_frac = attached / len(sc.units) if sc.units else 0.0
    return ((endpoint_frac + unit_frac) / 2,
            f'{resolved} of {total} stream endpoint(s) resolve; {attached} of '
            f'{len(sc.units)} unit(s) attached', missing)


# Port-order conventions (Revision 1, R1.11), per (family, side). Order-free
# sides never need an order; single-port sides hold one stream by convention.
_ORDER_FREE_SIDES = frozenset({('mixer', 'ins'), ('tank', 'ins'),
                               ('junction', 'ins'), ('junction', 'outs')})
_SINGLE_PORT_SIDES = frozenset({
    ('pump', 'ins'), ('pump', 'outs'), ('valve', 'ins'), ('valve', 'outs'),
    ('hx_utility', 'ins'), ('hx_utility', 'outs'),
    ('compressor', 'ins'), ('compressor', 'outs'),
    ('turbine', 'ins'), ('turbine', 'outs'), ('tank', 'outs'),
    ('mixer', 'outs'), ('splitter', 'ins'), ('flash', 'ins'),
    ('steam_injection_heater', 'outs')})


def _side_streams(sc, position, side):
    """Stream positions on one side ('ins'/'outs') of unit `position`."""
    uid = sc.units[position].get('id')
    index = sc.unit_inlets if side == 'ins' else sc.unit_outlets
    return index.get(uid, []) if isinstance(uid, str) else []


def _port_order_derivable(sc, position, side):
    """Whether the order of a multi-stream side follows from the data: flash
    outs (exactly one all-vapor and one all-liquid outlet), splitter outs
    (explicit split + full port states), distillation outs (explicit LHK +
    outlet compositions), HXprocess sides (all four streams carry
    composition). Every one reads stream data, so the unit's balances must
    close (R2.2, _closure)."""
    if not _closure(sc, position)[0]:
        return False
    family = sc.unit_classes[position][1]
    explicit = _explicit_keys(sc.units[position])
    streams = _side_streams(sc, position, side)
    if family == 'flash' and side == 'outs':
        sets = [_phase_set(sc.streams[i]) for i in streams]
        vapor = sum(1 for s in sets if s == frozenset({'g'}))
        liquid = sum(1 for s in sets if s and s <= {'l', 'L'})
        return len(streams) == 2 and vapor == 1 and liquid == 1
    if family == 'splitter' and side == 'outs':
        return 'split' in explicit and sc.all_ports_full(sc.units[position].get('id'))
    if family == 'distillation' and side == 'outs':
        return 'LHK' in explicit and all(sc.stream_states[i]['composition']
                                         for i in streams)
    if family == 'hx_process':
        ports = _side_streams(sc, position, 'ins') + _side_streams(sc, position, 'outs')
        return len(ports) == 4 and all(sc.stream_states[i]['composition']
                                       for i in ports)
    return False


def _side_credit(sc, position, side):
    """(credit, missing locator or None) for the port order of one unit side:
    order-free -> 1; <=1 stream -> 1 on a single-port side, else
    DERIVED_CREDIT (unexported ports may exist); several streams ->
    DERIVED_CREDIT when derivable, else 0."""
    family, label = sc.unit_classes[position][1], sc.unit_labels[position]
    n = len(_side_streams(sc, position, side))
    if (family, side) in _ORDER_FREE_SIDES:
        return 1.0, None
    if n <= 1:
        if (family, side) in _SINGLE_PORT_SIDES:
            return 1.0, None
        return DERIVED_CREDIT, (f'{label}.{side}: port index not representable '
                                f'(unexported ports may exist)')
    if _port_order_derivable(sc, position, side):
        return DERIVED_CREDIT, (f'{label}.{side}: order of {n} streams not '
                                f'representable (derivable from stream data)')
    return 0.0, f'{label}.{side}: order of {n} streams not representable'


def _score_top_02(sc):  # CMP-TOP-02
    if not sc.units:
        return None, 'no units', []
    pairs, missing = [], []
    for position in range(len(sc.units)):
        for side in ('ins', 'outs'):
            credit, locator = _side_credit(sc, position, side)
            pairs.append((credit, _unit_weight(sc, position)))
            if locator:
                missing.append(locator)
    ok = sum(1 for credit, _w in pairs if credit == 1.0)
    return (_wmean(pairs), f'not representable; {ok} of {len(pairs)} unit '
            f'port side(s) unambiguous by convention', missing)

#%% Criteria -- feeds

def _state_missing(sc, positions):
    """(mean state score, missing locators) over the given streams."""
    scores, missing = [], []
    for i in positions:
        state = sc.stream_states[i]
        scores.append(sum(state.values()) / len(state))
        missing.extend(f'{sc.stream_labels[i]}.{_STATE_PATHS[k]}'
                       for k, ok in state.items() if not ok)
    return _mean(scores), missing


def _is_empty_stream(stream):
    """True when a stream states a total flow that is numerically zero."""
    total = _total_flow(_as_dict(stream.get('stream_properties')))
    return total is not None and abs(total) <= ZERO_FLOW


def _states_flow(stream):
    """True when a stream states a numeric total flow that is not zero."""
    total = _total_flow(_as_dict(stream.get('stream_properties')))
    return total is not None and abs(total) > ZERO_FLOW


def _score_feed_01(sc):  # CMP-FEED-01
    # Equal weights; every feed counts (R1.0); every part of a feed's state
    # must be plausible (R2.0: T in (0, T_MAX) K, P > 0, flow >= 0, a
    # composition whose fractions sum to 1). A feed whose state says it is
    # EMPTY (total flow numerically 0: an unused placeholder port) scores 1
    # -- the zero flow states all such a port needs -- but ONLY IF some feed
    # states a non-zero flow (R2.12); otherwise nothing sets the problem's
    # scale and a stated zero counts as a missing flow, with no composition
    # exemption. Feeds with unknown flow keep partial credit (T, P,
    # composition). Deleting the only non-zero flow thus lowers the empty
    # feeds from 1 to their missing-flow score; writing zeros everywhere
    # earns nothing. (A mass-share weighting was dropped: deleting a large
    # feed's flow switched it to equal weights.)
    if not sc.feeds:
        return (0.0, 'no feed streams: nothing defines the degrees of freedom',
                ['streams: no feed (source_unit_id "None", sink a unit)'])
    anchored = any(_states_flow(sc.streams[i]) for i in sc.feeds)
    empty = [i for i in sc.feeds
             if anchored and _is_empty_stream(sc.streams[i])]
    scores, missing, full = [], [], 0
    for i in sc.feeds:
        if i in empty:
            scores.append(1.0)
            continue
        state = _stream_state(sc.streams[i], empty_exempt=anchored)
        if not anchored and _is_empty_stream(sc.streams[i]):
            state['flow'] = False
        scores.append(sum(state.values()) / len(state))
        full += all(state.values())
        missing.extend(f'{sc.stream_labels[i]}.{_STATE_PATHS[k]}'
                       + (' (a zero flow, while no feed states a non-zero '
                          'one)' if k == 'flow' and not anchored
                          and _is_empty_stream(sc.streams[i]) else '')
                       for k, ok in state.items() if not ok)
    return (_mean(scores), f'{full + len(empty)} of {len(sc.feeds)} feed(s) '
            f'fully stated' + (f' ({len(empty)} empty, stated by their zero '
                               f'flow)' if empty else '')
            + ('' if anchored else '; no feed states a non-zero flow'),
            missing)


# Chemical ids (lowercase) of each utility-port material (R2.9).
_PORT_MATERIAL_IDS = {
    'natural gas': frozenset({'ch4', 'methane', 'naturalgas', 'natural_gas'}),
    'lime': frozenset({'lime', 'cao', 'ca(oh)2', 'caoh2', 'calciumoxide',
                       'calciumhydroxide', 'calciumdihydroxide'}),
    'boiler chemicals': frozenset({'boilerchems', 'boilerchemicals',
                                   'boiler_chemicals'}),
    'makeup water': frozenset({'water', 'h2o'}),
}
PORT_MATERIAL_MIN = 0.5  # mol fraction making a feed a port's material


def _mole_fractions(stream):
    """{lowercase component id: mol fraction} of a non-empty stream whose
    species flows are computable (_species_flows), else None."""
    flows = _species_flows(stream)
    total = sum(flows.values()) if flows else 0.0
    if not total > 0.0:
        return None
    fractions = {}
    for name, flow in flows.items():
        key = str(name).strip().lower()
        fractions[key] = fractions.get(key, 0.0) + flow / total
    return fractions


def _port_materials(sc, position):
    """Utility-port materials of unit `position` that BioSTEAM prices itself
    when the feed's own price is 0 (R2.9): natural gas for combustors,
    dryers, boilers and boiler-turbogenerators (bst.stream_utility_prices);
    lime and boiler chemicals for boilers (class-default prices); makeup
    water for a process-water center -- or a facility of unknown kind,
    which might be one (bst.stream_utility_prices)."""
    family = sc.unit_classes[position][1]
    if family in ('combustor', 'dryer'):
        return ('natural gas',)
    if family in ('boiler', 'boiler_turbogenerator'):
        return ('natural gas', 'lime', 'boiler chemicals')
    if family == 'facility' and _facility_kind(sc, position) in (
            None, 'ProcessWaterCenter'):
        return ('makeup water',)
    return ()


def _utility_port_feed(sc, position):
    """(sink unit label, material) when feed `position` enters a unit with
    utility-port materials (_port_materials) and its composition does not
    rule them out: a feed whose composition is known (_mole_fractions) is
    the port's material only when >= PORT_MATERIAL_MIN of it is that
    material (natural gas: >= 50 mol% CH4); an unknown composition may be
    (R2.13). Else None: air and other feeds are scored normally."""
    sink = sc.streams[position].get('sink_unit_id')
    unit = sc.unit_positions.get(sink) if isinstance(sink, str) else None
    materials = _port_materials(sc, unit) if unit is not None else ()
    if not materials:
        return None
    fractions = _mole_fractions(sc.streams[position])
    for material in materials:
        if fractions is None or sum(
                x for name, x in fractions.items()
                if name in _PORT_MATERIAL_IDS[material]) >= PORT_MATERIAL_MIN:
            return sc.unit_labels[unit], (material if fractions is not None
                                          else '/'.join(materials) + '?')
    return None


def _score_feed_02(sc):  # CMP-FEED-02
    # Every boundary stream counts (R1.0); an EMPTY one (total flow
    # numerically 0) needs no price and scores 1, so deleting its flow (or
    # its stream_properties) can only lower its credit -- excluding it let
    # that deletion move a priced empty stream into the population.
    positions = sc.feeds + sc.outlets
    if not positions:
        return None, 'no boundary streams', []
    feeds = set(sc.feeds)
    scores, missing = [], []
    empty = 0
    for i in positions:
        if _is_empty_stream(sc.streams[i]):
            empty += 1
            scores.append(1.0)
            continue
        price = _num(sc.streams[i].get('price'))
        port = _utility_port_feed(sc, i) if i in feeds else None
        if price is None:
            scores.append(0.0)
            missing.append(f'{sc.stream_labels[i]}.price')
        elif price == 0 and port:
            # BioSTEAM prices a 0-priced utility-port material itself
            # (bst.stream_utility_prices or a class default), which SFF
            # cannot state.
            scores.append(0.5)
            missing.append(f'{sc.stream_labels[i]}.price: 0 on the {port[1]} '
                           f'port of {port[0]} (priced by BioSTEAM, not '
                           f'representable)')
        else:
            scores.append(1.0)
    ok = scores.count(1.0) - empty
    return (_mean(scores), f'{ok} of {len(positions) - empty} non-empty '
            f'boundary stream(s) priced' + (f' ({empty} empty, needing no '
                                            f'price)' if empty else ''),
            missing)

#%% Criteria -- unit operations

def _class_note(sc, position):
    """Why unit `position` did not identify its class at confidence 1, as a
    missing locator, or None when it did."""
    unit, label = sc.units[position], sc.unit_labels[position]
    name, family, confidence = sc.unit_classes[position]
    if confidence == 1.0:
        return None
    named = _dsm_class(unit)
    if name is None and named[0]:
        why = ('is abstract' if named[0] in _ABSTRACT_CLASSES else
               'is not a concrete core BioSTEAM class, although its URL '
               'claims biosteam')
        return (f"{label}.design_simulation_method: class '{named[0]}' {why}"
                + (f" (family '{family}' only)" if family else
                   ' (no family either)'))
    if confidence == 0.75:
        stated = name in (_dsm_class(unit)[0], unit.get('unit_type'))
        if stated:
            return (f"{label}: custom class '{name}' is not core BioSTEAM and "
                    f"metadata.reproducibility.flowsheet_model_package pins "
                    f"no commit|version")
        return (f"{label}.design_simulation_method: class not stated "
                f"('{name}' inferred from the design_input_specs keys)")
    if confidence == 0.5:
        return (f"{label}.design_simulation_method: class not stated (family "
                f"'{family}' inferred from unit_type / spec keys)")
    return (f"{label}: class unresolved (unit_type {unit.get('unit_type')!r} "
            f"names no known class or family)")


def _score_uop_01(sc):  # CMP-UOP-01
    if not sc.units:
        return None, 'no units', []
    missing = [note for note in (_class_note(sc, p) for p in range(len(sc.units)))
               if note]
    exact = sum(1 for c in sc.unit_classes if c[2] == 1.0)
    pairs = [(c[2], _unit_weight(sc, p)) for p, c in enumerate(sc.unit_classes)]
    return (_wmean(pairs), f'{exact} of {len(sc.units)} unit(s) identify their '
            f'class at confidence 1 (family-weighted)', missing)


def _score_uop_02(sc):  # CMP-UOP-02
    pairs, missing = [], []
    for position in _spec_population(sc):
        score, unit_missing = _unit_spec_score(sc, position)
        if score is None:
            continue
        pairs.append((score, _unit_weight(sc, position)))
        missing.extend(unit_missing)
    if not pairs:
        return None, 'no units that need operating specifications', []
    full = sum(1 for score, _w in pairs if score == 1.0)
    return (_wmean(pairs), f'{full} of {len(pairs)} applicable unit(s) fully '
            f'specified (family-weighted)', missing)


def _score_uop_03(sc):  # CMP-UOP-03
    if sc.registry_error:
        raise RuntimeError(f'design-spec registry unavailable: '
                           f'{sc.registry_error}')
    # Population by need (R1.0): every unit, except one whose class is known
    # exactly but that the registry does not list (a scorer vocabulary gap,
    # not a file gap). A unit whose class is not known exactly (inferred from
    # its spec keys, family only, unknown) scores 0: its parameter universe
    # is unknown -- measuring it against a key-set-inferred entry let deleting
    # design_simulation_method pick a tighter entry and raise the coverage.
    pairs, missing, unknown = [], [], 0
    for position, (unit, label) in enumerate(zip(sc.units, sc.unit_labels)):
        name, _family, confidence = sc.unit_classes[position]
        weight = _unit_weight(sc, position)
        if not (name and confidence == 1.0):
            unknown += 1
            pairs.append((0.0, weight))
            missing.append(f'{label}: class not identified at confidence 1, '
                           f'so its design-spec parameter universe is unknown')
            continue
        registry_class = _declared_registry_class(name, sc.registry)
        entry = _as_dict(sc.registry.get(registry_class)) if registry_class else {}
        params = list(_as_dict(entry.get('design_input_spec_params')))
        if not params:
            continue
        explicit = _explicit_keys(unit)
        excused = _excused_params(sc, position, explicit)
        needed = [p for p in params if p not in excused]
        absent = [p for p in needed if p not in explicit]
        pairs.append((1.0 - len(absent) / len(needed) if needed else 1.0, weight))
        missing.extend(f'{label}.design_input_specs.{p}' for p in absent)
    if not pairs:
        return (None, 'no units, or every unit is a known class the '
                'design-spec registry does not list', [])
    return (_wmean(pairs), f'{len(pairs) - unknown} unit(s) measured against '
            f'their registry class, {unknown} with no class known exactly '
            f'(score 0); {len(missing) - unknown} registry param(s) unstated '
            f'(family-weighted)', missing)


def _score_uop_04(sc):  # CMP-UOP-04
    positions = sc.internal + sc.outlets
    if not positions:
        return None, 'no internal or product streams', []
    # R2.4 consistency guard: the outlets of a physical-only unit whose
    # species appear or vanish without declared reactions (or cannot be
    # checked) do not count their composition as stated.
    suspect = {}
    for position, unit in enumerate(sc.units):
        consistent, why = _species_consistency(sc, position)
        uid = unit.get('id')
        if not consistent and isinstance(uid, str):
            for i in sc.unit_outlets.get(uid, []):
                suspect.setdefault(i, (sc.unit_labels[position], why))
    scores, missing, full = [], [], 0
    for i in sorted(positions):
        state = dict(sc.stream_states[i])
        if i in suspect:
            state['composition'] = False
        scores.append(sum(state.values()) / len(state))
        full += all(state.values())
        for key, ok in state.items():
            if ok:
                continue
            note = ''
            if key == 'composition' and sc.stream_states[i][key]:
                label, why = suspect[i]
                note = f' (stated, but inconsistent at {label}: {why})'
            missing.append(f'{sc.stream_labels[i]}.{_STATE_PATHS[key]}{note}')
    return (_mean(scores), f'{full} of {len(positions)} internal/product '
            f'stream(s) fully stated' + (f'; {len(suspect)} outlet(s) of '
                                         f'physical-only units fail the '
                                         f'species-consistency guard'
                                         if suspect else ''), missing)


def _score_uop_05(sc):  # CMP-UOP-05
    # Derived credit needs the class at confidence 1 AND a known family (an
    # abstract class such as 'Unit' names none). Population: the units that
    # need operating specifications (CMP-UOP-02) or facility settings
    # (CMP-UOP-06), whose numeric settings (T_min_app, ...) carry no units
    # either.
    pairs, missing = [], []
    for position in sorted(set(_spec_population(sc))
                           | set(_facility_population(sc))):
        name, family, confidence = sc.unit_classes[position]
        known = bool(name) and confidence == 1.0 and family is not None
        pairs.append((DERIVED_CREDIT if known else 0.0,
                      _unit_weight(sc, position)))
        missing.append(f'{sc.unit_labels[position]}.design_input_specs: '
                       f'quantity units not representable'
                       + (' (fixed by class conventions)' if known else ''))
    if not pairs:
        return None, 'no units that need operating specifications', []
    known = sum(1 for credit, _w in pairs if credit)
    return (_wmean(pairs), f'not representable; units implied by class '
            f'conventions for {known} of {len(pairs)} unit(s)', missing)


#%% Facility bindings and settings (CMP-UOP-06)

# Facility classes that only add a cost (no binding or setting a rebuild
# needs): excluded from CMP-UOP-06.
_COST_ONLY_FACILITIES = frozenset({
    'CIPpackage', 'FireWaterTank', 'AirDistributionPackage',
    'WastewaterSystemCost', 'ChemicalCapitalInvestment',
    'PlantAir_CIP_WasteWater_Facilities'})

# A facility's served agent: stated as `agent` (or a BoilerTurbogenerator's
# `other_agents`), else derivable when its utility results name a declared
# heat agent (the steam it raises, the cooling or chilled water it
# regenerates).
_SERVED_AGENT = _Req(1, ('agent', 'other_agents'), {'agent': ('served_agent',)})

# Per-class requirements (R2.5), checked against the biosteam source
# (facilities/_boiler_turbogenerator.py, _process_water_center.py,
# _cooling_tower.py, _chilled_water_package.py, _refrigeration_package.py)
# and hensmith's HeatExchangerNetwork. Every one is a constructor argument
# with a default, so it must be stated (R2.8). Stream- and unit-id lists
# count only when non-empty and every id is declared.
_FACILITY_RULES = {
    'BoilerTurbogenerator': (
        _Req(1, ('boiler_efficiency',), {}),
        _Req(1, ('turbogenerator_efficiency',), {}),
        _Req(1, ('satisfy_system_electricity_demand',), {}),
        _Req(1, ('fuel_source',), {}),
        _SERVED_AGENT),
    'Boiler': (_Req(1, ('boiler_efficiency',), {}),),
    'ProcessWaterCenter': (_Req(1, ('process_water_streams',), {}),
                           _Req(1, ('makeup_water_streams',), {})),
    'HeatExchangerNetwork': (_Req(1, ('T_min_app',), {}),
                             _Req(1, ('units',), {})),
    'CoolingTower': (_SERVED_AGENT,),
    'ChilledWaterPackage': (_SERVED_AGENT,),
    # RefrigerationPackage(ID, agent, efficiency=0.35): its efficiency too.
    'RefrigerationPackage': (_SERVED_AGENT, _Req(1, ('efficiency',), {})),
}

# Facility kind of a class-less facility-family unit, by keyword on its
# class name or unit_type (first match wins); 'cost_only' is excluded.
_FACILITY_KIND_KEYWORDS = tuple((kind, re.compile(pattern)) for kind, pattern in (
    ('BoilerTurbogenerator', r'turbo ?generator'),
    ('Boiler', r'\bboiler\b'),
    ('HeatExchangerNetwork', r'heat exchanger network|\bhxn\b'),
    ('CoolingTower', r'cooling tower'),
    ('ChilledWaterPackage', r'chilled water'),
    ('RefrigerationPackage', r'refrigeration'),
    ('ProcessWaterCenter', r'process water'),
    ('cost_only', r'\bcip\b|cip ?package|fire ?water|air distribution|'
                  r'plant air|waste ?water system cost|capital investment'),
))

# Design-spec keys naming stream / unit / agent ids.
_STREAM_LIST_KEYS = frozenset({'process_water_streams', 'makeup_water_streams'})


def _facility_kind(sc, position):
    """_FACILITY_RULES key of facility-family unit `position`, 'cost_only',
    or None (kind unknown): its class when listed, else a keyword on its
    class name or unit_type, else its family's only class (boiler,
    boiler_turbogenerator, heat_exchanger_network)."""
    name, family, _confidence = sc.unit_classes[position]
    if name in _FACILITY_RULES:
        return name
    if name in _COST_ONLY_FACILITIES:
        return 'cost_only'
    for text in (name, sc.units[position].get('unit_type')):
        if not _nonempty_str(text):
            continue
        variants = _normalized_texts(text)
        for kind, pattern in _FACILITY_KIND_KEYWORDS:
            if any(pattern.search(v) for v in variants):
                return kind
    return {'boiler': 'Boiler', 'boiler_turbogenerator': 'BoilerTurbogenerator',
            'heat_exchanger_network': 'HeatExchangerNetwork'}.get(family)


def _facility_population(sc):
    """Positions of the CMP-UOP-06 population (R2.5): facility-family units
    (FACILITY_FAMILIES) except cost-only facilities."""
    return [p for p, (_n, family, _c) in enumerate(sc.unit_classes)
            if family in FACILITY_FAMILIES
            and _facility_kind(sc, p) != 'cost_only']


def _valid_id_list(value, ids):
    """True when `value` is a non-empty list of string ids, every one in
    `ids` (R2.5: a binding to undeclared streams or units binds nothing)."""
    return (isinstance(value, (list, tuple)) and bool(value)
            and all(isinstance(v, str) and v in ids for v in value))


def _heat_agent_ids(sc):
    """Ids of the declared heat-utility agents."""
    return {agent['id'] for group, agent, _l in sc.agents
            if group == 'heat_utilities' and isinstance(agent.get('id'), str)}


def _facility_explicit(sc, position):
    """design_input_specs keys of unit `position` stated plausibly for
    CMP-UOP-06: _explicit_keys, with id-valued keys checked against the
    document -- process/makeup water stream lists against the declared
    streams, `units` against the declared units, `agent` (a heat-agent id)
    and `other_agents` (a list of them) against the declared heat agents."""
    specs = _as_dict(sc.units[position].get('design_input_specs'))
    stream_ids = {s['id'] for s in sc.streams if _nonempty_str(s.get('id'))}
    agents = _heat_agent_ids(sc)
    explicit = set()
    for key in _explicit_keys(sc.units[position]):
        value = specs[key]
        if key in _STREAM_LIST_KEYS:
            ok = _valid_id_list(value, stream_ids)
        elif key == 'units':
            ok = _valid_id_list(value, sc.unit_ids)
        elif key == 'agent':
            ok = isinstance(value, str) and value in agents
        elif key == 'other_agents':
            ok = _valid_id_list(value, agents)
        else:
            ok = True
        if ok:
            explicit.add(key)
    return explicit


def _serves_agent(sc, position):
    """True when unit `position`'s utility results name a declared heat
    agent (the agent a facility raises or regenerates is then derivable)."""
    unit = sc.units[position]
    agents = _heat_agent_ids(sc)
    return any(isinstance(k, str) and k in agents
               for key in _UTILITY_RESULT_KEYS
               for k in _as_dict(unit.get(key)))


def _facility_unit_score(sc, position):
    """(score, missing) of one CMP-UOP-06 unit: the mean of its facility
    kind's requirements (_FACILITY_RULES, scored like CMP-UOP-02
    requirements); 0 when its kind is unknown."""
    label = sc.unit_labels[position]
    kind = _facility_kind(sc, position)
    rules = _FACILITY_RULES.get(kind)
    if rules is None:
        return 0.0, [f'{label}: facility kind unknown, so its bindings and '
                     f'settings cannot be determined']
    explicit = _facility_explicit(sc, position)
    scores, missing = [], []
    for req in rules:
        score = _requirement_score(req, sc, position, explicit)
        scores.append(score)
        if score < 1.0:
            missing.append(_requirement_locator(label, req, score))
    return _mean(scores), missing


def _score_uop_06(sc):  # CMP-UOP-06
    pairs, missing = [], []
    for position in _facility_population(sc):
        score, gaps = _facility_unit_score(sc, position)
        pairs.append((score, _unit_weight(sc, position)))
        missing.extend(gaps)
    if not pairs:
        return None, 'no facilities with bindings or settings', []
    full = sum(1 for score, _w in pairs if score == 1.0)
    return (_wmean(pairs), f'{full} of {len(pairs)} facility unit(s) state '
            f'every binding and setting (family-weighted)', missing)

#%% Criteria -- reactions

def _species_change(sc, unit_id):
    """Species that appear in (or vanish from) a unit's outlets relative to
    its inlets, as evidence strings ('CO2 appears'); [] when the unit lacks
    inlets or outlets or nothing changes; None when some connected stream's
    species flows are not computable (_species_flows), so a change cannot be
    ruled out."""
    if not isinstance(unit_id, str):
        return []
    ins, outs = sc.unit_inlets.get(unit_id, []), sc.unit_outlets.get(unit_id, [])
    if not ins or not outs:
        return []
    flows_in, flows_out = {}, {}
    for positions, acc in ((ins, flows_in), (outs, flows_out)):
        for i in positions:
            flows = _species_flows(sc.streams[i])
            if flows is None:
                return None
            for name, value in flows.items():
                acc[name] = acc.get(name, 0.0) + value
    total = max(sum(flows_in.values()), sum(flows_out.values()))
    tol = max(ZERO_FLOW, SPECIES_RTOL * total)
    appear = [n for n, v in flows_out.items()
              if v > tol and flows_in.get(n, 0.0) <= ZERO_FLOW]
    vanish = [n for n, v in flows_in.items()
              if v > tol and flows_out.get(n, 0.0) <= ZERO_FLOW]
    return ([f'{n} appears' for n in sorted(appear)]
            + [f'{n} vanishes' for n in sorted(vanish)])


def _species_guarded(sc, position):
    """True when unit `position` falls under the species-appearance
    consistency guard (R2.4): a physical-only family (PHYSICAL_FAMILIES --
    a splitter unless it names a custom, non-core class, whose code may
    transform species) that declares no well-formed reaction. A splitter of
    unknown class is guarded: only an identified custom class waives it."""
    name, family, _confidence = sc.unit_classes[position]
    if family not in PHYSICAL_FAMILIES or sc.unit_reacts[position]:
        return False
    return not (family == 'splitter' and name and not sc.unit_core[position])


def _species_consistency(sc, position):
    """(consistent, reason) of the R2.4 consistency guard for unit
    `position`, cached per context: (True, '') unless the unit is guarded
    (_species_guarded); a guarded unit is consistent only when every
    connected stream's species flows are computable and no species appears
    or vanishes. An uncomputable port leaves consistency unverified (False),
    so deleting a stream's composition never lifts the guard."""
    cache = sc.consistency
    if position in cache:
        return cache[position]
    result = True, ''
    if _species_guarded(sc, position):
        change = _species_change(sc, sc.units[position].get('id'))
        if change is None:
            result = False, ('species flows not computable for every '
                             'connected stream, so no undeclared reaction '
                             'can be ruled out')
        elif change:
            result = False, (f"{', '.join(change[:3])} across a "
                             f"physical-only unit without declared reactions")
    cache[position] = result
    return result


def _reactive_reason(sc, position):
    """'reactor family' when unit `position` needs reactions by its family
    (R2.4: the reactor families -- continuous, batch, aerated and gas-fed
    bioreactors, incl. keyword reactors such as neutralizers,
    hydrotreaters, gasifiers, pyrolysis and hydrolysis units -- minus the
    class-implied-chemistry equilibrium reactors); else None. Declared
    reactions and species appearance no longer make a unit reactive: a
    population defined by data let deleting that data shrink it."""
    family = sc.unit_classes[position][1]
    if family in REACTOR_FAMILIES - CLASS_IMPLIED_CHEMISTRY_FAMILIES:
        return 'reactor family'
    return None


def _reactive_units(sc):
    """[(position, reason)] of the reactive units: the CMP-RXN-01
    population (_reactive_reason)."""
    return [(p, reason) for p, reason in
            ((p, _reactive_reason(sc, p)) for p in range(len(sc.units)))
            if reason is not None]


def _reaction_units(sc):
    """[(position, reason)] whose reactions CMP-RXN-02/03/04 score (R2.4):
    the reactive units, plus every other unit that declares reactions (a
    non-empty list of reaction objects; reason 'declares reactions').
    Deleting a field of a reaction never shrinks this population; deleting
    whole reactions (list elements) can."""
    reactive = dict(_reactive_units(sc))
    return [(p, reactive.get(p, 'declares reactions'))
            for p in range(len(sc.units))
            if p in reactive or _dicts(sc.units[p].get('reactions'))]


def _score_rxn_01(sc):  # CMP-RXN-01
    reactive = _reactive_units(sc)
    if not reactive:
        return None, 'no reactive units', []
    # A unit carries reactions only with >= 1 well-formed one (R2.0).
    pairs, missing = [], []
    for position, reason in reactive:
        carries = sc.unit_reacts[position]
        pairs.append((1.0 if carries else 0.0, _unit_weight(sc, position)))
        if not carries:
            missing.append(f'{sc.unit_labels[position]}.reactions: none '
                           f'well-formed declared ({reason})')
    ok = len(reactive) - len(missing)
    return (_wmean(pairs), f'{ok} of {len(reactive)} reactive unit(s) carry '
            f'reactions (family-weighted)', missing)


_MALFORMED_REACTION = (': not well-formed (its equation|stoichiometry names '
                       'no consumed and produced species)')


def _reaction_completeness(sc, reaction, where):
    """(score, missing) of one reaction's definition (R2.0): 0 unless it is
    well-formed (_well_formed); else the mean of {well-formed, reactant among
    the consumed species, conversion numeric in [0, 1]}."""
    consumed, _produced = sc.reaction_sides(reaction)
    if not consumed:
        return 0.0, [where + _MALFORMED_REACTION]
    reactant = reaction.get('reactant')
    parts = {
        'equation|stoichiometry': True,
        'reactant (a consumed species)': (isinstance(reactant, str)
                                          and reactant.strip() in consumed),
        'conversion (0..1)': _valid_fraction(reaction.get('conversion'))
        is not None,
    }
    return (sum(parts.values()) / len(parts),
            [f'{where}.{k}' for k, ok in parts.items() if not ok])


def _per_reactive_unit(sc, reaction_score):
    """(family-weighted mean, missing, n units scoring 1, n reactive units)
    over the reaction units (_reaction_units) of each unit's mean
    reaction_score(sc, position, index, reaction) -> (score, missing); a
    reactive unit without reactions contributes 0. The mean is None when the
    population is empty."""
    pairs, missing, full = [], [], 0
    for position, reason in _reaction_units(sc):
        label = sc.unit_labels[position]
        reactions = [(ri, r) for ri, r in
                     enumerate(_as_list(sc.units[position].get('reactions')))
                     if isinstance(r, dict)]
        if not reactions:
            pairs.append((0.0, _unit_weight(sc, position)))
            missing.append(f'{label}.reactions: none declared ({reason})')
            continue
        scores = []
        for ri, reaction in reactions:
            score, gaps = reaction_score(sc, position, ri, reaction)
            scores.append(score)
            missing.extend(gaps)
        unit_score = _mean(scores)
        full += unit_score == 1.0
        pairs.append((unit_score, _unit_weight(sc, position)))
    return _wmean(pairs), missing, full, len(pairs)


def _score_rxn_02(sc):  # CMP-RXN-02
    score, missing, full, n = _per_reactive_unit(
        sc, lambda sc, p, ri, r: _reaction_completeness(
            sc, r, f'{sc.unit_labels[p]}.reactions[{ri}]'))
    if not n:
        return None, 'no reactive units and no declared reactions', []
    return (score, f'{full} of {n} reactive / reaction-declaring unit(s) '
            f'define every reaction completely (family-weighted)', missing)


def _score_rxn_03(sc):  # CMP-RXN-03
    # Population = the reaction units (R2.4: the reactive units plus every
    # other unit declaring reactions), not the units that happen to carry
    # >= 2 reactions: deleting a reactor's reactions must not turn the
    # criterion not-applicable. Per unit: >= 2 reactions -> 1 when every one
    # is indexed, else 0; exactly one -> 1 (no order to state); none -> 0
    # (its reaction set, and so their order, is unknown). Only well-formed
    # reactions make a reaction set (R2.0): a unit with none scores 0.
    reactive = _reaction_units(sc)
    if not reactive:
        return None, 'no reactive units and no declared reactions', []
    pairs, missing, multi = [], [], 0
    for position, reason in reactive:
        label = sc.unit_labels[position]
        reactions = _dicts(sc.units[position].get('reactions'))
        if not sc.unit_reacts[position]:
            pairs.append((0.0, _unit_weight(sc, position)))
            missing.append(f'{label}.reactions: none well-formed declared '
                           f'({reason}), so their series/parallel order is '
                           f'unknown')
            continue
        multi += len(reactions) >= 2
        unindexed = [ri for ri, r in enumerate(reactions)
                     if len(reactions) >= 2 and (
                         isinstance(r.get('index'), bool)
                         or not isinstance(r.get('index'), int))]
        pairs.append((0.0 if unindexed else 1.0, _unit_weight(sc, position)))
        missing.extend(f'{label}.reactions[{ri}].index' for ri in unindexed)
    ok = sum(1 for score, _w in pairs if score == 1.0)
    return (_wmean(pairs), f'{ok} of {len(reactive)} reactive / '
            f'reaction-declaring unit(s) state their reaction order ({multi} with two or more reactions; '
            f'family-weighted)', missing)


def _reaction_coefficients(sc, reaction):
    """{chemical id: signed coefficient} of a reaction -- from its
    stoichiometry, else its equation (phase tags stripped) -- resolved by the
    validator's helpers; None when neither resolves."""
    validator = _validator()
    try:
        stoichiometry = reaction.get('stoichiometry')
        # A non-numeric coefficient counts as absent (R2.0), exactly as if
        # its entry were deleted.
        if isinstance(stoichiometry, dict):
            stoichiometry = {k: v for k, v in stoichiometry.items()
                             if _num(v) is not None}
        elif isinstance(stoichiometry, list):
            stoichiometry = [v if _num(v) is not None else 0.0
                             for v in stoichiometry]
        if _filled(stoichiometry):
            coeffs, _reason = validator._stoich_to_coeffs(stoichiometry, sc.vctx)
            if coeffs:
                return coeffs
        equation = reaction.get('equation')
        if isinstance(equation, str) and '->' in equation:
            sides = [' + '.join(_PHASE_TAG_RE.sub('', t.strip())
                                for t in side.split('+'))
                     for side in equation.split('->', 1)]
            return validator._parse_equation(' -> '.join(sides), sc.vctx) or None
    except Exception:  # malformed document values: no coefficients
        return None
    return None


def _has_phase_tags(equation):
    """True when every species of a reaction equation carries a thermosteam
    phase tag ('Glucose,l -> 2 Ethanol,l + 2 CO2,g')."""
    if not isinstance(equation, str) or '->' not in equation:
        return False
    terms = [t.strip() for side in equation.split('->', 1)
             for t in side.split('+') if t.strip()]
    return bool(terms) and all(_PHASE_TAG_RE.search(t) for t in terms)


def _basis_derivable(sc, reaction):
    """True when the reaction's basis follows from its coefficients: one of
    sum(nu*MW) ~ 0 (molar basis conserves mass) and sum(nu) ~ 0 (mass basis)
    holds, to BASIS_RTOL relative, with every molar mass resolvable
    (declared or from the formula, as the validator resolves it); when both
    hold (an isomerisation) the basis is immaterial (R2.10)."""
    coeffs = _reaction_coefficients(sc, reaction)
    if not coeffs:
        return False
    masses = [sc.vctx.molar_mass(cid) for cid in coeffs]
    if any(m is None for m in masses):
        return False
    nus = list(coeffs.values())
    terms = [nu * m for nu, m in zip(nus, masses)]
    molar_basis = abs(sum(terms)) <= BASIS_RTOL * sum(abs(t) for t in terms)
    mass_basis = abs(sum(nus)) <= BASIS_RTOL * sum(abs(nu) for nu in nus)
    # Both hold for an isomerisation (A -> B of equal molar mass): the basis
    # is then immaterial (R2.10), so it is derivable as well.
    return molar_basis or mass_basis


def _single_phase_ports(sc, position):
    """True when unit `position` has >=1 connected stream and every one
    carries phase data with at most one phase."""
    uid = sc.units[position].get('id')
    if not isinstance(uid, str):
        return False
    ports = sc.unit_inlets.get(uid, []) + sc.unit_outlets.get(uid, [])
    sets = [_phase_set(sc.streams[i]) for i in ports]
    return bool(sets) and all(s is not None and len(s) <= 1 for s in sets)


def _reaction_basis_phase(sc, position, ri, reaction):
    """(score, missing) of one reaction for CMP-RXN-04: mean of {basis,
    phase}, each DERIVED_CREDIT when derivable (neither is representable); 0
    for a reaction that is not well-formed (R2.0)."""
    where = f'{sc.unit_labels[position]}.reactions[{ri}]'
    if not sc.well_formed(reaction):
        return 0.0, [where + _MALFORMED_REACTION]
    basis = _basis_derivable(sc, reaction)
    phase = (_has_phase_tags(reaction.get('equation'))
             or _single_phase_ports(sc, position))
    missing = [f'{where}: basis (mol|wt) not representable'
               + (' (derivable from the mass balance)' if basis else ''),
               f'{where}: reacting phase not representable'
               + (' (derivable)' if phase else '')]
    return DERIVED_CREDIT * (basis + phase) / 2, missing


def _score_rxn_04(sc):  # CMP-RXN-04
    score, missing, _full, n = _per_reactive_unit(sc, _reaction_basis_phase)
    if not n:
        return None, 'no reactive units and no declared reactions', []
    return (score, f'not representable; basis and phase derivable '
            f'(DERIVED_CREDIT) where the data imply them, over {n} reactive '
            f'/ reaction-declaring unit(s)', missing)

#%% Criteria -- costing

def _cost_items(sc):
    """(unit, label, item_id, purchase_cost) for every purchase-cost item."""
    return [(unit, label, item, cost)
            for unit, label in zip(sc.units, sc.unit_labels)
            for item, cost in _as_dict(unit.get('purchase_costs')).items()]


def _correlation(unit, item):
    return _as_dict(_as_dict(unit.get('purchase_cost_correlations')).get(item))


_COST_ITEM_KEYS = ('purchase_costs', 'installed_costs',
                   'purchase_cost_correlations')


def _unit_cost_items(unit):
    """Cost items of one unit: the keys of purchase_costs, installed_costs
    and purchase_cost_correlations, first occurrence first. The per-item
    populations of CMP-COST-02/04/07 use this union (R1.0): deleting an item
    from one object (say, a poorly correlated item's purchase cost) leaves it
    in the population instead of dropping it and raising the unit's mean."""
    items = []
    for key in _COST_ITEM_KEYS:
        for item in _as_dict(unit.get(key)):
            if item not in items:
                items.append(item)
    return items


_NO_COSTED_UNITS = 'no costed units (no units, or all are cost-free mixers/splitters)'


def _per_costed_unit(sc, unit_score, positions=None):
    """(family-weighted mean or None, missing, n units scoring 1) over the
    costed units (or `positions`) of unit_score(sc, position) -> (score,
    missing locators)."""
    pairs, missing = [], []
    for position in sc.costed if positions is None else positions:
        score, gaps = unit_score(sc, position)
        pairs.append((score, _unit_weight(sc, position)))
        missing.extend(gaps)
    full = sum(1 for score, _w in pairs if score == 1.0)
    return _wmean(pairs), missing, full


def _cost_01_unit(sc, position):
    """CMP-COST-01 of one costed unit: purchase_costs holding >= 1 valid
    (numeric, >= 0) cost -> 1, else 0 (R2.0)."""
    costs = sc.units[position].get('purchase_costs')
    if isinstance(costs, dict) and any(_valid_cost(v) is not None
                                       for v in costs.values()):
        return 1.0, []
    note = (' (no numeric cost >= 0)' if isinstance(costs, dict) and costs
            else ' (empty)' if isinstance(costs, dict) else '')
    return 0.0, [f'{sc.unit_labels[position]}.purchase_costs{note}']


def _score_cost_01(sc):  # CMP-COST-01
    if not sc.costed:
        return None, _NO_COSTED_UNITS, []
    score, missing, ok = _per_costed_unit(sc, _cost_01_unit)
    return (score, f'{ok} of {len(sc.costed)} costed unit(s) record purchase '
            f'costs (family-weighted)', missing)


def _cost_02_unit(sc, position):
    """CMP-COST-02 of one costed unit: fraction of its cost items
    (_unit_cost_items) with a numeric installed cost; no items -> 0."""
    unit, label = sc.units[position], sc.unit_labels[position]
    items = _unit_cost_items(unit)
    if not items:
        return 0.0, [f'{label}.purchase_costs: no items (so no installed costs)']
    installed = _as_dict(unit.get('installed_costs'))
    missing = [f"{label}.installed_costs['{item}']" for item in items
               if _valid_cost(installed.get(item)) is None]
    return 1.0 - len(missing) / len(items), missing


def _score_cost_02(sc):  # CMP-COST-02
    if not sc.costed:
        return None, _NO_COSTED_UNITS, []
    score, missing, ok = _per_costed_unit(sc, _cost_02_unit)
    return (score, f'{ok} of {len(sc.costed)} costed unit(s) have an installed '
            f'cost for every item (family-weighted)', missing)


def _has_quantity_unit(value, declared):
    """A numeric design result has a unit: legacy inline units, or the entry
    in quantity_units_for_design_results -- a non-blank string, or exactly
    '' (the schema's documented dimensionless sentinel)."""
    units = value.get('units') if isinstance(value, dict) else declared
    return units == '' or _nonempty_str(units)


def _cost_03_unit(sc, position):
    """CMP-COST-03 of one costed unit: no numeric design result (absent,
    empty, or only non-numeric values; R2.0) -> 0; some numeric result
    without a quantity unit -> 0.5; else 1."""
    unit, label = sc.units[position], sc.unit_labels[position]
    results = unit.get('design_results')
    if not isinstance(results, dict) or not any(
            _num(v) is not None for v in results.values()):
        note = (' (no numeric result)' if isinstance(results, dict) and results
                else ' (empty)' if isinstance(results, dict) else '')
        return 0.0, [f'{label}.design_results{note}']
    units_map = _as_dict(unit.get('quantity_units_for_design_results'))
    unitless = [k for k, v in results.items() if _num(v) is not None
                and not _has_quantity_unit(v, units_map.get(k))]
    return (0.5 if unitless else 1.0,
            [f"{label}.quantity_units_for_design_results['{k}']"
             for k in unitless])


def _score_cost_03(sc):  # CMP-COST-03
    if not sc.costed:
        return None, _NO_COSTED_UNITS, []
    score, missing, ok = _per_costed_unit(sc, _cost_03_unit)
    return (score, f'{ok} of {len(sc.costed)} costed unit(s) state design '
            f'results with units (family-weighted)', missing)


_CORRELATION_CREDIT = {'power_law': 1.0, 'custom_function': 0.5}


def _cost_04_unit(sc, position):
    """CMP-COST-04 of one costed unit: 1 for a core BioSTEAM class identified
    at confidence 1 (BioSTEAM re-derives its cost from its class and design
    results); else the mean correlation credit over its cost items
    (_unit_cost_items; power_law 1, custom_function 0.5, none 0); no items
    -> 0."""
    if _core_class_known(sc, position):
        return 1.0, []
    unit, label = sc.units[position], sc.unit_labels[position]
    items = _unit_cost_items(unit)
    if not items:
        return 0.0, [f'{label}.purchase_costs: no items to correlate']
    scores, missing = [], []
    for item in items:
        kind = _correlation(unit, item).get('correlation_type')
        kind = kind if isinstance(kind, str) else None  # unhashable-safe
        scores.append(_CORRELATION_CREDIT.get(kind, 0.0))
        if kind != 'power_law':
            note = (' (custom_function: not re-derivable)'
                    if kind == 'custom_function' else '')
            missing.append(f"{label}.purchase_cost_correlations['{item}']{note}")
    return _mean(scores), missing


def _score_cost_04(sc):  # CMP-COST-04
    # Every costed unit (R1.0). A core class identified at confidence 1 stays
    # in the population and scores 1 -- BioSTEAM re-derives its cost from its
    # class and design results -- so deleting the statement that identifies
    # it (design_simulation_method) can only lower its credit; excluding it
    # instead let that deletion move it into the population and raise the
    # score.
    if not sc.costed:
        return None, _NO_COSTED_UNITS, []
    score, missing, ok = _per_costed_unit(sc, _cost_04_unit)
    core = sum(1 for p in sc.costed if _core_class_known(sc, p))
    return (score, f'{ok} of {len(sc.costed)} costed unit(s) re-costable: '
            f'{core} core BioSTEAM class(es) at confidence 1, the rest by '
            f'power-law correlations for every item (family-weighted)', missing)


def _back_derivable(unit, item, cost):
    """A power-law item from which the target CEPCI can be back-derived."""
    corr = _correlation(unit, item)
    if corr.get('correlation_type') != 'power_law':
        return False
    basis = corr.get('basis')
    size = _num(_as_dict(unit.get('design_results')).get(basis)) \
        if isinstance(basis, str) else None
    purchase = _num(cost)
    return (purchase is not None and purchase > 0 and size is not None
            and all(_num(corr.get(k)) is not None for k in
                    ('reference_cost', 'exponent', 'reference_size',
                     'reference_CE_index')))


def _score_cost_05(sc):  # CMP-COST-05
    if not sc.costed:
        return None, _NO_COSTED_UNITS, []
    missing = ['metadata: target cost index (CEPCI) of the costs not representable']
    source = next((f"{label}.purchase_costs['{item}']" for unit, label, item, cost
                   in _cost_items(sc) if _back_derivable(unit, item, cost)), None)
    if source:
        return (DERIVED_CREDIT, f'not representable; back-derivable from '
                f'{source}', missing)
    return 0.0, 'not representable and no power-law item allows back-derivation', missing


def _count_result_key(unit):
    """The first design_results key naming a count ('Number of reactors')
    with a numeric value (R2.0), or None."""
    for key, value in _as_dict(unit.get('design_results')).items():
        if isinstance(key, str) and key.strip().lower().startswith(
                'number of') and _num(value) is not None:
            return key
    return None


def _cost_06_unit(sc, position):
    """CMP-COST-06 of one costed unit: DERIVED_CREDIT when a design result
    states a count ('Number of ...'), else 0 (never representable)."""
    label = sc.unit_labels[position]
    key = _count_result_key(sc.units[position])
    if key:
        return DERIVED_CREDIT, [f"{label}: parallel counts (Unit.parallel, "
                                f"CostItem.N) not representable (derivable "
                                f"from design_results['{key}'])"]
    return 0.0, [f'{label}: parallel counts (Unit.parallel, CostItem.N) not '
                 f'representable']


def _score_cost_06(sc):  # CMP-COST-06
    if not sc.costed:
        return None, _NO_COSTED_UNITS, []
    score, missing, _ok = _per_costed_unit(sc, _cost_06_unit)
    derived = sum(1 for p in sc.costed if _count_result_key(sc.units[p]))
    return (score, f'not representable; derivable from a count design result '
            f'for {derived} of {len(sc.costed)} costed unit(s)', missing)


def _cost_07_unit(sc, position):
    """CMP-COST-07 of one costed unit: 1 when it has cost items
    (_unit_cost_items) and every one has a correlation stating a numeric
    lifetime; else DERIVED_CREDIT for a core class at confidence 1 (class
    default lifetime); else 0."""
    unit, label = sc.units[position], sc.unit_labels[position]
    items = _unit_cost_items(unit)
    if items and all(_num(_correlation(unit, item).get('lifetime')) is not None
                     for item in items):
        return 1.0, []
    if _core_class_known(sc, position):
        return DERIVED_CREDIT, [f'{label}: equipment lifetime not stated '
                                f'(class default applies)']
    return 0.0, [f'{label}: equipment lifetime not stated '
                 f'(purchase_cost_correlations[*].lifetime)']


def _score_cost_07(sc):  # CMP-COST-07
    if not sc.costed:
        return None, _NO_COSTED_UNITS, []
    score, missing, ok = _per_costed_unit(sc, _cost_07_unit)
    return (score, f'{ok} of {len(sc.costed)} costed unit(s) state every '
            f'lifetime (family-weighted)', missing)

#%% Criteria -- utilities

def _utility_refs(sc):
    """{agent id: first unit label referencing it} over every unit's
    utility_consumption_results and utility_production_results keys."""
    refs = {}
    for unit, label in zip(sc.units, sc.unit_labels):
        for key in ('utility_consumption_results', 'utility_production_results'):
            for agent_id in _as_dict(unit.get(key)):
                refs.setdefault(agent_id, label)
    return refs


def _agent_gaps(group, agent):
    """Unstated required fields of one declared agent (heat/other: numeric T
    and P plus a non-empty composition; power: an id)."""
    if group == 'power_utilities':
        return [] if _nonempty_str(agent.get('id')) else ['id']
    gaps = [k for k in ('temperature', 'pressure') if _num(agent.get(k)) is None]
    if not _dicts(agent.get('composition')):
        gaps.append('composition')
    return gaps


def _utility_need(sc):
    """True when the flowsheet needs utility agents (R1.9): a unit in a
    utility-using family, any unit utility result, or any declared agent."""
    return (any(family in UTILITY_USING_FAMILIES
                for _name, family, _c in sc.unit_classes)
            or bool(_utility_refs(sc)) or bool(sc.agents))


def _heat_need(sc):
    """True when the flowsheet needs heat-utility agents: a unit in a
    heat-exchanging family, or a declared heat agent."""
    return (any(family in HEAT_UTILITY_FAMILIES
                for _name, family, _c in sc.unit_classes)
            or any(group == 'heat_utilities' for group, _a, _l in sc.agents))


_NO_UTILITY_NEED = 'no unit needs utilities and no agent is declared or referenced'


def _missing_agent_kinds(sc):
    """Locators of the synthetic missing-agent entries of CMP-UTIL-01/02
    (R2.7), each scoring 0: a heat agent when a unit is in a heat-exchanging
    family (HEAT_UTILITY_FAMILIES) but no heat-utility agent is declared; a
    power utility when a unit is in a power-drawing or -delivering family
    (POWER_UTILITY_FAMILIES) but no power utility is declared. Need is per
    kind: a declared power utility says nothing about steam."""
    families = {family for _n, family, _c in sc.unit_classes}
    groups = {group for group, _a, _l in sc.agents}
    missing = []
    if families & HEAT_UTILITY_FAMILIES and 'heat_utilities' not in groups:
        missing.append('utilities.heat_utilities: no heat agent declared, '
                       'although units exchange heat')
    if families & POWER_UTILITY_FAMILIES and 'power_utilities' not in groups:
        missing.append('utilities.power_utilities: no power utility '
                       'declared, although units draw or deliver electricity')
    return missing


def _score_util_01(sc):  # CMP-UTIL-01
    if not _utility_need(sc):
        return None, _NO_UTILITY_NEED, []
    refs = _utility_refs(sc)
    declared = {agent.get('id'): (group, agent, label)
                for group, agent, label in sc.agents
                if isinstance(agent.get('id'), str)}
    if not refs and not sc.agents:
        return (0.0, 'utility-using units but no agent declared or referenced',
                ['utilities: no agents declared'])
    scores, missing = [], []
    for agent_id, unit_label in refs.items():
        if agent_id not in declared:
            scores.append(0.0)
            missing.append(f"utilities: no agent '{agent_id}' (referenced by "
                           f"{unit_label})")
    for group, agent, label in sc.agents:
        gaps = _agent_gaps(group, agent)
        size = 1 if group == 'power_utilities' else 3
        scores.append(1.0 - len(gaps) / size)
        missing.extend(f'{label}.{gap}' for gap in gaps)
    kinds = _missing_agent_kinds(sc)
    scores.extend(0.0 for _k in kinds)
    missing.extend(kinds)
    full = scores.count(1.0)
    return (_mean(scores), f'{full} of {len(scores)} referenced/declared '
            f'agent(s) resolve and are fully specified'
            + (f' ({len(kinds)} needed kind(s) missing)' if kinds else ''),
            missing)


# Accepted price fields per agent group, and the locator naming what to add.
# 'price' on a power agent is the <=0.0.6 name of electrical_energy_price
# (renamed with the 0.0.7 quantity-units reshape), so legacy files count.
_PRICE_FIELDS = {
    'heat_utilities': (('regeneration_price', 'heat_transfer_price'),
                       'regeneration_price|heat_transfer_price'),
    'power_utilities': (('electrical_energy_price', 'price'),
                        'electrical_energy_price'),
    'other_utilities': (('price',), 'price'),
}


def _score_util_02(sc):  # CMP-UTIL-02
    if not _utility_need(sc):
        return None, _NO_UTILITY_NEED, []
    # String ids only: a list-valued id must not raise (unhashable, R2.0).
    declared = {agent.get('id') for _g, agent, _l in sc.agents
                if isinstance(agent.get('id'), str)}
    undeclared = [(agent_id, unit_label) for agent_id, unit_label
                  in _utility_refs(sc).items() if agent_id not in declared]
    kinds = _missing_agent_kinds(sc)
    total = len(sc.agents) + len(undeclared) + len(kinds)
    if not (sc.agents or undeclared):
        return (0.0, 'utility-using units but no agent declared or referenced',
                ['utilities: no agents declared'])
    missing = [f"utilities: no agent '{agent_id}' to price (referenced by "
               f"{unit_label})" for agent_id, unit_label in undeclared]
    missing.extend(kinds)
    for group, agent, label in sc.agents:
        fields, locator = _PRICE_FIELDS[group]
        if not any(_num(agent.get(f)) is not None for f in fields):
            missing.append(f'{label}.{locator}')
    ok = total - len(missing)
    return ok / total, f'{ok} of {total} referenced/declared agent(s) priced', missing


def _agent_roles(sc):
    """{agent id: {'heating', 'cooling'}} from the sign of the duties units
    record for it (the exporter files duty > 0 under consumption and duty <=
    0 under production; only the sign is read). Agent-regenerating families
    (boilers, cooling towers, heat-exchanger networks) record reversed duties
    and are ignored."""
    roles = {}
    for unit, (_name, family, _c) in zip(sc.units, sc.unit_classes):
        if family in AGENT_REGENERATOR_FAMILIES:
            continue
        for key in ('utility_consumption_results', 'utility_production_results'):
            for agent_id, value in _as_dict(unit.get(key)).items():
                duty = _num(value)
                if duty:
                    roles.setdefault(agent_id, set()).add(
                        'heating' if duty > 0 else 'cooling')
    return roles


def _water_dominated(composition):
    """True when water is at least half of an agent composition (by the
    stated mol, else mass, fraction)."""
    water = sum(_entry_fraction(e) or 0.0 for e in _dicts(composition)
                if str(e.get('component_name', '')).strip().lower()
                in ('water', 'h2o'))
    return water >= 0.5


def _phase_change_agent(agent, roles):
    """True when a heat agent affirmatively delivers latent heat, so it
    needs no temperature limit (R2.13): an all-vapor agent recorded as
    heating only, with positive duty (a condensing heating agent such as
    steam), or an all-liquid agent that is not water-dominated and is
    recorded as cooling only (an evaporating refrigerant). An agent no unit
    records a duty for is not exempt. Liquid water-based agents (cooling or
    chilled water, brine) and liquid heating agents are sensible-heat
    agents."""
    phases = [e.get('phase') for e in _dicts(agent.get('composition'))]
    if not phases:
        return False
    role = next(iter(roles)) if len(roles) == 1 else None
    if all(p == 'g' for p in phases):
        return role == 'heating'
    if all(p == 'l' for p in phases):
        return role == 'cooling' and not _water_dominated(agent.get('composition'))
    return False


def _score_util_03(sc):  # CMP-UTIL-03
    if not _heat_need(sc):
        return None, 'no unit exchanges heat with a heat-utility agent', []
    heat = [(agent, label) for group, agent, label in sc.agents
            if group == 'heat_utilities']
    if not heat:
        return (0.0, 'heat-exchanging units but no heat-utility agent declared',
                ['utilities.heat_utilities: none declared'])
    roles = _agent_roles(sc)
    scores, missing = [], []
    for agent, label in heat:
        aid = agent.get('id')
        efficiency = _spec_efficiency(agent.get('heat_transfer_efficiency'))
        limit = (_valid_T(agent.get('temperature_limit')) is not None
                 or _phase_change_agent(
                     agent, roles.get(aid, set()) if isinstance(aid, str)
                     else set()))
        scores.append(0.5 * efficiency + 0.5 * limit)
        if not efficiency:
            missing.append(f'{label}.heat_transfer_efficiency')
        if not limit:
            missing.append(f'{label}.temperature_limit (sensible-heat agent)')
    full = scores.count(1.0)
    return _mean(scores), f'{full} of {len(heat)} heat agent(s) fully bounded', missing


_UTILITY_RESULT_KEYS = ('utility_consumption_results',
                        'utility_production_results')


def _draws_utility(sc, position):
    """True when unit `position` always draws a utility (R2.0,
    ALWAYS_DRAW_FAMILIES). Waived only on affirmative evidence (R2.13): an
    idle unit, every connected stream stating a zero total flow, draws
    nothing; a flash whose specs state it adiabatic (a valid Q of 0) draws
    no heat."""
    family = sc.unit_classes[position][1]
    if family not in ALWAYS_DRAW_FAMILIES:
        return False
    uid = sc.units[position].get('id')
    ports = (sc.unit_inlets.get(uid, []) + sc.unit_outlets.get(uid, [])
             if isinstance(uid, str) else [])
    if ports and all(_is_empty_stream(sc.streams[i]) for i in ports):
        return False
    if family == 'flash':
        q = _as_dict(sc.units[position].get('design_input_specs')).get('Q')
        return _num(q) != 0.0
    return True


def _util_04_unit(sc, position):
    """(score, missing) of one unit for CMP-UTIL-04: 0.5 per results object
    present (an object; entries without a numeric value count as absent,
    R2.0); 0 when the unit always draws a utility (_draws_utility) but
    neither object holds a numeric entry."""
    unit, label = sc.units[position], sc.unit_labels[position]
    objects = [unit.get(k) for k in _UTILITY_RESULT_KEYS]
    absent = [k for k, obj in zip(_UTILITY_RESULT_KEYS, objects)
              if not isinstance(obj, dict)]
    numeric = any(_num(v) is not None for obj in objects
                  if isinstance(obj, dict) for v in obj.values())
    if _draws_utility(sc, position) and not numeric:
        return 0.0, [f'{label}.{_UTILITY_RESULT_KEYS[0]}|'
                     f'{_UTILITY_RESULT_KEYS[1]} (no numeric result, although '
                     f'a {sc.unit_classes[position][1]} unit always draws a '
                     f'utility)']
    return 1.0 - len(absent) / 2, [f'{label}.{k}' for k in absent]


def _score_util_04(sc):  # CMP-UTIL-04
    if not sc.units:
        return None, 'no units', []
    pairs, missing = [], []
    for position in range(len(sc.units)):
        score, gaps = _util_04_unit(sc, position)
        pairs.append((score, _unit_weight(sc, position)))
        missing.extend(gaps)
    full = sum(1 for score, _w in pairs if score == 1.0)
    return (_wmean(pairs), f'{full} of {len(pairs)} unit(s) record utility '
            f'results (family-weighted)', missing)


def _score_util_05(sc):  # CMP-UTIL-05
    if not _heat_need(sc):
        return None, 'no unit exchanges heat with a heat-utility agent', []
    return (0.0, 'agent selection parameters not representable',
            ['utilities.heat_utilities: agent approach dT, isfuel and the '
             'global HeatUtility.dT not representable'])

#%% Criteria -- system & TEA

def _score_sys_01(sc):  # CMP-SYS-01
    return (0.0, 'annual operating hours/days not representable',
            ['metadata: annual operating hours (or days) not representable'])


# The BioSTEAM TEA parameter set (CMP-SYS-02); the first two are the ones SFF
# can state (TEA_currency, TEA_year).
_TEA_ITEMS = ('currency', 'start year', 'plant duration', 'IRR (discount rate)',
              'income tax', 'depreciation schedule', 'construction schedule',
              'startup period and fractions', 'working capital',
              'fixed-operating-cost model',
              'capital-cost model (Lang or DPI/TDC/FCI factors, OSBL units, '
              'TEA class)', 'financing',
              'price book (bst.stream_prices, incl. utility-port prices)')


def _score_sys_02(sc):  # CMP-SYS-02
    stated = {'currency': _nonempty_str(sc.metadata.get('TEA_currency')),
              'start year': _num(sc.metadata.get('TEA_year')) is not None}
    missing = [f'metadata.{f}' for f, item in
               (('TEA_currency', 'currency'), ('TEA_year', 'start year'))
               if not stated[item]]
    missing.extend(f'metadata: {item} not representable'
                   for item in _TEA_ITEMS if item not in stated)
    n = sum(stated.values())
    return (n / len(_TEA_ITEMS), f'{n} of {len(_TEA_ITEMS)} TEA parameter '
            f'groups stated (only currency and year are representable)', missing)


def _score_sys_03(sc):  # CMP-SYS-03
    return (0.0, 'convergence settings not representable',
            ['system: convergence method, tolerances and maxiter not '
             'representable'])


def _pinned(package):
    package = _as_dict(package)
    return (_nonempty_scalar(package.get('commit'))
            or _nonempty_scalar(package.get('version')))


def _score_sys_04(sc):  # CMP-SYS-04
    simulator = _as_dict(sc.metadata.get('process_simulator'))
    named = (_nonempty_str(simulator.get('name'))
             and _nonempty_scalar(simulator.get('version')))
    repro = _as_dict(sc.metadata.get('reproducibility'))
    pinned = any(_pinned(repro.get(k))
                 for k in ('simulator_package', 'flowsheet_model_package'))
    missing = []
    if not named:
        missing.append('metadata.process_simulator.name|version')
    if not pinned:
        missing.append('metadata.reproducibility.simulator_package|'
                       'flowsheet_model_package.commit|version')
    message = (f"simulator {'named' if named else 'not named'}; packages "
               f"{'pinned' if pinned else 'not pinned'}")
    return 0.5 * named + 0.5 * pinned, message, missing

#%% Criteria -- integrity (validator-derived)

def _gate_score(sc, check_id):
    """(score, message, missing) for a pass/fail validator gate result."""
    result = sc.check_result(check_id)
    if result is None:
        return None, f'no {check_id} result (validator not run)', []
    if result.status == 'pass':
        return 1.0, f'{check_id} passes', []
    return 0.0, f'{check_id} {result.status}s', [
        _clip(m.strip()) for m in (result.message or '').split('; ') if m.strip()]


def _score_int_01(sc):  # CMP-INT-01
    return _gate_score(sc, 'SCHEMA')


def _score_int_02(sc):  # CMP-INT-02
    score, message, missing = _gate_score(sc, 'XREF-01')
    if score == 0.0:  # name the failing referential checks, not the aggregate
        referential = getattr(_validator(), '_REFERENTIAL_IDS', ())
        missing = [_clip(f'{r.check_id}: {r.message}') for r in sc.results
                   if r.check_id in referential and r.status == 'fail'
                   and r.severity == 'error']
    return score, message, missing


_SEVERITY_ORDER = ('error', 'warning', 'info')
_STATUS_ORDER = ('pass', 'fail', 'skip')


def _validation_summary(results):
    """{severity: {status: count}} over validator results, severities and
    statuses in their canonical order (unknown ones after, sorted). Reported
    beside is_valid instead of a semantic-check criterion: scoring the pass
    fraction rewarded deleting data (a failing check turns into a skip)."""
    counts = {}
    for r in results:
        severity, status = getattr(r, 'severity', None), getattr(r, 'status', None)
        counts.setdefault(str(severity), {}).setdefault(str(status), 0)
        counts[str(severity)][str(status)] += 1

    def order(keys, canonical):
        return ([k for k in canonical if k in keys]
                + sorted(k for k in keys if k not in canonical))

    return {sev: {st: counts[sev][st] for st in order(counts[sev], _STATUS_ORDER)}
            for sev in order(counts, _SEVERITY_ORDER)}


def _bare_design_results(sc):
    """Locators of the bare numeric design results (no inline units) of a
    legacy-shape document: unitless quantities for CMP-INT-04 (R2.10)."""
    return [f'{label}.design_results.{key}'
            for unit, label in zip(sc.units, sc.unit_labels)
            for key, value in _as_dict(unit.get('design_results')).items()
            if not isinstance(value, dict) and _num(value) is not None]


def _score_int_04(sc):  # CMP-INT-04
    inline = list(_inline_quantities(sc.doc))
    if inline:
        # Legacy branch: every inline {value, units} quantity, plus every
        # bare numeric design result, which carries no units at all.
        missing = [f'{path}.units' for path, q in inline if not _filled(q.get('units'))]
        missing += [f'{path} (bare number, no units)'
                    for path in _bare_design_results(sc)]
        total = len(inline) + len(_bare_design_results(sc))
        ok = total - len(missing)
        return (ok / total, f'legacy inline shape: {ok} of {total} '
                f'quantities carry units', missing)
    result = sc.check_result('QU-01')
    if result is None or result.status == 'skip':
        return None, 'QU-01 not evaluated', []
    if result.status == 'pass':
        return 1.0, 'every quantity field has a quantity_units_global alias', []
    return 0.0, 'QU-01 fails', [_clip(result.message)]

#%% Criterion registry (id -> scorer; order and ids must match criteria.yaml)

_CRITERIA = {
    'CMP-THM-01': _score_thm_01, 'CMP-THM-02': _score_thm_02,
    'CMP-THM-03': _score_thm_03, 'CMP-THM-04': _score_thm_04,
    'CMP-THM-05': _score_thm_05, 'CMP-THM-06': _score_thm_06,
    'CMP-THM-07': _score_thm_07,
    'CMP-TOP-01': _score_top_01, 'CMP-TOP-02': _score_top_02,
    'CMP-FEED-01': _score_feed_01, 'CMP-FEED-02': _score_feed_02,
    'CMP-UOP-01': _score_uop_01, 'CMP-UOP-02': _score_uop_02,
    'CMP-UOP-03': _score_uop_03, 'CMP-UOP-04': _score_uop_04,
    'CMP-UOP-05': _score_uop_05, 'CMP-UOP-06': _score_uop_06,
    'CMP-RXN-01': _score_rxn_01, 'CMP-RXN-02': _score_rxn_02,
    'CMP-RXN-03': _score_rxn_03, 'CMP-RXN-04': _score_rxn_04,
    'CMP-COST-01': _score_cost_01, 'CMP-COST-02': _score_cost_02,
    'CMP-COST-03': _score_cost_03, 'CMP-COST-04': _score_cost_04,
    'CMP-COST-05': _score_cost_05, 'CMP-COST-06': _score_cost_06,
    'CMP-COST-07': _score_cost_07,
    'CMP-UTIL-01': _score_util_01, 'CMP-UTIL-02': _score_util_02,
    'CMP-UTIL-03': _score_util_03, 'CMP-UTIL-04': _score_util_04,
    'CMP-UTIL-05': _score_util_05,
    'CMP-SYS-01': _score_sys_01, 'CMP-SYS-02': _score_sys_02,
    'CMP-SYS-03': _score_sys_03, 'CMP-SYS-04': _score_sys_04,
    'CMP-INT-01': _score_int_01, 'CMP-INT-02': _score_int_02,
    'CMP-INT-04': _score_int_04,
}

#%% Driver + aggregation

def _run_criterion(criterion_id, sc):
    """(score or None, message, missing) of one criterion. A crashing or
    unregistered scorer scores 0 with the cause in the message -- one broken
    criterion never sinks the run."""
    scorer = _CRITERIA.get(criterion_id)
    if scorer is None:
        return 0.0, f'no scorer registered for {criterion_id}', []
    try:
        score, message, missing = scorer(sc)
    except Exception as exc:
        return 0.0, f'criterion raised {type(exc).__name__}: {exc}', []
    if score is None:
        return None, message, list(missing)
    return max(0.0, min(1.0, float(score))), message, list(missing)


def _evaluate_criteria(sc, criteria, version):
    """CriterionResult for every registry criterion, in registry order."""
    out = []
    for criterion_id, entry in criteria.items():
        score, message, missing = _run_criterion(criterion_id, sc)
        out.append(CriterionResult(
            criterion_id, entry['category'], entry['criticality'],
            entry['weight'],
            _is_representable(entry['representable_since'], version),
            'not_applicable' if score is None else 'scored', score, message,
            missing))
    return out


def _weighted_mean(results):
    """Sum(w * s) / Sum(w) over scored results, or None when none scored."""
    scored = [r for r in results if r.status == 'scored']
    weight = sum(r.weight for r in scored)
    return sum(r.weight * r.score for r in scored) / weight if weight else None


def _group_scores(results, field, keys):
    """{key: weighted mean of the results whose `field` equals key}."""
    return {k: _weighted_mean([r for r in results if getattr(r, field) == k])
            for k in keys}


def _recipe_route(sc):
    """The separate re-execution route: embedded load script + pinned
    environment present, their digests valid (MET-07), 'reproducible'
    declared."""
    repro = _as_dict(sc.metadata.get('reproducibility'))
    present = all(_nonempty_str(_as_dict(repro.get(k)).get('content'))
                  for k in ('environment', 'load_script'))
    met07 = sc.check_result('MET-07')
    digests = (None if met07 is None or met07.status == 'skip'
               else met07.status == 'pass')
    tags = sc.metadata.get('tags')
    return {'present': present, 'digests_valid': digests,
            'reproducible_declared': isinstance(tags, list)
            and 'reproducible' in tags}


def _run_validator(doc, schema):
    """The validator's results for `doc`: schema gate, every check, XREF-01
    and TAG-01 -- exactly what validate_flowsheet_against_SFF computes, in
    memory. The validator tolerates malformed sections, but a pathological
    document (e.g. a non-object metadata) can still make its context or the
    TAG-01 post-pass raise; that becomes an error-severity 'VALIDATOR' fail
    (after the always-safe schema gate) instead of an exception."""
    validator = _validator()
    try:
        vctx, results = validator._run_all_checks(doc, schema)
        results.append(validator._tag_gate(vctx, results))
    except Exception as exc:
        results = [validator._schema_gate(doc, schema), validator.CheckResult(
            'VALIDATOR', 'error', 'fail',
            f'validator raised {type(exc).__name__}: {exc}', '<root>')]
    return results


def _read_flowsheet(flowsheet):
    """A parsed document from a dict (used as-is, never mutated) or a path."""
    if isinstance(flowsheet, dict):
        return flowsheet
    if isinstance(flowsheet, (str, os.PathLike)):
        with open(flowsheet, 'r', encoding='utf-8') as f:
            return json.load(f)
    raise TypeError(f'flowsheet must be a path or a dict, not '
                    f'{type(flowsheet).__name__}')


def score_flowsheet_completeness(flowsheet, schema_file=None):
    """
    Score how completely an SFF flowsheet states what BioSTEAM needs to
    rebuild, simulate, cost and put it through TEA.

    Runs the two-layer validator in memory (schema gate, every semantic check,
    XREF-01 and TAG-01), then every criterion in ``criteria.yaml``. Scores are
    declarative: they measure what the file states (or lets a reader derive),
    never what a re-simulation would produce. The embedded reproducibility
    recipe is reported separately in ``recipe``.

    Parameters
    ----------
    flowsheet : str, os.PathLike or dict
        Path to an SFF JSON file, or an already-parsed document (not mutated).
    schema_file : str or pathlib.Path, optional
        SFF JSON Schema for the validator gate. Defaults to the schema shipped
        with this package.

    Returns
    -------
    CompletenessReport
        ``score`` (weighted mean over every scored criterion),
        ``representable_score`` (the same over criteria the file's
        ``sff_version`` can state; an unparseable or missing version counts as
        the schema's version), per-category and per-criticality scores (None
        when nothing in the group was scored), one :class:`CriterionResult`
        per criterion, the file's raw ``sff_version``, the validator verdict
        (``is_valid``), its results counted as ``validation_summary``
        (``{severity: {status: count}}``) and listed in full, and the
        ``recipe`` route summary. Neither the validator summary nor the
        recipe is folded into the score.

    Raises
    ------
    OSError, json.JSONDecodeError
        Only when the file (or schema) cannot be read or is not JSON; malformed
        SFF content never raises -- a criterion that crashes scores 0 with the
        exception text in its message.
    TypeError
        If `flowsheet` is neither a path nor a dict.
    """
    doc = _read_flowsheet(flowsheet)
    with open(schema_file or _validator()._SCHEMA_FILE, 'r',
              encoding='utf-8') as f:
        schema = json.load(f)
    results = _run_validator(doc, schema)
    is_valid = not any(r.status == 'fail' and r.severity == 'error'
                       for r in results)

    sc = _ScoringContext(doc, results)
    raw_version = sc.metadata.get('sff_version')
    version = _version_tuple(raw_version) or _version_tuple(
        schema.get('version') if isinstance(schema, dict) else None)
    registry = load_completeness_criteria()
    criteria = _evaluate_criteria(sc, registry['criteria'], version)
    return CompletenessReport(
        score=_weighted_mean(criteria),
        representable_score=_weighted_mean([c for c in criteria
                                            if c.representable]),
        category_scores=_group_scores(criteria, 'category',
                                      registry['categories']),
        criticality_scores=_group_scores(criteria, 'criticality', CRITICALITIES),
        criteria=criteria, sff_version=raw_version, is_valid=is_valid,
        validation_summary=_validation_summary(results),
        validation_results=results, recipe=_recipe_route(sc))

#%% Report formatting + CLI

def _fmt(score):
    return 'n/a' if score is None else f'{score:.3f}'


def _fmt_recipe(recipe):
    if not recipe.get('present'):
        return 'absent (no embedded load script + environment)'
    digests = {True: 'digests valid', False: 'DIGEST MISMATCH',
               None: 'digests unchecked'}[recipe.get('digests_valid')]
    declared = "'reproducible' declared" if recipe.get('reproducible_declared') \
        else "'reproducible' not declared"
    return f'embedded load script + environment; {digests}; {declared}'


def _fmt_validation(summary):
    """'error 30 pass / 1 fail; warning 4 pass' from a validation summary;
    'no validator results' when empty."""
    if not summary:
        return 'no validator results'
    return '; '.join(f'{severity} ' + ' / '.join(f'{n} {status}'
                                                  for status, n in counts.items())
                     for severity, counts in summary.items())


def format_completeness_report(report, max_missing=5):
    """
    Render a :class:`CompletenessReport` as a plain-text table.

    Parameters
    ----------
    report : CompletenessReport
        As returned by :func:`score_flowsheet_completeness`.
    max_missing : int, default 5
        Missing-information locators listed per criterion; the rest are
        summarized as a count.

    Returns
    -------
    str
        Header (scores, validity, validator summary, recipe route),
        per-category and
        per-criticality scores, then one row per criterion followed by its
        first `max_missing` missing locators.
    """
    titles = load_completeness_criteria()['categories']
    version = report.sff_version if report.sff_version is not None else '(none)'
    lines = [
        'SFF completeness report',
        f'  sff_version         : {version}   (valid: '
        f"{'yes' if report.is_valid else 'no'})",
        f'  validator checks    : {_fmt_validation(report.validation_summary)}',
        f'  overall score       : {_fmt(report.score)}   (all scored criteria)',
        f'  representable score : {_fmt(report.representable_score)}   '
        f'(criteria this sff_version can state)',
        f'  recipe route        : {_fmt_recipe(report.recipe)}',
        '', '  by category:']
    lines += [f'    {key:<10} {_fmt(score):>5}  {titles.get(key, "")}'
              for key, score in report.category_scores.items()]
    lines.append('  by criticality: ' + '  '.join(
        f'{key} {_fmt(score)}' for key, score in report.criticality_scores.items()))
    lines += ['', f"  {'criterion':<11} {'wt':>3} {'crit':<4} {'rep':<3} "
                  f"{'score':>5}  message"]
    for r in report.criteria:
        lines.append(f"  {r.criterion_id:<11} {r.weight:>3g} "
                     f"{r.criticality[0].upper():<4} "
                     f"{'yes' if r.representable else 'no':<3} "
                     f"{_fmt(r.score):>5}  {r.message}")
        lines += [f'      - {_clip(m, 150)}' for m in r.missing[:max(max_missing, 0)]]
        if len(r.missing) > max(max_missing, 0):
            lines.append(f'      ... and {len(r.missing) - max(max_missing, 0)} more')
    return '\n'.join(lines)


def _report_to_jsonable(report):
    """A JSON-serializable dict of a CompletenessReport."""
    out = report._asdict()
    out['criteria'] = [r._asdict() for r in report.criteria]
    out['validation_results'] = [r._asdict() if hasattr(r, '_asdict') else r
                                 for r in report.validation_results]
    return out


def main(argv=None):
    """CLI: print the completeness report of one SFF file (``--json`` for the
    full machine-readable report)."""
    parser = argparse.ArgumentParser(
        prog='python pisces_sff/completeness/_completeness.py',
        description='Score how completely an SFF flowsheet states what '
                    'BioSTEAM needs to rebuild it.')
    parser.add_argument('file', help='SFF JSON file')
    parser.add_argument('--json', action='store_true',
                        help='print the full report as JSON')
    parser.add_argument('--max-missing', type=int, default=5,
                        help='missing locators listed per criterion (text mode)')
    args = parser.parse_args(argv)
    try:  # never die on a console that cannot encode a chemical/unit name
        sys.stdout.reconfigure(errors='backslashreplace')
    except AttributeError:
        pass  # a redirected non-console stream (e.g. io.StringIO in tests)
    report = score_flowsheet_completeness(args.file)
    if args.json:
        print(json.dumps(_report_to_jsonable(report), indent=2))
    else:
        print(format_completeness_report(report, max_missing=args.max_missing))
    return 0


if __name__ == '__main__':
    sys.exit(main())
