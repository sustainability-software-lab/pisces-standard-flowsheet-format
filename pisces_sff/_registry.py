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

__all__ = ('load_model_registry', 'render_registry_readme',
           'write_registry_readmes')

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


_README_TEMPLATE = """\
<!-- AUTO-GENERATED FILE -- DO NOT EDIT BY HAND.
     Generated from pisces_sff/models/all_models.yaml by pisces_sff/_registry.py.
     Regenerate with: python -m pisces_sff._registry
     (or activate the committed pre-commit hook -- see below). -->

# SFF model recipes and exported flowsheets

This file indexes the model recipes under `pisces_sff/models/` and the
reference SFF exports under `pisces_sff/exported_flowsheets/`. It is generated
from `pisces_sff/models/all_models.yaml` -- the single source of truth for
model <-> flowsheet pairing. Edit that file, not this one. Only items with
both a model recipe and an exported flowsheet are registered.

## Naming convention

- Exported flowsheets are named `SF_<SIMULATOR>_<NN>` (`SF` = standard
  flowsheet); model recipes are named `M_<SIMULATOR>_<NN>` (`M` = model).
  `BST` = BioSTEAM; future simulators get their own uppercase code.
- Numbers are opaque, permanent IDs assigned in registration order: a new
  item takes the next free number when added; numbers are never reused and
  never re-sorted to restore any ordering property. They are zero-padded to
  two digits, and to three digits from 100 on (`SF_BST_100`). IDs are
  identifiers, not sort keys.
- A paired model and flowsheet usually share a number (`M_BST_01` <->
  `SF_BST_01`), but the authoritative pairing is the registry entry in
  `all_models.yaml`, not the string convention. Code must resolve pairing
  through the registry only.
- Items were renamed from earlier descriptive filenames with `git mv`; trace
  any file's history across the rename with `git log --follow <path>`.

## Keeping this file in sync

A committed pre-commit hook regenerates this README on every commit. Activate
it once per clone:

    git config core.hooksPath .githooks
    git config sff.python <path-to-a-python-with-pyyaml>   # only if python3/python don't resolve

## Registered models

| Model ID | Flowsheet ID | Title | Description | Simulator | Source corpus |
| --- | --- | --- | --- | --- | --- |
{table}
"""


def _table_cell(value):
    """
    Sanitize one value for a generated-Markdown table cell.

    Collapses all whitespace runs (including newlines from YAML block
    scalars) to single spaces and escapes literal ``|`` as ``\\|``, so no
    registry value can silently add rows or columns to the rendered table.

    Parameters
    ----------
    value : object
        The raw registry field value.

    Returns
    -------
    str
        The cell text, single-line, with pipes escaped.
    """
    return ' '.join(str(value).split()).replace('|', '\\|')


def render_registry_readme(registry):
    """
    Render the registry README content.

    Deterministic: rows are emitted in sorted-model-id order and every cell
    is sanitized by :func:`_table_cell` (whitespace collapsed to single
    spaces, pipes escaped), so rendering twice always produces identical
    text (which is what lets the pre-commit hook run unconditionally and
    the sync test compare bytes) and no registry value can corrupt the
    table shape.

    Parameters
    ----------
    registry : dict
        A mapping as returned by :func:`load_model_registry`.

    Returns
    -------
    str
        The README content, LF-separated, with a trailing newline.
    """
    rows = []
    for model_id in sorted(registry):
        entry = registry[model_id]
        rows.append(
            f"| {_table_cell(model_id)} | {_table_cell(entry['flowsheet'])} "
            f"| {_table_cell(entry['title'])} "
            f"| {_table_cell(entry['description'])} "
            f"| {_table_cell(entry['simulator'])} "
            f"| {_table_cell(entry['source_corpus'])} |")
    return _README_TEMPLATE.format(table='\n'.join(rows))


def write_registry_readmes(registry=None, paths=None):
    """
    Write the generated README to every destination and return the paths.

    Parameters
    ----------
    registry : dict, optional
        Defaults to :func:`load_model_registry` on the committed file.
    paths : iterable of str or Path, optional
        Defaults to the two committed destinations (``pisces_sff/models/`` and
        ``pisces_sff/exported_flowsheets/``). Tests pass temp paths.

    Returns
    -------
    list of Path
        The written files, byte-identical, LF-terminated (``newline='\\n'``,
        consistent with the repo's ``.gitattributes`` LF pin).
    """
    if registry is None:
        registry = load_model_registry()
    if paths is None:
        paths = README_PATHS
    text = render_registry_readme(registry)
    written = []
    for path in paths:
        path = Path(path)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        written.append(path)
    return written


def main(argv=None):
    """
    Regenerate the two committed registry READMEs in place.

    Parameters
    ----------
    argv : list of str, optional

    Returns
    -------
    int
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        prog='python -m pisces_sff._registry',
        description='Regenerate pisces_sff/models/README.md and '
                    'pisces_sff/exported_flowsheets/README.md from '
                    'all_models.yaml.',
    )
    parser.parse_args(argv)
    for path in write_registry_readmes():
        print(f'wrote {path}')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
