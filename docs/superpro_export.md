# SuperPro Designer → SFF Export

Export a SuperPro Designer `.spf` process model to the
[Standard Flowsheet Format (SFF)](https://sustainability-software-lab.github.io/pisces-standard-flowsheet-format/)
JSON file — directly from your local machine, with no cloud or server infrastructure required.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Windows** | SuperPro Designer's COM interface is Windows-only |
| **SuperPro Designer v14+** | Must be installed and licensed on the same machine |
| **Python 3.9+** | Any recent CPython distribution |
| **pywin32** | Installed via `pip install "pisces-sff[superpro]"` |

---

## Installation

```bash
pip install "pisces-sff[superpro]"
```

If you have already installed `pisces-sff` without extras:

```bash
pip install pywin32
```

After installing pywin32 for the first time, you may need to run the post-install
script once as administrator:

```bat
python -m pywin32_postinstall -install
```

---

## Quick Start

### Python API

```python
from pisces_sff.superpro import export_superpro_flowsheet_sff

export_superpro_flowsheet_sff(
    spf_path="my_process.spf",
    output_path="my_process.sff.json",
)
```

The function opens SuperPro, runs the mass/energy balances and economic
calculations, extracts all data, and writes the SFF JSON file.

### Command Line

```bat
python -m pisces_sff.superpro my_process.spf my_process.sff.json
```

Run `python -m pisces_sff.superpro --help` for all options.

---

## Supplying Metadata

Some fields — like `process_title`, `product_name`, and `organism` — are not
stored in the `.spf` COM model and must be provided by the user. Pass them
via the `metadata` argument (Python) or command-line flags (CLI):

**Python:**

```python
from pisces_sff.superpro import export_superpro_flowsheet_sff

sff = export_superpro_flowsheet_sff(
    spf_path="Ethanol_v14.spf",
    output_path="ethanol.sff.json",
    metadata={
        "process_title":       "Corn Stover Ethanol",
        "product_name":        "Ethanol",
        "organism":            "Zymomonas mobilis",
        "flowsheet_designers": "NREL Bioenergy Center",
        "source_doi":          "10.2172/1115954",
        "TEA_year":            2011,
    },
)

print(f"Exported {len(sff['units'])} units and {len(sff['streams'])} streams.")
```

**CLI:**

```bat
python -m pisces_sff.superpro ^
    Ethanol_v14.spf ethanol.sff.json ^
    --title "Corn Stover Ethanol" ^
    --product "Ethanol" ^
    --organism "Zymomonas mobilis" ^
    --designers "NREL Bioenergy Center" ^
    --doi "10.2172/1115954" ^
    --year 2011
```

### All CLI Options

```
usage: python -m pisces_sff.superpro <model.spf> <output.sff.json> [options]

positional arguments:
  spf_path              Path to the SuperPro .spf file
  output_path           Destination path for the SFF JSON file

options:
  --no-balances         Skip DoMEBalances / DoEconomicCalculations
  --no-molar-flows      Skip automatic molar flow computation
  --quiet, -q           Suppress progress output

metadata overrides:
  --title TITLE
  --product PRODUCT
  --organism ORGANISM
  --designers DESIGNERS
  --doi DOI
  --year YEAR           TEA base year (integer)
  --currency CURRENCY   ISO 4217 code, e.g. USD, EUR
```

---

## What Is Extracted

The following data is read from the SuperPro model via COM:

### Flowsheet Metadata

| SFF field | Source |
|---|---|
| `TEA_year` | COM VarID 36114 |
| `TEA_currency` | COM VarID 36130 (full name → ISO code) |
| `flowsheet_designers` | VarIDs 30129 + 30131 (name + company) |
| `product_name` | VarID 30491 (main product stream name) |
| `process_description` | VarID 30135 |
| `annual_throughput_kg_yr` | VarID 29956 |
| `total_purchase_cost_usd` | VarID 30464 |
| `DFC_usd` | VarID 30466 |
| `annual_revenue_usd` | VarID 36096 |
| `annual_operating_cost_usd` | VarID 30482 |
| `annual_labor_cost_usd` | VarID 30471 |
| `annual_electricity_cost_usd` | VarID 30489 |
| `annual_waste_treatment_usd` | VarID 30477 |
| `gross_profit_usd` | VarID 36103 |
| `net_profit_usd` | VarID 36104 |
| `gross_margin_pct` | VarID 36098 |
| `IRR_before_taxes_pct` | VarID 36100 |
| `IRR_after_taxes_pct` | VarID 36101 |
| `NPV_usd` | VarID 36109 |
| `ROI_pct` | VarID 36099 |
| `payback_period_years` | VarID 36105 |
| `is_batch_mode` | VarID 29953 |
| `number_of_batches_per_year` | VarID 30212 |
| `annual_operating_time_s` | VarID 30213 |

### Per Unit (Unit Procedures)

- `id` — procedure name
- `unit_type` — mapped from SuperPro operation type string
- `equipment_name`, `equipment_type`
- `purchase_costs` — equipment purchase cost ($)
- `design_results` — volume (m³), diameter (m), number of parallel units

### Per Stream

- `id`, `source_unit_id`, `sink_unit_id`
- `stream_type` — `"feed"`, `"product"`, `"waste"`, or `"internal"`
  (from COM classification flags: isInputStream, isOutputStream, isWaste, isRawMaterial)
- `price` — unit purchasing/disposal cost in USD/kg (feed and waste streams only)
- `stream_properties`: mass flow (kg/h), volumetric flow (m³/h), temperature (K), pressure (Pa)
- `composition` — mole fractions per component, phase (always `"l"` — COM v14 does not expose per-component phase)
- `total_molar_flow` — computed from composition + built-in MW database (see below)

### Molar Flow Computation

SuperPro's COM interface exposes mole fractions per component but not total
molar flow directly. The exporter computes it automatically:

```
avg_MW = Σ (x_i × MW_i)          [mole-fraction weighted]
molar_flow (kmol/h) = mass_flow (kg/h) / avg_MW (g/mol) / 1000
```

Molecular weights are looked up in a local dictionary of 300+ common
bioprocess chemicals (sugars, amino acids, solvents, gases, inorganics,
pharmaceuticals, enzymes, etc.). For names not in the local dictionary,
PubChem is queried automatically and the result is cached to
`mw_cache.json` alongside the package for future use.

Pass `enrich_molar_flows=False` to skip this step.

---

## What Is NOT Available via COM

The following fields require manual entry and cannot be extracted
automatically:

| Field | Reason |
|---|---|
| `process_title` | Not stored in the COM model — use `--title` or `metadata=` |
| `product_name` | Use `--product` or fall back to detected main product stream |
| `organism` | Not stored in the COM model |
| `flowsheet_designers` | Partially available (VarIDs 30129/30131); often empty |
| `source_doi` | Not stored in the COM model |
| Reaction kinetics / rate equations | Not exposed via COM v14 |
| Utility definitions (heat/power/other) | No enumerable utility list in COM v14 |
| Per-component phase state | COM returns composition only; phase hardcoded to `"l"` |
| Stream prices for **product** streams | COM VarID 25088 returns annual revenue (not $/kg) for products — skipped |

---

## Troubleshooting

### Designer.exe hangs on open

SuperPro sometimes shows a crash-recovery dialog on startup, which blocks
COM registration. The exporter has a 90-second watchdog that kills
`Designer.exe` and retries automatically. If the file still fails to open:

1. Open the `.spf` file in SuperPro manually to confirm it loads correctly.
2. Close SuperPro, then run the exporter again.
3. Try `--no-balances` to skip re-running the simulation.

### "GetObject failed" after 2 attempts

Usually means Designer.exe from a previous failed run is still exiting.
Wait 5–10 seconds and retry.

### License dialog on startup

If SuperPro shows a license dialog each time it starts, run SuperPro
manually once to accept the license, then retry the exporter.

### pywin32 ImportError

Run the post-install script:

```bat
python -m pywin32_postinstall -install
```

### "Not on Windows" error on macOS / Linux

SuperPro Designer is Windows-only. The exporter will raise `RuntimeError`
immediately if called on a non-Windows platform.

### Molar flows not computed for some streams

Streams whose components are not in the local MW database and PubChem
cannot resolve will skip molar flow computation. Check the console output
for a list of unresolved component names. You can add custom entries to
the local dictionary in `pisces_sff/superpro/_chemical_properties.py`.

---

## Output Format

The output is a valid SFF v0.0.2 JSON document. See the
[SFF schema](https://sustainability-software-lab.github.io/pisces-standard-flowsheet-format/)
for the full specification. The file can be loaded directly in
[Project Pisces](https://www.projectpisces.org) or processed programmatically:

```python
from pisces_sff import read_sff, validate_sff

sff = read_sff("my_process.sff.json")
print(sff["metadata"]["process_title"])
print(f"{len(sff['streams'])} streams")
```
