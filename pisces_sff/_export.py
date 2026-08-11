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
import numpy as np

from collections import deque
from math import isfinite
from numbers import Real
from types import FunctionType

from thermosteam import Reaction, ReactionSet, SeriesReaction, ParallelReaction
from thermosteam import Chemical
from thermosteam.reaction._reaction import get_stoichiometric_string
from biosteam import PowerUtility, System

import biosteam as bst

from ._version import CURRENT_SFF_VERSION

__all__ = ('export_biosteam_flowsheet',)


def _json_default(value):
    """Convert NumPy values emitted by BioSTEAM to JSON-native values.

    BioSTEAM stores results as NumPy scalars/arrays (and occasionally a deque),
    which json.dump cannot serialize on its own. Passed as json.dump(default=...),
    this is called only for those non-native objects and hands back a plain
    Python equivalent. Unknown types raise TypeError instead of silently
    producing a broken document, so a new container surfaces loudly.
    """
    if isinstance(value, np.generic):      # NumPy scalar (e.g. np.float64) -> Python scalar
        return value.item()
    if isinstance(value, np.ndarray):      # NumPy array -> nested list
        return value.tolist()
    if isinstance(value, deque):           # collections.deque -> list (seen in the acTAG model)
        return list(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _finite_mapping(mapping):
    """Drop keys whose numeric value is undefined (NaN/inf).

    Some BioSTEAM cost/design results are NaN or infinity when a unit is not
    fully specified. We omit the key rather than substitute a value because the
    schema types these results as `number`: NaN has no JSON numeric literal, and
    `null` is not a `number`, so both would fail validation. A missing optional
    key does validate, and it correctly means "not computed" rather than
    "explicitly none". Non-numeric values pass through untouched; the
    `allow_nan=False` guard on json.dump is the backstop that fails loudly on any
    non-finite value we miss.
    """
    return {
        key: value
        for key, value in mapping.items()
        if not isinstance(value, Real) or isfinite(value)
    }

#%% Entry-point export function

def export_biosteam_flowsheet(sys, filepath, sff_version, **kwargs):
    sff_version_formatted = sff_version.replace('.', '_')
    exec(f'export_biosteam_flowsheet_sff_{sff_version_formatted}(sys, filepath, **kwargs)')

#%% Export function for SFF schema v0.0.5
def export_biosteam_flowsheet_sff_0_0_5(sys, filepath, tea=None, 
                                        stoichiometry="dict", # must be one of (None, "vector", "dict")
                                        composition_units="both", # "mol%", "mass%", or "both"
                                        ):
    f = sys.flowsheet
    u, s = sys.units, sys.streams
    all_streams = list(s)
    all_sys_feeds = list(sys.feeds)
    all_sys_products = list(sys.products)
    if tea is None:
        tea = sys.TEA
    
    ## ------- Metadata ------- ## 
    metadata = {}
    # Stamp the real emitted version. This entry point was hardcoding '0.0.3'
    # while producing v0.0.5 output; sourcing the single CURRENT_SFF_VERSION
    # constant keeps the stamp honest and updatable in one place.
    metadata['sff_version'] = CURRENT_SFF_VERSION
    metadata['TEA_year'] = tea.duration[0]
    metadata['process_simulator'] = {'name': 'BioSTEAM',
                                     'version': bst.__version__}
    metadata['feedstocks'] = [{"display_name": format_name(stream.ID), "stream_id": stream.ID} 
                              for stream in all_streams if is_feedstock(stream, all_sys_feeds)]
    metadata['products'] = [{"display_name": format_name(stream.ID), "stream_id": stream.ID} 
                            for stream in all_streams if is_product(stream, all_sys_products)]
                
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
                # _finite_mapping strips NaN/inf entries these BioSTEAM result dicts
                # can hold for under-specified units. The schema types cost values
                # as `number`, so neither NaN (no JSON numeric literal) nor `null`
                # (not a `number`) would validate; a missing optional key does, and
                # it correctly reads as "not computed" rather than "explicitly none".
                "design_results": _finite_mapping(ru.design_results) if hasattr(ru, 'design_results') else {},
                "installed_costs": _finite_mapping(ru.installed_costs) if hasattr(ru, 'installed_costs') else {},
                "purchase_costs": _finite_mapping(ru.purchase_costs) if hasattr(ru, 'purchase_costs') else {},
                "utility_consumption_results": u_cons,
                "utility_production_results": u_prod,
                }
        units.append(unit)
        
    ## ------ Streams ------ ##
    streams = []
    for raw_stream in all_streams:
        rs = raw_stream
        if not (rs.source or rs.sink): continue # skip isolated streams
        stream = {"id": rs.ID,
                  "source_unit_id": rs.source.ID if rs.source is not None else "None",
                  "sink_unit_id": rs.sink.ID if rs.sink is not None else "None",
                  "price": {"value": rs.price, "units": "$/kg"},
                  "stream_properties": {
                      "total_mass_flow": {"value": rs.F_mass, "units": "kg/h"},
                      "total_molar_flow": {"value": rs.F_mol, "units": "kmol/h"},
                      "temperature": {"value": rs.T, "units": "K"},
                      "pressure": {"value": rs.P, "units": "Pa"},
                      "composition": get_composition(rs),
                      }
                  }
        try:
            stream["stream_properties"]["total_volumetric_flow"] = {"value": rs.F_vol, "units": "m3/h"}
        except Exception as e:
            # Volumetric flow is optional: some streams legitimately lack a liquid
            # molar volume method, so that one known case is skipped. Any other
            # failure is a real bug and re-raised (this previously dropped into an
            # interactive debugger, which hangs an unattended export).
            if 'liquid molar volume method' in str(e).lower():
                pass
            else:
                raise
        streams.append(stream)
    
    ## ------ Chemicals ------ ##
    chemicals = []
    repr_stream = all_streams[0] # !!! future: add support for multiple CompiledChemicals object (i.e., multiple sets of chemicals) within a single system
    chems = repr_stream.chemicals
    vle_chems = repr_stream.vle_chemicals
    for i, c in zip(range(len(chems)), chems):
        is_vle = c in vle_chems
        chemical = {"id": c.ID,
                    }
        chemical["included_in_thermo"] = is_vle
        if stoichiometry: 
            chemical["index"] = i
        if c.formula is not None:
            chemical["formula"] = c.formula
        if is_vle:
            chemical["registry_id"] = c.CAS
        chemical["molar_mass"] = c.MW
            
        chemicals.append(chemical)
        
    ## ----- Utilities ----- ##
    heat_utilities = []
    for hu_agent in all_hu_agents:
        hu = {
              "id": hu_agent.ID,
              "temperature": {"value": hu_agent.T, "units": "K"},
              "pressure": {"value": hu_agent.P, "units": "Pa"},
              "regeneration_price": {"value": hu_agent.regeneration_price, "units": "$/kmol"},
              "heat_transfer_price": {"value": hu_agent.heat_transfer_price, "units": "$/kJ"},
              "heat_transfer_efficiency": hu_agent.heat_transfer_efficiency if hu_agent.heat_transfer_efficiency is not None else 1.0,
              "composition": get_composition(hu_agent),
              "units_for_utility_results": "kJ/h",
              }
        heat_utilities.append(hu)
        
    power_utilities = []
    for pu_agent in all_pu_agents:
        pu = {"id": "Marginal grid electricity",
              "price": {"value": pu_agent.price, "units": "$/kWh"},
              "units_for_utility_results": "kW",
              }
        power_utilities.append(pu)
    
    other_utilities = []
    for ou_agent in all_ou_agents:
        ou = {
              "id": ou_agent.ID,
              "temperature": {"value": ou_agent.T, "units": "K"},
              "pressure": {"value": ou_agent.P, "units": "Pa"},
              "price": {"value": ou_agent.price or ng_price, "units": "$/kg"},
              "units_for_utility_results": "kg/h",
              "composition": get_composition(ou_agent),
              }
        other_utilities.append(ou)
    
    # Export
    flowsheet_to_export = {"metadata": metadata,
                           "units": units,
                           "streams": streams,
                           "chemicals": chemicals,
                           "utilities": {"heat_utilities": heat_utilities,
                                          "power_utilities": power_utilities,
                                          "other_utilities": other_utilities},
                           }
    # Write the document, or fail loudly. Previously a bare `except` swallowed
    # every write error into an interactive debugger, which silently stalls an
    # unattended export. `default=_json_default` converts BioSTEAM's NumPy/deque
    # values; `allow_nan=False` makes json.dump raise on any NaN/inf that slipped
    # past _finite_mapping, so we never emit non-standard JSON tokens.
    with open(filepath, "w") as json_file:
        json.dump(
            flowsheet_to_export,
            json_file,
            indent=4,
            default=_json_default,
            allow_nan=False,
        )
        
#%% Helper functions

def is_feedstock(stream, all_sys_feeds):
    if stream.ID == "":
        return False
    if not stream in all_sys_feeds:
        return False
    # if not stream.price
    #     return False
    max_C_atomic_flow = 0.0
    max_C_atomic_flow_stream = None
    for si in list(all_sys_feeds):
        if (not si.source) and max_C_atomic_flow < si.get_atomic_flow('C'):
            max_C_atomic_flow = si.get_atomic_flow('C')
            max_C_atomic_flow_stream = si
    if stream == max_C_atomic_flow_stream:
        return True
    return False


def is_product(stream, all_sys_products):
    if not stream in all_sys_products:
        return False
    if (not stream.cost>0.0):
        return False
    return True


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
    hus = unit.heat_utilities if hasattr(unit, 'heat_utilities') else {}
    pus = [unit.power_utility] if hasattr(unit, 'power_utility') else {}
    ous = [unit.natural_gas] if hasattr(unit, 'natural_gas') else {}
    
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


def _top_level_reactions(unit):
    """Return each top-level reaction once, in the unit's attribute order.

    Why not the old `{rxn for rxn in ...}` set comprehension: iterating a set of
    reaction objects yields them in hash (memory-address) order, so the same model
    exported twice produced reactions in a different order and the JSON diffed for
    no real reason. Walking `__dict__.values()` instead preserves the deterministic
    order the attributes were defined in, which is what makes exports repeatable.
    """
    rxntypes = (Reaction, ReactionSet)
    # Collect reactions in attribute order, de-duplicating by identity (id) so a
    # reaction referenced under two attribute names is listed only once.
    discovered = []
    seen = set()
    for value in unit.__dict__.values():
        if isinstance(value, rxntypes) and id(value) not in seen:
            discovered.append(value)
            seen.add(id(value))
    # Keep only the outermost reactions: a reaction whose parent set/reaction is
    # itself in `discovered` is a nested child and would otherwise be emitted twice.
    discovered_set = set(discovered)
    top_level = []
    for reaction in discovered:
        parent = getattr(reaction, '_parent', None)
        if parent is None and hasattr(reaction, '_parent_index'):
            parent, _ = reaction._parent_index
        if parent in discovered_set:
            continue
        top_level.append(reaction)
    return top_level


def get_reactions(unit, stoichiometry):
    u = unit
    # Deterministic, de-duplicated top-level reactions (see _top_level_reactions).
    all_reactions = _top_level_reactions(u)
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


def get_rxns_sorted_by_order_of_calls(unit, rxns):
    rxns_sorted = []
    rxn_funcs = [i._reaction for i in rxns]
    rxn_funcs_sorted = trace_function_calls(unit.simulate, rxn_funcs)
    return rxns_sorted


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


def get_design_input_specs(unit):
    param_names = ('LHK', 'Lr', 'Hr', 'x_bot', 'y_top', 'k',
                   'T', 'P',
                   'V', 'V_wf',
                   'tau',)
    dis = {}
    for p in param_names:
        if hasattr(unit, p):
            try:
                # getattr(unit, p) reads the attribute directly. This replaces an
                # earlier `exec(f'dis[p] = unit.{p}')`: because `p` is always one
                # of the fixed param_names above, building and running a code
                # string bought nothing over a plain attribute read, while exec
                # is slower and executes an interpolated string (an injection
                # footgun if param_names ever became caller-supplied).
                dis[p] = getattr(unit, p)
            except Exception:
                # These design inputs are all optional and unit-type specific;
                # reading one can fail (e.g. a property raises for this unit).
                # Skip the missing spec rather than dropping into an interactive
                # debugger, which previously froze an unattended export.
                continue
    return dis
