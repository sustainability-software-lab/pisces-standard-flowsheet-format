# AGENTS.md

## Project Overview

This repository defines and maintains the **Standard Flowsheet Format (SFF)** — a JSON-based standard (JSON Schema Draft 7) for serializing chemical process flowsheets. It captures unit operations, material/energy streams, thermodynamic properties, utility data, and cost estimates in a simulator-agnostic format.

The repo has three components:
1. **Schema** — versioned JSON Schema definitions in `schema/`
2. **BioSTEAM export** — a Python package (`pisces_sff`) that exports BioSTEAM flowsheets to SFF JSON
3. **Documentation site** — a MkDocs site served via GitHub Pages

**Core mental model**: A chemical process flowsheet is a directed graph where **unit operations are nodes** and **material/energy streams are edges**. The SFF schema serializes this structure as JSON.

## Repository Structure

```
pisces-standard-flowsheet-format/
├── pisces_sff/                          # Python package
│   ├── __init__.py
│   └── biosteam/                        # BioSTEAM export subpackage
│       ├── __init__.py                  # exports export_biosteam_flowsheet_sff
│       └── export.py                    # BioSTEAM → SFF export function
├── examples/                            # Usage examples
│   └── biosteam_export.py
├── schema/
│   ├── schema_v_0.0.1.json              # Initial SFF schema
│   └── schema_v_0.0.2.json              # Current SFF schema
├── exported_flowsheets/
│   └── bioindustrial_park/              # 11 example SFF JSON files
├── docs/                                # MkDocs documentation source
│   ├── index.md
│   ├── biosteam_export.md
│   ├── schema_reference.md
│   └── full_schema.md
├── pyproject.toml                       # Package config
├── mkdocs.yml                           # Documentation site config
└── .github/workflows/deploy.yml         # GitHub Pages deployment
```

## SFF Schema Reference

Current version: **v0.0.2** (`schema/schema_v_0.0.2.json`), JSON Schema Draft 7.

Top-level structure:
- `metadata` — source simulator, SFF version, process context (product name, organism, feedstock)
- `chemicals` — definition of chemical components and their properties
- `units[]` — array of unit operations. Each has: `id`, `unit_type`, `design_input_specs`, `thermo_property_package`, `reactions`, `design_results`, `installed_costs`, `purchase_costs`, `utility_consumption_results`, `utility_production_results`
- `streams[]` — array of connections. Each has: `id`, `source_unit_id`, `sink_unit_id`, `price`, `stream_properties` (flows, T, P), `composition` (component names, mol fractions)
- `utilities` — container for utility definitions, e.g., `utilities.heat_utilities`, `utilities.power_utilities`, `utilities.other_utilities`

When extending the schema, copy the latest version file, increment the version, and follow JSON Schema Draft 7 conventions.

## Code Conventions

### Type hints
Strongly preferred on all new or modified function signatures:
```python
def export_biosteam_flowsheet_sff(system: System, output_path: str) -> None:
```

### Docstrings
Google-style with `Args:` and `Returns:` sections:
```python
def export_biosteam_flowsheet_sff(system: System, output_path: str) -> None:
    """
    Export a BioSTEAM system to a Standard Flowsheet Format JSON file.

    Args:
        system: A simulated BioSTEAM System object
        output_path: Destination path for the output JSON file
    """
```

### Naming
- Classes: `PascalCase`
- Functions/methods: `snake_case`
- Private methods: `_` prefix

## How to Extend the Project

### Extending the SFF schema

1. Copy `schema/schema_v_0.0.2.json` to a new version file (e.g., `schema_v_0.0.3.json`)
2. Add or modify fields following JSON Schema Draft 7 conventions
3. Update `pisces_sff/biosteam/export.py` to populate new fields from BioSTEAM
4. Update `metadata.sff_version` references
5. Update the docs in `docs/schema_reference.md` and `docs/full_schema.md`

### Adding a new export source (non-BioSTEAM)

1. Create a new subpackage under `pisces_sff/` (e.g., `pisces_sff/aspen/`) with an `__init__.py` and an `export.py`
2. The function must produce JSON conforming to `schema/schema_v_0.0.2.json`
3. Follow the same output structure as `export_biosteam_flowsheet_sff()` in `pisces_sff/biosteam/export.py`

## Validation

There is no formal test suite. Validate changes by:
- Running `examples/biosteam_export.py` as a smoke test after changes to the export function
- Validating exported SFF files against `schema/schema_v_0.0.2.json` using a JSON Schema validator
- Building the docs locally with `mkdocs serve` after changes to `docs/`

## Key Dependencies

- Python 3.9+
- [BioSTEAM](https://github.com/BioSTEAMDevelopmentGroup/biosteam) (for export)
- [mkdocs-material](https://squidfunk.github.io/mkdocs-material/) (for the documentation site)

Install:
```bash
pip install -e ".[biosteam]"
```
