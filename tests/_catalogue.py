# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Parse ``sff_checks.md`` -- the authoritative requirement catalogue -- into
records the Tier 3 and Tier 4 coverage meta-tests can diff against.

The catalogue is prose with a strict record shape: a ``### <ID> — <title>``
heading followed by ``- **<Field>:** <text>`` bullets, any of which may wrap over
several lines. Reading it here (rather than restating the IDs in test code) is
what keeps the catalogue authoritative: adding a requirement to the markdown
immediately widens the required set and fails the tier that has no test for it.

Pure library: no assertions, no test classes.
"""

import re
from collections import namedtuple
from pathlib import Path

#: The catalogue file, at the repository root.
CATALOGUE_PATH = Path(__file__).resolve().parents[1] / 'sff_checks.md'

#: One requirement record. Every field is a (possibly empty) string.
Check = namedtuple(
    'Check', 'check_id title statement severity skipped_when enforcement')

_HEADING = re.compile(r'^###\s+(?P<id>[A-Z]+-\d+)\s+—\s+(?P<title>.+?)\s*$')
_ANY_HEADING = re.compile(r'^#{2,}\s')
_FIELD = re.compile(r'^-\s+\*\*(?P<name>[^:*]+):\*\*\s*(?P<text>.*)$')
_LEVEL = re.compile(r'`(error|warning|info)`')
_ENFORCEMENT_CLAUSE_SEP = re.compile(r'(?<=\))\s+\+\s+')
_ENFORCEMENT_KIND = re.compile(r'^(schema|validator)\b', re.IGNORECASE)

_CACHE = {}


def _parse():
    """Parse the catalogue file into ``{check_id: Check}``; cached per process."""
    lines = CATALOGUE_PATH.read_text(encoding='utf-8').splitlines()
    records = {}
    current_id = None
    current_title = ''
    fields = {}
    field_name = None

    def flush():
        if current_id is None:
            return
        records[current_id] = Check(
            check_id=current_id,
            title=current_title,
            statement=fields.get('statement', ''),
            severity=fields.get('severity', ''),
            skipped_when=fields.get('skipped when', ''),
            enforcement=fields.get('enforcement', ''),
        )

    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            flush()
            current_id = heading.group('id')
            current_title = heading.group('title')
            fields = {}
            field_name = None
            continue
        if _ANY_HEADING.match(line):
            # A non-record heading closes the current record.
            flush()
            current_id = None
            fields = {}
            field_name = None
            continue
        if current_id is None:
            continue
        field = _FIELD.match(line)
        if field:
            field_name = field.group('name').strip().lower()
            fields[field_name] = field.group('text').strip()
            continue
        if field_name and line.strip() and not line.lstrip().startswith('- '):
            # A continuation of the field above: join with a single space so a
            # wrapped Enforcement field (MET-04) reads as one sentence.
            fields[field_name] = (fields[field_name] + ' ' + line.strip()).strip()
            continue
        field_name = None
    flush()
    return records


def catalogue():
    """
    Return every catalogue record.

    Returns
    -------
    dict
        ``{check_id: Check}``.
    """
    if 'records' not in _CACHE:
        _CACHE['records'] = _parse()
    return _CACHE['records']


def check_ids():
    """
    Return every catalogue ID, sorted.

    Returns
    -------
    tuple of str
    """
    return tuple(sorted(catalogue()))


def _enforcement_kinds(enforcement_text, check_id=None):
    """
    Return the enforcement-location keywords declared at the head of an
    Enforcement field.

    A plain substring search for ``'schema'``/``'validator'`` over the whole
    field is unsound: a few records' Enforcement text incidentally contains
    one of those words in explanatory prose that is not a designation --
    MET-04 ends with "...so it needs no annual **schema** bump." and STR-04
    with "(JSON **Schema** cannot express ...)". Neither record is
    schema-enforced. The catalogue's own notation for dual enforcement is
    ``schema (...) + validator (...)``, so restricting the match to the first
    word of each clause recovers exactly the declared designations and
    nothing incidental.

    The clause separator is anchored on a closing parenthesis immediately
    before the ``+`` (``_ENFORCEMENT_CLAUSE_SEP``), not on a bare
    whitespace-padded ``+``: the catalogue's dual-enforcement notation always
    closes the first clause's parenthetical before the ``+`` (e.g.
    ``schema (narrowing — ...) + validator (...)``), whereas MET-04's
    Enforcement text contains an arithmetic ``year + 1`` that is not a clause
    boundary at all -- it sits mid-sentence with no preceding ``)``. Anchoring
    on ``)`` is therefore a structural signal, not a coincidence: it is true
    of every genuine dual-enforcement record and false of every incidental
    ``+``.

    A field with no clause whose leading word is ``schema`` or ``validator``
    is not silently unclassified -- it raises, because a record that
    classifies as neither would otherwise vanish from both
    `schema_enforced_ids` and `validator_enforced_ids` without any signal,
    silently narrowing what the Tier 3/4 coverage meta-tests require.

    Parameters
    ----------
    enforcement_text : str
    check_id : str, optional
        Included in the raised message to identify the offending record.

    Returns
    -------
    set of str
        Non-empty subset of ``{'schema', 'validator'}``.

    Raises
    ------
    ValueError
        If no clause's leading word is ``schema`` or ``validator``.
    """
    kinds = set()
    for clause in _ENFORCEMENT_CLAUSE_SEP.split(enforcement_text):
        match = _ENFORCEMENT_KIND.match(clause.strip())
        if match:
            kinds.add(match.group(1).lower())
    if not kinds:
        raise ValueError(
            f'{check_id or "<unknown>"}: Enforcement text has no clause '
            f'designating schema or validator: {enforcement_text!r}')
    return kinds


def schema_enforced_ids():
    """
    Return the IDs the schema is expected to enforce (Tier 3's subject).

    Returns
    -------
    tuple of str
        Sorted IDs whose Enforcement field designates ``schema``.
    """
    return tuple(sorted(
        cid for cid, rec in catalogue().items()
        if 'schema' in _enforcement_kinds(rec.enforcement, cid)))


def validator_enforced_ids():
    """
    Return the IDs ``validate_flowsheet_against_SFF`` is expected to enforce
    (Tier 4's subject).

    Returns
    -------
    tuple of str
        Sorted IDs whose Enforcement field designates ``validator``.
    """
    return tuple(sorted(
        cid for cid, rec in catalogue().items()
        if 'validator' in _enforcement_kinds(rec.enforcement, cid)))


def severity_of(check_id):
    """
    Return a check's declared severity.

    Where a record declares two levels on one line (UNIT-03, UTIL-04), the first
    is returned: it is the level of the record's primary failure mode.

    Parameters
    ----------
    check_id : str

    Returns
    -------
    str
        ``'error'``, ``'warning'`` or ``'info'``.

    Raises
    ------
    KeyError
        If `check_id` is not in the catalogue.
    ValueError
        If the record declares no recognizable level.
    """
    record = catalogue()[check_id]
    match = _LEVEL.search(record.severity)
    if match is None:
        raise ValueError(f'{check_id}: no severity level in {record.severity!r}')
    return match.group(1)


def is_skippable(check_id):
    """
    Return whether the catalogue declares a "Skipped when" condition.

    Parameters
    ----------
    check_id : str

    Returns
    -------
    bool
        False when the field begins with ``never``, else True.
    """
    text = catalogue()[check_id].skipped_when.strip().lower()
    return not text.startswith('never')
