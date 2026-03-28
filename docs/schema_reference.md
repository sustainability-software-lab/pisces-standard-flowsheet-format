# Schema Reference

The Standard Flowsheet Format (SFF) is a JSON document strictly adhering to our JSON schema structure. This page breaks down the core sections of the SFF schema in human-readable terms to help developers quickly understand its structure.

## Core Properties

Every valid SFF JSON object contains five essential properties at its root:

1. `metadata`: Contextual data about the process simulation.
2. `units`: The unit operations (nodes).
3. `streams`: Process streams connecting the units (edges).
4. `utilities`: Global heating, cooling, and power utilities used by the process.
5. `chemicals`: A list of pure chemical components involved.

---

### Metadata

The `metadata` object provides high-level information about the process flowsheet, versions, and economic settings.

- **sff_version**: The version of SFF used (e.g., `"1.0"`). *(Required)*
- **TEA_currency**: The currency used in the techno-economic analysis, typically `"USD"`.
- **TEA_year**: The baseline year used for calculating costs. *(Required)*
- **source_doi**: A digital object identifier pointing to the publication where the process was introduced.
- **process_title**: Title of the process flowsheet.
- **product_name**: Primary product being manufactured.
- **organism**: Organism used for bioproduction, if applicable.
- **flowsheet_designers**: Authors who designed the simulation.

---

### Chemicals

The `chemicals` array defines the chemical species available in the simulation. Each chemical must have:
- **id**: A unique string identifier.
- **registry_id**: A standard chemical identifier, like a CAS number or SMILES string.

---

### Utilities

The `utilities` object is broken down into three main categories of global utilities:

- **heat_utilities**: Heating and cooling utility types (e.g., "high-pressure steam"). Each details its temperature, pressure, regeneration and heat transfer prices, composition, and results unit.
- **power_utilities**: Electrical utility types (e.g., "marginal electricity"), listing their electricity price.
- **other_utilities**: Alternative utilities (e.g., combustion-based like "natural gas") with parameters similar to heat utilities.

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
- **purchase_costs** & **installed_costs**: Itemized economic data detailing the cost of this particular unit operation.
- **utility_consumption_results** & **utility_production_results**: Realized consumption and generation of power/heat per utility type (linking back to the IDs declared in `utilities`).

---

### Streams (Edges)

The `streams` array maps out the connectivity of the flowsheet, defining how materials flow from one unit to another.

- **id**: A unique string identifying the stream. *(Required)*
- **source_unit_id**: The ID of the originating unit operation. *(Required)*
- **sink_unit_id**: The ID of the receiving unit operation. *(Required)*
- **stream_description**: A qualitative description (e.g., "Make-up solvent").
- **price**: Cost per quantity of the stream material (e.g., $/kg).
- **stream_properties**: A detailed block containing strictly required state information:
    - **total_mass_flow**
    - **total_volumetric_flow**
    - **temperature**
    - **pressure**
    - **total_molar_flow** (optional)
    - **composition**: An array defining the phase, component name (linking to the `chemicals` array IDs), and exact mol fraction.
