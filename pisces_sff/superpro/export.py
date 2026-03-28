"""
export.py
─────────
Main entry point for exporting a SuperPro Designer .spf file to SFF JSON.

Requires:
  - Windows OS
  - SuperPro Designer installed and licensed
  - pywin32 (pip install pywin32)

Usage::

    from pisces_sff.superpro import export_superpro_flowsheet_sff

    sff = export_superpro_flowsheet_sff(
        spf_path="my_process.spf",
        output_path="my_process.sff.json",
    )
    print(f"Exported {len(sff['units'])} units and {len(sff['streams'])} streams.")
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Union

log = logging.getLogger(__name__)


def export_superpro_flowsheet_sff(
    spf_path: Union[str, Path],
    output_path: Union[str, Path],
    *,
    metadata: dict | None = None,
    run_balances: bool = True,
    enrich_molar_flows: bool = True,
    validate: bool = True,
    verbose: bool = True,
) -> dict:
    """
    Export a SuperPro Designer .spf file to an SFF JSON file.

    Args:
        spf_path:
            Path to the SuperPro Designer .spf file to export.
        output_path:
            Destination path for the output SFF JSON file.
        metadata:
            Optional dict of metadata fields to set or override in the SFF
            document. Common keys: ``process_title``, ``product_name``,
            ``organism``, ``flowsheet_designers``, ``source_doi``,
            ``TEA_year``, ``TEA_currency``.  Any field provided here takes
            precedence over values extracted from the model via COM.
        run_balances:
            If True (default), call SuperPro's DoMEBalances and
            DoEconomicCalculations before reading data. Disable only if the
            model has already been solved and you want a faster export.
        enrich_molar_flows:
            If True (default), compute total molar flow for each stream from
            the composition and a built-in molecular weight database (local
            dict + optional PubChem fallback for unknowns). This adds
            ``total_molar_flow`` to each stream's ``stream_properties``.
        validate:
            If True (default), validate the generated SFF document against
            the JSON schema before writing. Set to False only if you are
            intentionally producing a non-conformant file (e.g. for debugging
            incomplete models).
        verbose:
            If True (default), print progress messages and any conversion
            warnings to stdout.

    Returns:
        The SFF document as a Python dict.

    Raises:
        RuntimeError:
            If not running on Windows or if pywin32 is not installed.
        jsonschema.ValidationError:
            If ``validate=True`` and the generated SFF document does not
            conform to the target schema.
        _com_interface.SuperProCOMError:
            If SuperPro Designer cannot open the .spf file.
    """
    if sys.platform != "win32":
        raise RuntimeError(
            "export_superpro_flowsheet_sff() requires Windows with SuperPro Designer "
            "installed. This function cannot run on macOS or Linux."
        )

    try:
        import win32com.client  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "pywin32 is not installed. Install it with:\n"
            "  pip install pywin32\n"
            "Then run 'python -m pywin32_postinstall -install' as administrator."
        )

    from pisces_sff.core.io import write_sff

    from ._com_interface import LocalSuperProInstance
    from ._data_extraction import extract_all
    from ._sff_translator import translate

    spf_path    = Path(spf_path)
    output_path = Path(output_path)

    if verbose:
        print(f"Opening {spf_path.name} ...")

    inst = LocalSuperProInstance()
    try:
        inst.open_spf(spf_path)

        if run_balances:
            if verbose:
                print("Running mass/energy balances and economic calculations ...")
            inst.run_balances()

        if verbose:
            print("Extracting data from SuperPro ...")
        raw = extract_all(inst.document)

    finally:
        inst.close()

    # Optionally enrich molar flows from composition + MW database
    if enrich_molar_flows:
        if verbose:
            print("Computing molar flows from composition ...")
        from ._chemical_properties import enrich_molar_flows as _enrich
        raw = _enrich(raw)

    # Apply caller-supplied metadata overrides
    if metadata:
        raw.setdefault("metadata", {}).update(metadata)

    if verbose:
        print("Translating to SFF format ...")
    sff_doc, warnings = translate(raw)

    if warnings and verbose:
        print(f"\n{len(warnings)} conversion warning(s):")
        for w in warnings:
            print(f"  [{w['field']}] {w['message']}")
        print()

    if verbose:
        meta = sff_doc.get("metadata", {})
        print(
            f"Writing SFF to {output_path} ...\n"
            f"  Units:   {len(sff_doc.get('units', []))}\n"
            f"  Streams: {len(sff_doc.get('streams', []))}\n"
            f"  Process: {meta.get('process_title', '(not set)')}\n"
            f"  Product: {meta.get('product_name', '(not set)')}"
        )

    write_sff(sff_doc, output_path, validate=validate)

    if verbose:
        print(f"\nDone. SFF written to: {output_path.resolve()}")

    return sff_doc
