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


# The exact hand-claimed bucket, measured 2026-08-15 against schema v0.0.12.
# Pinned as a *set*, not just a count: Task 15 requires every entry here to be
# claimed by a hand-written test, and Task 14's generated sweep covers everything
# not here. A locator drifting sweepable -> unsweepable would otherwise silently
# shrink the sweep while the suite stayed green.
#
# Every entry falls in exactly one of three buckets, each justified in the fix
# report accompanying this commit:
#   * 40 whose instance pointer does not resolve in the conforming document (24
#     of them the entirely-unpopulated `other_utilities` block, plus optional
#     fields the compact document does not carry and the schema root's own
#     `type`);
#   * 11 in _schema_constraints.UNREJECTABLE -- real constraints whose
#     single-field violation the schema does not reject (sibling `anyOf`
#     branches, `if` conditions, `const` discriminators);
#   *  5 whose violation cannot be synthesized mechanically (3 `anyOf`,
#     2 `additionalProperties: false`) despite resolving.
_EXPECTED_UNSWEEPABLE = frozenset((
    '/definitions/stream_phase/properties/total_volumetric_flow/type#type:"number"',
    '/properties/chemicals/items/allOf/0/if/properties/included_in_thermo/const#const:true',
    '/properties/chemicals/items/allOf/1/if/properties/included_in_thermo/const#const:false',
    '/properties/chemicals/items/allOf/1/then/required#required:molar_mass',
    '/properties/metadata/additionalProperties/type#type:"string"',
    '/properties/metadata/properties/reproducibility/properties/environment/properties/path/type#type:"string"',
    '/properties/metadata/properties/reproducibility/properties/flowsheet_model_package/allOf/0/if/required#required:commit',
    '/properties/metadata/properties/reproducibility/properties/flowsheet_model_package/anyOf#anyOf:2',
    '/properties/metadata/properties/reproducibility/properties/flowsheet_model_package/anyOf/0/required#required:commit',
    '/properties/metadata/properties/reproducibility/properties/flowsheet_model_package/anyOf/1/required#required:version',
    '/properties/metadata/properties/reproducibility/properties/load_script/properties/path/type#type:"string"',
    '/properties/metadata/properties/reproducibility/properties/simulator_package/allOf/0/if/required#required:commit',
    '/properties/metadata/properties/reproducibility/properties/simulator_package/anyOf#anyOf:2',
    '/properties/metadata/properties/reproducibility/properties/simulator_package/anyOf/0/required#required:commit',
    '/properties/metadata/properties/reproducibility/properties/simulator_package/anyOf/1/required#required:version',
    '/properties/streams/items/properties/stream_properties/properties/enthalpy_flow/type#type:"number"',
    '/properties/streams/items/properties/stream_properties/properties/total_volumetric_flow/type#type:"number"',
    '/properties/units/items/additionalProperties#additionalProperties:false',
    '/properties/units/items/properties/reactions/items/anyOf#anyOf:2',
    '/properties/units/items/properties/reactions/items/anyOf/0/required#required:equation',
    '/properties/units/items/properties/reactions/items/anyOf/1/required#required:stoichiometry',
    '/properties/units/items/properties/thermo_property_package/additionalProperties/type#type:"string"',
    '/properties/units/items/properties/thermo_property_package/properties/PCF/type#type:"string"',
    '/properties/units/items/properties/thermo_property_package/properties/gamma/type#type:"string"',
    '/properties/units/items/properties/thermo_property_package/properties/mixture/type#type:"string"',
    '/properties/units/items/properties/thermo_property_package/properties/phi/type#type:"string"',
    '/properties/units/items/properties/utility_production_results/additionalProperties/type#type:"number"',
    '/properties/units/items/properties/utility_production_results/type#type:"object"',
    '/properties/utilities/properties/heat_utilities/items/additionalProperties#additionalProperties:false',
    '/properties/utilities/properties/heat_utilities/items/properties/temperature_limit/type#type:"number"',
    '/properties/utilities/properties/other_utilities/items/additionalProperties#additionalProperties:false',
    '/properties/utilities/properties/other_utilities/items/properties/composition/items/properties/component_name/type#type:"string"',
    '/properties/utilities/properties/other_utilities/items/properties/composition/items/properties/mol_fraction/maximum#maximum:1',
    '/properties/utilities/properties/other_utilities/items/properties/composition/items/properties/mol_fraction/minimum#minimum:0',
    '/properties/utilities/properties/other_utilities/items/properties/composition/items/properties/mol_fraction/type#type:"number"',
    '/properties/utilities/properties/other_utilities/items/properties/composition/items/properties/phase/type#type:"string"',
    '/properties/utilities/properties/other_utilities/items/properties/composition/items/required#required:component_name',
    '/properties/utilities/properties/other_utilities/items/properties/composition/items/required#required:mol_fraction',
    '/properties/utilities/properties/other_utilities/items/properties/composition/items/type#type:"object"',
    '/properties/utilities/properties/other_utilities/items/properties/composition/type#type:"array"',
    '/properties/utilities/properties/other_utilities/items/properties/id/type#type:"string"',
    '/properties/utilities/properties/other_utilities/items/properties/pressure/exclusiveMinimum#exclusiveMinimum:0',
    '/properties/utilities/properties/other_utilities/items/properties/pressure/type#type:"number"',
    '/properties/utilities/properties/other_utilities/items/properties/price/type#type:"number"',
    '/properties/utilities/properties/other_utilities/items/properties/quantity_units_for_utility_results/type#type:"string"',
    '/properties/utilities/properties/other_utilities/items/properties/temperature/exclusiveMinimum#exclusiveMinimum:0',
    '/properties/utilities/properties/other_utilities/items/properties/temperature/type#type:"number"',
    '/properties/utilities/properties/other_utilities/items/required#required:composition',
    '/properties/utilities/properties/other_utilities/items/required#required:id',
    '/properties/utilities/properties/other_utilities/items/required#required:pressure',
    '/properties/utilities/properties/other_utilities/items/required#required:quantity_units_for_utility_results',
    '/properties/utilities/properties/other_utilities/items/required#required:temperature',
    '/properties/utilities/properties/other_utilities/items/type#type:"object"',
    '/properties/utilities/properties/other_utilities/type#type:"array"',
    '/properties/utilities/properties/power_utilities/items/additionalProperties/type#type:"number"',
    '/type#type:"object"',
))


def _schema_validator():
    """Return a Draft-07 validator bound to the real committed schema."""
    import json
    import jsonschema
    schema = json.loads(sc.SCHEMA_PATH.read_text(encoding='utf-8'))
    return jsonschema.validators.validator_for(schema)(schema)


class TestSchemaConstraintLocators(unittest.TestCase):
    """The enumeration of declarative constraints in sff_schema.json.

    This is Tier 3's second coverage axis. Pinned because an enumerator that
    misses a keyword lets a constraint enter the schema untested while the tier
    still reports complete coverage.
    """

    def test_all_fourteen_keywords_are_tracked(self):
        """
        The keyword set Tier 3 must cover: every keyword sff_schema.json uses to
        constrain an instance. `const`, `uniqueItems` and `minProperties` were
        added 2026-08-15 -- they were real constraints in the schema that the
        original eleven-keyword tuple silently dropped.

        Expected: KEYWORDS is exactly required, enum, const, type, pattern,
        minimum, maximum, exclusiveMinimum, minLength, minItems, uniqueItems,
        minProperties, anyOf, additionalProperties.
        """
        self.assertEqual(set(sc.KEYWORDS), {
            'required', 'enum', 'const', 'type', 'pattern', 'minimum',
            'maximum', 'exclusiveMinimum', 'minLength', 'minItems',
            'uniqueItems', 'minProperties', 'anyOf', 'additionalProperties'})

    def test_no_constraining_keyword_is_left_unmodeled(self):
        """
        A constraining keyword outside KEYWORDS would enter the schema with no
        locator and therefore no test. The walk reports such keywords instead of
        skipping them, so this test is what makes a fifteenth one visible.

        Expected: unmodeled_keywords() is empty.
        """
        self.assertEqual(sc.unmodeled_keywords(), ())

    def test_locator_count_matches_the_measured_schema(self):
        """
        The constraint count, re-measured 2026-08-15 against schema v0.0.12
        after the walk learned to descend into schema-valued
        `additionalProperties`, to follow `$ref`, and to track four more
        keywords.

        Expected: 278 locators. A change means the schema gained or lost a
        constraint; update this number in the same commit as the schema edit.
        """
        self.assertEqual(len(sc.locators()), 278)

    def test_keyword_breakdown_matches_the_measured_schema(self):
        """
        Per-keyword counts, re-measured 2026-08-15.

        Expected: type 165, required 76, exclusiveMinimum 6, minimum 6,
        maximum 5, pattern 4, anyOf 3, minItems 3, additionalProperties 3,
        const 2, minLength 2, enum 1, minProperties 1, uniqueItems 1.
        """
        import collections
        counts = collections.Counter(loc.keyword for loc in sc.locators())
        self.assertEqual(dict(counts), {
            'type': 165, 'required': 76, 'exclusiveMinimum': 6, 'minimum': 6,
            'maximum': 5, 'pattern': 4, 'anyOf': 3, 'minItems': 3,
            'additionalProperties': 3, 'const': 2, 'minLength': 2, 'enum': 1,
            'minProperties': 1, 'uniqueItems': 1})

    def test_type_locator_count_matches_the_schema_text(self):
        """
        Every `"type":` key in the raw schema text is a value-type constraint,
        so the walk's `type` count must equal the literal count. This is the
        assertion that caught nine constraints hiding behind schema-valued
        `additionalProperties` (purchase_costs, installed_costs, the two
        utility_*_results maps, package_versions, ...).

        Expected: the number of `type` locators equals the number of `"type":`
        occurrences in sff_schema.json (165).
        """
        text = sc.SCHEMA_PATH.read_text(encoding='utf-8')
        self.assertEqual(len([l for l in sc.locators() if l.keyword == 'type']),
                         text.count('"type":'))

    def test_additional_properties_value_constraints_are_enumerated(self):
        """
        A schema-valued `additionalProperties` constrains every key the sibling
        `properties` does not name -- "purchase costs must be numbers" is
        exactly such a constraint, and a downstream consumer relies on it.

        Expected: the `type` locator under
        /properties/units/items/properties/purchase_costs/additionalProperties
        exists and addresses the conforming document's own cost entry.
        """
        matches = [l for l in sc.locators()
                   if l.schema_pointer == ('/properties/units/items/properties'
                                           '/purchase_costs/additionalProperties'
                                           '/type')]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].instance_pointer,
                         '/units/0/purchase_costs/Heat exchanger')

    def test_a_ref_target_constraint_carries_a_use_site_instance_pointer(self):
        """
        `$ref` targets are reported under the definition's canonical schema
        pointer -- so each is counted exactly once, however many use sites there
        are -- but must still resolve to a real field, or all 24 constraints in
        stream_phase and quantity_unit_entry are stranded in the hand-written
        bucket for no reason.

        Expected: stream_phase's `mol_fraction` maximum is enumerated once,
        under /definitions/stream_phase/..., addressing the conforming
        document's phase composition.
        """
        matches = [l for l in sc.locators()
                   if l.schema_pointer == ('/definitions/stream_phase/properties'
                                           '/composition/items/properties'
                                           '/mol_fraction/maximum')]
        self.assertEqual(len(matches), 1)
        self.assertEqual(
            matches[0].instance_pointer,
            '/streams/0/stream_properties/phases/l/composition/0/mol_fraction')

    def test_array_expansion_finds_a_field_populated_on_a_later_element(self):
        """
        Sweeping one array element is enough to prove a constraint fires, but
        *which* element cannot be hardcoded: `price` is absent from
        /streams/0 and present on /streams/1, so scoring index 0 alone would
        falsely call the constraint unreachable.

        Expected: the streams `price` type locator resolves to '/streams/1/price'.
        """
        matches = [l for l in sc.locators()
                   if l.schema_pointer == ('/properties/streams/items/properties'
                                           '/price/type')]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].instance_pointer, '/streams/1/price')

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
        collision would let one claim silently satisfy two constraints. Following
        `$ref` makes this load-bearing: a definition reached from twelve use
        sites must still yield one id per constraint.

        Expected: locator_id() yields 278 distinct strings.
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

    def test_unsynthesizable_cases_raise_cannot_synthesize_not_a_stray_error(self):
        """
        `violating_value` is the sweep's only documented failure mode; a
        ValueError from an unresolved pointer or a KeyError from an unmodeled
        type name would crash the generator instead of routing the locator to
        the hand-written bucket. Both paths are unreachable in today's schema
        and are exactly the paths a schema edit would hit first.

        Expected: CannotSynthesize for a minItems/uniqueItems/minProperties
        locator with no instance pointer, and for an unmodeled type name.
        """
        for keyword, detail in (('minItems', '2'), ('uniqueItems', 'true'),
                                ('minProperties', '2')):
            loc = sc.Locator('/fake', keyword, detail, '')
            with self.subTest(keyword=keyword):
                with self.assertRaises(sc.CannotSynthesize):
                    sc.violating_value(loc)
        with self.assertRaises(sc.CannotSynthesize):
            sc.violating_value(sc.Locator('/fake', 'type', '"nonesuch"',
                                          '/metadata/TEA_year'))

    def test_sweepable_and_unsweepable_partition_the_locators(self):
        """
        Every locator is either machine-sweepable or hand-claimed; none may fall
        between, which is what would create a silent gap.

        Expected: the two sets are disjoint and together account for all 278.
        """
        sweep = {sc.locator_id(l) for l in sc.sweepable()}
        hand = {sc.locator_id(l) for l in sc.unsweepable()}
        self.assertEqual(sweep & hand, set())
        self.assertEqual(len(sweep | hand), len(sc.locators()))

    def test_the_split_sizes_and_the_hand_claimed_set_are_pinned(self):
        """
        Pinning only the partition would let a locator migrate sweepable ->
        unsweepable with the suite still green and the generated sweep quietly
        one test smaller. Task 15 also claims each unsweepable id by hand, so
        the set itself -- not just its size -- is the contract.

        Expected: 222 sweepable, and the unsweepable ids are exactly
        _EXPECTED_UNSWEEPABLE (56 entries).
        """
        self.assertEqual(len(sc.sweepable()), 222)
        self.assertEqual({sc.locator_id(l) for l in sc.unsweepable()},
                         set(_EXPECTED_UNSWEEPABLE))

    def test_the_conforming_document_is_accepted_unmutated(self):
        """
        Every matched pair reads "conforming accepted, mutated rejected". The
        first half has to be true independently, or a rejection proves nothing
        about the mutation.

        Expected: the Draft-07 validator reports zero errors on
        conforming_document().
        """
        errors = list(_schema_validator().iter_errors(docs.conforming_document()))
        self.assertEqual([e.message for e in errors], [])

    def test_every_sweepable_violation_is_rejected_by_its_own_keyword(self):
        """
        `sweepable()` decides statically, from UNREJECTABLE, and never runs the
        validator -- otherwise Task 14's "the violation is rejected" assertion
        would be true by construction. This test is where that claim is actually
        checked, once, against the real schema: each single-field violation must
        be rejected, and rejected *citing its own keyword*, or the generated
        sweep would prove something other than what it says.

        One locator legitimately trips two keywords: the `roles` item
        `type: "string"` sits beside an `enum` of six strings, so no string can
        violate the type and the synthesized 12345 fails both. `type` is still
        among the cited keywords, which is what is asserted.

        Expected: for all 222 sweepable locators the mutated document has at
        least one error, and the locator's keyword is among the cited
        validators.
        """
        import _documents
        validator = _schema_validator()
        for loc in sc.sweepable():
            with self.subTest(locator=sc.locator_id(loc)):
                doc = _documents.mutated(loc.instance_pointer,
                                         sc.violating_value(loc))
                cited = {e.validator for e in validator.iter_errors(doc)}
                self.assertTrue(cited, 'violation was not rejected at all')
                self.assertIn(loc.keyword, cited)

    def test_every_unrejectable_entry_is_a_real_locator_that_is_not_rejected(self):
        """
        UNREJECTABLE is hand-maintained, so it can go stale in two directions: an
        id that no longer names a locator is dead bookkeeping, and an id whose
        violation the schema *does* now reject is a constraint wrongly kept out
        of the generated sweep.

        Expected: every UNREJECTABLE id names a current locator with a resolvable
        instance pointer, and its synthesized violation leaves the document
        valid.
        """
        import _documents
        validator = _schema_validator()
        by_id = {sc.locator_id(l): l for l in sc.locators()}
        for lid in sorted(sc.UNREJECTABLE):
            with self.subTest(locator=lid):
                self.assertIn(lid, by_id, 'stale UNREJECTABLE entry')
                loc = by_id[lid]
                self.assertTrue(loc.instance_pointer)
                doc = _documents.mutated(loc.instance_pointer,
                                         sc.violating_value(loc))
                errors = [e.message for e in validator.iter_errors(doc)]
                self.assertEqual(errors, [])

    def test_every_unsweepable_locator_has_a_holding_reason(self):
        """
        A locator excluded from the sweep for no stated reason is an untracked
        coverage hole. There are exactly three admissible reasons.

        Expected: each unsweepable locator either has no resolvable instance
        pointer, or is listed in UNREJECTABLE, or raises CannotSynthesize --
        and the three buckets account for all 56 (40 / 11 / 5).
        """
        import collections
        reasons = collections.Counter()
        for loc in sc.unsweepable():
            lid = sc.locator_id(loc)
            with self.subTest(locator=lid):
                if not loc.instance_pointer:
                    reasons['unresolved_pointer'] += 1
                elif lid in sc.UNREJECTABLE:
                    reasons['unrejectable'] += 1
                else:
                    with self.assertRaises(sc.CannotSynthesize):
                        sc.violating_value(loc)
                    reasons['cannot_synthesize'] += 1
        self.assertEqual(dict(reasons), {'unresolved_pointer': 40,
                                         'unrejectable': 11,
                                         'cannot_synthesize': 5})


if __name__ == '__main__':
    unittest.main()
