# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
# 
# This module is under the MIT open-source license. See 
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

import json
import inspect
import sys
import re
import warnings
import numpy as np

from collections import deque
from types import FunctionType

from thermosteam import Reaction, ReactionSet, SeriesReaction, ParallelReaction
from thermosteam import Chemical
from thermosteam.reaction._reaction import get_stoichiometric_string
from biosteam import PowerUtility, System

import biosteam as bst

from ._quantity_units import (
    quantity_units_global_for,
    scalar,
    uses_inline_scalar_style,
    version_tuple,
    quantity_units_for_design_results,
)
from .exceptions import (
    SFFExportError,
    FlowsheetWriteError,
    DesignInputSpecError,
)

__all__ = ('export_biosteam_flowsheet', 'available_sff_versions',
           'get_purchase_cost_correlations')

#%% Entry-point export function

# Versioned exporters are named `<_EXPORTER_PREFIX><major>_<minor>_<patch>`, and
# that name is the only registration they need: `export_biosteam_flowsheet`
# resolves the requested version by looking up the matching name in this module.
# Adding support for a new schema version therefore means adding one function
# with the right name -- nothing else in this section changes.
_EXPORTER_PREFIX = 'export_biosteam_flowsheet_sff_'


def export_biosteam_flowsheet(sys, filepath, sff_version, **kwargs):
    """
    Export a simulated BioSTEAM system to an SFF JSON file.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    sff_version : str
        SFF schema version to export against, in semantic versioning notation;
        e.g., ``'0.0.5'``. This selects the versioned exporter function to use
        and is recorded as ``metadata.sff_version`` in the exported file.
    **kwargs
        Forwarded to the versioned exporter function.

    Raises
    ------
    ValueError
        If no exporter is implemented for `sff_version`.
    """
    exporter = get_versioned_exporter(sff_version)
    return exporter(sys, filepath, sff_version=sff_version, **kwargs)


def get_versioned_exporter(sff_version):
    """
    Return the exporter function implementing a given SFF schema version.

    Parameters
    ----------
    sff_version : str
        SFF schema version in semantic versioning notation; e.g., ``'0.0.5'``.

    Returns
    -------
    function
        The module-level ``export_biosteam_flowsheet_sff_<major>_<minor>_<patch>``
        function for that version.

    Raises
    ------
    ValueError
        If no such function exists in this module.
    """
    name = _EXPORTER_PREFIX + str(sff_version).replace('.', '_')
    exporter = globals().get(name)
    if not isinstance(exporter, FunctionType):
        available = available_sff_versions()
        raise ValueError(
            f'no exporter implemented for SFF version {sff_version!r} '
            f'(expected a function named {name!r} in pisces_sff._export); '
            f"available versions: {', '.join(available) if available else 'none'}."
        )
    return exporter


def available_sff_versions():
    """
    Return the SFF schema versions this module can export, oldest first.

    Returns
    -------
    list of str
        Versions in semantic versioning notation; e.g., ``['0.0.5']``. These are
        read from the names of the versioned exporter functions defined here.
    """
    n = len(_EXPORTER_PREFIX)
    versions = [name[n:].replace('_', '.') for name, obj in globals().items()
                if name.startswith(_EXPORTER_PREFIX) and isinstance(obj, FunctionType)]
    return sorted(versions, key=lambda v: [(0, int(i), '') if i.isdigit() else (1, 0, i)
                                           for i in v.split('.')])

#%% Shared flowsheet assembly

#: First schema version that requires metadata.TEA_currency. Older exporters
#: omit the field so their historical output stays byte-stable; see the gated
#: emission in _build_sff_dict below.
_TEA_CURRENCY_SINCE = (0, 0, 8)

#: First schema version that structures a stream's composition and totals
#: per-phase (stream_properties.phases keyed by phase symbol) instead of a
#: single flat composition array. Gated in _build_sff_dict so 0.0.5-0.0.8 stay
#: byte-stable.
_PHASES_SINCE = (0, 0, 9)

#: First schema version that emits a stream's `roles` array (base topology role
#: input | output | internal, plus designation roles purchased_raw_material,
#: feedstock, product). Gated in _build_sff_dict so 0.0.5-0.0.9 stay byte-stable.
_ROLES_SINCE = (0, 0, 10)

#: First schema version that emits a stream's `enthalpy_flow` (whole-stream
#: enthalpy flow rate from biosteam `stream.H`, in kJ/hr) and the matching
#: `enthalpy_flow` entry in quantity_units_global. Gated in _build_sff_dict so
#: 0.0.5-0.0.10 stay byte-stable. See quantity_units_global_for.
_ENTHALPY_SINCE = (0, 0, 11)

# 0.0.12+ synthesizes a deterministic unique id for BioSTEAM streams whose .ID is
# blank ('') -- several auxiliary streams share an empty id, which is an ambiguous
# cross-reference key. Older exporters keep raw .ID so their historical output stays
# byte-stable.
_STREAM_ID_SYNTH_SINCE = (0, 0, 12)
# 0.0.12+ normalizes BioSTEAM's dimensionless design-result unit labels to '' (the
# SFF convention for dimensionless quantities), so quantity-unit strings are all
# pint-parseable. Older exporters keep the raw label for byte-stable output.
_DIMLESS_UNIT_NORM_SINCE = (0, 0, 12)
# BioSTEAM design-result "unit" strings that denote a dimensionless quantity and so
# map to '' under _DIMLESS_UNIT_NORM_SINCE. 'Ratio' is BioSTEAM's label for reflux
# ratios; extend this set if other dimensionless labels surface.
_DIMENSIONLESS_DESIGN_UNITS = frozenset({"Ratio"})

#: First schema version that records per-item purchase-cost correlations
#: (purchase_cost_correlations on each unit, from biosteam @cost cost_items),
#: so a consumer can re-derive equipment cost at a different size/CEPCI. Gated in
#: _build_sff_dict so 0.0.5-0.1.1 stay byte-stable. sff_checks.md UNIT-08/UNIT-09.
_PURCHASE_COST_CORR_SINCE = (0, 1, 2)

#: First schema version that records machine-verified provenance tags
#: (metadata.tags). Stamped ONLY by the 0.1.3 wrapper (never in _build_sff_dict),
#: so 0.0.5-0.1.2 stay byte-stable and never emit the key. sff_checks.md section 8.
_TAGS_SINCE = (0, 1, 3)

#: First schema version whose `chemicals` registry is the UNION of every
#: chemicals set the document references -- every stream's, every reaction's,
#: and every utility agent's -- rather than the representative stream's alone.
#: A BioSTEAM system may mix thermo packages (a wastewater-treatment area or a
#: cooling-tower/chilled-water facility with its own CompiledChemicals), and a
#: registry built from one stream then leaves those sections' component names
#: dangling (sff_checks.md STR-07, UTIL-04, UNIT-04, UNIT-06). Gated so older
#: exporters stay byte-stable; for a single-thermo system the union adds nothing.
_CHEMICAL_REGISTRY_UNION_SINCE = (0, 1, 3)

#: Absolute molar-flow threshold (kmol/hr) below which a stream or phase is
#: serialized as EMPTY -- empty composition -- mirroring the validator's
#: ZERO_FLOW (sff_checks.md "Default tolerances", STR-13). A converged
#: simulation can leave vanishing residues (1e-85 kg/hr of water in an unused
#: cooling-tower makeup line); listing a composition for material the
#: validator rightly treats as absent is the STR-13 contradiction. Not
#: version-gated: it only ever changes output for flows already below the
#: zero threshold, where the previous output was non-conforming.
ZERO_FLOW = 1e-12


def _assign_stream_ids(all_streams, sff_version):
    """Map each stream object to the id the SFF document should use for it.

    For ``sff_version`` below :data:`_STREAM_ID_SYNTH_SINCE`, this is the raw
    BioSTEAM ``stream.ID`` (byte-stable historical behavior). From 0.0.12 on,
    a stream whose ``.ID`` is blank (``''``) -- BioSTEAM leaves some auxiliary
    streams unnamed, and multiple blank ids collide -- is assigned a
    deterministic ``'unnamed_stream_<n>'`` id, ``n`` counting blank streams in
    system order and skipping any id already in use.

    Parameters
    ----------
    all_streams : list
        Streams in system order (``list(sys.streams)``).
    sff_version : str

    Returns
    -------
    dict
        ``{stream_object: resolved_id_str}`` for every stream in ``all_streams``.
    """
    resolved = {}
    if version_tuple(sff_version) < _STREAM_ID_SYNTH_SINCE:
        for s in all_streams:
            resolved[s] = s.ID
        return resolved
    existing = {s.ID for s in all_streams if s.ID}
    n = 0
    for s in all_streams:
        if s.ID:
            resolved[s] = s.ID
            continue
        n += 1
        candidate = "unnamed_stream_%d" % n
        while candidate in existing:
            n += 1
            candidate = "unnamed_stream_%d" % n
        existing.add(candidate)
        resolved[s] = candidate
    return resolved


# Every versioned exporter assembles the same core document; only the
# version-specific additions differ. Keeping the assembly here means adding a
# schema version costs one thin function rather than a copy of ~170 lines that
# would drift from this one. metadata['sff_version'] is assigned from the
# argument here and nowhere else -- see tests/tier1/test_version.py.
def _build_sff_dict(sys, tea=None,
                    stoichiometry="dict", # must be one of (None, "vector", "dict")
                    microorganisms=None, # optional list of microbial hosts; see metadata section below
                    source_doi=None, # optional; authored, see metadata section below
                    process_title=None, # optional; authored
                    flowsheet_designers=None, # optional; authored
                    sff_version=None, # recorded as metadata['sff_version']
                    ):
    """
    Assemble the SFF document for a simulated BioSTEAM system.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    source_doi : str, optional
        DOI of the source publication. Emitted only when truthy.
    process_title : str, optional
        Descriptive title for the process. Emitted only when truthy.
    flowsheet_designers : str, optional
        Name(s) of the flowsheet's authors. Emitted only when truthy.
    sff_version : str
        Version recorded as ``metadata['sff_version']``.

    Returns
    -------
    dict
        The SFF document, ready to serialize.
    """
    f = sys.flowsheet
    u, s = sys.units, sys.streams
    all_streams = list(s)
    stream_ids = _assign_stream_ids(all_streams, sff_version)
    all_sys_feeds = list(sys.feeds)
    all_sys_products = list(sys.products)
    # The highest-carbon boundary feed is invariant across the whole export, so
    # compute it once here and thread it into is_feedstock / get_stream_roles
    # rather than have each call re-scan every feed.
    max_carbon_feed = _max_carbon_boundary_feed(all_sys_feeds)
    if tea is None:
        tea = sys.TEA
    # Pre-0.0.7 emits inline {"value","units"} scalars and the legacy field
    # names; 0.0.7+ emits bare numbers whose units live in quantity_units_global.
    # Older exporters must stay byte-stable so historical exports reproduce, so
    # every version-dependent shape below is gated on this one flag.
    inline = uses_inline_scalar_style(sff_version)
    results_key = "units_for_utility_results" if inline else "quantity_units_for_utility_results"

    ## ------- Metadata ------- ##
    metadata = {}
    # Reported from the requested version rather than a hardcoded literal: this
    # previously read '0.0.3' regardless of the version exported against, so
    # every export misreported the schema it was written for.
    metadata['sff_version'] = sff_version
    # TEA_currency became a required metadata field in v0.0.8; older exporters
    # omit it to keep their historical output byte-stable. BioSTEAM reports all
    # cost results in USD.
    if version_tuple(sff_version) >= _TEA_CURRENCY_SINCE:
        metadata['TEA_currency'] = 'USD'
    metadata['TEA_year'] = tea.duration[0]
    metadata['process_simulator'] = {'name': 'BioSTEAM',
                                     'version': bst.__version__}
    metadata['feedstocks'] = [{"display_name": format_name(stream_ids[stream]), "stream_id": stream_ids[stream]}
                              for stream in all_streams if is_feedstock(stream, all_sys_feeds, max_carbon_feed)]
    metadata['products'] = [{"display_name": format_name(stream_ids[stream]), "stream_id": stream_ids[stream]}
                            for stream in all_streams if is_product(stream, all_sys_products)]

    # ------- Authored descriptive metadata (optional) -------
    # Human-authored fields a simulated System cannot carry: the source
    # publication and authorship of the flowsheet. Supplied by the caller (in
    # practice from a model's extended_metadata.yaml via the runner) and each
    # emitted only when truthy, so an absent value is simply omitted and output
    # for callers that pass nothing stays byte-stable. These properties already
    # exist (optional) in the schema from v0.0.10, so no version gate is needed.
    if source_doi:
        metadata['source_doi'] = source_doi
    if process_title:
        metadata['process_title'] = process_title
    if flowsheet_designers:
        metadata['flowsheet_designers'] = flowsheet_designers

    # ------- Microorganisms (optional) -------
    # A BioSTEAM System does not carry any host-organism identity, so this value
    # cannot be inferred from `sys`; callers must supply it explicitly via the
    # `microorganisms` argument. The v0.0.5 schema models this field as an array
    # of {"name": str, "label"?: str} objects (rather than a single string) so
    # that co-cultures and multi-host processes are each represented as distinct,
    # machine-readable entries. We normalize whatever the caller passes into that
    # shape here: a bare string is promoted to {"name": <string>}, and a dict is
    # accepted after confirming it carries a non-empty `name` (an optional `label`
    # is preserved when present). The key is omitted entirely when nothing is
    # supplied, because the schema marks `microorganisms` as optional and requires
    # at least one entry when present (minItems: 1), so emitting an empty list
    # would produce output that fails validation.
    if microorganisms:
        normalized_hosts = []
        for host in microorganisms:
            if isinstance(host, str):
                normalized_hosts.append({"name": host})
            elif isinstance(host, dict) and host.get("name"):
                entry = {"name": host["name"]}
                if host.get("label"):
                    entry["label"] = host["label"]
                normalized_hosts.append(entry)
            else:
                # Fail loudly rather than silently emit an invalid entry: a host
                # that is neither a string nor a dict-with-name cannot be mapped
                # to the schema's required {"name": ...} shape.
                raise ValueError(
                    f"Invalid microorganism entry {host!r}: expected a non-empty "
                    "string or a dict containing a non-empty 'name' key."
                )
        if normalized_hosts:
            metadata['microorganisms'] = normalized_hosts

    ## ------- Units ------- ##
    units = []
    all_hu_agents = set()
    all_pu_agents = set()
    all_ou_agents = set()
    ng_price = 0.0
    for raw_unit in list(u):
        ru = raw_unit
        u_cons, u_prod, hu_agents, pu_agents, ou_agents = get_utility_results(ru)
        all_hu_agents = all_hu_agents.union(hu_agents)
        all_pu_agents = all_pu_agents.union(pu_agents)
        all_ou_agents = all_ou_agents.union(ou_agents)
        if hasattr(ru, 'natural_gas_price'):
            ng_price = ru.natural_gas_price
            
        unit = {"id": ru.ID,
                "unit_type": get_unit_type(ru),
                "design_input_specs": get_design_input_specs(ru),
                "design_simulation_method": get_design_simulation_method(ru),
                "thermo_property_package": get_thermo(ru),
                "reactions": get_reactions(ru, stoichiometry=stoichiometry),
                "design_results": ru.design_results if hasattr(ru, 'design_results') else {},
                "installed_costs": ru.installed_costs if hasattr(ru, 'installed_costs') else {},
                "purchase_costs": ru.purchase_costs if hasattr(ru, 'purchase_costs') else {},
                "utility_consumption_results": u_cons,
                "utility_production_results": u_prod,
                }
        if not inline:
            qufd = quantity_units_for_design_results(ru)
            if version_tuple(sff_version) >= _DIMLESS_UNIT_NORM_SINCE:
                qufd = {k: ("" if v in _DIMENSIONLESS_DESIGN_UNITS else v)
                        for k, v in qufd.items()}
            unit["quantity_units_for_design_results"] = qufd
        # 0.1.2+ records the per-item purchase-cost correlation (biosteam
        # cost_items) so a consumer can re-derive equipment cost at a different
        # size/CEPCI. Emitted on every unit ({} for procedurally-costed units).
        # Gated so pre-0.1.2 exporters stay byte-stable. sff_checks UNIT-08/09.
        if version_tuple(sff_version) >= _PURCHASE_COST_CORR_SINCE:
            unit["purchase_cost_correlations"] = get_purchase_cost_correlations(ru)
        units.append(unit)
        
    ## ------ Streams ------ ##
    streams = []
    for raw_stream in all_streams:
        rs = raw_stream
        if not (rs.source or rs.sink): continue # skip isolated streams
        stream_properties = {
            "total_mass_flow": scalar(rs.F_mass, "kg/h", inline),
            "total_molar_flow": scalar(rs.F_mol, "kmol/h", inline),
            "temperature": scalar(rs.T, "K", inline),
            "pressure": scalar(rs.P, "Pa", inline),
        }
        # 0.0.9+ makes each phase first-class (its own totals + composition);
        # earlier versions keep the single flat composition array. Gated so the
        # older stream shape stays byte-identical.
        if version_tuple(sff_version) >= _PHASES_SINCE:
            stream_properties["phases"] = get_phase_properties(rs, inline)
        else:
            stream_properties["composition"] = get_composition(rs)
        stream = {"id": stream_ids[rs],
                  "source_unit_id": rs.source.ID if rs.source is not None else "None",
                  "sink_unit_id": rs.sink.ID if rs.sink is not None else "None",
                  "price": scalar(rs.price, "$/kg", inline),
                  "stream_properties": stream_properties,
                  }
        try:
            stream["stream_properties"]["total_volumetric_flow"] = scalar(rs.F_vol, "m3/h", inline)
        except Exception as e:
            # total_volumetric_flow is optional in the schema. A missing liquid
            # molar volume method is a common, expected reason it cannot be
            # computed; other failures are unexpected but still non-fatal. Either
            # way, omit it for this stream and continue rather than aborting the
            # whole export -- but warn on the unexpected case so it is not lost.
            if 'liquid molar volume method' not in str(e).lower():
                warnings.warn(
                    f"could not compute total_volumetric_flow for stream "
                    f"{rs.ID!r}; omitting it: {e}",
                    stacklevel=2,
                )
        # 0.0.11+ records the whole-stream enthalpy flow (biosteam `stream.H`,
        # kJ/hr). Unlike total_volumetric_flow, H has no known failure mode, so
        # it is emitted unconditionally within the gate. Gated so pre-0.0.11
        # stream shape stays byte-stable.
        if version_tuple(sff_version) >= _ENTHALPY_SINCE:
            stream["stream_properties"]["enthalpy_flow"] = scalar(rs.H, "kJ/hr", inline)
        # 0.0.10+ declares each stream's roles (base topology role plus any
        # designation roles). Gated so pre-0.0.10 stream shape stays byte-stable.
        if version_tuple(sff_version) >= _ROLES_SINCE:
            stream["roles"] = get_stream_roles(rs, all_sys_feeds, all_sys_products,
                                               max_carbon_feed)
        streams.append(stream)
    
    ## ------ Chemicals ------ ##
    chemicals = []
    repr_stream = all_streams[0]
    chems = repr_stream.chemicals
    vle_chems = repr_stream.vle_chemicals
    for i, c in zip(range(len(chems)), chems):
        chemicals.append(get_chemical_entry(c, i, c in vle_chems, stoichiometry))
    # 0.1.3+: complete the registry with every chemical from any OTHER chemicals
    # set the document references (multi-thermo systems), appended after the
    # representative stream's set so the single-thermo output is byte-identical.
    if version_tuple(sff_version) >= _CHEMICAL_REGISTRY_UNION_SINCE:
        chemicals.extend(get_additional_chemical_entries(
            chems, all_streams, list(u),
            list(all_hu_agents) + list(all_ou_agents), stoichiometry))
        
    ## ----- Utilities ----- ##
    # Deterministic ordering policy: utility agents form a registry, so the
    # heat_utilities / other_utilities arrays are emitted sorted by agent ID
    # rather than in process-dependent accumulation-set order. Two distinct
    # agents sharing an ID cannot occur in a compiled biosteam flowsheet, so
    # ties are not defended against. (power_utilities is a single synthetic
    # entry; order cannot matter there.)
    heat_utilities = []
    for hu_agent in sorted(all_hu_agents, key=lambda agent: agent.ID):
        hu = {
              "id": hu_agent.ID,
              "temperature": scalar(hu_agent.T, "K", inline),
              "pressure": scalar(hu_agent.P, "Pa", inline),
              "regeneration_price": scalar(hu_agent.regeneration_price, "$/kmol", inline),
              "heat_transfer_price": scalar(hu_agent.heat_transfer_price, "$/kJ", inline),
              "heat_transfer_efficiency": hu_agent.heat_transfer_efficiency if hu_agent.heat_transfer_efficiency is not None else 1.0,
              "composition": get_composition(hu_agent),
              }
        hu[results_key] = "kJ/h" if inline else "kJ/hr"
        heat_utilities.append(hu)

    power_utilities = []
    for pu_agent in all_pu_agents:
        pu = {"id": "Marginal grid electricity"}
        if inline:
            pu["price"] = {"value": pu_agent.price, "units": "$/kWh"}
        else:
            pu["electrical_energy_price"] = pu_agent.price
        pu[results_key] = "kW"
        power_utilities.append(pu)

    other_utilities = []
    for ou_agent in sorted(all_ou_agents, key=lambda agent: agent.ID):
        ou = {
              "id": ou_agent.ID,
              "temperature": scalar(ou_agent.T, "K", inline),
              "pressure": scalar(ou_agent.P, "Pa", inline),
              "price": scalar(ou_agent.price or ng_price, "$/kg", inline),
              }
        ou[results_key] = "kg/h" if inline else "kg/hr"
        ou["composition"] = get_composition(ou_agent)
        other_utilities.append(ou)

    document = {"metadata": metadata,
                "units": units,
                "streams": streams,
                "chemicals": chemicals,
                "utilities": {"heat_utilities": heat_utilities,
                              "power_utilities": power_utilities,
                              "other_utilities": other_utilities},
                }
    if not inline:
        # Version-filtered so exporters older than an entry's introduction
        # version keep their historical registry byte-for-byte.
        document["quantity_units_global"] = quantity_units_global_for(sff_version)
    return document


def _json_default(value):
    """Return JSON-native equivalents for containers emitted by BioSTEAM.

    NumPy scalars and arrays occur in design and cost results, while some
    BioSTEAM models retain ordered values in ``collections.deque``. Unknown
    objects still raise ``TypeError`` so the export boundary fails closed.
    """
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, deque):
        return list(value)
    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON serializable"
    )


def _write_sff_json(flowsheet_to_export, filepath):
    """
    Serialize an assembled SFF document to `filepath` as indented JSON.

    Raises
    ------
    FlowsheetWriteError
        If serialization or the file write fails.
    """
    try:
        with open(filepath, "w") as json_file:
            json.dump(
                flowsheet_to_export,
                json_file,
                indent=4,
                default=_json_default,
            )
    except Exception as e:
        raise FlowsheetWriteError(
            f"could not write SFF document to {filepath!r}: {e}"
        ) from e


#%% Export function for SFF schema v0.0.5
def export_biosteam_flowsheet_sff_0_0_5(sys, filepath, tea=None,
                                        stoichiometry="dict", # must be one of (None, "vector", "dict")
                                        microorganisms=None, # optional list of microbial hosts
                                        sff_version='0.0.5', # must match this function's name suffix
                                        ):
    """Export a simulated BioSTEAM system against SFF schema v0.0.5."""
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        sff_version=sff_version,
    )
    _write_sff_json(flowsheet_to_export, filepath)


#%% Export function for SFF schema v0.0.6
def export_biosteam_flowsheet_sff_0_0_6(sys, filepath, tea=None,
                                        stoichiometry="dict", # must be one of (None, "vector", "dict")
                                        microorganisms=None, # optional list of microbial hosts
                                        reproducibility=None, # optional recipe block; see pisces_sff._runner
                                        sff_version='0.0.6', # must match this function's name suffix
                                        ):
    """
    Export a simulated BioSTEAM system against SFF schema v0.0.6.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    reproducibility : dict, optional
        Recipe block written to ``metadata['reproducibility']``: the environment
        specification, load script, pinned packages, and resolved runtime facts.
        Built by :func:`pisces_sff._runner.build_reproducibility`. Omitted
        entirely when falsy, so hand exports still validate -- the schema marks
        the block optional.
    sff_version : str, optional
        Version recorded as ``metadata['sff_version']``.
    """
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        sff_version=sff_version,
    )
    if reproducibility:
        flowsheet_to_export['metadata']['reproducibility'] = reproducibility
    _write_sff_json(flowsheet_to_export, filepath)


#%% Export function for SFF schema v0.0.7
def export_biosteam_flowsheet_sff_0_0_7(sys, filepath, tea=None,
                                        stoichiometry="dict", # must be one of (None, "vector", "dict")
                                        microorganisms=None, # optional list of microbial hosts
                                        reproducibility=None, # optional recipe block; see pisces_sff._runner
                                        sff_version='0.0.7', # must match this function's name suffix
                                        ):
    """
    Export a simulated BioSTEAM system against SFF schema v0.0.7.

    Identical to the v0.0.6 exporter except for the quantity-unit shape the
    shared builder emits at this version: scalars and prices are bare numbers
    whose units are declared once in the top-level ``quantity_units_global``
    registry, each unit operation carries ``quantity_units_for_design_results``,
    the power-utility price is ``electrical_energy_price``, and the utility
    results-unit key is ``quantity_units_for_utility_results``.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    reproducibility : dict, optional
        Recipe block written to ``metadata['reproducibility']``. Built by
        :func:`pisces_sff._runner.build_reproducibility`. Omitted when falsy.
    sff_version : str, optional
        Version recorded as ``metadata['sff_version']``.
    """
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        sff_version=sff_version,
    )
    if reproducibility:
        flowsheet_to_export['metadata']['reproducibility'] = reproducibility
    _write_sff_json(flowsheet_to_export, filepath)


#%% Export function for SFF schema v0.0.8
def export_biosteam_flowsheet_sff_0_0_8(sys, filepath, tea=None,
                                        stoichiometry="dict", # must be one of (None, "vector", "dict")
                                        microorganisms=None, # optional list of microbial hosts
                                        reproducibility=None, # optional recipe block; see pisces_sff._runner
                                        sff_version='0.0.8', # must match this function's name suffix
                                        ):
    """
    Export a simulated BioSTEAM system against SFF schema v0.0.8.

    Identical to the v0.0.7 exporter except that ``metadata.TEA_currency`` is
    now a required field: the shared builder emits it as ``"USD"`` (the currency
    BioSTEAM reports all cost results in) at this version and above.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    reproducibility : dict, optional
        Recipe block written to ``metadata['reproducibility']``. Built by
        :func:`pisces_sff._runner.build_reproducibility`. Omitted when falsy.
    sff_version : str, optional
        Version recorded as ``metadata['sff_version']``.
    """
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        sff_version=sff_version,
    )
    if reproducibility:
        flowsheet_to_export['metadata']['reproducibility'] = reproducibility
    _write_sff_json(flowsheet_to_export, filepath)


#%% Export function for SFF schema v0.0.9
def export_biosteam_flowsheet_sff_0_0_9(sys, filepath, tea=None,
                                        stoichiometry="dict", # must be one of (None, "vector", "dict")
                                        microorganisms=None, # optional list of microbial hosts
                                        reproducibility=None, # optional recipe block; see pisces_sff._runner
                                        sff_version='0.0.9', # must match this function's name suffix
                                        ):
    """
    Export a simulated BioSTEAM system against SFF schema v0.0.9.

    Identical to the v0.0.8 exporter except for the stream structure the shared
    builder emits at this version: each stream's ``stream_properties.phases`` is
    an object keyed by phase symbol, and every phase carries its own total
    molar/mass/volumetric flows and its own composition (see
    :func:`get_phase_properties`). The whole-stream totals, temperature, and
    pressure are retained; the flat ``composition`` array is dropped.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    reproducibility : dict, optional
        Recipe block written to ``metadata['reproducibility']``. Built by
        :func:`pisces_sff._runner.build_reproducibility`. Omitted when falsy.
    sff_version : str, optional
        Version recorded as ``metadata['sff_version']``.
    """
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        sff_version=sff_version,
    )
    if reproducibility:
        flowsheet_to_export['metadata']['reproducibility'] = reproducibility
    _write_sff_json(flowsheet_to_export, filepath)


#%% Export function for SFF schema v0.0.10
def export_biosteam_flowsheet_sff_0_0_10(sys, filepath, tea=None,
                                         stoichiometry="dict", # must be one of (None, "vector", "dict")
                                         microorganisms=None, # optional list of microbial hosts
                                         source_doi=None, # optional; authored descriptive metadata
                                         process_title=None, # optional; authored
                                         flowsheet_designers=None, # optional; authored
                                         reproducibility=None, # optional recipe block; see pisces_sff._runner
                                         sff_version='0.0.10', # must match this function's name suffix
                                         ):
    """
    Export a simulated BioSTEAM system against SFF schema v0.0.10.

    Identical to the v0.0.9 exporter except that the shared builder additionally
    emits an optional ``roles`` array on every non-isolated stream: exactly one
    base topology role (``input`` | ``output`` | ``internal``) plus any
    designation roles (``purchased_raw_material`` on priced inputs, ``feedstock``
    on feedstock inputs, ``product`` on product outputs). See
    :func:`get_stream_roles`. The property is optional, so 0.0.9-shaped files
    still validate against this schema.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    source_doi : str, optional
        DOI of the source publication. Emitted only when truthy.
    process_title : str, optional
        Descriptive title for the process. Emitted only when truthy.
    flowsheet_designers : str, optional
        Name(s) of the flowsheet's authors. Emitted only when truthy.
    reproducibility : dict, optional
        Recipe block written to ``metadata['reproducibility']``. Built by
        :func:`pisces_sff._runner.build_reproducibility`. Omitted when falsy.
    sff_version : str, optional
        Version recorded as ``metadata['sff_version']``.
    """
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        source_doi=source_doi, process_title=process_title,
        flowsheet_designers=flowsheet_designers,
        sff_version=sff_version,
    )
    if reproducibility:
        flowsheet_to_export['metadata']['reproducibility'] = reproducibility
    _write_sff_json(flowsheet_to_export, filepath)


#%% Export function for SFF schema v0.0.11
def export_biosteam_flowsheet_sff_0_0_11(sys, filepath, tea=None,
                                         stoichiometry="dict", # must be one of (None, "vector", "dict")
                                         microorganisms=None, # optional list of microbial hosts
                                         source_doi=None, # optional; authored descriptive metadata
                                         process_title=None, # optional; authored
                                         flowsheet_designers=None, # optional; authored
                                         reproducibility=None, # optional recipe block; see pisces_sff._runner
                                         sff_version='0.0.11', # must match this function's name suffix
                                         ):
    """
    Export a simulated BioSTEAM system against SFF schema v0.0.11.

    Identical to the v0.0.10 exporter except that the shared builder additionally
    emits an optional ``enthalpy_flow`` on every non-isolated stream's
    ``stream_properties`` (the whole-stream enthalpy flow rate from biosteam
    ``stream.H``, in kJ/hr), plus the matching ``enthalpy_flow`` entry in
    ``quantity_units_global``. The property is optional, so 0.0.10-shaped files
    still validate against this schema.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    source_doi : str, optional
        DOI of the source publication. Emitted only when truthy.
    process_title : str, optional
        Descriptive title for the process. Emitted only when truthy.
    flowsheet_designers : str, optional
        Name(s) of the flowsheet's authors. Emitted only when truthy.
    reproducibility : dict, optional
        Recipe block written to ``metadata['reproducibility']``. Built by
        :func:`pisces_sff._runner.build_reproducibility`. Omitted when falsy.
    sff_version : str, optional
        Version recorded as ``metadata['sff_version']``.
    """
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        source_doi=source_doi, process_title=process_title,
        flowsheet_designers=flowsheet_designers,
        sff_version=sff_version,
    )
    if reproducibility:
        flowsheet_to_export['metadata']['reproducibility'] = reproducibility
    _write_sff_json(flowsheet_to_export, filepath)


#%% Export function for SFF schema v0.0.12
def export_biosteam_flowsheet_sff_0_0_12(sys, filepath, tea=None,
                                         stoichiometry="dict", # must be one of (None, "vector", "dict")
                                         microorganisms=None, # optional list of microbial hosts
                                         source_doi=None, # optional; authored descriptive metadata
                                         process_title=None, # optional; authored
                                         flowsheet_designers=None, # optional; authored
                                         reproducibility=None, # optional recipe block; see pisces_sff._runner
                                         sff_version='0.0.12', # must match this function's name suffix
                                         ):
    """
    Export a simulated BioSTEAM system against SFF schema v0.0.12.

    Emits the same document shape as the v0.0.11 exporter -- v0.0.12 only
    *tightens* the schema (declarative constraints from sff_checks.md: semver
    sff_version, TEA_year bounds, non-empty TEA_currency, 64-hex reproducibility
    digests, reaction conversion in [0, 1] and equation-or-stoichiometry, positive
    stream pressure, required total_mass_flow, positive molar mass, positive
    utility temperature/pressure). The reference export already satisfies all of
    them, so its output is byte-identical to the 0.0.11 export except for
    metadata.sff_version.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    source_doi : str, optional
        DOI of the source publication. Emitted only when truthy.
    process_title : str, optional
        Descriptive title for the process. Emitted only when truthy.
    flowsheet_designers : str, optional
        Name(s) of the flowsheet's authors. Emitted only when truthy.
    reproducibility : dict, optional
        Recipe block written to ``metadata['reproducibility']``. Built by
        :func:`pisces_sff._runner.build_reproducibility`. Omitted when falsy.
    sff_version : str, optional
        Version recorded as ``metadata['sff_version']``.
    """
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        source_doi=source_doi, process_title=process_title,
        flowsheet_designers=flowsheet_designers,
        sff_version=sff_version,
    )
    if reproducibility:
        flowsheet_to_export['metadata']['reproducibility'] = reproducibility
    _write_sff_json(flowsheet_to_export, filepath)


def export_biosteam_flowsheet_sff_0_1_0(sys, filepath, tea=None,
                                        stoichiometry="dict", # must be one of (None, "vector", "dict")
                                        microorganisms=None, # optional list of microbial hosts
                                        source_doi=None, # optional; authored descriptive metadata
                                        process_title=None, # optional; authored
                                        flowsheet_designers=None, # optional; authored
                                        reproducibility=None, # optional recipe block; see pisces_sff._runner
                                        sff_version='0.1.0', # must match this function's name suffix
                                        ):
    """
    Export a simulated BioSTEAM system against SFF schema v0.1.0.

    Emits the same document shape as the v0.0.12 exporter. v0.1.0 is a milestone
    release that graduates the format out of the 0.0.x line; it introduces no new
    schema-shape or constraint changes, so its output is byte-identical to the
    0.0.12 export except for metadata.sff_version. All version-gated behavior
    (stream-id synthesis, dimensionless-unit normalization, per-phase structure,
    stream roles, enthalpy flow, TEA_currency) is already active at 0.0.12 and
    remains so here, since 0.1.0 sorts above every gating threshold.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    source_doi : str, optional
        DOI of the source publication. Emitted only when truthy.
    process_title : str, optional
        Descriptive title for the process. Emitted only when truthy.
    flowsheet_designers : str, optional
        Name(s) of the flowsheet's authors. Emitted only when truthy.
    reproducibility : dict, optional
        Recipe block written to ``metadata['reproducibility']``. Built by
        :func:`pisces_sff._runner.build_reproducibility`. Omitted when falsy.
    sff_version : str, optional
        Version recorded as ``metadata['sff_version']``.
    """
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        source_doi=source_doi, process_title=process_title,
        flowsheet_designers=flowsheet_designers,
        sff_version=sff_version,
    )
    if reproducibility:
        flowsheet_to_export['metadata']['reproducibility'] = reproducibility
    _write_sff_json(flowsheet_to_export, filepath)


def export_biosteam_flowsheet_sff_0_1_1(sys, filepath, tea=None,
                                        stoichiometry="dict", # must be one of (None, "vector", "dict")
                                        microorganisms=None, # optional list of microbial hosts
                                        source_doi=None, # optional; authored descriptive metadata
                                        process_title=None, # optional; authored
                                        flowsheet_designers=None, # optional; authored
                                        reproducibility=None, # optional recipe block; see pisces_sff._runner
                                        sff_version='0.1.1', # must match this function's name suffix
                                        ):
    """
    Export a simulated BioSTEAM system against SFF schema v0.1.1.

    Emits the same document shape as the v0.1.0 exporter. v0.1.1 only *loosens*
    the schema: it drops the v0.0.12 `exclusiveMinimum: 0` constraint on
    chemicals[].molar_mass (CHEM-02), which now lives in the validator as a
    `warning` instead. That is a validation-only change with no effect on export
    output -- the reference export emits positive molar masses regardless -- so
    this export is byte-identical to the 0.1.0 export except for
    metadata.sff_version. All version-gated behavior active at 0.1.0 remains so
    here, since 0.1.1 sorts above every gating threshold.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    source_doi : str, optional
        DOI of the source publication. Emitted only when truthy.
    process_title : str, optional
        Descriptive title for the process. Emitted only when truthy.
    flowsheet_designers : str, optional
        Name(s) of the flowsheet's authors. Emitted only when truthy.
    reproducibility : dict, optional
        Recipe block written to ``metadata['reproducibility']``. Built by
        :func:`pisces_sff._runner.build_reproducibility`. Omitted when falsy.
    sff_version : str, optional
        Version recorded as ``metadata['sff_version']``.
    """
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        source_doi=source_doi, process_title=process_title,
        flowsheet_designers=flowsheet_designers,
        sff_version=sff_version,
    )
    if reproducibility:
        flowsheet_to_export['metadata']['reproducibility'] = reproducibility
    _write_sff_json(flowsheet_to_export, filepath)


def export_biosteam_flowsheet_sff_0_1_2(sys, filepath, tea=None,
                                        stoichiometry="dict", # must be one of (None, "vector", "dict")
                                        microorganisms=None, # optional list of microbial hosts
                                        source_doi=None, # optional; authored descriptive metadata
                                        process_title=None, # optional; authored
                                        flowsheet_designers=None, # optional; authored
                                        reproducibility=None, # optional recipe block; see pisces_sff._runner
                                        sff_version='0.1.2', # must match this function's name suffix
                                        ):
    """
    Export a simulated BioSTEAM system against SFF schema v0.1.2.

    Adds one optional, additive field over the v0.1.1 export: each unit gains a
    ``purchase_cost_correlations`` object (from biosteam ``@cost`` ``cost_items``),
    recording the parametric purchase-cost correlation behind each cost item so a
    consumer can re-derive equipment cost at a different design size or cost-year
    index (CEPCI). The field is present on every unit -- ``{}`` for
    procedurally-costed units with no ``cost_items``. Every existing field is
    byte-identical to the 0.1.1 export apart from metadata.sff_version and the new
    key. All version-gated behavior active at 0.1.1 remains so here.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    source_doi : str, optional
        DOI of the source publication. Emitted only when truthy.
    process_title : str, optional
        Descriptive title for the process. Emitted only when truthy.
    flowsheet_designers : str, optional
        Name(s) of the flowsheet's authors. Emitted only when truthy.
    reproducibility : dict, optional
        Recipe block written to ``metadata['reproducibility']``. Built by
        :func:`pisces_sff._runner.build_reproducibility`. Omitted when falsy.
    sff_version : str, optional
        Version recorded as ``metadata['sff_version']``.
    """
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        source_doi=source_doi, process_title=process_title,
        flowsheet_designers=flowsheet_designers,
        sff_version=sff_version,
    )
    if reproducibility:
        flowsheet_to_export['metadata']['reproducibility'] = reproducibility
    _write_sff_json(flowsheet_to_export, filepath)


def export_biosteam_flowsheet_sff_0_1_3(sys, filepath, tea=None,
                                        stoichiometry="dict", # must be one of (None, "vector", "dict")
                                        microorganisms=None, # optional list of microbial hosts
                                        source_doi=None, # optional; authored descriptive metadata
                                        process_title=None, # optional; authored
                                        flowsheet_designers=None, # optional; authored
                                        reproducibility=None, # optional recipe block; see pisces_sff._runner
                                        sff_version='0.1.3', # must match this function's name suffix
                                        ):
    """
    Export a simulated BioSTEAM system against SFF schema v0.1.3.

    Adds one optional, additive field over the v0.1.2 export: ``metadata.tags``,
    stamped **conditionally**. After assembling the document at 0.1.2 fidelity and
    attaching any reproducibility recipe, the exporter computes the static tags the
    document earns (via the validator's static earning rule) and stamps
    ``["exported-from-simulator"]`` **only if the export actually earns it** -- so
    a rich export (like corn, which carries a recipe and full results) stamps it,
    while a sparser export simply omits the key rather than writing a false claim.
    The exporter never stamps ``reproducible`` (that needs a second harness run to
    prove; see regenerate_corpus's opt-in stamping pass). Every other field is
    byte-identical to the 0.1.2 export apart from metadata.sff_version and the
    conditionally-present metadata.tags. Because tag earning requires a digest-valid
    reproducibility recipe (MET-07 must not skip), a direct export without one omits
    the tag. All version-gated behavior active at 0.1.2 remains so here.

    Parameters
    ----------
    sys : biosteam.System
        A simulated system to export.
    filepath : str
        Path to write the SFF JSON file to.
    tea : biosteam.TEA, optional
        TEA object to read cost assumptions from. Defaults to ``sys.TEA``.
    stoichiometry : str, optional
        One of ``None``, ``'vector'``, or ``'dict'``.
    microorganisms : list, optional
        Microbial hosts; each entry is a string or a dict with a ``'name'`` key.
    source_doi : str, optional
        DOI of the source publication. Emitted only when truthy.
    process_title : str, optional
        Descriptive title for the process. Emitted only when truthy.
    flowsheet_designers : str, optional
        Name(s) of the flowsheet's authors. Emitted only when truthy.
    reproducibility : dict, optional
        Recipe block written to ``metadata['reproducibility']``. Built by
        :func:`pisces_sff._runner.build_reproducibility`. Omitted when falsy.
    sff_version : str, optional
        Version recorded as ``metadata['sff_version']``.
    """
    flowsheet_to_export = _build_sff_dict(
        sys, tea=tea, stoichiometry=stoichiometry,
        microorganisms=microorganisms,
        source_doi=source_doi, process_title=process_title,
        flowsheet_designers=flowsheet_designers,
        sff_version=sff_version,
    )
    if reproducibility:
        flowsheet_to_export['metadata']['reproducibility'] = reproducibility
    # Stamp AFTER attaching the recipe: exported-from-simulator earning requires a
    # digest-valid reproducibility block (MET-07 must not skip).
    if version_tuple(sff_version) >= _TAGS_SINCE:
        _stamp_static_tags(flowsheet_to_export)
    _write_sff_json(flowsheet_to_export, filepath)


#%% Helper functions

def _max_carbon_boundary_feed(all_sys_feeds):
    """Return the boundary feed (``source`` is None) with the highest carbon
    atomic flow, or ``None`` when no boundary feed carries carbon.

    ``get_atomic_flow('C')`` is read once per feed. Ties resolve to the first
    feed reaching the maximum (strict ``<`` update), matching the historical
    selection so exporter output stays byte-stable.
    """
    max_C_atomic_flow = 0.0
    max_C_atomic_flow_stream = None
    for si in all_sys_feeds:
        if si.source:
            continue
        c_flow = si.get_atomic_flow('C')
        if max_C_atomic_flow < c_flow:
            max_C_atomic_flow = c_flow
            max_C_atomic_flow_stream = si
    return max_C_atomic_flow_stream


def is_feedstock(stream, all_sys_feeds, max_carbon_feed=None):
    """Return whether `stream` is the system's feedstock.

    A stream qualifies when it is a named boundary feed and is the highest-carbon
    boundary feed of the system. `max_carbon_feed` may be supplied (e.g. by
    :func:`_build_sff_dict`, which computes it once for the whole export) to skip
    the per-call feed scan; when omitted it is computed from `all_sys_feeds`.
    """
    if stream.ID == "":
        return False
    if not stream in all_sys_feeds:
        return False
    # if not stream.price
    #     return False
    if max_carbon_feed is None:
        max_carbon_feed = _max_carbon_boundary_feed(all_sys_feeds)
    return stream == max_carbon_feed


def is_product(stream, all_sys_products):
    if not stream in all_sys_products:
        return False
    if (not stream.cost>0.0):
        return False
    return True


def get_stream_roles(stream, all_sys_feeds, all_sys_products, max_carbon_feed=None):
    """Return the roles a stream plays (see design doc section 1).

    Exactly one base topology role is derived from the real source/sink objects
    (Python ``None``), not the ``"None"`` sentinel string written to
    ``source_unit_id`` / ``sink_unit_id``:

    - ``internal`` -- has both a source and a sink,
    - ``input``    -- has a sink but no source,
    - ``output``   -- has a source but no sink.

    Inputs additionally carry ``purchased_raw_material`` when priced
    (``price > 0``) and ``feedstock`` when ``is_feedstock`` selects them; the two
    can co-occur. Outputs additionally carry ``product`` when ``is_product``
    selects them. Order is deterministic (base role first, then
    ``purchased_raw_material``, ``feedstock``, ``product``) so exporter output
    stays byte-stable.

    Parameters
    ----------
    stream : thermosteam.Stream
        The stream to classify.
    all_sys_feeds : list
        ``sys.feeds``; passed through to :func:`is_feedstock`.
    all_sys_products : list
        ``sys.products``; passed through to :func:`is_product`.

    Returns
    -------
    list of str
        One base role followed by any designation roles, from the enum
        ``["input", "output", "purchased_raw_material", "feedstock", "product",
        "internal"]``.
    """
    roles = []
    has_source = stream.source is not None
    has_sink = stream.sink is not None
    if has_source and has_sink:
        roles.append("internal")
    elif has_sink:                       # input: has sink, no source
        roles.append("input")
        if stream.price and stream.price > 0:
            roles.append("purchased_raw_material")
        if is_feedstock(stream, all_sys_feeds, max_carbon_feed):
            roles.append("feedstock")
    elif has_source:                     # output: has source, no sink
        roles.append("output")
        if is_product(stream, all_sys_products):
            roles.append("product")
    return roles


def format_name(name):
    if not name:
        return ""
    if name.isupper(): # all caps name
        return name
    ## specific formatting
    if name=='TAL_product':
        return 'Triacetic acid lactone'
    if name=='KSA_product':
        return 'Potassium sorbate'
    name = name.replace("_feedstock", "").replace("_product", "")
    name = name.replace("nstover", "n stover")
    name = name.replace("glucose", "dextrose").replace("Glucose", "Dextrose")
    if "dextrose" in name or "Dextrose" in name:
        name = name.replace(" monohydrate", "").replace(" Monohydrate", "").replace("monohydrate", "").replace("Monohydrate", "")
    
    ## general formatting
    words = []
    current = name[0]
    for char in name[1:]:
        if char.isupper() and not current[-1].isupper():
            words.append(current)
            current = char
        else:
            current += char

    words.append(current)
    result = " ".join(words).lower()
    
    return result.capitalize()


def get_required_args(func):
    signature = inspect.signature(func)
    required_params = []
    for name, param in signature.parameters.items():
        if param.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD and param.default is inspect.Parameter.empty:
            required_params.append(name)
    return required_params


def get_thermo(unit):
    raw_thermo = rt = unit.thermo
    thermo = {"mixture": rt.mixture.__str__().replace('..., ', ''),
              "gamma": rt.Gamma.__name__,
              "phi": rt.Phi.__name__,
              "PCF": rt.PCF.__name__.replace('Poyinting', 'Poynting')}
    return thermo


def get_utility_results(unit):
    hus = unit.heat_utilities if hasattr(unit, 'heat_utilities') else []
    # power_utility / natural_gas may be present but None (attribute defined, no
    # utility attached); treat that like an absent attribute rather than
    # dereferencing None (pu.consumption / ou.F_mass) and aborting the export.
    power_utility = getattr(unit, 'power_utility', None)
    natural_gas = getattr(unit, 'natural_gas', None)
    pus = [power_utility] if power_utility is not None else []
    ous = [natural_gas] if natural_gas is not None else []
    
    u_cons = {}
    u_prod = {}
    
    hu_agents = set()
    for hu in hus:
        if hu.agent is None: continue
        hu_agents.add(hu.agent)
        if hu.duty>0: 
            if not hu.agent.ID in u_cons.keys():
                u_cons[hu.agent.ID] = hu.duty
            else:
                u_cons[hu.agent.ID] += hu.duty
                
        else: 
            if not hu.agent.ID in u_prod.keys():
                u_prod[hu.agent.ID] = hu.duty
            else:
                u_prod[hu.agent.ID] += hu.duty
                
    pu_agents = set([PowerUtility])
    for pu in pus:
        if pu.consumption>0: u_cons['Marginal grid electricity'] = pu.consumption
        if pu.production>0: u_prod['Marginal grid electricity'] = pu.production
    
    ou_agents = set()
    for ou in ous:
        ou_agents.add(ou)
        if ou.F_mass>0: 
            if not ou.ID in u_cons.keys():
                u_cons[ou.ID] = ou.F_mass
            else:
                u_cons[ou.ID] += ou.F_mass
                
        else: 
            if not ou.ID in u_prod.keys():
                u_prod[ou.ID] = ou.F_mass
            else:
                u_prod[ou.ID] += ou.F_mass
    return u_cons, u_prod, hu_agents, pu_agents, ou_agents


def get_purchase_cost_correlations(ru):
    """Serialize a unit's parametric purchase-cost correlations.

    Reads the BioSTEAM ``@cost`` machinery -- the class-level ``cost_items``
    (``{item_ID: CostItem}``), the instance bare-module factors ``F_BM`` (which
    honor user overrides), and ``equipment_lifetime`` -- into the SFF
    ``purchase_cost_correlations`` shape (sff_checks.md UNIT-08/UNIT-09), keyed
    by the same item IDs used in ``purchase_costs``. A ``custom_function`` item
    (``CostItem.f`` is not None) omits ``reference_cost``/``exponent`` because
    the correlation is an opaque callable.

    Parameters
    ----------
    ru : biosteam.Unit
        The unit to read. A unit with no ``cost_items`` returns ``{}``.

    Returns
    -------
    dict
        ``{item_ID: correlation_dict}``; empty when the unit carries no
        ``cost_items``. Never raises: a missing or malformed attribute degrades
        to omitting that field or that whole item, per the exporter's hardening
        conventions.
    """
    cost_items = getattr(ru, 'cost_items', None)
    if not cost_items:
        return {}
    if not isinstance(cost_items, dict):
        # Honor the never-raises contract if cost_items is a truthy non-dict
        # (no .items()); real biosteam always gives a dict, but degrade anyway.
        return {}
    f_bm = getattr(ru, 'F_BM', None) or {}
    lifetimes = _equipment_lifetimes(ru)
    out = {}
    for item_id, item in cost_items.items():
        try:
            out[item_id] = _cost_correlation_entry(item_id, item, f_bm, lifetimes)
        except Exception:
            # One oddly-shaped item never sinks the export; omit just this item.
            continue
    return out


def _stamp_static_tags(doc):
    """Stamp metadata.tags with the static tags this assembled document actually
    earns, for the 0.1.3+ exporter. Only ``exported-from-simulator`` is stamped
    (the provenance of a simulator export); ``reproducible`` is never stamped by
    the exporter (it needs a second harness run to prove). Stamping only when
    earned guarantees the exporter never writes a self-contradictory file (a
    stamped-but-unearned tag would be a TAG-01 error)."""
    from . import _validate
    with open(_validate._SCHEMA_FILE, 'r', encoding='utf-8') as f:
        schema = json.load(f)
    ctx, results = _validate._run_all_checks(doc, schema)
    verdict = _validate._earned_tags(ctx, results)
    if verdict['exported-from-simulator']['earned']:
        doc['metadata']['tags'] = ['exported-from-simulator']


def _equipment_lifetimes(ru):
    """Normalize ``unit.equipment_lifetime`` to a ``{ID: years}`` lookup.

    BioSTEAM stores either an ``int`` (one lifetime for every item) or a
    ``{ID: years}`` dict. An ``int`` is returned under the sentinel key ``None``,
    which :func:`_cost_correlation_entry` consults as a fallback for any id.
    """
    el = getattr(ru, 'equipment_lifetime', None)
    if isinstance(el, dict):
        return dict(el)
    if isinstance(el, int) and not isinstance(el, bool):
        return {None: el}
    return {}


def _cost_correlation_entry(item_id, item, f_bm, lifetimes):
    """Build one ``purchase_cost_correlations`` entry from a BioSTEAM CostItem.

    Field order is fixed for byte-stable output; sparse fields are appended only
    when non-default (sff_checks.md UNIT-09 sparse-field rule).
    """
    is_custom = getattr(item, 'f', None) is not None
    entry = {'correlation_type': 'custom_function' if is_custom else 'power_law',
             'basis': str(item.basis),
             'basis_units': str(item.units),
             'reference_size': float(item.S)}
    if not is_custom:
        entry['reference_cost'] = float(item.cost)
        entry['exponent'] = float(item.n)
    entry['reference_CE_index'] = float(item.CE)
    entry['installation_factor'] = float(f_bm.get(item_id, 1.0))
    entry['power_rate'] = float(getattr(item, 'kW', 0.0) or 0.0)
    if getattr(item, 'lb', None) is not None:
        entry['size_lower_bound'] = float(item.lb)
    if getattr(item, 'ub', None) is not None:
        entry['size_upper_bound'] = float(item.ub)
    lifetime = lifetimes.get(item_id, lifetimes.get(None))
    if lifetime is not None:
        entry['lifetime'] = int(lifetime)
    if getattr(item, 'magnitude', False):
        entry['magnitude'] = True
    if getattr(item, 'condition', None) is not None:
        entry['conditional'] = True
    return entry


def get_chemical_entry(chemical, index, is_vle, stoichiometry):
    """Build one SFF ``chemicals[]`` entry from a thermosteam ``Chemical``.

    Parameters
    ----------
    chemical : thermosteam.Chemical
        The chemical to serialize.
    index : int
        Position of this chemical in the thermo package; emitted as ``index``
        only when `stoichiometry` is truthy (index-based reaction stoichiometry
        references it -- see CHEM-04).
    is_vle : bool
        Whether the chemical participates in vapor-liquid equilibrium (is in the
        stream's ``vle_chemicals``); recorded as ``included_in_thermo``.
    stoichiometry : str or None
        Reaction-stoichiometry mode; when truthy, ``index`` is emitted.

    Returns
    -------
    dict
        A ``chemicals[]`` entry. Key order (``id``, ``included_in_thermo``,
        ``index``?, ``formula``?, ``registry_id``?, ``molar_mass``) is preserved
        for byte-stable output.

    Raises
    ------
    SFFExportError
        If the chemical is VLE (``included_in_thermo`` true) but exposes no CAS.
        The schema requires ``registry_id`` for such chemicals, so a missing CAS
        would otherwise emit ``registry_id: null`` and produce a document that
        fails validation. Fail loudly here, naming the chemical, rather than
        writing an invalid file.
    """
    c = chemical
    entry = {"id": c.ID}
    entry["included_in_thermo"] = is_vle
    if stoichiometry:
        entry["index"] = index
    if c.formula is not None:
        entry["formula"] = c.formula
    if is_vle:
        if not c.CAS:
            raise SFFExportError(
                f"chemical {c.ID!r} is included in thermo (VLE) but exposes no "
                "CAS registry id; SFF requires registry_id for such chemicals. "
                "Provide a CAS on the chemical or exclude it from thermo."
            )
        entry["registry_id"] = c.CAS
    entry["molar_mass"] = c.MW
    return entry


def get_composition(stream,
                    units='both', # 'mol%', 'mass%', or 'both'
                    ):
    s = stream
    phases = s.phases
    chem_IDs = [chem.ID for chem in list(s.chemicals)]
    # if isinstance(s, MultiStream):
    # if len(s.phases)>1:
    comp = []
    for p in phases:
        sp = s[p]
        if sp.F_mol <= ZERO_FLOW:
            continue  # empty phase (STR-13): no components listed
        for c in chem_IDs:
            if sp.imol[c]>0:
                comp.append({'phase':p, 'component_name':c})
                if units in ('mol%',):
                    comp[-1]['mol_fraction'] = sp.imol[c]/sp.F_mol
                elif units in ('mass%',):
                    comp[-1]['mass_fraction'] = sp.imass[c]/sp.F_mass
                elif units in ('both',):
                    comp[-1]['mol_fraction'] = sp.imol[c]/sp.F_mol
                    comp[-1]['mass_fraction'] = sp.imass[c]/sp.F_mass
    return comp


def get_phase_properties(stream, inline):
    """
    Build the per-phase properties block for a stream (SFF v0.0.9+).

    Returns a dict keyed by phase symbol ('l', 'g', 's', ...). Each value carries
    that phase's own total molar/mass/volumetric flows and its molar and mass
    composition, with all fractions taken relative to that phase. A phase with
    zero molar flow contributes no components (matching get_composition's
    ``sp.imol[c] > 0`` guard), so its ``composition`` is an empty list.

    Parameters
    ----------
    stream : thermosteam.Stream
        A simulated stream.
    inline : bool
        Passed through to :func:`scalar`: True emits inline ``{"value","units"}``
        pairs (pre-0.0.7 shape), False emits bare numbers whose units come from
        ``quantity_units_global``.

    Returns
    -------
    dict
        ``{phase_symbol: {"total_mass_flow", "total_molar_flow",
        "total_volumetric_flow"?, "composition"}}``.
    """
    s = stream
    chem_IDs = [chem.ID for chem in list(s.chemicals)]
    phases = {}
    for p in s.phases:
        sp = s[p]
        phase = {
            "total_mass_flow": scalar(sp.F_mass, "kg/h", inline),
            "total_molar_flow": scalar(sp.F_mol, "kmol/h", inline),
        }
        try:
            phase["total_volumetric_flow"] = scalar(sp.F_vol, "m3/h", inline)
        except Exception as e:
            # Same optional-field fallback as the whole-stream volumetric flow:
            # a missing liquid molar volume method is expected; anything else is
            # unexpected but still non-fatal. Omit the key and warn only on the
            # unexpected cause.
            if 'liquid molar volume method' not in str(e).lower():
                warnings.warn(
                    f"could not compute total_volumetric_flow for phase {p!r} "
                    f"of stream {getattr(s, 'ID', s)!r}; omitting it: {e}",
                    stacklevel=2,
                )
        composition = []
        # An empty phase (STR-13) lists no components, even if a vanishing
        # numerical residue is present.
        for c in (chem_IDs if sp.F_mol > ZERO_FLOW else ()):
            if sp.imol[c] > 0:
                composition.append({
                    "component_name": c,
                    "mol_fraction": sp.imol[c] / sp.F_mol,
                    "mass_fraction": sp.imass[c] / sp.F_mass,
                })
        phase["composition"] = composition
        phases[p] = phase
    return phases


def get_reaction_objects(unit):
    """
    Return the Reaction/ReactionSet objects attached to a unit, in the order
    :func:`get_reactions` emits them (discovery order, children of a collected
    parent dropped). Shared by :func:`get_reactions` and the chemical-registry
    union, so the two cannot disagree about which reactions a unit carries.
    """
    u = unit
    rxntypes = (Reaction, ReactionSet)
    # Deterministic ordering policy: emit reactions in discovery order -- the
    # insertion order of unit.__dict__, i.e., the order the model author
    # assigned them. That order is deterministic across processes and
    # engineering-meaningful, unlike iteration over a set of id()-hashed
    # objects (the previous behavior, which scrambled the emitted `index`
    # field differently on every run). Dedup and the parent filter below use
    # object identity, not equality: Reaction objects are not reliably
    # hashable-by-value, and the same object bound to two attribute names
    # must be emitted once.
    collected = []
    seen = set()
    for rxn in u.__dict__.values():
        if isinstance(rxn, rxntypes) and id(rxn) not in seen:
            seen.add(id(rxn))
            collected.append(rxn)
    # Drop a child reaction whose parent ReactionSet was also collected (the
    # parent's own iteration emits it). Membership is tested against the full
    # collected id-set; order is preserved by filtering the list.
    collected_ids = {id(rxn) for rxn in collected}
    all_reactions = []
    for rxn in collected:
        if hasattr(rxn, '_parent'):
            if id(rxn._parent) in collected_ids:
                continue
        elif hasattr(rxn, '_parent_index'):
            parent, _index = rxn._parent_index
            if id(parent) in collected_ids:
                continue
        all_reactions.append(rxn)
    return all_reactions


def get_additional_chemical_entries(base_chemicals, streams, units, agents,
                                    stoichiometry):
    """
    Build ``chemicals[]`` entries for every chemical referenced by the document
    that is NOT in `base_chemicals` (the representative stream's set).

    Walks, in order, every stream's chemicals set, every unit's reactions'
    chemicals sets, and every utility agent's chemicals set; a chemicals set
    already visited (by identity) is skipped, and within a new set only IDs not
    yet registered are appended. Registry ``index`` values continue from
    ``len(base_chemicals)``; ``is_vle`` comes from the owning set's VLE index
    (sff_checks.md STR-07 / UTIL-04 / UNIT-04 / UNIT-06 -- every referenced
    component name resolves to a registry entry).

    Parameters
    ----------
    base_chemicals : thermosteam.CompiledChemicals
        The set already serialized.
    streams, units, agents : iterable
        Objects whose ``.chemicals`` (or, for units, reactions' ``.chemicals``)
        sets are unioned in.
    stoichiometry : str or None
        Forwarded to :func:`get_chemical_entry`.

    Returns
    -------
    list of dict
        Entries in discovery order; empty for a single-thermo system.
    """
    registered = {c.ID for c in base_chemicals}
    visited = {id(base_chemicals)}
    entries = []
    index = len(base_chemicals)

    def sets():
        for obj in streams:
            yield getattr(obj, 'chemicals', None)
        for unit in units:
            for rxn in get_reaction_objects(unit):
                yield getattr(rxn, 'chemicals', None)
        for agent in agents:
            yield getattr(agent, 'chemicals', None)

    for chem_set in sets():
        if chem_set is None or id(chem_set) in visited:
            continue
        visited.add(id(chem_set))
        vle_index = set(getattr(chem_set, '_vle_index', ()))
        for i, c in enumerate(chem_set):
            if c.ID in registered:
                continue
            registered.add(c.ID)
            entries.append(get_chemical_entry(c, index, i in vle_index,
                                              stoichiometry))
            index += 1
    return entries


def get_reactions(unit, stoichiometry):
    all_reactions = get_reaction_objects(unit)
    reactions = []
    
    i = 0
    for rxn in all_reactions:
        if isinstance(rxn, (SeriesReaction, ParallelReaction)):
            is_series = isinstance(rxn, SeriesReaction)
            is_parallel = isinstance(rxn, ParallelReaction)
            for r in rxn:
                reaction = {"index": i,
                            "equation": get_equation(r),
                            "reactant": r.reactant,
                            "conversion": r.X,
                            }
                if stoichiometry is not None:
                    stoich_list = np.array(r.stoichiometry).tolist()
                    if stoichiometry=="vector":
                        reaction["stoichiometry"] = stoich_list
                    elif stoichiometry=="dict":
                        reaction["stoichiometry"] = {}
                        chems_list = list(r.chemicals)
                        for chem, stoich in zip(chems_list, stoich_list):
                            if not stoich==0:
                                reaction["stoichiometry"][chem.ID] = stoich
                reactions.append(reaction)
                if is_series: i+=1
            if is_parallel: i+=1
        else:
            reaction = {"index": i,
                        "equation": get_equation(rxn),
                        "reactant": rxn.reactant,
                        "conversion": rxn.X,
                        }
            if stoichiometry is not None:
                stoich_list = np.array(rxn.stoichiometry).tolist()
                if stoichiometry=="vector":
                    reaction["stoichiometry"] = stoich_list
                elif stoichiometry=="dict":
                    reaction["stoichiometry"] = {}
                    chems_list = list(rxn.chemicals)
                    for chem, stoich in zip(chems_list, stoich_list):
                        if not stoich==0:
                            reaction["stoichiometry"][chem.ID] = stoich
            reactions.append(reaction)
            i+=1
    
    return reactions


def trace_function_calls(A, F):
    """
    Traces the order of function calls when A is called and returns 
    the ordered list of functions from F that were called.

    Parameters
    ----------
    A : function
        The function to trace.
    F : list of functions
        List of functions to track.

    Returns
    -------
    list of functions
        Ordered list of functions from F that were called during A.
    """
    called = []

    # Create a set of function code objects for fast lookup
    target_codes = {func.__code__ for func in F}

    def tracer(frame, event, arg):
        if event == 'call':
            if frame.f_code in target_codes:
                for func in F:
                    if func.__code__ is frame.f_code:
                        called.append(func)
                        break
        return tracer

    sys.setprofile(tracer)
    try:
        A()
    finally:
        sys.setprofile(None)

    return called


def get_equation(rxn):
    # fullstr = rxn.__str__()
    # return fullstr[fullstr.index("'")+1:fullstr[fullstr.index("'")+1:].index("'")+len(fullstr[:fullstr.index("'")+1])]
    return get_stoichiometric_string(stoichiometry=rxn.stoichiometry, phases=rxn.phases, chemicals=rxn.chemicals)


def get_unit_type(unit):
    return unit.line


    # classpath = str(unit.__class__)
    # classpath = classpath[classpath.index("'")+1:]
    # classpath = classpath[:classpath.index("'")]
    
    # classname = classpath[classpath.rfind('.')+1:]
    
    # return classname


    # words = re.findall('[A-Z][^A-Z]*', classname)
    # unit_type = ''
    # for i in words:
    #     unit_type += i + ' '
    # return unit_type[:-1]
    
def get_design_simulation_method(unit):
    classpath = str(unit.__class__)
    classpath = classpath[classpath.index("'")+1:]
    classpath = classpath[:classpath.index("'")]
    
    classname = classpath[classpath.rfind('.')+1:]
    classpath = classpath.replace('.'+classname, '')
    classpath = classpath.replace('.', '/')
    
    link_address = ''
    
    if 'biosteam/' in classpath:
        link_address = 'https://github.com/BioSTEAMDevelopmentGroup/biosteam/blob/master/' + classpath + '.py'
    elif 'biorefineries/' in classpath:
        link_address = 'https://github.com/BioSTEAMDevelopmentGroup/Bioindustrial-Park/blob/master/' + classpath + '.py'
    
    return classname + ' on ' + link_address


def get_design_input_specs(unit): # !!! update
    param_names = ('LHK', 'Lr', 'Hr', 'x_bot', 'y_top', 'k', 
                   'T', 'P', 
                   'V', 'V_wf',
                   'tau',)
    dis = {}
    for p in param_names:
        if hasattr(unit, p):
            try:
                dis[p] = getattr(unit, p)
            except Exception as e:
                raise DesignInputSpecError(
                    f"could not read design input spec {p!r} for unit "
                    f"{getattr(unit, 'ID', unit)!r}: {e}"
                ) from e
    return dis
