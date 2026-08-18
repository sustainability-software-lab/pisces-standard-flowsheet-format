# Schema Reference

The SFF schema is a JSON Schema (draft-07). Below, each top-level section is
described in prose, followed by an auto-generated table rendered directly from
`pisces_sff/schema/sff_schema.json`. Two sections (`streams` and
`quantity_units_global`) reference the schema's shared `definitions` via
`$ref`; because those refs cannot be resolved when the directive is scoped to
a single top-level property, those two sections use a hand-written table
instead (see the note in each).

> **Current version: v0.1.3.** See [Full JSON Schema](full_schema.md) for the
> current schema file and links to every previous version.

## `metadata`
Provenance for the flowsheet: version, currency, DOI, feedstocks/products, TEA year, tags.

```{jsonschema} ../pisces_sff/schema/sff_schema.json#/properties/metadata
```

## `units`
The unit operations (graph nodes).

```{jsonschema} ../pisces_sff/schema/sff_schema.json#/properties/units
```

## `streams`
The streams (graph edges).

```{note}
This section is hand-written rather than rendered by `{jsonschema}`. The
auto-generated table leaves `stream_properties.phases` as an unresolved
`$ref` to `#/definitions/stream_phase` — the directive is scoped to
`#/properties/streams`, so it cannot follow a `$ref` back into the schema's
top-level `definitions`. The tables below are built from the same schema
file's own `description` strings.
```

Each entry in the `streams` array:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | A unique identifier for this stream. |
| `source_unit_id` | string | yes | The ID of the unit operation from which this stream originates. |
| `sink_unit_id` | string | yes | The ID of the unit operation that this stream enters. |
| `stream_description` | string | | A qualitative description of the stream from the perspective of either the source (e.g., 'Centrifugate') or sink unit operation (e.g., 'Make-up solvent'). |
| `price` | number | | Price per unit mass of the stream material. Quantity units are declared in the top-level `quantity_units_global` under `price` (default `USD/kg`). |
| `roles` | array of string, enum `[input, output, purchased_raw_material, feedstock, product, internal]`, unique items | | The roles this stream plays in the flowsheet. A stream always carries exactly one base topology role (`input` \| `output` \| `internal`) and may additionally carry designation roles (`purchased_raw_material`, `feedstock`, `product`). Emitted from schema v0.0.10 on; optional, so files written against earlier versions remain valid. |
| `stream_properties` | object, see below | yes | Structured object specifying the properties of the stream. |

`stream_properties` fields:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `total_mass_flow` | number | yes | Total mass flow rate. Quantity units: `quantity_units_global` `mass_flow` (default `kg/hr`). |
| `total_volumetric_flow` | number | | Total volumetric flow rate. Quantity units: `quantity_units_global` `volumetric_flow` (default `m3/hr`). |
| `total_molar_flow` | number | yes | Total molar flow rate. Quantity units: `quantity_units_global` `molar_flow` (default `kmol/hr`). |
| `temperature` | number, minimum 0 | yes | Temperature. Quantity units: `quantity_units_global` `temperature` (default `K`). |
| `pressure` | number, exclusiveMinimum 0 | yes | Pressure. Quantity units: `quantity_units_global` `pressure` (default `Pa`). |
| `enthalpy_flow` | number | | Total enthalpy flow rate of the stream (relative to the simulator's reference state). Optional; emitted from schema v0.0.11 on. Quantity units: `quantity_units_global` `enthalpy_flow` (default `kJ/hr`). |
| `phases` | object keyed by phase symbol, minProperties 1; each value is a `stream_phase` (below) | yes | The phases present in this stream, keyed by phase symbol (`l` = liquid, `g` = gas, `s` = solid, ...). Each value details that phase's own total flows and composition. |

`#/definitions/stream_phase` — one phase of a stream: its own total flows and its molar/mass composition, with all fractions taken relative to this phase:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `total_mass_flow` | number | | Total mass flow rate of this phase. Quantity units: `quantity_units_global` `mass_flow` (default `kg/hr`). |
| `total_molar_flow` | number | yes | Total molar flow rate of this phase. Quantity units: `quantity_units_global` `molar_flow` (default `kmol/hr`). |
| `total_volumetric_flow` | number | | Total volumetric flow rate of this phase. Quantity units: `quantity_units_global` `volumetric_flow` (default `m3/hr`). |
| `composition` | array of objects (below) | yes | An array detailing the chemical components present in this phase. All fractions are relative to this phase. |

Each `composition` entry:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `component_name` | string | yes | Chemical component ID, cross-referencing the `chemicals` array. |
| `mol_fraction` | number, 0-1 | yes | Mole fraction of this component within this phase. |
| `mass_fraction` | number, 0-1 | | Mass fraction of this component within this phase. |

## `chemicals`
The shared chemical registry.

```{jsonschema} ../pisces_sff/schema/sff_schema.json#/properties/chemicals
```

## `utilities`
The shared utility registry.

```{jsonschema} ../pisces_sff/schema/sff_schema.json#/properties/utilities
```

## `quantity_units_global`
The global quantity-units registry (bare-number model).

```{note}
This section is hand-written rather than rendered by `{jsonschema}`. Every
property here is a `$ref` to `#/definitions/quantity_unit_entry`; since the
directive is scoped to `#/properties/quantity_units_global`, it cannot follow
that ref back into `definitions`, so the auto-generated table would show
nothing but an unresolved `:ref:` for every row. The table below is built
from the same schema file's own `description` strings.
```

Global default quantity units for widely-used quantities, keyed by canonical
quantity name. Values of these quantities appear as bare numbers elsewhere in
the flowsheet and take their units from here. (Note: `units` in this schema
always means unit operations; unit-of-measure information is always called
"quantity units".)

The canonical quantity names declared in the schema — `temperature`,
`pressure`, `mass_flow`, `molar_flow`, `volumetric_flow`, `molar_mass`,
`price`, `electrical_energy_price`, `regeneration_price`,
`heat_transfer_price`, `enthalpy_flow` — are each optional and, if present,
each takes the `quantity_unit_entry` shape below. `additionalProperties` is
also a `quantity_unit_entry`, so a file may register further canonical
quantity names beyond this list.

`#/definitions/quantity_unit_entry` — a quantity-unit registry entry: the field names a quantity appears under, and its unit string:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `aliases` | array of string, minItems 1 | yes | Field names this quantity appears under in the flowsheet (so a consumer can resolve, e.g., `T` or `total_mass_flow` to this quantity). |
| `quantity_units` | string | yes | Unit string for this quantity (BioSTEAM default). |
