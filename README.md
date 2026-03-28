# Standard Flowsheet Format (SFF)

A JSON-based standard to represent chemical process flowsheets for interoperability across process simulators. Currently supports export from [BioSTEAM](https://github.com/BioSTEAMDevelopmentGroup/biosteam), with the schema designed to be simulator-agnostic.

This format captures:
- **Unit operations** — including design and cost results, utility demands and production, reactions, and design input specifications
- **Streams** — material flows, phases, temperature, pressure, and source/sink unit operation ports
- **Utilities** — heating, cooling, power, combustion, and others
- **Chemicals** — registry IDs and user-defined properties
- **Metadata** — source publication, flowsheet designers, TEA parameters, and process description

![Simplified visual representation of the SFF schema](images/SFF_visual_representation.png)

## Features

- **Simulator-agnostic JSON schema** — structured, versioned, and validated with JSON Schema Draft 7
- **Installable Python package** — `pip install pisces-sff` for schema validation and I/O; `pip install pisces-sff[biosteam]` for the BioSTEAM export adapter
- **Extensible adapter architecture** — adding a new simulator adapter is a drop-in subpackage; no changes to core required
- **Example data** — 11 bioindustrial process flowsheets exported from BioSTEAM

## Project Structure

```
pisces-standard-flowsheet-format-new/
├── pisces_sff/                          # Python package
│   ├── __init__.py                      # public API
│   ├── base.py                          # SFFExporter and SFFImporter Protocols
│   ├── core/                            # shared utilities (all adapters use these)
│   │   ├── validate.py                  # validate_sff(data, schema_version)
│   │   └── io.py                        # write_sff(...), read_sff(...)
│   └── biosteam/                        # BioSTEAM export adapter
│       ├── __init__.py
│       └── export.py
├── examples/                            # Usage examples
│   └── biosteam_export.py
├── export.py                            # Legacy export script (kept for compatibility)
├── examples_for_export.py              # Legacy example script (kept for compatibility)
├── schema/                              # JSON schema definitions
│   ├── schema_v_0.0.1.json
│   └── schema_v_0.0.2.json
├── exported_flowsheets/                 # Sample SFF data (11 flowsheets)
│   └── bioindustrial_park/
└── pyproject.toml                       # Package config
```

## Getting Started

### Install

```bash
# Core package only (schema validation + I/O, no simulator dependency)
pip install pisces-sff

# With BioSTEAM export adapter
pip install "pisces-sff[biosteam]"
```

Or install from source:

```bash
git clone https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format-new.git
cd pisces-standard-flowsheet-format-new
pip install -e ".[biosteam]"
```

### Export a BioSTEAM Flowsheet

```python
from pisces_sff.biosteam import export_biosteam_flowsheet_sff
from biorefineries import sugarcane as sc

sc.load()
sys = sc.create_sugarcane_to_ethanol_system()
sys.simulate()

export_biosteam_flowsheet_sff(sys, "my_flowsheet.json")
```

### Validate an SFF File

```python
from pisces_sff import validate_sff, read_sff

data = read_sff("my_flowsheet.json")
validate_sff(data)   # raises jsonschema.ValidationError if invalid
```

### Explore Example Flowsheets

Pre-exported flowsheets are in `exported_flowsheets/bioindustrial_park/`:

| Feedstock | Product |
|-----------|---------|
| Corn | 3-HP → Acrylic Acid |
| Corn | Succinic Acid |
| Dextrose | 3-HP → Acrylic Acid |
| Dextrose | Succinic Acid |
| Dextrose | TAL |
| Dextrose | TAL → Ketoacids |
| Sugarcane | Ethanol |
| Sugarcane | 3-HP → Acrylic Acid |
| Sugarcane | Succinic Acid |
| Sugarcane | TAL |
| Sugarcane | TAL → Ketoacids |

## Schema Versions

| Version | File | Status |
|---------|------|--------|
| 0.0.2 | [schema_v_0.0.2.json](schema/schema_v_0.0.2.json) | Latest |
| 0.0.1 | [schema_v_0.0.1.json](schema/schema_v_0.0.1.json) | Initial release |

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b my-feature`
3. Make your changes and submit a pull request

## License

MIT License. See [LICENSE](LICENSE) for details.
