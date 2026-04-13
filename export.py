# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
# 
# This module is under the MIT open-source license. See 
# https://github.com/sarangbhagwat/sff/blob/main/LICENSE
# for license details.

import json
import inspect
import sys
import re

from types import FunctionType

from thermosteam import Reaction, ReactionSet, SeriesReaction, ParallelReaction
from thermosteam.reaction._reaction import get_stoichiometric_string
from biosteam import PowerUtility, System

#%% Export function

def export_biosteam_flowsheet_sff(sys, file_path):
    f = sys.flowsheet
    u, s = sys.units, sys.streams
    
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
                "reactions": get_reactions(ru),
                "design_results": ru.design_results if hasattr(ru, 'design_results') else {},
                "installed_costs": ru.installed_costs if hasattr(ru, 'installed_costs') else {},
                "purchase_costs": ru.purchase_costs if hasattr(ru, 'purchase_costs') else {},
                "utility_consumption_results": u_cons,
                "utility_production_results": u_prod
                }
        units.append(unit)
        
    ## ------ Streams ------ ##
    streams = []
    for raw_stream in list(s):
        rs = raw_stream
        if not (rs.source or rs.sink): continue # skip isolated streams
        stream = {"id": rs.ID,
                  "source_unit_id": rs.source.ID if rs.source is not None else "None",
                  "sink_unit_id": rs.sink.ID if rs.sink is not None else "None",
                  "price": {"value": rs.price, "units": "$/kg"},
                  "stream_properties": {
                      "total_mass_flow": {"value": rs.F_mass, "units": "kg/h"},
                      "total_molar_flow": {"value": rs.F_mol, "units": "kmol/h"},
                      "total_volumetric_flow": {"value": rs.F_vol, "units": "m3/h"},
                      "temperature": {"value": rs.T, "units": "K"},
                      "pressure": {"value": rs.P, "units": "Pa"},
                      },
                  "composition": get_composition(rs)
                      # [
                      # {"component_name": c.ID,
                      #  "mol_fraction": rs.imol[c.ID]}
                      #  for c in list(rs.chemicals) if rs.imol[c.ID]>0
                      # ]
                  }
        streams.append(stream)
        
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
              "composition": get_composition(ou_agent)
              }
        other_utilities.append(ou)
    
    # Export
    flowsheet_to_export = {"units": units,
                           "streams": streams,
                           "utilities": {"heat_utilities": heat_utilities,
                                          "power_utilities": power_utilities,
                                          "other_utilities": other_utilities}
                           }
    try:
        with open(file_path, "w") as json_file:
            json.dump(flowsheet_to_export, json_file, indent=4)
    except:
        breakpoint()
        
#%% Helper functions

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


def get_composition(stream):
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
                comp.append({'phase':p, 'component_name':c, 'mol_fraction': sp.imol[c]/sp.F_mol})
    return comp


def get_reactions(unit): # !!! update -- fix order of reactions (potentially using settrace)
    u = unit
    rxntypes = (Reaction, ReactionSet)
    all_reactions = {rxn for rxn in u.__dict__.values() if isinstance(rxn, rxntypes)}
    reactions = []
    for rxn in tuple(all_reactions):
        if hasattr(rxn, '_parent') or hasattr(rxn, '_parent_index'):
            if rxn._parent in all_reactions: all_reactions.discard(rxn)
        elif hasattr(rxn, '_parent_index'):
            parent, index = rxn._parent_index
            if parent in all_reactions: all_reactions.discard(rxn)
    
    i = 0
    for rxn in all_reactions:
        if isinstance(rxn, (SeriesReaction, ParallelReaction)):
            is_series = isinstance(rxn, SeriesReaction)
            is_parallel = isinstance(rxn, ParallelReaction)
            for r in rxn:
                reaction = {"index": i,
                            "equation": get_equation(r),
                            "reactant": r.reactant,
                            "conversion": r.X,}
                reactions.append(reaction)
                if is_series: i+=1
            if is_parallel: i+=1
        else:
            reaction = {"index": i,
                        "equation": get_equation(rxn),
                        "reactant": rxn.reactant,
                        "conversion": rxn.X,}
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


def get_design_input_specs(unit): # !!! update
    param_names = ('LHK', 'Lr', 'Hr', 'x_bot', 'y_top', 'k', 
                   'T', 'P', 
                   'V', 'V_wf',
                   'tau',)
    dis = {}
    for p in param_names:
        if hasattr(unit, p):
            try:
                exec(f'dis[p] = unit.{p}')
            except:
                breakpoint()
    return dis
