# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
The model registry: ``pisces_sff/models/all_models.yaml``.

The registry is the load-bearing single source of truth for model <->
flowsheet pairing. Only items with BOTH a model recipe (a ``load.py``
directory under ``pisces_sff/models/``) and an exported flowsheet (under
``pisces_sff/exported_flowsheets/``) appear in it. It is consumed by
:func:`pisces_sff.regenerate_corpus` (which refuses to run with an
unregistered model directory on disk), by the README generator in this module
(``python -m pisces_sff._registry``), and by the Tier 1 consistency tests.

Import-light by design: no package-relative imports at module top and ``yaml``
is imported lazily inside functions, so Tier 1 can load this file by path
without pulling in biosteam.
"""

import argparse
import re
from pathlib import Path

__all__ = ('load_model_registry',)

_PKG_ROOT = Path(__file__).resolve().parent

#: The committed registry file.
REGISTRY_PATH = _PKG_ROOT / 'models' / 'all_models.yaml'

#: The two generated, committed README destinations (kept byte-identical).
README_PATHS = (
    _PKG_ROOT / 'models' / 'README.md',
    _PKG_ROOT / 'exported_flowsheets' / 'README.md',
)

# ID patterns from the 2026-08-18 naming-convention design: numbers are
# opaque, permanent IDs (zero-padded to two digits, three from 100 on).
_MODEL_ID_RE = re.compile(r'^M_[A-Z]+_\d{2,}$')
_FLOWSHEET_ID_RE = re.compile(r'^SF_[A-Z]+_\d{2,}$')

_REQUIRED_FIELDS = ('flowsheet', 'simulator', 'model_dir', 'flowsheet_file',
                    'title', 'description', 'source_corpus')


def load_model_registry(path=None, models_root=None, flowsheets_root=None):
    """
    Parse and validate the model registry.

    Parameters
    ----------
    path : str or Path, optional
        Registry file to load. Defaults to the committed
        ``pisces_sff/models/all_models.yaml``.
    models_root : str or Path, optional
        Root the entries' ``model_dir`` paths are relative to. Defaults to the
        registry file's own directory (the committed registry lives in
        ``pisces_sff/models/``).
    flowsheets_root : str or Path, optional
        Root the entries' ``flowsheet_file`` paths are relative to. Defaults
        to the ``exported_flowsheets`` directory sibling to `models_root`'s
        parent.

    Returns
    -------
    dict
        The ``models`` mapping: ``{model_id: entry_dict}``, where every entry
        carries ``flowsheet``, ``simulator``, ``model_dir``,
        ``flowsheet_file``, ``title``, ``description``, ``source_corpus``.

    Raises
    ------
    ValueError
        On a missing/unreadable file, malformed YAML, a shape without a
        top-level ``models`` mapping, a missing required field, an ID that
        does not match the naming convention, a flowsheet ID claimed by two
        models, or a referenced ``model_dir``/``flowsheet_file`` that does
        not exist on disk.
    """
    import yaml  # lazy: keep the module import-light for Tier 1

    path = Path(path) if path is not None else REGISTRY_PATH
    models_root = (Path(models_root) if models_root is not None
                   else path.parent)
    flowsheets_root = (Path(flowsheets_root) if flowsheets_root is not None
                       else path.parent.parent / 'exported_flowsheets')
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as e:
        raise ValueError(f'model registry not readable: {path}: {e}') from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f'model registry is not valid YAML: {path}: {e}') from e
    if not isinstance(data, dict) or not isinstance(data.get('models'), dict):
        raise ValueError(
            f"model registry must be a mapping with a top-level 'models' "
            f'mapping: {path}')
    models = data['models']
    claimed = {}  # flowsheet id -> model id, to reject duplicates
    for model_id, entry in models.items():
        if not _MODEL_ID_RE.match(str(model_id)):
            raise ValueError(
                f'model id {model_id!r} does not match '
                f'{_MODEL_ID_RE.pattern!r}')
        if not isinstance(entry, dict):
            raise ValueError(f'{model_id}: entry must be a mapping')
        missing = [k for k in _REQUIRED_FIELDS if k not in entry]
        if missing:
            raise ValueError(f'{model_id}: missing required field(s): {missing}')
        flowsheet = str(entry['flowsheet'])
        if not _FLOWSHEET_ID_RE.match(flowsheet):
            raise ValueError(
                f'{model_id}: flowsheet id {flowsheet!r} does not match '
                f'{_FLOWSHEET_ID_RE.pattern!r}')
        if flowsheet in claimed:
            raise ValueError(
                f'{model_id}: flowsheet id {flowsheet!r} is already claimed '
                f'by {claimed[flowsheet]}')
        claimed[flowsheet] = model_id
        model_dir = models_root / entry['model_dir']
        if not (model_dir / 'load.py').is_file():
            raise ValueError(
                f'{model_id}: model_dir {entry["model_dir"]!r} has no '
                f'load.py under {models_root}')
        flowsheet_file = flowsheets_root / entry['flowsheet_file']
        if not flowsheet_file.is_file():
            raise ValueError(
                f'{model_id}: flowsheet_file {entry["flowsheet_file"]!r} '
                f'not found under {flowsheets_root}')
    return models
