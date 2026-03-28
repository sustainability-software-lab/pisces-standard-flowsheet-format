# -*- coding: utf-8 -*-
# Example exports of biosteam flowsheets into a standard JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
# 
# This module is under the MIT open-source license. See 
# https://github.com/sarangbhagwat/sff/blob/main/LICENSE
# for license details.

from pisces_sff.biosteam import export_biosteam_flowsheet_sff

#%% Example 1: sugarcane-to-ethanol

from biorefineries import sugarcane as sc
sc.load()
sys = sc.create_sugarcane_to_ethanol_system()
sys.simulate()
sys.diagram('cluster')

export_biosteam_flowsheet_sff(sys, "sugarcane_to_ethanol.json")

