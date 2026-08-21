# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Corn-fed 3-hydroxypropionic acid (3-HP) to acrylic acid biorefinery, from the
Bioindustrial-Park ``biorefineries.HP`` module (``HP_2025_no_FGI`` branch),
in the configuration ``feedstock='corn'``, ``product='acrylic acid'``,
``fermentation_performance='DASbox'`` -- the corn scenario of
Tan et al., Nature Communications 2026 (https://doi.org/10.1038/s41467-025-67621-8).

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
PACKAGE_BRANCHES = {'biorefineries': 'HP_2025_no_FGI'}

MODEL_NAME = 'M_BST_02'

# Export-behavior flags forwarded to the exporter. Authored descriptive
# metadata (source_doi, process_title, flowsheet_designers, microorganisms)
# lives in extended_metadata.yaml, not here -- this dict is for how the export
# is produced, not what a human knows about the flowsheet. `stoichiometry`
# controls how reactions serialize (dict vs vector).
EXPORT_KWARGS = {
    'stoichiometry': 'dict',
}

# The configuration loaded, in the vocabulary of biorefineries.HP.load_model.
FEEDSTOCK = 'corn'
PRODUCT = 'acrylic acid'
FERMENTATION_PERFORMANCE = 'DASbox'

#%% Loader


def load():
    """
    Load and simulate the corn-fed 3-HP to acrylic acid biorefinery at baseline.

    Returns
    -------
    (biosteam.System, biosteam.TEA)
        The simulated system and its TEA object, ready to export.

    Notes
    -----
    Mirrors ``biorefineries.HP.models.load.load_HP_model(FEEDSTOCK, PRODUCT,
    FERMENTATION_PERFORMANCE)`` step for step -- same model module, same
    parameter-distribution workbook, same baseline evaluation sequence -- but
    locates the workbook with ``os.path`` instead of that function's
    hard-coded Windows path separators, so the model also loads on POSIX.
    """
    # Imported inside the function so that reading this module's declarations
    # (as the test suite does) does not pull in the biosteam stack.
    import os
    from biorefineries import HP
    from biorefineries.HP.models.corn import models_corn_improved_separations as models

    model = models.HP_model
    system = models.HP_sys
    spec = models.spec
    tea = models.HP_tea

    workbook = os.path.join(
        os.path.dirname(HP.__file__), 'analyses', 'full',
        'parameter_distributions', 'acrylic_acid_product',
        f'parameter-distributions_corn_Acrylic_{FERMENTATION_PERFORMANCE}.xlsx',
    )
    model.parameters = ()
    model.load_parameter_distributions(workbook, models.namespace_dict)
    model.get_parameters()
    model.load_samples(model.sample(N=2000, rule='L'))
    model.exception_hook = 'warn'

    # Baseline evaluation, as load_HP_model does it: evaluate at baseline,
    # size the plant to the desired production capacity, re-evaluate.
    model.metrics_at_baseline()
    spec.set_production_capacity()
    model.metrics_at_baseline()
    return system, tea


if __name__ == '__main__':
    system, tea = load()
    print(system)
    print(f'IRR: {tea.IRR}')
