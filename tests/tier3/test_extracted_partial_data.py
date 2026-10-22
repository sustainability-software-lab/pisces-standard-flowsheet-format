# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.
#
# Tier 3: tag-conditional relaxations for flowsheets extracted from literature.
# In a file whose metadata.tags contains an extracted-from-* tag, the schema
# accepts the partial data a paper reports: stream_properties with any subset
# of its fields, compositions on a mass basis, purchase-cost correlations
# without CEPCI / power rate / basis units / installation factor, heat and
# other utilities without temperature / pressure / composition, and a
# TEA_year of 'not_reported' / 'not_applicable'. A root-level else restores
# every requirement for all other files, so each previously valid file stays
# valid. Import-light: validates synthetic documents against the committed
# schema via jsonschema, never importing pisces_sff.

import unittest

from jsonschema import Draft7Validator

from tests._gating import skip_if_disabled
from tests.tier3.test_streams_schema import load_schema, minimal_doc

EXTRACTED = ("extracted-from-prose", "extracted-from-image", "extracted-from-table")
# Fields the metadata if/then rules require beside each extracted tag.
TAG_FIELDS = {"prose_type": "journal_article", "image_type": "process_flow_diagram",
              "extraction_method": "manual"}

CORRELATION = {"correlation_type": "power_law", "basis": "Flow rate", "reference_size": 500.0,
               "reference_cost": 2.855e7, "exponent": 0.6}
HEAT_UTILITY = {"id": "steam", "quantity_units_for_utility_results": "kJ/hr"}


def doc_with(tags=None):
    doc = minimal_doc()
    doc["units"][0]["purchase_costs"] = {"Mixer": 1.0e6}
    if tags is not None:
        doc["metadata"]["tags"] = list(tags)
        doc["metadata"].update(TAG_FIELDS)
    return doc


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_disabled(3)

    def setUp(self):
        self.v = Draft7Validator(load_schema())

    def assertAcceptedOnlyWhenExtracted(self, change):
        """`change(doc)` breaks a rule that only extracted files may break: rejected untagged and
        with exported-from-simulator; accepted with each extracted tag and with mixed tags."""
        for tags in (None, ["exported-from-simulator"]):
            doc = doc_with(tags)
            change(doc)
            self.assertFalse(self.v.is_valid(doc), f"accepted with tags={tags}")
        for tags in ([t] for t in EXTRACTED):
            with self.subTest(tags=tags):
                doc = doc_with(tags)
                change(doc)
                self.assertTrue(self.v.is_valid(doc), [e.message for e in self.v.iter_errors(doc)])
        doc = doc_with(["exported-from-simulator", "extracted-from-table"])
        change(doc)
        self.assertTrue(self.v.is_valid(doc), [e.message for e in self.v.iter_errors(doc)])


class TestStreamPropertiesPartial(_Base):
    def test_each_stream_level_field_may_be_omitted(self):
        """Omitting any one of total_mass_flow, total_molar_flow, temperature, pressure, phases → rejected without an extracted tag, accepted with one."""
        for field in ("total_mass_flow", "total_molar_flow", "temperature", "pressure", "phases"):
            with self.subTest(field=field):
                self.assertAcceptedOnlyWhenExtracted(
                    lambda d, f=field: d["streams"][0]["stream_properties"].pop(f))

    def test_only_a_temperature_is_enough(self):
        """stream_properties holding only a temperature → accepted only in extracted files."""
        self.assertAcceptedOnlyWhenExtracted(
            lambda d: d["streams"][0].__setitem__("stream_properties", {"temperature": 350.0}))

    def test_present_fields_keep_their_constraints(self):
        """Optional is not unconstrained: a pressure of 0 (STR-11) or a string mass flow → still rejected in an extracted file."""
        for field, bad in (("pressure", 0), ("total_mass_flow", "10")):
            with self.subTest(field=field):
                doc = doc_with(["extracted-from-prose"])
                doc["streams"][0]["stream_properties"] = {field: bad}
                self.assertFalse(self.v.is_valid(doc))


class TestMassBasisComposition(_Base):
    def _set_comp(self, doc, entry):
        doc["streams"][0]["stream_properties"]["phases"]["l"]["composition"] = [entry]

    def test_mass_fraction_instead_of_mol_fraction(self):
        """A phase component with mass_fraction but no mol_fraction → accepted only in extracted files."""
        self.assertAcceptedOnlyWhenExtracted(
            lambda d: self._set_comp(d, {"component_name": "Water", "mass_fraction": 1.0}))

    def test_a_fraction_is_still_required(self):
        """A component with neither mol_fraction nor mass_fraction → rejected even in an extracted file."""
        doc = doc_with(["extracted-from-prose"])
        self._set_comp(doc, {"component_name": "Water"})
        self.assertFalse(self.v.is_valid(doc))

    def test_mol_fraction_alone_still_accepted_everywhere(self):
        """A component with only mol_fraction → accepted with and without an extracted tag."""
        for tags in (None, ["extracted-from-image"]):
            doc = doc_with(tags)
            self._set_comp(doc, {"component_name": "Water", "mol_fraction": 1.0})
            self.assertTrue(self.v.is_valid(doc), [e.message for e in self.v.iter_errors(doc)])


class TestPartialCostCorrelations(_Base):
    def test_reported_correlation_without_cepci_power_rate_or_units(self):
        """A power_law correlation with basis, size, cost and exponent but no reference_CE_index, power_rate, basis_units or installation_factor → accepted only in extracted files."""
        self.assertAcceptedOnlyWhenExtracted(
            lambda d: d["units"][0].__setitem__("purchase_cost_correlations", {"Mixer": dict(CORRELATION)}))

    def test_power_law_still_needs_cost_and_exponent(self):
        """UNIT-09 schema half unchanged: a power_law without reference_cost → rejected in an extracted file."""
        doc = doc_with(["extracted-from-prose"])
        corr = dict(CORRELATION)
        del corr["reference_cost"]
        doc["units"][0]["purchase_cost_correlations"] = {"Mixer": corr}
        self.assertFalse(self.v.is_valid(doc))

    def test_correlation_stays_closed(self):
        """additionalProperties false unchanged: an unknown key → rejected in an extracted file."""
        doc = doc_with(["extracted-from-prose"])
        doc["units"][0]["purchase_cost_correlations"] = {"Mixer": {**CORRELATION, "year": 2016}}
        self.assertFalse(self.v.is_valid(doc))

    def test_complete_correlation_accepted_everywhere(self):
        """A correlation with every field → accepted with and without an extracted tag."""
        full = {**CORRELATION, "basis_units": "t/hr", "reference_CE_index": 567.5,
                "installation_factor": 1.8, "power_rate": 0.0}
        for tags in (None, ["extracted-from-table"]):
            doc = doc_with(tags)
            doc["units"][0]["purchase_cost_correlations"] = {"Mixer": full}
            self.assertTrue(self.v.is_valid(doc), [e.message for e in self.v.iter_errors(doc)])


class TestPartialUtilities(_Base):
    def test_heat_and_other_utilities_without_conditions(self):
        """A heat or other utility with only id and quantity_units_for_utility_results → accepted only in extracted files."""
        for group in ("heat_utilities", "other_utilities"):
            with self.subTest(group=group):
                self.assertAcceptedOnlyWhenExtracted(
                    lambda d, g=group: d["utilities"].__setitem__(g, [dict(HEAT_UTILITY)]))

    def test_utility_id_and_units_still_required(self):
        """id and quantity_units_for_utility_results stay required in an extracted file."""
        for missing in ("id", "quantity_units_for_utility_results"):
            with self.subTest(missing=missing):
                doc = doc_with(["extracted-from-prose"])
                util = dict(HEAT_UTILITY)
                del util[missing]
                doc["utilities"]["heat_utilities"] = [util]
                self.assertFalse(self.v.is_valid(doc))


class TestTeaYearNotReported(_Base):
    def test_sentinels_accepted_only_when_extracted(self):
        """TEA_year 'not_reported' or 'not_applicable' → rejected without an extracted tag, accepted with one."""
        for sentinel in ("not_reported", "not_applicable"):
            with self.subTest(sentinel=sentinel):
                self.assertAcceptedOnlyWhenExtracted(
                    lambda d, s=sentinel: d["metadata"].__setitem__("TEA_year", s))

    def test_other_strings_rejected(self):
        """Any other string (e.g. '2016', 'None') → still rejected in an extracted file."""
        for bad in ("2016", "None", ""):
            with self.subTest(bad=bad):
                doc = doc_with(["extracted-from-prose"])
                doc["metadata"]["TEA_year"] = bad
                self.assertFalse(self.v.is_valid(doc))

    def test_tea_year_still_required(self):
        """The key itself stays required in an extracted file."""
        doc = doc_with(["extracted-from-prose"])
        del doc["metadata"]["TEA_year"]
        self.assertFalse(self.v.is_valid(doc))


class TestPreviouslyValidFilesUnchanged(_Base):
    def test_minimal_documents_still_valid(self):
        """The untagged minimal document and its extracted-tagged copy are both accepted (a pure loosening)."""
        for tags in (None, ["extracted-from-prose"]):
            doc = doc_with(tags)
            self.assertTrue(self.v.is_valid(doc), [e.message for e in self.v.iter_errors(doc)])


if __name__ == "__main__":
    unittest.main()
