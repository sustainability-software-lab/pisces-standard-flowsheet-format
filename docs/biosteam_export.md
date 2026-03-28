# BioSTEAM Export

The `pisces_sff.biosteam` subpackage provides a function to export a simulated [BioSTEAM](https://github.com/BioSTEAMDevelopmentGroup/biosteam) system directly to an SFF JSON file.

## Subpackage Location

```
pisces_sff/
└── biosteam/
    ├── __init__.py     # exports export_biosteam_flowsheet_sff
    └── export.py       # implementation
```

## Installation

```bash
git clone https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format-new.git
cd pisces-standard-flowsheet-format-new
pip install -e ".[biosteam]"
```

This installs `pisces-sff` in editable mode along with `biosteam` and `thermosteam`.

## Usage

```python
from pisces_sff.biosteam import export_biosteam_flowsheet_sff
from biorefineries import sugarcane as sc

sc.load()
sys = sc.create_sugarcane_to_ethanol_system()
sys.simulate()

export_biosteam_flowsheet_sff(sys, "sugarcane_ethanol.json")
```

This produces a JSON file conforming to the [SFF schema](schema_reference.md) with all unit operations, streams, thermodynamic data, utility data, and cost estimates.

## API Reference

### `export_biosteam_flowsheet_sff(sys, file_path)`

Serialize a simulated BioSTEAM `System` object to an SFF JSON file.

| Parameter | Type | Description |
|-----------|------|-------------|
| `sys` | `biosteam.System` | A simulated BioSTEAM system (call `sys.simulate()` first) |
| `file_path` | `str` or `Path` | Output path for the JSON file |

The output JSON contains:

| Key | Description |
|-----|-------------|
| `units` | All unit operations with design specs, simulation methods, reactions, and cost data |
| `streams` | Material streams with flow rates, temperature, pressure, and composition |
| `heat_utilities` | Heat utility agents (cooling water, steam, etc.) with pricing |
| `power_utilities` | Electricity utility with pricing |
| `other_utilities` | Other utilities (e.g., natural gas) with pricing |

## Example Flowsheets

Pre-exported SFF files are available in `exported_flowsheets/bioindustrial_park/` for 11 bioindustrial processes:

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

## Adding Support for Other Simulators

The SFF schema is simulator-agnostic. To add export support for another simulator (e.g., Aspen Plus), create a new module in the repository root or as a new subpackage under `pisces_sff/`, implementing a function that produces JSON conforming to [`schema/schema_v_0.0.2.json`](https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format-new/blob/main/schema/schema_v_0.0.2.json).
