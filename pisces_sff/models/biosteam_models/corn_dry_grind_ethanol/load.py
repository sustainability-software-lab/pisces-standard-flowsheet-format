# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Conventional corn dry-grind ethanol biorefinery, from the Bioindustrial-Park
``biorefineries.corn`` module.

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

MODEL_NAME = 'corn_dry_grind_ethanol'

# Export-behavior flags forwarded to the exporter. Authored descriptive
# metadata (source_doi, process_title, flowsheet_designers, microorganisms)
# lives in extended_metadata.yaml, not here -- this dict is for how the export
# is produced, not what a human knows about the flowsheet. `stoichiometry`
# controls how reactions serialize (dict vs vector).
EXPORT_KWARGS = {
    'stoichiometry': 'dict',
}

#%% Loader


def load():
    """
    Load and simulate the corn dry-grind ethanol biorefinery.

    Returns
    -------
    (biosteam.System, biosteam.TEA)
        The simulated system and its TEA object. ``Biorefinery.__new__``
        simulates the system and solves for IRR, so the returned objects are
        ready to export with no further calls.
    """
    # Imported inside the function so that reading this module's declarations
    # (as the test suite does) does not pull in the biosteam stack.
    from biorefineries import corn
    biorefinery = corn.Biorefinery()
    return biorefinery.corn_sys, biorefinery.corn_tea


if __name__ == '__main__':
    system, tea = load()
    print(system)
    print(f'IRR: {tea.IRR}')
