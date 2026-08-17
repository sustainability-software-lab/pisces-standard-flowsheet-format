# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
#
# This module is under the MIT open-source license. See
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

"""
Regenerate the committed reference corpus by exporting every model that has a
recipe under ``pisces_sff/models/``.

Two entry points share one loop:

* :func:`regenerate_corpus` -- exports each discovered model into a
  caller-chosen directory. The Tier 3 test calls it with a temporary directory,
  so running the test never touches the committed corpus.
* ``python -m pisces_sff._regenerate_corpus`` -- the one deliberate command that
  writes the committed corpus files in
  ``pisces_sff/exported_flowsheets/bioindustrial_park/``. Regenerating the
  committed corpus is an announced act, never a side effect of a test run.

Imports nothing heavy at module top: :func:`pisces_sff.export_model` is imported
lazily inside :func:`regenerate_corpus`, so this module stays loadable by a
Tier-1 test (which injects a fake exporter) without pulling in biosteam.
"""

import argparse
from pathlib import Path

__all__ = ('regenerate_corpus', 'iter_model_dirs', 'MODELS_ROOT', 'CORPUS_DIR')

#: Root of the per-model recipes; every directory holding a load.py is a model.
MODELS_ROOT = Path(__file__).resolve().parent / 'models'

#: Destination for the committed reference corpus.
CORPUS_DIR = (Path(__file__).resolve().parent
              / 'exported_flowsheets' / 'bioindustrial_park')


def iter_model_dirs(models_root=MODELS_ROOT):
    """
    Return every directory holding a ``load.py``, at any depth under
    `models_root`, sorted for a stable order.

    Parameters
    ----------
    models_root : str or Path, optional

    Returns
    -------
    list of Path
    """
    return sorted(p.parent for p in Path(models_root).rglob('load.py'))


def regenerate_corpus(output_dir, models_root=MODELS_ROOT, export=None,
                      sff_version=None, stamp_reproducible=False,
                      comparison_rtol=1e-4, verify=None):
    """
    Export every discovered model into `output_dir` and return the written paths.

    Parameters
    ----------
    output_dir : str or Path
        Directory to write ``<model_name>.json`` files into; created if absent.
    models_root : str or Path, optional
        Root to discover model recipes under.
    export : callable, optional
        ``export(model_dir, output_path, sff_version=...)`` used to export one
        model. Defaults to :func:`pisces_sff.export_model` (the full harness),
        imported lazily so this module stays import-light for Tier 1. Tests
        inject a fake here.
    sff_version : str, optional
        SFF schema version to export against, threaded to `export`. Left as
        ``None`` by default so the export callable's own default applies -- for
        the harness that default is the schema's current ``"version"``
        (:func:`pisces_sff._version.read_schema_version`), so the committed
        corpus auto-syncs to the current schema without a manual pin.
    stamp_reproducible : bool, optional
        When True, after exporting each model run verify_reproducible on the
        written file at `comparison_rtol`; on success, record
        metadata.reproducibility.comparison_rtol and append "reproducible" to
        metadata.tags, then rewrite the file. Off by default so ordinary regen
        stays single-simulation -- this pass costs a SECOND full simulation per
        model and is run deliberately for a tag rollout.
    comparison_rtol : float, optional
        Tolerance recorded and used by the stamping pass. Default 1e-4.
    verify : callable, optional
        ``verify(path, rtol=...) -> (matches, diffs)`` used by the stamping pass.
        Defaults to :func:`pisces_sff.verify_reproducible`, imported lazily. Tests
        inject a fake.

    Returns
    -------
    list of Path
        The written output files, one per discovered model, in discovery order.
    """
    if export is None:
        from ._harness import export_model
        export = export_model
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for model_dir in iter_model_dirs(models_root):
        output_path = output_dir / f'{model_dir.name}.json'
        export(model_dir, output_path, sff_version=sff_version)
        if stamp_reproducible:
            _stamp_reproducible(output_path, comparison_rtol, verify)
        written.append(output_path)
    return written


def _stamp_reproducible(output_path, comparison_rtol, verify):
    """Verify a just-exported file reproduces, and on success stamp it as
    reproducible: record comparison_rtol and append the tag. Rewrites the file
    in place. A verification failure raises -- a corpus file must not carry a
    false reproducible claim."""
    import json
    if verify is None:
        from . import verify_reproducible as verify
    matches, diffs = verify(str(output_path), rtol=comparison_rtol)
    if not matches:
        raise RuntimeError(
            f'{output_path} did not reproduce within rtol={comparison_rtol}; '
            f'refusing to stamp reproducible. First diffs: {diffs[:5]}')
    with open(output_path, 'r', encoding='utf-8') as f:
        doc = json.load(f)
    repro = doc['metadata'].setdefault('reproducibility', {})
    repro['comparison_rtol'] = comparison_rtol
    tags = doc['metadata'].setdefault('tags', [])
    if 'reproducible' not in tags:
        tags.append('reproducible')
    _write_json(output_path, doc)


def _write_json(output_path, doc):
    """Write `doc` as JSON matching _export.py's _write_sff_json BYTE FOR BYTE
    (same open()/json.dump() call shape: default text-mode encoding, indent=4,
    default ensure_ascii=True, no trailing newline) so a stamped corpus file
    stays diff-clean against a freshly harness-exported one -- only the two
    stamped keys (metadata.tags, metadata.reproducibility.comparison_rtol)
    should differ. Deliberately does NOT add encoding=, newline=, or a trailing
    '\\n' -- _write_sff_json has none of those, confirmed against the committed
    corn file's raw bytes (no non-ASCII bytes, no trailing newline)."""
    import json
    with open(output_path, 'w') as f:
        json.dump(doc, f, indent=4)


def main(argv=None):
    """
    Regenerate the committed corpus in-place. See the module docstring.

    Parameters
    ----------
    argv : list of str, optional

    Returns
    -------
    int
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        prog='python -m pisces_sff._regenerate_corpus',
        description='Regenerate the committed reference corpus in-place.',
    )
    parser.add_argument(
        '--sff-version', default=None,
        help='SFF schema version to export against; defaults to the schema\'s '
             'current "version", so the corpus tracks the schema automatically.')
    args = parser.parse_args(argv)
    written = regenerate_corpus(CORPUS_DIR, sff_version=args.sff_version)
    for path in written:
        print(f'wrote {path}')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
