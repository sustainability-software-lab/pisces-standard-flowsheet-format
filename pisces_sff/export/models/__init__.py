# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Per-model export recipes.

Each model lives in its own directory holding an ``environment.yaml`` (a pinned
environment specification) and a ``load.py`` (which loads and simulates the
model). :func:`pisces_sff.export_model` builds the environment from the former
and runs the latter inside it, so a recipe cannot claim pins it did not use.

Directories are grouped per source simulator (``biosteam_models/``, and others
as they are added), but that grouping is organizational only: the runner
dispatches on the ``SIMULATOR`` declaration inside each ``load.py``, so a new
simulator needs no change here.
"""

__all__ = ()
