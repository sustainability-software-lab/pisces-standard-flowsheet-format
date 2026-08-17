# Schema Reference

The Standard Flowsheet Format (SFF) is a JSON document strictly adhering to our JSON schema structure. This page breaks down the core sections of the SFF schema in human-readable terms to help developers quickly understand its structure.

> **Current version: v0.1.1.** This release loosens one constraint: the `exclusiveMinimum: 0` on `chemicals[].molar_mass` (CHEM-02) is dropped from the schema and re-homed in the validator as a `warning`. It is a backwards-compatible widening — every valid v0.1.0 document remains a valid v0.1.1 document — and it does not change export output shape. The predecessor v0.1.0 was a milestone release carrying no shape or constraint changes over v0.0.12. The `vX.Y.Z` annotations throughout this page record the version at which each field or constraint was **introduced** (or, for CHEM-02, changed), and remain the definitive history.

## Core Properties

Every valid SFF JSON object contains six essential properties at its root:

1. `metadata`: Contextual data about the process simulation.
2. `units`: The unit operations (nodes).
3. `streams`: Process streams connecting the units (edges).
4. `utilities`: Global heating, cooling, and power utilities used by the process.
5. `chemicals`: A list of pure chemical components involved.
6. `quantity_units_global`: A registry of default quantity units for widely-used quantities and prices, keyed by canonical name; each entry carries the `aliases` a quantity appears under and its `quantity_units` string. Bare numeric quantities elsewhere in the file resolve their units here.

---

### Metadata

The `metadata` object provides high-level information about the process flowsheet, versions, and economic settings.

- **sff_version**: The version of SFF used (e.g., `"1.0"`). *(Required)*
- **TEA_currency**: The currency used to report all cost results, typically `"USD"`. BioSTEAM exports always report `"USD"`. *(Required)*
- **TEA_year**: The baseline year used for calculating costs. *(Required)*
- **source_doi**: A digital object identifier pointing to the publication where the process was introduced.
- **process_title**: Title of the process flowsheet.
- **feedstocks**: Feedstock material streams for this flowsheet, each with a required `stream_id` and an optional `display_name`. *(Required)*
- **products**: Product and co-product material streams for this flowsheet, each with a required `stream_id` and an optional `display_name`. *(Required)*
- **microorganisms**: Microbial hosts used for bioproduction, if applicable. Represented as a list (not a single string) so that co-cultures and multi-host processes can each be a distinct entry; every entry has a required `name` and an optional `label`.
- **flowsheet_designers**: Authors who designed the simulation.
- **reproducibility**: Everything needed to rebuild the environment and re-run the model that produced this flowsheet: the full text and SHA-256 of the environment specification (`environment`) and load script (`load_script`), the pinned `simulator_package` and `flowsheet_model_package` (each identified by a VCS `commit` + `url`, or by a released `version`), and a `resolved` block recording the Python version, platform, environment key, timestamp, and installed package versions observed at export time. Optional — flowsheets exported without a recipe omit it.

---

### Chemicals

The `chemicals` array defines the chemical species available in the simulation. Each chemical must have:
- **id**: A unique string identifier.
- **registry_id**: A standard chemical identifier, like a CAS number or SMILES string.

---

### Quantity Units Global

The `quantity_units_global` object is a registry of default quantity units for widely-used quantities and prices, keyed by canonical name (e.g., `temperature`, `mass_flow`, `price`, `enthalpy_flow`). Each entry carries:
- **aliases**: The field names a quantity appears under across the flowsheet (e.g., `temperature` also covers `T` and `temperature_limit`).
- **quantity_units**: The unit string for that quantity (e.g., `"K"`, `"kg/hr"`, `"USD/kg"`).

Bare numeric scalars elsewhere in the file (stream and utility properties, prices) resolve their units by looking up the relevant field name against this registry's aliases.

---

### Utilities

The `utilities` object is broken down into three main categories of global utilities:

- **heat_utilities**: Heating and cooling utility types (e.g., "high-pressure steam"). Each details its temperature, pressure, regeneration and heat transfer prices, composition, and `quantity_units_for_utility_results` (the units of its per-unit-operation values in `utility_consumption_results`/`utility_production_results`).
- **power_utilities**: Electrical utility types (e.g., "marginal electricity"), listing their `electrical_energy_price` and `quantity_units_for_utility_results`.
- **other_utilities**: Alternative utilities (e.g., combustion-based like "natural gas") with parameters similar to heat utilities, including `quantity_units_for_utility_results`.

---

### Units (Nodes)

The `units` array contains all operational nodes of the process graph (e.g., reactors, distillation columns). Each unit object includes:

- **id**: A unique string identifying this particular unit. *(Required)*
- **unit_type**: A description indicating the kind of operation (e.g., "Distillation"). *(Required)*
- **design_input_specs**: Essential design specifications needed to simulate this unit.
- **design_simulation_method**: The analytical or computational methodology applied to simulate the unit (e.g., "McCabe-Thiele").
- **thermo_property_package**: Defines how thermodynamic parameters (mixture, gamma, phi, PCF) were estimated.
- **reactions**: Detailed definitions for chemical or biological reactions taking place inside the unit, indicating parallel indices, conversions, and target reactants.
- **design_results**: Generated metrics for the operation of this unit.
- **quantity_units_for_design_results**: Quantity units for each key in `design_results`, by the same key (from the simulator's `_units`).
- **purchase_costs** & **installed_costs**: Itemized economic data detailing the cost of this particular unit operation.
- **purchase_cost_correlations**: *(optional, v0.1.2+)* Per-item parametric purchase-cost correlations, keyed by the same item IDs as `purchase_costs`. Each entry records an exponential ("six-tenths rule") correlation so a consumer can re-derive equipment cost at a different design size or cost-year index. For a `power_law` item: `purchase_cost = (CE_target / reference_CE_index) × reference_cost × (size / reference_size)^exponent`, then `installed_cost = installation_factor × purchase_cost`; if `size_upper_bound` is set and exceeded, the item parallelizes into `ceil(size / size_upper_bound)` units. A `custom_function` item omits `reference_cost`/`exponent` (opaque model) — fall back to the recorded `purchase_costs` value. See `sff_checks.md` UNIT-08/UNIT-09.
- **utility_consumption_results** & **utility_production_results**: Realized consumption and generation of power/heat per utility type (linking back to the IDs declared in `utilities`).

---

### Streams (Edges)

The `streams` array maps out the connectivity of the flowsheet, defining how materials flow from one unit to another.

- **id**: A unique string identifying the stream. *(Required)*
- **source_unit_id**: The ID of the originating unit operation. *(Required)*
- **sink_unit_id**: The ID of the receiving unit operation. *(Required)*
- **stream_description**: A qualitative description (e.g., "Make-up solvent").
- **roles**: An optional array (added in v0.0.10) naming the roles this stream plays. Every non-isolated stream carries exactly one base topology role — `input` (a sink but no source), `output` (a source but no sink), or `internal` (both) — and may additionally carry designation roles: `purchased_raw_material` (a priced input), `feedstock` (a feedstock input; can co-occur with `purchased_raw_material`), and `product` (a product output). Values are unique; the enum is `["input", "output", "purchased_raw_material", "feedstock", "product", "internal"]`. Omitted by exporters targeting pre-0.0.10 schemas.
- **price**: A bare number giving the cost per quantity of the stream material. Its units come from `quantity_units_global` under `price` (BioSTEAM-native default `USD/kg`), not an inline unit string.
- **stream_properties**: A detailed block of stream state. `total_mass_flow`, `total_molar_flow`, `temperature`, `pressure`, and `phases` are required (`total_mass_flow` became required in v0.0.12); the remaining scalars are optional. Each scalar below is a bare number whose units come from `quantity_units_global` (BioSTEAM-native defaults noted); `phases` is an object (see below):
    - **total_mass_flow** (`kg/hr`; required as of v0.0.12)
    - **total_molar_flow** (`kmol/hr`)
    - **temperature** (`K`)
    - **pressure** (`Pa`; must be greater than 0 as of v0.0.12)
    - **total_volumetric_flow** (`m3/hr`, optional)
    - **enthalpy_flow** (`kJ/hr`, optional; added in v0.0.11): The whole-stream enthalpy flow rate, relative to the simulator's reference state (from BioSTEAM `stream.H`). Omitted by exporters targeting pre-0.0.11 schemas.
    - **phases**: An object keyed by phase symbol (`l`, `g`, `s`, ...). Each phase carries its own `total_molar_flow` (required) and `composition` (required), plus optional `total_mass_flow` and `total_volumetric_flow`. Each `composition` entry gives a `component_name` (linking to the `chemicals` array IDs) and mol/mass fractions **relative to that phase**; the phase is the parent key, not a per-component field.

---

### Validation constraints (v0.0.12)

v0.0.12 adds eight declarative JSON-Schema constraints, catalogued in `sff_checks.md` and enforced by the schema itself (no code required to check them), plus two validator-enforced warning checks (MET-04 and CHEM-02):

- **MET-01**: `metadata.sff_version` must match the semver pattern `^[0-9]+\.[0-9]+\.[0-9]+$`.
- **MET-04**: `metadata.TEA_year` should lie within a plausible range (1900 ≤ year ≤ current
  calendar year + 1). Enforced by the validator as a `warning` (not the schema), so an
  implausible year flags a warning without making the file non-conforming.
- **MET-05**: `metadata.TEA_currency` must be a non-empty string (`minLength: 1`).
- **MET-06**: `reproducibility.environment.sha256`, `reproducibility.load_script.sha256`, and `reproducibility.resolved.env_key` must each match the 64-hex pattern `^[0-9a-f]{64}$`.
- **UNIT-04**: a reaction's `conversion` must be between 0 and 1, inclusive.
- **UNIT-05**: a reaction must provide at least one of `equation` or `stoichiometry`.
- **STR-11**: a stream's `stream_properties.pressure` must be strictly greater than 0.
- **STR-12**: `stream_properties.total_mass_flow` is required on every stream (gated, breaking change vs. pre-0.0.12 schemas).
- **CHEM-02**: a chemical's `molar_mass` should be strictly greater than 0. Enforced by the
  validator as a `warning` (not the schema, as of v0.1.1), so a non-positive molar mass flags a
  warning without making the file non-conforming.
- **UTIL-05**: `temperature` and `pressure` on `heat_utilities` and `other_utilities` entries must each be strictly greater than 0 (`temperature_limit` is exempt — a cooling-utility limit may legitimately take any value).
