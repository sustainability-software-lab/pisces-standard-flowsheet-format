# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Parent side of the reproducible export harness.

Reads a model's pinned environment specification, provisions the conda
environment it describes, and runs the export inside that environment via
:mod:`pisces_sff.export._runner`. Running in the provisioned environment (rather than
in whatever environment the caller happens to be in) is what makes the recorded
pins true rather than merely declared.

This module imports only the standard library and PyYAML, so it stays usable
from any environment -- including ones without a simulator installed.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import yaml

__all__ = ('export_model', 'ensure_environment', 'environment_key',
           'environment_name', 'canonical_environment_text', 'pip_requirements',
           'parse_pip_requirement', 'package_record', 'sha256_bytes',
           'find_conda_exe', 'environment_python', 'export_lock', 'LOCK_PATH')

#: Prefix for harness-created conda environments. The remainder of the name is
#: the first 12 hex characters of the environment key.
ENV_NAME_PREFIX = 'sff-'

#: Repository root; the only entry placed on the child's PYTHONPATH.
REPO_ROOT = Path(__file__).resolve().parents[2]

def _schema_version():
    """
    Read the schema's ``"version"`` without importing ``pisces_sff._version``.

    This duplicates the one-line read in
    :func:`pisces_sff._version.read_schema_version` on purpose: ``_harness`` must
    stay importable by file path -- ``tests/tier1/test_harness.py`` loads it that
    way, relying on it importing only the standard library and PyYAML, so a
    relative import of ``_version`` here is not available. The single source of
    truth is unchanged: this reads the same schema file.
    """
    schema_file = Path(__file__).resolve().parents[1] / 'schema' / 'sff_schema.json'
    with open(schema_file, 'r', encoding='utf-8') as f:
        return json.load(f)['version']


#: SFF schema version exports are written against by default. Derived from the
#: schema's own "version" field (the single source of truth) rather than pinned
#: as a literal, so a schema version bump needs no matching edit here.
DEFAULT_SFF_VERSION = _schema_version()

#%% Recipe helpers


def sha256_bytes(data):
    """
    Return the SHA-256 hex digest of `data`.

    Parameters
    ----------
    data : bytes

    Returns
    -------
    str
        64-character lowercase hex digest.
    """
    return hashlib.sha256(data).hexdigest()


def canonical_environment_text(text):
    """
    Return a canonical form of an environment specification.

    ``name`` and ``prefix`` are dropped and mappings are dumped with sorted
    keys, so that cosmetic edits -- renaming the environment, reordering keys --
    do not change the environment key and strand the environment already built
    from the same dependencies.

    Parameters
    ----------
    text : str
        Contents of an ``environment.yaml`` file.

    Returns
    -------
    str
    """
    specification = yaml.safe_load(text) or {}
    specification = {k: v for k, v in specification.items()
                     if k not in ('name', 'prefix')}
    return yaml.safe_dump(specification, sort_keys=True, default_flow_style=False)


def environment_key(text):
    """
    Return the content-derived identity of an environment specification.

    Parameters
    ----------
    text : str
        Contents of an ``environment.yaml`` file.

    Returns
    -------
    str
        SHA-256 hex digest of the canonicalized specification. Two models with
        identical dependencies therefore share one environment, and any change
        to a dependency forks a new one.
    """
    return sha256_bytes(canonical_environment_text(text).encode('utf-8'))


def environment_name(text):
    """
    Return the conda environment name for an environment specification.

    Parameters
    ----------
    text : str
        Contents of an ``environment.yaml`` file.

    Returns
    -------
    str
        ``'sff-'`` followed by the first 12 characters of the environment key.
    """
    return ENV_NAME_PREFIX + environment_key(text)[:12]


def pip_requirements(text):
    """
    Return the pip requirement entries of an environment specification.

    Parameters
    ----------
    text : str
        Contents of an ``environment.yaml`` file.

    Returns
    -------
    list of str
        Entries of every ``pip:`` mapping under ``dependencies``, in order.
    """
    specification = yaml.safe_load(text) or {}
    entries = []
    for dependency in specification.get('dependencies') or ():
        if isinstance(dependency, dict):
            entries.extend(dependency.get('pip') or ())
    return entries


def parse_pip_requirement(entry):
    """
    Parse one pip requirement entry into a package record.

    Parameters
    ----------
    entry : str
        A pip requirement, e.g. ``'numpy==1.26.4'``,
        ``'biorefineries @ git+https://host/org/repo@<sha>'``, or an editable
        VCS pin ``'-e git+https://host/org/repo@<sha>#egg=biorefineries'``.

    Returns
    -------
    dict or None
        ``{'name', 'version'}`` for a released pin, ``{'name', 'url', 'commit'}``
        for a VCS pin, or ``None`` for a blank line, an option directive, or a
        requirement this parser does not recognize.
    """
    entry = (entry or '').strip()
    # An editable VCS install ("-e git+<url>@<sha>#egg=<name>") is the pip-native
    # form of "clone the repository at this commit and put it on PYTHONPATH" --
    # the setup some models' own reproduction instructions prescribe, and the
    # only way to install a subpackage that the repository's setup.py does not
    # ship (pip clones the whole tree into <env>/src). It is as much a pin as
    # the "name @ git+..." form, so it parses to the same record.
    for flag in ('--editable', '-e'):
        if entry.startswith(flag + ' ') or entry.startswith(flag + '='):
            reference = entry[len(flag) + 1:].strip()
            if reference.startswith('git+'):
                return _vcs_record(None, reference)
            return None
    if not entry or entry.startswith('-'):
        return None
    if ' @ ' in entry:
        name, _, reference = entry.partition(' @ ')
        return _vcs_record(name.strip(), reference.strip())
    if entry.startswith('git+'):
        return _vcs_record(None, entry)
    if '==' in entry:
        name, _, version = entry.partition('==')
        return {'name': name.strip(), 'version': version.strip()}
    return None


def _vcs_record(name, reference):
    """
    Build a package record from a ``git+`` reference; None if not one.

    A ``git+`` reference with no ``@<commit>`` is rejected (returns ``None``)
    rather than returned as a partial ``{'name', 'url'}`` record. This parser
    exists to produce *pins* -- ``{'name', 'version'}`` or
    ``{'name', 'url', 'commit'}`` -- and a commit-less VCS reference is not
    one: it resolves to whatever the branch tip happens to be at install time,
    which is exactly the kind of unpinned dependency the reproducibility
    contract (and the v0.0.6 schema's package-record shape) exists to catch.
    Do not "fix" this by filling in a fake commit or dropping the requirement
    to have one -- let ``package_record`` raise instead, so an unpinned
    simulator/model dependency fails loudly before a conda environment is
    built and a simulation is run, not silently at schema validation after.
    """
    if not reference.startswith('git+'):
        return None
    url = reference[len('git+'):]
    url, _, fragment = url.partition('#')
    commit = None
    # Split on '@' only within the final path segment, so that a 'user@host'
    # style URL is not mistaken for a commit pin.
    if '@' in url.rsplit('/', 1)[-1]:
        url, _, commit = url.rpartition('@')
    if not commit:
        return None
    if name is None:
        for part in fragment.split('&'):
            if part.startswith('egg='):
                name = part[len('egg='):]
        if name is None:
            name = url.rstrip('/').rsplit('/', 1)[-1]
            if name.endswith('.git'):
                name = name[:-len('.git')]
    return {'name': name, 'url': url, 'commit': commit}


def _normalized(name):
    """Normalize a distribution name for comparison (PEP 503-ish)."""
    return name.strip().lower().replace('_', '-').replace('.', '-')


def package_record(env_text, package_name, branch=None):
    """
    Return the pinned package record for `package_name`.

    Derived from the environment specification rather than declared separately,
    so the provenance recorded in an exported flowsheet cannot disagree with the
    environment the export ran in.

    Parameters
    ----------
    env_text : str
        Contents of an ``environment.yaml`` file.
    package_name : str
        Distribution name to look up; matched ignoring case and ``-``/``_``.
    branch : str, optional
        Branch the pinned commit is reachable from, recorded when given.

    Returns
    -------
    dict
        Suitable for ``metadata.reproducibility.simulator_package`` and
        ``.flowsheet_model_package``.

    Raises
    ------
    ValueError
        If no pip requirement in the specification names `package_name`.
    """
    for entry in pip_requirements(env_text):
        record = parse_pip_requirement(entry)
        if record and _normalized(record['name']) == _normalized(package_name):
            if branch:
                record = dict(record, branch=branch)
            return record
    raise ValueError(
        f'no pip requirement for package {package_name!r} in the environment '
        'specification; every package recorded in metadata.reproducibility must '
        'be pinned there.'
    )


#%% Environment provisioning


def find_conda_exe(conda_exe=None):
    """
    Locate a usable conda executable.

    ``conda`` is frequently absent from ``PATH`` in non-interactive shells even
    where conda is installed, so common installation locations are searched
    before giving up.

    Parameters
    ----------
    conda_exe : str, optional
        Explicit path or command name. When given, only this is tried: silently
        falling back to a different conda would build the environment somewhere
        the caller did not ask for.

    Returns
    -------
    str
        Path to a conda executable.

    Raises
    ------
    FileNotFoundError
        If no candidate exists, naming what was searched.
    """
    if conda_exe:
        for candidate in (conda_exe, shutil.which(conda_exe)):
            if candidate and Path(candidate).exists():
                return str(candidate)
        raise FileNotFoundError(
            f'the conda executable {conda_exe!r} does not exist.'
        )
    home = Path.home()
    candidates = [
        os.environ.get('SFF_CONDA_EXE'),
        os.environ.get('CONDA_EXE'),
        shutil.which('conda'),
        str(home / 'anaconda3' / 'Scripts' / 'conda.exe'),
        str(home / 'miniconda3' / 'Scripts' / 'conda.exe'),
        str(home / 'anaconda3' / 'bin' / 'conda'),
        str(home / 'miniconda3' / 'bin' / 'conda'),
        '/opt/conda/bin/conda',
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise FileNotFoundError(
        'no conda executable found; environment provisioning needs one. Set the '
        'SFF_CONDA_EXE environment variable or pass conda_exe=... explicitly. '
        'Searched: ' + ', '.join(repr(c) for c in candidates if c)
    )


def _environment_prefix(conda, name, run):
    """Return the prefix of the conda environment called `name`, or None."""
    result = run([conda, 'env', 'list', '--json'],
                 capture_output=True, text=True, check=True)
    for prefix in json.loads(result.stdout).get('envs', ()):
        if Path(prefix).name == name:
            return prefix
    return None


def _editable_source_dir(conda, name, run):
    """
    Return the directory pip should check editable VCS requirements out into
    for the environment called `name`.

    pip's default ``--src`` outside a virtualenv is ``./src`` of its working
    directory, which during ``conda env create`` is the directory holding the
    environment file -- i.e. the model recipe inside this repository. The
    checkout belongs with the environment instead, so it is removed with it
    and never pollutes the repository: ``<envs dir>/<name>/src``, the prefix
    conda will create for ``-n <name>``. Falls back to a ``src`` directory
    beside the conda root when ``conda info`` reports no environment
    directories.
    """
    result = run([conda, 'info', '--json'],
                 capture_output=True, text=True, check=True)
    info = json.loads(result.stdout or '{}')
    envs_dirs = info.get('envs_dirs') or ()
    if envs_dirs:
        return str(Path(envs_dirs[0]) / name / 'src')
    root = info.get('root_prefix') or str(Path(conda).resolve().parents[1])
    return str(Path(root) / 'envs' / name / 'src')


def ensure_environment(env_yaml_path, recreate=False, conda_exe=None, run=None):
    """
    Return the prefix of the conda environment described by an environment file,
    creating it if necessary.

    Parameters
    ----------
    env_yaml_path : str or Path
        Path to an ``environment.yaml``.
    recreate : bool, optional
        Remove and rebuild the environment even if it already exists.
    conda_exe : str, optional
        Explicit conda executable; see :func:`find_conda_exe`.
    run : callable, optional
        Subprocess runner, injectable for testing. Defaults to
        :func:`subprocess.run`.

    Returns
    -------
    str
        Path to the environment prefix.
    """
    if run is None:
        run = subprocess.run
    conda = find_conda_exe(conda_exe)
    env_yaml_path = Path(env_yaml_path).resolve()
    text = env_yaml_path.read_text(encoding='utf-8')
    name = environment_name(text)
    prefix = _environment_prefix(conda, name, run)
    if prefix is not None and recreate:
        run([conda, 'env', 'remove', '-n', name, '-y'], check=True)
        prefix = None
    if prefix is None:
        # PIP_NO_DEPS disables pip's dependency resolution for the whole
        # creation. Without it, Bioindustrial-Park's declared `biosteam>=2.53.0`
        # replaces the pinned biosteam commit and every pin below it becomes
        # fiction. It cannot be expressed as `--no-deps` inside the pip: block:
        # conda writes that block verbatim into a requirements file, and pip's
        # requirements-file parser rejects --no-deps as an unknown option.
        # PIP_SRC sends editable VCS checkouts ("-e git+...") into the
        # environment prefix rather than pip's default ./src of its working
        # directory (the recipe directory) -- see _editable_source_dir.
        env = dict(os.environ, PIP_NO_DEPS='1',
                   PIP_SRC=_editable_source_dir(conda, name, run))
        try:
            run([conda, 'env', 'create', '-n', name, '-f', str(env_yaml_path)],
                check=True, env=env)
        except Exception:
            # A partially-created environment still matches the content hash, so
            # leaving it in place would make every later export reuse a broken
            # environment.
            run([conda, 'env', 'remove', '-n', name, '-y'], check=False)
            raise
        prefix = _environment_prefix(conda, name, run)
        if prefix is None:
            raise RuntimeError(
                f'conda reported success but environment {name!r} does not exist'
            )
    return prefix


def environment_python(prefix):
    """
    Return the Python interpreter inside a conda environment prefix.

    Parameters
    ----------
    prefix : str or Path

    Returns
    -------
    Path
    """
    prefix = Path(prefix)
    return prefix / 'python.exe' if os.name == 'nt' else prefix / 'bin' / 'python'


#%% Export orchestration

#: Guards against concurrent exports; see :func:`export_lock`.
LOCK_PATH = Path(tempfile.gettempdir()) / 'pisces_sff_export.lock'


@contextmanager
def export_lock():
    """
    Refuse to run two exports at once.

    Exporting simulates a system, which recompiles and writes a shared on-disk
    numba cache; two simultaneous writers corrupt it, and the resulting import
    error looks nothing like its cause. Enforced here rather than left to the
    caller.

    Raises
    ------
    RuntimeError
        If the lock is already held.
    """
    try:
        descriptor = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(
            f'another SFF export appears to be running (lock file {LOCK_PATH}). '
            'Concurrent simulations corrupt the shared numba cache. If no export '
            'is running, delete that file and retry.'
        )
    try:
        os.write(descriptor, str(os.getpid()).encode('utf-8'))
        os.close(descriptor)
        yield
    finally:
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass


def export_model(model_dir, output_path, recreate_env=False, conda_exe=None,
                 sff_version=None, run=None):
    """
    Export a model to SFF from inside the environment its recipe pins.

    Provisions the conda environment described by ``<model_dir>/environment.yaml``
    (reusing it when one already matches), then runs :mod:`pisces_sff.export._runner`
    with that environment's interpreter. The child's ``PYTHONPATH`` is set to the
    repository root alone, so source clones on a user-level ``PYTHONPATH`` cannot
    shadow the pinned installs -- which is the failure mode that made previous
    exports irreproducible.

    Parameters
    ----------
    model_dir : str or Path
        Directory containing ``environment.yaml`` and ``load.py``.
    output_path : str or Path
        Path to write the SFF JSON file to. Parent directories are created.
    recreate_env : bool, optional
        Rebuild the environment even if it already exists.
    conda_exe : str, optional
        Explicit conda executable; see :func:`find_conda_exe`.
    sff_version : str, optional
        SFF schema version to export against. Defaults to ``None``, which
        resolves to :data:`DEFAULT_SFF_VERSION` (the schema's current
        ``"version"``).
    run : callable, optional
        Subprocess runner, injectable for testing.

    Returns
    -------
    Path
        `output_path`.

    Raises
    ------
    FileNotFoundError
        If the model directory is missing a required file.
    RuntimeError
        If the child process exits non-zero.
    """
    if run is None:
        run = subprocess.run
    if sff_version is None:
        sff_version = DEFAULT_SFF_VERSION
    model_dir = Path(model_dir).resolve()
    env_yaml_path = model_dir / 'environment.yaml'
    load_script_path = model_dir / 'load.py'
    for required in (env_yaml_path, load_script_path):
        if not required.is_file():
            raise FileNotFoundError(
                f'{required} is required: a model directory must contain both '
                'environment.yaml and load.py.'
            )
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    text = env_yaml_path.read_text(encoding='utf-8')
    key = environment_key(text)
    prefix = ensure_environment(env_yaml_path, recreate=recreate_env,
                                conda_exe=conda_exe, run=run)

    # Scrub the inherited context: conda variables would point the child back at
    # the parent's environment, and user site-packages is another shadowing path.
    child_env = {k: v for k, v in os.environ.items()
                 if k not in ('CONDA_PREFIX', 'CONDA_DEFAULT_ENV', 'CONDA_SHLVL',
                              'CONDA_PYTHON_EXE', 'PYTHONHOME')}
    child_env['PYTHONPATH'] = str(REPO_ROOT)
    child_env['PYTHONNOUSERSITE'] = '1'
    # The exporter no longer contains bare breakpoint() calls (its error
    # branches now raise from pisces_sff.exceptions). This remains as a
    # defensive guard: any stray breakpoint() reached in this TTY-less child --
    # from our code or a dependency -- would hang forever, so neutralize it.
    child_env['PYTHONBREAKPOINT'] = '0'

    command = [str(environment_python(prefix)), '-m', 'pisces_sff.export._runner',
               '--model-dir', str(model_dir),
               '--output', str(output_path),
               '--env-key', key,
               '--sff-version', str(sff_version)]
    with export_lock():
        result = run(command, env=child_env)
    if result.returncode != 0:
        raise RuntimeError(
            f'export failed for model {model_dir.name!r} '
            f'(child process exited with code {result.returncode}); '
            'see the output above.'
        )
    return output_path
