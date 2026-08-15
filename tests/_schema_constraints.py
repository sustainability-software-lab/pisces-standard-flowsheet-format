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

Pure library: no assertions, no test classes.
"""

import json
from collections import namedtuple
from pathlib import Path

import jsonschema

import _documents

#: The schema under test.
SCHEMA_PATH = (Path(__file__).resolve().parents[1]
               / 'pisces_sff' / 'schema' / 'sff_schema.json')

#: The declarative keywords Tier 3 must cover (spec §3).
KEYWORDS = ('required', 'enum', 'type', 'pattern', 'minimum', 'maximum',
            'exclusiveMinimum', 'minLength', 'minItems', 'anyOf',
            'additionalProperties')

#: One declarative constraint. `instance_pointer` is '' when the constraint
#: governs a field the conforming document does not populate.
Locator = namedtuple('Locator', 'schema_pointer keyword detail instance_pointer')

_CACHE = {}


class CannotSynthesize(Exception):
    """Raised when a constraint's violation cannot be derived mechanically."""


def _schema():
    if 'schema' not in _CACHE:
        _CACHE['schema'] = json.loads(
            SCHEMA_PATH.read_text(encoding='utf-8'))
    return _CACHE['schema']


def _validator():
    if 'validator' not in _CACHE:
        schema = _schema()
        _CACHE['validator'] = jsonschema.validators.validator_for(schema)(schema)
    return _CACHE['validator']


def _instance_pointers(instance_path, doc):
    """Expand an instance path template into concrete pointers present in `doc`.

    `instance_path` is a list of steps where the string ``'[]'`` marks an array
    element. Arrays are expanded to index 0 only: one element is enough to prove
    a constraint fires, and expanding further would multiply the sweep without
    testing anything new.
    """
    pointers = ['']
    for step in instance_path:
        if step == '[]':
            pointers = [f'{p}/0' for p in pointers]
        else:
            token = str(step).replace('~', '~0').replace('/', '~1')
            pointers = [f'{p}/{token}' for p in pointers]
    # The empty string is the document root, not a field-level JSON pointer;
    # _documents.pointer_exists deliberately raises on it rather than treating
    # it as "absent" (it exists precisely to catch a typo'd pointer, and a
    # bare '' is not a typo). A constraint attached at the walk's own root
    # (instance_path == []) has no addressable field to report, so it is left
    # unresolved -- the same '' sentinel Locator already uses for "cannot be
    # resolved" -- rather than asking pointer_exists to adjudicate it.
    return [p for p in pointers if p and _documents.pointer_exists(doc, p)]


def _walk(node, schema_pointer, instance_path, out, doc, definitions):
    """Recursive constraint collector. See :func:`locators`."""
    if not isinstance(node, dict):
        return

    if '$ref' in node:
        # Do not re-walk the referenced definition's body from here: it is a
        # single schema location reused at multiple use sites (11 times for
        # quantity_unit_entry alone), and re-walking it at each site would
        # multiply every constraint inside it by the number of $ref sites --
        # e.g. quantity_unit_entry's one `minItems` would be counted 11 times
        # instead of the single time it actually appears in the schema text.
        # The standalone pass over `definitions` in `locators()` is what
        # enumerates a $ref target's own constraints, exactly once each.
        return

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
              instance_path + [name], out, doc, definitions)
    if 'items' in node:
        _walk(node['items'], f'{schema_pointer}/items',
              instance_path + ['[]'], out, doc, definitions)
    for group in ('anyOf', 'oneOf', 'allOf'):
        for index, child in enumerate(node.get(group, ())):
            _walk(child, f'{schema_pointer}/{group}/{index}', instance_path,
                  out, doc, definitions)
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
                  out, doc, definitions)


def locators():
    """
    Return every declarative constraint in the schema.

    Returns
    -------
    tuple of Locator
        Sorted by ``(schema_pointer, keyword, detail)``.
    """
    if 'locators' in _CACHE:
        return _CACHE['locators']
    schema = _schema()
    doc = _documents.conforming_document()
    out = []
    definitions = schema.get('definitions', {})
    _walk(schema, '', [], out, doc, definitions)
    for name, node in definitions.items():
        # Definitions are also walked standalone so a constraint reachable only
        # through a $ref is still enumerated, even though its instance pointer
        # cannot be resolved from the definition alone.
        _walk(node, f'/definitions/{name}', [], out, doc, definitions)
    _CACHE['locators'] = tuple(sorted(
        set(out), key=lambda l: (l.schema_pointer, l.keyword, l.detail)))
    return _CACHE['locators']


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
        the surrounding shape and must be hand-written.
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
        return {
            'string': 12345,
            'number': 'not-a-number',
            'integer': 'not-a-number',
            'boolean': 'not-a-boolean',
            'object': 'not-an-object',
            'array': 'not-an-array',
        }[detail]
    if keyword == 'enum':
        return '__not_in_enum__'
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
    if keyword == 'minItems':
        if detail == 1:
            return []
        instance = _documents.pointer_get(_documents.conforming_document(),
                                          locator.instance_pointer)
        return [instance[0]] * (detail - 1)
    raise CannotSynthesize(f'{locator_id(locator)}: unhandled keyword')


def sweepable():
    """
    Return the locators Tier 3's generated sweep can test on its own.

    Returns
    -------
    tuple of Locator
        Those with a resolvable instance pointer, a synthesizable violation,
        and a violation the schema genuinely rejects.

    Notes
    -----
    A synthesizable violation is not automatically a real one: a `required`
    name that is also satisfiable through a sibling ``anyOf`` branch, or that
    only matters inside an ``if`` condition, can be deleted without the
    document being rejected (e.g. ``simulator_package`` requires ``commit`` OR
    ``version``; deleting ``commit`` alone leaves ``version`` to satisfy the
    ``anyOf``). Calling such a locator sweepable would generate a Tier 3 test
    that asserts a rejection that never happens, so each candidate is checked
    against the real schema before being counted as sweepable.
    """
    out = []
    for locator in locators():
        if not locator.instance_pointer:
            continue
        try:
            value = violating_value(locator)
        except CannotSynthesize:
            continue
        doc = _documents.mutated(locator.instance_pointer, value)
        if _validator().is_valid(doc):
            continue
        out.append(locator)
    return tuple(out)


def unsweepable():
    """
    Return the locators that must be claimed by a hand-written Tier 3 test.

    Returns
    -------
    tuple of Locator
    """
    sweep = {locator_id(l) for l in sweepable()}
    return tuple(l for l in locators() if locator_id(l) not in sweep)
