# Schema Reference

The SFF schema is a JSON Schema (draft-07). Below, each top-level section is
described in prose, followed by an auto-generated table rendered directly from
`pisces_sff/schema/sff_schema.json`. Two sections (`streams` and
`quantity_units_global`) reference the schema's shared `definitions` via
`$ref`; because those refs cannot be resolved when the directive is scoped to
a single top-level property, those two sections use a hand-written table
instead (see the note in each).

> **Current version: v0.2.4.** See [Full JSON Schema](full_schema.md) for the
> current schema file and links to every previous version.

## `metadata`
Provenance for the flowsheet: version, currency, DOI, feedstocks/products, TEA year, tags.

From v0.2.1 on, `metadata.additionalProperties` is `true` (previously
`{"type": "string"}`): a producer may attach ad-hoc metadata keys of any JSON
type beyond those defined below, not only string-valued ones. This is a
backwards-compatible loosening -- every file valid under an earlier version
remains valid -- and does not change any field the reference exporter emits.

v0.2.2 adds the following for flowsheets extracted from literature rather than
exported from a simulator:

- `process_simulator` (still required) and `microorganisms` (still optional)
  also accept one of two exact strings stating explicitly why no simulator /
  microbial host is given: `"not_reported"` (the source does not name one) or
  `"not_applicable"` (there is none -- e.g., a flowsheet taken from a patent
  that was never simulated, or an abiotic process). `null` and `"None"` are
  **not** accepted: absence must say which of the two it is.
- `prose_type` (`journal_article`, `patent`, `thesis`,
  `technical_report`, `conference_paper`, `book`, `other`) is **required** when
  `tags` contains `extracted-from-prose` or `extracted-from-table`. (Named
  `literature_type` in v0.2.2 and v0.2.3; renamed `prose_type` in v0.2.4
  for consistency with `image_type` and the `extracted-from-prose` tag. A
  file that still writes `literature_type` beside one of those tags must
  rename it to validate.)
- `image_type` (`process_flow_diagram`, `block_flow_diagram`,
  `piping_and_instrumentation_diagram`, `other`) is **required** when `tags`
  contains `extracted-from-image`.
- `extraction_method` (any JSON type -- e.g. a string naming the pipeline or
  procedure, or a structured object) is **required** when `tags` contains any
  `extracted-from-*` tag.
- In files carrying an `extracted-from-*` tag, a stream phase's
  `total_molar_flow` is optional (see the `streams` section below). v0.2.3
  extends the same relaxation to a chemical's `included_in_thermo_equilibria`
  (see the `chemicals` section below).

Unreleased (proposed with this change): the same tag-conditional pattern for the
partial data a paper reports. In files carrying an `extracted-from-*` tag:

- `stream_properties` requires none of its fields: a stream may give any subset
  of `total_mass_flow`, `total_molar_flow`, `temperature`, `pressure` and
  `phases`. Fields that are present keep their types and bounds.
- A phase composition entry needs `mol_fraction` **or** `mass_fraction`.
- A `purchase_cost_correlations` entry needs only `correlation_type`, `basis`
  and `reference_size` (plus `reference_cost` and `exponent` for `power_law`);
  `basis_units`, `reference_CE_index`, `installation_factor` and `power_rate`
  become optional.
- `heat_utilities` / `other_utilities` entries need only `id` and
  `quantity_units_for_utility_results`.
- `TEA_year` may be `"not_reported"` or `"not_applicable"` instead of a number
  (`TEA_currency`, a string, can already carry the same two strings).

A root-level `else` restores every one of these requirements in all other
files, so each previously valid file stays valid and the reference exporter's
output is unaffected.

The three conditional requirements are the schema half of `TAG-01` (like
`reproducibility.comparison_rtol` beside `reproducible`): a file declaring an
`extracted-from-*` tag written before v0.2.2 must add the matching field to
validate. The reference exporter never stamps those tags, so its output is
unchanged.

```{jsonschema} ../../pisces_sff/schema/sff_schema.json#/properties/metadata
```

## `units`
The unit operations (graph nodes).

From v0.2.2 on, a unit accepts additional, producer-defined properties
beyond those defined below (previously `additionalProperties: false`). The
nested `purchase_cost_correlations` entries remain closed.

From v0.1.4 on, the reference exporter fills `design_input_specs` from a
per-unit-type registry (`pisces_sff/export/design_specs/biosteam.yaml`): each unit
class declares which initialization parameters constitute its design input
specification and an ordered list of accessors to read each one (e.g. a
`Pump` whose `P` is unset falls back to its outlet's pressure, still
reported under `P`). Parameters that resolve to no value are omitted --
files exported at v0.1.4+ contain no `null` design specs -- and unit types
absent from the registry carry `{}`. This changes only the *contents* of the
existing free-form object; the schema shape is unchanged, so older files
remain valid.

```{jsonschema} ../../pisces_sff/schema/sff_schema.json#/properties/units
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
| `stream_properties` | object, see below | | Structured object specifying the properties of the stream. |

`stream_properties` fields:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `total_mass_flow` | number | yes, unless extracted | Total mass flow rate. Quantity units: `quantity_units_global` `mass_flow` (default `kg/hr`). |
| `total_volumetric_flow` | number | | Total volumetric flow rate. Quantity units: `quantity_units_global` `volumetric_flow` (default `m3/hr`). |
| `total_molar_flow` | number | yes, unless extracted | Total molar flow rate. Quantity units: `quantity_units_global` `molar_flow` (default `kmol/hr`). |
| `temperature` | number, minimum 0 | yes, unless extracted | Temperature. Quantity units: `quantity_units_global` `temperature` (default `K`). |
| `pressure` | number, exclusiveMinimum 0 | yes, unless extracted | Pressure. Quantity units: `quantity_units_global` `pressure` (default `Pa`). |
| `enthalpy_flow` | number | | Total enthalpy flow rate of the stream (relative to the simulator's reference state). Optional; emitted from schema v0.0.11 on. Quantity units: `quantity_units_global` `enthalpy_flow` (default `kJ/hr`). |
| `phases` | object keyed by phase symbol, minProperties 1; each value is a `stream_phase` (below) | yes, unless extracted | The phases present in this stream, keyed by phase symbol (`l` = liquid, `g` = gas, `s` = solid, ...). Each value details that phase's own total flows and composition. |

`#/definitions/stream_phase` — one phase of a stream: its own total flows and its molar/mass composition, with all fractions taken relative to this phase:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `total_mass_flow` | number | | Total mass flow rate of this phase. Quantity units: `quantity_units_global` `mass_flow` (default `kg/hr`). |
| `total_molar_flow` | number | yes, unless extracted | Total molar flow rate of this phase. Quantity units: `quantity_units_global` `molar_flow` (default `kmol/hr`). Optional from v0.2.2 when `metadata.tags` contains an `extracted-from-*` tag; required otherwise. |
| `total_volumetric_flow` | number | | Total volumetric flow rate of this phase. Quantity units: `quantity_units_global` `volumetric_flow` (default `m3/hr`). |
| `composition` | array of objects (below) | yes | An array detailing the chemical components present in this phase. All fractions are relative to this phase. |

From v0.2.2 the `stream_phase` definition itself requires only `composition`;
a root-level `if`/`else` requires each phase's `total_molar_flow` unless
`metadata.tags` contains `extracted-from-prose`, `extracted-from-image`, or
`extracted-from-table` (literature sources rarely report per-phase molar
flows). This is a loosening: every file valid under an earlier version stays
valid. Since the change proposed above, the stream-level fields are also
optional in extracted files (required otherwise).

Each `composition` entry:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `component_name` | string | yes | Chemical component ID, cross-referencing the `chemicals` array. |
| `mol_fraction` | number, 0-1 | yes, unless extracted | Mole fraction of this component within this phase. In extracted files, `mol_fraction` or `mass_fraction` is required. |
| `mass_fraction` | number, 0-1 | | Mass fraction of this component within this phase. |

## `chemicals`
The shared chemical registry.

v0.2.3 renames each chemical's `included_in_thermo` flag to
`included_in_thermo_equilibria`; its meaning and its `registry_id` (when
`true`) / `molar_mass` (when `false`) conditions are unchanged. This is a
breaking change: a file that still writes `included_in_thermo` must rename it
to validate. The flag is required unless `metadata.tags` contains
`extracted-from-prose`, `extracted-from-image`, or `extracted-from-table`
(the same root-level `if`/`else` that relaxes a phase's `total_molar_flow`).
When an extracted file omits it, neither `registry_id` nor `molar_mass` is
required. The reference exporter writes the new name from v0.2.3; its earlier
versioned exporters keep writing `included_in_thermo`.

```{jsonschema} ../../pisces_sff/schema/sff_schema.json#/properties/chemicals
```

## `utilities`
The shared utility registry.

From v0.2.2 on, `heat_utilities` and `other_utilities` entries accept
additional, producer-defined properties beyond those defined below
(previously `additionalProperties: false`); `power_utilities` entries already
accepted additional numeric properties.

```{jsonschema} ../../pisces_sff/schema/sff_schema.json#/properties/utilities
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
