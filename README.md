# Standard Flowsheet Format (SFF)

A JSON-based standard for serializing chemical process flowsheets — including unit operations, streams, thermodynamic properties, utility data, and cost estimates — for interoperability across different process simulators and analysis tools. Currently supports export from [BioSTEAM](https://github.com/BioSTEAMDevelopmentGroup/biosteam), with the schema designed to be simulator-agnostic.

![Simplified visual representation of the SFF schema](images/SFF_visual_representation.png)

## Features

- **Simulator-agnostic JSON schema** — structured, versioned, and validated with JSON Schema Draft 7
- **Complete process representation** — units, streams, chemicals, utilities, TEA data, and LCA boundaries
- **Machine-readable and human-readable** — easy to parse in Python, JavaScript, or any language with JSON support
- **Documentation site** — schema reference hosted via GitHub Pages
- **Example data** — 11 bioindustrial process flowsheets exported from BioSTEAM
- **Graph learning subpackage** — GNN-based experimentation for analyzing flowsheet structure and predicting process properties

## Project Structure

```
.
├── export.py                        # BioSTEAM → SFF export function
├── examples_for_export.py           # Usage examples for export
├── schema/                          # JSON schema definitions
│   ├── schema_v_0.0.1.json
│   └── schema_v_0.0.2.json         # Latest version
├── exported_flowsheets/             # Sample SFF data (11 flowsheets)
│   └── bioindustrial_park/
├── docs/                            # MkDocs documentation source
│   ├── index.md
│   ├── schema_reference.md
│   └── full_schema.md
├── mkdocs.yml                       # Documentation site config
├── graph_learning/                  # GNN experimentation subpackage
│   ├── src/                         # Models, training, evaluation, inference
│   ├── quick_demo.py                # Minimal end-to-end demo
│   ├── main_pipeline.py             # Full training pipeline
│   ├── config.yaml                  # Model & training configuration
│   └── requirements.txt             # Python dependencies
└── .github/workflows/deploy.yml     # GitHub Pages deployment
```

## Getting Started

### Prerequisites

- Python 3.9+
- [BioSTEAM](https://github.com/BioSTEAMDevelopmentGroup/biosteam) (for exporting flowsheets)

### Clone the Repository

```bash
git clone https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format.git
cd pisces-standard-flowsheet-format
```

### Export a BioSTEAM Flowsheet

```python
from export import export_biosteam_flowsheet_sff
from biorefineries import sugarcane as sc

sc.load()
sys = sc.create_sugarcane_to_ethanol_system()
sys.simulate()

export_biosteam_flowsheet_sff(sys, "my_flowsheet.json")
```

This produces a JSON file conforming to the SFF schema with all unit operations, streams, thermodynamic data, and cost estimates.

### Explore Example Flowsheets

Pre-exported flowsheets are available in `exported_flowsheets/bioindustrial_park/`:

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

### View the Schema

- **Online**: [Schema Reference](https://sustainability-software-lab.github.io/pisces-standard-flowsheet-format/schema_reference/)
- **Download**: [schema/schema_v_0.0.2.json](schema/schema_v_0.0.2.json)
- **Programmatic access**: `https://sustainability-software-lab.github.io/pisces-standard-flowsheet-format/schema.json`

## Graph Learning Subpackage

The `graph_learning/` directory contains a GNN-based framework for analyzing flowsheet graphs using Graph Attention Networks (GATs). See [graph_learning/README.md](graph_learning/README.md) for full details.

```bash
cd graph_learning
pip install -r requirements.txt
python quick_demo.py
```

## Documentation Site

The full schema documentation is hosted at:
**https://sustainability-software-lab.github.io/pisces-standard-flowsheet-format/**

To build and serve the docs locally:

```bash
pip install mkdocs-material
mkdocs serve
```

## Schema Versions

| Version | File | Status |
|---------|------|--------|
| 0.0.2 | [schema_v_0.0.2.json](schema/schema_v_0.0.2.json) | Latest |
| 0.0.1 | [schema_v_0.0.1.json](schema/schema_v_0.0.1.json) | Initial release |

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b my-feature`
3. Make your changes
4. Submit a pull request

For graph learning development, install dependencies with:

```bash
cd graph_learning
pip install -r requirements.txt
```

## License

MIT License. See [LICENSE](LICENSE) for details.
