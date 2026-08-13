# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Child side of the reproducible export harness.

Runs *inside* the environment a model's recipe pins, launched by
:func:`pisces_sff.export_model`. Loads the model, simulates it, assembles the
``metadata.reproducibility`` payload, calls the versioned exporter once, and
validates the result.

Usable directly for debugging, provided the current environment can import the
model's dependencies::

    python -m pisces_sff._runner --model-dir <dir> --output <path>
"""

import argparse
import importlib.util
import platform
import sys
import warnings
from datetime import datetime
from pathlib import Path

import yaml

from ._harness import (DEFAULT_SFF_VERSION, REPO_ROOT, environment_key,
                       package_record, sha256_bytes)
from ._validate import validate_json_against_schema

__all__ = ('run_model_export', 'build_reproducibility', 'load_model_module',
           'load_extended_metadata')

#: Per-model file holding human-authored descriptive metadata (source_doi,
#: process_title, flowsheet_designers, microorganisms). Its top-level keys map
#: one-to-one onto versioned-exporter keyword arguments.
EXTENDED_METADATA_FILENAME = 'extended_metadata.yaml'

SCHEMA_PATH = Path(__file__).resolve().parent / 'schema' / 'sff_schema.json'

#: Packages whose installed versions are recorded in `resolved.package_versions`.
#: Distinguishes what actually ran from what the recipe declared.
TRACKED_PACKAGES = ('biosteam', 'biorefineries', 'thermosteam', 'chemicals',
                    'thermo', 'fluids', 'flexsolve', 'numpy', 'scipy', 'pandas',
                    'numba', 'llvmlite')

#%% Model loading


def load_model_module(model_dir):
    """
    Import a model's ``load.py`` by file path.

    Imported by path rather than as a package module so that a model directory
    needs no packaging and can be dropped in as data.

    Parameters
    ----------
    model_dir : str or Path
        Directory containing ``load.py``.

    Returns
    -------
    module
    """
    model_dir = Path(model_dir).resolve()
    path = model_dir / 'load.py'
    spec = importlib.util.spec_from_file_location(f'sff_model_{model_dir.name}', path)
    if spec is None:
        raise FileNotFoundError(f'could not load a model module from {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_extended_metadata(model_dir):
    """
    Read a model's ``extended_metadata.yaml`` into a dict of authored metadata.

    This file is the home for human-authored descriptive metadata that a
    simulated system cannot carry -- ``source_doi``, ``process_title``,
    ``flowsheet_designers``, and ``microorganisms``. Its top-level keys map
    one-to-one onto keyword arguments of the versioned exporter, so the exporter
    signature (not an allowlist here) is the authority on which keys are valid:
    an unknown key surfaces as a ``TypeError`` when the runner forwards the dict.

    Parameters
    ----------
    model_dir : str or Path
        Directory that may contain ``extended_metadata.yaml``.

    Returns
    -------
    dict
        The parsed mapping, or ``{}`` when the file is absent or empty.

    Raises
    ------
    ValueError
        If the file exists but does not parse to a mapping (malformed YAML, or a
        top-level list/scalar).
    """
    path = Path(model_dir).resolve() / EXTENDED_METADATA_FILENAME
    if not path.exists():
        # Missing file is allowed: every authored field is schema-optional. Warn
        # (rather than fail) so minimal or legacy models still export, while the
        # convention is nudged for models that should carry this metadata.
        warnings.warn(
            f'no {EXTENDED_METADATA_FILENAME} in {path.parent}; exporting '
            'without authored metadata (source_doi, process_title, '
            'flowsheet_designers, microorganisms).',
            stacklevel=2,
        )
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError as error:
        raise ValueError(f'{path} is not valid YAML: {error}') from error
    if loaded is None:  # present but empty -- a valid "nothing to declare yet"
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(
            f'{path} must contain a top-level mapping of metadata fields, got '
            f'{type(loaded).__name__}.'
        )
    return loaded

#%% Reproducibility payload


def _installed_versions():
    """Map distribution name -> installed version for TRACKED_PACKAGES."""
    import importlib
    from importlib.metadata import PackageNotFoundError, version

    versions = {}
    for name in TRACKED_PACKAGES:
        try:
            versions[name] = version(name)
            continue
        except PackageNotFoundError:
            pass
        # Fallback for a package importable from a source checkout on
        # PYTHONPATH rather than a proper install -- it then carries no
        # dist-info for importlib.metadata to find, which is exactly how this
        # repo's own dev environment resolves biosteam/biorefineries. The
        # module's own __version__ still records what actually ran; every name
        # in TRACKED_PACKAGES matches its import name.
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        module_version = getattr(module, '__version__', None)
        if module_version:
            versions[name] = module_version
    return versions


def _file_record(path, file_format, extra=None):
    """Build an embedded-file record: format, filename, path, sha256, content."""
    path = Path(path).resolve()
    data = path.read_bytes()
    record = {'format': file_format,
              'filename': path.name,
              'sha256': sha256_bytes(data),
              'content': data.decode('utf-8')}
    try:
        record['path'] = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        # Model directories outside the repository simply carry no repo-relative
        # path; the embedded content still makes the record self-sufficient.
        pass
    if extra:
        record.update(extra)
    return record


def build_reproducibility(model_dir, module, env_key=None):
    """
    Assemble the ``metadata.reproducibility`` payload for a model.

    Parameters
    ----------
    model_dir : str or Path
        Directory containing ``environment.yml`` and ``load.py``.
    module : module
        The model's imported ``load.py``, read for its declarations.
    env_key : str, optional
        Environment key supplied by the harness. Recomputed from the
        environment specification when absent.

    Returns
    -------
    dict
        Conforming to the ``metadata.reproducibility`` shape in the current SFF
        schema.
    """
    model_dir = Path(model_dir).resolve()
    env_path = model_dir / 'environment.yml'
    env_text = env_path.read_text(encoding='utf-8')
    branches = getattr(module, 'PACKAGE_BRANCHES', None) or {}
    simulator_package = module.SIMULATOR_PACKAGE
    flowsheet_model_package = module.FLOWSHEET_MODEL_PACKAGE
    reproducibility = {
        'environment': _file_record(env_path, 'conda-environment-yaml'),
        'load_script': _file_record(model_dir / 'load.py', 'python',
                                    {'entry_point': 'load'}),
        # Derived from the environment specification rather than declared
        # separately, so these pins cannot disagree with the environment used.
        'simulator_package': package_record(env_text, simulator_package,
                                            branches.get(simulator_package)),
        'flowsheet_model_package': package_record(
            env_text, flowsheet_model_package,
            branches.get(flowsheet_model_package)),
        'resolved': {
            'python_version': platform.python_version(),
            'platform': platform.platform(),
            'env_key': env_key or environment_key(env_text),
            'exported_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'package_versions': _installed_versions(),
        },
    }
    # Authored metadata now affects export output (microorganisms lives here),
    # so record the file alongside environment.yml and load.py to keep an export
    # reproducible from its recorded inputs. Present only when the file exists.
    extended_path = model_dir / EXTENDED_METADATA_FILENAME
    if extended_path.exists():
        reproducibility['extended_metadata'] = _file_record(extended_path, 'yaml')
    return reproducibility

#%% Export


def run_model_export(model_dir, output_path, sff_version=None, env_key=None):
    """
    Load, simulate, and export a model, then validate the result.

    Parameters
    ----------
    model_dir : str or Path
        Directory containing ``environment.yml`` and ``load.py``.
    output_path : str or Path
        Path to write the SFF JSON file to.
    sff_version : str, optional
        SFF schema version to export against. Defaults to ``None``, which
        resolves to :data:`pisces_sff._harness.DEFAULT_SFF_VERSION` (the schema's
        current ``"version"``).
    env_key : str, optional
        Environment key supplied by the harness.

    Returns
    -------
    Path
        `output_path`.

    Raises
    ------
    ValueError
        If the model declares a simulator with no export entry point.
    RuntimeError
        If ``load()`` raises, or if the written file fails schema validation.
        A failed validation leaves the file on disk for inspection.
    """
    from . import _export

    if sff_version is None:
        sff_version = DEFAULT_SFF_VERSION
    model_dir = Path(model_dir).resolve()
    output_path = Path(output_path)
    module = load_model_module(model_dir)
    # Human-authored descriptive metadata (source_doi, process_title,
    # flowsheet_designers, microorganisms) lives beside the recipe, not in the
    # System. Missing file -> warn + {} (see load_extended_metadata). Its keys
    # map onto exporter kwargs; a typo'd key surfaces as a TypeError below.
    authored_metadata = load_extended_metadata(model_dir)
    simulator = getattr(module, 'SIMULATOR', 'biosteam')
    # Name-based dispatch, mirroring the versioned-exporter lookup in _export:
    # adding a simulator means adding an export entry point with the matching
    # name, and nothing here changes.
    entry_point_name = f'export_{simulator}_flowsheet'
    exporter = getattr(_export, entry_point_name, None)
    if exporter is None:
        raise ValueError(
            f'model {model_dir.name!r} declares SIMULATOR={simulator!r}, but no '
            f'export entry point named {entry_point_name!r} exists in '
            'pisces_sff._export.'
        )
    # Built before simulating so a malformed recipe fails in milliseconds
    # instead of after a multi-minute simulation.
    reproducibility = build_reproducibility(model_dir, module, env_key=env_key)

    try:
        system, tea = module.load()
    except Exception as error:
        # Attach the model name: a bare traceback from deep inside a simulator
        # gives no clue which recipe was being run.
        raise RuntimeError(
            f'load() failed for model {model_dir.name!r}: {error}'
        ) from error

    exporter(system, str(output_path), sff_version=sff_version, tea=tea,
             reproducibility=reproducibility,
             **authored_metadata,
             **(getattr(module, 'EXPORT_KWARGS', None) or {}))

    is_valid, errors = validate_json_against_schema(str(output_path),
                                                    str(SCHEMA_PATH))
    if not is_valid:
        raise RuntimeError(
            f'exported flowsheet {output_path} failed validation against SFF '
            f'{sff_version}; the file was left in place for inspection:\n'
            + '\n'.join(errors[:10])
        )
    return output_path

#%% Command-line interface


def main(argv=None):
    """
    Command-line entry point invoked by :func:`pisces_sff.export_model`.

    Parameters
    ----------
    argv : list of str, optional

    Returns
    -------
    int
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        prog='python -m pisces_sff._runner',
        description='Load, simulate, and export one model to SFF.',
    )
    parser.add_argument('--model-dir', required=True,
                        help='directory containing environment.yml and load.py')
    parser.add_argument('--output', required=True,
                        help='path to write the SFF JSON file to')
    parser.add_argument('--sff-version', default=None,
                        help='SFF schema version to export against; defaults to '
                             'the schema\'s current "version"')
    parser.add_argument('--env-key', default=None,
                        help='environment key recorded in the exported file')
    args = parser.parse_args(argv)
    try:
        path = run_model_export(args.model_dir, args.output,
                                sff_version=args.sff_version,
                                env_key=args.env_key)
    except Exception as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    print(f'wrote {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
