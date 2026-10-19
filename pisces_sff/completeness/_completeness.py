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
imported inside functions. Run as a script for a plain-text report
(``--json`` for the full machine-readable report; ``--render-docs`` prints
the generated criteria tables of ``docs/validation/completeness.md``)::

    python pisces_sff/completeness/_completeness.py FILE [--json] [--max-missing N]
    python pisces_sff/completeness/_completeness.py --render-docs
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
CriterionResult.__doc__ = """
One scored completeness criterion.

Returned in ``CompletenessReport.criteria``: one per entry of the criteria
registry (``criteria.yaml``), in registry order. Each field is documented
on its attribute.
"""

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
CompletenessReport.__doc__ = """
The completeness report of one SFF flowsheet.

Returned by :func:`score_flowsheet_completeness`; rendered as text by
:func:`format_completeness_report`. Each field is documented on its
attribute.
"""

# Field docstrings (shown by the API docs instead of "Alias for field number").
for _tuple, _docs in (
        (CriterionResult, {
            'criterion_id': 'Criterion id from criteria.yaml, e.g. '
                            '"CMP-THM-01".',
            'category': 'Category key, e.g. "thermo".',
            'criticality': 'One of "essential", "costing", "supplementary" '
                           'or "integrity".',
            'weight': 'Weight in points (normalized when aggregating).',
            'representable': "True when the file's sff_version can state "
                             "this criterion explicitly.",
            'status': '"scored", or "not_applicable" when the flowsheet has '
                      'no need for what the criterion measures.',
            'score': 'Score in [0, 1], or None when not applicable.',
            'message': 'One-line human-readable summary.',
            'missing': 'Locators of what is absent or invalid, e.g. '
                       '"streams[feed].stream_properties.temperature".'}),
        (CompletenessReport, {
            'score': 'Weighted mean over every scored criterion (None when '
                     'none is scored).',
            'representable_score': 'The same weighted mean over the criteria '
                                   "the file's sff_version can state.",
            'category_scores': '{category: weighted mean or None}.',
            'criticality_scores': '{criticality: weighted mean or None}.',
            'criteria': 'One CriterionResult per registry criterion, in '
                        'registry order.',
            'sff_version': "The file's raw metadata.sff_version (None when "
                           "absent).",
            'is_valid': "The validator's verdict: no schema failure and no "
                        "error-severity fail.",
            'validation_summary': 'Validator results counted as {severity: '
                                  '{status: count}}; never folded into the '
                                  'score.',
            'validation_results': 'Every validator CheckResult (schema gate, '
                                  'semantic checks, XREF-01, TAG-01).',
            'recipe': 'The separate re-execution route: {"present", '
                      '"digests_valid", "reproducible_declared"}; never '
                      'folded into the score.'})):
    for _field, _doc in _docs.items():
        getattr(_tuple, _field).__doc__ = _doc
del _tuple, _docs, _field, _doc

# Credit for information the file does not state but that is derivable from
# other fields (e.g. an HXutility outlet temperature read off its outlet stream).
DERIVED_CREDIT = 0.75

# CMP-THM-02 identity tiers, the MAXIMUM over those a chemical reaches (R3.2;
# deleting one datum can only remove a tier): a registry id (CAS/InChI/
# InChIKey/SMILES) the `chemicals` database resolves, not contradicted by the
# chemical's molar mass or formula, 1; a well-formed registry id the database
# cannot resolve (or any while the database is unavailable) -- nothing can
# check it -- REGISTRY_UNRESOLVED_CREDIT; the chemical id resolves by name
# (the lookup thermosteam's Chemical(ID) performs, R4.1: its database search
# with CamelCase splitting, then its built-in pseudo-components such as
# Cellulose, Starch, Yeast or Ash) AND the record's molar mass
# (or formula) matches the chemical's own (verified), or resolves with
# nothing stated to verify it against (unverified); or only a formula
# narrows it down (R2.3).
REGISTRY_UNRESOLVED_CREDIT = 0.5
NAME_RESOLVED_CREDIT = 0.75
NAME_UNVERIFIED_CREDIT = 0.5
FORMULA_ONLY_CREDIT = 0.25

# R8.12 (CMP-THM-06): an IdealMixture descriptor that does not state its
# include_excess_energies flag names the mixture class but not the model:
# half the descriptor's credit.
MIXTURE_FLAG_UNSTATED_CREDIT = 0.5

# R2.3: a name-resolved record verifies the chemical when their molar masses
# agree to NAME_MW_RTOL; R2.0/R3.2: a registry id whose database record (or
# InChI formula layer) differs from the chemical's molar mass by more than
# REGISTRY_MW_RTOL, formulas not matching either, is invalid.
NAME_MW_RTOL = 0.01
REGISTRY_MW_RTOL = 0.02

# R2.0 stream-state plausibility: temperatures in (0, T_MAX) K; the
# fractions of one composition sum to 1 within FRACTION_SUM_TOL.
T_MAX = 1.0e4
FRACTION_SUM_TOL = 1e-3

# R2.2: a derivation from stream data needs the unit's total mass balance
# (and, for a physical unit, every species' molar balance) to close within
# CLOSURE_RTOL of the throughput.
CLOSURE_RTOL = 0.01

# R8.6: a species' flow changes across a unit (or the flowsheet) only when
# the change exceeds BOTH CLOSURE_RTOL of the species' own flow (the larger
# of in and out) AND SPECIES_THROUGHPUT_RTOL of the throughput -- the
# unit's inlet total (the flowsheet's boundary inlet total) on the basis
# compared (_species_changed). A printed stream table rounds each stream
# on its own (wt% to 2 decimals, integer kg/h), so a minor species may
# differ by more than 1 % of itself while the imbalance is ~1e-5 of the
# throughput; material conversion (95 % of a feed's glucose) exceeds both.
SPECIES_THROUGHPUT_RTOL = 1e-3

# CMP-RXN-04 (R8.8): a reaction's basis is derivable when its R6.2 closure
# holds within RXN_CLOSURE_RTOL on one basis and not on the other; when it
# holds on both (an isomerisation, or nearly one), only a closure within
# BASIS_RTOL on one of them decides it (_basis_derivable).
BASIS_RTOL = 1e-3

# R6.2: a reaction whose participants all have molar masses is well-formed
# only if it conserves mass on a mol basis (|sum(nu*MW)|) or a wt basis
# (|sum(nu)|) within RXN_CLOSURE_RTOL of its reactant side. Over the 6587
# testable corpus reactions no imbalance falls between 0.02 % and 1.5 %:
# 6223 close within 0.02 % (3-significant-figure equations round by at
# most ~0.3 %), and all 364 others are atom-imbalanced as written (the
# smallest, 1.5 %, drops H2 in 'HMTHP -> H2O + DHL' with DHL stated as
# C6H10O2; others omit O2, H2O, H2 or a nitrogen source), so 1 % trips
# none but the non-conserving.
RXN_CLOSURE_RTOL = 0.01

# R7.4: a well-formed reaction failing the R6.2 closure test is not
# discarded. Its CMP-RXN-02 equation|stoichiometry component earns
# NONCONSERVING_CREDIT -- a faithful re-import would reproduce it, but it
# cannot be told apart from a transcription error -- or full credit when
# its unit carries it alone and the unit's species-flow changes are
# proportional to its coefficients within RXN_CONFIRM_RTOL. Full credit
# needs affirmative evidence (R2.13), so a reaction whose closure cannot
# be tested (a participant's molar mass unknown) earns NONCONSERVING_CREDIT
# too unless its unit's streams confirm it: deleting a molar mass must not
# lift a non-conserving reaction to 1.
NONCONSERVING_CREDIT = 0.5
RXN_CONFIRM_RTOL = 0.02

# Same absolute zero-flow threshold as the validator's ZERO_FLOW (STR-13);
# duplicated to stay import-light, equality pinned by a Tier 1 test.
ZERO_FLOW = 1e-12

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

# thermosteam.chemicals.identifiers.spaceout_words: a space before every
# capital letter that does not begin a word ('AceticAcid' -> 'Acetic Acid').
_SPACEOUT_RE = re.compile(r'\B([A-Z])')


def _spaceout_words(text):
    """thermosteam's ``spaceout_words``: 'AceticAcid' -> 'Acetic Acid',
    'CalciumDihydroxide' -> 'Calcium Dihydroxide', 'CO2' -> 'C O2'."""
    return _SPACEOUT_RE.sub(r' \1', text)


# thermosteam's ChemicalMetadataDB files, in its list order: it pops them
# from the END on a lookup miss, so the small databases load first and the
# large one last, and a name keeps the first record that claimed it.
_METADATA_FILES = ('chemical identifiers pubchem large.tsv', 'Inorganic db.tsv',
                   'Anion db.tsv', 'Cation db.tsv',
                   'chemical identifiers example user db.tsv',
                   'chemical identifiers pubchem small.tsv')
_MetadataRecord = namedtuple('_MetadataRecord', 'name formula MW')


class _MetadataDB:
    """Import-light mirror of thermosteam's ``ChemicalMetadataDB``
    (``thermosteam/chemicals/identifiers.py``) over the `chemicals`
    package's identifier files (R4.1). The `chemicals` package's own
    database differs in ways that change answers: it keeps the LAST record
    claiming a name ('acetate' -> the acetate anion, where thermosteam's
    first-wins index gives acetic acid), tries SMILES and formulas before
    names ('CO' -> methanol) and never splits CamelCase ('AceticAcid').
    Indexes start with the elements; a lookup miss loads the next file and
    retries, exactly as thermosteam's ``search`` does."""

    _INDEXES = ('CAS', 'pubchem', 'smiles', 'InChI', 'InChI_key', 'name',
                'formula')

    def __init__(self, folder, elements, homonuclear, check_cas, cas_to_int,
                 serialize_formula):
        self.index = {key: {} for key in self._INDEXES}
        self.unloaded = [os.path.join(folder, f) for f in _METADATA_FILES]
        self.check_cas, self.cas_to_int = check_cas, cas_to_int
        self.serialize_formula = serialize_formula
        index = self.index
        for ele in elements:
            name = ele.name.lower()
            record = _MetadataRecord(name, ele.symbol, ele.MW)
            index['InChI_key'][ele.InChI_key] = record
            index['CAS'][int(ele.CAS.replace('-', ''))] = record
            index['pubchem'][ele.PubChem] = record
            index['smiles'][ele.smiles] = record
            index['InChI'][ele.InChI] = record
            index['formula'][ele.symbol] = record
            index['name'][('monatomic ' + name) if ele.number in homonuclear
                          else name] = record

    def load(self, path):
        """Index one identifier file: a CAS number seen before keeps its
        record (and its other index entries); names (iupac, common and
        synonyms, ';'-separated, lower-cased) keep their first record."""
        index = self.index
        names = index['name']
        with open(path, encoding='utf-8') as f:
            for line in f:
                values = line.rstrip('\n').split('\t')
                (pubchemid, cas, formula, mw, smiles, inchi,
                 inchi_key) = values[:7]
                cas = int(cas.replace('-', ''))
                record = index['CAS'].get(cas)
                if record is None:
                    common = values[8] if len(values) > 8 else ''
                    record = _MetadataRecord(common, formula, float(mw))
                    index['CAS'][cas] = record
                    index['pubchem'][int(pubchemid)] = record
                    index['smiles'][smiles] = record
                    index['InChI'][inchi] = record
                    index['InChI_key'][inchi_key] = record
                    index['formula'][formula] = record
                for synonyms in values[7:]:
                    for name in synonyms.split(';'):
                        if name:
                            names.setdefault(name.lower(), record)

    def _get(self, index, key):
        return self.index[index].get(key)

    def _search(self, ID):
        """thermosteam's ``ChemicalMetadataDB._search`` over the loaded
        files, None for a miss: a CAS number by CAS, then as a name; '_' ->
        ' '; an 'InChI=1S/' / 'InChI=1/', 'InChIKey=', 'PubChem=' or
        'SMILES=' prefix searches only that index; otherwise the lower-cased
        CamelCase-split id (_spaceout_words), then the lower-cased id, as
        names; last the id as a formula."""
        if self.check_cas(ID):
            return (self._get('CAS', self.cas_to_int(ID))
                    or self._get('name', ID))
        ID = ID.replace('_', ' ')
        low = ID.lower()
        if len(ID) > 9:
            inchi = (ID[9:] if low[:9] == 'inchi=1s/'
                     else ID[8:] if low[:8] == 'inchi=1/' else None)
            if inchi:
                return self._get('InChI', inchi)
            if low[:9] == 'inchikey=':
                return self._get('InChI_key', ID[9:])
        if len(ID) > 8 and low[:8] == 'pubchem=':
            return self._get('pubchem', int(ID[8:]))
        if len(ID) > 7 and low[:7] == 'smiles=':
            return self._get('smiles', ID[7:])
        for name in (_spaceout_words(ID).lower(), low):
            record = self._get('name', name)
            if record:
                return record
        try:
            formula = self.serialize_formula(ID)
        except Exception:
            return None
        return self._get('formula', formula)

    def search(self, ID):
        """The record `ID` resolves to, loading the next file after each
        miss (thermosteam's ``search``); None when nothing resolves it."""
        def attempt():
            try:
                return self._search(ID)
            except Exception:
                return None
        record = attempt()
        while record is None and self.unloaded:
            self.load(self.unloaded.pop())
            record = attempt()
        return record


def _chemicals_search():
    """The database lookup thermosteam's ``Chemical(ID)`` performs before it
    falls back to its built-in pseudo-components (R4.1) -- a search function
    (id -> metadata record with ``MW`` and ``formula``, or None / raising
    when unresolved) -- built lazily once; None when the `chemicals` package
    cannot be imported (the CMP-THM-02 database tiers are then unavailable).
    It searches a _MetadataDB (thermosteam's algorithm over the `chemicals`
    identifier files) built on first use -- ALWAYS this import-light
    replica, never a database thermosteam may have installed in this
    process, so one file scores the same whatever else the process has
    imported. No thermosteam import is made."""
    if not _CHEMICALS_SEARCH:
        try:
            from chemicals import identifiers
            from chemicals.elements import (homonuclear_elemental_gases,
                                            periodic_table, serialize_formula)
            own = []

            def search(ID):
                ID = ID.strip()
                if not ID:
                    return None
                if not own:
                    own.append(_MetadataDB(
                        identifiers.folder, periodic_table,
                        homonuclear_elemental_gases, identifiers.check_CAS,
                        identifiers.CAS_to_int, serialize_formula))
                return own[0].search(ID)
        except Exception:
            search = None
        _CHEMICALS_SEARCH.append(search)
    return _CHEMICALS_SEARCH[0]


def _database_record(name):
    """The `chemicals` database record `name` (a chemical name, synonym,
    CAS number, InChI, or an 'InChIKey='/'SMILES='-prefixed registry id --
    see _registry_query) resolves to by thermosteam's lookup
    (_chemicals_search), or None. Cached per name, so a failed lookup costs
    one search (the first also loads the database, ~1 s); never raises --
    an unavailable package or any lookup error means None."""
    if not _nonempty_str(name):
        return None
    name = name.strip()
    if name not in _NAME_LOOKUP:
        search = _chemicals_search()
        try:
            record = search(name) if search is not None else None
        except Exception:
            record = None
        _NAME_LOOKUP[name] = record if record else None
    return _NAME_LOOKUP[name]


# thermosteam's built-in bioprocess pseudo-components (R4.1): the chemicals
# ``Chemical(ID)`` builds from ``thermosteam/_biorefinery_chemicals.py`` when
# its database lookup fails. STATIC -- generated once from the thermosteam
# source (no runtime import): each registered name or alias (the function
# name, its register() aliases, their lower-case forms) under thermosteam's
# normalised key (spaces, '-' and '_' removed, lower-cased), keeping only the
# keys the `chemicals` database does NOT resolve (Glucan, Lignin, Xylitol,
# Cellobiose, H3PO4, P4O10, Glucose, Sucrose, Xylose and their aliases
# resolve there first, so Chemical(ID) never reaches the built-in). Value:
# (built-in name, molar mass, formula or None, locked phase). Rubber's
# mass-fraction combustion formula is no molecular formula (None);
# CarbonBlack's '1C' is written 'C'.
_Builtin = namedtuple('_Builtin', 'name MW formula phase')
_BUILTIN_SPECS = (
    ('Ash', 1.0, None, 's', ('ash',)),
    ('Flocculant', 1.0, None, 's', ('flocculant',)),
    ('Solids', 1.0, None, 's', ('solids',)),
    ('Bacteria', 21.824692, 'CH1.8O0.5', 's', ('bacteria', 'drybacteria')),
    ('NitrogenBacteria', 24.626032, 'CH1.8O0.5N0.2', 's',
     ('nbacteria', 'nitrogenbacteria')),
    ('Yeast', 22.5931474, 'CH1.61O0.56', 's', ('dryyeast', 'yeast')),
    ('NitrogenYeast', 24.8342194, 'CH1.61O0.56N0.16', 's',
     ('nitrogenyeast', 'nyeast')),
    ('Sluge', 23.2372561, 'CH1.64O0.39N0.23S0.0035', 's',
     ('sluge', 'wwtsludge')),
    ('Protein', 22.8393778, 'CH1.57O0.31N0.29S0.007', 's',
     ('amylase', 'cellulase', 'enzyme', 'protein')),
    ('Glucan', 162.1406, 'C6H10O5', 's', ('cellulose',)),
    ('Starch', 162.1406, 'C6H10O5', 's', ('starch',)),
    ('Fiber', 162.1406, 'C6H10O5', 's', ('fiber',)),
    ('Hemicellulose', 132.11462, 'C5H8O4', 's', ('hemicellulose',)),
    ('Xylan', 132.11462, 'C5H8O4', 's', ('arabinan', 'xylan')),
    ('SolubleLignin', 152.14732, 'C8H8O3', 'l', ('solublelignin',)),
    ('GlucoseOligomer', 162.1406, 'C6H10O5', 'l',
     ('galactoseoligomer', 'glucoseoligomer', 'mannoseoligomer')),
    ('XyloseOligomer', 132.11462, 'C5H8O4', 'l',
     ('arabinoseoligomer', 'xyloseoligomer')),
    ('Rubber', 0.9999766, None, 's', ('rubber',)),
    ('CarbonBlack', 12.0107, 'C', 's',
     ('activatedcarbon', 'carbonblack', 'char', 'rawcarbonblack')),
)
_BUILTIN_CHEMICALS = {key: _Builtin(name, mw, formula, phase)
                      for name, mw, formula, phase, keys in _BUILTIN_SPECS
                      for key in keys}


def _builtin_key(name):
    """thermosteam's normalised built-in key: spaces, '-' and '_' removed,
    lower-cased ('Dry_Yeast' -> 'dryyeast')."""
    return (name.strip().replace(' ', '').replace('-', '').replace('_', '')
            .lower())


def _builtin_record(name):
    """The built-in pseudo-component (_Builtin) `name` names under
    thermosteam's normalised key, or None; never raises."""
    if not _nonempty_str(name):
        return None
    return _BUILTIN_CHEMICALS.get(_builtin_key(name))


def _name_record(name):
    """What thermosteam's ``Chemical(name)`` resolves `name` to (R4.1): the
    `chemicals` database record (_database_record), else the built-in
    pseudo-component (_builtin_record), else None. Both carry ``MW`` and
    ``formula``; a built-in also its locked ``phase``. Never raises."""
    return _database_record(name) or _builtin_record(name)


def _name_resolvable(name):
    """True when chemical id `name` resolves by name as thermosteam's
    ``Chemical(ID)`` resolves it (CMP-THM-02 name tier; see _name_record)."""
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
    """True only for the explicit boundary sentinel "None" (R5.8: a missing
    key, JSON null or "" is no boundary but a dangling endpoint,
    _is_dangling)."""
    return endpoint == BOUNDARY


def _is_dangling(endpoint):
    """R5.8: a stream endpoint that is not stated -- a missing
    source_unit_id / sink_unit_id key (read as None), JSON null, a
    non-string or a blank string (INVALID == MISSING): neither a unit id
    nor the boundary sentinel, so the stream's connectivity is unknown."""
    return not _nonempty_str(endpoint)


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


# R5.3: the (flow key, fraction key) of each species-flow basis.
_BASIS_KEYS = {'molar': ('total_molar_flow', 'mol_fraction'),
               'mass': ('total_mass_flow', 'mass_fraction')}


def _phase_flow(phase, sp, single, key):
    """Valid (>= 0) flow `key` (total_molar_flow / total_mass_flow) of one
    phase of a stream, or None; the only phase of a single-phase stream
    (`single`: _sole_phase) falls back to the stream-level total of the
    same key (R5.3)."""
    flow = _valid_flow(_as_dict(phase).get(key))
    if flow is None and single:
        flow = _valid_flow(_as_dict(sp).get(key))
    return flow


def _empty_phase(phase):
    """True when a phase states a valid zero flow of its own
    (total_molar_flow or total_mass_flow <= ZERO_FLOW): the exporter's
    empty phase (STR-13), empty on either basis."""
    return any(flow is not None and flow <= ZERO_FLOW
               for flow in (_valid_flow(_as_dict(phase).get(key))
                            for key in ('total_molar_flow', 'total_mass_flow')))


def _sole_phase(phases, symbol):
    """True when phase `symbol` is the only phase of a stream's `phases`
    (a non-empty dict) that may carry material: every other phase is
    stated empty (_empty_phase). Such a stream is single-phase (R5.3 /
    R7.3): the phase takes the stream's amount as its own -- the
    stream-level total in _phases_ok and _species_flows, the volume flow in
    _phases_ok for CMP-FEED-01 / CMP-UOP-04 -- so a stated-empty phase
    beside the material one changes nothing, and deleting it never raises
    what the other phase yields. (Deleting a phase that is not stated
    empty is outside the deletion guarantee: see _phases_ok.)"""
    return all(key == symbol or _empty_phase(phase)
               for key, phase in phases.items())


def _volumetric_amount(sp):
    """R7.3: the valid (>= 0) total_volumetric_flow (internal m3/hr) of
    stream_properties `sp` when its temperature and pressure are valid too
    (_valid_T / _valid_P: INVALID == MISSING), else None -- the stream
    amount a phase stating no mass or molar flow may rest on when the
    stream states no mass or molar total either (_phases_ok with
    `volumetric`), since BioSTEAM derives the totals from the volume flow,
    T, P and composition (R6.7)."""
    sp = _as_dict(sp)
    if (_valid_T(sp.get('temperature')) is None
            or _valid_P(sp.get('pressure')) is None):
        return None
    return _valid_flow(sp.get('total_volumetric_flow'))


def _phases_ok(phases, sp=None, volumetric=False):
    """Every phase has a known amount -- a valid (>= 0) total_molar_flow OR
    total_mass_flow of its own, or, for the only phase that may carry
    material (_sole_phase: every other phase stated empty), the stream's:
    the stream-level total of stream_properties `sp` (R5.3), else, with
    `volumetric` (R7.3, CMP-FEED-01 and CMP-UOP-04 only), the stream's
    volumetric amount (_volumetric_amount: a valid volume flow at a valid
    T and P) -- and at least one carries flow; each phase with flow has a
    plausible composition (_composition_ok: mol OR mass fractions), while
    a zero-flow phase is exempt and may be empty (the exporter serializes
    a phase with F_mol <= ZERO_FLOW as empty -- see STR-13). Of several
    phases that may carry material, each needs its own amount: the phase
    split fixes the stream's species flows, so a stream amount without it
    states no phase's amount (R5.3 / R7.3: "for a single-phase stream").
    A stated-empty phase beside the material one changes nothing, so
    deleting it never raises the credit; deleting a whole phase that is
    not stated empty asserts a different set of phases (the schema lists
    the phases present) and is outside the deletion guarantee, like
    deleting a list element. A
    stated zero total still wins over a volume flow, and a zero stream
    amount populates nothing."""
    if not isinstance(phases, dict) or not phases:
        return False
    populated = False
    for symbol, phase in phases.items():
        flow = None
        if isinstance(phase, dict):
            single = _sole_phase(phases, symbol)
            for source, key in ((phase, 'total_molar_flow'),
                                (phase, 'total_mass_flow'),
                                (sp, 'total_molar_flow'),
                                (sp, 'total_mass_flow')):
                if source is sp and not single:
                    break
                flow = _valid_flow(_as_dict(source).get(key))
                if flow is not None:
                    break
            if flow is None and volumetric and single:
                flow = _volumetric_amount(sp)
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


def _stream_state(stream, empty_exempt=True, volumetric=False):
    """Booleans {T, P, flow, composition}: which parts of a stream's state are
    stated with a plausible value (R2.0: temperature in (0, T_MAX) K,
    pressure > 0, total flow >= 0, composition per _phases_ok -- per-phase
    shape, 0.0.9+, molar or mass basis -- or _composition_ok on the legacy
    flat ``stream_properties.composition``). With `empty_exempt`, a stream
    stating a zero total flow satisfies composition. `volumetric` (R7.3,
    CMP-FEED-01 / CMP-UOP-04): the stream's volume flow is the amount the
    only phase of a single-phase stream may rest on when it states none
    (_phases_ok). Missing stream_properties -> all False."""
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    total = _total_flow(sp)
    composition = ((empty_exempt and total is not None and total <= ZERO_FLOW)
                   or _phases_ok(sp.get('phases'), sp, volumetric)
                   or _composition_ok(sp.get('composition')))
    return {'T': _valid_T(sp.get('temperature')) is not None,
            'P': _valid_P(sp.get('pressure')) is not None,
            'flow': total is not None,
            'composition': bool(composition)}


def _volumetric_flow_credit(stream):
    """R6.7: DERIVED_CREDIT for the flow part of a stream's state when it
    states no valid mass or molar total (_total_flow: absent or invalid --
    INVALID == MISSING) but a valid, non-zero total_volumetric_flow
    (internal m3/hr) with a valid temperature and pressure and a
    composition that fixes its density -- per phase (_phases_ok, or every
    phase not stated empty, at least one, with a plausible composition,
    _composition_ok) or the legacy flat list -- since BioSTEAM derives the
    totals from them; else
    0. It is the tier below a stated total (MAX over tiers). A stream
    stating a zero total (an empty stream, credited or not as a
    placeholder, R6.3) or a zero volume flow earns nothing from it, so
    padding with zero-flow streams stays worthless and deleting a stated
    zero total never adds the tier to a consistent empty stream."""
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    volume = _valid_flow(sp.get('total_volumetric_flow'))
    if (_total_flow(sp) is not None or volume is None or volume <= ZERO_FLOW
            or _valid_T(sp.get('temperature')) is None
            or _valid_P(sp.get('pressure')) is None):
        return 0.0
    phases = sp.get('phases')
    # A stated-empty phase (_empty_phase) needs no composition, as in
    # _phases_ok, so deleting one never lifts the tier.
    fixed = (_phases_ok(phases, sp, volumetric=True)
             or _composition_ok(sp.get('composition'))
             or (isinstance(phases, dict) and all(
                 _composition_ok(_as_dict(phase).get('composition'))
                 or _empty_phase(phase) for phase in phases.values())
                 and not all(_empty_phase(phase)
                             for phase in phases.values())))
    return DERIVED_CREDIT if fixed else 0.0


# Locator suffix of each stream-state part, for `missing` entries.
_STATE_PATHS = {
    'T': 'stream_properties.temperature',
    'P': 'stream_properties.pressure',
    'flow': 'stream_properties.total_mass_flow|total_molar_flow',
    'composition': 'stream_properties.phases[*].composition',
}
# R6.7: suffix of a flow locator whose part earns the volumetric tier.
_VOLUMETRIC_NOTE = ' (only derivable from total_volumetric_flow)'


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


def _species_flows(stream, basis='molar'):
    """{component: flow} of a stream on `basis` -- 'molar' (phase
    total_molar_flow x mol_fraction) or 'mass' (phase total_mass_flow x
    mass_fraction; R5.3) -- or None when not computable from valid data on
    that basis (no valid flow of the basis for some phase -- the only phase
    of a single-phase stream, any other phase stated empty (_sole_phase),
    may use the stream-level total -- an
    implausible composition, an entry without a valid fraction of the
    basis, or a legacy flat composition over several phases, whose phase
    split is unknown). A zero-flow stream -> {}; so is a per-phase stream
    whose every phase states a zero flow. A phase stated empty on one
    basis (_empty_phase) is empty on the other too."""
    flow_key, fraction_key = _BASIS_KEYS[basis]
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    total = _total_flow(sp)
    if total is not None and total <= ZERO_FLOW:
        return {}
    phases = sp.get('phases')
    if isinstance(phases, dict) and phases:
        blocks = []
        for symbol, phase in phases.items():
            phase = _as_dict(phase)
            flow = _phase_flow(phase, sp, _sole_phase(phases, symbol),
                               flow_key)
            if flow is None and _empty_phase(phase):
                flow = 0.0  # stated empty on the other basis
            if flow is None:
                return None
            if flow > ZERO_FLOW:
                if not _composition_ok(phase.get('composition')):
                    return None
                blocks.append((flow, phase['composition']))
    elif _composition_ok(sp.get('composition')) \
            and _valid_flow(sp.get(flow_key)) is not None \
            and len({e.get('phase') if isinstance(e.get('phase'), str) else None
                     for e in sp['composition']}) == 1:
        blocks = [(_valid_flow(sp.get(flow_key)), sp['composition'])]
    else:
        return None
    flows = {}
    for flow, composition in blocks:
        for entry in composition:
            x = _valid_fraction(entry.get(fraction_key))
            if x is None:
                return None
            name = entry['component_name']
            flows[name] = flows.get(name, 0.0) + flow * x
    return flows


def _basis_flows(streams):
    """(basis, [species flows per stream]) of a set of streams compared
    together -- one unit's ports (R5.3): the molar basis when every stream's
    molar species flows are computable (_species_flows), else the mass
    basis when every stream's mass species flows are (never a mix of the
    two); None when neither basis covers them all."""
    for basis in _BASIS_KEYS:
        flows = [_species_flows(stream, basis) for stream in streams]
        if all(f is not None for f in flows):
            return basis, flows
    return None


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

class _Unconverted(float):
    """A finite stream or utility quantity whose stated unit the scorer does
    not recognise (R3.4, _to_internal): no plausibility bound applies to it
    (any finite number is valid; a flow must still be >= 0, which no unit
    changes) and no consistency guard reads it (_stream_value)."""


def _valid_number(x, lo=None, hi=None, lo_open=False, hi_open=False):
    """_num(x) when it lies within [lo, hi] (either bound open, or absent),
    else None. An _Unconverted quantity skips the bounds (R3.4)."""
    value = _num(x)
    if value is None:
        return None
    if isinstance(x, _Unconverted):
        return value
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
    """A plausible flow: finite, >= 0 (also in an unrecognised unit: no
    flow unit makes a negative number plausible); else None."""
    value = _valid_number(x, 0.0)
    return value if value is not None and value >= 0 else None


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


def _spec_open_fraction(x):
    """A fraction strictly between 0 and 1 (R3.11: a column's light/heavy
    key recoveries Lr/Hr and top/bottom compositions y_top/x_bot)."""
    return _valid_number(x, 0.0, 1.0, lo_open=True, hi_open=True) is not None


def _spec_above_one(x):
    """A number > 1 (R3.11: a distillation column's k, the ratio of reflux
    to minimum reflux)."""
    return _valid_number(x, 1.0, lo_open=True) is not None


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


def _spec_efficiency_basis(x):
    """R8.9: a boiler's efficiency basis -- 'LHV' or 'HHV', the only values
    BioSTEAM's Boiler / BoilerTurbogenerator accept (anything else raises
    at simulation)."""
    return isinstance(x, str) and x in ('LHV', 'HHV')


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
                     'turbogenerator_efficiency', 'theta_O2', 'theta'),
                    _spec_efficiency),
    **dict.fromkeys(('Lr', 'Hr', 'y_top', 'x_bot'), _spec_open_fraction),
    **dict.fromkeys(('moisture_content', 'sludge_moisture'), _spec_fraction),
    # R8.10: biosteam.wastewater yields and conversions (AnMBR Y_biogas /
    # Y_biomass, PolishingFilter X_decomp / X_growth) and ReverseOsmosis's
    # water recovery are fractions; a RefluxDrum's holdup and surge times
    # (minutes, BioSTEAM-native) are positive.
    **dict.fromkeys(('water_recovery', 'Y_biogas', 'Y_biomass', 'X_decomp',
                     'X_growth'), _spec_fraction),
    **dict.fromkeys(('holdup_time', 'surge_time'), _spec_positive),
    'boiler_efficiency_basis': _spec_efficiency_basis,
    'duty_per_kg': _spec_positive,
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


# Family-specific predicates overriding _SPEC_PREDICATES (R3.11): a
# distillation column's k is the reflux ratio over its minimum, so <= 1
# cannot converge (an adsorption column's k is a rate constant: > 0 only).
_FAMILY_SPEC_PREDICATES = {
    ('distillation', 'k'): _spec_above_one,
    # R8.10: BiogasUpgrading's upgraded fraction and CH4 loss; a CHP's
    # combined efficiency.
    ('splitter', 'ratio'): _spec_fraction, ('splitter', 'loss'): _spec_fraction,
    ('boiler_turbogenerator', 'eff'): _spec_efficiency}


def _valid_spec(key, value, family=None):
    """True when design_input_specs[key] = value is plausible for the
    parameter's role (_FAMILY_SPEC_PREDICATES for the unit's `family`, else
    _SPEC_PREDICATES; else any non-empty value)."""
    predicate = None
    if isinstance(key, str):
        predicate = (_FAMILY_SPEC_PREDICATES.get((family, key))
                     or _SPEC_PREDICATES.get(key))
    return (predicate or _nonempty_value)(value)

#%% Quantity units (R3.4: convert to BioSTEAM's internal units)

# Stream and utility-agent quantities are converted to BioSTEAM's internal
# units (K, Pa, kg/hr, kmol/hr, m3/hr) BEFORE plausibility checks, consistency
# guards and closure tests. The unit comes from the legacy inline
# {value, units} pair, else from the quantity_units_global entry whose
# aliases name the field; none stated -> the value is taken as internal.
# A design_input_specs key with a physical dimension (_SPEC_DIMENSIONS) that
# a quantity_units_global entry lists among its aliases takes that entry's
# unit the same way (R5.2); any other spec value is read as BioSTEAM-native.
# internal = value * scale + offset; unit strings are matched after removing
# blanks, '^' and '**' ('m^3/hr' -> 'm3/hr').
_QUANTITY_DIMENSIONS = {
    'temperature': 'temperature', 'temperature_limit': 'temperature',
    'pressure': 'pressure', 'total_mass_flow': 'mass_flow',
    'total_molar_flow': 'molar_flow', 'total_volumetric_flow': 'volumetric_flow',
}
_F_SCALE = 5.0 / 9.0
_PSI = 6894.757293168
_ATM = 101325.0  # Pa: the zero of a gauge pressure unit (R6.6)
_GALLON = 3.785411784e-3  # m3 (US gallon)
_UNIT_FACTORS = {
    'temperature': {
        **dict.fromkeys(('K', 'kelvin', 'Kelvin'), (1.0, 0.0)),
        **dict.fromkeys(('degC', '\u00b0C', 'C', 'celsius', 'Celsius'),
                        (1.0, 273.15)),
        **dict.fromkeys(('degF', '\u00b0F', 'F', 'fahrenheit', 'Fahrenheit'),
                        (_F_SCALE, 273.15 - 32.0 * _F_SCALE)),
        **dict.fromkeys(('R', 'degR', '\u00b0R', 'rankine', 'Rankine'),
                        (_F_SCALE, 0.0)),
    },
    'pressure': {
        'Pa': (1.0, 0.0), 'hPa': (1e2, 0.0), 'kPa': (1e3, 0.0),
        'MPa': (1e6, 0.0), 'bar': (1e5, 0.0), 'mbar': (1e2, 0.0),
        'atm': (101325.0, 0.0), 'psi': (_PSI, 0.0), 'psia': (_PSI, 0.0),
        'mmHg': (133.322387415, 0.0), 'torr': (101325.0 / 760.0, 0.0),
        'Torr': (101325.0 / 760.0, 0.0),
        # Explicitly absolute spellings.
        **dict.fromkeys(('bara', 'bar(a)'), (1e5, 0.0)),
        **dict.fromkeys(('kPaa', 'kPa(a)'), (1e3, 0.0)),
        **dict.fromkeys(('MPaa', 'MPa(a)'), (1e6, 0.0)), 'psi(a)': (_PSI, 0.0),
        # R6.6: gauge units read relative to 1 atm (offset 101325 Pa).
        **dict.fromkeys(('psig', 'psi(g)'), (_PSI, _ATM)),
        **dict.fromkeys(('barg', 'bar(g)'), (1e5, _ATM)),
        **dict.fromkeys(('mbarg', 'mbar(g)'), (1e2, _ATM)),
        **dict.fromkeys(('kPag', 'kPa(g)'), (1e3, _ATM)),
        **dict.fromkeys(('MPag', 'MPa(g)'), (1e6, _ATM)),
        **dict.fromkeys(('atmg', 'atm(g)'), (101325.0, _ATM)),
    },
    'mass_flow': {
        **dict.fromkeys(('kg/hr', 'kg/h'), (1.0, 0.0)),
        'kg/s': (3600.0, 0.0), 'kg/min': (60.0, 0.0),
        **dict.fromkeys(('kg/day', 'kg/d'), (1.0 / 24.0, 0.0)),
        'g/s': (3.6, 0.0), **dict.fromkeys(('g/hr', 'g/h'), (1e-3, 0.0)),
        **dict.fromkeys(('t/h', 't/hr', 'tonne/hr', 'tonne/h', 'tonnes/hr',
                         'tonnes/h'), (1e3, 0.0)),
        **dict.fromkeys(('t/d', 't/day', 'tonne/day', 'tonnes/day'),
                        (1e3 / 24.0, 0.0)),
        **dict.fromkeys(('lb/hr', 'lb/h'), (0.45359237, 0.0)),
    },
    'molar_flow': {
        **dict.fromkeys(('kmol/hr', 'kmol/h'), (1.0, 0.0)),
        'kmol/s': (3600.0, 0.0), 'kmol/min': (60.0, 0.0),
        'mol/s': (3.6, 0.0), 'mol/min': (0.06, 0.0),
        **dict.fromkeys(('mol/hr', 'mol/h'), (1e-3, 0.0)),
        **dict.fromkeys(('lbmol/hr', 'lbmol/h'), (0.45359237, 0.0)),
    },
    'volumetric_flow': {
        **dict.fromkeys(('m3/hr', 'm3/h'), (1.0, 0.0)),
        'm3/s': (3600.0, 0.0), 'm3/min': (60.0, 0.0),
        **dict.fromkeys(('m3/day', 'm3/d'), (1.0 / 24.0, 0.0)),
        **dict.fromkeys(('L/hr', 'L/h', 'l/hr', 'l/h'), (1e-3, 0.0)),
        **dict.fromkeys(('L/min', 'l/min'), (0.06, 0.0)),
        **dict.fromkeys(('L/s', 'l/s'), (3.6, 0.0)),
        **dict.fromkeys(('gal/min', 'gpm', 'GPM'), (_GALLON * 60.0, 0.0)),
        **dict.fromkeys(('gal/hr', 'gal/h'), (_GALLON, 0.0)),
        **dict.fromkeys(('ft3/hr', 'ft3/h'), (0.028316846592, 0.0)),
        **dict.fromkeys(('ft3/min', 'cfm'), (0.028316846592 * 60.0, 0.0)),
    },
    # Design-specification dimensions (R5.2).
    'heat_flow': {  # kJ/hr
        **dict.fromkeys(('kJ/hr', 'kJ/h'), (1.0, 0.0)), 'J/hr': (1e-3, 0.0),
        **dict.fromkeys(('MJ/hr', 'MJ/h'), (1e3, 0.0)),
        **dict.fromkeys(('GJ/hr', 'GJ/h'), (1e6, 0.0)),
        **dict.fromkeys(('W', 'J/s'), (3.6, 0.0)), 'kW': (3600.0, 0.0),
        'MW': (3.6e6, 0.0),
        **dict.fromkeys(('kcal/hr', 'kcal/h'), (4.184, 0.0)),
        **dict.fromkeys(('Btu/hr', 'BTU/hr', 'Btu/h', 'BTU/h'),
                        (1.05505585262, 0.0)),
        **dict.fromkeys(('MMBtu/hr', 'MMBTU/hr', 'MMBtu/h', 'MMBTU/h'),
                        (1.05505585262e3, 0.0)),
    },
    'time': {  # hr
        **dict.fromkeys(('hr', 'h', 'hour', 'hours'), (1.0, 0.0)),
        **dict.fromkeys(('min', 'minute', 'minutes'), (1.0 / 60.0, 0.0)),
        **dict.fromkeys(('s', 'sec', 'second', 'seconds'),
                        (1.0 / 3600.0, 0.0)),
        **dict.fromkeys(('d', 'day', 'days'), (24.0, 0.0)),
    },
    'volume': {  # m3
        'm3': (1.0, 0.0),
        **dict.fromkeys(('L', 'l', 'liter', 'litre'), (1e-3, 0.0)),
        'gal': (_GALLON, 0.0), 'ft3': (0.028316846592, 0.0),
    },
    'specific_energy': {  # kJ/kg
        'kJ/kg': (1.0, 0.0), 'MJ/kg': (1e3, 0.0), 'J/kg': (1e-3, 0.0),
        'kcal/kg': (4.184, 0.0), **dict.fromkeys(('Btu/lb', 'BTU/lb'),
                                                 (2.326, 0.0)),
    },
}
# A temperature DIFFERENCE (dT, T_min_app) takes every temperature unit, but
# scales without the offset: 5 degC of approach is 5 K (R5.2).
_UNIT_FACTORS['temperature_difference'] = {
    unit: (scale, 0.0)
    for unit, (scale, _offset) in _UNIT_FACTORS['temperature'].items()}
# Likewise a pressure DIFFERENCE (dP_design) takes every pressure unit
# without the gauge offset: 5 psig of rise is 5 psi (R6.6).
_UNIT_FACTORS['pressure_difference'] = {
    unit: (scale, 0.0)
    for unit, (scale, _offset) in _UNIT_FACTORS['pressure'].items()}

# R5.2: design_input_specs keys with a physical dimension (the _UNIT_FACTORS
# table their quantity unit is read from), checked against the biosteam
# source; every other key -- split and other fractions, efficiencies, flags,
# LHK, N_stages, ratios such as k, reflux or pr, ids -- is dimensionless and
# needs no unit. The wastewater loading rates have a dimension the built-in
# table does not cover ('loading_rate'): no unit of theirs is recognised.
_SPEC_DIMENSIONS = {
    **dict.fromkeys(('T', 'T_lim0', 'T_lim1', 'Temperature'), 'temperature'),
    **dict.fromkeys(('dT', 'T_min_app'), 'temperature_difference'),
    'P': 'pressure', 'dP_design': 'pressure_difference',
    **dict.fromkeys(('Q', 'H', 'H_lim0', 'H_lim1', 'Duty'), 'heat_flow'),
    # R8.10: a RefluxDrum's holdup and surge times are times too (minutes
    # natively; only their unit and sign are ever read).
    **dict.fromkeys(('tau', 'tau_0', 'cycle_time', 'holdup_time',
                     'surge_time'), 'time'),
    'V_max': 'volume',
    'duty_per_kg': 'specific_energy',
    **dict.fromkeys(('OLR', 'OLRall', 'HLR'), 'loading_rate'),
}
# V is a vapor fraction (flash, exchangers, evaporators: dimensionless)
# except in the families where it is a vessel volume. It is never converted
# (no check reads its magnitude), only its unit is scored (CMP-UOP-05).
_FAMILY_SPEC_DIMENSIONS = {('batch_bioreactor', 'V'): 'volume',
                           ('crystallizer', 'V'): 'volume'}
# R5.4: design_input_specs keys holding per-chemical split vectors.
_SPLIT_KEYS = frozenset({'split', 'solids_split', 'aqueous_split'})


def _unit_key(unit):
    """A unit string with blanks, '^' and '**' removed ('m^3 / hr' ->
    'm3/hr'), or None for a non-string or blank one (no unit stated)."""
    if not isinstance(unit, str):
        return None
    key = re.sub(r'\s+', '', unit).replace('**', '').replace('^', '')
    return key or None


def _quantity_unit_index(doc):
    """{field alias: unit key, or '' when the aliases of several
    quantity_units_global entries disagree on it} over the document's
    quantity_units_global (entries whose unit is not a non-blank string
    state nothing)."""
    index = {}
    for entry in _as_dict(_as_dict(doc).get('quantity_units_global')).values():
        entry = _as_dict(entry)
        unit = _unit_key(entry.get('quantity_units'))
        if unit is None:
            continue
        for alias in _as_list(entry.get('aliases')):
            if isinstance(alias, str):
                index[alias] = unit if index.get(alias, unit) == unit else ''
    return index


def _stated_unit(value, field, index):
    """Unit key a quantity is stated in: a legacy inline {value, units}
    pair's own units, else the quantity_units_global unit of `field`
    (`index`, _quantity_unit_index; '' when entries disagree); None when
    neither states one."""
    inline = _unit_key(value.get('units')) if isinstance(value, dict) else None
    return inline if inline is not None else index.get(field)


def _other_dimensions(unit, dimension):
    """R8.16: the dimensions (_UNIT_FACTORS tables, a '_difference' table
    folded into its base) whose units include the unit key `unit` when
    `dimension`'s own table does not -- a temperature stated in 'kg/hr'
    -> ('mass_flow',) -- else () (a unit of `dimension`, no unit, or one no
    table knows)."""
    if not unit or unit in _UNIT_FACTORS.get(dimension, {}):
        return ()
    return tuple(sorted({name.replace('_difference', '')
                         for name, table in _UNIT_FACTORS.items()
                         if unit in table}))


def _to_internal(value, field, index, dimension=None):
    """`value` of quantity `field` in BioSTEAM's internal unit (R3.4): a
    legacy inline {value, units} pair converts by its own units, anything
    else by the quantity_units_global unit of the field (`index`,
    _quantity_unit_index). `dimension` names the _UNIT_FACTORS table (default
    _QUANTITY_DIMENSIONS[field]; a design-spec key passes its own, R5.2). No
    unit stated -> the number as is; a recognised unit -> the converted
    float; a unit the table knows only for another dimension -> None (R8.16:
    the quantity is invalid, INVALID == MISSING); an unrecognised (or
    ambiguous) one -> _Unconverted. A value that is not a finite number is
    returned unchanged, so the predicates still reject it."""
    number = _num(value)
    if number is None:
        return value
    unit = _stated_unit(value, field, index)
    if unit is None:
        return number if isinstance(value, dict) else value
    dimension = dimension or _QUANTITY_DIMENSIONS[field]
    factor = _UNIT_FACTORS.get(dimension, {}).get(unit)
    if factor is None:
        if _other_dimensions(unit, dimension):
            return None
        return _Unconverted(number)
    return number * factor[0] + factor[1]


def _spec_dimension(key, family=None):
    """R5.2 physical dimension of design_input_specs[key] in a unit of
    `family` (_FAMILY_SPEC_DIMENSIONS, else _SPEC_DIMENSIONS), or None for a
    dimensionless key, which needs no unit."""
    if not isinstance(key, str):
        return None
    return (_FAMILY_SPEC_DIMENSIONS.get((family, key))
            or _SPEC_DIMENSIONS.get(key))


def _spec_unit_recognised(key, unit, family=None):
    """True when `unit` (the unit key design_input_specs[key] is stated in,
    _stated_unit) is a unit of the key's dimension in the built-in table
    (R5.2); False for no, a blank / ambiguous, or an unrecognised unit."""
    dimension = _spec_dimension(key, family)
    return bool(unit) and unit in _UNIT_FACTORS.get(dimension, {})


def _split_basis(chemicals):
    """R5.4: the length a list-valued split vector must have -- the number
    of chemicals carrying an integer `index` -- when those indices are
    exactly 0..n-1 (n >= 1), else None (no list split is readable). BioSTEAM
    orders a split vector by the thermo's chemical order, which `index`
    records."""
    indices = [c['index'] for c in chemicals
               if isinstance(c.get('index'), int)
               and not isinstance(c.get('index'), bool)]
    return (len(indices)
            if indices and sorted(indices) == list(range(len(indices)))
            else None)


def _split_readable(value, basis, chem_ids):
    """R5.4: False for a split vector that cannot be mapped to chemicals --
    a list whose length is not the _split_basis `basis` (or no basis), or a
    dict with a key that is not a declared chemical id (`chem_ids`); True
    otherwise (a scalar split, or a readable vector)."""
    if isinstance(value, list):
        return basis is not None and len(value) == basis
    if isinstance(value, dict):
        return all(key in chem_ids for key in value)
    return True


def _internal_unit(unit, index, basis, chem_ids):
    """(copy of `unit` for scoring, {spec key: stated unit key}) (R5.2 /
    R5.4): each dimensional design_input_specs value (_SPEC_DIMENSIONS; a
    list, such as an evaporator's pressures, element-wise) converted to
    BioSTEAM's internal unit from its inline or quantity_units_global unit
    (_to_internal), and a split vector that cannot be mapped to chemicals
    (_split_readable) dropped -- INVALID == MISSING. The second value
    records the unit key each spec key is stated in (_stated_unit), for
    CMP-UOP-05. The document is not modified."""
    specs = unit.get('design_input_specs')
    if not isinstance(specs, dict):
        return unit, {}
    out, units = {}, {}
    for key, value in specs.items():
        if isinstance(key, str):
            stated = _stated_unit(value, key, index)
            if stated is not None:
                units[key] = stated
        dimension = _SPEC_DIMENSIONS.get(key) if isinstance(key, str) else None
        if dimension is not None:
            if isinstance(value, list):
                value = [_to_internal(v, key, index, dimension) for v in value]
            else:
                value = _to_internal(value, key, index, dimension)
        if key in _SPLIT_KEYS and not _split_readable(value, basis, chem_ids):
            continue
        out[key] = value
    return dict(unit, design_input_specs=out), units


def _converted(obj, index):
    """Shallow copy of a dict with each quantity field (_QUANTITY_DIMENSIONS)
    it holds converted by _to_internal; non-dicts come back unchanged."""
    if not isinstance(obj, dict):
        return obj
    out = dict(obj)
    for field in _QUANTITY_DIMENSIONS:
        if field in out:
            out[field] = _to_internal(out[field], field, index)
    return out


def _internal_stream(stream, index):
    """Copy of a stream whose stream_properties and phases hold their
    temperature, pressure and flows in BioSTEAM's internal units
    (_converted); compositions are shared, the document is not modified."""
    sp = stream.get('stream_properties')
    if not isinstance(sp, dict):
        return stream
    sp = _converted(sp, index)
    if isinstance(sp.get('phases'), dict):
        sp['phases'] = {symbol: _converted(phase, index)
                        for symbol, phase in sp['phases'].items()}
    return dict(stream, stream_properties=sp)


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


def _same_counts(a, b):
    """True when two {element: count} maps agree (counts within 1e-9)."""
    return (a is not None and b is not None and set(a) == set(b)
            and all(abs(a[k] - b[k]) <= 1e-9 for k in a))


def _record_agreement(record, chem, rtol=NAME_MW_RTOL):
    """'verified' when a database record's molar mass matches the chemical's
    (declared or formula-derived) within `rtol` (NAME_MW_RTOL for the name
    tier, REGISTRY_MW_RTOL for a registry id), or their formulas match;
    'unverified' when either side states nothing comparable; 'mismatch'
    otherwise (R2.3, R3.2)."""
    chem = _as_dict(chem)
    mine_mw, theirs_mw = _chemical_molar_mass(chem), _record_molar_mass(record)
    mine_f = _formula_counts(chem.get('formula'))
    theirs_f = _formula_counts(getattr(record, 'formula', None))
    if mine_mw is not None and theirs_mw is not None \
            and abs(mine_mw - theirs_mw) <= rtol * theirs_mw:
        return 'verified'
    if _same_counts(mine_f, theirs_f):
        return 'verified'
    comparable = ((mine_mw is not None and theirs_mw is not None)
                  or (mine_f is not None and theirs_f is not None))
    return 'mismatch' if comparable else 'unverified'


def _name_tier(chem, cid):
    """CMP-THM-02 name tier of chemical id `cid` (chem: its declared entry,
    or None): None when the id is not a string, is an isomer-ambiguous
    formula (_isomer_ambiguous), or does not resolve by name as
    thermosteam's Chemical(ID) resolves it (_name_record: the database,
    then the built-in pseudo-components); else the _record_agreement of the
    resolved record with the chemical."""
    if not _nonempty_str(cid) or _isomer_ambiguous(cid):
        return None
    record = _name_record(cid)
    return None if record is None else _record_agreement(record, chem)


_CAS_RE = re.compile(r'^(\d{2,7})-(\d{2})-(\d)$')
_INCHI_RE = re.compile(r'^InChI=1S?/(\S+)$')
# The mobile-proton layer of an InChI ('/p+1': one proton more than the
# formula layer states).
_INCHI_PROTON_RE = re.compile(r'/p([+-]\d+)(?=/|$)')
_INCHI_COMPONENT_RE = re.compile(r'^(\d*)(\S+)$')
_INCHIKEY_RE = re.compile(r'^(?:InChIKey=)?([A-Z]{14}-[A-Z]{10}-[A-Z])$')
_SMILES_PREFIX_RE = re.compile(r'^SMILES=', re.IGNORECASE)
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


def _inchi_formula(inchi):
    """{element: count} of an InChI's formula layer -- the text after
    'InChI=1S/' up to the next '/', its dot-separated components (each with
    an optional leading multiplier, '2Na.H2O4S') summed, plus the protons of
    a mobile-proton layer ('/p+1') -- or None when `inchi` is no InChI or
    its formula layer does not parse."""
    match = _INCHI_RE.match(inchi.strip()) if isinstance(inchi, str) else None
    if not match:
        return None
    body = match.group(1)
    counts = {}
    for component in body.split('/', 1)[0].split('.'):
        parts = _INCHI_COMPONENT_RE.match(component)
        element_counts = _formula_counts(parts.group(2)) if parts else None
        multiplier = int(parts.group(1)) if parts and parts.group(1) else 1
        if element_counts is None or multiplier < 1:
            return None
        for symbol, n in element_counts.items():
            counts[symbol] = counts.get(symbol, 0) + multiplier * n
    protons = _INCHI_PROTON_RE.search(body)
    if protons:
        counts['H'] = counts.get('H', 0) + int(protons.group(1))
        if counts['H'] < 0:
            return None
        if counts['H'] == 0:
            del counts['H']
    return counts or None


def _counts_molar_mass(counts):
    """Molar mass of an {element: count} map (via the validator's formula
    parser), or None."""
    if not counts:
        return None
    formula = ''.join(f'{symbol}{n:g}' for symbol, n in sorted(counts.items()))
    return _validator()._molar_mass_from_formula(formula)


def _inchi_contradicts(inchi, chem):
    """True when an InChI's formula layer (_inchi_formula) disagrees with
    the chemical's own parseable formula, or -- when the chemical states no
    parseable formula -- with its declared or formula-derived molar mass
    beyond REGISTRY_MW_RTOL (R3.2). Nothing comparable -> False."""
    layer = _inchi_formula(inchi)
    chem = _as_dict(chem)
    mine = _formula_counts(chem.get('formula'))
    if layer is None:
        return False
    if mine is not None:
        return not _same_counts(layer, mine)
    mine_mw, theirs_mw = _chemical_molar_mass(chem), _counts_molar_mass(layer)
    return (mine_mw is not None and theirs_mw is not None
            and abs(mine_mw - theirs_mw) > REGISTRY_MW_RTOL * theirs_mw)


def _registry_query(rid):
    """(kind, database query) of a well-formed registry id, else (None,
    None) (R2.0/R3.2). Kinds: 'CAS' (format and check digit), 'InChI' (its
    formula layer parses, _inchi_formula), 'InChIKey' (14-10-1 capitals,
    an 'InChIKey=' prefix allowed) and 'SMILES' (_smiles_like, a 'SMILES='
    prefix allowed). The query is the string
    `chemicals.identifiers.search_chemical` parses as that kind: CAS numbers
    and InChIs as they are, 'InChIKey=<key>' and 'SMILES=<text>' (a bare
    'C' would otherwise resolve to the element carbon, not methane)."""
    if not isinstance(rid, str):
        return None, None
    rid = rid.strip()
    if _valid_cas(rid):
        return 'CAS', rid
    if _inchi_formula(rid) is not None:
        return 'InChI', rid
    key = _INCHIKEY_RE.match(rid)
    if key:
        return 'InChIKey', f'InChIKey={key.group(1)}'
    smiles = _SMILES_PREFIX_RE.sub('', rid)
    if _smiles_like(smiles):
        return 'SMILES', f'SMILES={smiles}'
    return None, None


def _registry_status(chem, cid=None):
    """R3.2 status of a chemical's registry_id: 'absent' (none stated),
    'malformed' (not a well-formed CAS/InChI/InChIKey/SMILES,
    _registry_query), 'contradicted' (an InChI whose formula layer, or a
    database record the id resolves to whose molar mass or formula,
    disagrees with the chemical's own -- REGISTRY_MW_RTOL), 'resolved' (the
    `chemicals` database resolves it and nothing contradicts it), 'unresolved'
    (well-formed, but the database does not know it: a real but unlisted
    number, or an invented one) or 'unavailable' (well-formed; the database
    cannot be imported) or 'conflicted' (R5.5: resolved, but the chemical
    states no molar mass or formula to settle it and its id -- `cid`,
    default the chemical's own -- resolves by name (_name_tier's lookup) to
    a record of another formula or molar mass, _records_disagree). Lookups
    are cached (_database_record: the database only, never a built-in
    pseudo-component); never raises."""
    chem = _as_dict(chem)
    rid = chem.get('registry_id')
    if rid is None or (isinstance(rid, str) and not rid.strip()):
        return 'absent'
    kind, query = _registry_query(rid)
    if kind is None:
        return 'malformed'
    if kind == 'InChI' and _inchi_contradicts(query, chem):
        return 'contradicted'
    if _chemicals_search() is None:
        return 'unavailable'
    record = _database_record(query)
    if record is None:
        return 'unresolved'
    agreement = _record_agreement(record, chem, REGISTRY_MW_RTOL)
    if agreement == 'mismatch':
        return 'contradicted'
    if agreement == 'unverified':
        cid = chem.get('id') if cid is None else cid
        named = (_name_record(cid) if _nonempty_str(cid)
                 and not _isomer_ambiguous(cid) else None)
        if named is not None and _records_disagree(record, named):
            return 'conflicted'
    return 'resolved'


def _records_disagree(a, b):
    """True when two database / built-in records name different species
    (R5.5): their formulas both parse and differ, else their molar masses
    (_record_molar_mass) both exist and differ by more than
    REGISTRY_MW_RTOL; False when nothing compares."""
    fa = _formula_counts(getattr(a, 'formula', None))
    fb = _formula_counts(getattr(b, 'formula', None))
    if fa is not None and fb is not None:
        return not _same_counts(fa, fb)
    ma, mb = _record_molar_mass(a), _record_molar_mass(b)
    return (ma is not None and mb is not None
            and abs(ma - mb) > REGISTRY_MW_RTOL * max(ma, mb))


def _database_resolved(chem, cid):
    """True when the chemical is identified as thermosteam would identify
    it (R3.2, R4.1): its registry id resolves in the `chemicals` database
    without contradiction, or its id resolves by name -- in the database or
    as a built-in pseudo-component (_name_record) -- with a verified or
    unverified record (_name_tier). With the database unavailable only a
    built-in resolves."""
    return ((chem is not None and _registry_status(chem, cid) == 'resolved')
            or _name_tier(chem, cid) in ('verified', 'unverified'))


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
# data also needs every species' balance to close. Every HOSTING family
# (R7.1: tank, distillation, MESH-type column, extractor) is one: without
# declared (stage) reactions it only separates or holds, and species
# converting across it leave its stream data unfit to derive a column's
# or a vessel's specifications from.
PHYSICAL_FAMILIES = frozenset({
    'mixer', 'hx_utility', 'hx_process', 'pump', 'valve', 'compressor',
    'turbine', 'flash', 'distillation', 'staged_column', 'extraction',
    'evaporator', 'steam_injection_heater', 'tank', 'splitter'})
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

# ---- R6.1 / R7.1: which claims can host chemistry ----
# Families whose claim, unless an exact class's code is known, CANNOT host
# chemistry (R6.1(a), widened by R7.1): no BioSTEAM unit of these families
# converts species -- an evaporator, crystallizer, conveyor or mill, or
# steam-injection heater concentrates, crystallizes, moves or heats what it
# is fed -- so species converted across such a claim (flows computable, no
# well-formed reaction declared) make it implausible.
NO_CHEMISTRY_FAMILIES = frozenset({
    'pump', 'valve', 'compressor', 'turbine', 'hx_utility', 'hx_process',
    'flash', 'mixer', 'splitter', 'junction', 'heat_exchanger_network',
    'evaporator', 'crystallizer', 'solids_handling',
    'steam_injection_heater'})
# HOSTING families (R7.1): a vessel, column or extractor may carry a
# biorefinery's neutralization, liquefaction or reactive separation. Such a
# claim without an exact class's code keeps its family whatever its streams
# show; it never enters the reactive population itself -- one flowsheet-level
# pseudo-member stands for all of them (_unexplained_hosts) -- and only a
# tank claim is also held to reactor rules (_chemistry_rules).
HOSTING_FAMILIES = frozenset({'tank', 'distillation', 'staged_column',
                              'extraction'})
# Families whose BioSTEAM units exchange no heat with an agent (a pump, a
# valve, a turbine; a compressor may, through its intercoolers): a heat duty
# contradicts their claim (R5.1).
_NO_HEAT_FAMILIES = frozenset({'pump', 'valve', 'turbine'})
# R8.3: families whose units may PRODUCE electricity (a turbine or expander,
# a boiler-turbogenerator or CHP, and a boiler or combustor, labels a paper
# may give a CHP unit): power a unit produces contradicts every other claim
# tested (_physics_contradiction).
_POWER_PRODUCING_FAMILIES = frozenset({'turbine', 'boiler',
                                       'boiler_turbogenerator', 'combustor'})
# Exact core classes that accept an optional reaction set (biosteam
# stage.py / distillation.py: MESHDistillation and MultiStageEquilibrium take
# stage_reactions, StageEquilibrium and PhasePartition a reaction): they can
# host chemistry, so they are treated like HOSTING_FAMILIES claims.
_OPTIONAL_CHEMISTRY_CLASSES = frozenset({
    'MESHDistillation', 'MultiStageEquilibrium', 'StageEquilibrium',
    'PhasePartition'})
# Species a combustion can produce (thermosteam's combustion reactions give
# CO2, H2O, SO2, N2, HCl, HF, Br2, I2 and P4O10; boilers also bind SO2 with
# lime and reject ash). R8.4: thermosteam writes each species' combustion
# as its own reaction, and the reaction of an oxygen-rich or oxidised
# species releases O2 ('NitricOxide -> 0.5 O2 + 0.5 N2', 'CaSO4 -> O2 +
# SO2 + 40.1 Ash'), which the set's other reactions consume; with the
# 'elemental' handling of chemicals.combustion its non-combustible atoms
# leave in their elemental state (_elemental_product).
_COMBUSTION_PRODUCTS = ('CO2', 'H2O', 'Water', 'SO2', 'N2', 'NO', 'NO2',
                        'N2O', 'HCl', 'HF', 'Br2', 'I2', 'P4O10', 'Ash',
                        'CaSO4', 'Gypsum', 'O2', 'Oxygen')
# Elements a combustion reaction turns into combustion products (the
# combustible elements of chemicals.combustion); any other element leaves
# a combustion in its elemental state.
_COMBUSTIBLE_ELEMENTS = frozenset({'C', 'H', 'N', 'O', 'S', 'Br', 'I', 'Cl',
                                   'F', 'P'})
_ELEMENT_KEY_RE = re.compile(r'f:([A-Z][a-z]?)\d+(?:\.\d+)?')
# R6.1(c): classes whose own code converts species (verified in the
# biosteam and Bioindustrial-Park sources), as (species it may produce,
# species it may consume -- None: any), each named by chemical id or plain
# formula. Exempt from R6.1(a) for exactly that chemistry: a conversion
# outside the signature still contradicts the class claim. DrumDryer burns
# natural gas (CH4 -> CO2 + 2 H2O into its emissions); ThermalOxidizer,
# Boiler, BoilerTurbogenerator and CHP burn their feeds; AmineAbsorption
# consumes its MEA and make-up water; ProcessWaterCenter keeps only the
# water of its recycles; the Bioindustrial-Park AmmoniaAdditionTank (HP,
# succinic, oxalic, BDO) neutralizes 2 NH4OH + H2SO4 -> (NH4)2SO4 + 2 H2O.
_BUILTIN_CHEMISTRY = {
    'DrumDryer': (('CO2', 'H2O', 'Water'), ('CH4', 'Methane', 'NaturalGas')),
    'ThermalOxidizer': (_COMBUSTION_PRODUCTS, None),
    'Boiler': (_COMBUSTION_PRODUCTS, None),
    'BoilerTurbogenerator': (_COMBUSTION_PRODUCTS, None),
    'CHP': (_COMBUSTION_PRODUCTS, None),
    'AmineAbsorption': ((), ('MEA', 'Monoethanolamine', 'Ethanolamine',
                             'C2H7NO', 'H2O', 'Water')),
    'ProcessWaterCenter': ((), None),
    'AmmoniaAdditionTank': (('AmmoniumSulfate', 'NH4SO4', 'H8N2O4S', 'H2O',
                             'Water'),
                            ('AmmoniumHydroxide', 'NH4OH', 'H5NO', 'NH3',
                             'Ammonia', 'H2SO4', 'SulfuricAcid')),
}
# The same for a claim of a family whose chemistry the class fixes, made
# without an exact class's code (a label, an unpinned custom class): a
# combustor or boiler burns its feeds; a facility at most discards species
# (a process-water center keeps only water) and produces none; a dryer at
# most burns its fuel (R7.1: its built-in combustion, as a DrumDryer fires
# natural gas) and converts nothing beyond it.
_FAMILY_CHEMISTRY = {
    'combustor': (_COMBUSTION_PRODUCTS, None),
    'boiler': (_COMBUSTION_PRODUCTS, None),
    'boiler_turbogenerator': (_COMBUSTION_PRODUCTS, None),
    'facility': ((), None),
    'dryer': (_COMBUSTION_PRODUCTS, None),
}
# Junction-family classes that conserve nothing species by species by
# design (a Scaler multiplies its inlet, a Duplicator copies it to every
# outlet, a MockSplitter's outlets are set elsewhere, a diagram-only unit is
# never simulated): only species appearing or vanishing contradicts them,
# and a label-only junction claim may be any of them.
_NONCONSERVING_JUNCTIONS = frozenset({
    'Scaler', 'Duplicator', 'MockSplitter', 'FakeSplitter',
    'DiagramOnlyUnit', 'DiagramOnlyStreamUnit', 'DiagramOnlySystemUnit'})
# Junction and Scaler pass their inlet's T and P on (the pure-splitter
# bounds apply); a Junction also conserves total mass (a Scaler does not).
_T_P_JUNCTIONS = frozenset({'Junction', 'Scaler'})

# Per-unit averages are UNWEIGHTED (R4.12, superseding R1.6's family
# weights): each unit counts once. A weight derived from deletable data (the
# family, read from unit_type / design_simulation_method) broke the deletion
# guarantee in every criterion whose per-unit score does not depend on it --
# deleting a costed pump's unit_type moved its weight and raised COST-01.
# Major equipment keeps its emphasis through its richer requirement sets.

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
    # R8.10: biosteam.wastewater's combined heat and power unit -- the
    # family its label keywords give (R8.2), scored in CMP-UOP-06 by its
    # own class rules (_FACILITY_RULES['CHP']).
    'CHP': ('boiler_turbogenerator', None),
    'CIPpackage': ('facility', None),
    'ChemicalCapitalInvestment': ('facility', None),
    'ChilledWaterPackage': ('facility', None),
    'CoolingTower': ('facility', None),
    'Facility': ('facility', None),
    'FakeSplitter': ('junction', None),  # biosteam's alias of MockSplitter
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
# hensmith package and re-exported by biosteam) and FakeSplitter (the
# exported alias of MockSplitter), enumerated once from the biosteam 2.54
# source tree and committed here, so the scorer needs no biosteam import.
# Only these classes identify a unit at confidence 1 without a custom-class
# location in the pinned model package (R3.6). Table entries outside it
# (Bioindustrial-Park classes, classes made by biosteam's tank_factory
# inside a biorefinery, and older biosteam names the corpus still carries:
# AdsorptionColumnTSA, NRELFermentation, NRELBatchBioreactor) are custom
# classes.
_CORE_CLASSES = frozenset({
    'AdiabaticMultiStageVLEColumn', 'AeratedBioreactor',
    'AerobicDigestion', 'AirDistributionPackage', 'AmineAbsorption',
    'AnMBR', 'AnaerobicBioreactor', 'AnaerobicDigestion',
    'BagassePelletMill', 'BatchCrystallizer', 'BeltThickener',
    'BinaryDistillation', 'BiogasUpgrading', 'BlowdownMixer', 'Boiler',
    'BoilerTurbogenerator', 'CHP', 'CIPpackage', 'CO2Compression',
    'ChemicalCapitalInvestment', 'ChilledWaterPackage', 'Clarifier',
    'ConveyingBelt', 'CoolingTower', 'CrushingMill', 'DrumDryer',
    'EnzymeTreatment', 'Evaporator', 'FacilityModule', 'FakeSplitter',
    'FireWaterTank',
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

# R4.8 port-count plausibility. STATIC, generated once from the biosteam
# source (no runtime import): for every core class, the most inlet / outlet
# streams (max inlets, max outlets) it can carry -- its _N_ins / _N_outs
# where the side is fixed-size (_ins_size_is_fixed / _outs_size_is_fixed),
# None where the side is variable (a False flag, or an instance-level
# override in the class's own methods: MultiStageEquilibrium,
# MultiStageMixerSettlers). Read at the biosteam 2.54 head and taken as the
# MOST PERMISSIVE value over 43 commits sampled across its history since
# 2022, so a file exported against an older biosteam is not contradicted by
# a later narrowing (ChilledWaterPackage and FireWaterTank had 1 / 1 ports
# before 2025, CoolingTower 3 / 3, Boiler 5 outlets, AnaerobicDigestion
# 2 / 4). Only a claim contradicted by MORE non-empty streams on a side than
# this allows is invalid (_ports_plausible).
_PORT_LIMITS = {
    **dict.fromkeys(('ChemicalCapitalInvestment', 'HeatExchangerNetwork'),
                    (0, 0)),
    **dict.fromkeys((
        'AirDistributionPackage', 'BagassePelletMill', 'BatchCrystallizer',
        'CIPpackage', 'CO2Compression', 'ChilledWaterPackage',
        'ConveyingBelt', 'FireWaterTank', 'HXutilities', 'HXutility',
        'HammerMill', 'IsenthalpicValve', 'IsentropicCompressor',
        'IsentropicTurbine', 'IsothermalCompressor', 'Junction',
        'MassBalance', 'MultistageCompressor', 'PelletMill',
        'PolytropicCompressor', 'Pump', 'RefrigerationPackage', 'Scaler',
        'ScrewFeeder', 'Shredder', 'StorageTank', 'WWTpump',
        'WastewaterSystemCost'), (1, 1)),
    **dict.fromkeys((
        'Clarifier', 'Flash', 'LLECentrifuge', 'LLESettler',
        'LiquidsPartitionSettler', 'LiquidsRatioCentrifuge',
        'LiquidsSplitCentrifuge', 'LiquidsSplitSettler', 'MagneticSeparator',
        'MembraneBioreactor', 'MolecularSieve', 'MultiEffectEvaporator',
        'PhasePartition', 'PhaseSplitter', 'RefluxDrum', 'ReverseOsmosis',
        'SplitFlash', 'Splitter', 'SprayDryer', 'VibratingScreen'), (1, 2)),
    **dict.fromkeys(('SLLECentrifuge', 'SolidLiquidsSplitCentrifuge'),
                    (1, 3)),
    **dict.fromkeys(('FakeSplitter', 'MockSplitter', 'ReversedSplitter'),
                    (1, None)),
    **dict.fromkeys(('BiogasUpgrading', 'HXprocess', 'VentScrubber'),
                    (2, 2)),
    'Evaporator': (2, 3),
    'AnaerobicDigestion': (2, 4),
    'ThermalOxidizer': (3, 1),
    **dict.fromkeys(('AerobicDigestion', 'AmineAbsorption'), (3, 2)),
    **dict.fromkeys(('CoolingTower', 'DrumDryer',
                     'SingleComponentAdsorptionColumn'), (3, 3)),
    'PolishingFilter': (3, 4),
    **dict.fromkeys(('FluidizedCatalyticCracking', 'ProcessWaterCenter'),
                    (4, 3)),
    'AnMBR': (6, 4),
    'BoilerTurbogenerator': (7, 3),
    'Boiler': (7, 5),
    **dict.fromkeys((
        'BlowdownMixer', 'CHP', 'EnzymeTreatment', 'LiquidsMixingTank',
        'MixTank', 'Mixer', 'MockMixer', 'ReactivePhaseStage',
        'SinglePhaseReactor', 'SinglePhaseStage', 'SteamMixer'),
        (None, 1)),
    **dict.fromkeys((
        'AdiabaticMultiStageVLEColumn', 'AeratedBioreactor',
        'AnaerobicBioreactor', 'BeltThickener', 'BinaryDistillation',
        'CrushingMill', 'GasFedBioreactor', 'MixerSettler',
        'NRELEthanolFermentation', 'PressureFilter', 'RotaryVacuumFilter',
        'ScrewPress', 'Separator', 'ShortcutColumn', 'SludgeCentrifuge',
        'SludgeHandling', 'SolidsCentrifuge', 'SolidsSeparator'),
        (None, 2)),
    'InternalCirculationRx': (None, 3),
    **dict.fromkeys((
        'FacilityModule', 'GibbsEquilibriumReactor', 'GroupedUnit',
        'MESHDistillation', 'Module', 'MultiStageEquilibrium',
        'MultiStageMixerSettlers', 'Skipped', 'StageEquilibrium'),
        (None, None)),
}

# R4.8 family conventions, for a claim no exact core class backs (a
# unit_type keyword family, a named abstract or custom class, a key-set
# inference): a mixer has one outlet; a pump, valve or utility heat
# exchanger one inlet and one outlet. Every core class of these families
# obeys them (SinglePhaseStage, the one multi-inlet hx_utility class, is
# only ever claimed exactly, by its own _PORT_LIMITS entry); a process-
# process exchanger (HXprocess, the family's one class) two of each. There
# is no splitter convention: the splitter family also holds the
# multi-inlet Separator, VentScrubber, AmineAbsorption, filters and presses.
_FAMILY_PORT_LIMITS = {'mixer': (None, 1), 'pump': (1, 1), 'valve': (1, 1),
                       'hx_utility': (1, 1), 'hx_process': (2, 2)}
# R6.3: fixed (inlets, outlets) port counts of corpus-known custom
# (Bioindustrial-Park) classes, read from their source like _PORT_LIMITS:
# PlantAir_CIP_WasteWater_Facilities (corn/units.py) subclasses bst.Facility
# without overriding BioSTEAM's fixed-size 1 / 1 Unit default. Only the
# placeholder-port cap reads it (_fixed_port_counts).
_NONCORE_PORT_LIMITS = {'PlantAir_CIP_WasteWater_Facilities': (1, 1)}
# R4.14: inlets (non-empty or of unknown flow) a mixer family claim needs
# when no exact class backs it.
MIXER_MIN_INLETS = 2
# A bare 'heat exchanger' / 'HX' head noun: BioSTEAM labels utility and
# process-process exchangers alike ('Heat exchanger' is both classes' line).
_ANY_EXCHANGER_RE = re.compile(r'(?:heat )?exchanger|hx')

# Keyword families (R4.4: head-noun matching). A unit_type or class name is
# read as a phrase whose HEAD noun -- the rightmost equipment match -- names
# the equipment: 'Wastewater pump' is a pump, 'Waste water cooler' a
# utility heat exchanger. Where several equipment patterns end at the same
# place the longest match wins, so compound names keep their specific family
# ('Mixer settler' extraction, not a settler; 'Steam mixer' a steam-injection
# heater; 'MESH distillation' / 'Stripping column' staged columns; 'Crushing
# mill' a splitter, not a mill; 'Heat exchanger network'; 'Aerated
# bioreactor'; 'Batch co-fermentation'). Two exceptions override the head:
# a reaction, flash or settling word DIRECTLY before a vessel head (tank,
# vessel, drum) makes it a reactor, flash or splitter ('Neutralization
# tank', 'Fermentation tank', 'Reaction vessel', 'Flash drum', 'Decanter
# tank'; R8.1: _vessel_modifier); a feed/effluent, interchanger, economizer
# or recuperator word with a heat-exchanger head makes it a process-process
# exchanger ('Feed/effluent heat exchanger'). A junction marker (junction,
# scaler, duplicator, mass balance, diagram only, mock) anywhere wins
# outright.
_EQUIPMENT_KEYWORDS = tuple((family, re.compile(pattern)) for family, pattern in (
    # R8.2: a combined heat and power (CHP) or cogeneration unit is one too.
    ('boiler_turbogenerator', r'turbo ?generator|combined heat (?:and|&) '
                              r'power|\bchp\b|cogenerat\w*'),
    ('heat_exchanger_network', r'heat exchanger network|\bhxn\b'),
    # R5.7: a fire-water tank (BioSTEAM's FireWaterTank) is a facility.
    ('facility', r'cooling tower|fire ?water(?: storage)?(?: tank)?'),
    # R8.2: an extraction, LLE or wash column / tower is an extractor (its
    # head absorbed, like the adsorber's); only the word 'extraction' takes
    # a column, so 'Extractive distillation column' stays a column.
    ('extraction', r'(?:(?:liquid ?liquid|\blle\b|solvent|counter ?current) )?'
                   r'extraction (?:column|tower)|'
                   r'(?:liquid ?liquid|wash(?:ing)?) (?:column|tower)|'
                   r'mixer ?settlers?|extract\w*|liquid ?liquid|'
                   r'\blle\b(?: (?:centrifuge|settler|column|tower))?'),
    ('steam_injection_heater', r'steam ?mixer|jet ?cooker|steam injection'
                               r'(?: heater)?|direct steam(?: \w+)?'),
    # R8.2: an absorption or stripping tower is a staged column too.
    ('staged_column', r'\bmesh\b(?: distillation| column)?|multi ?stage '
                      r'(?:equilibrium|vle)(?: column)?|adiabatic multi ?stage'
                      r'(?: (?:equilibrium|vle))?(?: column)?|'
                      r'absor(?:ber|ption (?:column|tower))|'
                      r'stripp(?:er|ing (?:column|tower))'),
    # R8.2: a flare burns its feed like a thermal oxidizer.
    ('combustor', r'combust\w*|oxidi[sz]er|furnace|incinerat\w*|burner|'
                  r'\bflares?\b'),
    ('dryer', r'dryer|drier\b|drying'),
    ('equilibrium_reactor', r'(?:gibbs )?equilibrium reactor|gibbs'),
    # R8.2: a bare bubble column is a gas-sparged (aerated) bioreactor.
    ('aerated_bioreactor', r'\baerated(?: (?:bio)?reactor| ferment\w*)?|'
                           r'gas ?fed(?: (?:bio)?reactor| ferment\w*)?|'
                           r'bubble columns?\b'),
    ('batch_bioreactor', r'batch ?(?:bio)?reactor|batch ?(?:co ?)?ferment\w*|'
                         r'\bssf\b|simultaneous saccharification'
                         r'(?: (?:and )?ferment\w*)?'),
    ('hx_process', r'hx ?process|interchanger|economi[sz]er|recuperat\w*'),
    # R5.7: a chiller or a steriliser is a utility-heated / -cooled
    # exchanger; a fan or blower a compressor; an expander a turbine; a kiln
    # a reactor; a knock-out drum a flash; ion exchange and an adsorber
    # (adsorption column) a splitter. R8.2: a heat pump moves heat (never
    # a pump).
    ('hx_utility', r'hx ?utilit\w*|heat exchanger|heater|cooler|condenser|'
                   r'reboiler|\bhx\b|chiller|sterili[sz]\w*|heat pumps?'),
    ('pump', r'pump'),
    ('valve', r'valve'),
    ('compressor', r'compress\w*|blower|\bfans?\b'),
    ('turbine', r'turbine|expander'),
    ('evaporator', r'evaporat\w*'),
    ('flash', r'flash|knock ?out(?: (?:drum|pot|vessel))?'),
    ('crystallizer', r'crystalli\w*'),
    # R6.1(d): the reaction steps ammonia addition, (over)liming,
    # conditioning, acidification, (trans)esterification and mashing name a
    # reactor too ('Ammonia addition tank', 'Overliming tank', 'Mash tank'
    # through the vessel exception). R7.5: so do a reformer and an
    # autoclave. R8.2: and a converter, a hydrocracker, a calciner, a CSTR
    # and a (reaction) kettle -- a 'Kettle reboiler' stays an exchanger.
    # A trickling or (an)aerobic filter and a biofilter are biofilm
    # reactors, longer than the splitter's 'filter' at the same head, so
    # they win it. A 'Polishing filter' stays a filter (R8.2): in general
    # usage it is a fine, cartridge or tertiary media filter; the
    # biological one (biosteam.wastewater's PolishingFilter, a reactor in
    # _CLASS_TABLE) declares its reactions, so R8.4 holds it to reactor
    # rules and its heat duty is no contradiction.
    ('reactor', r'reactor|ferment\w*|saccharif\w*|digest\w*|hydroly\w*|'
                r'liquefaction|\brx\b|\bmbr\b|seed train|neutrali[sz]\w*|'
                r'hydrotreat\w*|gasif\w*|pyroly\w*|oxidation|\bkilns?\b|'
                r'ammonia ?addition|(?:over)?liming|condition\w*|'
                r'acidif\w*|esterif\w*|\bmash(?:ing)?\b|reformer|'
                r'autoclave|converter|hydrocrack\w*|calcin\w*|\bcstr\b|'
                r'kettles?|(?:trickling|(?:an)?aerobic) filters?|'
                r'\bbio ?filters?'),
    # R7.5: ultra-, nano- and microfiltration, pervaporation and pressure
    # swing adsorption (PSA, a whole token only) separate too; a membrane
    # is a splitter unless a later head names more ('Membrane bioreactor'
    # stays a reactor). Like the adsorber and ion-exchange alternatives,
    # both PSA forms absorb a trailing column/bed head, so 'Pressure swing
    # adsorption column' and 'PSA column' stay splitters rather than
    # yielding the head to distillation. R8.2: so do chromatography, resin,
    # (activated) carbon, guard and simulated-moving-bed (SMB) columns and
    # beds; reverse osmosis (RO, a whole token) and electrodialysis
    # separate too; a divider, tee or manifold only divides a stream (a
    # pure-splitter word, _PURE_SPLITTER_WORD_RE). R8.2: 'Sludge handling'
    # names BioSTEAM's SludgeHandling, a splitter (sludge and water out),
    # longer than the solids-handling 'handling' at the same head.
    ('splitter', r'splitter|\bsplit\w*|centrifuge|filter|\bpress\b|screen|'
                 r'separator|decanter|settler|sieve|scrubber|clarifier|'
                 r'adsor[bp]\w*(?: (?:column|bed))?|absorp\w*|membrane|'
                 r'cyclone|osmosis|thickener|crushing mill|'
                 r'ion ?exchang\w*(?: (?:column|bed|resin))?|'
                 r'(?:ultra|nano|micro) ?filtration|pervaporat\w*|'
                 r'\bpsa\b(?: (?:column|bed))?|'
                 r'pressure swing adsorption(?: (?:column|bed))?|'
                 r'chromatograph\w*(?: (?:columns?|beds?))?|'
                 r'(?:resin|(?:activated )?carbon|guard) (?:columns?|beds?)|'
                 r'activated carbon|(?:simulated moving bed|\bsmb\b)'
                 r'(?: (?:columns?|beds?|chromatograph\w*))?|'
                 r'\bro\b|electrodialy\w*|dividers?|\btees?\b|manifolds?|'
                 r'sludge handling'),
    ('distillation', r'distillation|column|rectifier'),
    ('tank', r'tank|storage|vessel|hopper|\bsilo|\bbin\b|\bdrum\b'),
    # R7.5: a pelletizer (pelletiser) handles solids.
    ('solids_handling', r'convey\w*|\bmill|crusher|grinder|screw|'
                        r'grain handling|hammer|handling|shredder|feeder|'
                        r'pelleti[sz]er'),
    ('mixer', r'mixer|\bmix\b|blend\w*'),
))
_JUNCTION_RE = re.compile(r'\bjunction|\bscaler\b|duplicator|mass balance|'
                          r'diagram only|\bmock\b')
# The vessel exception (a reaction, flash or settling word directly before a
# vessel head) and the process-exchanger exception (a heat-exchanger head
# with a feed/effluent word).
_VESSEL_RE = re.compile(r'tank|vessel|\bdrum\b')
_VESSEL_MODIFIER_FAMILIES = REACTOR_FAMILIES | {'flash'}
# R8.1 / R8.4: words that make a vessel head a reactor only as its direct
# modifier ('Reaction tank', 'Hydrogenation vessel', 'Continuous stirred
# tank', 'Pretreatment tank'), besides the reactor family's own keywords;
# and R8.2: those that make it a splitter ('Settling tank', 'Decanter tank',
# 'Separation vessel', 'Phase separator drum').
_VESSEL_REACTOR_MODIFIER_RE = re.compile(
    r'reaction|reacting|stirred|pre ?treat\w*|hydrogenat\w*|'
    r'polymeri[sz]\w*|precipitat\w*|isomeri[sz]\w*|saponif\w*|acidulat\w*|'
    r'conversion')
_VESSEL_SPLITTER_MODIFIER_RE = re.compile(
    r'settl\w*|decant\w*|sediment\w*|(?:phase )?separat(?:or|ion|ing)s?')
# R8.1: product nouns name what a vessel holds, not a reaction in it
# (hydrolysate, digestate, broth, beer, filtrate, liquor), and so does a
# reaction verb's past participle ('Fermented mash tank', 'Neutralized
# slurry tank'): neither makes a vessel a reactor.
_PRODUCT_NOUN_RE = re.compile(
    r'hydroly[sz]ates?|digestates?|broths?|beers?|filtrates?|liquors?|'
    r'(?:ferment|neutrali[sz]|hydroly[sz]|digest|saccharifi|liquefi|'
    r'condition|acidifi|esterifi|hydrotreat|gasifi|pyroly[sz]|oxidi[sz]|'
    r'hydrogenat|polymeri[sz]|precipitat|isomeri[sz]|saponifi|acidulat|'
    r'pre ?treat|mash|lim|react|convert|reform|calcin)ed')
_HX_PROCESS_MODIFIER_RE = re.compile(r'feed ?effluent|interchang|economi[sz]|'
                                     r'recuperat')
# Generic head nouns: a phrase ending in one names no equipment itself, so a
# service modifier decides its family ('Wastewater treatment', 'Chilled
# water package', 'Boiler unit').
_GENERIC_HEAD_RE = re.compile(r'\b(?:system|treatment|package|unit|plant|'
                              r'section|area|facilit\w*|center|centre)s?\b')
# Service modifiers, in precedence order: they name what a unit serves, not
# what it is, and decide the family only when the head noun is generic or
# absent -- named plant facilities first (the wastewater cost lump, plant
# air / CIP, fire water, water services), then wastewater treatment (scored
# as the reactor family of biosteam.wastewater's digesters, R2.10), then a
# boiler (R7.5: a heat recovery steam generator, HRSG, is one, and names
# the service of an 'HRSG feed pump' as a boiler does); a bare 'facility'
# last.
_SERVICE_KEYWORDS = tuple((family, re.compile(pattern)) for family, pattern in (
    ('facility', r'waste ?water system cost|plant air|air distribution|'
                 r'\bcip\b|cip ?package|fire ?water|chilled water|'
                 r'process water|cooling water|refrigeration'),
    ('reactor', r'waste ?water'),
    ('boiler', r'\bboiler\b|heat recovery steam generator|\bhrsg\b'),
    ('facility', r'facilit'),
))


def _normalized_texts(text):
    """(raw lowercase, CamelCase-split spaced lowercase) variants of a name,
    so 'HXutility' and 'GrainHandling' both match their keywords."""
    spaced = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ',
                    text)
    spaced = re.sub(r'[\s_\-/]+', ' ', spaced).strip().lower()
    return text.lower(), spaced


def _equipment_matches(text):
    """Every equipment keyword match in `text` as (end, -start, family),
    so max() picks the head: the rightmost, then the longest."""
    return [(m.end(), -m.start(), family)
            for family, pattern in _EQUIPMENT_KEYWORDS
            for m in pattern.finditer(text)]


def _phrase_family(text):
    """R4.4 head-noun family of one normalized phrase, or None (see the
    _EQUIPMENT_KEYWORDS comment)."""
    if _JUNCTION_RE.search(text):
        return 'junction'
    matches = _equipment_matches(text)
    generic = [m.end() for m in _GENERIC_HEAD_RE.finditer(text)]
    head = max(matches) if matches else None
    if head is None or (generic and max(generic) > head[0]):
        for family, pattern in _SERVICE_KEYWORDS:
            if pattern.search(text):
                return family
    if head is None:
        return None
    end, start, family = head
    if family == 'tank' and _VESSEL_RE.fullmatch(text, -start, end):
        family = _vessel_modifier(text, -start, matches) or family
    if family == 'hx_utility' and _HX_PROCESS_MODIFIER_RE.search(text):
        return 'hx_process'
    return family


def _vessel_modifier(text, start, matches):
    """R8.1: the family a vessel head (tank, vessel, drum) starting at
    `start` in normalized phrase `text` takes from the word DIRECTLY
    before it -- a reactor-family or flash keyword (`matches`, the phrase's
    _equipment_matches) or a vessel-only reactor word
    (_VESSEL_REACTOR_MODIFIER_RE: 'Reaction tank', 'Continuous stirred
    tank') gives that family, a settling / decanting / separating word
    (_VESSEL_SPLITTER_MODIFIER_RE) the splitter -- or None. Any word
    between them -- a service qualifier (feed, storage, surge, holding,
    buffer, day, product, effluent, ...: 'Fermenter feed tank', 'Reformer
    feed drum') or what the vessel holds ('Fermentation broth tank') --
    keeps the tank family, and so does a product noun or a reaction
    verb's past participle (_PRODUCT_NOUN_RE: 'Hydrolysate tank',
    'Fermented mash tank'). Such tanks stay hosting claims: R7.1 covers
    the chemistry they may hide."""
    found = [m for m in matches
             if m[2] in _VESSEL_MODIFIER_FAMILIES and m[0] <= start]
    for pattern, family in ((_VESSEL_REACTOR_MODIFIER_RE, 'reactor'),
                            (_VESSEL_SPLITTER_MODIFIER_RE, 'splitter')):
        found += [(m.end(), -m.start(), family)
                  for m in pattern.finditer(text, 0, start)]
    found = [m for m in found if not text[m[0]:start].strip()]
    if not found:
        return None
    end, begin, family = max(found)
    words = text[:end].split()
    # The participle qualifies the modifier ('fermented mash') unless it
    # belongs to a multi-word modifier match itself.
    qualified = (len(words) > 1 and -begin >= end - len(words[-1])
                 and _PRODUCT_NOUN_RE.fullmatch(words[-2]))
    if _PRODUCT_NOUN_RE.fullmatch(words[-1]) or qualified:
        return None
    return family


def _keyword_family(text):
    """R4.4 keyword family of `text` (a unit_type or a class name): the
    head-noun family (_phrase_family) of its CamelCase-split, separator-
    normalized form, else of its raw lowercase form ('HXutility',
    'CIPpackage'); None when neither names a family."""
    if not _nonempty_str(text):
        return None
    raw, spaced = _normalized_texts(text)
    return _phrase_family(spaced) or _phrase_family(raw)


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


#   R3.6: a repository URL -- https / ssh / git / pip "git+" / scp-like
#   "git@host:owner/name" -- up to its owner and name (".git" and any path,
#   "@<ref>", "#fragment" or "?query" after them ignored).
_REPO_URL_RE = re.compile(
    r'^\s*(?:git\+)?(?:(?:https?|ssh|git)://(?:[^@/\s]+@)?([^/:\s]+)'
    r'(?::\d+)?/|[^@/\s:]+@([^/:\s]+):)([^/\s]+)/([^/#?@\s]+?)'
    r'(?:\.git)?(?:[/#?@]|\s*$)', re.I)


def _repo_key(url):
    """(host, owner, name) of the repository a URL points into (lowercase;
    a leading 'www.' dropped), or None when `url` names no repository."""
    match = _REPO_URL_RE.match(url) if isinstance(url, str) else None
    if not match:
        return None
    host = (match.group(1) or match.group(2)).lower()
    host = host[4:] if host.startswith('www.') else host
    return host, match.group(3).lower(), match.group(4).lower()


def _pinned_repository(metadata):
    """R3.6: the repository key (_repo_key) of
    metadata.reproducibility.flowsheet_model_package.url when that package
    also pins a commit or version (_pinned); else None. Only a custom class
    whose source URL lies in this repository is identified exactly."""
    package = _as_dict(_as_dict(_as_dict(metadata).get('reproducibility'))
                       .get('flowsheet_model_package'))
    return _repo_key(package.get('url')) if _pinned(package) else None


def _class_confidence(class_name, source, pinned_repo):
    """Identity confidence of a class NAMED by a unit (R2.6, R3.6): 1.0 for
    a concrete core class (_is_core_class); None -- identifies a family at
    most -- for an abstract class (_ABSTRACT_CLASSES) or a class a biosteam
    URL claims but that is neither on the core list nor a curated table
    entry (tank_factory and legacy biosteam classes are table entries);
    else a custom class: 1.0 only when its source URL lies in the pinned
    model-package repository `pinned_repo` (_pinned_repository: same host,
    owner and name, and a commit or version pinned), else 0.75 -- without
    both, nothing locates the class's code at a fixed revision."""
    if class_name in _ABSTRACT_CLASSES:
        return None
    if _is_core_class(class_name, source):
        return 1.0
    if (_nonempty_str(source) and _BIOSTEAM_URL_RE.search(source)
            and class_name not in _CLASS_TABLE):
        return None
    repo = _repo_key(source)
    return 1.0 if repo is not None and repo == pinned_repo else 0.75


def _infer_class_from_specs(unit, registry):
    """(hint, family) from the unit's valid design_input_specs key set
    (R3.1): EVERY design-spec registry entry whose parameter set contains
    the key set belongs to ONE (known) family, the unambiguous inference;
    hint names the unique tightest such entry (else None) for messages only
    -- an inferred class is never used as the unit's class. None when the
    unit states no valid spec, no entry contains the key set, or the
    containing entries span several families. Deleting a key can only add
    containing entries, i.e. only add ambiguity (monotone)."""
    keys = _explicit_keys(unit)
    if not keys:
        return None
    supersets = []
    for name, entry in _as_dict(registry).items():
        params = set(_as_dict(_as_dict(entry).get('design_input_spec_params')))
        if keys <= params:
            supersets.append((len(params), name))
    families = {_class_family(name) for _size, name in supersets}
    if len(families) != 1 or None in families:
        return None
    tightest = min(size for size, _name in supersets)
    tied = [name for size, name in supersets if size == tightest]
    return (tied[0] if len(tied) == 1 else None), families.pop()


def _port_limits(class_name, family, core, label=None):
    """(max inlets, max outlets) a class / family claim allows (R4.8; None
    on a side: unlimited): an exactly named core class's _PORT_LIMITS entry,
    else, for a claim without a class, the convention of the exact classes
    its `label` names (_label_port_limits, R8.3), else its family's
    _FAMILY_PORT_LIMITS convention, else (None, None)."""
    if core and class_name in _PORT_LIMITS:
        return _PORT_LIMITS[class_name]
    if class_name is None:
        limits = _label_port_limits(family, label)
        if limits is not None:
            return limits
    return _FAMILY_PORT_LIMITS.get(family, (None, None))


# R8.3: the port conventions of a label-only claim that names exact classes
# sharing one. BioSTEAM's conveyors, mills, feeders, shredders and
# pelletizers (ConveyingBelt, HammerMill, ScrewFeeder, Shredder, PelletMill,
# BagassePelletMill) and its cost-only facilities (CIPpackage,
# FireWaterTank, AirDistributionPackage, WastewaterSystemCost) deliver one
# outlet. Their inlets are not capped: a mill fed fresh chips and screen
# oversize, or a conveyor carrying two solid feeds, merges material, which
# the claim allows (and physics bounds, _physics_contradiction). A pure
# splitter label caps neither side: one fed by several streams (an FSplit)
# is held to dividing their mixture (_unmixed). (A 'Sludge handling' label
# names BioSTEAM's SludgeHandling, a splitter: see the splitter keywords.)
_SOLIDS_LABEL_PORTS = (None, 1)
_COST_ONLY_LABEL_PORTS = (None, 1)


def _facility_label_kind(text):
    """_FACILITY_KIND_KEYWORDS kind (a _FACILITY_RULES key or 'cost_only')
    a class name or unit_type names, or None."""
    if not _nonempty_str(text):
        return None
    variants = _normalized_texts(text)
    for kind, pattern in _FACILITY_KIND_KEYWORDS:
        if any(pattern.search(v) for v in variants):
            return kind
    return None


def _label_port_limits(family, label):
    """R8.3: (max inlets, max outlets) of a `family` claim made without a
    class by `label` (a unit_type or a named abstract class), when the
    label names exact classes whose port conventions it then inherits,
    else None: a 'Junction' / 'Scaler' label that exact class's
    _PORT_LIMITS (N8); a solids-handling label (a conveyor, mill, grinder,
    feeder, shredder, pelletizer, grain handling) one outlet; a facility
    label its kind's class (_facility_label_kind: a 'Cooling tower' the
    CoolingTower's, a cost-only 'Plant air' or 'CIP package' one outlet);
    a heat-exchanger-network label the HeatExchangerNetwork's (no material
    ports). One inlet split into two outlets of different composition is
    a separation none of these claims can make. A pure-splitter label
    (_pure_splitter_label) takes any number of ports: its physics decide
    (_unmixed)."""
    if not _nonempty_str(label):
        return None
    if family == 'junction':
        exact = _junction_label_class(label)
        return _PORT_LIMITS[exact] if exact else None
    if family == 'solids_handling':
        return _SOLIDS_LABEL_PORTS
    if family == 'heat_exchanger_network':
        return _PORT_LIMITS['HeatExchangerNetwork']
    if family == 'facility':
        kind = _facility_label_kind(label)
        if kind == 'cost_only':
            return _COST_ONLY_LABEL_PORTS
        return _PORT_LIMITS.get(kind)
    return None


def _port_contradiction(class_name, family, core, ports, label=None):
    """R4.8: why a class / family claim is contradicted by `ports` -- the
    unit's (non-empty inlets, non-empty outlets) counts, streams of unknown
    flow included, None when unknown -- e.g. '2 non-empty outlet(s), at
    most 1 allowed'; None when the claim is plausible.

    R4.14: a mixer FAMILY claim without an exact class (`class_name` None:
    a unit_type keyword, a named abstract class, a key-set inference) also
    needs >= MIXER_MIN_INLETS inlets that are non-empty or of unknown flow
    -- a one-inlet 'Mixer' label mixes nothing, so it cannot shed the needs
    of the unit it hides (a reactor, an exchanger). Exact-class mixers use
    the static port table only. (A document whose label-only mixer has a
    single non-empty inlet beside stated-empty ones makes an implausible
    claim, so it is not a consistent document: deleting an empty inlet's
    flow there may restore the claim.)"""
    if ports is None:
        return None
    for side, count, limit in zip(('inlet', 'outlet'), ports,
                                  _port_limits(class_name, family, core,
                                               label)):
        if limit is not None and count > limit:
            return f'{count} non-empty {side}(s), at most {limit} allowed'
    if class_name is None and family == 'mixer' and ports[0] < MIXER_MIN_INLETS:
        return (f'{ports[0]} non-empty inlet(s), a mixer claim without an '
                f'exact class needs at least {MIXER_MIN_INLETS}')
    return None


# R5.1: classes whose claim is a PURE splitter (no heat, work or chemistry:
# every outlet at the inlet's T and P). Other splitter-family classes
# (solids separators, centrifuges, settlers, the T-setting Separator) are not.
PURE_SPLITTER_CLASSES = frozenset({'Splitter', 'PhaseSplitter',
                                   'MockSplitter', 'FakeSplitter'})
# BioSTEAM's MockSplitter (FakeSplitter is its alias) does nothing when
# simulated -- its outlets are set elsewhere -- so their T and P may differ
# from its inlet's legitimately (corpus S408 / S410: 265 -> 298 K, 10 -> 1
# atm): its claim is exempt from the T / P bounds, not from the species and
# duty checks.
MOCK_SPLITTER_CLASSES = frozenset({'MockSplitter', 'FakeSplitter'})
# R8.3: head nouns of a label that claims a PURE splitter (it only divides a
# stream). Any other splitter-family label (centrifuge, filter, press,
# screen, sieve, membrane, RO / UF / NF, PSA, thickener, scrubber, settler,
# decanter, chromatography, ...) may draw power and drop pressure.
_PURE_SPLITTER_WORD_RE = re.compile(
    r'splitters?|split\w*|dividers?|tees?|manifolds?')
# R8.3: a splitter qualified by 'component' (SuperPro Designer's Component
# Splitter, often fed several streams) sends each component its specified
# split: a separator label, not a pure splitter.
_COMPONENT_SPLITTER_RE = re.compile(r'\bcomponents? ?(?:splitters?|split\w*)')
# Head nouns of a label-only separator regenerated by a temperature swing
# (sorption, absorption and regeneration heads): an adsorber (TSA / PSA),
# an ion, cation or anion exchanger, a resin, carbon or guard bed,
# chromatography or SMB, an (amine) absorber or scrubber, and a molecular
# sieve (_MOLECULAR_SIEVE_RE: a bare 'Sieve' or 'Sieve screen' is a
# mechanical screen). BioSTEAM's MolecularSieve and AdsorptionColumnTSA
# add regeneration steam and cooling, so a heat duty is their duty, not a
# contradiction: such a label is held to species, pressure-rise and
# temperature-band evidence only (_physics_contradiction). Every other
# separator head (centrifuge, filter, press, screen, sieve, thickener,
# settler, decanter, clarifier, membrane, RO / UF / NF, cyclone) is
# mechanical, so a heat duty contradicts it.
_SORPTION_HEAD_RE = re.compile(
    r'adsor[bp]\w*(?: (?:column|bed))?|absor[bp]\w*|scrubbers?|'
    r'ion ?exchang\w*(?: (?:column|bed|resin))?|'
    r'\bpsa\b(?: (?:column|bed))?|'
    r'pressure swing adsorption(?: (?:column|bed))?|'
    r'chromatograph\w*(?: (?:columns?|beds?))?|'
    r'(?:resin|(?:activated )?carbon|guard) (?:columns?|beds?)|'
    r'activated carbon|(?:simulated moving bed|\bsmb\b)'
    r'(?: (?:columns?|beds?|chromatograph\w*))?')
_MOLECULAR_SIEVE_RE = re.compile(r'molecular ?sieves?$')
# R8.3: the temperature band (K) a label-only separator, mixer or
# solids-handling claim keeps its outlets in: [coldest inlet - LABEL_T_ABS,
# hottest inlet + LABEL_T_ABS] (a mixer and a solids handler: the upper
# bound only). Wider than GUARD_T_ABS, since such a unit may do work on its
# streams (a centrifuge, a mill) without a heat duty.
LABEL_T_ABS = 5.0
# R8.3 (N8): the junction word a label-only junction claim names, and the
# exact class whose bounds it is then held to (a 'Junction' label to the
# Junction's, a 'Scaler' label to the Scaler's).
_JUNCTION_LABEL_CLASSES = (('Junction', re.compile(r'\bjunction')),
                           ('Scaler', re.compile(r'\bscaler\b')))


def _recorded_duty(sc, position):
    """R5.1: {'heat': locator, 'power': locator} of the first non-zero
    duty of each kind unit `position` records -- a numeric, non-zero
    utility_consumption_results / utility_production_results entry; its
    agent's group decides the kind: power_utilities -> 'power',
    heat_utilities or an undeclared agent (kind unknown) -> 'heat',
    other_utilities (material flows, not duties) -> ignored. R8.3: a
    power entry the unit PRODUCES (a positive production result, as the
    exporter files a turbine's or generator's output, or a negative
    consumption) is reported under 'power_out' too."""
    utilities = _as_dict(sc.doc.get('utilities'))
    groups = {}
    for group in ('heat_utilities', 'power_utilities', 'other_utilities'):
        for agent in _dicts(utilities.get(group)):
            if _nonempty_str(agent.get('id')):
                groups.setdefault(agent['id'], group)
    found = {}
    unit = sc.units[position]
    for key in _UTILITY_RESULT_KEYS:
        for agent_id, value in _as_dict(unit.get(key)).items():
            group = groups.get(agent_id)
            number = _num(value)
            if group == 'other_utilities' or not number:
                continue
            kind = 'power' if group == 'power_utilities' else 'heat'
            found.setdefault(kind, f'{key}.{agent_id}')
            if kind == 'power' and (number > 0) == (
                    key == 'utility_production_results'):
                found.setdefault('power_out', f'{key}.{agent_id}')
    return found


def _physics_contradictions(sc, position):
    """R5.1: the stream / duty evidence against a need-removing claim on
    unit `position`, as {kind: reason} (kinds absent when there is none):
    'heat' / 'power' -- it records a non-zero heat (or unknown-kind) /
    power duty (_recorded_duty); 'species' -- declaring no well-formed
    reaction, species appear or vanish across it (_species_change, flows
    computable); 'mixer' -- some outlet's T exceeds the hottest inlet's by
    more than GUARD_T_ABS, or its P the highest inlet's by more than
    GUARD_P_RTOL (upper bounds only: a mixer may cool, and mixes at the
    lowest inlet pressure); 'splitter' -- some outlet's T or P differs
    from its one inlet's by more than GUARD_T_ABS / GUARD_P_RTOL. R8.3:
    'rise' -- some outlet's P exceeds the highest inlet's by more than
    GUARD_P_RTOL (the pressure half of 'mixer'); 'hot' / 'cold' -- some
    outlet's T lies more than LABEL_T_ABS above the hottest / below the
    coldest inlet's (the band of a label-only claim). Streams
    stated empty carry nothing and are ignored; a bound is tested only when
    every non-empty inlet states the quantity, and only outlets stating it
    are compared -- deleting a value never creates a contradiction.
    R6.1: 'conversion' -- declaring no well-formed reaction, with its total
    mass closing (_mass_closure: else a missing port explains any species
    imbalance), some species that neither appears nor vanishes is still
    produced or consumed beyond CLOSURE_RTOL (_species_conversion);
    'changed' -- under the same conditions, not a reason but the
    ({species: identity keys} produced, {...} consumed) pair of every
    converted species (appearing and vanishing ones included), which a
    built-in chemistry signature is matched against; 'mass' -- the total
    mass balance is evaluable and does not close (_mass_balance). R8.3:
    'rise_above_atm' -- a 'rise' whose highest outlet ends above 1 atm
    (beyond GUARD_P_RTOL); 'power_out' -- power the unit produces
    (_recorded_duty); 'separation' -- its outlets separate its feed
    (_separation); 'unmixed' -- fed by two or more streams, an outlet does
    not carry their mixture (_unmixed).
    _physics_contradiction decides which kinds contradict which claim."""
    uid = sc.units[position].get('id')
    if not _nonempty_str(uid):
        return {}
    ins, outs = ([sc.streams[i] for i in index.get(uid, [])
                  if not _is_empty_stream(sc.streams[i])]
                 for index in (sc.unit_inlets, sc.unit_outlets))
    found = {kind: (f'produces power ({where})' if kind == 'power_out'
                    else f'records a non-zero {kind} duty ({where})')
             for kind, where in _recorded_duty(sc, position).items()}
    if not sc.unit_reacts[position]:
        change = _species_change(sc, uid)
        if change:
            found['species'] = (f"{', '.join(change[:3])} without declared "
                                f"reactions")
        # A species balance is evidence of conversion only where the total
        # mass closes: otherwise a missing port (a legacy export's lost
        # outlet) explains the imbalance as well.
        converted = (_species_conversion(sc, uid)
                     if _mass_closure(sc, position)[0] else None)
        if converted and any(converted):
            produced, consumed = converted
            shown = {c.split(' ', 1)[0] for c in change or ()}
            extra = ([f'{n} produced' for n in produced if n not in shown]
                     + [f'{n} consumed' for n in consumed if n not in shown])
            if extra:
                found['conversion'] = (
                    f"{', '.join(extra[:3])} beyond {CLOSURE_RTOL:.0%} "
                    f"without declared reactions")
            formulas = {c.get('id'): c.get('formula') for c in sc.chemicals}
            found['changed'] = tuple(
                {n: _species_keys(n, formulas.get(n)) for n in names}
                for names in (produced, consumed))
    evaluable, closes, why = _mass_balance(sc, position)
    if evaluable and not closes:
        found['mass'] = why
    separated = _separation(ins, outs)
    if separated:
        found['separation'] = separated
    unmixed = _unmixed(ins, outs)
    if unmixed:
        found['unmixed'] = unmixed
    if _dicts(sc.units[position].get('reactions')):
        # Not a reason: the unit declares reactions of its own (a non-empty
        # list of reaction objects, as _declared_chemistry counts them).
        found['declares'] = True
    mixer, splitter = [], []
    for part, name in (('T', 'temperature'), ('P', 'pressure')):
        inlet = [_stream_value(s, part) for s in ins]
        outlet = [v for v in (_stream_value(s, part) for s in outs)
                  if v is not None]
        if not inlet or any(v is None for v in inlet) or not outlet:
            continue
        top = max(inlet)
        limit = (top + GUARD_T_ABS if part == 'T'
                 else top * (1.0 + GUARD_P_RTOL))
        if max(outlet) > limit:
            mixer.append(f'outlet {name} {max(outlet):.6g} above the '
                         f'highest inlet {name} {top:.6g}')
            if part == 'P':
                found['rise'] = mixer[-1]
                if max(outlet) > _ATM * (1.0 + GUARD_P_RTOL):
                    # R8.3: a rise beyond the 1 atm a built-in pump
                    # returns its outlets to.
                    found['rise_above_atm'] = (
                        f'{mixer[-1]}, and above 1 atm')
        # R8.3: the looser band of a label-only claim (LABEL_T_ABS).
        if part == 'T' and max(outlet) > top + LABEL_T_ABS:
            found['hot'] = (f'outlet temperature {max(outlet):.6g} more '
                            f'than {LABEL_T_ABS:g} K above the hottest '
                            f'inlet temperature {top:.6g}')
        if part == 'T' and min(outlet) < min(inlet) - LABEL_T_ABS:
            found['cold'] = (f'outlet temperature {min(outlet):.6g} more '
                             f'than {LABEL_T_ABS:g} K below the coldest '
                             f'inlet temperature {min(inlet):.6g}')
        if len(inlet) == 1:
            off = [v for v in outlet if abs(v - top) > (
                GUARD_T_ABS if part == 'T' else GUARD_P_RTOL * top)]
            if off:
                splitter.append(f'outlet {name} {off[0]:.6g} differs from '
                                f'the inlet {name} {top:.6g}')
    for kind, reasons in (('mixer', mixer), ('splitter', splitter)):
        if reasons:
            found[kind] = '; '.join(reasons)
    return found


def _fractions(flows):
    """{species: fraction} of {species: flow}, or None for no flow."""
    total = sum(flows.values())
    return ({k: v / total for k, v in flows.items()}
            if total > ZERO_FLOW else None)


def _same_composition(a, b):
    """True when two {species: fraction} compositions agree: no species'
    fractions differ by more than both CLOSURE_RTOL of the larger and
    SPECIES_THROUGHPUT_RTOL absolute (the R8.6 pair, read on fractions: a
    printed table rounding each stream on its own stays inside it)."""
    for name in set(a) | set(b):
        x, y = a.get(name, 0.0), b.get(name, 0.0)
        if abs(x - y) > max(CLOSURE_RTOL * max(x, y),
                            SPECIES_THROUGHPUT_RTOL):
            return False
    return True


def _separation(ins, outs):
    """R8.3: why the non-empty ports `ins` / `outs` show a
    separation -- two or more outlets, one of whose composition matches
    neither any inlet's nor the inlets' mixed composition
    (_same_composition) -- or None (fewer than two outlets, or species
    flows not computable on one basis over every port: a deleted value
    never creates the evidence). A junction or a process heat exchanger
    passes each stream through unchanged, so a separation contradicts
    such a claim (_physics_contradiction)."""
    if not ins or len(outs) < 2:
        return None
    computed = _basis_flows(list(ins) + list(outs))
    if computed is None:
        return None
    _basis, flows = computed
    mixed = {}
    for flow in flows[:len(ins)]:
        for name, value in flow.items():
            mixed[name] = mixed.get(name, 0.0) + value
    candidates = [c for c in (_fractions(f) for f in flows[:len(ins)]
                              + [mixed]) if c is not None]
    for flow in flows[len(ins):]:
        out = _fractions(flow)
        if out is None or any(_same_composition(out, c)
                              for c in candidates):
            continue
        return ('an outlet composition differs from every inlet\'s and '
                'from the inlets mixed (a separation)')
    return None


def _unmixed(ins, outs):
    """R8.3: why the non-empty ports `ins` / `outs` of a unit fed by two or
    more streams show something other than one mixture divided -- an
    outlet whose composition differs from the inlets' mixed composition
    (_same_composition), unless every outlet is a single phase and no two
    share it (the mixture split by phase) -- or None (fewer than two
    inlets, no outlet, or species flows or phases not computable: a
    deleted value never creates the evidence). A multi-feed pure splitter
    (an FSplit: its feeds mixed, then divided) passes the mixture to every
    outlet."""
    if len(ins) < 2 or not outs:
        return None
    computed = _basis_flows(list(ins) + list(outs))
    if computed is None:
        return None
    _basis, flows = computed
    mixed = {}
    for flow in flows[:len(ins)]:
        for name, value in flow.items():
            mixed[name] = mixed.get(name, 0.0) + value
    mixed = _fractions(mixed)
    if mixed is None:
        return None
    if all(out is not None and _same_composition(out, mixed)
           for out in (_fractions(f) for f in flows[len(ins):])):
        return None
    phases = [_phase_set(stream) for stream in outs]
    if (all(p is not None and len(p) == 1 for p in phases)
            and len(set().union(*phases)) == len(phases)):
        return None
    return ('an outlet composition differs from the mixture of its '
            f'{len(ins)} inlets (not one mixture divided)')


def _species_keys(name, formula=None):
    """R6.1(c): identity keys of a species, matched against a built-in
    chemistry signature (_BUILTIN_CHEMISTRY): its name lower-cased without
    separators ('n:co2', 'n:ammoniumsulfate') and the element counts of its
    name or declared formula where either is a plain formula ('f:C1O2'), so
    'CO2', 'CarbonDioxide' stated with formula CO2 and 'co2' all match."""
    keys = set()
    if _nonempty_str(name):
        keys.add('n:' + re.sub(r'[^a-z0-9]', '', name.lower()))
    for text in (name, formula):
        counts = _formula_counts(text)
        if counts:
            keys.add('f:' + ''.join(f'{el}{n:g}'
                                    for el, n in sorted(counts.items())))
    return frozenset(keys)


def _chemistry_claim(class_name, family, core, confidence):
    """R6.1: whether a class / family claim can host chemistry --
    'reactor' (a reactor family: it declares its chemistry); 'builtin' (a
    _BUILTIN_CHEMISTRY class, at any confidence: its own code converts the
    species of its signature); 'class_code' (a custom class located in the
    pinned model package, confidence 1: its code may convert anything,
    EXC-02); 'capable' (a HOSTING claim, R7.1: an exact core class of
    _OPTIONAL_CHEMISTRY_CLASSES, or a claim without a known class's code --
    `class_name` None, or a custom class below confidence 1 that the
    curated _CLASS_TABLE does not know -- of a HOSTING_FAMILIES family:
    tank, distillation, staged column, extraction); 'none' (any other
    exact core class or unpinned curated biorefinery class, or such a
    claim of a NO_CHEMISTRY_FAMILIES family -- since R7.1 including
    evaporator, crystallizer, solids handling and steam-injection heater:
    it cannot host chemistry); 'builtin' too for such a claim of a
    _FAMILY_CHEMISTRY family (combustor, boiler, boiler-turbogenerator,
    facility, and since R7.1 dryer); else 'free' (no family)."""
    if family in REACTOR_FAMILIES:
        return 'reactor'
    if class_name is not None:
        if class_name in _BUILTIN_CHEMISTRY:
            return 'builtin'
        if not core and confidence == 1.0:
            return 'class_code'
        if core or class_name in _CLASS_TABLE:
            # A core class, or a corpus-known biorefinery class of the
            # curated table (its source read: none but AmmoniaAdditionTank
            # converts species) that is not pinned: its code is known.
            return ('capable' if class_name in _OPTIONAL_CHEMISTRY_CLASSES
                    else 'none')
    if family in NO_CHEMISTRY_FAMILIES:
        return 'none'
    if family in HOSTING_FAMILIES:
        return 'capable'
    if family in _FAMILY_CHEMISTRY:
        return 'builtin'
    return 'free'


def _builtin_contradiction(class_name, family, physics):
    """R6.1(c): why the species a built-in chemistry claim converts
    (physics['changed'], _physics_contradictions) exceed its signature --
    the class's _BUILTIN_CHEMISTRY entry, else its family's
    _FAMILY_CHEMISTRY entry: a species produced that the chemistry does not
    produce, or consumed that it does not consume -- or None (nothing
    converted, or all of it explained)."""
    signature = (_BUILTIN_CHEMISTRY.get(class_name)
                 or _FAMILY_CHEMISTRY.get(family))
    if signature is None:
        return None
    produced, consumed = physics.get('changed') or ({}, {})
    allowed = [None if names is None else
               frozenset().union(*(_species_keys(n) for n in names))
               for names in signature]
    combustion = signature[0] is _COMBUSTION_PRODUCTS
    bad = [f'{n} produced' for n, keys in produced.items()
           if not keys & allowed[0]
           and not (combustion and _elemental_product(keys))]
    if allowed[1] is not None:
        bad += [f'{n} consumed' for n, keys in consumed.items()
                if not keys & allowed[1]]
    if not bad:
        return None
    what = (class_name if class_name in _BUILTIN_CHEMISTRY
            else f'a {family} claim')
    return (f"implausible for {what}: {', '.join(bad[:3])} beyond its "
            f"built-in chemistry without declared reactions")


def _elemental_product(keys):
    """R8.4: True when a species' identity keys (_species_keys) include the
    formula of one element that combustion leaves elemental ('Na', 'Ca',
    'Si': not a _COMBUSTIBLE_ELEMENTS element), as the 'elemental'
    handling of chemicals.combustion writes a decomposition's leftover
    atoms."""
    for key in keys:
        match = _ELEMENT_KEY_RE.fullmatch(key)
        if match and match.group(1) not in _COMBUSTIBLE_ELEMENTS:
            return True
    return False


def _physics_contradiction(class_name, family, physics, core=None,
                           confidence=None, label=None):
    """R5.1 / R6.1: why the stream / duty evidence `physics`
    (_physics_contradictions; None or {} when there is none) contradicts a
    need-removing claim, or None. A mixer claim with a class (exact or
    named): contradicted by a heat duty, a power duty (except for the
    @cost-decorated Mixer subclasses of _COSTED_CLASSES, whose cost item
    may carry agitator kW), species appearing / vanishing or otherwise
    converted ('species', 'conversion'), or its T / P upper bounds
    ('mixer'). A pure-splitter claim -- a PURE_SPLITTER_CLASSES class, or
    (R8.3) a splitter family claim without a class whose `label` (the
    unit_type, or the named abstract class) has a pure-splitter head noun
    (_pure_splitter_label: splitter, split, divider, tee, manifold) -- by
    any duty, converted species, or T / P deviations ('splitter'); a
    MOCK_SPLITTER_CLASSES claim by a duty or species appearing /
    vanishing only (it does nothing when simulated, so its outlets' T and
    P are set elsewhere). R8.3: every other splitter claim without a class
    (a centrifuge, filter, press, membrane, ... label, or a key-set
    inference: `label` None) by a heat duty (a power duty is allowed),
    converted species, a pressure rise ('rise': a pressure drop is
    allowed; with a power duty only a rise above 1 atm,
    'rise_above_atm'), power it produces ('power_out') or an outlet T
    outside the inlets' band widened by LABEL_T_ABS ('hot', 'cold'); a
    mixer claim without a class by the same except power drawn and 'cold'
    (a mixer may cool); a solids-handling claim without a class by the
    same as the mixer's. A pure-splitter label fed by several streams also
    by the mixer bounds, 'cold' and 'unmixed'.

    R6.1(a) / R7.1: every other claim that cannot host chemistry
    (_chemistry_claim 'none': an exact core class without built-in
    chemistry, or a pump, valve, compressor, turbine, exchanger, flash,
    junction, evaporator, crystallizer or steam-injection heater claim
    without an exact class's code) by converted species; a junction claim
    also by any duty, the exact Junction and Scaler (and, R8.3, a
    'Junction' / 'Scaler' label: _junction_label_class) by the
    pure-splitter T / P bounds, the Junction by a total mass balance that
    does not close ('mass'), and the junctions that conserve nothing
    species by species (_NONCONSERVING_JUNCTIONS, and any other label-only
    junction claim) only by species appearing / vanishing, every junction
    claim but a MockSplitter or diagram-only one (_mock_claim) by a
    'separation', and a label-only process exchanger by a 'separation'.
    R5.1: a pump, valve or turbine claim also by a heat duty
    (_NO_HEAT_FAMILIES); each of these claims but a turbine's also by
    power it produces ('power_out', _POWER_PRODUCING_FAMILIES). R6.1(c): a
    _BUILTIN_CHEMISTRY class, or a combustor / boiler / facility / dryer
    claim without an exact class's code (_FAMILY_CHEMISTRY), by a
    conversion outside its signature (_builtin_contradiction), a facility
    or dryer claim also by power it produces. A HOSTING
    claim (tank, column, extractor, R7.1) is never contradicted by
    converted species. `core` (default: on the curated core list)
    and `confidence` (default 1) qualify an exact class claim. Other claims
    are not tested; an implausible one counts as absent."""
    physics = physics or {}
    if core is None:
        core = class_name in _CORE_CLASSES
    chemistry = _chemistry_claim(class_name, family, core,
                                 1.0 if confidence is None else confidence)
    converted = ('species', 'conversion')
    if family == 'mixer' and class_name is None:
        # R8.3: a label-only mixer may draw power (an agitated blend tank),
        # not produce it.
        kinds = ('heat', 'power_out') + converted + ('rise', 'hot')
        what = 'a mixer'
    elif family == 'mixer':
        kinds = (('heat',) if class_name in _COSTED_CLASSES
                 else ('heat', 'power')) + converted + ('mixer',)
        what = 'a mixer'
    elif class_name in MOCK_SPLITTER_CLASSES:
        kinds, what = ('heat', 'power', 'species'), 'a mock splitter'
    elif (class_name in PURE_SPLITTER_CLASSES if class_name
          else family == 'splitter' and _pure_splitter_label(label)):
        # R8.3: a pure splitter fed by one stream is held to that inlet's
        # T and P ('splitter'). A label-only one fed by several streams
        # (an FSplit: an exact class takes one inlet) mixes them and
        # divides the mixture: its outlets stay within the inlets' bounds
        # ('mixer', 'cold') and carry the mixture ('unmixed').
        # (A one-inlet deviation, 'splitter', already implies the bounds.)
        kinds = (('heat', 'power') + converted + ('splitter',)
                 + (('mixer', 'cold', 'unmixed') if class_name is None
                    and not physics.get('splitter') else ()))
        what = 'a pure splitter'
    elif family == 'splitter' and class_name is None:
        # R8.3: any other label-only separator may draw power and drop
        # pressure; a heat duty, an outlet outside the inlets' temperature
        # band, a pressure rise without a power duty, or with one a rise
        # above 1 atm (the work of a built-in pump, as BioSTEAM's
        # sludge-handling units and reverse osmosis raise their outlets to
        # 1 atm, 'rise_above_atm'), or power it produces ('power_out': a
        # turbine's) contradicts it. A sorption
        # or regeneration head (_sorption_label: a molecular sieve, an
        # adsorber, an ion exchanger, an absorber) may take a heat duty
        # too: its temperature swing is its duty, and with a heat duty
        # recorded its outlets may leave the inlets' temperature band (an
        # AmineAbsorption sets its outlets to 40 C). Deleting that duty
        # restores the band test, so it can only add a contradiction.
        # R8.4: so may a separator that declares reactions of its own
        # ('declares': a biological polishing filter's chilled water) --
        # held to reactor rules, its heat duty is a reactor's.
        sorption = _sorption_label(label) or bool(physics.get('declares'))
        kinds = ((() if sorption else ('heat',)) + converted
                 + (() if sorption and physics.get('heat') else ('hot', 'cold'))
                 + (('rise_above_atm',) if physics.get('power')
                    else ('rise',)) + ('power_out',))
        what = 'a separator'
    elif family == 'solids_handling' and class_name is None:
        # R8.3 (N8): a label-only conveyor, mill or screw may draw power
        # and heat its solids a little, not take a heat duty or produce
        # power.
        kinds = converted + ('heat', 'power_out', 'rise', 'hot')
        what = 'a solids-handling unit'
    elif family == 'junction' and chemistry == 'none':
        # R8.3 (N8): a 'Junction' / 'Scaler' label is held to that exact
        # class's bounds. A junction separates nothing, except a
        # MockSplitter or diagram-only claim (_mock_claim), whose outlets
        # are set elsewhere: it is held to a duty and species appearing
        # or vanishing only, as the exact MockSplitter is.
        exact = class_name or _junction_label_class(label)
        conserving = (exact is not None
                      and exact not in _NONCONSERVING_JUNCTIONS)
        kinds = (('heat', 'power') + (converted if conserving else ('species',))
                 + (('splitter',) if exact in _T_P_JUNCTIONS else ())
                 + (('mass',) if exact == 'Junction' else ())
                 + (() if _mock_claim(class_name, label) else ('separation',)))
        what = 'a junction'
    elif family == 'hx_process' and class_name is None:
        # R8.3: a process-process exchanger claim without a class passes
        # each stream through; its outlets cannot separate.
        kinds = converted + ('separation', 'power_out')
        what = 'a process heat exchanger'
    elif family in _NO_HEAT_FAMILIES and chemistry == 'none':
        # R5.1: BioSTEAM's pumps, valves and turbines exchange no heat
        # with an agent (a compressor may: its intercoolers).
        kinds = ('heat',) + converted + (
            () if family in _POWER_PRODUCING_FAMILIES else ('power_out',))
        what = f'a {family}'
    elif chemistry == 'none':
        kinds = converted + (() if family in _POWER_PRODUCING_FAMILIES
                             else ('power_out',))
        what = 'a unit that hosts no chemistry'
    elif chemistry == 'builtin':
        why = _builtin_contradiction(class_name, family, physics)
        if (why is None and physics.get('power_out')
                and family not in _POWER_PRODUCING_FAMILIES):
            why = f"implausible for a {family} claim: {physics['power_out']}"
        return why
    else:
        return None
    reasons = [physics[kind] for kind in kinds if physics.get(kind)]
    return f"implausible for {what}: {'; '.join(reasons)}" if reasons else None


def _ports_plausible(class_name, family, core, ports, physics=None,
                     confidence=None, label=None):
    """True unless the claim is contradicted by its ports
    (_port_contradiction, R4.8/R4.14) or by its stream physics
    (_physics_contradiction, R5.1 / R6.1; `label`: the text naming a claim
    without a class, R8.3)."""
    return (_port_contradiction(class_name, family, core, ports,
                                label) is None
            and _physics_contradiction(class_name, family, physics, core,
                                       confidence, label) is None)


def _label_head(text):
    """R8.3: (family, head-noun text) of a unit_type or class name, read
    as _keyword_family reads it (its CamelCase-split form, else its raw
    lowercase form); (None, None) when it names no family."""
    if not _nonempty_str(text):
        return None, None
    raw, spaced = _normalized_texts(text)
    for variant in (spaced, raw):
        family = _phrase_family(variant)
        if family is not None:
            matches = _equipment_matches(variant)
            if not matches:
                return family, None
            end, start, _head = max(matches)
            return family, variant[-start:end]
    return None, None


def _pure_splitter_label(text):
    """R8.3: True when `text` (a unit_type or class name) names a splitter
    whose head noun is a pure-splitter word (_PURE_SPLITTER_WORD_RE:
    'Splitter', 'Stream split', 'Divider', 'Tee', 'Manifold') -- a claim
    that the unit only divides a stream; False for every other splitter
    label ('Centrifuge', 'Filter press', 'Membrane', 'Settling tank') and
    for a component splitter (_COMPONENT_SPLITTER_RE: 'Component
    splitter', which separates)."""
    family, head = _label_head(text)
    return (family == 'splitter' and head is not None
            and _PURE_SPLITTER_WORD_RE.fullmatch(head) is not None
            and not any(_COMPONENT_SPLITTER_RE.search(variant)
                        for variant in _normalized_texts(text)))


def _sorption_label(text):
    """True when `text` (a unit_type or class name) names a separator
    whose head noun is a sorption or regeneration word
    (_SORPTION_HEAD_RE: 'Adsorption', 'Cation exchange', 'Amine
    absorption', 'Activated carbon bed') or a molecular sieve
    (_MOLECULAR_SIEVE_RE, the head 'sieve' directly after 'molecular'),
    read as _label_head reads it; False for every other label ('Sieve
    screen', 'Centrifuge', 'Filter press')."""
    if not _nonempty_str(text):
        return False
    for variant in _normalized_texts(text)[::-1]:
        if _phrase_family(variant) is None:
            continue
        matches = _equipment_matches(variant)
        if not matches:
            return False
        end, start, family = max(matches)
        return family == 'splitter' and (
            _SORPTION_HEAD_RE.fullmatch(variant, -start, end) is not None
            or _MOLECULAR_SIEVE_RE.search(variant[:end]) is not None)
    return False


def _junction_label_class(text):
    """R8.3 (N8): 'Junction' or 'Scaler' when `text` (a unit_type or class
    name) names that junction word (_JUNCTION_LABEL_CLASSES), whose exact
    class's bounds the label-only claim is held to; else None (a
    duplicator, mass-balance, diagram-only or mock label)."""
    if not _nonempty_str(text):
        return None
    variants = _normalized_texts(text)
    for name, pattern in _JUNCTION_LABEL_CLASSES:
        if any(pattern.search(v) for v in variants):
            return name
    return None


# R8.3 (N8): the classes whose outlets are set elsewhere -- BioSTEAM's
# MockSplitter (FakeSplitter) and diagram-only units, never simulated.
_MOCK_CLASSES = MOCK_SPLITTER_CLASSES | frozenset({
    'DiagramOnlyUnit', 'DiagramOnlyStreamUnit', 'DiagramOnlySystemUnit'})


def _mock_claim(class_name, label):
    """R8.3 (N8): True when a junction claim names a MockSplitter or a
    diagram-only unit exactly -- its class, or, without a class, a `label`
    spelling that class's name (_MOCK_CLASSES: 'MockSplitter') -- whose
    outlets, set elsewhere, may differ from each other as corpus S301's
    vent and liquid do. Any other mock, diagram-only or mass-balance label
    ('Mock', 'Diagram only') names no class whose code is known: a
    separation still contradicts it."""
    if class_name is not None:
        return class_name in _MOCK_CLASSES
    return isinstance(label, str) and label.strip() in _MOCK_CLASSES


def _generic_exchanger(text):
    """True when the head noun of `text` (a unit_type) is a bare heat
    exchanger -- 'Heat exchanger', 'Hydrolysate heat exchanger', 'HX' --
    which names a utility or a process-process exchanger (R4.8: its ports
    decide between hx_utility and hx_process); not 'Heater', 'Cooler',
    'Condenser' or 'HXutility'."""
    if not _nonempty_str(text):
        return False
    spaced = _normalized_texts(text)[1]
    matches = _equipment_matches(spaced)
    if not matches:
        return False
    end, start, family = max(matches)
    return (family == 'hx_utility'
            and _ANY_EXCHANGER_RE.fullmatch(spaced, -start, end) is not None)


def _unit_class(unit, registry=None, pinned_repo=None, ports=None,
                physics=None):
    """(class_name or None, family or None, confidence) for one unit.

    R4.3: exact class identity comes ONLY from design_simulation_method
    "Class on <URL>": the named class scores _class_confidence -- 1.0 for a
    concrete core BioSTEAM class, 1.0 or 0.75 for a custom class (source URL
    in the pinned model-package repository `pinned_repo`, or not; R3.6).
    Its family is the class's own, else the unit_type keyword family, else
    the key-set inference. An abstract class, or an unverifiable biosteam-
    URL claim, names no class (R2.6): its family joins the STATED family
    sources. unit_type never names a class -- not even a class name
    written there -- it yields a family by keywords only (R4.4).

    Without a named class (R3.1), a STATED family -- the unit_type keyword
    family, else the family of the abstract / unverifiable class named --
    wins: the key-set inference (_infer_class_from_specs) only confirms it
    (0.75 when the unambiguous inferred family equals it, else 0.5) and
    never swaps it. With no stated family, the unambiguous key-set family
    gives (None, family, 0.75). An inferred class is never returned as the
    class (its rules and class knowledge stay family-level); else
    (None, None, 0.0).

    R4.8: `ports` -- the unit's (non-empty inlets, non-empty outlets), None
    when unknown -- invalidates every claim they contradict
    (_port_contradiction): the named class together with its family, the
    unit_type family, a named abstract class's family, the inferred family.
    An invalid claim counts as absent (INVALID == MISSING). A bare 'heat
    exchanger' unit_type (_generic_exchanger) whose ports rule out a
    utility exchanger names a process-process exchanger (hx_process).

    R5.1: `physics` -- the unit's stream / duty evidence
    (_physics_contradictions), None when unknown -- likewise invalidates
    every mixer claim and pure-splitter claim it contradicts
    (_physics_contradiction; R8.3: the unit_type, or the named abstract
    class, is the `label` that tells a pure-splitter claim from any other
    separator label, which may draw power and drop pressure); with no
    other valid claim the family is
    unknown, so the unit carries every need (R4.13). R6.1 / R7.1: so does
    every claim that cannot host chemistry across which species convert
    (an exact core class without built-in chemistry, a pump / valve /
    compressor / turbine / exchanger / flash / junction / evaporator /
    crystallizer / solids-handling / steam-injection heater claim without
    an exact class's code), and a built-in chemistry class or claim
    (a dryer's combustion) converting species outside its signature.

    R7.2: a key-set family that is a legitimate rule-set variant of the
    stated one (_rule_variant_families: a MESH column's keys under a
    'Distillation column' label, a batch bioreactor's under 'Fermenter')
    confirms it like an equal family (0.75)."""
    unit = _as_dict(unit)
    registry = _as_dict(registry)
    name, source = _dsm_class(unit)
    unit_type = unit.get('unit_type')
    keyword = _keyword_family(unit_type)
    if keyword == 'hx_utility' and _generic_exchanger(unit_type) and not (
            _ports_plausible(None, keyword, False, ports)):
        keyword = 'hx_process'  # a bare 'heat exchanger' with 2 ins / outs
    if not _ports_plausible(None, keyword, False, ports, physics,
                            label=unit_type):
        keyword = None
    inferred = _infer_class_from_specs(unit, registry)
    # A key-set splitter inference under a sorption unit_type ('Molecular
    # sieve', _sorption_label) is judged as that label is: its regeneration
    # heat duty must not deny the key set the confirmation it gives without
    # it (deleting the honest steam would then raise CMP-UOP-01).
    if inferred and not _ports_plausible(
            None, inferred[1], False, ports, physics,
            label=(unit_type if inferred[1] == 'splitter'
                   and _sorption_label(unit_type) else None)):
        inferred = None
    if name:
        confidence = _class_confidence(name, source, pinned_repo)
        if confidence is not None:
            family = _class_family(name)
            if family is None and name not in _CLASS_TABLE:
                family = keyword or (inferred[1] if inferred else None)
            if _ports_plausible(name, family, _is_core_class(name, source),
                                ports, physics, confidence):
                return name, family, confidence
            name = None  # R4.8 / R5.1: a contradicted class claims nothing
    named = _class_family(name) if name else None
    if not _ports_plausible(None, named, False, ports, physics, label=name):
        named = None
    stated = keyword or named
    if stated:
        agrees = (inferred is not None
                  and inferred[1] in _rule_variant_families(stated))
        return None, stated, (0.75 if agrees else 0.5)
    if inferred:
        return None, inferred[1], 0.75
    return None, None, 0.0


def _declared_registry_class(class_name, registry):
    """Design-spec registry entry of a named class: the class itself if
    listed, else the curated table's registry_class if listed; or None. Uses
    no key-set inference (R3.1): it is what a unit's class-level rules
    resolve through and what CMP-UOP-03 measures a unit whose class is
    known exactly against."""
    registry = _as_dict(registry)
    if not _nonempty_str(class_name):
        return None
    if class_name in registry:
        return class_name
    ancestor = _CLASS_TABLE.get(class_name, (None, None))[1]
    return ancestor if ancestor in registry else None


def _is_cost_free(class_name, family):
    """True when a unit of this exactly known class carries no purchase cost
    by default: a COST_FREE_CLASSES class, or a class of a COST_FREE_FAMILIES
    family unless it is a known @cost-decorated subclass. R4.3: never
    without a class (`class_name` None) -- a family from unit_type keywords
    or spec keys is no evidence a unit is cost-free; callers pass the class
    only when it is known at confidence 1."""
    if not _nonempty_str(class_name):
        return False
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
#   when      -- a data / class-knowledge condition gating the whole
#                requirement (None: always); it is waived only on affirmative
#                evidence, so an unevaluable condition applies it (R2.13;
#                see _requirement_applies). A waived requirement still
#                counts, as met (R5.9: _unit_spec_score).
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
# R3.5 class-knowledge waivers ('class_param', R2.13 principle): each of
# these family-level requirements applies unless the unit's class is known
# exactly (named, confidence 1) and affirmatively takes none of its keys
# (_class_lacks_param).
# Retentate moisture of a solids separator: SolidsSeparator's (and its
# subclasses') moisture_content -- sludge_moisture in biosteam.wastewater's
# SludgeHandling -- the water fraction of the solids outlet, so derivable
# from full port states (closure-gated, R2.2). Splitter, MolecularSieve,
# Separator, the liquids centrifuges/settlers ... take neither: waived.
_MOISTURE = _Req(1, ('moisture_content', 'sludge_moisture'),
                 {'moisture_content': ('all_ports_state',)},
                 when='class_param')
# DrumDryer: moisture_content of the dried solids (constructor default 0.15)
# and the drying temperature T (default 343.15 K) are constructor arguments
# with defaults, so they must be stated (R2.8); split is required. The
# SLLE / solid-liquids centrifuges take moisture_content (default 0.5) too.
_MOISTURE_CONTENT = _Req(1, ('moisture_content',),
                       {'moisture_content': ('all_ports_state',)},
                       when='class_param')
_DRYER_T = _Req(1, ('T',), {'T': ('outlet_T',)}, when='class_param')
# ThermalOxidizer: natural gas burned per kg of feed (duty_per_kg, default
# 61 in the constructor), not derivable without the fuel's heating value.
_DUTY_PER_KG = _Req(1, ('duty_per_kg',), {}, when='class_param')
# R8.10 (N3): biosteam.wastewater's SludgeHandling classes (BeltThickener,
# the high-rate SludgeCentrifuge) split by the sludge's moisture content and
# the soluble chemicals that follow the water (constructor defaults 0.96 /
# 0.8 and ()), not by a split vector; both are derivable from full port
# states (closure-gated, R2.2), as a split is.
_SLUDGE_HANDLING = (
    _Req(1, ('sludge_moisture',), {'sludge_moisture': ('all_ports_state',)}),
    _Req(1, ('solubles',), {'solubles': ('all_ports_state',)}))

# Family rules (Revision 1, R1.4; R3.5), checked against the biosteam source.
_FAMILY_RULES = {
    'mixer': (), 'solids_handling': (), 'facility': (), 'junction': (),
    'combustor': (_DUTY_PER_KG,),
    'splitter': (_SPLIT, _MOISTURE),
    'dryer': (_SPLIT, _MOISTURE_CONTENT, _DRYER_T),
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
                            {'solids_split': ('all_ports_state',)}),
                       _MOISTURE_CONTENT),
    'SolidLiquidsSplitCentrifuge': (
        _Req(1, ('solids_split',), {'solids_split': ('all_ports_state',)}),
        _Req(1, ('aqueous_split',), {'aqueous_split': ('all_ports_state',)}),
        _MOISTURE_CONTENT),
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
    # R8.10: AnMBR's biogas and biomass yields and PolishingFilter's
    # decomposition and growth conversions set their reactions (and so
    # their mass balances) in the constructor (_init defaults 0.86 / 0.05,
    # 0.74 / 0.22).
    'AnMBR': (_Req(1, ('T',), {'T': ('outlet_T',)}),
              _Req(1, ('reactor_type',), {}),
              _Req(1, ('Y_biogas',), {}), _Req(1, ('Y_biomass',), {})),
    'PolishingFilter': (_Req(1, ('T',), {'T': ('outlet_T',)}),
                        _Req(1, ('OLR', 'HLR'), {}),
                        _Req(1, ('X_decomp',), {}), _Req(1, ('X_growth',), {})),
    'InternalCirculationRx': (
        _Req(1, ('T',), {'T': ('outlet_T',)}),
        _Req(1, ('OLRall',), {}),
        _Req(1, ('V_wf',), {})),
    'AnaerobicDigestion': (_Req(1, ('sludge_split',), {}),),
    'AerobicDigestion': (_Req(1, ('evaporation',), {}),),
    # R8.10 (N3): the degrees of freedom these biosteam.wastewater classes
    # take instead of a split (checked against their _init signatures).
    # ReverseOsmosis (wastewater/conventional.py): the water recovery,
    # derivable from full port states (the permeate's share of the water);
    # the high-rate variant fixes it in _run (_SOURCE_CLASS_RULES).
    'ReverseOsmosis': (_Req(1, ('water_recovery',),
                            {'water_recovery': ('all_ports_state',)}),),
    # BiogasUpgrading: the upgraded fraction of the biogas and the CH4 lost
    # in upgrading (lost CH4 leaves no stream, so the ports do not close
    # and neither derives).
    'BiogasUpgrading': (_Req(1, ('ratio',), {}), _Req(1, ('loss',), {})),
    'BeltThickener': _SLUDGE_HANDLING,
    'SludgeHandling': _SLUDGE_HANDLING,
    # WWTpump: the pump type picks its design algorithm (a required
    # constructor argument); it takes no P or dP_design.
    'WWTpump': (_Req(1, ('pump_type',), {}),),
    # RefluxDrum: sized by its holdup time (constructor default 3 min) --
    # the tank's residence time; it takes no tau.
    'RefluxDrum': (_Req(1, ('holdup_time',), {}),),
}

# R8.10: class-level rules of a same-named core class variant, keyed by
# (class name, the biosteam source path its design_simulation_method URL
# names; see _UNLISTED_CORE_SOURCES). The high-rate ReverseOsmosis fixes
# its water recovery in _run (no parameter); the high-rate SludgeCentrifuge
# is a SludgeHandling. A URL naming neither keeps the class-name rules.
_SOURCE_CLASS_RULES = {
    ('ReverseOsmosis', 'wastewater/high_rate/system.py'): (),
    ('SludgeCentrifuge', 'wastewater/high_rate/sludge_handling.py'):
        _SLUDGE_HANDLING,
}


def _unit_rules(sc, position):
    """Essential requirements of unit `position`: class-level rules (by the
    source variant of a same-named core class, R8.10 _SOURCE_CLASS_RULES;
    by class; then by registry class), else its family's; None when the
    family is unknown."""
    name, family, _conf = sc.unit_classes[position]
    variant = _source_variant(sc, position)
    if variant in _SOURCE_CLASS_RULES:
        return _SOURCE_CLASS_RULES[variant]
    for key in (name, sc.unit_registry_classes[position]):
        if key in _CLASS_RULES:
            return _CLASS_RULES[key]
    return _FAMILY_RULES.get(family)


# R7.2: the legitimate rule-set variants of a family claim made without an
# exact class (a label, an abstract or unpinned custom class, a key-set
# inference): the families whose rules describe the BioSTEAM classes such a
# unit may be. A column is a BinaryDistillation / ShortcutColumn or a
# MESHDistillation (and a MESH-type label a binary column); a reactor or
# bioreactor a continuous stirred tank (batch flag included), an NREL-type
# batch bioreactor or an aerated bioreactor. The unit's own family comes
# first (ties keep its locators).
_REACTOR_VARIANTS = ('reactor', 'batch_bioreactor', 'aerated_bioreactor')
_RULE_VARIANTS = {
    'distillation': ('distillation', 'staged_column'),
    'staged_column': ('staged_column', 'distillation'),
    **{family: (family,) + tuple(f for f in _REACTOR_VARIANTS if f != family)
       for family in _REACTOR_VARIANTS},
}


def _rule_variant_families(family):
    """R7.2: the families whose rules legitimately describe a claim of
    `family` made without an exact class (_RULE_VARIANTS; the family
    itself first), or (family,)."""
    return _RULE_VARIANTS.get(family, (family,))


def _rule_variants(sc, position):
    """R7.2: [(family, rules), ...] unit `position` may be scored by in
    CMP-UOP-02 / CMP-UOP-05 -- its own (family, _unit_rules), plus, for a
    family claim that names no class (a unit_type label, an abstract or
    unverifiable class, a key-set inference), every other legitimate
    variant's family rules (_rule_variant_families). Its score is the MAX
    over them: the max of monotone scores is monotone, so deleting a key
    still never raises it. [] when the family is unknown. A named class
    keeps its own rules, exact or not: its class knowledge (a registry
    entry lacking `batch`, a class-attribute default, R2.8) describes that
    class, not a variant, and class_default credit never reaches a claim
    naming none."""
    rules = _unit_rules(sc, position)
    if rules is None:
        return []
    name, family, _confidence = sc.unit_classes[position]
    if name is not None or rules is not _FAMILY_RULES.get(family):
        return [(family, rules)]
    return [(f, _FAMILY_RULES[f]) for f in _rule_variant_families(family)]


def _hosting_claim(sc, position):
    """R7.1: True when unit `position` makes a HOSTING claim
    (_chemistry_claim 'capable'): a tank, distillation or MESH column or
    extractor claim without an exact class's code, or an exact core class
    taking an optional reaction set. Such a claim keeps its family whatever
    its streams show."""
    name, family, confidence = sc.unit_classes[position]
    return _chemistry_claim(name, family, sc.unit_core[position],
                            confidence) == 'capable'


def _ruled_out(sc, position):
    """R7.1: True when unexplained chemistry on unit `position` is
    positively ruled out -- per unit (_no_conversion: its species flows
    computable, each balancing within CLOSURE_RTOL, its total mass
    closing) or flowsheet-wide (_flowsheet_explained). Deleting data only
    removes evidence, so it can only turn False."""
    return _no_conversion(sc, position) or _flowsheet_explained(sc)[0]


def _chemistry_rules(sc, position):
    """R7.1: the reactor rule-set variants ([(family, rules), ...] over
    _REACTOR_VARIANTS, R7.2) when unit `position` is a HOSTING tank claim
    (_hosting_claim, family 'tank') not ruled out (_ruled_out), else None.
    CMP-UOP-02 then scores the unit by the LOWER of its own rules' best
    variant and the reactor rules' best one (it must be specified both as
    the vessel it claims to be and as the reactor it may be), CMP-UOP-05
    likewise -- relabelling a fermenter 'Holding tank' cannot trade a
    reactor's needs for a tank's. Not for an undeclaring column or
    extractor (it carries chemistry only as a reactive separation,
    specified as a column), nor for claims that cannot host chemistry (a
    conversion across those makes them implausible instead, R6.1(a) /
    R7.1).

    R8.4: declared chemistry is never ruled out. The reactor variants
    apply too to a claim without a class, whatever family it claims, and
    to a hosting tank claim, that declares reactions of its own
    (_declared_chemistry), whatever the per-unit or flowsheet-level
    evidence says: that evidence (_ruled_out) rules out only UNDECLARED
    chemistry."""
    if _reactor_candidate(sc, position):
        return [(f, _FAMILY_RULES[f]) for f in _REACTOR_VARIANTS]
    return None


def _reactor_candidate(sc, position):
    """True when unit `position`, whose claimed family is no reactor
    family, may be a reactor all the same, and so carries a reactor's
    needs besides its own: it declares chemistry of its own
    (_declared_chemistry, R8.4), or it makes a hosting tank claim
    (_hosting_claim, family 'tank', R7.1) that is not ruled out
    (_ruled_out). Cached per context. Such a unit is scored by the lower
    of its own rules and the reactor variants in CMP-UOP-02 / CMP-UOP-05
    (_chemistry_rules), by the lower of its own port convention and a
    reactor's in CMP-TOP-02 (_side_credit), and counts as a reactor-family
    member wherever a family-driven need is formed (_unit_families: the
    CMP-UTIL-01..05 heat and utility needs) -- so relabelling a fermenter
    that is the flowsheet's only heat user 'Holding tank', 'Junction' or
    'Pump' sheds none of a reactor's needs. Deleting data only removes the
    rule-out evidence, so it can only turn True."""
    cache = sc.reactor_candidates
    if position not in cache:
        cache[position] = _declared_chemistry(sc, position) or (
            sc.unit_classes[position][1] == 'tank'
            and _hosting_claim(sc, position)
            and not _ruled_out(sc, position))
    return cache[position]


def _declared_chemistry(sc, position):
    """R8.4: True when unit `position` is claimed without a class (a
    unit_type label, an abstract or unverifiable class, a key-set
    inference) for a known family other than the reactor families, or
    makes a hosting tank claim (_hosting_claim: an unpinned custom tank
    class too), and declares reactions of its own -- a non-empty list of
    reaction objects,
    as the CMP-RXN-02..04 population counts them, so deleting a field of
    a reaction never lifts the reactor rules -- that its family's own
    chemistry does not account for. A family whose class fixes its
    chemistry (_FAMILY_CHEMISTRY: combustor, boiler, boiler-
    turbogenerator, facility, dryer) accounts for declared reactions only
    on affirmative evidence (R2.13): every one well-formed and every
    species it produces or consumes within the family's signature
    (_builtin_contradiction), as a boiler declaring its combustion does.
    Such a unit is scored by the lower of its own rules and the reactor
    variants (_chemistry_rules) and is reactive itself (_reactive_reason):
    relabelling a fermenter that declares its reactions 'Reaction tank',
    'Holding tank', 'Mixer' or 'Conveyor' cannot shed a reactor's needs,
    nor can deleting such a label (the unknown family is reactive too)."""
    name, family, _confidence = sc.unit_classes[position]
    if family is None or family in REACTOR_FAMILIES:
        return False
    if name is not None and not (family == 'tank'
                                 and _hosting_claim(sc, position)):
        return False
    reactions = _dicts(sc.units[position].get('reactions'))
    if not reactions:
        return False
    if family not in _FAMILY_CHEMISTRY:
        return True
    if not all(sc.well_formed(r) for r in reactions):
        return True
    consumed, produced = set(), set()
    for reaction in reactions:
        left, right = sc.reaction_sides(reaction)
        consumed |= left
        produced |= right
    formulas = {c.get('id'): c.get('formula') for c in sc.chemicals}
    changed = tuple({n: _species_keys(n, formulas.get(n)) for n in names}
                    for names in (produced, consumed))
    return _builtin_contradiction(None, family, {'changed': changed}) is not None


def _spec_population(sc):
    """Positions of units that need operating specifications (CMP-UOP-02;
    CMP-UOP-05 adds the CMP-UOP-06 facilities): unknown family, a
    non-empty rule set (facility families have none: R2.5), or a hosting
    tank held to reactor rules too (_chemistry_rules, R7.1)."""
    return [p for p in range(len(sc.units))
            if _unit_rules(sc, p) != () or _chemistry_rules(sc, p)]


def _explicit_keys(unit, family=None, spec_units=None):
    """design_input_specs keys stated with a value plausible for their role
    in a unit of `family` (_valid_spec; R2.0: an implausible value counts as
    unstated). R8.16: a key whose dimension only `family` fixes
    (_FAMILY_SPEC_DIMENSIONS: a batch bioreactor's V is a volume), stated
    in a unit the table knows only for another dimension (`spec_units`,
    the unit each key is stated in: _ScoringContext.spec_units), is
    invalid too; the always-dimensional keys were invalidated when the unit
    was read (_to_internal)."""
    specs = _as_dict(_as_dict(unit).get('design_input_specs'))
    spec_units = spec_units or {}

    def mismatched(key):
        dimension = _FAMILY_SPEC_DIMENSIONS.get((family, key))
        return (dimension is not None
                and bool(_other_dimensions(spec_units.get(key), dimension)))

    return {k for k, v in specs.items()
            if _valid_spec(k, v, family) and not mismatched(k)}


def _stream_value(stream, part):
    """Valid temperature ('T', in (0, T_MAX) K) or pressure ('P', > 0) of a
    stream in BioSTEAM's internal unit, or None -- also for a quantity in an
    unrecognised unit (_Unconverted, R3.4), which no guard or pressure-rise
    test may compare."""
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    value = sp.get('temperature' if part == 'T' else 'pressure')
    if isinstance(value, _Unconverted):
        return None
    return _valid_T(value) if part == 'T' else _valid_P(value)


def _phase_set(stream):
    """Phases carrying flow in a stream (per-phase shape, or legacy flat
    composition entries tagged with a phase); a zero-flow stream -> empty
    set; None when the stream has no phase data, or when some phase's
    presence is unknown (R2.13: every caller reads the set as
    affirmative evidence -- a single-phase port, an unchanged phase set --
    so an unknown phase must not count as absent): a phase stating neither
    a valid flow of its own nor a composition, or a flat entry without a
    valid fraction. Deleting such a phase's composition or zero flow, or
    an entry's fraction, then voids the set instead of dropping the phase
    from it (which raised CMP-RXN-04's phase part)."""
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
            if flow is None and not _as_list(phase.get('composition')):
                return None
            if flow is None or abs(flow) > ZERO_FLOW:
                found.add(symbol)
        return frozenset(found)
    entries = _dicts(sp.get('composition'))
    if entries and all(_nonempty_str(e.get('phase')) for e in entries):
        fractions = [_entry_fraction(e) for e in entries]
        if any(x is None for x in fractions):
            return None
        return frozenset(e['phase'] for e, x in zip(entries, fractions)
                         if x > 0.0)
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
    (PHYSICAL_FAMILIES), or of unknown family (R4.13: it could be one), and
    declares no well-formed reaction: its species must then balance one by
    one (R2.2)."""
    family = sc.unit_classes[position][1]
    return ((family is None or family in PHYSICAL_FAMILIES)
            and not sc.unit_reacts[position])


def _mean_molar_mass(composition, molar_masses):
    """R8.5: the mean molar mass (g/mol) of a plausible composition
    (_composition_ok) whose every component has a resolved molar mass in
    `molar_masses` ({chemical id: g/mol}): sum(x_i * MW_i) over its mol
    fractions, else 1 / sum(w_i / MW_i) over its mass fractions -- the
    first kind every entry states validly, summing to 1 within
    FRACTION_SUM_TOL; else None. Deleting a molar mass or a fraction can
    only make it None."""
    if not _composition_ok(composition):
        return None
    mws = [_as_dict(molar_masses).get(e['component_name'])
           for e in composition]
    if not all(mws):
        return None
    for key in ('mol_fraction', 'mass_fraction'):
        fractions = [_valid_fraction(e.get(key)) for e in composition]
        if (any(x is None for x in fractions)
                or abs(sum(fractions) - 1.0) > FRACTION_SUM_TOL):
            continue
        if key == 'mol_fraction':
            return sum(x * mw for x, mw in zip(fractions, mws))
        inverse = sum(w / mw for w, mw in zip(fractions, mws))
        return 1.0 / inverse if inverse > 0.0 else None
    return None


def _phase_mass(phase, sp, single, molar_masses):
    """R8.5: mass flow of one phase of a stream (stream_properties `sp`):
    its valid total_mass_flow -- for the only phase that may carry
    material (`single`: _sole_phase), the stream's (_phase_flow) -- 0.0
    when stated empty (_empty_phase), else its valid total_molar_flow
    (likewise) x the mean molar mass of its composition
    (_mean_molar_mass); else None."""
    phase = _as_dict(phase)
    mass = _phase_flow(phase, sp, single, 'total_mass_flow')
    if mass is not None:
        return mass
    if _empty_phase(phase):
        return 0.0
    moles = _phase_flow(phase, sp, single, 'total_molar_flow')
    if moles is None or moles <= ZERO_FLOW:
        return moles
    mw = _mean_molar_mass(phase.get('composition'), molar_masses)
    return None if mw is None else moles * mw


def _phase_total_mass(sp, molar_masses):
    """R8.5: mass flow of a stream (stream_properties `sp`) stating no
    valid total_mass_flow, from its phases: the sum of every phase's mass
    (_phase_mass: a stated mass flow, else a molar flow x its
    composition's mean molar mass, the only phase of a single-phase stream
    taking the stream's totals); for the legacy flat composition of one
    phase, the stream's total_molar_flow x its mean molar mass. None when
    any phase's mass is not computable (a molar mass unresolved, a
    fraction invalid, a flow missing, or several phases sharing one stream
    total: their split is unknown)."""
    phases = sp.get('phases')
    if isinstance(phases, dict) and phases:
        total = 0.0
        for symbol, phase in phases.items():
            mass = _phase_mass(phase, sp, _sole_phase(phases, symbol),
                               molar_masses)
            if mass is None:
                return None
            total += mass
        return total
    composition = sp.get('composition')
    moles = _valid_flow(sp.get('total_molar_flow'))
    if (moles is None or not isinstance(composition, list)
            or len({e.get('phase') if isinstance(e.get('phase'), str)
                    else None for e in _dicts(composition)}) != 1):
        return None
    mw = _mean_molar_mass(composition, molar_masses)
    return None if mw is None else moles * mw


def _stream_mass(stream, molar_masses=None):
    """Mass a stream carries: 0.0 when it states a zero total flow (mass or
    molar: no mass), else its valid total_mass_flow, else (R8.5) the mass
    its phases carry (_phase_total_mass: stated phase mass flows, or molar
    flows x mean molar masses from the document's resolved `molar_masses`,
    sc.molar_masses -- so a molar-basis stream table closes its total
    mass balance like its mass-basis twin), else None. Deleting a molar
    mass, a fraction or a flow only removes the fallback."""
    if _is_empty_stream(stream):
        return 0.0
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    mass = _valid_flow(sp.get('total_mass_flow'))
    if mass is None:
        mass = _phase_total_mass(sp, molar_masses or {})
    return mass


def _massless(sc, positions, role):
    """Reason a balance is not evaluable: the first of the streams at
    `positions` (those whose mass _stream_mass cannot tell) named, with a
    count of the others -- N5: one stream stating only a volumetric flow
    suspends the balance, so the locator names it (a recorded residual:
    its mass is not solved from the balance)."""
    label = sc.stream_labels[positions[0]]
    sp = _as_dict(sc.streams[positions[0]].get('stream_properties'))
    volume = (' (only a volumetric flow, which carries no mass)'
              if _valid_flow(sp.get('total_volumetric_flow')) is not None
              else '')
    others = (f' (and {len(positions) - 1} other stream(s))'
              if len(positions) > 1 else '')
    # The first species of the stream whose molar mass does not resolve
    # (declared or from the formula; a name-resolved record is unverified,
    # so never evidence), when that is what withholds the molar fallback.
    phases = sp.get('phases')
    compositions = ([_as_dict(ph).get('composition') for ph in phases.values()]
                    if isinstance(phases, dict) else [sp.get('composition')])
    unresolved = sorted({e.get('component_name') for c in compositions
                         for e in _dicts(c)
                         if _nonempty_str(e.get('component_name'))
                         and e['component_name'] not in sc.molar_masses})
    unresolved = (f"; no molar mass resolved for {unresolved[0]} to close "
                  f"the mass balance" if unresolved else '')
    return (f'{role} {label}{others} states no valid total_mass_flow (nor a '
            f'phase amount with resolved molar masses: R8.5{unresolved})'
            f'{volume}')


def _mass_balance(sc, position):
    """(evaluable, closes, reason) of unit `position`'s total mass balance:
    evaluable when it has connected streams and each states a valid
    total_mass_flow or a zero total flow (no mass, _stream_mass); it closes
    when mass in and out agree within CLOSURE_RTOL. reason says why not
    ('' when it closes)."""
    uid = sc.units[position].get('id')
    ins = sc.unit_inlets.get(uid, []) if isinstance(uid, str) else []
    outs = sc.unit_outlets.get(uid, []) if isinstance(uid, str) else []
    if not ins + outs:
        return False, False, 'no connected streams'
    masses = [_stream_mass(sc.streams[i], sc.molar_masses)
              for i in ins + outs]
    if any(m is None for m in masses):
        return False, False, _massless(
            sc, [i for i, m in zip(ins + outs, masses) if m is None],
            'connected stream')
    m_in, m_out = sum(masses[:len(ins)]), sum(masses[len(ins):])
    if abs(m_in - m_out) > CLOSURE_RTOL * max(m_in, m_out):
        return True, False, (f'mass balance does not close (in {m_in:.6g}, '
                             f'out {m_out:.6g})')
    return True, True, ''


def _dangling_port(sc, position):
    """R5.8: why a dangling stream (_is_dangling endpoint: its
    source_unit_id / sink_unit_id not stated) may be a missing port of
    unit `position`, or '' when none can be. A stream whose sink is not
    stated may be an inlet of any unit, one whose source is not stated an
    outlet of any unit; it is ruled out only when it carries no mass, or
    when the unit's total mass balance, which closes without it, would not
    close with it (so, on a consistent document, it was not this unit's
    port). A stream of unknown mass is never ruled out. Called on a unit
    whose mass balance is evaluable."""
    uid = sc.units[position].get('id')
    masses = [[_stream_mass(sc.streams[i], sc.molar_masses)
               for i in index.get(uid, [])]
              for index in (sc.unit_inlets, sc.unit_outlets)]
    m_in, m_out = sum(masses[0]), sum(masses[1])
    for i in sc.dangling:
        stream = sc.streams[i]
        mass = _stream_mass(stream, sc.molar_masses)
        if mass == 0.0:
            continue
        for key, side in (('sink_unit_id', 'inlet'), ('source_unit_id',
                                                      'outlet')):
            if not _is_dangling(stream.get(key)):
                continue
            if mass is not None:
                a, b = (m_in + mass, m_out) if side == 'inlet' else (
                    m_in, m_out + mass)
                if abs(a - b) > CLOSURE_RTOL * max(a, b):
                    continue
            return (f'{sc.stream_labels[i]} ({key} not stated) may be a '
                    f'missing {side}: the balance cannot rule it out')
    return ''


def _gap_port(sc, m_in, m_out):
    """R5.8 for a failing total mass balance (m_in in, m_out out; the
    R4.10 guard): why a dangling stream (_is_dangling endpoint) may be a
    missing port that accounts for the gap, or '' when none can be. A
    stream whose sink is not stated may be a missing inlet of any unit,
    one whose source is not stated a missing outlet; it can account for
    the gap only on the side the balance lacks (less in than out: an
    inlet; less out than in: an outlet), and only when it may carry mass
    (one stated to carry none, _stream_mass 0, explains nothing; one of
    unknown mass may). On a document whose complete form closes the
    balance, the balance can only fail once ports are lost, and a port is
    lost only by deleting the endpoint that attached it, which leaves it
    dangling on exactly that side; the side that fails is one that lost a
    mass-carrying port (if only outlets were lost, less out than in: the
    balance cannot fail the other way). So on a consistent document every
    failing balance has such an excuse -- the guard never fires on one,
    and deleting data can never lift it from one."""
    key, side = (('sink_unit_id', 'inlet') if m_in < m_out
                 else ('source_unit_id', 'outlet'))
    for i in sc.dangling:
        stream = sc.streams[i]
        if (_is_dangling(stream.get(key))
                and _stream_mass(stream, sc.molar_masses) != 0.0):
            return (f'{sc.stream_labels[i]} ({key} not stated) may be a '
                    f'missing {side}')
    return ''


def _mass_closure(sc, position):
    """(closes, reason) of unit `position`'s TOTAL mass closure: its mass
    balance is evaluable and closes (_mass_balance), and no dangling
    stream may be a missing port of it (_dangling_port, R5.8). What the
    CMP-TOP-02 single-port credit rests on (R5.3: enough to show no
    material-carrying port is missing, whatever the species basis).
    Cached per context."""
    cache = sc.mass_closures
    if position not in cache:
        _evaluable, closes, why = _mass_balance(sc, position)
        if closes:
            why = _dangling_port(sc, position)
            closes = not why
        cache[position] = closes, why
    return cache[position]


def _side_species(sc, unit_id):
    """(basis, {species: flow in}, {species: flow out}) over unit
    `unit_id`'s inlets and outlets, all on one basis (_basis_flows, R5.3:
    molar when every port's molar species flows are computable, else mass
    when every port's mass species flows are); None when no basis covers
    every connected stream."""
    ins = sc.unit_inlets.get(unit_id, []) if isinstance(unit_id, str) else []
    outs = sc.unit_outlets.get(unit_id, []) if isinstance(unit_id, str) else []
    found = _basis_flows([sc.streams[i] for i in ins + outs])
    if found is None:
        return None
    basis, flows = found
    sides = []
    for part in (flows[:len(ins)], flows[len(ins):]):
        acc = {}
        for species in part:
            for name, value in species.items():
                acc[name] = acc.get(name, 0.0) + value
        sides.append(acc)
    return basis, sides[0], sides[1]


# R5.3: reason prefix of a species check whose inputs are missing (as
# opposed to one that found the species inconsistent).
_NOT_COMPUTABLE = 'species flows not computable'


def _closure(sc, position):
    """(closes, reason) of unit `position`'s balances (R2.2): the total
    mass closure (_mass_closure: every connected stream stating a valid
    total_mass_flow, or a zero total flow, in and out agreeing within
    CLOSURE_RTOL, and no dangling stream possibly a missing port, R5.8),
    and, for a physical unit (_physical_unit), every species' flow --
    molar, else mass (_side_species, R5.3) -- relative to the total
    throughput on that basis, and the R2.4 species-appearance guard finds
    it consistent (_species_consistency). reason says why not ('' when it
    closes); it tells species flows that are not computable from ones
    that do not balance. Cached per context."""
    cache = sc.closures
    if position in cache:
        return cache[position]
    uid = sc.units[position].get('id')
    result = _mass_closure(sc, position)
    if result[0]:
        if _physical_unit(sc, position):
            found = _side_species(sc, uid)
            if found is None:
                result = False, (f'{_NOT_COMPUTABLE} (molar or mass basis) '
                                 f'for every port of a physical unit')
            else:
                basis, flows_in, flows_out = found
                total = max(sum(flows_in.values()), sum(flows_out.values()))
                off = sorted(n for n in set(flows_in) | set(flows_out)
                             if abs(flows_in.get(n, 0.0) - flows_out.get(n, 0.0))
                             > CLOSURE_RTOL * total)
                if off:
                    result = False, (f"species balance does not close for "
                                     f"{', '.join(off[:3])} ({basis} basis)")
        if result[0]:  # R2.4: species evidence blocks derivations too
            result = _species_consistency(sc, position)
    cache[position] = result
    return result


# Derivation conditions that read stream data (R2.2: need _closure).
_STREAM_CONDITIONS = frozenset({'outlet_T', 'outlet_P', 'all_ports_state',
                                'enthalpy', 'outlet_phases'})


# R8.10: constructor parameters of the core classes (_CORE_CLASSES) that the
# design-spec registry does not list, nor a curated registry ancestor.
# STATIC, generated once from the biosteam 2.54 source (and hensmith's
# HeatExchangerNetwork) the way the registry generator sweeps a class --
# `_init` when defined, else `__init__`, without self / ID / ins / outs /
# thermo and *args / **kwargs -- so the scorer needs no biosteam import and
# the exporter registry, and so the exporter's output, stays unchanged. The
# exporter writes {} for these classes (its registry has no entry): a gap in
# the file, which CMP-UOP-03 now measures instead of leaving the unit out.
# Left out: the meta classes Module, FacilityModule, GroupedUnit and Skipped
# (containers of other units) and FakeSplitter (the exported alias of
# MockSplitter, a junction); a boiler's T_emissions (setting it raises 'not
# yet implemented': no degree of freedom). A class listed with no parameter
# (AirDistributionPackage, CIPpackage, WastewaterSystemCost; the high-rate
# ReverseOsmosis) stays out of CMP-UOP-03: it has nothing to state.
# Algorithm knobs (a heat-exchanger network's Qmin, force_ideal_thermo,
# avoid_recycle, ...) are constructor arguments with defaults like any
# other (R2.8), so they are needs. Same-named classes in different modules
# are listed by source path in _UNLISTED_CORE_SOURCES; their entry here is
# the union of the variants (a unit whose URL names neither carries every
# variant's needs).
_BOILER_PARAMS = ('boiler_efficiency', 'side_steam', 'agent', 'other_agents',
                  'fuel_price', 'natural_gas_price', 'ash_disposal_price',
                  'CO2_emissions_concentration', 'boiler_efficiency_basis',
                  'fuel_source', 'oxygen_rich_gas_composition')
_UNLISTED_CORE_SOURCES = {
    'ReverseOsmosis': {
        'wastewater/conventional.py': ('water_recovery',),
        'wastewater/high_rate/system.py': ()},
    'SludgeCentrifuge': {
        'wastewater/conventional.py': ('split', 'order', 'moisture_content',
                                       'strict_moisture_content'),
        'wastewater/high_rate/sludge_handling.py': ('sludge_moisture',
                                                    'solubles',
                                                    'centrifuge_type')},
}
_UNLISTED_CORE_PARAMS = {
    'AerobicDigestion': ('reactions', 'evaporation'),
    'AirDistributionPackage': (),
    'AnMBR': ('reactor_type', 'membrane_configuration', 'membrane_type',
              'membrane_material', 'membrane_unit_cost',
              'include_aerobic_filter', 'add_GAC',
              'include_degassing_membrane', 'Y_biogas', 'Y_biomass',
              'biodegradability', 'split', 'sludge_conc', 'T',
              'include_pump_building_cost', 'include_excavation_cost'),
    'AnaerobicDigestion': ('reactions', 'sludge_split'),
    'BeltThickener': ('sludge_moisture', 'solubles', 'max_capacity',
                      'power_demand'),
    'BiogasUpgrading': ('ratio', 'loss', 'unit_upgrading_cost',
                        'unit_upgrading_GWP'),
    'Boiler': _BOILER_PARAMS + ('satisfy_system_steam_demand',),
    'BoilerTurbogenerator': _BOILER_PARAMS + (
        'turbogenerator_efficiency', 'satisfy_system_electricity_demand'),
    'CHP': ('eff', 'unit_CAPEX'),
    'CIPpackage': (),
    'ChemicalCapitalInvestment': ('chemical', 'price'),
    'ChilledWaterPackage': ('agent',),
    'CoolingTower': ('agent',),
    'Evaporator': ('Q', 'V', 'P', 'chemical'),
    'FireWaterTank': ('fire_water_flow_rate',),
    'HeatExchangerNetwork': ('T_min_app', 'units', 'ignored', 'Qmin',
                             'force_ideal_thermo', 'cache_network',
                             'avoid_recycle', 'acceptable_energy_balance_error',
                             'replace_unit_heat_utilities', 'sort_hus_by_T'),
    'PolishingFilter': ('filter_type', 'OLR', 'HLR', 'X_decomp', 'X_growth',
                        'split', 'T', 'include_degassing_membrane',
                        'include_pump_building_cost', 'include_excavation_cost',
                        'hxn_ok'),
    'ProcessWaterCenter': ('makeup_water_streams', 'process_water_streams',
                           'reverse_osmosis_water_price', 'process_water_price'),
    'RefluxDrum': ('vessel_material', 'has_glycol_groups', 'has_amine_groups',
                   'vessel_type', 'holdup_time', 'surge_time',
                   'has_mist_eliminator'),
    'RefrigerationPackage': ('agent', 'efficiency'),
    'SludgeHandling': ('sludge_moisture', 'solubles'),
    'WWTpump': ('pump_type', 'add_inputs', 'Q_mgd'),
    'WastewaterSystemCost': (),
    **{name: tuple(dict.fromkeys(p for params in variants.values()
                                 for p in params))
       for name, variants in _UNLISTED_CORE_SOURCES.items()},
}


def _source_path_named(source, path):
    """True when a design_simulation_method URL `source` (query and fragment
    ignored) ends in the biosteam source path `path`
    ('wastewater/high_rate/system.py')."""
    if not _nonempty_str(source):
        return False
    source = re.split(r'[?#]', source.strip())[0]
    return source.endswith('/' + path)


def _source_variant(sc, position):
    """R8.10: (class name, source path) of unit `position` when its class is
    a core class known exactly (named, confidence 1) with same-named
    variants in different biosteam modules (_UNLISTED_CORE_SOURCES) and
    its design_simulation_method URL names one of their paths; else
    None."""
    name, _family, confidence = sc.unit_classes[position]
    variants = _UNLISTED_CORE_SOURCES.get(name)
    if not variants or confidence != 1.0 or not sc.unit_core[position]:
        return None
    source = _dsm_class(sc.units[position])[1]
    return next(((name, path) for path in variants
                 if _source_path_named(source, path)), None)


def _unlisted_core_params(sc, position):
    """R8.10: the constructor parameters (a tuple) of unit `position`'s
    class when it is a core class known exactly (named, confidence 1) that
    the design-spec registry does not list (nor a curated ancestor) and
    _UNLISTED_CORE_PARAMS records -- the variant its URL names
    (_source_variant), else every variant's -- or None."""
    name, _family, confidence = sc.unit_classes[position]
    if (not name or confidence != 1.0 or not sc.unit_core[position]
            or name not in _UNLISTED_CORE_PARAMS
            or _declared_registry_class(name, sc.registry)):
        return None
    variant = _source_variant(sc, position)
    if variant:
        return _UNLISTED_CORE_SOURCES[name][variant[1]]
    return _UNLISTED_CORE_PARAMS[name]


def _registry_params(sc, position):
    """Constructor parameters of unit `position`'s named class: the keys of
    its declared design-spec registry entry (_declared_registry_class: the
    class itself, else its curated registry ancestor), else -- R8.10 -- an
    exactly known core class's _UNLISTED_CORE_PARAMS
    (_unlisted_core_params); None when the class is unknown or neither
    lists it."""
    name = sc.unit_classes[position][0]
    declared = _declared_registry_class(name, sc.registry)
    if not declared:
        params = _unlisted_core_params(sc, position)
        return None if params is None else set(params)
    return set(_as_dict(_as_dict(sc.registry.get(declared))
                        .get('design_input_spec_params')))


def _class_lacks_param(sc, position, keys):
    """R3.5 class-knowledge waiver (R2.13 principle): True when unit
    `position`'s class is known exactly (named, confidence 1) and
    affirmatively takes none of `keys` -- its design-spec registry entry (or
    curated registry ancestor), or for a core class the registry does not
    list its _UNLISTED_CORE_PARAMS entry (R8.10; biosteam.wastewater's
    ReverseOsmosis and BiogasUpgrading take no moisture setting), lists
    none of them. An inexact or unknown class, or a class without such
    evidence, lacks nothing (the requirement applies)."""
    name, _family, confidence = sc.unit_classes[position]
    if not name or confidence != 1.0:
        return False
    params = _registry_params(sc, position)
    return params is not None and not set(keys) & params


def _class_attribute_default(sc, position, key):
    """True when `key` of unit `position` is a class-level attribute default
    (R2.8): the class is concrete and identified at confidence 1, and `key`
    is NOT one of its constructor parameters (_registry_params; a class
    that neither the registry nor _UNLISTED_CORE_PARAMS lists gives no such
    evidence). A constructor argument with a default must be stated."""
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
    rise >= 1 Pa (no_pressure_rise), port evidence counting only when the
    unit's balances close (_closure: no port missing), the unit naming a
    class whose
    registry entry lacks `batch` or is absent (batch_param), the unit's
    exactly known class affirmatively taking none of the requirement's keys
    (class_param, R3.5: _class_lacks_param). An unevaluable condition
    applies it, so deleting data can only un-waive a requirement; a waived
    one scores as met (R5.9), so un-waiving can only lower a unit's
    score."""
    if req.when is None:
        return True
    unit = sc.units[position]
    uid = unit.get('id')
    if req.when == 'class_param':
        return not _class_lacks_param(sc, position, req.keys)
    # Port evidence (phase sets, pressures) is affirmative only over a
    # complete port set: the unit's balances close (R2.2), so detaching a
    # stream's endpoint can never turn a condition false.
    ports_complete = _closure(sc, position)[0]
    if req.when == 'phase_change':
        return not ports_complete or _phase_change(sc, uid) is not False
    if req.when == 'phase_change_nonrigorous':
        if _as_dict(unit.get('design_input_specs')).get('rigorous') is True:
            return False
        return not ports_complete or _phase_change(sc, uid) is not False
    if req.when == 'no_pressure_rise':
        rise = _pressure_rise(sc, uid)
        return not ports_complete or rise is None or rise < 1.0
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
    guard checks the largest-flow outlet only. Values are compared in
    internal units (R5.2); one in an unrecognised unit (_Unconverted) is
    never compared."""
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
        if isinstance(specs.get(key), _Unconverted):
            continue
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


def _requirement_locator(label, req, score, inconsistent=(), blocked='',
                         unaliased=()):
    """Missing locator of one unmet requirement; `blocked` names why a
    derivation from stream data was unavailable (_closure). R6.6: the
    `unaliased` inconsistent keys state no quantity unit (read in
    BioSTEAM's native unit), so the note suggests declaring a
    quantity_units_global alias for them."""
    count = 'one' if req.n == 1 else str(req.n)
    if inconsistent:
        note = (f" (inconsistent: {', '.join(inconsistent)} disagrees with "
                f"the outlet stream")
        if unaliased:
            note += (f"; {', '.join(unaliased)} states no quantity unit, so "
                     f"it is read in BioSTEAM's native unit -- if it is "
                     f"stated in another, declare a quantity_units_global "
                     f"alias for it")
        note += ')'
    elif score > 0:
        note = ' (only derivable)'
    else:
        note = ''
    if blocked:
        note += f' (derivation from stream data unavailable: {blocked})'
    return f"{label}.design_input_specs: {count} of {'|'.join(req.keys)}{note}"


def _unit_spec_score(sc, position):
    """(score, missing) of one unit against its essential rules: the mean
    over EVERY requirement its rules list, a waived one counting as met
    (R5.9); an unknown family scores 0 (no way to know what to specify);
    (None, []) only for an empty rule set.

    R5.9: a requirement waived on affirmative evidence (_requirement_applies:
    phase sets known and equal, a known pressure rise, an exactly known
    class lacking the parameter) stays in the mean and scores 1 -- the
    evidence that it is irrelevant is itself the information (unchanged
    phases fix the phase outcome as a stated V would). The requirement set
    is thus fixed by family / class need, never shrunk by data: deleting
    the waiving evidence turns that 1 into the requirement's own score and
    can only lower the mean (dropping the requirement instead let a stated
    key join a mean it then raised).

    R7.2: a family claim without an exact class scores the MAX over its
    legitimate rule-set variants (_rule_variants: a 'Beer column' stating
    a MESH column's keys is scored as one, a 'Fermenter' stating an NREL
    batch bioreactor's is not asked for V_wf / batch).

    R7.1: a hosting tank not ruled out (_chemistry_rules) scores the LOWER
    of that and the best reactor variant's mean (an empty own rule set
    leaves the reactor's): deleting the evidence that rules the tank out
    can only add the second part, never raise the score.

    A named separator class held to rules of its own (_CLASS_RULES,
    _SOURCE_CLASS_RULES) scores at least the DERIVED_CREDIT-capped score
    of its splitter family's reading (_family_floor): an AmineAbsorption
    whose CO2_recovery is unstated is still a separator whose split its
    full port states re-derive, so deleting its design_simulation_method
    (leaving the family reading) never raises its score."""
    variants = _rule_variants(sc, position)
    if not variants:
        label = sc.unit_labels[position]
        return 0.0, [f'{label}: unit family unknown, so its essential '
                     f'specifications cannot be determined']
    results = [_best_variant(sc, position, rule_sets)
               for rule_sets in (variants, _chemistry_rules(sc, position))
               if rule_sets and any(rules for _f, rules in rule_sets)]
    if not results:
        return None, []
    floor = _family_floor(sc, position)
    if floor is not None and variants and any(r for _f, r in variants) \
            and floor[0] > results[0][0]:
        results[0] = floor
    return min(results, key=lambda result: result[0])


def _family_floor(sc, position):
    """(score, missing) of the splitter-family reading of a named
    separator class held to rules of its own (_unit_rules is not the
    splitter's _FAMILY_RULES entry: AmineAbsorption's CO2_recovery, a
    liquids centrifuge's Ks, an adsorption column's isotherm): the
    splitter rules scored on derivations and waivers alone (no explicit
    key counts: they are the class's to state), capped at DERIVED_CREDIT
    -- whatever a separator class computes its outlets from, they are its
    split, which full port states re-derive (closure-gated, R2.2), as
    they do with its class deleted. None for any other claim (other
    families' class rules carry needs their family reading does not
    re-derive: a reactor's yields, a stage's phases) or when that is 0.
    The floor of
    _unit_spec_score: a max of monotone scores stays monotone. Its missing
    locators are the class rules' own, with a note that the family
    reading re-derives the unit."""
    name, family, _confidence = sc.unit_classes[position]
    rules = _unit_rules(sc, position)
    if (name is None or family != 'splitter' or rules is None
            or rules is _FAMILY_RULES['splitter']):
        return None
    score = min(_rules_score(sc, position, _FAMILY_RULES['splitter'],
                             'splitter', frozenset())[0], DERIVED_CREDIT)
    if score <= 0.0:
        return None
    own = _rules_score(sc, position, rules)[1] if rules else []
    return score, own + [f"{sc.unit_labels[position]}: class '{name}' "
                         f"specifications unstated; a generic splitter with "
                         f"the port-derived split fractions reproduces its "
                         f"outlets (derived credit)"]


def _best_variant(sc, position, rule_sets):
    """R7.2: (mean, missing) of the first of `rule_sets` ([(family,
    rules), ...]) whose _rules_score is highest; empty rule sets are
    skipped."""
    scored = [_rules_score(sc, position, rules, family)
              for family, rules in rule_sets if rules]
    return max(scored, key=lambda result: result[0])


def _rules_score(sc, position, rules, family=None, explicit=None):
    """(mean, missing) of unit `position` over the requirements `rules`
    (_unit_spec_score): a waived requirement scores 1 (R5.9), the others
    _requirement_score with a locator each when below 1. `family` (default:
    the unit's) is the family whose predicates judge its keys -- a rule-set
    variant's own (R7.2). `explicit` (default: the keys the unit states
    validly, _explicit_keys) is the set of keys counted as stated; an
    empty set scores derivations and waivers only (_family_floor)."""
    unit, label = sc.units[position], sc.unit_labels[position]
    if explicit is None:
        explicit = _explicit_keys(unit, family or sc.unit_classes[position][1],
                                  sc.spec_units[position])
    scores, missing = [], []
    for req in rules:
        if not _requirement_applies(req, sc, position):
            scores.append(1.0)  # waived: met (R5.9)
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
                blocked='' if closes or not streamed else why,
                unaliased=[k for k in bad
                           if k not in sc.spec_units[position]]))
    return _mean(scores), missing


def _excused_params(sc, position, explicit):
    """Registry params excused for CMP-UOP-03: the unstated alternatives of
    every essential requirement of unit `position` already satisfied by
    explicit keys that count (an HXutility with T set excuses V and H; a
    non-rigorous V alone excuses nothing) -- a facility's own requirements
    too (R8.10: a boiler stating its agent excuses other_agents)."""
    excused = set()
    rules = tuple(_unit_rules(sc, position) or ())
    if sc.unit_classes[position][1] in FACILITY_FAMILIES:
        rules += _FACILITY_RULES.get(_facility_kind(sc, position), ())
    for req in rules:
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
    stream positions -- dangling (R5.8: an endpoint not stated,
    _is_dangling; in no other list), feeds (boundary source, real sink),
    outlets (real source, boundary sink), isolated (both boundary),
    internal -- and inlets/outlets: {unit_id: [stream positions]} (a
    dangling stream still indexes under its stated unit endpoint)."""
    topo = {'feeds': [], 'outlets': [], 'isolated': [], 'internal': [],
            'dangling': [], 'inlets': {uid: [] for uid in unit_ids},
            'unit_outlets': {uid: [] for uid in unit_ids}}
    for i, stream in enumerate(streams):
        source, sink = stream.get('source_unit_id'), stream.get('sink_unit_id')
        source_b, sink_b = _is_boundary(source), _is_boundary(sink)
        if _is_dangling(source) or _is_dangling(sink):
            kind = 'dangling'
        else:
            kind = ('isolated' if source_b and sink_b else 'feeds' if source_b
                    else 'outlets' if sink_b else 'internal')
        topo[kind].append(i)
        if not sink_b and _nonempty_str(sink):
            topo['inlets'].setdefault(sink, []).append(i)
        if not source_b and _nonempty_str(source):
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
        # R3.4: streams (and, below, utility agents) are read in BioSTEAM's
        # internal units, converted from the stated quantity units.
        self.quantity_units = _quantity_unit_index(self.doc)
        self.streams = [_internal_stream(s, self.quantity_units) for s in
                        _first_by_id(_dicts(self.doc.get('streams')))]
        self.chemicals = _first_by_id(_dicts(self.doc.get('chemicals')))
        # R5.2 / R5.4: units read with their dimensional specs in internal
        # units and unreadable split vectors dropped; spec_units[position]
        # records the unit each spec key is stated in (CMP-UOP-05).
        chem_ids = frozenset(c['id'] for c in self.chemicals
                             if _nonempty_str(c.get('id')))
        basis = _split_basis(self.chemicals)
        internal = [_internal_unit(u, self.quantity_units, basis, chem_ids)
                    for u in _first_by_id(_dicts(self.doc.get('units')))]
        self.units = [u for u, _units in internal]
        self.spec_units = [units for _u, units in internal]
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
        # R5.8: streams with an endpoint not stated (_is_dangling).
        self.dangling = topo['dangling']
        self.unit_inlets, self.unit_outlets = topo['inlets'], topo['unit_outlets']
        # R3.8: unit ids stream endpoints reference but no unit declares.
        # They join the unit populations of CMP-UOP-01/02 and CMP-TOP-02
        # (scoring 0), so deleting a unit's id never sheds a need.
        self.phantom_units = sorted(
            {e for s in self.streams
             for e in (s.get('source_unit_id'), s.get('sink_unit_id'))
             if _nonempty_str(e) and not _is_boundary(e)
             and e not in self.unit_ids})

        # Per unit: declares >= 1 well-formed reaction (R2.0). R7.4: a
        # reaction failing the R6.2 closure test (which needs the validator
        # context) is not discarded -- only its CMP-RXN-02 definition is
        # discounted (sc.conserves).
        self._vctx = None
        self._molar_masses = None
        self._well_formed = {}
        self._conserving = {}
        self.closures = {}  # _closure cache: position -> (closes, reason)
        self.mass_closures = {}  # _mass_closure cache (R5.3 / R5.8)
        self.consistency = {}  # _species_consistency cache (R2.4)
        # R7.1 caches: _flowsheet_explained, _unexplained_hosts;
        # _reactor_candidate per unit position; _unit_families.
        self.flowsheet_evidence = None
        self.hidden_hosts = None
        self.reactor_candidates = {}
        self.unit_families = None
        self.heat_need = None  # _heat_need cache
        self.unit_reacts = [any(self.well_formed(r)
                                for r in _dicts(u.get('reactions')))
                            for u in self.units]
        # Unit class identity: (class, family, confidence) per unit, whether
        # the class is core BioSTEAM, and its design-spec registry class.
        self.pinned_repo = _pinned_repository(self.metadata)
        # R4.8: per unit, its (non-empty inlets, non-empty outlets).
        self.unit_ports = [self.nonempty_ports(u.get('id')) for u in self.units]
        # R5.1: per unit, its stream / duty evidence against a mixer or a
        # pure-splitter claim (_physics_contradictions).
        self.unit_physics = [_physics_contradictions(self, p)
                             for p in range(len(self.units))]
        self.unit_classes = [_unit_class(u, self.registry, self.pinned_repo,
                                         ports, physics)
                             for u, ports, physics in zip(
                                 self.units, self.unit_ports,
                                 self.unit_physics)]
        # The URL counts only when design_simulation_method named the class
        # the unit resolved to (not when an abstract one fell through to it).
        self.unit_core = [
            _is_core_class(name, _dsm_class(u)[1]
                           if _dsm_class(u)[0] == name else None)
            for u, (name, _f, _c) in zip(self.units, self.unit_classes)]
        # Registry class of the NAMED class only (R3.1: never inferred).
        self.unit_registry_classes = [
            _declared_registry_class(name, self.registry)
            for name, _f, _c in self.unit_classes]
        self.reactions = [(i, j, r) for i, u in enumerate(self.units)
                          for j, r in enumerate(_as_list(u.get('reactions')))
                          if isinstance(r, dict)]
        self.unit_positions = {}
        for i, u in enumerate(self.units):
            if _nonempty_str(u.get('id')):
                self.unit_positions.setdefault(u['id'], i)
        # Costed population (R1.8): units whose class/family carries a
        # purchase cost by default.
        # R4.3: cost-free status needs the class known exactly.
        self.costed = [i for i, (name, family, c) in enumerate(self.unit_classes)
                       if not _is_cost_free(name if c == 1.0 else None, family)]

        utilities = _as_dict(self.doc.get('utilities'))
        agents, seen = [], set()
        for group in ('heat_utilities', 'power_utilities', 'other_utilities'):
            for i, agent in enumerate(_dicts(utilities.get(group))):
                aid = agent.get('id')
                if _nonempty_str(aid):
                    if aid in seen:  # R2.1: first occurrence across groups
                        continue
                    seen.add(aid)
                agents.append((group, _converted(agent, self.quantity_units),
                               _label(f'utilities.{group}', agent, i)))
        self.agents = agents

    @property
    def molar_masses(self):
        """{chemical id: molar mass} of the declared chemicals whose molar
        mass is known (_chemical_molar_mass: declared, else from the
        formula), built on first use -- R5.3's mass-basis mole
        fractions."""
        if self._molar_masses is None:
            self._molar_masses = {}
            for chem in self.chemicals:
                cid = chem.get('id')
                if _nonempty_str(cid) and cid not in self._molar_masses:
                    mw = _chemical_molar_mass(chem)
                    if mw:
                        self._molar_masses[cid] = mw
        return self._molar_masses

    def nonempty_ports(self, unit_id):
        """(inlets, outlets) of unit `unit_id` that are not stated empty
        (_is_empty_stream: a stream of unknown flow counts), the R4.8
        port-count evidence; (0, 0) for a unit without an id."""
        if not _nonempty_str(unit_id):
            return 0, 0
        return tuple(sum(not _is_empty_stream(self.streams[i])
                         for i in side.get(unit_id, []))
                     for side in (self.unit_inlets, self.unit_outlets))

    def well_formed(self, reaction):
        """_well_formed against this document's chemical ids and indices;
        cached per reaction object. R7.4: mass conservation (R6.2) no
        longer decides it -- a non-conserving reaction is still a declared
        one (sc.conserves), so a unit's reaction evidence depends only on
        the species its reactions name, which deleting a field can only
        shrink."""
        cached = self._well_formed.get(id(reaction))
        if cached is None or cached[0] is not reaction:
            # The reaction object is kept with its verdict, so its id is
            # never reused by another object while cached.
            cached = self._well_formed[id(reaction)] = (reaction, (
                _well_formed(reaction, self.chem_ids, self.chem_by_index)))
        return cached[1]

    def conserves(self, reaction):
        """_conserves_mass (R6.2: True when closing on a mol or a wt basis
        within RXN_CLOSURE_RTOL, False when testably not, None when
        untestable), cached per reaction object."""
        cached = self._conserving.get(id(reaction))
        if cached is None or cached[0] is not reaction:
            cached = self._conserving[id(reaction)] = (
                reaction, _conserves_mass(self, reaction))
        return cached[1]

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


# Why a stated registry id earned no registry-tier credit (CMP-THM-02 notes).
_REGISTRY_FAULTS = {
    'malformed': 'registry_id is not a well-formed CAS number, InChI, '
                 'InChIKey or SMILES',
    'contradicted': "registry_id contradicts the chemical's molar mass or "
                    "formula (its database record, or its InChI formula "
                    "layer, disagrees)",
}


def _thm_02_credit(chem, cid):
    """(credit, missing suffix or None, tier) of one declared chemical's
    identity: the MAXIMUM over the tiers it reaches (R3.2; the first listed
    wins a tie) -- 'registry' 1 when its registry id resolves in the
    `chemicals` database without contradiction (_registry_status);
    'unresolved' REGISTRY_UNRESOLVED_CREDIT for a well-formed id the database
    cannot resolve (or any while it is unavailable); 'name'
    NAME_RESOLVED_CREDIT when the id resolves by name (never a formula with
    >= 2 carbon atoms) and the record's molar mass or formula matches the
    chemical's; 'unverified' NAME_UNVERIFIED_CREDIT when the chemical states
    neither; 'formula' FORMULA_ONLY_CREDIT when a parseable formula (or the
    formula-like id itself) narrows it down; else 'none' 0. A malformed or
    contradicted registry id counts as absent (R2.0), and the note says
    so. A registry id the database resolves to another species than the
    chemical's id names, with nothing stated to settle which is right
    ('conflicted', R5.5), is capped at REGISTRY_UNRESOLVED_CREDIT as
    'conflicted', and the note flags the conflict."""
    status = _registry_status(chem, cid)
    if status == 'resolved':
        return 1.0, None, 'registry'
    fault = _REGISTRY_FAULTS.get(status)
    tier = _name_tier(chem, cid)
    options = []
    if status == 'conflicted':
        options.append((REGISTRY_UNRESOLVED_CREDIT, 'conflicted', (
            '.registry_id (resolves to another species than the id names by '
            'name, and no molar mass or formula settles which is right: '
            'unverified)')))
    if status in ('unresolved', 'unavailable'):
        options.append((REGISTRY_UNRESOLVED_CREDIT, 'unresolved', (
            '.registry_id (well-formed, but the chemicals database does not '
            'resolve it: unverifiable)' if status == 'unresolved' else
            '.registry_id (well-formed; the chemicals database is unavailable '
            'to resolve it)')))
    record = _name_record(cid) if tier in ('verified', 'unverified') else None
    how = (f"a thermosteam built-in pseudo-component ({record.name})"
           if isinstance(record, _Builtin) else 'name')
    if tier == 'verified':
        options.append((NAME_RESOLVED_CREDIT, 'name', f'.registry_id (id '
                        f'resolvable by {how} only; molar mass verified)'))
    elif tier == 'unverified':
        options.append((NAME_UNVERIFIED_CREDIT, 'unverified', f'.registry_id '
                        f'(id resolvable by {how} only; no molar mass or '
                        f'formula to verify the match)'))
    why = (' (the name resolves to a compound of another molar mass)'
           if tier == 'mismatch' else
           ' (id is a molecular formula with >= 2 carbon atoms: '
           'isomer-ambiguous)' if _isomer_ambiguous(cid) else '')
    if _valid_formula(_as_dict(chem).get('formula')) or _formula_like(cid):
        options.append((FORMULA_ONLY_CREDIT, 'formula',
                        f'.registry_id (formula only){why}'))
    options.append((0.0, 'none', '.registry_id (no formula either; id not '
                    f'resolvable by name){why}'))
    credit, best, note = max(options, key=lambda option: option[0])
    return credit, note + (f'; {fault}' if fault else ''), best


def _referenced_mean(scored):
    """R4.7 aggregate of CMP-THM-02/03 over [(score, referenced)]: with no
    chemical referenced, the lowest single score (the same nested-set rule
    with the referenced set empty -- the plain mean would let deleting the
    last reference raise the score); otherwise the mean over the
    referenced chemicals plus every unreferenced chemical that lowers it --
    the minimum over all sets S (referenced <= S <= all) of mean(S), reached
    by adding unreferenced scores in ascending order while each lies below
    the running mean. It is never above min(mean over referenced, mean over
    all), so unreferenced chemicals can lower the score but never raise it
    (12 well-identified unreferenced chemicals padded onto a sparse file
    change nothing), and it stays deletion-monotone where that literal
    min(...) is not: a chemical losing its last reference (a deleted
    composition or reaction equation) only widens the family minimised
    over, whereas min(referenced mean, overall mean) would rise when a
    low-scoring chemical left the referenced mean."""
    if not any(ref for _s, ref in scored):
        return min(s for s, _ref in scored)
    chosen = [s for s, ref in scored if ref]
    total, n = sum(chosen), len(chosen)
    for s in sorted(s for s, ref in scored if not ref):
        if s >= total / n:
            break
        total, n = total + s, n + 1
    return total / n


def _score_thm_02(sc):  # CMP-THM-02
    population = _chemical_population(sc)
    if not population:
        return _empty_chemical_population(sc)
    refs = _chemical_references(sc)
    scored, missing, tiers, builtin = [], [], [], 0
    for chem, label, cid in population:
        if chem is None:
            scored.append((0.0, True))
            missing.append(f'{label}: not declared')
            continue
        credit, note, tier = _thm_02_credit(chem, cid)
        scored.append((credit, cid in refs))
        tiers.append(tier)
        builtin += (tier in ('name', 'unverified')
                    and isinstance(_name_record(cid), _Builtin))
        if note:
            missing.append(label + note)
    unreferenced = sum(1 for _s, ref in scored if not ref)
    return (_referenced_mean(scored), f'{tiers.count("registry")} of '
            f'{len(scored)} chemical(s) carry a database-resolved registry '
            f'id, {tiers.count("name")} more resolve by a verified name, '
            f'{tiers.count("unverified")} by an unverified one '
            f'({builtin} of these as thermosteam built-in pseudo-components), '
            f'{tiers.count("unresolved")} carry an unresolvable id'
            + (f', {tiers.count("conflicted")} an id naming another species '
               f'than their name' if 'conflicted' in tiers else '')
            + (f'; {unreferenced} unreferenced chemical(s) can only lower the '
               f'score' if unreferenced and len(scored) > unreferenced
               else ''), missing)


def _thm_03_credit(chem, cid):
    """(credit, missing suffix or None) of one declared chemical's molar
    mass (R5.6), the MAX over: a stated molar mass (> 0) or one derived
    from a parseable formula, 1; a registry id the database resolves
    without contradiction or conflict (_registry_status 'resolved') to a
    record with a molar mass, 1; an id resolving by name to a thermosteam
    built-in pseudo-component, or to a database record the chemical's own
    data verify (_name_tier), NAME_RESOLVED_CREDIT; to a database record
    nothing stated verifies, NAME_UNVERIFIED_CREDIT (R6.8: it may name
    another species); else 0."""
    derive = _validator()._molar_mass_from_formula  # same rule as _Context.molar_mass
    declared = _num(chem.get('molar_mass'))
    formula = chem.get('formula')
    if (declared is not None and declared > 0) or (
            _nonempty_str(formula) and derive(formula) is not None):
        return 1.0, None
    if _registry_status(chem, cid) == 'resolved' and _record_molar_mass(
            _database_record(_registry_query(chem.get('registry_id'))[1])
    ) is not None:
        return 1.0, None
    tier = _name_tier(chem, cid)
    record = _name_record(cid) if tier in ('verified', 'unverified') else None
    if record is not None and _record_molar_mass(record) is not None:
        if tier == 'verified' or isinstance(record, _Builtin):
            return NAME_RESOLVED_CREDIT, (
                f'.molar_mass (only from the thermosteam built-in '
                f'{record.name})' if isinstance(record, _Builtin) else
                '.molar_mass (only from the database record its name '
                'resolves to)')
        # R6.8: an unverified database name (nothing stated to check it
        # against) earns NAME_UNVERIFIED_CREDIT, as in CMP-THM-02.
        return NAME_UNVERIFIED_CREDIT, ('.molar_mass (only from the database '
                                        'record its name resolves to, '
                                        'unverified: it may name another '
                                        'species)')
    return 0.0, ('.molar_mass (or a parseable formula, or a registry id '
                 'the database resolves)')


def _score_thm_03(sc):  # CMP-THM-03
    population = _chemical_population(sc)
    if not population:
        return _empty_chemical_population(sc)
    refs = _chemical_references(sc)
    scored, missing = [], []
    for chem, label, cid in population:
        if chem is None:
            scored.append((0.0, True))
            missing.append(f'{label}: not declared')
            continue
        credit, note = _thm_03_credit(chem, cid)
        scored.append((credit, cid in refs))
        if note:
            missing.append(label + note)
    full = sum(1 for s, _ref in scored if s == 1.0)
    partial = sum(1 for s, _ref in scored if 0.0 < s < 1.0)
    return (_referenced_mean(scored),
            f'{full} of {len(scored)} chemical(s) have a molar mass (stated, '
            f'from a formula, or from a database-resolved registry id)'
            + (f', {partial} more only from a thermosteam built-in or a '
               f'(verified or unverified) database name' if partial else ''),
            missing)


# CMP-THM-04 credit of included_in_thermo_equilibria: false on a chemical the
# streams never show, which says it is locked but not in which phase (R4.5).
FLAG_UNOBSERVED_CREDIT = 0.5


def _thermo_flag(chem):
    """A chemical's VLE flag: ``included_in_thermo_equilibria`` (v0.2.3+), else
    the legacy ``included_in_thermo`` key written before v0.2.3, else None."""
    chem = _as_dict(chem)
    if 'included_in_thermo_equilibria' in chem:
        return chem['included_in_thermo_equilibria']
    return chem.get('included_in_thermo')


def _builtin_phase(chem, cid):
    """The locked phase of the thermosteam built-in pseudo-component that
    chemical id `cid` resolves to (R4.1: the database misses it, its
    normalised name is a built-in, and its molar mass or formula does not
    contradict the chemical's), else None."""
    record = _name_record(cid)
    if not isinstance(record, _Builtin) or _name_tier(chem, cid) not in (
            'verified', 'unverified'):
        return None
    return record.phase


def _thm_04_credit(chem, cid, seen):
    """(credit, note or None) of one declared chemical's phase behaviour:
    the MAXIMUM over (R4.5) (a) its ``included_in_thermo_equilibria`` flag
    (legacy ``included_in_thermo`` before v0.2.3, _thermo_flag) -- true (it
    partitions) and not contradicted by the streams showing it only ever as
    a solid, 1; false (locked) and shown in exactly one phase, 1; false and
    never shown, FLAG_UNOBSERVED_CREDIT (the locked phase is unnamed); a
    contradicted flag (false but shown in >= 2 phases, true but only ever
    's') or a non-boolean one counts as absent -- (b) the stream phases
    `seen` alone, two or more (partitions) or only 's' (locked),
    DERIVED_CREDIT; (c) the locked phase of the thermosteam built-in the id
    resolves to (_builtin_phase), DERIVED_CREDIT."""
    flag = _thermo_flag(chem)
    contradicted = ((flag is True and seen == {'s'})
                    or (flag is False and len(seen) >= 2))
    options = [(0.0, 'phase behaviour not stated '
                '(included_in_thermo_equilibria)')]
    if (flag is True and not contradicted) or (flag is False
                                               and len(seen) == 1):
        options.append((1.0, None))
    elif flag is False and not seen:
        options.append((FLAG_UNOBSERVED_CREDIT, 'included_in_thermo_equilibria '
                        'false (locked), but no stream shows the phase it is '
                        'locked in'))
    if len(seen) >= 2 or seen == {'s'}:
        options.append((DERIVED_CREDIT, 'phase behaviour derived from the '
                        'stream phases only'))
    phase = _builtin_phase(chem, cid)
    if phase:
        options.append((DERIVED_CREDIT, f'phase behaviour derived from the '
                        f"thermosteam built-in's locked phase '{phase}' only"))
    credit, note = max(options, key=lambda option: option[0])
    if note and contradicted:
        note += (f'; included_in_thermo_equilibria {str(flag).lower()} '
                 f'contradicts the stream phases')
    return credit, note


def _score_thm_04(sc):  # CMP-THM-04
    population = _chemical_population(sc)
    if not population:
        return _empty_chemical_population(sc)
    phases = _chemical_phases(sc)
    scores, missing, full, derived = [], [], 0, 0
    for chem, label, cid in population:
        if chem is None:
            scores.append(0.0)
            missing.append(f'{label}: not declared')
            continue
        seen = phases.get(cid, set()) if cid else set()
        credit, note = _thm_04_credit(chem, cid, seen)
        scores.append(credit)
        full += credit == 1.0
        derived += credit == DERIVED_CREDIT
        if note:
            shown = ','.join(sorted(seen)) or 'none'
            missing.append(f'{label}: {note}; stream phases seen: {shown}')
    return (_mean(scores), f'{full} of {len(scores)} chemical(s) state '
            f'included_in_thermo_equilibria consistently with the stream '
            f'phases; '
            f'{derived} more derivable (stream phases or a built-in locked '
            f'phase)', missing)


def _score_thm_05(sc):  # CMP-THM-05
    # Population = declared + referenced ids (R2.3): a declared, unreferenced
    # chemical still needs property data (Chemicals.compile() requires key
    # properties for every member). Exempt only a chemical the `chemicals`
    # database actually RESOLVES (R3.2, _database_resolved): a registry id
    # it finds without contradiction, or a verified/unverified name (not a
    # mismatching or isomer-ambiguous one) -- resolved as thermosteam's
    # Chemical(ID) resolves it, its built-in pseudo-components included
    # (R4.1: Cellulose, Starch, Yeast, Ash, ... come with their property
    # models). A well-formed id the database does not know (an invented
    # CAS, or a real one it does not list, such as cellulose's) proves
    # nothing, and with the database unavailable only a built-in is exempt.
    # An undeclared referenced id is judged by its name.
    population = _chemical_population(sc)
    if not population:
        return _empty_chemical_population(sc)
    lacking = [label for chem, label, cid in population
               if not _database_resolved(chem, cid)]
    if not lacking:
        return (None, 'every chemical resolves as thermosteam resolves it '
                '(database registry id or name, or a built-in '
                'pseudo-component)', [])
    missing = [f'{label}: property data (V, Cn, H/S reference, Hf, '
               f'Psat/Tb/Hvap, LHV) not representable' for label in lacking]
    unavailable = ('; the chemicals database is unavailable, so only '
                   'built-in pseudo-components resolve'
                   if _chemicals_search() is None else '')
    return (0.0, f'{len(lacking)} chemical(s) resolved neither by registry '
            f'id nor by name (database or thermosteam built-in) need '
            f'property data no SFF field can state{unavailable}', missing)


_TPP_KEYS = ('mixture', 'gamma', 'phi', 'PCF')
# R8.12: IdealMixture(chemicals, include_excess_energies=False) -- the flag
# adds excess enthalpy and entropy (about 8 % of a gas mixture's enthalpy at
# 30 bar), so a descriptor naming the class without it states half the
# mixture model (MIXTURE_FLAG_UNSTATED_CREDIT). No other mixture class takes
# it.
_EXCESS_ENERGIES_RE = re.compile(
    r'\binclude_excess_energies\s*=\s*(?:True|False)\b')


def _mixture_credit(value):
    """R8.12: credit of a thermo_property_package 'mixture' descriptor --
    0 unless it names a thermosteam mixture class
    (_valid_package_descriptor); an IdealMixture must also state
    include_excess_energies (True or False, as the exporter writes
    'IdealMixture(include_excess_energies=False)'), else
    MIXTURE_FLAG_UNSTATED_CREDIT; any other class 1."""
    if not _valid_package_descriptor('mixture', value):
        return 0.0
    if _LEADING_IDENTIFIER_RE.match(value).group(1) != 'IdealMixture':
        return 1.0
    return (1.0 if _EXCESS_ENERGIES_RE.search(value)
            else MIXTURE_FLAG_UNSTATED_CREDIT)


def _score_thm_06(sc):  # CMP-THM-06
    if not sc.units:
        return None, 'no units', []
    # A descriptor counts only when it names a thermosteam model class of its
    # role (R2.0; _valid_package_descriptor); an IdealMixture only in part
    # without its include_excess_energies flag (R8.12, _mixture_credit).
    scores, missing = [], []
    for unit, label in zip(sc.units, sc.unit_labels):
        tpp = _as_dict(unit.get('thermo_property_package'))
        credits = {k: (_mixture_credit(tpp.get(k)) if k == 'mixture' else
                       float(_valid_package_descriptor(k, tpp.get(k))))
                   for k in _TPP_KEYS}
        scores.append(sum(credits.values()) / len(_TPP_KEYS))
        for k, credit in credits.items():
            if credit == 0.0:
                missing.append(f'{label}.thermo_property_package.{k}')
            elif credit < 1.0:
                missing.append(f'{label}.thermo_property_package.{k}: '
                               f'IdealMixture without include_excess_energies '
                               f'(partial credit)')
    return (_mean(scores), f'{scores.count(1.0)} of {len(scores)} unit(s) '
            f'state a full property package', missing)


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
    scores, missing = [], []
    for package, label in zip(packages, sc.unit_labels):
        if _nonempty_str(package.get('id')):
            credit = 1.0
        else:
            credit = DERIVED_CREDIT if single else 0.0
            missing.append(f'{label}.thermo_property_package.id'
                           + (' (one full package shared by every unit: '
                              'a single chemical set is implied)'
                              if single else ''))
        scores.append(credit)
    named = sum(1 for p in packages if _nonempty_str(p.get('id')))
    return (_mean(scores), f'{named} of {len(packages)} unit(s) name their '
            f'chemical set' + ('; every unit shares one full property package '
                               '(single thermo implied)' if single else ''),
            missing)

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
            elif _is_dangling(endpoint):  # R5.8: never the boundary
                missing.append(f'{label}.{key}: not stated (dangling: '
                               f'neither a unit id nor the "None" boundary)')
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


def _port_order_derivable(sc, position, side, family=None):
    """Whether the order of a multi-stream side follows from the data: flash
    outs (exactly one all-vapor and one all-liquid outlet), splitter outs
    (explicit split + full port states), distillation outs (explicit LHK +
    outlet compositions), HXprocess sides (all four streams carry
    composition). Every one reads stream data, so the unit's balances must
    close (R2.2, _closure). `family`: the family whose convention is read
    (default: the unit's)."""
    if not _closure(sc, position)[0]:
        return False
    family = sc.unit_classes[position][1] if family is None else family
    explicit = _explicit_keys(sc.units[position], family,
                              sc.spec_units[position])
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
    """(credit, missing locator or None) for the port order of one unit side
    (_family_side_credit under the unit's family). R8.4: a claim held to
    reactor rules for the reactions it declares (_declared_chemistry) takes
    the lower of its own family's port convention and the reactor
    family's -- it may be that reactor, whose ports have no convention --
    so a 'Tank', 'Pump' or 'Flash drum' label cannot trade a declared
    fermentation's port needs for a tank's, pump's or flash's; so does a
    hosting tank claim that is not ruled out (_reactor_candidate, R7.1),
    so a 'Holding tank' label cannot trade an undeclared fermentation's
    port needs for a tank's order-free inlets."""
    family = sc.unit_classes[position][1]
    own = _family_side_credit(sc, position, side, family)
    if _reactor_candidate(sc, position):
        credit, locator = _family_side_credit(sc, position, side, 'reactor')
        if credit < own[0]:
            why = ('reactions it declares, R8.4'
                   if _declared_chemistry(sc, position)
                   else 'a hosting claim not ruled out, R7.1')
            return credit, (f"{locator}; held to a reactor's port "
                            f"convention, as it may host a reaction ({why})")
    return own


def _family_side_credit(sc, position, side, family):
    """(credit, missing locator or None) for the port order of one unit side
    under the conventions of `family`:
    order-free -> 1; a unit without an id -> 0 (R3.8: no stream can name its
    ports). A side on which at most one stream carries material (m <= 1;
    stated-empty streams are placeholders, like unexported MissingStream
    ports) -> 1 on a single-port side, else DERIVED_CREDIT (unexported or
    empty ports may exist) -- both resting on the stream COUNT, so only when
    the unit's TOTAL mass closes (_mass_closure, R5.3: that alone shows no
    material-carrying port is missing, whatever basis -- or none -- its
    species flows are computable on; R5.8: nor may a dangling stream be a
    missing port, so detaching a stream's endpoint can never raise the
    credit); else 0. Two or more material-carrying streams ->
    DERIVED_CREDIT when their order is derivable (which reads stream data,
    so needs the full _closure), else 0."""
    label = sc.unit_labels[position]
    if not _nonempty_str(sc.units[position].get('id')):
        # R3.8: no stream can name the ports of a unit without an id (its
        # streams, if any, dangle as an undeclared referenced id).
        return 0.0, (f'{label}.{side}: no unit id, so no stream can state '
                     f'its ports')
    streams = _side_streams(sc, position, side)
    n = len(streams)
    if (family, side) in _ORDER_FREE_SIDES:
        return 1.0, None
    m = sum(1 for i in streams if not _is_empty_stream(sc.streams[i]))
    if m <= 1:
        closes, why = _mass_closure(sc, position)
        if not closes:
            return 0.0, (f'{label}.{side}: port index not representable (no '
                         f'port convention applies: {why})')
        if (family, side) in _SINGLE_PORT_SIDES:
            return 1.0, None
        return DERIVED_CREDIT, (f'{label}.{side}: port index not representable '
                                f'(unexported or empty ports may exist)')
    if _port_order_derivable(sc, position, side, family):
        return DERIVED_CREDIT, (f'{label}.{side}: order of {n} streams not '
                                f'representable (derivable from stream data)')
    return 0.0, f'{label}.{side}: order of {n} streams not representable'


def _score_top_02(sc):  # CMP-TOP-02
    if not sc.units and not sc.phantom_units:
        return None, 'no units', []
    scores, missing = [], []
    for position in range(len(sc.units)):
        for side in ('ins', 'outs'):
            credit, locator = _side_credit(sc, position, side)
            scores.append(credit)
            if locator:
                missing.append(locator)
    ok = scores.count(1.0)
    # R3.8: both sides of an undeclared referenced unit score 0.
    phantom_scores, phantom_missing = _phantom_scores(
        sc, 'its port conventions are unknown')
    return (_mean(scores + phantom_scores * 2), f'not representable; {ok} of '
            f'{len(scores)} unit port side(s) unambiguous by convention'
            + _phantom_note(sc), missing + phantom_missing)

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


def _dangling_note(sc, position):
    """Missing locator of dangling stream `position` (R5.8) in the
    CMP-FEED-01/02 populations: the endpoint key(s) not stated, and that it
    scores 0 there (its connectivity is unknown: it may be a boundary
    stream, but is never credited as one)."""
    stream = sc.streams[position]
    keys = '|'.join(key for key in ('source_unit_id', 'sink_unit_id')
                    if _is_dangling(stream.get(key)))
    return (f'{sc.stream_labels[position]}.{keys}: not stated (dangling: '
            f'never a boundary stream, scores 0)')


# CMP-FEED-01 composition credit of a feed whose plausible composition is a
# legacy flat list with an untagged entry: its phase split is unknown, and
# BioSTEAM would assume liquid (R4.6).
UNTAGGED_COMPOSITION_CREDIT = 0.75

# thermosteam's phase symbols (thermosteam._phase.valid_phases).
_PHASE_SYMBOLS = frozenset({'s', 'l', 'g', 'S', 'L'})


def _feed_phases_stated(stream):
    """True when a feed's phases are stated (R4.6): its composition is given
    per phase (``stream_properties.phases``, _phases_ok: molar or mass
    basis, R5.3; R7.3: a single-phase stream's phase amount may be its
    volume flow),
    or its legacy flat composition tags every entry with a thermosteam
    phase symbol."""
    sp = _as_dict(_as_dict(stream).get('stream_properties'))
    if _phases_ok(sp.get('phases'), sp, volumetric=True):
        return True
    entries = _as_list(sp.get('composition'))
    return bool(entries) and all(
        isinstance(e, dict) and isinstance(e.get('phase'), str)
        and e['phase'] in _PHASE_SYMBOLS for e in entries)


def _feed_01_entry(sc, position, zero_note=None):
    """(score, fully stated, missing locators) of feed `position` on its
    state (T, P, flow, composition; equal parts). `zero_note` not None: the
    feed's stated zero flow earns no placeholder credit (_score_feed_01), so
    its flow counts as missing (no composition exemption either) and
    `zero_note` explains why on the flow locator. R6.7: a missing flow part
    earns _volumetric_flow_credit (MAX over tiers); R7.3: the volume flow
    is then also the amount of a single-phase stream's phase stating none
    (_phases_ok), so the stated composition earns its part. R4.6: a plausible composition without its phases
    stated earns UNTAGGED_COMPOSITION_CREDIT."""
    stream = sc.streams[position]
    state = _stream_state(stream, empty_exempt=False, volumetric=True)
    if zero_note is not None:
        state['flow'] = False
    parts = {key: float(ok) for key, ok in state.items()}
    volumetric = 0.0 if state['flow'] else _volumetric_flow_credit(stream)
    parts['flow'] = max(parts['flow'], volumetric)
    untagged = state['composition'] and not _feed_phases_stated(stream)
    if untagged:  # R4.6
        parts['composition'] = UNTAGGED_COMPOSITION_CREDIT
    missing = []
    for key, ok in state.items():
        if ok:
            continue
        note = ''
        if key == 'flow':
            note = (zero_note or '') + (_VOLUMETRIC_NOTE if volumetric else '')
        missing.append(f'{sc.stream_labels[position]}.{_STATE_PATHS[key]}{note}')
    if untagged:
        missing.append(f'{sc.stream_labels[position]}.stream_properties.'
                       f'composition[*].phase (untagged: BioSTEAM would '
                       f'assume liquid)')
    return (sum(parts.values()) / len(parts),
            all(state.values()) and not untagged, missing)


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
    # feed's flow switched it to equal weights.) R3.7 / R6.3: the empty-feed
    # credit also needs a FREE FIXED inlet port of the sink unit
    # (_placeholder_ports: an exactly known class with a fixed inlet count,
    # less the inlets that are not stated empty, and the unit's total mass
    # closure); padding a unit of unknown class, a variable-inlet class
    # (Mixer) or a family-only claim with zero-flow feeds earns nothing, and
    # empties beyond the free ports score as missing-flow feeds.
    # A stream with both endpoints at the boundary (isolated: a feed whose
    # sink_unit_id was lost) stays in the population, so deleting an
    # endpoint never sheds an incomplete feed (R3.8). R4.6: a plausible
    # composition earns its full part only with its phases stated (per-phase
    # shape, or every legacy flat entry phase-tagged); an untagged legacy
    # entry leaves the phase to BioSTEAM's liquid default, so the part earns
    # UNTAGGED_COMPOSITION_CREDIT. R5.8: a stream with an endpoint not
    # stated (dangling) may be a feed -- a feed whose source or sink key was
    # lost is one -- so it stays in the population, but it is never a
    # boundary stream: it scores 0 and anchors nothing (else detaching an
    # internal stream would add a fully stated 'feed'). R6.7: a feed stating
    # only a volumetric flow (with T, P and composition) earns
    # DERIVED_CREDIT for its flow part.
    feeds = sorted(sc.feeds + sc.isolated)
    if not feeds + sc.dangling:
        return (0.0, 'no feed streams: nothing defines the degrees of freedom',
                ['streams: no feed (source_unit_id "None", sink a unit)'])
    anchored = any(_states_flow(sc.streams[i]) for i in feeds)
    empties = [i for i in feeds if _is_empty_stream(sc.streams[i])]
    if anchored:
        credited, why = _placeholder_ports(
            sc, empties, 0, lambda i: _feed_01_entry(sc, i, '')[0])
    else:
        credited = set()
        why = dict.fromkeys(empties, _no_placeholder(
            'no feed states a non-zero flow'))
    scores, missing, full = [], [], 0
    for i in sc.dangling:
        scores.append(0.0)
        missing.append(_dangling_note(sc, i))
    for i in feeds:
        if i in credited:
            scores.append(1.0)
            continue
        score, complete, locators = _feed_01_entry(sc, i, why.get(i))
        scores.append(score)
        full += complete
        missing.extend(locators)
    message = f'{full + len(credited)} of {len(feeds)} feed(s) fully stated'
    if sc.dangling:
        message += (f'; {len(sc.dangling)} dangling stream(s) (an endpoint '
                    f'not stated) score 0')
    if empties:
        message += (f'; {len(empties)} empty feed(s) (zero flow), '
                    f'{len(credited)} credited as placeholder port(s) (free '
                    f'fixed inlets of a unit of exactly known class)')
    if not anchored:
        message += '; no feed states a non-zero flow'
    return _mean(scores), message, missing


def _fixed_port_counts(sc, position):
    """R6.3: (inlets, outlets) port counts fixed by unit `position`'s
    exactly known class (confidence 1): an exact core class's _PORT_LIMITS
    entry, a corpus-known custom class's _NONCORE_PORT_LIMITS entry; None on
    a side the class leaves variable (a Mixer's inlets) or that no table
    lists, and (None, None) for any claim below confidence 1 (a family, a
    label, a key-set inference), which fixes no port count."""
    name, _family, confidence = sc.unit_classes[position]
    if confidence < 1.0 or not isinstance(name, str):
        return None, None
    table = _PORT_LIMITS if sc.unit_core[position] else _NONCORE_PORT_LIMITS
    return table.get(name, (None, None))


_PORT_SIDES = ('inlet', 'outlet')


def _no_placeholder(reason):
    """Locator note of an empty boundary stream that earns no placeholder
    credit (N17: its zero flow IS stated; what is missing is a port to hold
    it, so CMP-FEED-01 scores its flow, and CMP-FEED-02 its price, as
    missing): ' (zero flow stated, but no placeholder credit: <reason>)'."""
    return f' (zero flow stated, but no placeholder credit: {reason})'


def _placeholder_ports(sc, positions, side, fallback):
    """R6.3: (credited, why) -- which EMPTY streams (_is_empty_stream) among
    boundary streams `positions` earn placeholder-port credit, and, for
    every other empty one, a locator note saying why not. `side` 0: feeds,
    held by their sink unit's inlets; 1: products, by their source unit's
    outlets. A unit holds at most its free fixed ports on that side
    (_fixed_port_counts less its streams there that are not stated empty,
    unknown flows included -- sc.unit_ports), and only when its total mass
    closure is shown (_mass_closure: no material-carrying port is missing).
    Variable-port classes, claims below confidence 1 and undeclared units
    hold none. Where a unit's empties exceed its free ports, those with the
    lowest `fallback` score (their score as missing-flow streams) are
    credited, which maximises the criterion; deleting a field can then only
    lower a fallback, or turn an empty stream into one of unknown flow that
    takes a port and leaves the pool, never raise the sum."""
    key = ('sink_unit_id', 'source_unit_id')[side]
    noun = _PORT_SIDES[side]
    by_unit, why = {}, {}
    for i in positions:
        if not _is_empty_stream(sc.streams[i]):
            continue
        uid = sc.streams[i].get(key)
        unit = (sc.unit_positions.get(uid) if isinstance(uid, str)
                and not _is_boundary(uid) else None)
        if unit is None:
            why[i] = _no_placeholder('no declared unit holds it as a port')
        else:
            by_unit.setdefault(unit, []).append(i)
    credited = set()
    for unit, empties in by_unit.items():
        label = sc.unit_labels[unit]
        name, _family, confidence = sc.unit_classes[unit]
        fixed = _fixed_port_counts(sc, unit)[side]
        closes, reason = _mass_closure(sc, unit)
        if confidence < 1.0:
            note = _no_placeholder(f'{label}, whose class is not known '
                                   f'exactly, fixes no port')
        elif fixed is None:
            note = _no_placeholder(f'{label}, whose {name} {noun} count is '
                                   f'not fixed')
        elif not closes:
            note = _no_placeholder(f'{label}, whose total mass closure is '
                                   f'not shown ({reason}), may lack ports')
        else:
            taken = sc.unit_ports[unit][side]
            free = max(0, fixed - taken)
            ranked = sorted(empties, key=lambda i: (fallback(i), i))
            credited.update(ranked[:free])
            empties = ranked[free:]
            note = _no_placeholder(f'beyond the free fixed {noun} ports of '
                                   f'{label}: {name} has {fixed}, {taken} '
                                   f'taken by streams not stated empty')
        for i in empties:
            why[i] = note
    return credited, why


# Chemical ids (lowercase) of each utility-port material (R2.9).
_PORT_MATERIAL_IDS = {
    'natural gas': frozenset({'ch4', 'methane', 'naturalgas', 'natural_gas'}),
    'lime': frozenset({'lime', 'cao', 'ca(oh)2', 'caoh2', 'calciumoxide',
                       'calciumhydroxide', 'calciumdihydroxide'}),
    'boiler chemicals': frozenset({'boilerchems', 'boilerchemicals',
                                   'boiler_chemicals'}),
    'makeup water': frozenset({'water', 'h2o'}),
    'ash disposal': frozenset({'ash'}),  # R8.15: an outlet port
}
PORT_MATERIAL_MIN = 0.5  # mol fraction making a feed a port's material


def _mole_fractions(stream, molar_masses=None):
    """{lowercase component id: mol fraction} of a non-empty stream whose
    molar species flows are computable (_species_flows), else -- R5.3 --
    whose mass species flows are and every component of which has a molar
    mass in `molar_masses` ({chemical id: g/mol}); else None."""
    flows = _species_flows(stream)
    if flows is None and molar_masses:
        mass = _species_flows(stream, 'mass')
        if mass is not None and all(molar_masses.get(n) for n in mass):
            flows = {n: m / molar_masses[n] for n, m in mass.items()}
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
    if _facility_kind(sc, position) == 'CHP':
        return ()  # R8.10: a CHP buys no fuel; its inlets are process feeds
    if family in ('boiler', 'boiler_turbogenerator'):
        return ('natural gas', 'lime', 'boiler chemicals')
    if family == 'facility' and _facility_kind(sc, position) in (
            None, 'ProcessWaterCenter'):
        return ('makeup water',)
    return ()


def _port_material(sc, position, materials):
    """The utility-port material of `materials` stream `position` may be:
    one its known composition (_mole_fractions) holds at >=
    PORT_MATERIAL_MIN (natural gas: >= 50 mol% CH4), or -- an unknown
    composition may be any of them (R2.13) -- 'a/b?'; None when its known
    composition rules every one out."""
    fractions = _mole_fractions(sc.streams[position], sc.molar_masses)
    for material in materials:
        if fractions is None or sum(
                x for name, x in fractions.items()
                if name in _PORT_MATERIAL_IDS[material]) >= PORT_MATERIAL_MIN:
            return (material if fractions is not None
                    else '/'.join(materials) + '?')
    return None


def _utility_port_feed(sc, position):
    """(sink unit label, material) when feed `position` enters a unit with
    utility-port materials (_port_materials) and its composition does not
    rule them out (_port_material): a feed whose composition is known is
    the port's material only when >= PORT_MATERIAL_MIN of it is that
    material; an unknown composition may be (R2.13). Else None: air and
    other feeds are scored normally."""
    sink = sc.streams[position].get('sink_unit_id')
    unit = sc.unit_positions.get(sink) if isinstance(sink, str) else None
    materials = _port_materials(sc, unit) if unit is not None else ()
    material = _port_material(sc, position, materials) if materials else None
    return (sc.unit_labels[unit], material) if material else None


def _port_products(sc, position):
    """R8.15: outlet utility-port materials of unit `position` that
    BioSTEAM prices itself when the product's own price is 0: a boiler's
    or boiler-turbogenerator's ash disposal (bst.stream_utility_prices
    ['Ash disposal'], defined as a utility of the class) -- not a CHP's,
    which prices nothing -- and, since either may be one (R6.5), a
    facility of unknown kind's or a unit of unknown family's; else ()."""
    family = sc.unit_classes[position][1]
    if family in ('boiler', 'boiler_turbogenerator'):
        return (() if _facility_kind(sc, position) == 'CHP'
                else ('ash disposal',))
    if family is None or (family == 'facility'
                          and _facility_kind(sc, position) is None):
        return ('ash disposal',)
    return ()


def _utility_port_product(sc, position):
    """R8.15: (source description, material) when product `position`
    leaves a unit with outlet utility-port materials (_port_products; a
    unit id no unit declares may be one too) and its composition does not
    rule them out (_port_material: ash disposal is >= PORT_MATERIAL_MIN
    ash, or the composition is unknown); else None."""
    source = sc.streams[position].get('source_unit_id')
    if not _nonempty_str(source) or _is_boundary(source):
        return None
    unit = sc.unit_positions.get(source)
    if unit is None:
        where, materials = (f'units[{source}] (referenced, not declared)',
                            ('ash disposal',))
    else:
        where, materials = sc.unit_labels[unit], _port_products(sc, unit)
    material = _port_material(sc, position, materials) if materials else None
    return (where, material) if material else None


def _score_feed_02(sc):  # CMP-FEED-02
    # Every boundary stream counts (R1.0); an EMPTY one (total flow
    # numerically 0) held as a placeholder port (R6.3, below) needs no price
    # and scores 1, so deleting its flow (or its stream_properties) can only
    # lower its credit -- excluding it let
    # that deletion move a priced empty stream into the population. An
    # isolated stream (both endpoints at the boundary) counts too: deleting
    # an unpriced product's source_unit_id must not drop it (R3.8). R5.8: a
    # dangling stream (an endpoint not stated) may be a boundary stream, so
    # it stays in the population, but it is never credited as one: it
    # scores 0, even when empty (else detaching a priced or empty internal
    # stream's endpoint would add a credited 'boundary' stream).
    # R6.3: an EMPTY stream needs no price only as a placeholder port
    # (_placeholder_ports: a free fixed port of an exactly known class whose
    # total mass closure is shown, feeds at the sink unit's inlets, products
    # at the source unit's outlets); any other empty stream is scored on its
    # price like a stream of unknown flow. R6.5: a 0 price on a feed into a
    # unit that is not declared or is of unknown family earns half credit:
    # that unit may be one whose utility port BioSTEAM prices itself.
    positions = sc.feeds + sc.outlets + sc.isolated
    if not positions + sc.dangling:
        return None, 'no boundary streams', []
    feeds = set(sc.feeds)
    entry = lambda i: _feed_02_entry(sc, i, i in feeds)
    fallback = lambda i: entry(i)[0]
    credited, why = _placeholder_ports(sc, sc.feeds + sc.isolated, 0,
                                       fallback)
    outlets, why_out = _placeholder_ports(sc, sc.outlets, 1, fallback)
    credited |= outlets
    why.update(why_out)
    scores = [0.0 for _i in sc.dangling]
    missing = [_dangling_note(sc, i) for i in sc.dangling]
    priced = 0
    for i in positions:
        if i in credited:
            scores.append(1.0)
            continue
        score, note = entry(i)
        scores.append(score)
        priced += score == 1.0
        if note:
            missing.append(note + (why.get(i, '') if note.endswith('.price')
                                   else ''))
    needing = len(positions) - len(credited)
    return (_mean(scores), f'{priced} of {needing} boundary stream(s) needing '
            f'a price priced' + (f' ({len(credited)} empty placeholder '
                                 f'port(s), needing no price)'
                                 if credited else '')
            + (f'; {len(sc.dangling)} dangling stream(s) (an endpoint not '
               f'stated) score 0' if sc.dangling else ''), missing)


def _feed_02_entry(sc, position, feed):
    """(score, missing locator or None) of boundary stream `position` on
    its price (CMP-FEED-02): unstated -> 0; 0 on a feed (`feed` True) into
    a utility port (_utility_port_feed) or into a unit that is not declared
    or is of unknown family (_unresolved_sink, R6.5) -> 0.5; 0 on a
    product out of an outlet utility port (_utility_port_product, R8.15:
    ash disposal) -> 0.5; else 1."""
    label = sc.stream_labels[position]
    price = _num(sc.streams[position].get('price'))
    if price is None:
        return 0.0, f'{label}.price'
    if price != 0:
        return 1.0, None
    if not feed:
        # R8.15: a 0-priced ash-disposal outlet is priced by BioSTEAM too.
        port = _utility_port_product(sc, position)
        if port:
            return 0.5, (f'{label}.price: 0 on the {port[1]} outlet of '
                         f'{port[0]} (priced by BioSTEAM, not representable)')
        return 1.0, None
    port = _utility_port_feed(sc, position)
    if port:
        # BioSTEAM prices a 0-priced utility-port material itself
        # (bst.stream_utility_prices or a class default), which SFF
        # cannot state.
        return 0.5, (f'{label}.price: 0 on the {port[1]} port of {port[0]} '
                     f'(priced by BioSTEAM, not representable)')
    sink = _unresolved_sink(sc, position)
    if sink:
        return 0.5, (f'{label}.price: 0 into {sink}, which may be a utility '
                     f'port BioSTEAM prices itself (half credit)')
    return 1.0, None


def _unresolved_sink(sc, position):
    """R6.5: a description of feed `position`'s sink when it is a unit id no
    unit declares ('units[X] (referenced, not declared)') or a declared unit
    of unknown family ('units[X] (unknown family)'), else None. Such a unit
    may have a utility port BioSTEAM prices itself, so a 0 price there is
    not affirmatively a free feed."""
    sink = sc.streams[position].get('sink_unit_id')
    if not _nonempty_str(sink) or _is_boundary(sink):
        return None
    unit = sc.unit_positions.get(sink)
    if unit is None:
        return f'units[{sink}] (referenced, not declared)'
    if sc.unit_classes[unit][1] is None:
        return f'{sc.unit_labels[unit]} (unknown family)'
    return None

#%% Criteria -- unit operations

def _class_note(sc, position):
    """Why unit `position` did not identify its class at confidence 1, as a
    missing locator, or None when it did."""
    unit, label = sc.units[position], sc.unit_labels[position]
    name, family, confidence = sc.unit_classes[position]
    if confidence == 1.0:
        return None
    named = _dsm_class(unit)
    contradicted = _port_notes(sc, position)
    implausible = _physics_notes(sc, position)
    suffix = ((f"; ignored as contradicted by its ports (R4.8): "
               f"{'; '.join(contradicted)}" if contradicted else '')
              + (f"; ignored as physically implausible (R5.1 / R6.1): "
                 f"{'; '.join(implausible)}" if implausible else ''))
    if name is None and named[0]:
        by_ports = any(n.startswith(f"class '{named[0]}' ")
                       for n in contradicted)
        why = ('is abstract' if named[0] in _ABSTRACT_CLASSES else
               'is not a concrete core BioSTEAM class, although its URL '
               'claims biosteam' if _class_confidence(
                   named[0], named[1], sc.pinned_repo) is None else
               'is contradicted by its ports' if by_ports else
               'is physically implausible')
        return (f"{label}.design_simulation_method: class '{named[0]}' {why}"
                + (f" (family '{family}' only)" if family else
                   ' (no family either)') + suffix)
    if confidence == 0.75 and name:
        return (f"{label}: custom class '{name}' is not core BioSTEAM and its "
                f"source URL is not in the repository "
                f"metadata.reproducibility.flowsheet_model_package pins "
                f"(url + commit|version)")
    if confidence == 0.75:
        hint, inferred = (_infer_class_from_specs(unit, sc.registry)
                          or (None, None))
        variant = (f" as its '{inferred}' rule-set variant (R7.2)"
                   if inferred and inferred != family else '')
        return (f"{label}.design_simulation_method: class not stated (family "
                f"'{family}' confirmed{variant} by every design-spec "
                f"registry class holding its design_input_specs keys"
                + (f"; tightest '{hint}'" if hint else '') + ')' + suffix)
    if confidence == 0.5:
        return (f"{label}.design_simulation_method: class not stated (family "
                f"'{family}' from unit_type keywords / a named abstract "
                f"class; its design_input_specs keys do not confirm it "
                f"unambiguously)" + suffix)
    return (f"{label}: class unresolved (unit_type {unit.get('unit_type')!r} "
            f"names no known family)" + suffix)


def _port_notes(sc, position):
    """R4.8: the claims of unit `position` its ports contradict, each as
    "<claim> (<why>)" -- a design_simulation_method class (with its family)
    and the unit_type keyword family."""
    unit, ports = sc.units[position], sc.unit_ports[position]
    notes = []
    name, source = _dsm_class(unit)
    keyword = _keyword_family(unit.get('unit_type'))
    if name and _class_confidence(name, source, sc.pinned_repo) is not None:
        why = _port_contradiction(name, _class_family(name) or keyword,
                                  _is_core_class(name, source), ports)
        if why:
            notes.append(f"class '{name}' ({why})")
    why = _port_contradiction(None, keyword, False, ports,
                              unit.get('unit_type'))
    if why and not (keyword == 'hx_utility'
                    and _generic_exchanger(unit.get('unit_type'))
                    and _ports_plausible(None, 'hx_process', False, ports)):
        notes.append(f"unit_type family '{keyword}' ({why})")
    return notes


def _physics_notes(sc, position):
    """R5.1 / R6.1: the claims of unit `position` its stream physics
    contradict (sc.unit_physics, _physics_contradiction: mixers, pure
    splitters, junctions, claims that cannot host chemistry, built-in
    chemistry classes), each as "<claim> (<why>)" -- a
    design_simulation_method class (with its family) and the unit_type
    keyword family."""
    unit, physics = sc.units[position], sc.unit_physics[position]
    notes = []
    name, source = _dsm_class(unit)
    keyword = _keyword_family(unit.get('unit_type'))
    confidence = _class_confidence(name, source, sc.pinned_repo) if name else None
    if name and confidence is not None:
        why = _physics_contradiction(name, _class_family(name) or keyword,
                                     physics, _is_core_class(name, source),
                                     confidence)
        if why:
            notes.append(f"class '{name}' ({why})")
    why = _physics_contradiction(None, keyword, physics,
                                 label=unit.get('unit_type'))
    if why:
        notes.append(f"unit_type family '{keyword}' ({why})")
    return notes


def _phantom_scores(sc, what):
    """(scores, missing) for the R3.8 undeclared referenced units: each
    scores 0 (counting once, like every unit: R4.12), its locator saying
    `what` it lacks."""
    return ([0.0 for _uid in sc.phantom_units],
            [f'units[{uid}]: referenced by a stream endpoint but not '
             f'declared, so {what}' for uid in sc.phantom_units])


def _phantom_note(sc):
    """Message suffix counting the R3.8 undeclared referenced units."""
    n = len(sc.phantom_units)
    return (f'; {n} referenced but undeclared unit id(s) score 0' if n else '')


def _score_uop_01(sc):  # CMP-UOP-01
    if not sc.units and not sc.phantom_units:
        return None, 'no units', []
    missing = [note for note in (_class_note(sc, p) for p in range(len(sc.units)))
               if note]
    exact = sum(1 for c in sc.unit_classes if c[2] == 1.0)
    scores = [c[2] for c in sc.unit_classes]
    phantom_scores, phantom_missing = _phantom_scores(sc, 'its class is unknown')
    return (_mean(scores + phantom_scores),
            f'{exact} of {len(sc.units)} unit(s) identify their class at '
            f'confidence 1' + _phantom_note(sc), missing + phantom_missing)


def _score_uop_02(sc):  # CMP-UOP-02
    scores, missing = [], []
    for position in _spec_population(sc):
        score, unit_missing = _unit_spec_score(sc, position)
        if score is None:
            continue
        scores.append(score)
        missing.extend(unit_missing)
    phantom_scores, phantom_missing = _phantom_scores(
        sc, 'its operating specifications are unknown')
    if not scores + phantom_scores:
        return None, 'no units that need operating specifications', []
    return (_mean(scores + phantom_scores), f'{scores.count(1.0)} of '
            f'{len(scores)} applicable unit(s) fully specified'
            + _phantom_note(sc), missing + phantom_missing)


def _score_uop_03(sc):  # CMP-UOP-03
    if sc.registry_error:
        raise RuntimeError(f'design-spec registry unavailable: '
                           f'{sc.registry_error}')
    # Population by need (R1.0): every unit, except a CORE class known
    # exactly with no parameter to state -- its registry entry lists none,
    # or (R8.10) the registry does not list it and its constructor takes
    # none (_UNLISTED_CORE_PARAMS), or the static table does not know it.
    # A core class the registry does not list is measured against its
    # constructor parameters (_unlisted_core_params): the exporter writes
    # {} for it, a gap in the file. A unit whose class is not known exactly
    # (inferred from its spec keys, family only, unknown) scores 0: its
    # parameter universe is unknown -- measuring it against a
    # key-set-inferred entry let deleting design_simulation_method pick a
    # tighter entry and raise the coverage. An exact CUSTOM class the
    # registry does not list (nor a curated ancestor) also scores 0 (R3.6):
    # the file states none of its parameters (the exporter emits {} for
    # it), a real gap. A facility's id-valued bindings count as in
    # CMP-UOP-06 (_stated_keys: declared ids; R8.11 explicit empties).
    scores, missing, unknown = [], [], 0
    for position, (unit, label) in enumerate(zip(sc.units, sc.unit_labels)):
        name, _family, confidence = sc.unit_classes[position]
        if not (name and confidence == 1.0):
            unknown += 1
            scores.append(0.0)
            missing.append(f'{label}: class not identified at confidence 1, '
                           f'so its design-spec parameter universe is unknown')
            continue
        registry_class = _declared_registry_class(name, sc.registry)
        if registry_class is None and not sc.unit_core[position]:
            unknown += 1
            scores.append(0.0)
            missing.append(f"{label}: custom class '{name}' has no "
                           f"design-spec registry entry, so the file states "
                           f"none of its parameters")
            continue
        if registry_class is None:
            params = list(_unlisted_core_params(sc, position) or ())
        else:
            params = list(_as_dict(_as_dict(sc.registry.get(registry_class))
                                   .get('design_input_spec_params')))
        if not params:
            continue
        explicit = _stated_keys(sc, position)
        excused = _excused_params(sc, position, explicit)
        needed = [p for p in params if p not in excused]
        absent = [p for p in needed if p not in explicit]
        scores.append(1.0 - len(absent) / len(needed) if needed else 1.0)
        missing.extend(f'{label}.design_input_specs.{p}' for p in absent)
    if not scores:
        return (None, 'no units, or every unit is a known core class with '
                'no design-spec parameter to state', [])
    return (_mean(scores), f'{len(scores) - unknown} unit(s) measured against '
            f"their registry class (or a core class's constructor "
            f'parameters), {unknown} with no class known exactly or '
            f'a custom class the registry does not list (score 0); '
            f'{len(missing) - unknown} design-spec param(s) unstated', missing)


def _score_uop_04(sc):  # CMP-UOP-04
    # A stream whose source_unit_id is not stated (missing, not a string,
    # or empty: only the "None" sentinel affirms the boundary) may be an
    # internal or product stream, so it stays in the population (need, not
    # data: deleting an internal stream's source must not drop it) and
    # scores 0 -- its state cannot be attributed to any unit's outlet, so
    # it verifies nothing (else deleting a feed's source would add a fully
    # stated stream).
    # R5.8: a unit outlet whose sink is not stated (dangling) stays in too.
    unsourced = [i for i, s in enumerate(sc.streams)
                 if not _nonempty_str(s.get('source_unit_id'))]
    positions = sorted(set(sc.internal + sc.outlets + unsourced) | {
        i for i in sc.dangling
        if not _is_boundary(sc.streams[i].get('source_unit_id'))})
    if not positions:
        return None, 'no internal or product streams', []
    # R2.4 consistency guard: the outlets of a physical-only unit whose
    # species appear or vanish without declared reactions (or cannot be
    # checked) do not count their composition as stated.
    # R4.10: the outlets of a unit whose total mass balance is evaluable
    # (inlets and outlets all state their mass flow) and fails to close do
    # not count their flow as stated (INVALID == MISSING: nothing tells
    # which stated flow is wrong), nor, then, a stated-zero flow's
    # composition exemption. Families whose chemistry the class fixes
    # (CLASS_IMPLIED_CHEMISTRY_FAMILIES) are exempt: their classes may
    # account mass implicitly -- BioSTEAM's DrumDryer emits the CO2 and H2O
    # of the natural gas it burns without drawing the O2 from any inlet.
    # R7.4: so is a unit declaring a well-formed reaction that is not
    # testably mass-conserving (_may_change_mass: R6.2 fails or cannot be
    # tested) -- such a reaction may create or destroy mass, so the
    # imbalance may be the faithful export R7.4 credits. R5.8: nor does it
    # fire while a stream whose endpoint is not stated may be a missing
    # port that accounts for the gap (_gap_port) -- the balance of an
    # incomplete but consistent document fails whenever one of its ports
    # lost the endpoint attaching it, and deleting that stream's mass flow
    # (or a participant's molar mass, making a conserving reaction
    # untestable) must not then lift the guard. So the guard fires only
    # on a unit whose stated balance fails with every declared reaction
    # conserving mass (or none declared) and no missing port to explain
    # it: an inconsistent document. It never fires on a consistent one,
    # so deleting the data it rests on never lifts it from one; a
    # confirmed conserving reaction whose streams are merely off stays
    # guarded.
    suspect, unbalanced = {}, {}
    for position, unit in enumerate(sc.units):
        consistent, why = _species_consistency(sc, position)
        uid = unit.get('id')
        if not isinstance(uid, str):
            continue
        outs = sc.unit_outlets.get(uid, [])
        if not consistent:
            for i in outs:
                suspect.setdefault(i, (sc.unit_labels[position], why))
        if sc.unit_classes[position][1] in CLASS_IMPLIED_CHEMISTRY_FAMILIES:
            continue
        evaluable, closes, why = _mass_balance(sc, position)
        ins = sc.unit_inlets.get(uid, [])
        if (not evaluable or closes or not ins or not outs
                or _may_change_mass(sc, position)):
            continue
        m_in, m_out = (sum(_stream_mass(sc.streams[i], sc.molar_masses)
                           for i in side)
                       for side in (ins, outs))
        if not _gap_port(sc, m_in, m_out):
            for i in outs:
                unbalanced.setdefault(i, (sc.unit_labels[position], why))
    # R3.8: a unit that streams reference but no unit declares is of
    # unknown family, declares no reactions and states no balance: its
    # outlets' composition stays unverified and, when its mass balance is
    # evaluable and fails with no missing port to explain it (_gap_port,
    # R5.8, as above), their flow too -- deleting a unit's id must not
    # lift either guard from its outlets.
    for pid in sc.phantom_units:
        label = f'units[{pid}]'
        ins, outs = sc.unit_inlets.get(pid, []), sc.unit_outlets.get(pid, [])
        for i in outs:
            suspect.setdefault(i, (label, f'{_NOT_COMPUTABLE}: the unit is '
                                          f'referenced but not declared'))
        masses = [_stream_mass(sc.streams[i], sc.molar_masses)
                  for i in ins + outs]
        if ins and outs and all(m is not None for m in masses):
            m_in, m_out = sum(masses[:len(ins)]), sum(masses[len(ins):])
            if (abs(m_in - m_out) > CLOSURE_RTOL * max(m_in, m_out)
                    and not _gap_port(sc, m_in, m_out)):
                for i in outs:
                    unbalanced.setdefault(i, (label, (
                        f'mass balance does not close (in {m_in:.6g}, out '
                        f'{m_out:.6g})')))
    scores, missing, full = [], [], 0
    unsourced = set(unsourced)
    for i in positions:
        if i in unsourced:
            scores.append(0.0)
            missing.append(f'{sc.stream_labels[i]}.source_unit_id (not '
                           f'stated, so its state is no unit outlet state)')
            continue
        # R7.3: the volume flow is the amount of a single-phase stream's
        # phase stating none.
        stated = _stream_state(sc.streams[i], volumetric=True)
        state = dict(stated)
        if i in unbalanced:
            state = dict(_stream_state(sc.streams[i], empty_exempt=False,
                                       volumetric=True), flow=False)
        if i in suspect:
            state['composition'] = False
        # R6.7: a stream stating no mass or molar total earns the
        # volumetric tier for its flow part (MAX over tiers), unless the
        # composition that tier needs is suspect. (An unbalanced outlet
        # states its totals, so it earns nothing from its volume flow.)
        volumetric = (0.0 if state['flow'] or i in suspect
                      else _volumetric_flow_credit(sc.streams[i]))
        parts = {key: float(ok) for key, ok in state.items()}
        parts['flow'] = max(parts['flow'], volumetric)
        scores.append(sum(parts.values()) / len(parts))
        full += all(state.values())
        for key, ok in state.items():
            if ok:
                continue
            note = _VOLUMETRIC_NOTE if key == 'flow' and volumetric else ''
            if key == 'flow' and i in unbalanced:
                label, why = unbalanced[i]
                note += f' (stated, but unbalanced at {label}: {why})'
            elif key == 'composition' and i in suspect and stated[key]:
                label, why = suspect[i]
                note = (f' (stated, but unverified at {label}: {why})'
                        if why.startswith(_NOT_COMPUTABLE) else
                        f' (stated, but inconsistent at {label}: {why})')
            missing.append(f'{sc.stream_labels[i]}.{_STATE_PATHS[key]}{note}')
    # R5.3: an unverifiable guard (species flows not computable) and a
    # failed one (species appear / vanish) are reported apart.
    unverified = sum(1 for _label, why in suspect.values()
                     if why.startswith(_NOT_COMPUTABLE))
    inconsistent = len(suspect) - unverified
    notes = [f'{inconsistent} outlet(s) of physical-only units fail the '
             f'species-consistency guard (species appear or vanish)'
             if inconsistent else '',
             f'{unverified} outlet(s) of physical-only units left unverified '
             f'by the species-consistency guard ({_NOT_COMPUTABLE})'
             if unverified else '',
             f'{len(unbalanced)} outlet(s) of units whose mass balance does '
             f'not close earn no flow credit' if unbalanced else '',
             f'{len(unsourced)} stream(s) without a stated source score 0'
             if unsourced else '']
    return (_mean(scores), f'{full} of {len(positions)} internal/product '
            f'stream(s) fully stated' + ''.join(f'; {n}' for n in notes if n),
            missing)


def _uop_05_unit(sc, position, facility):
    """(credit, missing) of one CMP-UOP-05 unit (R5.2): 1 when every
    applying essential requirement (CMP-UOP-02 rules; a facility's
    _FACILITY_RULES) is met -- n of its keys -- by counted explicit keys
    that are dimensionless (_spec_dimension None) or stated in a unit
    recognised for their dimension (inline, or the quantity_units_global
    alias); else `base`: DERIVED_CREDIT when the class is identified at
    confidence 1 with a known family (its conventions imply BioSTEAM's
    native units), else 0. That is the MIN over the requirements of a
    credit in {base, 1}. The requirement set is fixed by NEED, so deleting
    a key or an alias never raises it (a stated key without a unit scores
    what its absence does), and a requirement that deleted evidence
    un-waives can only lower the minimum. A unit none of whose
    requirements applies needs no unit (1); one whose requirements are
    unknown (unknown family or facility kind) scores `base`. A waived
    requirement counts as met (credit 1, R5.9, as in CMP-UOP-02), the
    identity of the minimum, so skipping it is the same thing.

    As in CMP-UOP-02, a family claim without an exact class takes the MAX
    over its legitimate rule-set variants (R7.2, _rule_variants), and a
    hosting tank not ruled out the lower of that and the best reactor
    variant (R7.1, _chemistry_rules): max and min of monotone credits stay
    monotone."""
    label = sc.unit_labels[position]
    name, family, confidence = sc.unit_classes[position]
    base = (DERIVED_CREDIT if name and confidence == 1.0 and family is not None
            else 0.0)
    convention = ' (class conventions imply it)' if base else ''
    # R8.4: a facility claim declaring chemistry its family does not
    # account for is measured over the reactor variants too.
    chemistry = _chemistry_rules(sc, position)
    if facility:
        rules = _FACILITY_RULES.get(_facility_kind(sc, position))
        if rules is None:
            results = [(base, [f'{label}.design_input_specs: specification '
                               f'units unknown (its needs are unknown)'
                               f'{convention}'])]
        else:
            results = [_uop_05_credit(sc, position, rules, family, base,
                                      True, _facility_explicit(sc, position)
                                      if rules else set())]
        rule_sets_list = (chemistry,)
    else:
        # R7.2: the best legitimate rule-set variant of a claim without an
        # exact class; R7.1: a hosting tank not ruled out is measured over
        # the reactor variants too (the lower of the two).
        variants = _rule_variants(sc, position)
        if not variants:
            return base, [f'{label}.design_input_specs: specification units '
                          f'unknown (its needs are unknown){convention}']
        results, rule_sets_list = [], (variants, chemistry)
    for rule_sets in rule_sets_list:
        if rule_sets:
            results.append(max(
                (_uop_05_credit(sc, position, rules, f, base, False,
                                _explicit_keys(sc.units[position], f,
                                               sc.spec_units[position]))
                 for f, rules in rule_sets),
                key=lambda result: result[0]))
    return min(results, key=lambda result: result[0])


def _uop_05_credit(sc, position, rules, family, base, facility, explicit):
    """(credit, missing) of unit `position` over one rule set `rules`
    (_uop_05_unit): the MIN over its requirements of 1 (met by `explicit`
    keys that are dimensionless or stated in a unit recognised for their
    dimension in `family`) or `base`; a waived requirement counts as met
    (1, R5.9; facility settings are never waived); 1 for no
    requirements."""
    label = sc.unit_labels[position]
    convention = ' (class conventions imply it)' if base else ''
    credits, missing = [], []
    for req in rules:
        if not facility and not _requirement_applies(req, sc, position):
            credits.append(1.0)  # waived: met (R5.9)
            continue
        known, unitless = 0, []
        for key in sorted(_counted_keys(req, sc, position, explicit)):
            if _spec_dimension(key, family) is None or _spec_unit_recognised(
                    key, sc.spec_units[position].get(key), family):
                known += 1
            else:
                unitless.append(key)
        best = 1.0 if known >= req.n else base
        credits.append(best)
        if best < 1.0:
            what = (f"{', '.join(unitless)} stated without a recognised "
                    f"quantity unit" if unitless else
                    f"one of {'|'.join(req.keys)} unstated")
            missing.append(f'{label}.design_input_specs: {what}{convention}')
    return min(credits, default=1.0), missing


def _score_uop_05(sc):  # CMP-UOP-05
    # Population: the units that need operating specifications (CMP-UOP-02)
    # or facility settings (CMP-UOP-06), whose numeric settings (T_min_app,
    # ...) need units too (R5.2).
    facilities = set(_facility_population(sc))
    scores, missing = [], []
    for position in sorted(set(_spec_population(sc)) | facilities):
        score, gaps = _uop_05_unit(sc, position, position in facilities)
        scores.append(score)
        missing.extend(gaps)
    if not scores:
        return None, 'no units that need operating specifications', []
    return (_mean(scores), f'{scores.count(1.0)} of {len(scores)} unit(s) '
            f'state every dimensional specification they need with a '
            f'recognised quantity unit (quantity_units_global aliases)',
            missing)


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

# R8.9: a boiler's efficiency basis ('LHV' or 'HHV': it picks the fuel's
# heating value the efficiency applies to, so it sets the steam raised, the
# turbogenerator's electricity and the boiler's cost), its fuel source and
# its side steam (a declared stream id, or null: none; R8.11).
_EFFICIENCY_BASIS = _Req(1, ('boiler_efficiency_basis',), {})
_FUEL_SOURCE = _Req(1, ('fuel_source',), {})
_SIDE_STEAM = _Req(1, ('side_steam',), {})

# Per-class requirements (R2.5), checked against the biosteam source
# (facilities/_boiler_turbogenerator.py, _process_water_center.py,
# _cooling_tower.py, _chilled_water_package.py, _refrigeration_package.py,
# wastewater/high_rate/system.py's CHP) and hensmith's
# HeatExchangerNetwork. Every one is a constructor argument with a default,
# so it must be stated (R2.8). Stream- and unit-id lists count only when
# every id is declared, and non-empty unless an empty list or null has a
# BioSTEAM meaning for that binding (R8.11, _EMPTY_BINDINGS).
_FACILITY_RULES = {
    'BoilerTurbogenerator': (
        _Req(1, ('boiler_efficiency',), {}),
        _Req(1, ('turbogenerator_efficiency',), {}),
        _Req(1, ('satisfy_system_electricity_demand',), {}),
        _FUEL_SOURCE, _SERVED_AGENT, _EFFICIENCY_BASIS, _SIDE_STEAM),
    # R8.9: a plain Boiler takes the same agent, fuel source, efficiency
    # basis and side steam as a BoilerTurbogenerator.
    'Boiler': (_Req(1, ('boiler_efficiency',), {}), _FUEL_SOURCE,
               _SERVED_AGENT, _EFFICIENCY_BASIS, _SIDE_STEAM),
    # R8.10: CHP(ID, ins, outs, eff=0.3375, unit_CAPEX=1225): the combined
    # combustion-and-power efficiency sets the electricity it generates
    # (unit_CAPEX, a cost, is CMP-UOP-03's).
    'CHP': (_Req(1, ('eff',), {}),),
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
    ('BoilerTurbogenerator', r'turbo ?generator|combined heat (?:and|&) '
                             r'power|\bchp\b|cogenerat\w*'),  # R8.2
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

# R8.11: bindings whose BioSTEAM semantics give an explicit empty list or
# null a meaning, by facility kind -> the empty values that count as
# stated: a process-water center's stream lists ([] = none; null = the
# facilities' and system's streams, discovered at simulation), a boiler's
# side_steam (null = none), a heat-exchanger network's units (null or [] =
# every unit of the system). Every other binding counts only when
# non-empty (an empty placeholder binds nothing).
_EMPTY_BINDINGS = {
    'ProcessWaterCenter': {'process_water_streams': (None, []),
                           'makeup_water_streams': (None, [])},
    'BoilerTurbogenerator': {'side_steam': (None,)},
    'Boiler': {'side_steam': (None,)},
    'HeatExchangerNetwork': {'units': (None, [])},
}


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
        kind = _facility_label_kind(text)
        if kind is not None:
            return kind
    return {'boiler': 'Boiler', 'boiler_turbogenerator': 'BoilerTurbogenerator',
            'heat_exchanger_network': 'HeatExchangerNetwork'}.get(family)


def _facility_population(sc):
    """Positions of the CMP-UOP-06 population (R2.5): facility-family units
    (FACILITY_FAMILIES) except cost-only facilities. R2.5: a unit whose
    unit_type names a facility stays in it when its ports or streams
    contradict that claim (family unknown; it then scores 0,
    _facility_unit_score): the label is schema-required, so the facility
    need it states never leaves with the data that shows the
    contradiction (a declared reaction's equation deleted, say)."""
    return [p for p, (_n, family, _c) in enumerate(sc.unit_classes)
            if (family in FACILITY_FAMILIES
                or (family is None and _keyword_family(
                    sc.units[p].get('unit_type')) in FACILITY_FAMILIES))
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


def _empty_binding(kind, key, value):
    """R8.11: True when `value` is an explicit empty list or null that
    _EMPTY_BINDINGS gives a meaning for binding `key` of a facility of
    `kind`."""
    allowed = _EMPTY_BINDINGS.get(kind, {}).get(key, ())
    return ((value is None and None in allowed)
            or (isinstance(value, list) and not value and [] in allowed))


def _facility_explicit(sc, position):
    """design_input_specs keys of unit `position` stated plausibly for
    CMP-UOP-06: _explicit_keys, with id-valued keys checked against the
    document -- process/makeup water stream lists and a boiler's side_steam
    against the declared streams, `units` against the declared units,
    `agent` (a heat-agent id) and `other_agents` (a list of them) against
    the declared heat agents -- plus, R8.11, a binding stated as the
    explicit empty list or null its facility kind gives a meaning
    (_empty_binding)."""
    unit = sc.units[position]
    specs = _as_dict(unit.get('design_input_specs'))
    kind = _facility_kind(sc, position)
    stream_ids = {s['id'] for s in sc.streams if _nonempty_str(s.get('id'))}
    agents = _heat_agent_ids(sc)
    explicit = {key for key, value in specs.items()
                if _empty_binding(kind, key, value)}
    for key in _explicit_keys(unit, sc.unit_classes[position][1],
                              sc.spec_units[position]):
        value = specs[key]
        if key in _STREAM_LIST_KEYS:
            ok = _valid_id_list(value, stream_ids)
        elif key == 'side_steam':
            ok = isinstance(value, str) and value in stream_ids
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


def _stated_keys(sc, position):
    """design_input_specs keys of unit `position` that count as stated in
    CMP-UOP-03: a facility-family unit's _facility_explicit (bindings to
    declared ids, R8.11 explicit empties), as CMP-UOP-05/06 count them;
    else _explicit_keys."""
    if sc.unit_classes[position][1] in FACILITY_FAMILIES:
        return _facility_explicit(sc, position)
    return _explicit_keys(sc.units[position], sc.unit_classes[position][1],
                          sc.spec_units[position])


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
    if sc.unit_classes[position][1] is None:
        return 0.0, [f'{label}: facility claim contradicted by its own '
                     f'ports, streams or duties, so no facility bindings '
                     f'or settings can be read']
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
    scores, missing = [], []
    for position in _facility_population(sc):
        score, gaps = _facility_unit_score(sc, position)
        if _reactor_candidate(sc, position):
            # R8.4: a facility claim on a unit that declares reactions
            # its facility chemistry does not account for
            # (_declared_chemistry: a 'Cooling tower' declaring a
            # fermentation) states no facility the flowsheet can have: it
            # scores 0 here, so the facility need it adds (a criterion the
            # honest reactor label leaves not applicable) can never lift
            # the mean. Monotone: deleting a field of a declared reaction
            # keeps the declaration (_declared_chemistry counts reaction
            # objects; one no longer well-formed is not accounted for).
            score = 0.0
            gaps = [f'{sc.unit_labels[position]}: declares reactions its '
                    f'facility chemistry does not account for, so its '
                    f'facility claim states no bindings (scored 0; '
                    f'CMP-UOP-02 holds it to reactor rules)']
        scores.append(score)
        missing.extend(gaps)
    if not scores:
        return None, 'no facilities with bindings or settings', []
    return (_mean(scores), f'{scores.count(1.0)} of {len(scores)} facility '
            f'unit(s) state every binding and setting', missing)

#%% Criteria -- reactions

def _species_changed(flow_in, flow_out, throughput):
    """R8.6: True when a species' flow changes from `flow_in` to
    `flow_out` across a unit (or the flowsheet) by more than BOTH
    CLOSURE_RTOL of its own flow (the larger of the two) AND
    SPECIES_THROUGHPUT_RTOL of `throughput` (the inlet total in the
    species' own flow unit, _species_throughputs), and more than
    ZERO_FLOW. A printed table's independent rounding of each stream (a
    trace species at wt% to 2 decimals) stays below the throughput share;
    material conversion exceeds both."""
    return abs(flow_out - flow_in) > max(
        ZERO_FLOW, CLOSURE_RTOL * max(flow_in, flow_out),
        SPECIES_THROUGHPUT_RTOL * throughput)


def _species_throughputs(basis, flows_in, flows_out, molar_masses):
    """R8.6: {species: the throughput its change is judged against
    (_species_changed), in its own flow unit on `basis`} for species flows
    `flows_in` / `flows_out` compared on `basis` ('molar' / 'mass',
    _side_species / _basis_flows) -- one MASS footing whatever the basis:
    on the mass basis the inlet mass total; on the molar basis, when every
    species' molar mass resolves (`molar_masses`, sc.molar_masses), the
    inlet mass total (sum of n_i x MW_i) divided by the species' own molar
    mass. So a table stating both bases reaches the same verdict on
    either, and deleting its mol fractions (or its mass fractions) leaves
    the verdict unchanged -- judging the share on the basis compared let
    a light species appearing (H2: 5e-3 of the molar throughput, 5e-4 of
    the mass throughput) count only while the molar basis was stated.
    When a molar mass does not resolve, 0 for every species: only the
    stricter CLOSURE_RTOL-of-own-flow rule applies, so deleting a molar
    mass can only add evidence (_species_change_test first looks for a
    mass footing on the same ports)."""
    names = set(flows_in) | set(flows_out)
    if basis == 'mass':
        return dict.fromkeys(names, sum(flows_in.values()))
    mws = {name: _as_dict(molar_masses).get(name) for name in names}
    if not all(_positive_mw(mw) for mw in mws.values()):
        return dict.fromkeys(names, 0.0)
    total = sum(flow * mws[name] for name, flow in flows_in.items())
    return {name: total / mws[name] for name in names}


def _species_change_test(sc, basis, flows_in, flows_out, ins, outs):
    """R8.6: the test `changed(name)` -> True when species `name` changes
    (_species_changed) between species flows `flows_in` / `flows_out` on
    `basis`, summed over the streams `ins` / `outs` (the ports compared,
    or the flowsheet boundary). Its throughput share has one MASS footing
    (_species_throughputs); on the molar basis with a molar mass that
    does not resolve, the same ports' MASS species flows give that footing
    when every one of them states them: the change is then judged on the
    mass basis against the mass inlet total (a table stating both bases
    lists a chemical by name only). Only without any footing does the
    stricter CLOSURE_RTOL-of-own-flow rule apply alone. Deleting a molar
    mass therefore leaves the verdict on the mass footing, and deleting
    mass data only moves it to the stricter rule: either can only add
    evidence."""
    names = set(flows_in) | set(flows_out)
    if basis == 'molar' and not all(
            _positive_mw(_as_dict(sc.molar_masses).get(n)) for n in names):
        mass = [_species_flows(stream, 'mass')
                for stream in list(ins) + list(outs)]
        if all(flow is not None for flow in mass):
            m_in, m_out = ({}, {})
            for k, flow in enumerate(mass):
                side = m_in if k < len(ins) else m_out
                for name, value in flow.items():
                    side[name] = side.get(name, 0.0) + value
            total = sum(m_in.values())
            return lambda name: _species_changed(
                m_in.get(name, 0.0), m_out.get(name, 0.0), total)
    throughput = _species_throughputs(basis, flows_in, flows_out,
                                      sc.molar_masses)
    return lambda name: _species_changed(
        flows_in.get(name, 0.0), flows_out.get(name, 0.0),
        throughput.get(name, 0.0))


def _positive_mw(value):
    """True when `value` is a usable molar mass: a positive number (not a
    bool)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value > 0.0)


def _species_change(sc, unit_id):
    """Species that appear in (or vanish from) a unit's outlets relative to
    its inlets, as evidence strings ('CO2 appears'): a species with no
    inlet (outlet) flow whose outlet (inlet) flow is a change
    (_species_changed, R8.6: beyond SPECIES_THROUGHPUT_RTOL of the unit's
    inlet total); [] when the unit lacks inlets or outlets or nothing
    changes; None when the connected streams' species flows are not
    computable on one basis -- molar, else mass (_side_species, R5.3) --
    so a change cannot be ruled out."""
    if not isinstance(unit_id, str):
        return []
    ins, outs = sc.unit_inlets.get(unit_id, []), sc.unit_outlets.get(unit_id, [])
    if not ins or not outs:
        return []
    found = _side_species(sc, unit_id)
    if found is None:
        return None
    basis, flows_in, flows_out = found
    changed = _species_change_test(
        sc, basis, flows_in, flows_out, [sc.streams[i] for i in ins],
        [sc.streams[i] for i in outs])
    appear = [n for n in flows_out
              if flows_in.get(n, 0.0) <= ZERO_FLOW and changed(n)]
    vanish = [n for n in flows_in
              if flows_out.get(n, 0.0) <= ZERO_FLOW and changed(n)]
    return ([f'{n} appears' for n in sorted(appear)]
            + [f'{n} vanishes' for n in sorted(vanish)])


def _species_conversion(sc, unit_id):
    """R6.1: ([produced], [consumed]) species across unit `unit_id` -- those
    whose total flow out exceeds / falls short of the flow in by more than
    CLOSURE_RTOL of the larger of the two and more than
    SPECIES_THROUGHPUT_RTOL of the unit's inlet total (_species_changed,
    R8.6), on one basis (_side_species: molar, else mass, R5.3); a
    superset of _species_change's appearing / vanishing species. ([], [])
    when the unit lacks inlets or outlets or every species balances; None
    when the connected streams' species flows are not computable."""
    if not isinstance(unit_id, str):
        return [], []
    ins, outs = sc.unit_inlets.get(unit_id), sc.unit_outlets.get(unit_id)
    if not ins or not outs:
        return [], []
    found = _side_species(sc, unit_id)
    if found is None:
        return None
    basis, flows_in, flows_out = found
    changed = _species_change_test(
        sc, basis, flows_in, flows_out, [sc.streams[i] for i in ins],
        [sc.streams[i] for i in outs])
    produced, consumed = [], []
    for name in sorted(set(flows_in) | set(flows_out)):
        a, b = flows_in.get(name, 0.0), flows_out.get(name, 0.0)
        if changed(name):
            (produced if b > a else consumed).append(name)
    return produced, consumed


def _no_conversion(sc, position):
    """R6.1(b) / R7.1: True when unit `position`'s streams show
    affirmatively that nothing converts across it -- a hosting unit ruled
    out per unit (_ruled_out): its total mass closes (_mass_closure, no
    dangling stream possibly a missing port) and every species' flow is
    computable and none changes (_species_conversion finds none: R8.6,
    beyond CLOSURE_RTOL of its own flow and SPECIES_THROUGHPUT_RTOL of the
    unit's inlet total). Deleting any of that evidence can only make it
    False."""
    if not _mass_closure(sc, position)[0]:
        return False
    return _species_conversion(sc, sc.units[position].get('id')) == ([], [])


def _species_guarded(sc, position):
    """True when unit `position` falls under the species-appearance
    consistency guard (R2.4): a physical-only family (PHYSICAL_FAMILIES --
    a splitter unless it names a custom, non-core class, whose code may
    transform species) that declares no well-formed reaction. A splitter of
    unknown class is guarded: only an identified custom class waives it.
    R4.13: a unit of unknown family could be physical-only, so it is
    guarded like a splitter (only an identified custom class waives it) --
    deleting a flash's unit_type must not lift the guard."""
    name, family, _confidence = sc.unit_classes[position]
    if sc.unit_reacts[position] or (family is not None
                                    and family not in PHYSICAL_FAMILIES):
        return False
    return not (family in ('splitter', None) and name
                and not sc.unit_core[position])


def _species_consistency(sc, position):
    """(consistent, reason) of the R2.4 consistency guard for unit
    `position`, cached per context: (True, '') unless the unit is guarded
    (_species_guarded); a guarded unit is consistent only when every
    connected stream's species flows are computable on one basis (molar,
    else mass: R5.3) and no species appears or vanishes. An uncomputable
    port leaves consistency unverified (False; the reason then starts with
    _NOT_COMPUTABLE, telling it from an inconsistency), so deleting a
    stream's composition never lifts the guard; so does a dangling stream
    (R5.8) that may be a missing port of the unit (its total mass closure,
    _mass_closure, does not rule that out), so deleting a port's endpoint
    never lifts it either."""
    cache = sc.consistency
    if position in cache:
        return cache[position]
    result = True, ''
    if _species_guarded(sc, position):
        change = _species_change(sc, sc.units[position].get('id'))
        if change is None:
            result = False, (f'{_NOT_COMPUTABLE} (molar or mass basis) for '
                             f'every connected stream, so no undeclared '
                             f'reaction can be ruled out')
        elif (not change and sc.dangling
              and not _mass_closure(sc, position)[0]):
            # R5.8: a stream whose endpoint is not stated may be a missing
            # port of this unit (its closure cannot rule that out), so its
            # species balance cannot be verified.
            result = False, (f'{_NOT_COMPUTABLE} over every port: a stream '
                             f'without a stated endpoint may be a missing '
                             f'port, so no undeclared reaction can be ruled '
                             f'out')
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
    class-implied-chemistry equilibrium reactors); 'family unknown' when
    the unit's family is unknown (R4.13: it could be a reactor, so it needs
    reactions unless affirmative evidence says otherwise -- deleting a
    reactor's only family evidence must not shed the need); else None.
    Declared reactions and species appearance no longer make a unit
    reactive: a population defined by data let deleting that data shrink
    it.

    R7.1: a HOSTING claim (_hosting_claim: a tank, column or extractor
    without an exact class's code, or a core class taking an optional
    reaction set) never enters individually, evident conversion or not:
    one flowsheet-level pseudo-member stands for all of them
    (_unexplained_hosts), so an evident and an unverifiable hosting unit
    weigh the same and moving between them changes nothing.

    R8.4: a claim held to reactor rules for the reactions it declares
    (_declared_chemistry: a label of any non-reactor family, or a hosting
    tank claim, listing reaction objects its family's own chemistry does
    not account for) is reactive -- as its unknown family would be once
    its label is deleted -- and scores by whether one is well-formed; a
    hosting claim among them is then no pseudo-member host."""
    family = sc.unit_classes[position][1]
    if family is None:
        return 'family unknown'
    if family in REACTOR_FAMILIES - CLASS_IMPLIED_CHEMISTRY_FAMILIES:
        return 'reactor family'
    if _declared_chemistry(sc, position):
        return 'declares reactions, so held to reactor rules'
    return None


def _flowsheet_explained(sc):
    """R7.1 flowsheet-level evidence, as (explained, reason) -- reason says
    why not ('' when explained); cached per context. Explained when no
    stream lacks a stated endpoint (R5.8: a dangling stream may be a
    missing boundary stream), the flowsheet has a non-empty feed and a
    non-empty product, every boundary stream (feeds, products, and
    streams bounded at both ends, which cancel out) has species flows
    computable on one basis (molar, else mass: _basis_flows), every feed
    and product a known total mass (_stream_mass, R8.5: a molar-basis
    stream's too) with the overall mass balance closing within
    CLOSURE_RTOL, and every species produced or consumed across the
    flowsheet (_species_changed, R8.6: beyond CLOSURE_RTOL of its own flow
    and SPECIES_THROUGHPUT_RTOL of the boundary inlet total, as
    _species_conversion judges one unit) takes part in a declared
    well-formed reaction on some unit (sc.well_formed). Deleting data only
    removes evidence, so it can only turn False."""
    if sc.flowsheet_evidence is None:
        sc.flowsheet_evidence = _flowsheet_evidence(sc)
    return sc.flowsheet_evidence


def _flowsheet_evidence(sc):
    """Uncached _flowsheet_explained."""
    if sc.dangling:
        return False, (f'{sc.stream_labels[sc.dangling[0]]} states no '
                       f'endpoint, so the flowsheet boundary is unknown')
    ins, outs = sc.feeds, sc.outlets
    if not all(any(not _is_empty_stream(sc.streams[i]) for i in side)
               for side in (ins, outs)):
        return False, 'no non-empty feed and product stream'
    masses = [_stream_mass(sc.streams[i], sc.molar_masses)
              for i in ins + outs]
    if any(m is None for m in masses):
        return False, _massless(
            sc, [i for i, m in zip(ins + outs, masses) if m is None],
            'feed or product')
    m_in, m_out = sum(masses[:len(ins)]), sum(masses[len(ins):])
    if abs(m_in - m_out) > CLOSURE_RTOL * max(m_in, m_out):
        return False, (f'the overall mass balance does not close (in '
                       f'{m_in:.6g}, out {m_out:.6g})')
    found = _basis_flows([sc.streams[i] for i in ins + outs + sc.isolated])
    if found is None:
        return False, (f'{_NOT_COMPUTABLE} (molar or mass basis) for every '
                       f'boundary stream')
    basis, flows = found
    sides = []
    for part in (flows[:len(ins)], flows[len(ins):len(ins) + len(outs)]):
        acc = {}
        for species in part:
            for name, value in species.items():
                acc[name] = acc.get(name, 0.0) + value
        sides.append(acc)
    flows_in, flows_out = sides
    # R8.6: the boundary inlet total, on one mass footing.
    changed = _species_change_test(
        sc, basis, flows_in, flows_out, [sc.streams[i] for i in ins],
        [sc.streams[i] for i in outs])
    declared = set()
    for _i, _j, reaction in sc.reactions:
        if sc.well_formed(reaction):
            for side in sc.reaction_sides(reaction):
                declared.update(side)
    unexplained = []
    for name in sorted(set(flows_in) | set(flows_out)):
        a, b = flows_in.get(name, 0.0), flows_out.get(name, 0.0)
        if changed(name) and name not in declared:
            unexplained.append(f"{name} {'produced' if b > a else 'consumed'}")
    if unexplained:
        return False, (f"{', '.join(unexplained[:3])} across the flowsheet "
                       f"without a declared well-formed reaction")
    return True, ''


def _unexplained_hosts(sc):
    """R7.1: positions of the HOSTING units (_hosting_claim) that declare
    no well-formed reaction (R8.4: nor reactions they are held to reactor
    rules for, _declared_chemistry) and are not ruled out per unit
    (_no_conversion), when flowsheet-level evidence is absent
    (_flowsheet_explained); else []. Cached per context. When non-empty,
    ONE pseudo-member ('unexplained conversion possible among <units>')
    joins the CMP-RXN-01..04 populations scoring 0 (_hidden_chemistry):
    deleting data only removes evidence, so the pseudo-member can only
    appear, and an evident and an unverifiable hosting unit are covered by
    the same one."""
    if sc.hidden_hosts is None:
        hosts = [p for p in range(len(sc.units))
                 if _hosting_claim(sc, p) and not sc.unit_reacts[p]
                 and not _declared_chemistry(sc, p)
                 and not _no_conversion(sc, p)]
        sc.hidden_hosts = hosts if hosts and not (
            _flowsheet_explained(sc)[0]) else []
    return sc.hidden_hosts


def _hidden_chemistry(sc, what):
    """R7.1: the missing locator of the CMP-RXN-01..04 pseudo-member
    (_unexplained_hosts), saying `what` it lacks, or None when there is
    none."""
    hosts = _unexplained_hosts(sc)
    if not hosts:
        return None
    return (f"flowsheet: unexplained conversion possible among "
            f"{', '.join(sc.unit_labels[p] for p in hosts)} (tank, column or "
            f"extractor claims without well-formed reactions whose streams "
            f"do not rule conversion out, and no flowsheet-level evidence: "
            f"{_flowsheet_explained(sc)[1]}), so {what}")


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
    hidden = _hidden_chemistry(sc, 'no reaction set is declared for it')
    if not reactive and not hidden:
        return None, 'no reactive units', []
    # A unit carries reactions only with >= 1 well-formed one (R2.0).
    scores, missing = [], []
    for position, reason in reactive:
        carries = sc.unit_reacts[position]
        scores.append(1.0 if carries else 0.0)
        if not carries:
            missing.append(f'{sc.unit_labels[position]}.reactions: none '
                           f'well-formed declared ({reason})')
    if hidden:  # R7.1: one pseudo-member for the hosting claims, scoring 0
        scores.append(0.0)
        missing.append(hidden)
    ok = scores.count(1.0)
    return (_mean(scores), f'{ok} of {len(scores)} reactive unit(s) carry '
            f'reactions' + _HIDDEN_NOTE * bool(hidden), missing)


# R7.1: message suffix of a CMP-RXN-01..04 population holding the
# pseudo-member.
_HIDDEN_NOTE = ('; one of them is the flowsheet-level pseudo-member for '
                'hosting claims that may hide conversion')


_MALFORMED_REACTION = (': not well-formed (its equation|stoichiometry names '
                       'no consumed and produced species)')
# R7.4: the model-quality note of a well-formed reaction failing the R6.2
# closure test that its unit's stream data do not confirm.
_NONCONSERVING_REACTION = (': reaction does not conserve mass as written (its '
                           'coefficients close on neither a mol nor a wt '
                           f'basis within {RXN_CLOSURE_RTOL:.0%}: R6.2) and '
                           "its unit's stream data do not confirm it, so its "
                           'equation|stoichiometry earns '
                           f'{NONCONSERVING_CREDIT:g} (R7.4)')
# R7.4 / R2.13: the note of a well-formed reaction whose closure cannot be
# tested and that its unit's stream data do not confirm.
_UNTESTED_REACTION = (': mass conservation not testable (a participant has '
                      'no molar mass or formula, or its chemical no id) and '
                      "its unit's stream data do not confirm the reaction, "
                      'so its equation|stoichiometry earns '
                      f'{NONCONSERVING_CREDIT:g} (R7.4)')


def _malformed_note(sc, reaction):
    """'' when the reaction is well-formed (sc.well_formed), else the
    locator suffix saying why: it names no consumed and produced species
    (_MALFORMED_REACTION). R7.4: a reaction that does not conserve mass
    is no longer malformed (see _formed_credit)."""
    return '' if sc.well_formed(reaction) else _MALFORMED_REACTION


def _proportional_change(change, coeffs):
    """R7.4: True when the species-flow changes `change` ({species: out -
    in}) equal extent x `coeffs` ({chemical id: signed coefficient}) for
    one positive extent within RXN_CONFIRM_RTOL: every participant's ratio
    change / coefficient is positive and within RXN_CONFIRM_RTOL of the
    largest, and every other species' change within RXN_CONFIRM_RTOL of
    the largest participant change."""
    ratios = [change.get(name, 0.0) / nu for name, nu in coeffs.items()
              if nu]
    if not ratios or min(ratios) <= 0.0:
        return False
    if max(ratios) - min(ratios) > RXN_CONFIRM_RTOL * max(ratios):
        return False
    scale = max(abs(change.get(name, 0.0)) for name, nu in coeffs.items()
                if nu)
    return all(abs(value) <= RXN_CONFIRM_RTOL * scale
               for name, value in change.items() if not coeffs.get(name))


def _reaction_confirmed(sc, position, reaction):
    """R7.4: True when unit `position` carries exactly this one reaction
    object and its own streams' species-flow changes (outlets minus
    inlets) are proportional to the reaction's coefficients
    (_proportional_change, over its _coefficient_sources) on a basis on
    which every port's species flows are computable (_species_flows) --
    as written (molar flows for a mol-basis reaction, mass flows for a
    wt-basis one) or, R8.7, converted with the resolved molar masses
    (_basis_coefficients: nu x MW on mass flows, nu / MW on molar ones);
    a species outside the reaction changing within the R8.6 tolerance
    (_species_changed) counts as unchanged -- a faithful export of a model
    whose reaction does not conserve mass. No stream may
    lack a stated endpoint (R5.8: it may be a missing port, and such a
    unit's mass closure, which the reaction breaks, cannot rule that out).
    Deleting stream data or an endpoint only removes the confirmation."""
    unit = sc.units[position]
    uid = unit.get('id')
    reactions = _dicts(unit.get('reactions'))
    if (not _nonempty_str(uid) or sc.dangling or len(reactions) != 1
            or reactions[0] is not reaction):
        return False
    ins, outs = sc.unit_inlets.get(uid, []), sc.unit_outlets.get(uid, [])
    if not ins or not outs:
        return False
    sources = _coefficient_sources(sc, reaction)
    for basis in _BASIS_KEYS:
        flows = [_species_flows(sc.streams[i], basis) for i in ins + outs]
        if any(f is None for f in flows):
            continue
        flows_in, flows_out = {}, {}
        for k, species in enumerate(flows):
            side = flows_in if k < len(ins) else flows_out
            for name, value in species.items():
                side[name] = side.get(name, 0.0) + value
        changed = _species_change_test(
            sc, basis, flows_in, flows_out, [sc.streams[i] for i in ins],
            [sc.streams[i] for i in outs])
        for coeffs in sources:
            for scaled in _basis_coefficients(coeffs, basis,
                                              sc.molar_masses):
                # R8.6: a species outside the reaction whose flow does not
                # change beyond the species tolerance (a rounded table's
                # trace) is no change.
                change = {name: flows_out.get(name, 0.0)
                          - flows_in.get(name, 0.0)
                          for name in set(flows_in) | set(flows_out)
                          if scaled.get(name) or changed(name)}
                if _proportional_change(change, scaled):
                    return True
    return False


def _basis_coefficients(coeffs, basis, molar_masses):
    """R7.4 / R8.7: the coefficient sets a reaction's species-flow changes
    on `basis` ('molar' / 'mass' species flows) may be proportional to:
    its coefficients as written (a mol-basis reaction on molar flows, a
    wt-basis one on mass flows) and, when every participant's molar mass
    resolves (`molar_masses`, sc.molar_masses), their converse -- nu_i x
    MW_i on mass flows (a mol-basis reaction), nu_i / MW_i on molar flows
    (a wt-basis one). Deleting a molar mass only drops the converse."""
    sets = [coeffs]
    mws = {name: _as_dict(molar_masses).get(name) for name in coeffs}
    if coeffs and all(mws.values()):
        sets.append({name: (nu * mws[name] if basis == 'mass'
                            else nu / mws[name])
                     for name, nu in coeffs.items()})
    return sets


def _may_change_mass(sc, position):
    """R4.10 / R7.4: True when unit `position` declares a well-formed
    reaction (sc.well_formed) that is not testably mass-conserving
    (sc.conserves not True: R6.2 fails, or cannot be tested) -- declared
    chemistry that may create or destroy mass, so a failing total mass
    balance of the unit may be the faithful export R7.4 credits rather
    than an inconsistency. Read from the declared chemistry alone, never
    from the stream data the balance is checked on: a unit whose balance
    fails while every reaction it declares conserves mass, and no stream
    lacking an endpoint can be its missing port (_gap_port, R5.8), is
    inconsistent (a confirmed conserving reaction whose streams are
    merely off stays guarded), and an exemption resting on stream
    evidence would let deleting that evidence's other half (an endpoint,
    a feed's mass flow) lift the guard from a consistent unit whose
    reaction the streams could not confirm. Deleting a participant's
    molar mass or chemical id can turn the exemption on (conserves ->
    None), but only where the guard does not fire on a consistent
    document anyway; deleting a stoichiometry entry can turn it off
    (conserves -> True), which can only let the guard fire."""
    return any(sc.well_formed(r) and sc.conserves(r) is not True
               for r in _dicts(sc.units[position].get('reactions')))


def _formed_credit(sc, position, reaction):
    """R7.4: credit of a well-formed reaction's equation|stoichiometry
    component in CMP-RXN-02 -- 1 on affirmative evidence (R2.13) only:
    it conserves mass (sc.conserves True: R6.2, tested) or, on unit
    `position` (None: not tested), its stream data confirm it
    (_reaction_confirmed: a faithful re-import reproduces it); else
    NONCONSERVING_CREDIT -- for a reaction that does not conserve mass as
    written, which cannot be told apart from a transcription error, and
    for one whose closure cannot be tested (a participant without a molar
    mass), so deleting a molar mass or a chemical id never lifts it.
    Deleting a stoichiometry entry can lift it -- one whose term held the
    balance off, or left the closure untestable -- but that states a
    different reaction, outside the deletion guarantee as deleting a list
    element is (_conserves_mass). A reaction that does not conserve mass
    with the document's own molar masses contradicts its chemicals, so
    that document is internally inconsistent and outside the guarantee,
    while the reaction itself is still credited NONCONSERVING_CREDIT as
    the possibly faithful export of a model that writes it so."""
    if sc.conserves(reaction) or (position is not None and
                                  _reaction_confirmed(sc, position, reaction)):
        return 1.0
    return NONCONSERVING_CREDIT


def _reaction_completeness(sc, reaction, where, position=None):
    """(score, missing) of one reaction's definition (R2.0): 0 unless it is
    well-formed (sc.well_formed: names consumed and produced species);
    else the mean of {well-formed -- _formed_credit on unit `position`,
    NONCONSERVING_CREDIT for a reaction that does not conserve mass as
    written, or whose closure cannot be tested, unless its unit's streams
    confirm it (R7.4) -- reactant among the consumed species, conversion
    numeric in [0, 1]}."""
    note = _malformed_note(sc, reaction)
    if note:
        return 0.0, [where + note]
    consumed, _produced = sc.reaction_sides(reaction)
    reactant = reaction.get('reactant')
    parts = {
        'equation|stoichiometry': _formed_credit(sc, position, reaction),
        'reactant (a consumed species)': float(
            isinstance(reactant, str) and reactant.strip() in consumed),
        'conversion (0..1)': float(
            _valid_fraction(reaction.get('conversion')) is not None),
    }
    missing = [where + (_UNTESTED_REACTION if sc.conserves(reaction) is None
                        else _NONCONSERVING_REACTION)] * (
        parts['equation|stoichiometry'] < 1.0)
    return (sum(parts.values()) / len(parts),
            missing + [f'{where}.{k}' for k, ok in parts.items()
                       if not ok])


def _per_reactive_unit(sc, reaction_score):
    """(mean, missing, n units scoring 1, n reactive units) over the
    reaction units (_reaction_units, each counting once: R4.12) of each
    unit's mean reaction_score(sc, position, index, reaction) -> (score,
    missing); a reactive unit without reactions contributes 0, and so does
    the R7.1 pseudo-member (_hidden_chemistry) when present. The mean is
    None when the population is empty."""
    unit_scores, missing, full = [], [], 0
    hidden = _hidden_chemistry(sc, 'its reactions are unknown')
    if hidden:
        unit_scores.append(0.0)
        missing.append(hidden)
    for position, reason in _reaction_units(sc):
        label = sc.unit_labels[position]
        reactions = [(ri, r) for ri, r in
                     enumerate(_as_list(sc.units[position].get('reactions')))
                     if isinstance(r, dict)]
        if not reactions:
            unit_scores.append(0.0)
            missing.append(f'{label}.reactions: none declared ({reason})')
            continue
        scores = []
        for ri, reaction in reactions:
            score, gaps = reaction_score(sc, position, ri, reaction)
            scores.append(score)
            missing.extend(gaps)
        unit_score = _mean(scores)
        full += unit_score == 1.0
        unit_scores.append(unit_score)
    return _mean(unit_scores), missing, full, len(unit_scores)


def _score_rxn_02(sc):  # CMP-RXN-02
    score, missing, full, n = _per_reactive_unit(
        sc, lambda sc, p, ri, r: _reaction_completeness(
            sc, r, f'{sc.unit_labels[p]}.reactions[{ri}]', p))
    if not n:
        return None, 'no reactive units and no declared reactions', []
    # R7.4: the model-quality note -- reactions that do not conserve mass
    # as written, and how many of them their unit's streams confirm; R2.13:
    # likewise those whose closure cannot be tested.
    declared = [(p, r) for p, _reason in _reaction_units(sc)
                for r in _dicts(sc.units[p].get('reactions'))
                if sc.well_formed(r)]
    note = ''
    for verdict, what in ((False, 'do not conserve mass as written'),
                          (None, 'cannot be tested for mass conservation')):
        group = [(p, r) for p, r in declared if sc.conserves(r) is verdict]
        if group:
            confirmed = sum(_reaction_confirmed(sc, p, r) for p, r in group)
            note += (f'; {len(group)} reaction(s) {what} ({confirmed} '
                     f'confirmed by their unit\'s stream data)')
    return (score, f'{full} of {n} reactive / reaction-declaring unit(s) '
            f'define every reaction completely'
            + _HIDDEN_NOTE * bool(_unexplained_hosts(sc)) + note, missing)


def _score_rxn_03(sc):  # CMP-RXN-03
    # Population = the reaction units (R2.4: the reactive units plus every
    # other unit declaring reactions), not the units that happen to carry
    # >= 2 reactions: deleting a reactor's reactions must not turn the
    # criterion not-applicable. Per unit: >= 2 reactions -> 1 when every one
    # is indexed, else 0; exactly one -> 1 (no order to state); none -> 0
    # (its reaction set, and so their order, is unknown). Only well-formed
    # reactions make a reaction set (R2.0): a unit with none scores 0. The
    # R7.1 pseudo-member (_hidden_chemistry) scores 0 too.
    reactive = _reaction_units(sc)
    hidden = _hidden_chemistry(sc, 'its reaction order is unknown')
    if not reactive and not hidden:
        return None, 'no reactive units and no declared reactions', []
    scores, missing, multi = [], [], 0
    if hidden:
        scores.append(0.0)
        missing.append(hidden)
    for position, reason in reactive:
        label = sc.unit_labels[position]
        reactions = _dicts(sc.units[position].get('reactions'))
        if not sc.unit_reacts[position]:
            scores.append(0.0)
            missing.append(f'{label}.reactions: none well-formed declared '
                           f'({reason}), so their series/parallel order is '
                           f'unknown')
            continue
        multi += len(reactions) >= 2
        unindexed = [ri for ri, r in enumerate(reactions)
                     if len(reactions) >= 2 and (
                         isinstance(r.get('index'), bool)
                         or not isinstance(r.get('index'), int))]
        scores.append(0.0 if unindexed else 1.0)
        missing.extend(f'{label}.reactions[{ri}].index' for ri in unindexed)
    return (_mean(scores), f'{scores.count(1.0)} of {len(scores)} '
            f'reactive / reaction-declaring unit(s) state their reaction '
            f'order ({multi} with two or more reactions)'
            + _HIDDEN_NOTE * bool(hidden), missing)


def _coefficient_sources(sc, reaction, identities=False):
    """[{chemical id: signed coefficient}, ...] of a reaction, one per
    source that resolves through the validator's helpers: its stoichiometry
    first, then its equation (phase tags stripped); [] when neither
    does. An equation whose every species resolves but whose coefficients
    cancel (an identity, 'Ethanol -> Ethanol') resolves to {} and is kept
    only with `identities` (_closure_error: it creates no mass); a
    stoichiometry resolving to {} never is -- its coefficients are all zero
    or not numeric (INVALID == MISSING), so it names no reaction."""
    validator = _validator()
    sources = []
    reaction = _as_dict(reaction)
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
                sources.append(coeffs)
    except Exception:  # malformed document values: no coefficients
        pass
    try:
        equation = reaction.get('equation')
        if isinstance(equation, str) and '->' in equation:
            sides = [' + '.join(_PHASE_TAG_RE.sub('', t.strip())
                                for t in side.split('+'))
                     for side in equation.split('->', 1)]
            coeffs = validator._parse_equation(' -> '.join(sides), sc.vctx)
            if coeffs or (identities and coeffs is not None):
                sources.append(coeffs)
    except Exception:  # malformed document values: no coefficients
        pass
    return sources


def _reaction_coefficients(sc, reaction):
    """{chemical id: signed coefficient} of a reaction -- from its
    stoichiometry, else its equation (phase tags stripped) -- resolved by the
    validator's helpers; None when neither resolves."""
    sources = _coefficient_sources(sc, reaction)
    return sources[0] if sources else None


def _closure_error(sc, reaction):
    """R6.2: the smallest relative mass imbalance of a reaction over its
    coefficient sources (_coefficient_sources) whose participants all have
    molar masses (declared, else from the formula, as the validator
    resolves them): per source the smaller of the mol-basis imbalance
    |sum(nu * MW)| / sum over consumed species of |nu| * MW and the
    wt-basis imbalance |sum(nu)| / sum over consumed species of |nu|;
    0 for a source whose resolved coefficients all cancel (an identity:
    no mass is created, whatever the molar masses). None when no source is
    testable (a species unresolved, a molar mass missing, or nothing
    consumed)."""
    errors = []
    for coeffs in _coefficient_sources(sc, reaction, identities=True):
        if not coeffs:
            errors.append(0.0)
            continue
        found = _basis_errors(sc, coeffs)
        if found is not None:
            errors.append(min(found))
    return min(errors) if errors else None


def _basis_errors(sc, coeffs):
    """R6.2 / R8.8: (mol-basis, wt-basis) relative mass imbalance of one
    set of resolved coefficients `coeffs` ({chemical id: signed nu}):
    |sum(nu * MW)| / sum over consumed species of |nu| * MW, and
    |sum(nu)| / sum over consumed species of |nu|, with every molar mass
    resolved as the validator resolves it (declared, else from the
    formula); None when a molar mass is missing or nothing is
    consumed."""
    masses = [sc.vctx.molar_mass(cid) for cid in coeffs]
    if not coeffs or any(m is None for m in masses):
        return None
    nus = list(coeffs.values())
    mass_in = sum(-nu * m for nu, m in zip(nus, masses) if nu < 0)
    coeff_in = sum(-nu for nu in nus if nu < 0)
    if mass_in <= 0.0 or coeff_in <= 0.0:
        return None
    return (abs(sum(nu * m for nu, m in zip(nus, masses))) / mass_in,
            abs(sum(nus)) / coeff_in)


def _conserves_mass(sc, reaction):
    """R6.2: True when the reaction is testable (_closure_error not None)
    and conserves mass on a mol or a wt basis within RXN_CLOSURE_RTOL of
    its reactant side, False when testable and it does not, None when it
    is untestable (a participant's molar mass unknown, or nothing
    consumed). Only True is affirmative evidence of the closure (R2.13):
    deleting a participant's molar mass or its chemical's id turns True or
    False into None, never None into True. Deleting an entry of the
    stoichiometry object (a participant and its coefficient) can break the
    closure (True -> False) and can as well restore it: False -> True when
    the dropped term held the balance off, None -> True when it left the
    closure untestable (no molar mass, or an undeclared chemical). Such a
    deletion states a different reaction, as deleting a list element does,
    so it is outside the deletion guarantee whatever the reaction
    (CMP-RXN-02). False means the reaction contradicts the document's own
    molar masses, so the document is internally inconsistent and deletions
    on it are outside the guarantee too (_formed_credit)."""
    error = _closure_error(sc, reaction)
    return None if error is None else error <= RXN_CLOSURE_RTOL


def _has_phase_tags(equation):
    """True when every species of a reaction equation carries a thermosteam
    phase tag ('Glucose,l -> 2 Ethanol,l + 2 CO2,g')."""
    if not isinstance(equation, str) or '->' not in equation:
        return False
    terms = [t.strip() for side in equation.split('->', 1)
             for t in side.split('+') if t.strip()]
    return bool(terms) and all(_PHASE_TAG_RE.search(t) for t in terms)


def _basis_derivable(sc, reaction):
    """True when the reaction's basis follows from its coefficients (its
    stoichiometry, else its equation: _reaction_coefficients), every molar
    mass resolvable (declared or from the formula, as the validator
    resolves it). R8.8: its R6.2 closure (_basis_errors: the mol-basis
    |sum(nu*MW)| and the wt-basis |sum(nu)|, relative to the reactant
    side) holds within RXN_CLOSURE_RTOL on exactly one basis -- that one
    (a 3-significant-figure equation, ~0.2 % off, has its basis); when it
    holds on both (an isomerisation, or nearly one), a closure within
    BASIS_RTOL on either decides it -- on both, the basis is immaterial
    (R2.10); on one, that one -- else it is ambiguous; a reaction
    closing on neither (R7.4) has no derivable basis."""
    coeffs = _reaction_coefficients(sc, reaction)
    errors = _basis_errors(sc, coeffs) if coeffs else None
    if errors is None:
        return False
    holds = [error <= RXN_CLOSURE_RTOL for error in errors]
    if all(holds):
        return any(error <= BASIS_RTOL for error in errors)
    return any(holds)


def _single_phase_ports(sc, position):
    """True when unit `position` has >=1 connected stream and every one
    carries phase data with at most one phase, and (R5.8) no stream without
    a stated endpoint may be a missing port of it (with one present, its
    total mass closure, _mass_closure, must rule that out)."""
    uid = sc.units[position].get('id')
    if not isinstance(uid, str):
        return False
    if sc.dangling and not _mass_closure(sc, position)[0]:
        return False
    ports = sc.unit_inlets.get(uid, []) + sc.unit_outlets.get(uid, [])
    sets = [_phase_set(sc.streams[i]) for i in ports]
    return bool(sets) and all(s is not None and len(s) <= 1 for s in sets)


def _reaction_basis_phase(sc, position, ri, reaction):
    """(score, missing) of one reaction for CMP-RXN-04: mean of {basis,
    phase}, each DERIVED_CREDIT when derivable (neither is representable); 0
    for a reaction that is not well-formed (R2.0). R7.4: one that does not
    conserve mass as written is scored too -- its basis is never derivable
    (_basis_derivable needs its closure within RXN_CLOSURE_RTOL on one
    basis, R8.8)."""
    where = f'{sc.unit_labels[position]}.reactions[{ri}]'
    note = _malformed_note(sc, reaction)
    if note:
        return 0.0, [where + note]
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
            f'/ reaction-declaring unit(s)'
            + _HIDDEN_NOTE * bool(_unexplained_hosts(sc)), missing)

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
    """(mean or None, missing, n units scoring 1) over the costed units (or
    `positions`), each counting once (R4.12), of unit_score(sc, position) ->
    (score, missing locators)."""
    scores, missing = [], []
    for position in sc.costed if positions is None else positions:
        score, gaps = unit_score(sc, position)
        scores.append(score)
        missing.extend(gaps)
    return _mean(scores), missing, scores.count(1.0)


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
            f'costs', missing)


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
            f'cost for every item', missing)


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
            f'results with units', missing)


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
            f'power-law correlations for every item', missing)


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


def _bounded_item(unit):
    """The first power-law correlation item stating a positive
    size_upper_bound whose basis has a numeric design result S, so its
    count follows as N = ceil(S / size_upper_bound) (R3.11), or None."""
    results = _as_dict(unit.get('design_results'))
    for item, corr in _as_dict(unit.get('purchase_cost_correlations')).items():
        corr = _as_dict(corr)
        basis = corr.get('basis')
        if (corr.get('correlation_type') == 'power_law'
                and _valid_number(corr.get('size_upper_bound'), 0.0,
                                  lo_open=True) is not None
                and isinstance(basis, str)
                and _num(results.get(basis)) is not None):
            return item
    return None


def _count_source(unit):
    """What a unit's parallel counts derive from: "design_results['<count
    key>']" (_count_result_key), "purchase_cost_correlations['<item>']
    size_upper_bound" (_bounded_item), or None."""
    key = _count_result_key(unit)
    if key:
        return f"design_results['{key}']"
    item = _bounded_item(unit)
    if item is not None:
        return f"purchase_cost_correlations['{item}'].size_upper_bound"
    return None


def _cost_06_source(sc, position):
    """What costed unit `position`'s parallel counts derive from: its own
    data (_count_source), else -- R8.13, as CMP-COST-07 credits a class
    default lifetime -- "its class's own sizing" for a core BioSTEAM class
    known exactly (_core_class_known: rebuilt from its class, its design
    re-sizes the equipment, each CostItem.N of an @cost-decorated class is
    recomputed from the item's size upper bound, and Unit.parallel keeps
    its class default; a procedurally costed class such as Pump or
    HXutility has no @cost items, so the wording names neither); else
    None."""
    source = _count_source(sc.units[position])
    if source:
        return source
    if _core_class_known(sc, position):
        return "its class's own sizing (its design and any @cost size bounds)"
    return None


def _cost_06_unit(sc, position):
    """CMP-COST-06 of one costed unit: DERIVED_CREDIT when its counts are
    derivable (_cost_06_source: a count design result 'Number of ...', a
    power-law item's size_upper_bound over its basis' design result, or,
    R8.13, the own sizing of a core class known exactly), else 0 (never
    representable)."""
    label = sc.unit_labels[position]
    source = _cost_06_source(sc, position)
    if source:
        return DERIVED_CREDIT, [f"{label}: parallel counts (Unit.parallel, "
                                f"CostItem.N) not representable (derivable "
                                f"from {source})"]
    return 0.0, [f'{label}: parallel counts (Unit.parallel, CostItem.N) not '
                 f'representable']


def _score_cost_06(sc):  # CMP-COST-06
    if not sc.costed:
        return None, _NO_COSTED_UNITS, []
    score, missing, _ok = _per_costed_unit(sc, _cost_06_unit)
    derived = sum(1 for p in sc.costed if _cost_06_source(sc, p))
    return (score, f'not representable; derivable from a count design result, '
            f"a size upper bound or a core class's own sizing for "
            f'{derived} of {len(sc.costed)} costed unit(s)', missing)


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
    derived = sum(1 for p in sc.costed if _cost_07_unit(sc, p)[0]
                  == DERIVED_CREDIT)
    return (score, f'{ok} of {len(sc.costed)} costed unit(s) state every '
            f'lifetime; {derived} more earn derived credit ({DERIVED_CREDIT:g}) '
            f'as core BioSTEAM classes known exactly, whose class-default '
            f'lifetime applies', missing)

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


def _agent_phase_ok(composition):
    """True when a heat agent's phase is stated (R4.2): a non-empty
    composition whose every entry carries the same phase, 'l' or 'g' --
    BioSTEAM's UtilityAgent is a one-phase stream (liquid cooling water,
    vapor steam), and its latent heat depends on that phase."""
    entries = _as_list(composition)
    phases = [entry.get('phase') if isinstance(entry, dict) else None
              for entry in entries]
    return bool(phases) and phases[0] in ('l', 'g') and all(
        phase == phases[0] for phase in phases)


# Parts of an agent scored by CMP-UTIL-01, per group (R4.2: heat agents
# also need their phase).
_AGENT_PARTS = {'power_utilities': 1, 'heat_utilities': 4}
_OTHER_AGENT_PARTS = 3


def _agent_gaps(group, agent):
    """Unstated or implausible required fields of one declared agent (R3.3,
    the shared stream-state predicates: heat/other agents need a plausible
    temperature (_valid_T) and pressure (_valid_P) and a plausible
    composition (_composition_ok); a heat agent also its phase
    (_agent_phase_ok, R4.2); power: an id)."""
    if group == 'power_utilities':
        return [] if _nonempty_str(agent.get('id')) else ['id']
    gaps = [k for k, valid in (('temperature', _valid_T), ('pressure', _valid_P))
            if valid(agent.get(k)) is None]
    if not _composition_ok(agent.get('composition')):
        gaps.append('composition')
    if group == 'heat_utilities' and not _agent_phase_ok(
            agent.get('composition')):
        gaps.append('composition[*].phase (one phase, l or g, on every entry)')
    return gaps


def _unit_families(sc):
    """The families of the declared units; None stands for every unit whose
    family is unknown (R4.13: it could be anything, so it carries every
    family-dependent need -- see _needs_family). A unit that may be a
    reactor although its family is none adds the reactor family too, so
    its label cannot shed a reactor's heat and utility needs
    (CMP-UTIL-01..05): one held to reactor rules (_reactor_candidate: it
    declares chemistry of its own, R8.4, or makes a hosting tank claim not
    ruled out, R7.1), and any other hosting claim not ruled out
    (_hosting_claim, _ruled_out: an 'Extractor' or 'Wash column' label may
    hide a fermentation too, and extraction exchanges no heat of its
    own). Deleting data only removes the rule-out evidence, so the
    reactor family can only join. Cached per context."""
    if sc.unit_families is None:
        families = {family for _name, family, _c in sc.unit_classes}
        if any(_reactor_candidate(sc, p)
               or (_hosting_claim(sc, p) and not _ruled_out(sc, p))
               for p in range(len(sc.units))):
            families.add('reactor')
        sc.unit_families = frozenset(families)
    return set(sc.unit_families)


def _needs_family(families, needing):
    """True when a unit of one of `families` (a _unit_families set) carries
    the need of the `needing` family set: a unit of that set, or, R4.13, a
    unit of unknown family (affirmative evidence alone waives a need)."""
    return None in families or bool(families & needing)


def _utility_need(sc):
    """True when the flowsheet needs utility agents (R1.9): a unit in a
    utility-using family or of unknown family (R4.13), a heat need
    (_heat_need), a unit's result naming an agent no utilities group
    declares (_undeclared_refs), or any agent CMP-UTIL-01/02 count
    (_counted_agents: every power and other agent, a heat agent only where
    heat is needed). A declared heat agent a unit's results name creates
    no need of its own: that need follows from claims, never from the
    deletable result."""
    return (_needs_family(_unit_families(sc), UTILITY_USING_FAMILIES)
            or _heat_need(sc) or bool(_undeclared_refs(sc))
            or bool(_counted_agents(sc)))


def _undeclared_refs(sc):
    """[(agent id, unit label)] of the agents units' utility results name
    (_utility_refs) that no utilities group declares, in reference
    order."""
    declared = {agent.get('id') for _g, agent, _l in sc.agents
                if isinstance(agent.get('id'), str)}
    return [(agent_id, unit_label) for agent_id, unit_label
            in _utility_refs(sc).items() if agent_id not in declared]


# Exact separator classes BioSTEAM regenerates by a temperature swing: they
# add regeneration steam and cooling to every simulation (MolecularSieve's
# add_heat_utility, AdsorptionColumnTSA's regeneration), so they need heat
# agents although their family (splitter) does not.
REGENERATING_CLASSES = frozenset({'MolecularSieve', 'AdsorptionColumnTSA'})
# Facility kinds that serve a heat agent to the rest of the plant: BioSTEAM's
# CoolingTower, ChilledWaterPackage and RefrigerationPackage size the agent
# they regenerate from its temperature and temperature limit, so they need
# heat agents although their family (facility) does not. (Boilers and
# boiler-turbogenerators raise steam: their families need heat agents.)
HEAT_SERVING_FACILITY_KINDS = frozenset({'CoolingTower', 'ChilledWaterPackage',
                                         'RefrigerationPackage'})


def _regenerating_separator(sc, position):
    """True when unit `position` is a separator regenerated by a
    temperature swing, whose heat duty is its own (R8.3): an exact
    REGENERATING_CLASSES class, or a splitter-family claim without a class
    whose unit_type names a sorption or regeneration head (_sorption_label:
    a molecular sieve, adsorber, ion exchanger, absorber or scrubber). It
    carries the heat need as a heat-exchanging family does: the need rests
    on its claim, never on the duty it records, so deleting that duty (or
    the heat agents) never sheds it."""
    name, family, _confidence = sc.unit_classes[position]
    if name is not None:
        return name in REGENERATING_CLASSES
    return family == 'splitter' and _sorption_label(
        sc.units[position].get('unit_type'))


def _heat_serving_facility(sc, position):
    """True when unit `position` makes a facility claim that serves a heat
    agent (_facility_kind in HEAT_SERVING_FACILITY_KINDS: a cooling tower,
    a chilled-water or refrigeration package) or whose kind is unknown (it
    may be one, R4.13). Its claim needs heat agents, whatever duty it
    records."""
    if sc.unit_classes[position][1] != 'facility':
        return False
    kind = _facility_kind(sc, position)
    return kind is None or kind in HEAT_SERVING_FACILITY_KINDS


def _heat_family_need(sc):
    """True when a unit's claim needs heat-utility agents: a unit in a
    heat-exchanging family, of unknown family (R4.13) or held to reactor
    rules (_unit_families), a regenerating separator
    (_regenerating_separator), or a heat-serving facility
    (_heat_serving_facility)."""
    return (_needs_family(_unit_families(sc), HEAT_UTILITY_FAMILIES)
            or any(_regenerating_separator(sc, p)
                   or _heat_serving_facility(sc, p)
                   for p in range(len(sc.units))))


def _heat_need(sc):
    """True when the flowsheet needs heat-utility agents: exactly when a
    unit's claim needs them (_heat_family_need). The need follows from
    claims alone, never from data a document may omit: neither a heat
    duty a unit records (deleting the duty a label-only storage tank, an
    extractor ruled out as a host or a pump records must not turn
    CMP-UTIL-03 / CMP-UTIL-05 not applicable) nor a declared heat agent
    (deleting a spare agent's list must not either). A claim that can
    take no heat duty is contradicted by one instead (_physics_contradiction:
    a mixer, a pure splitter, a mechanical separator, a solids handler, a
    junction, a pump, valve or turbine), and its unknown family carries
    the need. Cached per context."""
    if sc.heat_need is None:
        sc.heat_need = _heat_family_need(sc)
    return sc.heat_need


def _counted_agents(sc):
    """The declared agents CMP-UTIL-01/02 score ([(group, agent, label)],
    in sc.agents order): every power and other agent, and every heat agent
    when some unit needs heat (_heat_need). A heat agent no unit's claim
    needs is no information a rebuild of that claim reads: counting it
    could only lower the score (R6.4), so deleting its list would raise it,
    and counting it only while a unit's result names it would let deleting
    that result raise it."""
    if _heat_need(sc):
        return list(sc.agents)
    return [(group, agent, label) for group, agent, label in sc.agents
            if group != 'heat_utilities']


_NO_UTILITY_NEED = 'no unit needs utilities and no agent is declared or referenced'


def _agent_mean(scored):
    """R6.4 aggregate of CMP-UTIL-01/02/03 over [(score, referenced)], where
    `referenced` marks an agent some unit's results name (and the synthetic
    undeclared / missing-kind entries, which stand for a need): the lowest
    mean over the nested sets S (referenced <= S <= all, S not empty), as
    _referenced_mean aggregates chemicals (R4.7) -- so agents no unit
    references can lower the score, never raise it (ten fully specified
    standard agents padded beside a half-specified referenced one change
    nothing). With nothing referenced, the same rule gives the lowest single
    score (as for chemicals): deleting a unit's last utility result then
    only widens the family minimised over, never raises the score."""
    if not scored:
        return None
    if not any(ref for _s, ref in scored):
        return min(s for s, _ref in scored)
    return _referenced_mean(scored)


def _agent_referenced(agent, refs):
    """True when a declared agent's (string) id is a key of `refs`
    (_utility_refs: named by some unit's utility results)."""
    aid = agent.get('id')
    return isinstance(aid, str) and aid in refs


def _unreferenced_note(scored):
    """Message suffix counting the unreferenced agents of [(score,
    referenced)] (_agent_mean), or ''."""
    count = sum(1 for _s, ref in scored if not ref)
    return (f'; {count} agent(s) no unit references can only lower the score'
            if count else '')


def _missing_agent_kinds(sc):
    """Locators of the synthetic missing-agent entries of CMP-UTIL-01/02
    (R2.7), each scoring 0: a heat agent when a unit is in a heat-exchanging
    family (HEAT_UTILITY_FAMILIES) but no heat-utility agent is declared; a
    power utility when a unit is in a power-drawing or -delivering family
    (POWER_UTILITY_FAMILIES) but no power utility is declared. Need is per
    kind: a declared power utility says nothing about steam. R4.13: a unit
    of unknown family needs both kinds."""
    families = _unit_families(sc)
    groups = {group for group, _a, _l in sc.agents}
    missing = []
    for group, needing, noun, why in (
            ('heat_utilities', HEAT_UTILITY_FAMILIES, 'heat agent',
             'exchange heat'),
            ('power_utilities', POWER_UTILITY_FAMILIES, 'power utility',
             'draw or deliver electricity')):
        needed = (_heat_family_need(sc) if group == 'heat_utilities'
                  else _needs_family(families, needing))
        if group in groups or not needed:
            continue
        missing.append(f'utilities.{group}: no {noun} declared, although '
                       f'{_need_reason(sc, needing, why)}')
    return missing


def _need_reason(sc, needing, why):
    """Why a claim-driven agent need of the `needing` families holds, as
    the tail of a CMP-UTIL-01..03 message or locator (`why`: e.g.
    'exchange heat'): units whose claimed family needs it; else units of
    unknown family (R4.13); else the units that may host a reaction
    (_reactor_candidate: a hosting tank claim not ruled out, R7.1, or
    reactions they declare, R8.4; or an extractor or column hosting claim
    not ruled out, _unit_families) and so carry a reactor's need -- named,
    since no unit of theirs claims to exchange heat; else the regenerating
    separators (_regenerating_separator); else the heat-serving facilities
    (_heat_serving_facility)."""
    claimed = {family for _n, family, _c in sc.unit_classes}
    if claimed & needing:
        return f'units {why}'
    if None in claimed:
        return f'units of unknown family (R4.13) may {why}'
    hosts = [sc.unit_labels[p] for p in range(len(sc.units))
             if _reactor_candidate(sc, p)
             or (_hosting_claim(sc, p) and not _ruled_out(sc, p))]
    if hosts and needing & REACTOR_FAMILIES:
        return (f"{', '.join(hosts[:3])} may host a reaction (a hosting "
                f"claim not ruled out, R7.1, or reactions declared, R8.4), "
                f"so a reactor's need applies")
    if needing & HEAT_UTILITY_FAMILIES:
        sieves = [sc.unit_labels[p] for p in range(len(sc.units))
                  if _regenerating_separator(sc, p)]
        if sieves:
            return (f"{', '.join(sieves[:3])} regenerate by a temperature "
                    f"swing")
        served = [sc.unit_labels[p] for p in range(len(sc.units))
                  if _heat_serving_facility(sc, p)]
        if served:
            return (f"{', '.join(served[:3])} serve a heat agent (a cooling "
                    f"tower, chilled-water or refrigeration package, or a "
                    f"facility of unknown kind)")
    return f'units {why}'


def _score_util_01(sc):  # CMP-UTIL-01
    if not _utility_need(sc):
        return None, _NO_UTILITY_NEED, []
    refs = _utility_refs(sc)
    declared = {agent.get('id'): (group, agent, label)
                for group, agent, label in sc.agents
                if isinstance(agent.get('id'), str)}
    counted = _counted_agents(sc)
    if not refs and not counted:
        return (0.0, 'utility-using units but no agent declared or referenced',
                ['utilities: no agents declared'])
    scored, missing = [], []  # [(score, referenced)] (R6.4, _agent_mean)
    for agent_id, unit_label in refs.items():
        if agent_id not in declared:
            scored.append((0.0, True))
            missing.append(f"utilities: no agent '{agent_id}' (referenced by "
                           f"{unit_label})")
    for group, agent, label in counted:
        gaps = _agent_gaps(group, agent)
        size = _AGENT_PARTS.get(group, _OTHER_AGENT_PARTS)
        scored.append((1.0 - len(gaps) / size, _agent_referenced(agent, refs)))
        missing.extend(f'{label}.{gap}' for gap in gaps)
    kinds = _missing_agent_kinds(sc)
    scored.extend((0.0, True) for _k in kinds)
    missing.extend(kinds)
    full = sum(1 for s, _ref in scored if s == 1.0)
    return (_agent_mean(scored), f'{full} of {len(scored)} referenced/declared '
            f'agent(s) resolve and are fully specified'
            + (f' ({len(kinds)} needed kind(s) missing)' if kinds else '')
            + _unreferenced_note(scored), missing)


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


def _valid_price(group, value):
    """A plausible utility price (R3.3): finite; for a heat agent's
    regeneration or heat-transfer price also >= 0. Else None."""
    return _valid_number(value, 0.0 if group == 'heat_utilities' else None)


_FUEL_ID_RE = re.compile(r'natural[\s_-]*gas|fuel', re.IGNORECASE)


def _fuel_agent(agent):
    """True unless the data affirmatively show that an other-utility agent
    is no fuel (R3.9, R2.13): its id names natural gas or a fuel, or its
    composition is methane-dominated (>= PORT_MATERIAL_MIN), or its
    composition is not plausibly stated (_composition_ok) -- then it may be
    one."""
    aid = agent.get('id')
    if isinstance(aid, str) and _FUEL_ID_RE.search(aid):
        return True
    composition = agent.get('composition')
    if not _composition_ok(composition):
        return True
    methane = sum(_entry_fraction(e) or 0.0 for e in _dicts(composition)
                  if str(e.get('component_name', '')).strip().lower()
                  in _PORT_MATERIAL_IDS['natural gas'])
    return methane >= PORT_MATERIAL_MIN


def _score_util_02(sc):  # CMP-UTIL-02
    if not _utility_need(sc):
        return None, _NO_UTILITY_NEED, []
    # String ids only: a list-valued id must not raise (unhashable, R2.0).
    declared = {agent.get('id') for _g, agent, _l in sc.agents
                if isinstance(agent.get('id'), str)}
    undeclared = [(agent_id, unit_label) for agent_id, unit_label
                  in _utility_refs(sc).items() if agent_id not in declared]
    kinds = _missing_agent_kinds(sc)
    counted = _counted_agents(sc)
    total = len(counted) + len(undeclared) + len(kinds)
    if not (counted or undeclared):
        return (0.0, 'utility-using units but no agent declared or referenced',
                ['utilities: no agents declared'])
    missing = [f"utilities: no agent '{agent_id}' to price (referenced by "
               f"{unit_label})" for agent_id, unit_label in undeclared]
    missing.extend(kinds)
    # [(score, referenced)] (R6.4, _agent_mean): undeclared and missing
    # kinds score 0 and stand for a need.
    scored = [(0.0, True)] * (len(undeclared) + len(kinds))
    refs = _utility_refs(sc)
    for group, agent, label in counted:
        fields, locator = _PRICE_FIELDS[group]
        prices = [p for p in (_valid_price(group, agent.get(f)) for f in fields)
                  if p is not None]
        referenced = _agent_referenced(agent, refs)
        if not prices:
            scored.append((0.0, referenced))
            missing.append(f'{label}.{locator}')
        elif group == 'other_utilities' and prices[0] == 0 \
                and _fuel_agent(agent):
            # R3.9: BioSTEAM prices a 0-priced fuel from bst.stream_prices,
            # which SFF cannot state (as CMP-FEED-02's utility ports).
            scored.append((0.5, referenced))
            missing.append(f'{label}.price: 0 on a fuel agent (priced by '
                           f'BioSTEAM, not representable)')
        else:
            scored.append((1.0, referenced))
    fuels = sum(1 for s, _ref in scored if s == 0.5)
    priced = sum(1 for s, _ref in scored if s == 1.0)
    return (_agent_mean(scored), f'{priced} of {total} '
            f'referenced/declared agent(s) priced'
            + (f' ({fuels} fuel agent(s) priced 0: half credit)' if fuels
               else '') + _unreferenced_note(scored), missing)


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


def _is_water(entry):
    """True when a composition entry names water ('Water' / 'H2O')."""
    return str(entry.get('component_name', '')).strip().lower() in (
        'water', 'h2o')


def _water_minor(composition):
    """R8.14: True only on affirmative evidence that water is NOT the bulk
    of an agent composition: a non-empty composition every entry of which
    names its species (a non-empty string) with a valid fraction
    (_entry_fraction), the species other than water summing to >= 0.5.
    An unnamed or fraction-less entry, or water merely unobserved, is no
    such evidence."""
    entries = _as_list(composition)
    if not entries or not all(
            isinstance(e, dict) and _nonempty_str(e.get('component_name'))
            and _entry_fraction(e) is not None for e in entries):
        return False
    return sum(_entry_fraction(e) for e in entries if not _is_water(e)) >= 0.5


def _phase_change_agent(agent, roles):
    """True when a heat agent affirmatively delivers latent heat, so it
    needs no temperature limit (R2.13): an all-vapor agent recorded as
    heating only, with positive duty (a condensing heating agent such as
    steam), or an all-liquid agent affirmatively not water-based
    (_water_minor, R8.14) and recorded as cooling only (an evaporating
    refrigerant). An agent no unit
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
        # R8.14: waived only on affirmative evidence of a non-water agent.
        return role == 'cooling' and _water_minor(agent.get('composition'))
    return False


def _score_util_03(sc):  # CMP-UTIL-03
    if not _heat_need(sc):
        return None, 'no unit exchanges heat with a heat-utility agent', []
    heat = [(agent, label) for group, agent, label in sc.agents
            if group == 'heat_utilities']
    if not heat:
        reason = _need_reason(sc, HEAT_UTILITY_FAMILIES, 'exchange heat')
        return (0.0, f'no heat-utility agent declared, although {reason}',
                [f'utilities.heat_utilities: none declared, although '
                 f'{reason}'])
    roles = _agent_roles(sc)
    refs = _utility_refs(sc)
    scored, missing = [], []  # [(score, referenced)] (R6.4, _agent_mean)
    for agent, label in heat:
        aid = agent.get('id')
        efficiency = _spec_efficiency(agent.get('heat_transfer_efficiency'))
        limit = (_valid_T(agent.get('temperature_limit')) is not None
                 or _phase_change_agent(
                     agent, roles.get(aid, set()) if isinstance(aid, str)
                     else set()))
        scored.append((0.5 * efficiency + 0.5 * limit,
                       _agent_referenced(agent, refs)))
        if not efficiency:
            missing.append(f'{label}.heat_transfer_efficiency')
        if not limit:
            missing.append(f'{label}.temperature_limit (sensible-heat agent)')
    full = sum(1 for s, _ref in scored if s == 1.0)
    return (_agent_mean(scored), f'{full} of {len(heat)} heat agent(s) fully '
            f'bounded' + _unreferenced_note(scored), missing)


_UTILITY_RESULT_KEYS = ('utility_consumption_results',
                        'utility_production_results')


def _draws_utility(sc, position):
    """True when unit `position` always draws a utility (R2.0,
    ALWAYS_DRAW_FAMILIES). Waived only on affirmative evidence (R2.13): an
    idle unit, every connected stream stating a zero total flow, draws
    nothing; a flash whose specs state it adiabatic (a valid Q of 0) draws
    no heat. R4.13: a unit of unknown family could be a pump or an
    exchanger, so it draws unless idle."""
    family = sc.unit_classes[position][1]
    if family is not None and family not in ALWAYS_DRAW_FAMILIES:
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
        family = sc.unit_classes[position][1]
        who = (f'a {family} unit always draws' if family else
               'a unit of unknown family may draw')
        return 0.0, [f'{label}.{_UTILITY_RESULT_KEYS[0]}|'
                     f'{_UTILITY_RESULT_KEYS[1]} (no numeric result, although '
                     f'{who} a utility)']
    return 1.0 - len(absent) / 2, [f'{label}.{k}' for k in absent]


def _score_util_04(sc):  # CMP-UTIL-04
    if not sc.units:
        return None, 'no units', []
    scores, missing = [], []
    for position in range(len(sc.units)):
        score, gaps = _util_04_unit(sc, position)
        scores.append(score)
        missing.extend(gaps)
    return (_mean(scores), f'{scores.count(1.0)} of {len(scores)} unit(s) '
            f'record utility results', missing)


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


# The BioSTEAM TEA parameter set (CMP-SYS-02, R3.10); the first three are the
# ones SFF can state: TEA_currency, TEA_year (the cost -- dollar -- year of
# every price and cost, per the schema; not the plant's start year) and the
# TEA target product (metadata.products).
_TEA_ITEMS = ('currency', 'dollar year (cost basis)', 'target product',
              'plant start year and duration', 'IRR (discount rate)',
              'income tax', 'depreciation schedule', 'construction schedule',
              'startup period and fractions', 'working capital',
              'fixed-operating-cost model',
              'capital-cost model (Lang or DPI/TDC/FCI factors, OSBL units, '
              'TEA class)', 'financing',
              'price book (bst.stream_prices, incl. utility-port prices)')


def _target_products_designated(sc):
    """True when metadata.products is a non-empty list whose every entry
    names (stream_id) a declared boundary outlet: the TEA's target product
    (R3.10)."""
    products = sc.metadata.get('products')
    outlet_ids = {sc.streams[i].get('id') for i in sc.outlets
                  if _nonempty_str(sc.streams[i].get('id'))}
    return (isinstance(products, list) and bool(products)
            and all(isinstance(p, dict) and isinstance(p.get('stream_id'), str)
                    and p['stream_id'] in outlet_ids for p in products))


# R4.9: a TEA currency is an ISO 4217 code; a TEA (dollar) year an integer in
# [TEA_YEAR_MIN, TEA_YEAR_MAX].
_CURRENCY_RE = re.compile(r'^[A-Z]{3}$')
TEA_YEAR_MIN, TEA_YEAR_MAX = 1900, 2100


def _valid_tea_currency(x):
    """True for a three-capital-letter currency code ('USD', 'EUR'); 'x',
    'dollars' or 'usd' state nothing BioSTEAM's TEA can use (R4.9)."""
    return isinstance(x, str) and bool(_CURRENCY_RE.match(x))


def _valid_tea_year(x):
    """True for an integral year in [TEA_YEAR_MIN, TEA_YEAR_MAX] (an int, an
    integral float, or a legacy inline {value, units}); 99999 or 1.5 is
    implausible and counts as unstated (R4.9)."""
    year = _num(x)
    return (year is not None and year.is_integer()
            and TEA_YEAR_MIN <= year <= TEA_YEAR_MAX)


def _score_sys_02(sc):  # CMP-SYS-02
    stated = {'currency': _valid_tea_currency(sc.metadata.get('TEA_currency')),
              'dollar year (cost basis)': _valid_tea_year(
                  sc.metadata.get('TEA_year')),
              'target product': _target_products_designated(sc)}
    missing = [f'metadata.{field}{note}' for field, item, note in
               (('TEA_currency', 'currency', ' (a three-letter code such as '
                 'USD)'),
                ('TEA_year', 'dollar year (cost basis)', ' (cost year: an '
                 f'integer in [{TEA_YEAR_MIN}, {TEA_YEAR_MAX}])'),
                ('products', 'target product', ' (non-empty; every stream_id '
                 'a declared boundary outlet)'))
               if not stated[item]]
    missing.extend(f'metadata: {item} not representable'
                   for item in _TEA_ITEMS if item not in stated)
    n = sum(stated.values())
    return (n / len(_TEA_ITEMS), f'{n} of {len(_TEA_ITEMS)} TEA parameter '
            f'groups stated (only currency, dollar year and target product '
            f'are representable)', missing)


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


_NO_FLOWSHEET = 'no units and no streams: nothing to cross-reference or measure'


def _score_int_02(sc):  # CMP-INT-02
    if not sc.units and not sc.streams:  # R3.11
        return None, _NO_FLOWSHEET, []
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


def _dimension_mismatches(sc):
    """R8.16: sorted "<field> in '<unit>' (a <dimension> unit)" notes, one
    per field and unit, for every quantity the scorer reads with a known
    dimension -- a stream's or phase's or utility agent's temperature,
    pressure or flow, a dimensional design_input_specs key (per element of
    a list) -- whose stated unit (inline, else its quantity_units_global
    alias) the built-in table knows only for another dimension. Each such
    quantity scores as invalid wherever it is read (_to_internal,
    _explicit_keys); CMP-INT-04 names them without changing its score."""
    notes = set()

    def note(value, field, dimension):
        if _num(value) is None:
            return
        unit = _stated_unit(value, field, sc.quantity_units)
        others = _other_dimensions(unit, dimension)
        if others:
            notes.add(f"{field} in '{unit}' (a {' or '.join(others)} unit)")

    def quantities(obj):
        for field, dimension in _QUANTITY_DIMENSIONS.items():
            if field in obj:
                note(obj[field], field, dimension)

    for stream in _dicts(sc.doc.get('streams')):
        sp = _as_dict(stream.get('stream_properties'))
        quantities(sp)
        for phase in _as_dict(sp.get('phases')).values():
            quantities(_as_dict(phase))
    utilities = _as_dict(sc.doc.get('utilities'))
    for group in ('heat_utilities', 'power_utilities', 'other_utilities'):
        for agent in _dicts(utilities.get(group)):
            quantities(agent)
    # The units as stated (sc.units holds converted copies, same positions).
    raw_units = _first_by_id(_dicts(sc.doc.get('units')))
    for position, unit in enumerate(raw_units):
        family = sc.unit_classes[position][1]
        specs = _as_dict(unit.get('design_input_specs'))
        for key, value in specs.items():
            dimension = _spec_dimension(key, family)
            if dimension is None:
                continue
            for element in (value if isinstance(value, list) else [value]):
                note(element, key, dimension)
    return sorted(notes)


def _score_int_04(sc):  # CMP-INT-04
    if not sc.units and not sc.streams:  # R3.11
        return None, _NO_FLOWSHEET, []
    mismatched = _dimension_mismatches(sc)
    flagged = ('' if not mismatched else
               f'; but {len({m.split(" in ", 1)[0] for m in mismatched})} '
               f'field(s) state quantities in a unit '
               f'of another dimension, invalid wherever they are read '
               f'(R8.16): {", ".join(mismatched[:3])}'
               + (', ...' if len(mismatched) > 3 else ''))
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
                f'quantities carry units' + flagged, missing)
    # R4.11: current shape -- QU-01 (every quantity field has a
    # quantity_units_global alias) and QU-02 (every unit string parses), half
    # each; a QU-02 result that is missing or skipped earns nothing.
    result = sc.check_result('QU-01')
    if result is None or result.status == 'skip':
        return None, 'QU-01 not evaluated', []
    parseable = sc.check_result('QU-02')
    passes, missing = 0, []
    for check_id, r, what in (
            ('QU-01', result, 'a quantity field lacks a quantity_units_global '
             'alias'),
            ('QU-02', parseable, 'a quantity-unit string does not parse')):
        if r is not None and r.status == 'pass':
            passes += 1
        else:
            status = 'not evaluated' if r is None else f'{r.status}s'
            missing.append(_clip(f'{check_id} {status} ({what})'
                                 + (f': {r.message}' if r is not None
                                    and r.message else '')))
    message = ('every quantity field has a quantity_units_global alias and '
               'every unit string parses' if passes == 2 else
               f'{passes} of QU-01 (aliases) and QU-02 (parseable unit '
               f'strings) pass')
    # R8.16: the validator's verdict drives the score; a unit of another
    # dimension parses, so it is only flagged here.
    return passes / 2, message + flagged, missing

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

#%% Docs rendering (criteria reference of docs/validation/completeness.md)

# ASCII punctuation that could open inline markup in MyST/CommonMark
# (emphasis, code, links, raw HTML, roles, table cells). Registry text is
# prose, never markup, so each occurrence is backslash-escaped.
_MD_SPECIAL_RE = re.compile(r'([\\`*_\[\]{}<>|$~])')


def _md_text(text):
    """`text` as one line of literal MyST inline text: whitespace runs
    (newlines included) collapsed to one space and markup characters
    backslash-escaped, so it is safe in a table cell or a definition."""
    return _MD_SPECIAL_RE.sub(r'\\\1', ' '.join(str(text).split()))


def _render_criteria_markdown(registry=None):
    """
    Render the criteria registry as the MyST reference tables of the docs.

    Parameters
    ----------
    registry : dict, optional
        As returned by :func:`load_completeness_criteria`. Defaults to the
        committed ``criteria.yaml``.

    Returns
    -------
    str
        MyST Markdown with LF line endings, ending in exactly one newline: a
        summary table of the categories (criteria count, total weight and
        share of the overall weight); per non-empty category a ``###``
        heading, a table of its criteria (id, title, weight, criticality,
        representable since -- ``all versions`` for ``"0.0.0"``, ``never``
        for null) and each criterion's full rationale as a definition list;
        then the exclusions table (id, kind, title, rationale). It is the
        block between the ``BEGIN GENERATED`` / ``END GENERATED`` markers of
        ``docs/validation/completeness.md`` (pinned by a Tier 1 test; print
        it with the ``--render-docs`` CLI flag).
    """
    if registry is None:
        registry = load_completeness_criteria()
    categories = registry['categories']
    criteria = list(registry['criteria'].values())
    total = sum(c['weight'] for c in criteria)
    by_category = {key: [c for c in criteria if c['category'] == key]
                   for key in categories}
    lines = [f'{len(criteria)} criteria in {len(categories)} categories, '
             f'total weight {total:g}.', '',
             '| Key | Category | Criteria | Total weight | Share of weight |',
             '| --- | --- | ---: | ---: | ---: |']
    for key, title in categories.items():
        weight = sum(c['weight'] for c in by_category[key])
        lines.append(f'| `{key}` | {_md_text(title)} | {len(by_category[key])} '
                     f'| {weight:g} | {100 * weight / total:.1f} % |')
    for key, title in categories.items():
        if not by_category[key]:
            continue
        lines += ['', f'### {_md_text(title)}', '',
                  '| ID | Criterion | Weight | Criticality | '
                  'Representable since |',
                  '| --- | --- | ---: | --- | --- |']
        for c in by_category[key]:
            since = c['representable_since']
            since = ('never' if since is None else
                     'all versions' if since == '0.0.0' else since)
            lines.append(f"| {c['id']} | {_md_text(c['title'])} | "
                         f"{c['weight']:g} | {c['criticality']} | {since} |")
        for c in by_category[key]:
            lines += ['', f"{c['id']} -- {_md_text(c['title'])}",
                      f": {_md_text(c['rationale'])}"]
    lines += ['', '### Exclusions (deliberately not scored)', '',
              '| ID | Kind | Information | Why it is not scored |',
              '| --- | --- | --- | --- |']
    lines += [f"| {e['id']} | `{e['kind']}` | {_md_text(e['title'])} | "
              f"{_md_text(e['rationale'])} |" for e in registry['exclusions']]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    """CLI: print the completeness report of one SFF file (``--json`` for the
    full machine-readable report), or, with ``--render-docs``, the generated
    criteria reference block of ``docs/validation/completeness.md``."""
    parser = argparse.ArgumentParser(
        prog='python pisces_sff/completeness/_completeness.py',
        description='Score how completely an SFF flowsheet states what '
                    'BioSTEAM needs to rebuild it.')
    parser.add_argument('file', nargs='?', help='SFF JSON file')
    parser.add_argument('--json', action='store_true',
                        help='print the full report as JSON')
    parser.add_argument('--max-missing', type=int, default=5,
                        help='missing locators listed per criterion (text mode)')
    parser.add_argument('--render-docs', action='store_true',
                        help='print the criteria reference tables (MyST) that '
                             'docs/validation/completeness.md embeds between '
                             'its GENERATED markers, and exit')
    args = parser.parse_args(argv)
    try:  # never die on a console that cannot encode a chemical/unit name
        sys.stdout.reconfigure(errors='backslashreplace')
    except AttributeError:
        pass  # a redirected non-console stream (e.g. io.StringIO in tests)
    if args.render_docs:
        print(_render_criteria_markdown(), end='')
        return 0
    if args.file is None:
        parser.error('the following arguments are required: file '
                     '(unless --render-docs is given)')
    report = score_flowsheet_completeness(args.file)
    if args.json:
        print(json.dumps(_report_to_jsonable(report), indent=2))
    else:
        print(format_completeness_report(report, max_missing=args.max_missing))
    return 0


if __name__ == '__main__':
    sys.exit(main())
