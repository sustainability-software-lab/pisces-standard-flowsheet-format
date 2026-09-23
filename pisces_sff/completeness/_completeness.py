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
# file's sff_version; recipe: the separate re-execution route (embedded load
# script + pinned environment), deliberately NOT folded into the score.
CompletenessReport = namedtuple(
    'CompletenessReport',
    'score representable_score category_scores criticality_scores criteria '
    'sff_version is_valid validation_results recipe')

# Credit for information the file does not state but that is derivable from
# other fields (e.g. an HXutility outlet temperature read off its outlet stream).
DERIVED_CREDIT = 0.75

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

#%% Criteria registry (criteria.yaml)

_CRITERIA_CACHE = None
_TOP_KEYS = frozenset({'categories', 'criteria', 'exclusions'})
_CRITERION_KEYS = frozenset({'id', 'category', 'title', 'weight', 'criticality',
                             'representable_since', 'rationale', 'sff_paths'})
_EXCLUSION_KEYS = frozenset({'id', 'kind', 'title', 'rationale'})
_EXCLUSION_KINDS = ('not_capturable', 'derived')
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
    """Numeric mol_fraction (else mass_fraction) of a composition entry."""
    if not isinstance(entry, dict):
        return None
    x = _num(entry.get('mol_fraction'))
    return x if x is not None else _num(entry.get('mass_fraction'))


def _composition_ok(composition):
    """Non-empty composition list whose every entry has a numeric fraction."""
    return (isinstance(composition, list) and bool(composition)
            and all(_entry_fraction(e) is not None for e in composition))


def _phases_ok(phases):
    """Every phase states a numeric total_molar_flow and a complete
    composition; a zero-flow phase may be empty (the exporter serializes a
    phase with F_mol <= ZERO_FLOW as empty -- see STR-13)."""
    if not isinstance(phases, dict) or not phases:
        return False
    for phase in phases.values():
        flow = _num(phase.get('total_molar_flow')) \
            if isinstance(phase, dict) else None
        if flow is None:
            return False
        if abs(flow) > ZERO_FLOW and not _composition_ok(phase.get('composition')):
            return False
    return True


def _total_flow(sp):
    """First numeric total flow (mass, else molar) of a stream_properties."""
    for key in ('total_mass_flow', 'total_molar_flow'):
        value = _num(sp.get(key))
        if value is not None:
            return value
    return None


def _stream_state(stream):
    """Booleans {T, P, flow, composition}: which parts of a stream's state are
    stated. Composition accepts the per-phase shape (0.0.9+) or the legacy
    flat ``stream_properties.composition``; a zero-flow stream satisfies it.
    Missing stream_properties -> all False."""
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    total = _total_flow(sp)
    composition = ((total is not None and abs(total) <= ZERO_FLOW)
                   or _phases_ok(sp.get('phases'))
                   or _composition_ok(sp.get('composition')))
    return {'T': _num(sp.get('temperature')) is not None,
            'P': _num(sp.get('pressure')) is not None,
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
    """{component: molar flow} of a stream, or None when not computable (no
    molar flow basis or incomplete composition). A zero-flow stream -> {}."""
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    total = _total_flow(sp)
    if total is not None and abs(total) <= ZERO_FLOW:
        return {}
    phases = sp.get('phases')
    if isinstance(phases, dict) and phases:
        if not _phases_ok(phases):
            return None
        blocks = [(_num(p.get('total_molar_flow')), _as_list(p.get('composition')))
                  for p in phases.values()]
    elif _composition_ok(sp.get('composition')) \
            and _num(sp.get('total_molar_flow')) is not None:
        blocks = [(_num(sp.get('total_molar_flow')), sp['composition'])]
    else:
        return None
    flows = {}
    for molar_flow, composition in blocks:
        for entry in _dicts(composition):
            name, x = entry.get('component_name'), _num(entry.get('mol_fraction'))
            if _nonempty_str(name) and x is not None:
                flows[name] = flows.get(name, 0.0) + molar_flow * x
    return flows


def _equation_species(equation, declared=frozenset()):
    """Species named in a reaction equation 'A + 2 B -> 3 C'. A leading
    coefficient must be separated by whitespace ('3 HP'); a glued one ('2H2O')
    is stripped only when that yields a declared chemical id, so names that
    start with a digit survive."""
    if not isinstance(equation, str) or '->' not in equation:
        return []
    names = []
    for side in equation.split('->', 1):
        for term in side.split('+'):
            term = term.strip()
            if not term:
                continue
            name = re.sub(r'^\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s+', '', term)
            if name not in declared:
                glued = re.sub(r'^\d+(?:\.\d+)?', '', name).strip()
                name = glued if glued in declared else name
            names.append(name)
    return names


def _int_or_none(x):
    if isinstance(x, bool):
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _stoichiometry_species(stoichiometry, declared, chem_by_index):
    """Species with a nonzero coefficient in a stoichiometry: an object keyed
    by chemical id or index, or an index-ordered array. Unresolvable indices
    come back as 'index <n>'."""
    if isinstance(stoichiometry, dict):
        items = stoichiometry.items()
    elif isinstance(stoichiometry, list):
        items = enumerate(stoichiometry)
    else:
        return []
    names = []
    for key, value in items:
        if not _num(value):
            continue
        if isinstance(key, str) and key in declared:
            names.append(key)
            continue
        index = _int_or_none(key)
        names.append(chem_by_index.get(index, f'index {key}')
                     if index is not None else str(key))
    return names


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

#%% Unit class resolution

# "Class on <source URL>": the exporter's design_simulation_method convention.
_DSM_CLASS_RE = re.compile(r'^\s*([A-Za-z_]\w*)\s+on\s+\S+')

FAMILIES = ('mixer', 'splitter', 'flash', 'hx_utility', 'hx_process', 'pump',
            'valve', 'compressor', 'turbine', 'distillation', 'evaporator',
            'reactor', 'tank', 'facility', 'solids_handling')

# Curated class -> family, consulted before keyword matching. Only classes the
# keywords would miss or misplace are listed (keywords handle the rest).
_CLASS_FAMILY = {
    'CleaningSystem': 'splitter',       # corn biorefinery: a Splitter subclass
    'CrushingMill': 'splitter',         # splits juice from bagasse ("mill")
    'JetCooker': 'hx_utility',          # direct steam heating to a set T
    'EnzymeTreatment': 'reactor',       # held at T for tau
    'FluidizedCatalyticCracking': 'reactor',
    'ReactivePhaseStage': 'reactor',
    'HXutility': 'hx_utility',
    'HXutilities': 'hx_utility',
    'HXprocess': 'hx_process',
    'SteamMixer': 'mixer',
    'EnzymeHydrolysateMixer': 'mixer',  # a Mixer subclass ("hydroly" misleads)
    'BiogasUpgrading': 'splitter',      # CO2 removal from biogas
}

# Ordered (family, pattern): the first family whose pattern matches the
# normalized text wins, so specific families precede generic ones (facility
# before tank for "fire water tank"; splitter before solids_handling for
# "screw press"; tank before mixer for "mix tank").
_FAMILY_KEYWORDS = tuple((family, re.compile(pattern)) for family, pattern in (
    ('facility', r'boiler|turbogenerator|cooling tower|chilled water|'
                 r'process water|waste ?water|\bcip\b|cip ?package|'
                 r'fire ?water|air distribution|heat exchanger network|'
                 r'facilit'),
    ('hx_process', r'hx ?process|interchanger|economi[sz]er'),
    ('hx_utility', r'hx ?utilit|heat exchanger|heater|cooler|condenser|'
                   r'reboiler'),
    ('pump', r'pump'),
    ('valve', r'valve'),
    ('compressor', r'compress|blower'),
    ('turbine', r'turbine'),
    ('evaporator', r'evaporator'),
    ('flash', r'flash'),
    ('reactor', r'reactor|ferment|saccharif|digest|hydroly|\bssf\b|combust|'
                r'liquefaction|oxidi[sz]er|\brx\b|\bmbr\b|seed train'),
    ('splitter', r'splitter|\bsplit|centrifuge|filter|\bpress\b|screen|'
                 r'separator|decanter|dryer|settler|sieve|scrubber|clarifier|'
                 r'adsorp|absor|membrane|cyclone|osmosis|thickener|'
                 r'crystalli'),
    ('distillation', r'distillation|column|stripper|rectifier'),
    ('tank', r'tank|storage|vessel|hopper|\bsilo'),
    ('solids_handling', r'convey|\bmill|crusher|grinder|screw|grain handling|'
                        r'hammer|handling|shredder|feeder'),
    ('mixer', r'mixer|\bmix\b|blend'),
))


def _normalized_texts(text):
    """(raw lowercase, de-camelCased spaced lowercase) variants of a name, so
    'HXutility' and 'GrainHandling' both match their keywords."""
    spaced = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ',
                    text)
    spaced = re.sub(r'[\s_\-/]+', ' ', spaced).strip().lower()
    return text.lower(), spaced


def _keyword_family(text):
    """Family whose keyword pattern matches `text` (a unit_type or a class
    name), or None."""
    if not _nonempty_str(text):
        return None
    variants = _normalized_texts(text)
    for family, pattern in _FAMILY_KEYWORDS:
        if any(pattern.search(v) for v in variants):
            return family
    return None


def _class_family(class_name):
    """Family of a known class: curated table, then keywords on its name."""
    return _CLASS_FAMILY.get(class_name) or _keyword_family(class_name)


def _unit_class(unit, known_classes=frozenset()):
    """(class_name or None, family or None, confidence) for one unit:
    1.0 from design_simulation_method "Class on <source>" or a unit_type that
    exactly names a known class; 0.5 when a unit_type keyword suggests only a
    family; else (None, None, 0.0)."""
    unit = _as_dict(unit)
    dsm = unit.get('design_simulation_method')
    match = _DSM_CLASS_RE.match(dsm) if isinstance(dsm, str) else None
    if match:
        name = match.group(1)
        return name, _class_family(name), 1.0
    unit_type = unit.get('unit_type')
    unit_type = unit_type.strip() if isinstance(unit_type, str) else None
    if unit_type and (unit_type in known_classes or unit_type in _CLASS_FAMILY):
        return unit_type, _class_family(unit_type), 1.0
    family = _keyword_family(unit_type)
    if family:
        return None, family, 0.5
    return None, None, 0.0

#%% Essential operating-specification rules (CMP-UOP-02 / CMP-UOP-03)

# One requirement: `n` of `keys` must be explicit design_input_specs. derive
# maps a key to the conditions (all must hold) under which its value is
# derivable from other data; see _condition_holds.
_Req = namedtuple('_Req', 'n keys derive')

_P_FROM_OUTLET = _Req(1, ('P',), {'P': ('outlet_P',)})

_FAMILY_RULES = {
    'mixer': (), 'facility': (), 'solids_handling': (),
    'splitter': (_Req(1, ('split',), {'split': ('all_ports_state',)}),),
    'flash': (_Req(2, ('T', 'P', 'V', 'Q', 'y', 'x'),
                   {'T': ('outlet_T',), 'P': ('outlet_P',),
                    'V': ('all_ports_state',)}),),
    'hx_utility': (_Req(1, ('T', 'V', 'H'), {'T': ('outlet_T',)}),),
    'hx_process': (_Req(1, ('dT', 'T_lim0', 'T_lim1', 'H_lim0', 'H_lim1'),
                        {'T_lim0': ('outlet_T',)}),),
    'pump': (_Req(1, ('P', 'dP_design'), {'P': ('outlet_P',)}),),
    'valve': (_P_FROM_OUTLET,),
    'compressor': (_P_FROM_OUTLET, _Req(1, ('eta',), {})),
    'turbine': (_P_FROM_OUTLET, _Req(1, ('eta',), {})),
    'distillation': (
        _Req(1, ('LHK',), {}),
        _P_FROM_OUTLET,
        _Req(1, ('Lr', 'y_top'), {'Lr': ('all_ports_state', 'explicit:LHK')}),
        _Req(1, ('Hr', 'x_bot'), {'Hr': ('all_ports_state', 'explicit:LHK')}),
        _Req(1, ('k', 'Rmin'), {'k': ('design_result:Reflux',)})),
    'evaporator': (_P_FROM_OUTLET,
                   _Req(1, ('V',), {'V': ('all_ports_state',)})),
    'reactor': (_Req(1, ('T',), {'T': ('outlet_T',)}), _Req(1, ('tau',), {})),
    'tank': (_Req(1, ('tau',), {}),),
}

# Families that need no operating specification (excluded from CMP-UOP-02).
_UNSPECIFIED_FAMILIES = frozenset(f for f, rules in _FAMILY_RULES.items()
                                  if not rules)


def _explicit_keys(unit):
    """design_input_specs keys stated with a non-null value."""
    specs = _as_dict(_as_dict(unit).get('design_input_specs'))
    return {k for k, v in specs.items() if v is not None}


def _condition_holds(condition, sc, unit, explicit):
    """Evaluate one derivation condition for `unit` (see _FAMILY_RULES)."""
    uid = unit.get('id')
    if condition == 'outlet_T':
        return sc.all_outlets_have(uid, 'T')
    if condition == 'outlet_P':
        return sc.all_outlets_have(uid, 'P')
    if condition == 'all_ports_state':
        return sc.all_ports_full(uid)
    if condition.startswith('explicit:'):
        return condition.split(':', 1)[1] in explicit
    if condition.startswith('design_result:'):
        name = condition.split(':', 1)[1]
        return _as_dict(unit.get('design_results')).get(name) is not None
    raise ValueError(f'unknown derivation condition {condition!r}')


def _requirement_score(req, sc, unit, explicit):
    """Explicit keys earn 1 each, derivable keys DERIVED_CREDIT, up to n:
    (min(e, n) + DERIVED_CREDIT * min(d, n - min(e, n))) / n."""
    e = min(sum(k in explicit for k in req.keys), req.n)
    d = sum(1 for key, conditions in req.derive.items()
            if key not in explicit
            and all(_condition_holds(c, sc, unit, explicit) for c in conditions))
    return (e + DERIVED_CREDIT * min(d, req.n - e)) / req.n


def _requirement_locator(label, req, score):
    count = 'one' if req.n == 1 else str(req.n)
    note = ' (only derivable from stream data)' if score > 0 else ''
    return f"{label}.design_input_specs: {count} of {'|'.join(req.keys)}{note}"


def _unit_spec_score(sc, position):
    """(score, missing) of one unit against its family's essential rules;
    an unknown family scores 0 (no way to know what to specify)."""
    unit, label = sc.units[position], sc.unit_labels[position]
    family = sc.unit_classes[position][1]
    rules = _FAMILY_RULES.get(family)
    if rules is None:
        return 0.0, [f'{label}: unit family unknown, so its essential '
                     f'specifications cannot be determined']
    explicit = _explicit_keys(unit)
    scores, missing = [], []
    for req in rules:
        score = _requirement_score(req, sc, unit, explicit)
        scores.append(score)
        if score < 1.0:
            missing.append(_requirement_locator(label, req, score))
    return _mean(scores), missing


def _excused_params(family, explicit):
    """Registry params excused for CMP-UOP-03: the unstated alternatives of
    every family requirement already satisfied explicitly (an HXutility with T
    set excuses V and H)."""
    excused = set()
    for req in _FAMILY_RULES.get(family, ()):
        if sum(k in explicit for k in req.keys) >= req.n:
            excused.update(k for k in req.keys if k not in explicit)
    return excused

#%% Scoring context

def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def _dicts(x):
    return [e for e in _as_list(x) if isinstance(e, dict)]


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
        self.units = _dicts(self.doc.get('units'))
        self.streams = _dicts(self.doc.get('streams'))
        self.chemicals = _dicts(self.doc.get('chemicals'))
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

        known = frozenset(self.registry) | frozenset(_CLASS_FAMILY)
        self.unit_classes = [_unit_class(u, known) for u in self.units]
        self.reactions = [(i, j, r) for i, u in enumerate(self.units)
                          for j, r in enumerate(_as_list(u.get('reactions')))
                          if isinstance(r, dict)]

        utilities = _as_dict(self.doc.get('utilities'))
        self.agents = [(group, agent, _label(f'utilities.{group}', agent, i))
                       for group in ('heat_utilities', 'power_utilities',
                                     'other_utilities')
                       for i, agent in enumerate(_dicts(utilities.get(group)))]

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


def _score_thm_02(sc):  # CMP-THM-02
    if not sc.chemicals:
        return None, 'no chemicals declared', []
    scores, missing = [], []
    for chem, label in zip(sc.chemicals, sc.chem_labels):
        if _filled(chem.get('registry_id')):
            scores.append(1.0)
        elif _filled(chem.get('formula')):
            scores.append(0.5)
            missing.append(f'{label}.registry_id (formula only)')
        else:
            scores.append(0.0)
            missing.append(f'{label}.registry_id (no formula either)')
    n_id = scores.count(1.0)
    return (_mean(scores), f'{n_id} of {len(scores)} chemical(s) carry a '
            f'registry id', missing)


def _score_thm_03(sc):  # CMP-THM-03
    if not sc.chemicals:
        return None, 'no chemicals declared', []
    derive = _validator()._molar_mass_from_formula  # same rule as _Context.molar_mass
    ok, missing = 0, []
    for chem, label in zip(sc.chemicals, sc.chem_labels):
        declared = _num(chem.get('molar_mass'))
        formula = chem.get('formula')
        if (declared is not None and declared > 0) or (
                _nonempty_str(formula) and derive(formula) is not None):
            ok += 1
        else:
            missing.append(f'{label}.molar_mass (or a parseable formula)')
    return (ok / len(sc.chemicals),
            f'{ok} of {len(sc.chemicals)} chemical(s) have a molar mass', missing)


def _score_thm_04(sc):  # CMP-THM-04
    if not sc.chemicals:
        return None, 'no chemicals declared', []
    phases = _chemical_phases(sc)
    derived, missing = 0, []
    for chem, label in zip(sc.chemicals, sc.chem_labels):
        seen = phases.get(chem.get('id'), set()) if _nonempty_str(chem.get('id')) else set()
        if len(seen) >= 2 or seen == {'s'}:
            derived += 1
        else:
            shown = ','.join(sorted(seen)) or 'none'
            missing.append(f'{label}: phase behaviour (locked state / '
                           f'phase_ref) not stated; stream phases seen: {shown}')
    return (DERIVED_CREDIT * derived / len(sc.chemicals),
            f'not representable; derivable from stream data for {derived} of '
            f'{len(sc.chemicals)} chemical(s)', missing)


def _score_thm_05(sc):  # CMP-THM-05
    if not sc.chemicals:
        return None, 'no chemicals declared', []
    lacking = [label for chem, label in zip(sc.chemicals, sc.chem_labels)
               if not _filled(chem.get('registry_id'))]
    if not lacking:
        return None, 'every chemical carries a registry id (database-resolvable)', []
    missing = [f'{label}: property data (V, Cn, H/S reference, Hf, '
               f'Psat/Tb/Hvap, LHV) not representable' for label in lacking]
    return (0.0, f'{len(lacking)} chemical(s) without a registry id need '
            f'property data no SFF field can state', missing)


_TPP_KEYS = ('mixture', 'gamma', 'phi', 'PCF')


def _score_thm_06(sc):  # CMP-THM-06
    if not sc.units:
        return None, 'no units', []
    scores, missing = [], []
    for unit, label in zip(sc.units, sc.unit_labels):
        tpp = _as_dict(unit.get('thermo_property_package'))
        absent = [k for k in _TPP_KEYS if not _filled(tpp.get(k))]
        scores.append(1.0 - len(absent) / len(_TPP_KEYS))
        missing.extend(f'{label}.thermo_property_package.{k}' for k in absent)
    full = scores.count(1.0)
    return (_mean(scores), f'{full} of {len(scores)} unit(s) state a full '
            f'property package', missing)


def _score_thm_07(sc):  # CMP-THM-07
    declaring = [(unit, label) for unit, label in zip(sc.units, sc.unit_labels)
                 if any(_filled(_as_dict(unit.get('thermo_property_package')).get(k))
                        for k in _TPP_KEYS)]
    packages = {tuple(str(_as_dict(u.get('thermo_property_package')).get(k))
                      for k in _TPP_KEYS) for u, _l in declaring}
    if len(packages) <= 1:
        return None, 'units do not declare more than one property package', []
    missing = [f'{label}.thermo_property_package.id' for unit, label in declaring
               if not _filled(_as_dict(unit.get('thermo_property_package')).get('id'))]
    ok = len(declaring) - len(missing)
    return (ok / len(declaring), f'{len(packages)} distinct packages; {ok} of '
            f'{len(declaring)} unit(s) name their chemical set', missing)

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


def _score_top_02(sc):  # CMP-TOP-02
    if not sc.units:
        return None, 'no units', []
    credits, missing = [], []
    for unit, label in zip(sc.units, sc.unit_labels):
        uid = unit.get('id')
        for side, index in (('ins', sc.unit_inlets), ('outs', sc.unit_outlets)):
            n = len(index.get(uid, [])) if isinstance(uid, str) else 0
            credits.append(1.0 if n <= 1 else 0.0)
            if n > 1:
                missing.append(f'{label}.{side}: order of {n} streams not '
                               f'representable')
    ok = int(sum(credits))
    return (_mean(credits), f'not representable; {ok} of {len(credits)} '
            f'unit port side(s) unambiguous (<=1 stream)', missing)

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


def _score_feed_01(sc):  # CMP-FEED-01
    if not sc.feeds:
        return (0.0, 'no feed streams: nothing defines the degrees of freedom',
                ['streams: no feed (source_unit_id "None", sink a unit)'])
    score, missing = _state_missing(sc, sc.feeds)
    full = sum(1 for i in sc.feeds if all(sc.stream_states[i].values()))
    return score, f'{full} of {len(sc.feeds)} feed(s) fully stated', missing


def _is_empty_stream(stream):
    """True when a stream states a total flow that is numerically zero."""
    total = _total_flow(_as_dict(stream.get('stream_properties')))
    return total is not None and abs(total) <= ZERO_FLOW


def _score_feed_02(sc):  # CMP-FEED-02
    positions = [i for i in sc.feeds + sc.outlets
                 if not _is_empty_stream(sc.streams[i])]
    if not positions:
        return None, 'no non-empty boundary streams', []
    missing = [f'{sc.stream_labels[i]}.price' for i in positions
               if _num(sc.streams[i].get('price')) is None]
    ok = len(positions) - len(missing)
    return (ok / len(positions), f'{ok} of {len(positions)} non-empty boundary '
            f'stream(s) priced', missing)

#%% Criteria -- unit operations

def _score_uop_01(sc):  # CMP-UOP-01
    if not sc.units:
        return None, 'no units', []
    missing = []
    for (name, family, confidence), unit, label in zip(
            sc.unit_classes, sc.units, sc.unit_labels):
        if confidence == 0.5:
            missing.append(f"{label}.design_simulation_method: class not "
                           f"stated (family '{family}' inferred from unit_type)")
        elif confidence == 0.0:
            missing.append(f"{label}: class unresolved (unit_type "
                           f"{unit.get('unit_type')!r} names no known class or "
                           f"family)")
    exact = sum(1 for c in sc.unit_classes if c[2] == 1.0)
    return (_mean(c[2] for c in sc.unit_classes),
            f'{exact} of {len(sc.units)} unit(s) identify an exact class', missing)


def _score_uop_02(sc):  # CMP-UOP-02
    scores, missing = [], []
    for position, (_name, family, _conf) in enumerate(sc.unit_classes):
        if family in _UNSPECIFIED_FAMILIES:
            continue
        score, unit_missing = _unit_spec_score(sc, position)
        scores.append(score)
        missing.extend(unit_missing)
    if not scores:
        return None, 'no units that need operating specifications', []
    full = scores.count(1.0)
    return (_mean(scores), f'{full} of {len(scores)} applicable unit(s) fully '
            f'specified', missing)


def _score_uop_03(sc):  # CMP-UOP-03
    if sc.registry_error:
        raise RuntimeError(f'design-spec registry unavailable: '
                           f'{sc.registry_error}')
    scores, missing = [], []
    for unit, label, (name, family, _conf) in zip(sc.units, sc.unit_labels,
                                                    sc.unit_classes):
        entry = _as_dict(sc.registry.get(name)) if name else {}
        params = list(_as_dict(entry.get('design_input_spec_params')))
        if not params:
            continue
        explicit = _explicit_keys(unit)
        needed = [p for p in params if p not in _excused_params(family, explicit)]
        absent = [p for p in needed if p not in explicit]
        scores.append(1.0 - len(absent) / len(needed) if needed else 1.0)
        missing.extend(f'{label}.design_input_specs.{p}' for p in absent)
    if not scores:
        return None, 'no unit has an exact design-spec registry class', []
    return (_mean(scores), f'{len(scores)} unit(s) with a registry class; '
            f'{len(missing)} registry param(s) unstated', missing)


def _score_uop_04(sc):  # CMP-UOP-04
    positions = sc.internal + sc.outlets
    if not positions:
        return None, 'no internal or product streams', []
    score, missing = _state_missing(sc, sorted(positions))
    full = sum(1 for i in positions if all(sc.stream_states[i].values()))
    return (score, f'{full} of {len(positions)} internal/product stream(s) '
            f'fully stated', missing)

#%% Criteria -- reactions

def _species_change(sc, unit_id):
    """Species that appear in (or vanish from) a unit's outlets relative to
    its inlets -- evidence of reaction. Empty unless the unit has inlets and
    outlets whose compositions are all computable."""
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
                return []
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


def _reactive_reason(sc, position):
    """Why a unit counts as reactive for CMP-RXN-01, or None. Facilities are
    exempt from the species-appearance evidence: their balances are not
    process balances (a process-water center or boiler discards and makes up
    species internally), so a vanishing species there is not a reaction."""
    unit = sc.units[position]
    family = sc.unit_classes[position][1]
    if family == 'reactor':
        return 'reactor family'
    if _dicts(unit.get('reactions')):
        return 'declares reactions'
    if family == 'facility':
        return None
    change = _species_change(sc, unit.get('id'))
    return ', '.join(change[:3]) if change else None


def _score_rxn_01(sc):  # CMP-RXN-01
    reactive, missing = 0, []
    for position, unit in enumerate(sc.units):
        reason = _reactive_reason(sc, position)
        if reason is None:
            continue
        reactive += 1
        if not _dicts(unit.get('reactions')):
            missing.append(f'{sc.unit_labels[position]}.reactions: none '
                           f'declared ({reason})')
    if not reactive:
        return None, 'no reactive units', []
    ok = reactive - len(missing)
    return ok / reactive, f'{ok} of {reactive} reactive unit(s) carry reactions', missing


def _score_rxn_02(sc):  # CMP-RXN-02
    if not sc.reactions:
        return None, 'no reactions', []
    scores, missing = [], []
    for ui, ri, reaction in sc.reactions:
        where = f'{sc.unit_labels[ui]}.reactions[{ri}]'
        conversion = _num(reaction.get('conversion'))
        parts = {
            'equation|stoichiometry': _filled(reaction.get('equation'))
            or _filled(reaction.get('stoichiometry')),
            'reactant': _nonempty_str(reaction.get('reactant')),
            'conversion (0..1)': conversion is not None and 0.0 <= conversion <= 1.0,
        }
        scores.append(sum(parts.values()) / len(parts))
        missing.extend(f'{where}.{k}' for k, ok in parts.items() if not ok)
    full = scores.count(1.0)
    return _mean(scores), f'{full} of {len(scores)} reaction(s) complete', missing


def _score_rxn_03(sc):  # CMP-RXN-03
    multi = [(u, label) for u, label in zip(sc.units, sc.unit_labels)
             if len(_dicts(u.get('reactions'))) >= 2]
    if not multi:
        return None, 'no unit carries two or more reactions', []
    ok, missing = 0, []
    for unit, label in multi:
        unindexed = [ri for ri, r in enumerate(_dicts(unit.get('reactions')))
                     if isinstance(r.get('index'), bool)
                     or not isinstance(r.get('index'), int)]
        ok += not unindexed
        missing.extend(f'{label}.reactions[{ri}].index' for ri in unindexed)
    return (ok / len(multi), f'{ok} of {len(multi)} multi-reaction unit(s) '
            f'index every reaction', missing)


def _score_rxn_04(sc):  # CMP-RXN-04
    labels = sorted({sc.unit_labels[ui] for ui, _ri, _r in sc.reactions})
    if not labels:
        return None, 'no reactions', []
    return (0.0, 'reaction basis (mol/wt) and reacting phase not representable',
            [f'{label}.reactions: basis (mol|wt) and phase not representable'
             for label in labels])

#%% Criteria -- costing

def _cost_items(sc):
    """(unit, label, item_id, purchase_cost) for every purchase-cost item."""
    return [(unit, label, item, cost)
            for unit, label in zip(sc.units, sc.unit_labels)
            for item, cost in _as_dict(unit.get('purchase_costs')).items()]


def _correlation(unit, item):
    return _as_dict(_as_dict(unit.get('purchase_cost_correlations')).get(item))


def _score_cost_01(sc):  # CMP-COST-01
    if not sc.units:
        return None, 'no units', []
    missing = [f'{label}.purchase_costs' for unit, label
               in zip(sc.units, sc.unit_labels)
               if not isinstance(unit.get('purchase_costs'), dict)]
    ok = len(sc.units) - len(missing)
    return (ok / len(sc.units), f'{ok} of {len(sc.units)} unit(s) record '
            f'purchase costs', missing)


def _score_cost_02(sc):  # CMP-COST-02
    items = _cost_items(sc)
    if not items:
        return None, 'no purchase-cost items', []
    missing = [f"{label}.installed_costs['{item}']"
               for unit, label, item, _cost in items
               if _num(_as_dict(unit.get('installed_costs')).get(item)) is None]
    ok = len(items) - len(missing)
    return (ok / len(items), f'{ok} of {len(items)} cost item(s) have an '
            f'installed cost', missing)


def _has_quantity_unit(value, declared):
    """A numeric design result has a unit: legacy inline units, or a string in
    quantity_units_for_design_results ('' is the dimensionless sentinel)."""
    if isinstance(value, dict):
        return isinstance(value.get('units'), str)
    return isinstance(declared, str)


def _score_cost_03(sc):  # CMP-COST-03
    if not sc.units:
        return None, 'no units', []
    scores, missing = [], []
    for unit, label in zip(sc.units, sc.unit_labels):
        results = unit.get('design_results')
        if not isinstance(results, dict):
            scores.append(0.0)
            missing.append(f'{label}.design_results')
            continue
        units_map = _as_dict(unit.get('quantity_units_for_design_results'))
        unitless = [k for k, v in results.items() if _num(v) is not None
                    and not _has_quantity_unit(v, units_map.get(k))]
        scores.append(0.5 if unitless else 1.0)
        missing.extend(f"{label}.quantity_units_for_design_results['{k}']"
                       for k in unitless)
    full = scores.count(1.0)
    return (_mean(scores), f'{full} of {len(scores)} unit(s) state design '
            f'results with units', missing)


_CORRELATION_CREDIT = {'power_law': 1.0, 'custom_function': 0.5}


def _score_cost_04(sc):  # CMP-COST-04
    items = _cost_items(sc)
    if not items:
        return None, 'no purchase-cost items', []
    scores, missing = [], []
    for unit, label, item, _cost in items:
        kind = _correlation(unit, item).get('correlation_type')
        scores.append(_CORRELATION_CREDIT.get(kind, 0.0))
        if kind != 'power_law':
            note = ' (custom_function: not re-derivable)' if kind == 'custom_function' else ''
            missing.append(f"{label}.purchase_cost_correlations['{item}']{note}")
    power = scores.count(1.0)
    return (_mean(scores), f'{power} of {len(items)} cost item(s) carry a '
            f'power-law correlation', missing)


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
    items = _cost_items(sc)
    if not items:
        return None, 'no purchase-cost items', []
    missing = ['metadata: target cost index (CEPCI) of the costs not representable']
    source = next((f"{label}.purchase_costs['{item}']" for unit, label, item, cost
                   in items if _back_derivable(unit, item, cost)), None)
    if source:
        return (DERIVED_CREDIT, f'not representable; back-derivable from '
                f'{source}', missing)
    return 0.0, 'not representable and no power-law item allows back-derivation', missing


def _score_cost_06(sc):  # CMP-COST-06
    power = [(label, item) for unit, label, item, _c in _cost_items(sc)
             if _correlation(unit, item).get('correlation_type') == 'power_law']
    if not power:
        return None, 'no power-law cost items', []
    return (0.0, 'parallel-unit count (CostItem.N) not representable',
            [f"{label}.purchase_cost_correlations['{item}']: parallel-unit "
             f"count N not representable" for label, item in power])

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
        return [] if _filled(agent.get('id')) else ['id']
    gaps = [k for k in ('temperature', 'pressure') if _num(agent.get(k)) is None]
    if not _dicts(agent.get('composition')):
        gaps.append('composition')
    return gaps


def _score_util_01(sc):  # CMP-UTIL-01
    refs = _utility_refs(sc)
    declared = {agent.get('id'): (group, agent, label)
                for group, agent, label in sc.agents
                if isinstance(agent.get('id'), str)}
    if not refs and not sc.agents:
        return None, 'no utility agents referenced or declared', []
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
    full = scores.count(1.0)
    return (_mean(scores), f'{full} of {len(scores)} referenced/declared '
            f'agent(s) resolve and are fully specified', missing)


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
    if not sc.agents:
        return None, 'no utility agents declared', []
    missing = []
    for group, agent, label in sc.agents:
        fields, locator = _PRICE_FIELDS[group]
        if not any(_num(agent.get(f)) is not None for f in fields):
            missing.append(f'{label}.{locator}')
    ok = len(sc.agents) - len(missing)
    return ok / len(sc.agents), f'{ok} of {len(sc.agents)} agent(s) priced', missing


def _score_util_03(sc):  # CMP-UTIL-03
    heat = [(agent, label) for group, agent, label in sc.agents
            if group == 'heat_utilities']
    if not heat:
        return None, 'no heat-utility agents declared', []
    scores, missing = [], []
    for agent, label in heat:
        efficiency = _num(agent.get('heat_transfer_efficiency')) is not None
        phases = [e.get('phase') for e in _dicts(agent.get('composition'))]
        condensing = bool(phases) and all(p == 'g' for p in phases)
        limit = _num(agent.get('temperature_limit')) is not None or condensing
        scores.append(0.5 * efficiency + 0.5 * limit)
        if not efficiency:
            missing.append(f'{label}.heat_transfer_efficiency')
        if not limit:
            missing.append(f'{label}.temperature_limit (not a condensing agent)')
    full = scores.count(1.0)
    return _mean(scores), f'{full} of {len(heat)} heat agent(s) fully bounded', missing


def _score_util_04(sc):  # CMP-UTIL-04
    if not sc.units:
        return None, 'no units', []
    scores, missing = [], []
    for unit, label in zip(sc.units, sc.unit_labels):
        keys = ('utility_consumption_results', 'utility_production_results')
        absent = [k for k in keys if not isinstance(unit.get(k), dict)]
        scores.append(1.0 - len(absent) / 2)
        missing.extend(f'{label}.{k}' for k in absent)
    full = scores.count(1.0)
    return (_mean(scores), f'{full} of {len(scores)} unit(s) record utility '
            f'results', missing)

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
              'capital-cost model (Lang or DPI/TDC/FCI factors)', 'financing')


def _score_sys_02(sc):  # CMP-SYS-02
    stated = {'currency': _nonempty_str(sc.metadata.get('TEA_currency')),
              'start year': _num(sc.metadata.get('TEA_year')) is not None}
    missing = [f'metadata.{f}' for f, item in
               (('TEA_currency', 'currency'), ('TEA_year', 'start year'))
               if not stated[item]]
    missing.extend(f'metadata: {item} not representable'
                   for item in _TEA_ITEMS if item not in stated)
    n = sum(stated.values())
    return (n / len(_TEA_ITEMS), f'{n} of {len(_TEA_ITEMS)} TEA parameters '
            f'stated (only currency and year are representable)', missing)


def _score_sys_03(sc):  # CMP-SYS-03
    return (0.0, 'convergence settings not representable',
            ['system: convergence method, tolerances and maxiter not '
             'representable'])


def _pinned(package):
    package = _as_dict(package)
    return _filled(package.get('commit')) or _filled(package.get('version'))


def _score_sys_04(sc):  # CMP-SYS-04
    simulator = _as_dict(sc.metadata.get('process_simulator'))
    named = _filled(simulator.get('name')) and _filled(simulator.get('version'))
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


_NON_SEMANTIC_IDS = frozenset({'SCHEMA', 'XREF-01', 'TAG-01'})
_SEVERITY_WEIGHT = {'error': 1.0, 'warning': 0.5}


def _score_int_03(sc):  # CMP-INT-03
    executed = [r for r in sc.results if r.check_id not in _NON_SEMANTIC_IDS
                and r.severity in _SEVERITY_WEIGHT and r.status != 'skip']
    if not executed:
        return None, 'no semantic checks executed', []
    total = sum(_SEVERITY_WEIGHT[r.severity] for r in executed)
    passed = sum(_SEVERITY_WEIGHT[r.severity] for r in executed
                 if r.status == 'pass')
    missing = [_clip(f'{r.check_id} ({r.severity}) at {r.path}: {r.message}')
               for r in executed if r.status != 'pass']
    n_pass = sum(1 for r in executed if r.status == 'pass')
    return (passed / total, f'{n_pass} of {len(executed)} executed semantic '
            f'check(s) pass', missing)


def _score_int_04(sc):  # CMP-INT-04
    inline = list(_inline_quantities(sc.doc))
    if inline:
        missing = [f'{path}.units' for path, q in inline if not _filled(q.get('units'))]
        ok = len(inline) - len(missing)
        return (ok / len(inline), f'legacy inline shape: {ok} of {len(inline)} '
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
    'CMP-RXN-01': _score_rxn_01, 'CMP-RXN-02': _score_rxn_02,
    'CMP-RXN-03': _score_rxn_03, 'CMP-RXN-04': _score_rxn_04,
    'CMP-COST-01': _score_cost_01, 'CMP-COST-02': _score_cost_02,
    'CMP-COST-03': _score_cost_03, 'CMP-COST-04': _score_cost_04,
    'CMP-COST-05': _score_cost_05, 'CMP-COST-06': _score_cost_06,
    'CMP-UTIL-01': _score_util_01, 'CMP-UTIL-02': _score_util_02,
    'CMP-UTIL-03': _score_util_03, 'CMP-UTIL-04': _score_util_04,
    'CMP-SYS-01': _score_sys_01, 'CMP-SYS-02': _score_sys_02,
    'CMP-SYS-03': _score_sys_03, 'CMP-SYS-04': _score_sys_04,
    'CMP-INT-01': _score_int_01, 'CMP-INT-02': _score_int_02,
    'CMP-INT-03': _score_int_03, 'CMP-INT-04': _score_int_04,
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
        and results, and the ``recipe`` route summary.

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
        Header (scores, validity, recipe route), per-category and
        per-criticality scores, then one row per criterion followed by its
        first `max_missing` missing locators.
    """
    titles = load_completeness_criteria()['categories']
    version = report.sff_version if report.sff_version is not None else '(none)'
    lines = [
        'SFF completeness report',
        f'  sff_version         : {version}   (valid: '
        f"{'yes' if report.is_valid else 'no'})",
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
