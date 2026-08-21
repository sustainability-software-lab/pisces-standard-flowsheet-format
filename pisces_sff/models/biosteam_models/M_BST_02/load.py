# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Corn -> 3-hydroxypropionic acid (3-HP) -> acrylic acid biorefinery, from the
Bioindustrial-Park ``biorefineries.HP`` module: corn feedstock, low-pH 3-HP
fermentation (no in-situ neutralization; the module default), separation
process A (``systems.corn.system_corn_improved_separations``), and catalytic
dehydration of 3-HP to acrylic acid. Baseline parameters are the ``300L_FGI``
distributions used by the source publication
(https://doi.org/10.1038/s41467-026-75285-1).

Run this file directly to load and simulate the model without the harness:
``python load.py``.
"""

#%% Model declarations

# Selects the export entry point: the runner resolves `export_<SIMULATOR>_flowsheet`
# in pisces_sff._export. Dispatch is by this value rather than by directory
# name, so a model from another simulator only changes this line.
SIMULATOR = 'biosteam'

# Distribution names, resolved against environment.yaml's pip requirements to
# fill metadata.reproducibility.simulator_package / .flowsheet_model_package.
# Deriving the pins from the environment specification (instead of restating
# them here) is what keeps the two representations from disagreeing.
SIMULATOR_PACKAGE = 'biosteam'
FLOWSHEET_MODEL_PACKAGE = 'biorefineries'

# Branches the pinned commits are reachable from, where known. Advisory only --
# a branch is not a pin, and is recorded so a reader can locate the commit.
PACKAGE_BRANCHES = {'biorefineries': 'master'}

MODEL_NAME = 'M_BST_02'

# Export-behavior flags forwarded to the exporter. Authored descriptive
# metadata (source_doi, process_title, flowsheet_designers, microorganisms)
# lives in extended_metadata.yaml, not here -- this dict is for how the export
# is produced, not what a human knows about the flowsheet. `stoichiometry`
# controls how reactions serialize (dict vs vector).
EXPORT_KWARGS = {
    'stoichiometry': 'dict',
}

#%% Loader

# Configuration passed to `biorefineries.HP.load_model`. The third argument is
# the label of the parameter-distribution workbook
# (analyses/full/parameter_distributions/acrylic_acid_product/
# parameter-distributions_corn_Acrylic_300L_FGI.xlsx), i.e. the baseline the
# source publication's scripts (HP/analyses/fermentation/TRY_analysis_FGI.py)
# load before sweeping titer, rate, and yield.
FEEDSTOCK = 'corn'
PRODUCT = 'acrylic acid'
FERMENTATION_PERFORMANCE = '300L_FGI'


def load():
    """
    Load and simulate the corn -> 3-HP -> acrylic acid biorefinery at baseline.

    Returns
    -------
    (biosteam.System, biosteam.TEA)
        The simulated system and its TEA object. ``HP.load_model`` loads the
        baseline parameter distributions, sets the production capacity, and
        evaluates the model at baseline (which simulates the system and
        solves the TEA), so the returned objects are ready to export with no
        further calls.
    """
    # Imported inside the function so that reading this module's declarations
    # (as the test suite does) does not pull in the biosteam stack.
    from biorefineries import HP
    HP.load_model(FEEDSTOCK, PRODUCT, FERMENTATION_PERFORMANCE)
    return HP.system, HP.tea


if __name__ == '__main__':
    system, tea = load()
    print(system)
    from biorefineries import HP
    print(f'MPSP: {HP.get_adjusted_MSP():.4f} $/kg acrylic acid')
