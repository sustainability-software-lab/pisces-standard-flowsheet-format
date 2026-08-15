# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
One compact SFF document that conforms completely -- to the schema and to every
check in ``sff_checks.md`` -- plus JSON-pointer helpers for mutating it.

Tier 3 builds each matched pair by mutating a single field and asserting the
schema rejects it *at that path*; Tier 4 needs a base on which every check
passes, so that a finding after a mutation can only have come from the mutation.
Both tiers share this one document: two hand-rolled fixtures would drift, and a
drifted "conforming" document turns every test built on it into noise.

Compact on purpose (one unit, two streams, two chemicals, two utilities) but
populated everywhere it must be reachable: ``reproducibility``,
``microorganisms``, ``relevant_patents``, stream ``roles``, unit ``reactions``
and ``design_results`` all appear so Tier 3's constraint sweep can address them.

Numbers are chosen to satisfy the physical checks exactly rather than within
tolerance: Water is 18.01528 g/mol and Ethanol 46.06844 g/mol, so the product
stream's 0.9/0.1 mole split gives a mean molar mass of 20.820596 g/mol and
5.0 kmol/hr x 20.820596 = 104.10298 kg/hr (STR-10); the matching mass fractions
are 0.9 x 18.01528 / 20.820596 = 0.7787362 and 0.1 x 46.06844 / 20.820596 =
0.2212638, which are both the true values to 1e-8 and sum to exactly 1 (STR-08).
The feed's 100.0 kg/hr of pure water is 100.0 / 18.01528 = 5.550843506 kmol/hr.

``quantity_units_global`` declares only the aliases this document uses. QU-04
flags unused aliases, and Tier 4 needs a ``pass`` case for it, which a copy of
the exporter's fuller registry would not provide.

Pure library: no assertions, no test classes.
"""

import copy
import json
from pathlib import Path

__all__ = ('DELETE', 'conforming_document', 'pointer_get', 'pointer_set',
           'pointer_delete', 'pointer_exists', 'mutated', 'write_temp')

#: Sentinel for :func:`mutated`: remove the addressed field instead of replacing
#: it. A plain ``None`` cannot serve -- ``None`` is itself a value worth setting.
DELETE = object()

#%% The document

_DOCUMENT = {
    'quantity_units_global': {
        'temperature': {'aliases': ['temperature'], 'quantity_units': 'K'},
        'pressure': {'aliases': ['pressure'], 'quantity_units': 'Pa'},
        'mass_flow': {'aliases': ['total_mass_flow'], 'quantity_units': 'kg/hr'},
        'molar_flow': {'aliases': ['total_molar_flow'],
                       'quantity_units': 'kmol/hr'},
        'molar_mass': {'aliases': ['molar_mass'], 'quantity_units': 'g/mol'},
        'price': {'aliases': ['price'], 'quantity_units': 'USD/kg'},
        'electrical_energy_price': {'aliases': ['electrical_energy_price'],
                                    'quantity_units': 'USD/kWhr'},
        'regeneration_price': {'aliases': ['regeneration_price'],
                               'quantity_units': 'USD/kmol'},
        'heat_transfer_price': {'aliases': ['heat_transfer_price'],
                                'quantity_units': 'USD/kJ'},
    },
    'metadata': {
        'sff_version': '0.0.12',
        'TEA_currency': 'USD',
        'TEA_year': 2018,
        'source_doi': '10.0000/sff.test',
        'process_title': 'Compact conforming test flowsheet',
        'process_simulator': {'name': 'biosteam', 'version': '2.46.1'},
        'flowsheet_designers': 'SFF test suite',
        'feedstocks': [{'display_name': 'Water feed', 'stream_id': 'feed'}],
        'products': [{'display_name': 'Ethanol product', 'stream_id': 'product'}],
        'microorganisms': [{'name': 'Saccharomyces cerevisiae',
                            'label': 'yeast'}],
        'relevant_patents': [{'label': 'US0000000',
                              'url': 'https://example.invalid/patent'}],
        'reproducibility': {
            'environment': {
                'format': 'conda-yaml', 'filename': 'environment.yaml',
                'sha256': '0' * 64, 'content': 'name: sff-test\n',
            },
            'load_script': {
                'format': 'python', 'filename': 'load.py',
                'sha256': '1' * 64, 'content': 'def load():\n    pass\n',
                'entry_point': 'load',
            },
            'simulator_package': {
                'name': 'biosteam',
                'url': 'https://example.invalid/biosteam.git',
                'commit': 'a' * 40, 'branch': 'main', 'version': '2.46.1',
            },
            'flowsheet_model_package': {
                'name': 'biorefineries',
                'url': 'https://example.invalid/biorefineries.git',
                'commit': 'b' * 40, 'branch': 'master', 'version': '2.0.0',
            },
            'resolved': {
                'python_version': '3.9.25', 'platform': 'test',
                'env_key': '2' * 64, 'exported_at': '2026-08-15T00:00:00Z',
                'package_versions': {'biosteam': '2.46.1'},
            },
        },
    },
    'units': [
        {
            'id': 'U1',
            'unit_type': 'HXutility',
            'design_simulation_method': 'shortcut',
            'design_input_specs': {'T': 350.0},
            'thermo_property_package': {'id': 'default'},
            'reactions': [{
                'index': 0,
                'equation': 'Water -> Ethanol',
                'reactant': 'Water',
                'conversion': 0.1,
                'stoichiometry': {'Water': -1.0, 'Ethanol': 1.0},
            }],
            'design_results': {'Area': 10.0},
            'quantity_units_for_design_results': {'Area': 'm^2'},
            'purchase_costs': {'Heat exchanger': 10000.0},
            'installed_costs': {'Heat exchanger': 32000.0},
            'utility_consumption_results': {
                'low_pressure_steam': 5000.0,
                'grid_electricity': 1.5,
            },
        },
    ],
    'streams': [
        {
            'id': 'feed',
            'source_unit_id': 'None',
            'sink_unit_id': 'U1',
            'stream_description': 'Water fed from outside the system boundary',
            'roles': ['input', 'feedstock'],
            'stream_properties': {
                'total_mass_flow': 100.0,
                'total_molar_flow': 5.550843506,
                'temperature': 298.15,
                'pressure': 101325.0,
                'phases': {
                    'l': {
                        'total_mass_flow': 100.0,
                        'total_molar_flow': 5.550843506,
                        'composition': [
                            {'component_name': 'Water',
                             'mol_fraction': 1.0, 'mass_fraction': 1.0},
                        ],
                    },
                },
            },
        },
        {
            'id': 'product',
            'source_unit_id': 'U1',
            'sink_unit_id': 'None',
            'stream_description': 'Water/ethanol product leaving the boundary',
            'price': 1.0,
            'roles': ['output', 'product'],
            'stream_properties': {
                'total_mass_flow': 104.10298,
                'total_molar_flow': 5.0,
                'temperature': 350.0,
                'pressure': 101325.0,
                'phases': {
                    'l': {
                        'total_mass_flow': 104.10298,
                        'total_molar_flow': 5.0,
                        'composition': [
                            {'component_name': 'Water',
                             'mol_fraction': 0.9, 'mass_fraction': 0.7787362},
                            {'component_name': 'Ethanol',
                             'mol_fraction': 0.1, 'mass_fraction': 0.2212638},
                        ],
                    },
                },
            },
        },
    ],
    'chemicals': [
        {'id': 'Water', 'included_in_thermo': True, 'index': 0,
         'registry_id': '7732-18-5', 'formula': 'H2O', 'molar_mass': 18.01528},
        {'id': 'Ethanol', 'included_in_thermo': True, 'index': 1,
         'registry_id': '64-17-5', 'formula': 'C2H6O', 'molar_mass': 46.06844},
    ],
    'utilities': {
        'heat_utilities': [{
            'id': 'low_pressure_steam',
            'temperature': 425.15,
            'pressure': 502246.105,
            'regeneration_price': 0.2316765,
            'heat_transfer_price': 0.0,
            'heat_transfer_efficiency': 1.0,
            'composition': [{'phase': 'g', 'component_name': 'Water',
                             'mol_fraction': 1.0, 'mass_fraction': 1.0}],
            'quantity_units_for_utility_results': 'kJ/hr',
        }],
        'power_utilities': [{
            'id': 'grid_electricity',
            'electrical_energy_price': 0.07,
            'quantity_units_for_utility_results': 'kW',
        }],
    },
}


def conforming_document():
    """
    Return a fresh, fully conforming SFF document.

    Returns
    -------
    dict
        A deep copy, so a caller may mutate it freely.
    """
    return copy.deepcopy(_DOCUMENT)


#%% JSON-pointer helpers

def _split(pointer):
    """Return the RFC-6901 `pointer` as a list of unescaped string tokens.

    Tokens are kept as strings here -- whether a token addresses a list index
    or a dict key depends on the *container* it is resolved against, which is
    only known during the walk (see :func:`_walk`), not from the token's own
    spelling.
    """
    if not pointer.startswith('/'):
        raise ValueError('not a JSON pointer: %r' % (pointer,))
    steps = []
    for token in pointer[1:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        steps.append(token)
    return steps


def _resolve_step(node, step):
    """Resolve one RFC-6901 `step` against `node`, per the container's type.

    A list only ever accepts an integer index; a dict key that happens to look
    like an integer (or is zero-padded, e.g. ``'01'``) must still be used as a
    string key against a dict. The container decides, not the token.
    """
    return int(step) if isinstance(node, list) else step


def _walk(doc, steps):
    """Return the container holding the last step, and that step."""
    node = doc
    for step in steps[:-1]:
        node = node[_resolve_step(node, step)]
    return node, _resolve_step(node, steps[-1])


def pointer_get(doc, pointer):
    """
    Return the value a JSON pointer addresses.

    Parameters
    ----------
    doc : dict
    pointer : str
        RFC-6901 pointer, e.g. ``'/streams/0/stream_properties/pressure'``.

    Returns
    -------
    object

    Raises
    ------
    KeyError, IndexError
        If the pointer does not resolve.
    ValueError
        If `pointer` is malformed (e.g. missing its leading ``/``).
    """
    container, last = _walk(doc, _split(pointer))
    return container[last]


def pointer_set(doc, pointer, value):
    """
    Replace the value a JSON pointer addresses, in place.

    Parameters
    ----------
    doc : dict
    pointer : str
    value : object
    """
    container, last = _walk(doc, _split(pointer))
    container[last] = value


def pointer_delete(doc, pointer):
    """
    Remove the field a JSON pointer addresses, in place.

    Parameters
    ----------
    doc : dict
    pointer : str
    """
    container, last = _walk(doc, _split(pointer))
    del container[last]


def pointer_exists(doc, pointer):
    """
    Return whether a JSON pointer resolves in `doc`.

    Used by Tier 3's sweep to report locators the base document cannot reach,
    rather than skipping them silently.

    Parameters
    ----------
    doc : dict
    pointer : str

    Returns
    -------
    bool

    Raises
    ------
    ValueError
        If `pointer` is malformed (e.g. missing its leading ``/``). This is
        deliberately NOT reported as ``False``: a malformed pointer is a
        programmer error, distinct from a locator that is simply absent from
        this document, and the two must never be conflated -- most of all by
        the generated sweep that drives this helper across ~265 locators,
        which would otherwise silently score a typo'd pointer as "covered".
    """
    try:
        pointer_get(doc, pointer)
    except (KeyError, IndexError, TypeError):
        return False
    return True


def mutated(pointer, value):
    """
    Return a conforming document with exactly one field changed.

    Parameters
    ----------
    pointer : str
        RFC-6901 pointer to the field to change.
    value : object
        The replacement, or :data:`DELETE` to remove the field instead.

    Returns
    -------
    dict
    """
    doc = conforming_document()
    if value is DELETE:
        pointer_delete(doc, pointer)
    else:
        pointer_set(doc, pointer, value)
    return doc


#%% Landing a document on disk

def write_temp(doc, directory, name='flowsheet.json'):
    """
    Write a document to `directory` and return its path.

    Both validators take file paths rather than dicts, so every test that runs
    them needs the document on disk.

    Parameters
    ----------
    doc : dict
    directory : str or Path
    name : str, optional

    Returns
    -------
    Path
    """
    path = Path(directory) / name
    with path.open('w', encoding='utf-8') as handle:
        json.dump(doc, handle)
    return path
