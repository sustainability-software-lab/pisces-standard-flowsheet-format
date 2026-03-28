"""
CLI entry point: python -m pisces_sff.superpro <model.spf> <output.sff.json>

Options
-------
  --no-balances       Skip running mass/energy balances (use cached results)
  --no-molar-flows    Skip molar flow computation from composition
  --quiet             Suppress progress output

Metadata overrides (optional)
-------------------------------
  --title TITLE
  --product PRODUCT
  --organism ORGANISM
  --designers DESIGNERS
  --doi DOI
  --year YEAR          TEA base year (integer)
  --currency CURRENCY  ISO 4217 currency code (e.g. USD, EUR)
"""

from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pisces_sff.superpro",
        description="Export a SuperPro Designer .spf file to SFF JSON.",
    )
    p.add_argument("spf_path", help="Path to the SuperPro .spf file")
    p.add_argument("output_path", help="Destination path for the SFF JSON file")

    p.add_argument("--no-balances",    action="store_true",
                   help="Skip DoMEBalances / DoEconomicCalculations (use cached results)")
    p.add_argument("--no-molar-flows", action="store_true",
                   help="Skip automatic molar flow computation from composition")
    p.add_argument("--quiet", "-q",    action="store_true",
                   help="Suppress progress output (warnings still shown)")

    # Metadata overrides
    meta = p.add_argument_group("metadata overrides")
    meta.add_argument("--title",     dest="process_title")
    meta.add_argument("--product",   dest="product_name")
    meta.add_argument("--organism",  dest="organism")
    meta.add_argument("--designers", dest="flowsheet_designers")
    meta.add_argument("--doi",       dest="source_doi")
    meta.add_argument("--year",      dest="TEA_year",    type=int)
    meta.add_argument("--currency",  dest="TEA_currency")

    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Collect any non-None metadata overrides supplied on the command line
    meta_keys = ("process_title", "product_name", "organism",
                 "flowsheet_designers", "source_doi", "TEA_year", "TEA_currency")
    metadata = {k: getattr(args, k) for k in meta_keys if getattr(args, k) is not None}

    from .export import export_superpro_flowsheet_sff

    try:
        export_superpro_flowsheet_sff(
            spf_path=args.spf_path,
            output_path=args.output_path,
            metadata=metadata or None,
            run_balances=not args.no_balances,
            enrich_molar_flows=not args.no_molar_flows,
            verbose=not args.quiet,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
