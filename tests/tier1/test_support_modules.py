# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 1: self-tests for the shared support libraries under tests/. These are
# tests OF the test infrastructure -- if the inventory or the catalogue parser is
# wrong, every coverage meta-test built on it is wrong in the same direction and
# would still report green. They declare no COVERS: they cover no pisces_sff
# helper, and the coverage meta-test only requires that every inventory entry is
# claimed, not that every class claims something.

import sys
import unittest
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

import _helper_inventory as inv
import _catalogue as cat
import _documents as docs
import _schema_constraints as sc


class TestHelperInventory(unittest.TestCase):
    """The AST-derived inventory of pisces_sff module-level callables.

    Pinned because every Tier 1 and Tier 2 coverage claim is checked against it:
    an inventory that silently loses a module stops requiring tests for it.
    """

    def test_all_eight_modules_are_inventoried(self):
        """
        Every module named in MODULES contributes at least one callable.

        Expected: the set of module prefixes present in inventory() equals
        {'pisces_sff.' + m for m in MODULES}, with no module missing.
        """
        prefixes = {'.'.join(name.split('.')[:2]) for name in inv.inventory()}
        self.assertEqual(prefixes, {f'pisces_sff.{m}' for m in inv.MODULES})

    def test_inventory_finds_a_known_function_and_a_known_class(self):
        """
        The AST walk picks up both `def` and `class` statements at module level.

        Expected: 'pisces_sff._export.get_composition' (a function) and
        'pisces_sff.exceptions.SFFError' (a class) are both in inventory().
        """
        names = inv.inventory()
        self.assertIn('pisces_sff._export.get_composition', names)
        self.assertIn('pisces_sff.exceptions.SFFError', names)

    def test_nested_functions_are_not_inventoried(self):
        """
        Only module-level callables count; a function defined inside another is
        an implementation detail with no independent contract.

        Expected: no inventory entry contains a name defined inside a function
        body -- checked via the known-nested 'wrapper' name used by
        _export.trace_function_calls.
        """
        self.assertNotIn('pisces_sff._export.wrapper', inv.inventory())

    def test_inventory_is_sorted_and_unique(self):
        """
        The inventory is a stable, deduplicated sequence.

        Expected: inventory() equals sorted(set(inventory())) as a tuple, so a
        coverage diff is reproducible run to run.
        """
        names = inv.inventory()
        self.assertEqual(list(names), sorted(set(names)))

    def test_validator_check_functions_are_exempt_from_tiers_1_and_2(self):
        """
        D1: the _check_* functions are Tier 4's subject, exercised end-to-end
        through validate_flowsheet_against_SFF, so they are not also required as
        Tier 1/Tier 2 helper tests.

        Expected: is_exempt() returns a non-empty reason for
        'pisces_sff._validate._check_unit_id_uniqueness' at tiers 1 and 2, and
        that name is absent from required_for_tier(1) and required_for_tier(2).
        """
        name = 'pisces_sff._validate._check_unit_id_uniqueness'
        for tier in (1, 2):
            self.assertTrue(inv.is_exempt(name, tier))
            self.assertNotIn(name, inv.required_for_tier(tier))

    def test_tier_2_exempts_the_four_harness_helpers(self):
        """
        Spec §3: ensure_environment, export_lock, export_model and
        run_model_export are exempt from Tier 2 but required in Tier 1 (the first
        two) or covered by Tier 6 (the last two).

        Expected: all four are absent from required_for_tier(2), and each carries
        a non-empty reason in exemptions_for_tier(2).
        """
        four = (
            'pisces_sff._harness.ensure_environment',
            'pisces_sff._harness.export_lock',
            'pisces_sff._harness.export_model',
            'pisces_sff._runner.run_model_export',
        )
        exemptions = inv.exemptions_for_tier(2)
        for name in four:
            self.assertNotIn(name, inv.required_for_tier(2))
            self.assertTrue(exemptions.get(name))

    def test_every_exemption_carries_a_non_empty_reason(self):
        """
        An exemption without a reason is an untracked coverage hole.

        Expected: every value in EXEMPT is a non-empty string.
        """
        for key, reason in inv.EXEMPT.items():
            with self.subTest(key=key):
                self.assertTrue(reason and reason.strip(), key)

    def test_exempt_keys_all_name_real_callables(self):
        """
        An exemption for a helper that no longer exists is stale bookkeeping that
        would mask a genuine gap if the name were ever reused.

        Expected: every dotted name in EXEMPT appears in inventory(), except the
        rule-based _check_* exemptions which are generated from the inventory
        itself and therefore cannot be stale.
        """
        names = set(inv.inventory())
        for (dotted, tier) in inv.EXEMPT:
            with self.subTest(name=dotted):
                self.assertIn(dotted, names)


class TestCatalogueParser(unittest.TestCase):
    """The sff_checks.md parser behind the Tier 3 and Tier 4 coverage claims.

    Pinned because the catalogue is prose: a parser that silently drops a record
    makes the tier that consumes it report full coverage of a smaller set.
    """

    def test_catalogue_holds_forty_two_checks(self):
        """
        The catalogue's ID count, pinned as of 2026-08-15.

        Expected: check_ids() has exactly 42 entries. A change here means a
        requirement was added or removed and the Tier 3/4 coverage sets moved.
        """
        self.assertEqual(len(cat.check_ids()), 42)

    def test_nine_ids_are_schema_enforced(self):
        """
        Spec §3: the schema-enforced set is the nine IDs whose Enforcement field
        mentions `schema`, two of which (UNIT-04, UNIT-05) are schema+validator.

        Expected: schema_enforced_ids() equals exactly MET-01, MET-05, MET-06,
        UNIT-04, UNIT-05, STR-11, STR-12, CHEM-02, UTIL-05.
        """
        self.assertEqual(set(cat.schema_enforced_ids()), {
            'MET-01', 'MET-05', 'MET-06', 'UNIT-04', 'UNIT-05',
            'STR-11', 'STR-12', 'CHEM-02', 'UTIL-05'})

    def test_thirty_five_ids_are_validator_enforced(self):
        """
        Spec §3: the validator-enforced set is 35 IDs, already including XREF-01.

        Expected: validator_enforced_ids() has 35 entries and contains XREF-01,
        UNIT-04 and UNIT-05 (the last two being schema+validator).
        """
        ids = cat.validator_enforced_ids()
        self.assertEqual(len(ids), 35)
        for expected in ('XREF-01', 'UNIT-04', 'UNIT-05'):
            self.assertIn(expected, ids)

    def test_severity_is_read_from_the_record(self):
        """
        Severity drives Tier 4's assertion that a check has not been silently
        downgraded, so it must come from the catalogue, not from the code.

        Expected: MET-01 is 'error', MET-03 is 'warning', CHEM-05 is 'info'.
        """
        self.assertEqual(cat.severity_of('MET-01'), 'error')
        self.assertEqual(cat.severity_of('MET-03'), 'warning')
        self.assertEqual(cat.severity_of('CHEM-05'), 'info')

    def test_dual_severity_records_take_the_first_level(self):
        """
        UNIT-03 declares `error` for a missing unit and `warning` for an orphan
        key on one line; the first is the primary failure mode.

        Expected: severity_of('UNIT-03') is 'error'.
        """
        self.assertEqual(cat.severity_of('UNIT-03'), 'error')

    def test_never_skipped_checks_are_marked_unskippable(self):
        """
        Tier 4 only demands a skip case where the catalogue declares one.

        Expected: is_skippable('MET-01') is False ("Skipped when: never"), and
        is_skippable('MET-04') is True ("Skipped when: TEA_year absent").
        """
        self.assertFalse(cat.is_skippable('MET-01'))
        self.assertTrue(cat.is_skippable('MET-04'))

    def test_wrapped_enforcement_field_is_joined(self):
        """
        MET-04's Enforcement field wraps over three source lines; a parser that
        stops at the first newline would lose the helper name for other records
        the same way.

        Expected: MET-04's enforcement text contains both the helper name
        '_check_tea_year_plausible' and the trailing words 'current' and 'year',
        proving the continuation lines were joined.
        """
        text = cat.catalogue()['MET-04'].enforcement
        self.assertIn('_check_tea_year_plausible', text)
        self.assertIn('current', text)
        self.assertIn('year', text)

    def test_non_check_headings_are_not_parsed_as_checks(self):
        """
        sff_checks.md carries prose `###` headings ("Severity levels", "Default
        tolerances") that are not requirement records.

        Expected: every key in catalogue() matches the ID pattern, so no prose
        heading leaks into the coverage sets.
        """
        import re
        for key in cat.catalogue():
            with self.subTest(key=key):
                self.assertRegex(key, r'^[A-Z]+-\d+$')

    def test_every_record_has_a_severity_and_an_enforcement(self):
        """
        A record missing either field cannot be assigned to Tier 3 or Tier 4.

        Expected: all 42 records have non-empty severity and enforcement text.
        """
        for check_id, record in cat.catalogue().items():
            with self.subTest(check_id=check_id):
                self.assertTrue(record.severity)
                self.assertTrue(record.enforcement)

    def test_every_check_id_is_schema_or_validator_enforced(self):
        """
        Union invariant: no catalogue ID is classified into neither set.

        A classification bug that drops a record from both
        `schema_enforced_ids()` and `validator_enforced_ids()` (rather than
        raising) would silently narrow what the Tier 3/4 coverage meta-tests
        require, with no test noticing. Pinning the union against the full ID
        set catches that even if a future record's Enforcement wording
        happens to slip past `_enforcement_kinds` without raising.

        Expected: the union of schema_enforced_ids() and
        validator_enforced_ids() equals check_ids() exactly (42 IDs, no
        fewer, no extras).
        """
        union = set(cat.schema_enforced_ids()) | set(cat.validator_enforced_ids())
        self.assertEqual(union, set(cat.check_ids()))

    def test_unclassifiable_enforcement_text_raises(self):
        """
        `_enforcement_kinds` must fail loudly, not vanish the record, when no
        clause's leading word is `schema` or `validator`.

        Expected: calling `_enforcement_kinds` directly on prose that never
        leads a clause with either keyword (e.g. "Enforced through the
        schema layer.", where "schema" is not the first word) raises
        ValueError.
        """
        with self.assertRaises(ValueError):
            cat._enforcement_kinds('Enforced through the schema layer.', 'FAKE-00')

    def test_arithmetic_plus_does_not_split_the_enforcement_clause(self):
        """
        MET-04's Enforcement text contains "...current calendar year + 1...",
        an arithmetic plus, not the catalogue's dual-enforcement separator.
        The separator is anchored on a closing parenthesis immediately before
        the `+` (the catalogue's actual dual-enforcement notation is
        `schema (...) + validator (...)`), which is structurally absent here
        since "year" has no preceding `)`.

        Expected: `_enforcement_kinds` on MET-04's own enforcement text
        returns exactly {'validator'}, not a set poisoned by an extra clause
        starting with "1,".
        """
        text = cat.catalogue()['MET-04'].enforcement
        self.assertIn('+', text)
        self.assertEqual(cat._enforcement_kinds(text, 'MET-04'), {'validator'})


# Running the full validator over the base document has to happen in a FRESH
# interpreter, not in this one. Tier 1 installs fake biosteam/thermosteam modules
# into sys.modules (tests/tier1/_export_stub.py) so the export helpers can be
# tested without the real simulator; with that stub in place, _validate's
# `from thermosteam.units_of_measure import ureg` fails, `_unit_is_parseable`
# returns False for every string, and QU-02/UTIL-03 report spurious failures
# against a perfectly good document. Whether the stub is loaded depends on which
# other test modules pytest collected first, so an in-process check would also be
# order-dependent. A child process sees the real thermosteam and gives the honest
# answer either way.
_VALIDATE_IN_CLEAN_PROCESS = r'''
import importlib.util, json, sys, tempfile

tests_root, validate_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, tests_root)
import _documents

spec = importlib.util.spec_from_file_location('sff_validate_subprocess',
                                              validate_path)
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)

with tempfile.TemporaryDirectory() as tmp:
    path = _documents.write_temp(_documents.conforming_document(), tmp)
    is_valid, results = validate.validate_flowsheet_against_SFF(str(path))

with open(out_path, 'w', encoding='utf-8') as handle:
    json.dump({'is_valid': is_valid,
               'results': [list(r[:4]) for r in results]}, handle)
'''


# The child imports thermosteam, which is slow and JIT-heavy on a cold numba
# cache -- generous but finite, so a hung child fails the test instead of
# hanging the whole suite.
_VALIDATE_SUBPROCESS_TIMEOUT_S = 300


def _validate_conforming_document_in_a_clean_process(case):
    """Validate the base document in a child interpreter; return the report as
    ``{'is_valid': bool, 'results': [[check_id, severity, status, message], ...]}``."""
    import json
    import subprocess
    import tempfile
    validate_path = TESTS_ROOT.parent / 'pisces_sff' / '_validate.py'
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / 'report.json'
        try:
            completed = subprocess.run(
                [sys.executable, '-c', _VALIDATE_IN_CLEAN_PROCESS,
                 str(TESTS_ROOT), str(validate_path), str(out_path)],
                capture_output=True, text=True,
                timeout=_VALIDATE_SUBPROCESS_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            case.fail(
                'validator subprocess timed out after '
                f'{_VALIDATE_SUBPROCESS_TIMEOUT_S}s running: {exc.cmd!r}')
        case.assertEqual(completed.returncode, 0,
                         f'validator subprocess failed:\n{completed.stderr}')
        with out_path.open('r', encoding='utf-8') as handle:
            return json.load(handle)


class TestConformingDocument(unittest.TestCase):
    """The compact SFF document Tiers 3 and 4 mutate.

    Pinned here rather than in the tiers that use it: if the base document stops
    conforming, every mutation test built on it fails for the wrong reason, and
    the resulting noise hides the one real regression.
    """

    def test_document_has_the_four_required_top_level_sections(self):
        """
        The schema requires metadata, units, streams and utilities at the root.

        Expected: all four keys are present in conforming_document().
        """
        doc = docs.conforming_document()
        for key in ('metadata', 'units', 'streams', 'utilities'):
            self.assertIn(key, doc)

    def test_each_call_returns_an_independent_copy(self):
        """
        Tiers mutate the document in place; a shared object would leak a mutation
        from one test into the next and make failures order-dependent.

        Expected: mutating one returned document leaves a second call unaffected.
        """
        first = docs.conforming_document()
        first['streams'][0]['id'] = 'MUTATED'
        self.assertNotEqual(docs.conforming_document()['streams'][0]['id'],
                            'MUTATED')

    def test_pointer_get_reaches_a_nested_scalar(self):
        """
        JSON pointers address the fields Tier 3 mutates.

        Expected: pointer_get(doc, '/streams/0/stream_properties/pressure')
        returns 101325.0.
        """
        doc = docs.conforming_document()
        self.assertEqual(
            docs.pointer_get(doc, '/streams/0/stream_properties/pressure'),
            101325.0)

    def test_pointer_set_replaces_only_the_addressed_field(self):
        """
        A mutation must be single-field, or a rejection cannot be attributed.

        Expected: after pointer_set on pressure, pressure is the new value and
        temperature is unchanged.
        """
        doc = docs.conforming_document()
        docs.pointer_set(doc, '/streams/0/stream_properties/pressure', 0)
        props = doc['streams'][0]['stream_properties']
        self.assertEqual(props['pressure'], 0)
        self.assertEqual(props['temperature'], 298.15)

    def test_pointer_delete_removes_the_addressed_field(self):
        """
        Deletion is how a `required` constraint is violated.

        Expected: after pointer_delete on '/metadata/TEA_currency', the key is
        absent and pointer_exists returns False.
        """
        doc = docs.conforming_document()
        docs.pointer_delete(doc, '/metadata/TEA_currency')
        self.assertNotIn('TEA_currency', doc['metadata'])
        self.assertFalse(docs.pointer_exists(doc, '/metadata/TEA_currency'))

    def test_pointer_exists_is_false_for_an_unreachable_path(self):
        """
        Tier 3's sweep uses this to report locators the base document cannot
        reach, rather than skipping them silently.

        Expected: pointer_exists(doc, '/utilities/other_utilities/0/price') is
        False -- the document declares no other_utilities.
        """
        doc = docs.conforming_document()
        self.assertFalse(
            docs.pointer_exists(doc, '/utilities/other_utilities/0/price'))

    def test_pointer_exists_raises_on_a_malformed_pointer(self):
        """
        A pointer missing its leading '/' is a programmer typo, not a locator
        that is legitimately absent from the document -- conflating the two
        would let the generated sweep score a malformed pointer as "covered".

        Expected: pointer_exists(doc, 'metadata/TEA_currency') raises
        ValueError instead of returning False.
        """
        doc = docs.conforming_document()
        with self.assertRaises(ValueError):
            docs.pointer_exists(doc, 'metadata/TEA_currency')

    def test_pointer_resolution_treats_a_digit_string_dict_key_as_a_key(self):
        """
        RFC 6901 resolves a token against the *container's* type: a dict key
        that looks like an integer (or is zero-padded, e.g. '01') must stay a
        string key, never be coerced into a list index just because the token
        is all digits.

        Expected: against a dict keyed '01' (string) and 1 (int), pointer_get
        on '/container/01' returns the string-keyed value, pointer_set on the
        same pointer changes only that entry, and the int-keyed entry 1 is
        left untouched throughout.
        """
        doc = {'container': {'01': 'zero-one', 1: 'do-not-touch'}}
        self.assertEqual(docs.pointer_get(doc, '/container/01'), 'zero-one')
        docs.pointer_set(doc, '/container/01', 'changed')
        self.assertEqual(doc['container']['01'], 'changed')
        self.assertEqual(doc['container'][1], 'do-not-touch')
        docs.pointer_delete(doc, '/container/01')
        self.assertNotIn('01', doc['container'])
        self.assertEqual(doc['container'][1], 'do-not-touch')

    def test_pointer_resolution_still_indexes_a_list_by_position(self):
        """
        Pins that resolving a token against the container's type does not
        regress the ordinary case: every pointer this document's own mutation
        tests use addresses a list by numeric position.

        Expected: pointer_get(doc, '/streams/1/id') returns 'product', the
        second entry of the streams list.
        """
        doc = docs.conforming_document()
        self.assertEqual(docs.pointer_get(doc, '/streams/1/id'), 'product')

    def test_mutated_returns_a_document_with_one_change(self):
        """
        The convenience wrapper Tier 3 builds each matched pair from.

        Expected: mutated('/metadata/TEA_year', 'not-a-number') has that string
        at TEA_year while a fresh conforming_document() still has 2018.
        """
        doc = docs.mutated('/metadata/TEA_year', 'not-a-number')
        self.assertEqual(doc['metadata']['TEA_year'], 'not-a-number')
        self.assertEqual(docs.conforming_document()['metadata']['TEA_year'], 2018)

    def test_mutated_with_the_DELETE_sentinel_removes_the_field(self):
        """
        `required` violations need removal, not replacement.

        Expected: mutated('/metadata/TEA_currency', DELETE) has no TEA_currency.
        """
        doc = docs.mutated('/metadata/TEA_currency', docs.DELETE)
        self.assertNotIn('TEA_currency', doc['metadata'])

    def test_write_temp_produces_a_readable_json_file(self):
        """
        Both validators take file paths, so the fixture must be able to land on
        disk.

        Expected: write_temp returns an existing path whose JSON round-trips to
        an equal document.
        """
        import json
        import tempfile
        doc = docs.conforming_document()
        with tempfile.TemporaryDirectory() as tmp:
            path = docs.write_temp(doc, tmp)
            self.assertTrue(path.exists())
            with path.open('r', encoding='utf-8') as handle:
                self.assertEqual(json.load(handle), doc)

    def test_the_base_document_conforms_to_schema_and_every_check(self):
        """
        The guard that keeps Tiers 3 and 4 honest: run the REAL two-layer
        validator over the base document. Tier 3 asserts that a mutation is
        rejected and Tier 4 that a mutation makes a check fire; both readings
        are only meaningful if the unmutated document is clean to begin with.

        The three skips are structural properties of a compact flowsheet, not
        slack: STR-03 needs a doubly-isolated stream, STR-13 needs a stream
        carrying a zero flow scalar, and CHEM-04 needs index-based
        stoichiometry -- none of which a two-stream, id-keyed document has.

        Expected: is_valid is True, no result has status 'fail' at any severity,
        and the non-'pass' results are exactly {STR-03, STR-13, CHEM-04}, all of
        them 'skip'.
        """
        report = _validate_conforming_document_in_a_clean_process(self)
        failures = [r for r in report['results'] if r[2] == 'fail']
        self.assertEqual(failures, [], f'unexpected failures: {failures}')
        self.assertTrue(report['is_valid'])
        non_pass = [r for r in report['results'] if r[2] != 'pass']
        self.assertEqual({r[0] for r in non_pass},
                         {'STR-03', 'STR-13', 'CHEM-04'})
        for check_id, _severity, status, _message in non_pass:
            with self.subTest(check_id=check_id):
                self.assertEqual(status, 'skip')


class TestSchemaConstraintLocators(unittest.TestCase):
    """The enumeration of declarative constraints in sff_schema.json.

    This is Tier 3's second coverage axis. Pinned because an enumerator that
    misses a keyword lets a constraint enter the schema untested while the tier
    still reports complete coverage.
    """

    def test_all_eleven_keywords_are_tracked(self):
        """
        Spec §3 names the keyword set Tier 3 must cover.

        Expected: KEYWORDS is exactly required, enum, type, pattern, minimum,
        maximum, exclusiveMinimum, minLength, minItems, anyOf,
        additionalProperties.
        """
        self.assertEqual(set(sc.KEYWORDS), {
            'required', 'enum', 'type', 'pattern', 'minimum', 'maximum',
            'exclusiveMinimum', 'minLength', 'minItems', 'anyOf',
            'additionalProperties'})

    def test_locator_count_matches_the_measured_schema(self):
        """
        The constraint count, measured 2026-08-15 against schema v0.0.12.

        Expected: 265 locators. A change means the schema gained or lost a
        constraint; update this number in the same commit as the schema edit.
        """
        self.assertEqual(len(sc.locators()), 265)

    def test_keyword_breakdown_matches_the_measured_schema(self):
        """
        Per-keyword counts, measured 2026-08-15.

        Expected: type 156, required 76, exclusiveMinimum 6, minimum 6,
        maximum 5, pattern 4, anyOf 3, minItems 3, additionalProperties 3,
        minLength 2, enum 1.
        """
        import collections
        counts = collections.Counter(loc.keyword for loc in sc.locators())
        self.assertEqual(dict(counts), {
            'type': 156, 'required': 76, 'exclusiveMinimum': 6, 'minimum': 6,
            'maximum': 5, 'pattern': 4, 'anyOf': 3, 'minItems': 3,
            'additionalProperties': 3, 'minLength': 2, 'enum': 1})

    def test_a_known_catalogue_constraint_is_located(self):
        """
        STR-11's exclusiveMinimum on stream pressure must be enumerated, and its
        instance pointer must address the conforming document's field.

        Expected: exactly one locator with keyword 'exclusiveMinimum' whose
        instance_pointer is '/streams/0/stream_properties/pressure'.
        """
        matches = [l for l in sc.locators()
                   if l.keyword == 'exclusiveMinimum'
                   and l.instance_pointer == '/streams/0/stream_properties/pressure']
        self.assertEqual(len(matches), 1)

    def test_locator_ids_are_unique(self):
        """
        The id is the token a Tier 3 class declares in SCHEMA_CONSTRAINTS; a
        collision would let one claim silently satisfy two constraints.

        Expected: locator_id() yields 265 distinct strings.
        """
        ids = [sc.locator_id(l) for l in sc.locators()]
        self.assertEqual(len(set(ids)), len(ids))

    def test_violating_value_for_exclusive_minimum_is_the_boundary(self):
        """
        exclusiveMinimum: 0 is violated by exactly 0, which is also the value
        STR-11's own test uses.

        Expected: violating_value for the stream-pressure locator is 0.
        """
        loc = [l for l in sc.locators()
               if l.instance_pointer == '/streams/0/stream_properties/pressure'
               and l.keyword == 'exclusiveMinimum'][0]
        self.assertEqual(sc.violating_value(loc), 0)

    def test_violating_value_for_required_is_the_delete_sentinel(self):
        """
        A `required` constraint is violated by removing the property.

        Expected: violating_value returns _documents.DELETE for any required
        locator.
        """
        import _documents
        loc = [l for l in sc.locators() if l.keyword == 'required'][0]
        self.assertIs(sc.violating_value(loc), _documents.DELETE)

    def test_anyof_cannot_be_synthesized(self):
        """
        anyOf and additionalProperties: false need a hand-written violation; the
        enumerator must say so rather than guess.

        Expected: violating_value raises CannotSynthesize for every anyOf
        locator.
        """
        for loc in [l for l in sc.locators() if l.keyword == 'anyOf']:
            with self.subTest(locator=sc.locator_id(loc)):
                with self.assertRaises(sc.CannotSynthesize):
                    sc.violating_value(loc)

    def test_sweepable_and_unsweepable_partition_the_locators(self):
        """
        Every locator is either machine-sweepable or hand-claimed; none may fall
        between, which is what would create a silent gap.

        Expected: the two sets are disjoint and together account for all 265.
        """
        sweep = {sc.locator_id(l) for l in sc.sweepable()}
        hand = {sc.locator_id(l) for l in sc.unsweepable()}
        self.assertEqual(sweep & hand, set())
        self.assertEqual(len(sweep | hand), len(sc.locators()))


if __name__ == '__main__':
    unittest.main()
