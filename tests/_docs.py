# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""Minimal SFF document that passes BOTH the schema gate and every validator
check, plus tiny nested-path mutators. Tiers 3 and 4 start from valid_doc() and
break exactly one thing, so this document being fully valid is asserted directly
in tests/tier4/test_docs_fixture.py."""

import copy


def _base():
    return {
        "metadata": {
            "sff_version": "0.0.12",
            "TEA_currency": "USD",
            "TEA_year": 2020,
            "process_simulator": {"name": "BioSTEAM", "version": "2.46.1"},
            "feedstocks": [{"stream_id": "feed"}],
            "products": [{"stream_id": "prod"}],
        },
        "quantity_units_global": {
            "temperature": {"aliases": ["temperature"], "quantity_units": "K"},
            "pressure": {"aliases": ["pressure"], "quantity_units": "Pa"},
            "mass_flow": {"aliases": ["total_mass_flow"],
                          "quantity_units": "kg/hr"},
            "molar_flow": {"aliases": ["total_molar_flow"],
                           "quantity_units": "kmol/hr"},
            "molar_mass": {"aliases": ["molar_mass"], "quantity_units": "g/mol"},
        },
        "units": [{"id": "U1", "unit_type": "Mixer"}],
        "streams": [
            {
                "id": "feed", "source_unit_id": "None", "sink_unit_id": "U1",
                # STR-04/STR-05: topology role "input" agrees with connectivity
                # (source == boundary, sink == real unit). MET-03: designation
                # role "feedstock" agrees with metadata.feedstocks below.
                "roles": ["input", "feedstock"],
                "stream_properties": {
                    "total_mass_flow": 46.07, "total_molar_flow": 1.0,
                    "temperature": 300.0, "pressure": 101325.0,
                    "phases": {"l": {"total_molar_flow": 1.0, "composition": [
                        {"component_name": "Ethanol", "mol_fraction": 1.0}]}},
                    "composition": [{"component_name": "Ethanol",
                                     "mol_fraction": 1.0}],
                },
            },
            {
                "id": "prod", "source_unit_id": "U1", "sink_unit_id": "None",
                # STR-04/STR-05: topology role "output" agrees with connectivity
                # (source == real unit, sink == boundary). MET-03: designation
                # role "product" agrees with metadata.products below.
                "roles": ["output", "product"],
                "stream_properties": {
                    "total_mass_flow": 46.07, "total_molar_flow": 1.0,
                    "temperature": 300.0, "pressure": 101325.0,
                    "phases": {"l": {"total_molar_flow": 1.0, "composition": [
                        {"component_name": "Ethanol", "mol_fraction": 1.0}]}},
                    "composition": [{"component_name": "Ethanol",
                                     "mol_fraction": 1.0}],
                },
            },
        ],
        "chemicals": [
            # included_in_thermo: False (schema then-branch requires molar_mass,
            # not registry_id) keeps the fixture minimal -- no need for a CAS/
            # SMILES registry id just to satisfy the schema's if/then.
            {"id": "Ethanol", "index": 0, "formula": "C2H6O",
             "molar_mass": 46.07, "included_in_thermo": False},
        ],
        "utilities": {"heat_utilities": [], "power_utilities": [],
                      "other_utilities": []},
    }


def valid_doc():
    """Return a fresh minimal SFF document, fully valid at schema + validator."""
    return copy.deepcopy(_base())


def _walk(doc, path):
    keys = path.split("/")
    node = doc
    for k in keys[:-1]:
        node = node[int(k)] if isinstance(node, list) else node[k]
    return node, keys[-1]


def mutate(doc, path, value):
    """Set a nested field. path is '/'-joined; list indices are numeric strings.
    e.g. mutate(doc, 'streams/0/stream_properties/pressure', 0)."""
    node, last = _walk(doc, path)
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def remove(doc, path):
    """Delete a nested field addressed exactly like mutate()."""
    node, last = _walk(doc, path)
    if isinstance(node, list):
        del node[int(last)]
    else:
        del node[last]
