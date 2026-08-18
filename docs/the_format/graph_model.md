# The graph model

An SFF document models a flowsheet as a **directed graph**. `units` are the
graph's nodes, and `streams` are the graph's edges — a stream carries material
or energy from one unit to another, the same way a process flow diagram does.

## Shared registries

Two sections of the document act as shared registries, referenced by ID rather
than duplicated inline:

- `chemicals` — every chemical species used anywhere in the flowsheet, each
  identified by `chemical.id`.
- `utilities` — every utility agent (heating/cooling media, power, and so on)
  used anywhere in the flowsheet, each identified by its utility `id`.

Units and streams point into these registries by ID instead of repeating
chemical or utility data at every point of use, so each chemical or utility is
described once and referenced everywhere it applies.

## Cross-references are string IDs

The graph's edges and the registry lookups are all expressed as plain string
IDs cross-referencing another part of the document:

- `stream.source_unit_id` → `unit.id` — the unit a stream originates from.
- `stream.sink_unit_id` → `unit.id` — the unit a stream flows into.
- `composition[].component_name` → `chemical.id` — a stream's composition
  entries name a chemical by its registry ID.
- Per-unit utility result keys → utility `id` — a unit's utility results are
  keyed by the ID of the utility they consumed.

Because these references are strings rather than embedded objects, a
flowsheet stays a well-formed graph: any consumer can resolve a unit, stream,
chemical, or utility by following its ID rather than parsing a nested
structure.

## Provenance in `metadata`

The `metadata` section carries the flowsheet's provenance rather than its
topology: `sff_version`, `TEA_currency`, a source DOI, feedstocks and
products, microbial hosts, the TEA year, and, as of v0.1.3, `tags` and
`reproducibility`.

## Top-level document shape

Putting the graph, its registries, and its provenance together, a top-level
SFF document has this shape:

```
quantity_units_global
metadata
units
streams
chemicals
utilities
```
