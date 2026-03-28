"""
pisces_sff.superpro
───────────────────
SuperPro Designer → SFF export adapter.

Exports a .spf file to an SFF JSON file using SuperPro's COM interface.

Requirements
------------
- Windows OS
- SuperPro Designer v14+ installed and licensed
- pywin32 (pip install "pisces-sff[superpro]")

Quick start
-----------
::

    from pisces_sff.superpro import export_superpro_flowsheet_sff

    export_superpro_flowsheet_sff(
        spf_path="my_process.spf",
        output_path="my_process.sff.json",
        metadata={
            "process_title":     "My Bioprocess",
            "product_name":      "Ethanol",
            "organism":          "Saccharomyces cerevisiae",
            "flowsheet_designers": "Your Name",
        },
    )

CLI
---
::

    python -m pisces_sff.superpro my_process.spf my_process.sff.json
"""

from .export import export_superpro_flowsheet_sff

__all__ = ["export_superpro_flowsheet_sff"]
