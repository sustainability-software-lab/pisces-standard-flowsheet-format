# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Enumerate every declarative constraint in ``sff_schema.json`` and, where it can,
synthesize a value that violates it.

This is the second coverage axis of Tier 3 (spec §5). The first axis -- the nine
catalogue IDs the schema enforces -- only covers what someone remembered to
catalogue; this one covers what the schema actually says, so a ``required``
entry or an ``enum`` added without a test fails the tier immediately.

Each constraint is reported as a :class:`Locator` carrying both where it lives in
the schema and which field of the conforming document it governs. A locator
whose instance pointer cannot be resolved, or whose violation cannot be derived
mechanically (``anyOf``, ``additionalProperties: false``), is reported as
*unsweepable* rather than dropped -- Tier 3 requires each of those to be claimed
by a hand-written test.

The sweepable/unsweepable split is **static**: it is decided from the locator
alone plus the hand-maintained :data:`UNREJECTABLE` set, never by running the
validator. Running the validator here would be byte-for-byte the computation the
generated sweep performs, which would make the sweep's "the violation is
rejected" assertion true by construction and unfalsifiable. Keeping the split
static means a *newly* unrejectable constraint fails the sweep loudly instead of
quietly dropping out of it.

Pure library: no assertions, no test classes.
"""

import json
from collections import namedtuple
from pathlib import Path

import _documents

#: The schema under test.
SCHEMA_PATH = (Path(__file__).resolve().parents[1]
               / 'pisces_sff' / 'schema' / 'sff_schema.json')

#: The declarative keywords Tier 3 must cover. Every keyword the schema uses to
#: constrain an instance appears here; :func:`unmodeled_keywords` reports any
#: constraining keyword the walk meets that is *not* in this tuple, so a
#: fifteenth one cannot enter the schema invisibly.
KEYWORDS = ('required', 'enum', 'const', 'type', 'pattern', 'minimum',
            'maximum', 'exclusiveMinimum', 'minLength', 'minItems',
            'uniqueItems', 'minProperties', 'anyOf', 'additionalProperties')

#: Draft-07 keywords that assert something about an instance. Used only to
#: detect a constraining keyword the walk does not model (see KEYWORDS).
_ASSERTION_KEYWORDS = frozenset((
    'type', 'enum', 'const', 'multipleOf', 'maximum', 'exclusiveMaximum',
    'minimum', 'exclusiveMinimum', 'maxLength', 'minLength', 'pattern',
    'items', 'additionalItems', 'maxItems', 'minItems', 'uniqueItems',
    'contains', 'maxProperties', 'minProperties', 'required', 'properties',
    'patternProperties', 'additionalProperties', 'dependencies',
    'propertyNames', 'allOf', 'anyOf', 'oneOf', 'not', 'if', 'then', 'else',
))

#: Keywords the walk handles by *descending* rather than by emitting a locator.
#: A keyword in here needs no locator of its own -- the constraints it carries
#: are enumerated inside it.
_TRAVERSED_KEYWORDS = frozenset((
    'properties', 'items', 'allOf', 'anyOf', 'oneOf', 'if', 'then', 'else',
    'additionalProperties', '$ref',
))

#: One declarative constraint. `instance_pointer` is '' when the constraint
#: governs a field the conforming document does not populate.
Locator = namedtuple('Locator', 'schema_pointer keyword detail instance_pointer')

#: An instance-path step standing for "every element of this array".
_ARRAY = '[]'

#: An instance-path step standing for "every key of this object that the
#: schema's own `properties` does not name" -- i.e. the keys ``additionalProperties``
#: actually governs.
_Extra = namedtuple('_Extra', 'declared')

_CACHE = {}


#: Locators whose synthesized violation the schema does **not** reject, keyed by
#: :func:`locator_id`. These are excluded from :func:`sweepable` statically, with
#: a reason each, rather than by running the validator -- see the module
#: docstring for why. Every entry is a constraint that is real but cannot be made
#: to fire by changing one field of a conforming document, so Tier 3 must claim
#: it with a hand-written test that mutates more than one field (or asserts the
#: conditional's other branch).
UNREJECTABLE = frozenset((
    # `simulator_package` / `flowsheet_model_package` each declare
    # `anyOf: [{required:[commit]}, {required:[version]}]`. Deleting either name
    # alone leaves the sibling branch satisfied, so the document still validates.
    '/properties/metadata/properties/reproducibility/properties/simulator_package/anyOf/0/required#required:commit',
    '/properties/metadata/properties/reproducibility/properties/simulator_package/anyOf/1/required#required:version',
    '/properties/metadata/properties/reproducibility/properties/flowsheet_model_package/anyOf/0/required#required:commit',
    '/properties/metadata/properties/reproducibility/properties/flowsheet_model_package/anyOf/1/required#required:version',
    # The same two packages' `allOf[0].if: {required: [commit]}` conditions.
    # Deleting `commit` makes the `if` false, so the paired `then`
    # (`required: [url]`) simply never applies and nothing fails.
    '/properties/metadata/properties/reproducibility/properties/simulator_package/allOf/0/if/required#required:commit',
    '/properties/metadata/properties/reproducibility/properties/flowsheet_model_package/allOf/0/if/required#required:commit',
    # `chemicals[].allOf[1]` is `if included_in_thermo == false then require
    # molar_mass`. Both chemicals in the conforming document set
    # `included_in_thermo: true`, so the `if` never matches and deleting
    # `molar_mass` is not rejected.
    '/properties/chemicals/items/allOf/1/then/required#required:molar_mass',
    # `reactions[].anyOf: [{required:[equation]}, {required:[stoichiometry]}]`.
    # The conforming reaction carries both, so deleting either one alone leaves
    # the other to satisfy the anyOf.
    '/properties/units/items/properties/reactions/items/anyOf/0/required#required:equation',
    '/properties/units/items/properties/reactions/items/anyOf/1/required#required:stoichiometry',
    # The two `const` discriminators are the *conditions* of `chemicals[].allOf`,
    # not assertions about a conforming document. Violating a `const` inside an
    # `if` only makes that conditional inapplicable; it can never reject.
    # (allOf/0 asserts `included_in_thermo` is `true` -- the conforming value --
    # so its violation `false` merely switches which branch applies; allOf/1
    # asserts `false`, which the conforming document already does not match, so
    # no single-field edit can turn it into a rejection either.)
    '/properties/chemicals/items/allOf/0/if/properties/included_in_thermo/const#const:true',
    '/properties/chemicals/items/allOf/1/if/properties/included_in_thermo/const#const:false',
))


class CannotSynthesize(Exception):
    """Raised when a constraint's violation cannot be derived mechanically."""


def _schema():
    if 'schema' not in _CACHE:
        _CACHE['schema'] = json.loads(
            SCHEMA_PATH.read_text(encoding='utf-8'))
    return _CACHE['schema']


def _resolve_schema_pointer(pointer):
    """Return the subschema an internal ``$ref`` target pointer addresses."""
    node = _schema()
    for token in pointer.lstrip('/').split('/'):
        node = node[token.replace('~1', '/').replace('~0', '~')]
    return node


def _escape(token):
    return str(token).replace('~', '~0').replace('/', '~1')


def _instance_pointers(instance_path, doc):
    """Expand an instance path template into concrete pointers present in `doc`.

    `instance_path` is a list of steps: a plain string is an object key,
    :data:`_ARRAY` marks an array element, and an :class:`_Extra` marks the keys
    ``additionalProperties`` governs (every key the sibling ``properties`` does
    not name).

    Arrays and ``additionalProperties`` are expanded across *all* their members,
    in document order, and the caller takes the first pointer that resolves. One
    element is enough to prove a constraint fires -- sweeping more would multiply
    the sweep without testing anything new -- but which element that is cannot be
    hardcoded: ``streams[].price`` exists only on ``/streams/1``, so scoring
    index 0 alone would falsely call it unreachable.

    The walk resolves against `doc` step by step rather than building a pointer
    string and asking :func:`_documents.pointer_exists` about it, because the
    expansion steps need to see the container to know what members it has.
    """
    pointers, nodes = [''], [doc]
    for step in instance_path:
        next_pointers, next_nodes = [], []
        for pointer, node in zip(pointers, nodes):
            if step == _ARRAY:
                if isinstance(node, list):
                    for index, child in enumerate(node):
                        next_pointers.append(f'{pointer}/{index}')
                        next_nodes.append(child)
            elif isinstance(step, _Extra):
                if isinstance(node, dict):
                    for key, child in node.items():
                        if key in step.declared:
                            continue
                        next_pointers.append(f'{pointer}/{_escape(key)}')
                        next_nodes.append(child)
            elif isinstance(node, dict) and step in node:
                next_pointers.append(f'{pointer}/{_escape(step)}')
                next_nodes.append(node[step])
        pointers, nodes = next_pointers, next_nodes
    # A constraint attached at the walk's own root (instance_path == []) has no
    # addressable field, so it stays unresolved -- the '' sentinel Locator
    # already documents -- rather than claiming the document root.
    return [p for p in pointers if p]


def _walk(node, schema_pointer, instance_path, out, doc, unmodeled, ref_stack):
    """Recursive constraint collector. See :func:`locators`."""
    if not isinstance(node, dict):
        return

    if '$ref' in node:
        # Follow the reference, but report the constraints under the
        # *definition's* canonical schema pointer, not the caller's. The schema
        # references quantity_unit_entry 12 times and stream_phase once;
        # re-walking the body under each caller's pointer would multiply every
        # constraint inside it by the number of use sites (the over-counting bug
        # this module used to avoid by not following $ref at all). Reporting
        # under the definition's own pointer keeps each constraint counted once
        # and locator_id() unique, while still giving it a real instance pointer
        # from the use site -- locators() merges the duplicates and keeps the
        # first *resolvable* one.
        ref = node['$ref']
        if not ref.startswith('#/'):
            unmodeled.append((schema_pointer, f'$ref -> {ref}'))
            return
        target_pointer = ref[1:]
        if target_pointer in ref_stack:
            # Self-referential or cyclic $ref: the body is already being walked
            # further up this path, so stopping here terminates the recursion
            # without losing any constraint.
            return
        _walk(_resolve_schema_pointer(target_pointer), target_pointer,
              instance_path, out, doc, unmodeled,
              ref_stack | {target_pointer})
        return

    for keyword in sorted(set(node) & _ASSERTION_KEYWORDS
                          - set(KEYWORDS) - _TRAVERSED_KEYWORDS):
        unmodeled.append((schema_pointer, keyword))

    resolved = _instance_pointers(instance_path, doc)
    here = resolved[0] if resolved else ''

    for keyword in KEYWORDS:
        if keyword not in node:
            continue
        value = node[keyword]
        if keyword == 'required':
            for name in value:
                child = _instance_pointers(instance_path + [name], doc)
                out.append(Locator(f'{schema_pointer}/required', 'required',
                                   name, child[0] if child else ''))
        elif keyword == 'additionalProperties':
            # A schema-valued additionalProperties is descended into below; only
            # the `false` form is a constraint in its own right.
            if value is False:
                out.append(Locator(f'{schema_pointer}/additionalProperties',
                                   'additionalProperties', 'false', here))
        elif keyword == 'anyOf':
            out.append(Locator(f'{schema_pointer}/anyOf', 'anyOf',
                               str(len(value)), here))
        else:
            out.append(Locator(f'{schema_pointer}/{keyword}', keyword,
                               json.dumps(value, sort_keys=True), here))

    for name, child in node.get('properties', {}).items():
        _walk(child, f'{schema_pointer}/properties/{name}',
              instance_path + [name], out, doc, unmodeled, ref_stack)
    if 'items' in node:
        _walk(node['items'], f'{schema_pointer}/items',
              instance_path + [_ARRAY], out, doc, unmodeled, ref_stack)
    extra = node.get('additionalProperties')
    if isinstance(extra, dict):
        # additionalProperties governs exactly the keys `properties` does not
        # name, so the instance step is "every other key of this object".
        # Skipping this branch hid nine value-type constraints (purchase_costs,
        # installed_costs, utility_*_results, package_versions, ...), each of
        # which a downstream consumer relies on.
        _walk(extra, f'{schema_pointer}/additionalProperties',
              instance_path + [_Extra(frozenset(node.get('properties', {})))],
              out, doc, unmodeled, ref_stack)
    for group in ('anyOf', 'oneOf', 'allOf'):
        for index, child in enumerate(node.get(group, ())):
            _walk(child, f'{schema_pointer}/{group}/{index}', instance_path,
                  out, doc, unmodeled, ref_stack)
    # 'if'/'then'/'else' (conditional validation, draft-07) apply to the same
    # object the parent node describes -- not to a nested property -- so they
    # are walked at the *same* instance_path, only the schema_pointer grows.
    # Missing this dropped 8 `required` locators (the commit/url pairs on
    # simulator_package and flowsheet_model_package, and the
    # included_in_thermo/registry_id and included_in_thermo/molar_mass pairs
    # on chemicals) that only exist inside these conditional branches.
    for key in ('if', 'then', 'else'):
        if key in node:
            _walk(node[key], f'{schema_pointer}/{key}', instance_path,
                  out, doc, unmodeled, ref_stack)


def _collect():
    """Walk the schema once; cache the locators and the unmodeled keywords."""
    if 'locators' in _CACHE:
        return
    schema = _schema()
    doc = _documents.conforming_document()
    out, unmodeled = [], []
    _walk(schema, '', [], out, doc, unmodeled, frozenset())
    for name, node in schema.get('definitions', {}).items():
        # Definitions are also walked standalone so a constraint in a definition
        # nothing references is still enumerated. A definition that *is*
        # referenced yields the same (schema_pointer, keyword, detail) keys from
        # both passes; the merge below keeps one entry, preferring whichever
        # sighting carries a resolvable instance pointer.
        _walk(node, f'/definitions/{name}', [], out, doc, unmodeled,
              frozenset({f'/definitions/{name}'}))
    merged = {}
    for locator in out:
        key = (locator.schema_pointer, locator.keyword, locator.detail)
        kept = merged.get(key)
        if kept is None or (not kept.instance_pointer
                            and locator.instance_pointer):
            merged[key] = locator
    _CACHE['unmodeled'] = tuple(sorted(set(unmodeled)))
    _CACHE['locators'] = tuple(sorted(
        merged.values(),
        key=lambda l: (l.schema_pointer, l.keyword, l.detail)))


def locators():
    """
    Return every declarative constraint in the schema.

    Returns
    -------
    tuple of Locator
        Sorted by ``(schema_pointer, keyword, detail)``.
    """
    _collect()
    return _CACHE['locators']


def unmodeled_keywords():
    """
    Return constraining keywords the walk met but does not enumerate.

    A non-empty result means the schema gained a keyword :data:`KEYWORDS` does
    not track, so a real constraint is entering the schema with no locator and
    therefore no test. Reported rather than skipped in silence.

    Returns
    -------
    tuple of (str, str)
        ``(schema_pointer, keyword)`` pairs, sorted and deduplicated.
    """
    _collect()
    return _CACHE['unmodeled']


def locator_id(locator):
    """
    Return the stable token a Tier 3 class declares in ``SCHEMA_CONSTRAINTS``.

    Parameters
    ----------
    locator : Locator

    Returns
    -------
    str
        ``'<schema_pointer>#<keyword>:<detail>'``.
    """
    return f'{locator.schema_pointer}#{locator.keyword}:{locator.detail}'


def _instance_value(locator):
    """Return the conforming document's value at `locator`'s instance pointer."""
    if not locator.instance_pointer:
        raise CannotSynthesize(
            f'{locator_id(locator)}: violation needs the conforming value, but '
            'the instance pointer does not resolve')
    return _documents.pointer_get(_documents.conforming_document(),
                                  locator.instance_pointer)


def violating_value(locator):
    """
    Return a value that violates `locator`'s constraint.

    Parameters
    ----------
    locator : Locator

    Returns
    -------
    object
        The violating value, or :data:`_documents.DELETE` for ``required``.

    Raises
    ------
    CannotSynthesize
        For ``anyOf`` and ``additionalProperties``, whose violations depend on
        the surrounding shape and must be hand-written; for a keyword this
        module does not model; and for the keywords that need the conforming
        value (``minItems``, ``uniqueItems``, ``minProperties``) when the
        instance pointer does not resolve.
    """
    keyword = locator.keyword
    if keyword == 'required':
        return _documents.DELETE
    if keyword in ('anyOf', 'additionalProperties'):
        raise CannotSynthesize(
            f'{locator_id(locator)}: needs a hand-written violation')
    detail = json.loads(locator.detail)
    if keyword == 'type':
        if isinstance(detail, list):
            return None
        try:
            return {
                'string': 12345,
                'number': 'not-a-number',
                'integer': 'not-a-number',
                'boolean': 'not-a-boolean',
                'object': 'not-an-object',
                'array': 'not-an-array',
                'null': 'not-null',
            }[detail]
        except KeyError:
            raise CannotSynthesize(
                f'{locator_id(locator)}: unmodeled type name {detail!r}')
    if keyword == 'enum':
        return '__not_in_enum__'
    if keyword == 'const':
        # Any value other than the constant. Kept type-compatible where it can
        # be, so the violation trips `const` rather than a sibling `type`.
        if isinstance(detail, bool):
            return not detail
        if isinstance(detail, str):
            return detail + '__not_const__'
        if isinstance(detail, (int, float)):
            return detail + 1
        return '__not_the_const_value__'
    if keyword == 'pattern':
        return '__does_not_match__'
    if keyword == 'minimum':
        return detail - 1
    if keyword == 'maximum':
        return detail + 1
    if keyword == 'exclusiveMinimum':
        return detail
    if keyword == 'minLength':
        return 'x' * (detail - 1)
    if keyword == 'uniqueItems':
        if not detail:
            raise CannotSynthesize(
                f'{locator_id(locator)}: uniqueItems is false, nothing to violate')
        instance = _instance_value(locator)
        if not instance:
            raise CannotSynthesize(
                f'{locator_id(locator)}: no element to duplicate')
        return [instance[0], instance[0]]
    if keyword == 'minProperties':
        if detail == 1:
            return {}
        instance = _instance_value(locator)
        return dict(list(instance.items())[:detail - 1])
    if keyword == 'minItems':
        if detail == 1:
            return []
        instance = _instance_value(locator)
        return [instance[0]] * (detail - 1)
    raise CannotSynthesize(f'{locator_id(locator)}: unhandled keyword')


def sweepable():
    """
    Return the locators Tier 3's generated sweep can test on its own.

    Returns
    -------
    tuple of Locator
        Those with a resolvable instance pointer, a synthesizable violation,
        and an id absent from :data:`UNREJECTABLE`.

    Notes
    -----
    The decision is made from the locator and :data:`UNREJECTABLE` alone -- the
    validator is never run here. Deciding it by running the validator would be
    the same computation the generated sweep performs, which would make the
    sweep's "the violation is rejected" assertion true by construction. With the
    split static, a constraint that becomes unrejectable (a new sibling ``anyOf``
    branch, a new ``if`` gate) fails the sweep loudly instead of silently
    leaving it.
    """
    if 'sweepable' in _CACHE:
        return _CACHE['sweepable']
    out = []
    for locator in locators():
        if not locator.instance_pointer:
            continue
        if locator_id(locator) in UNREJECTABLE:
            continue
        try:
            violating_value(locator)
        except CannotSynthesize:
            continue
        out.append(locator)
    _CACHE['sweepable'] = tuple(out)
    return _CACHE['sweepable']


def unsweepable():
    """
    Return the locators that must be claimed by a hand-written Tier 3 test.

    Returns
    -------
    tuple of Locator
    """
    if 'unsweepable' in _CACHE:
        return _CACHE['unsweepable']
    sweep = {locator_id(l) for l in sweepable()}
    _CACHE['unsweepable'] = tuple(
        l for l in locators() if locator_id(l) not in sweep)
    return _CACHE['unsweepable']
