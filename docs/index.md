# Standard Flowsheet Format (SFF)

The **Standard Flowsheet Format (SFF)** is a JSON-based standard for representing
chemical and bioprocess flowsheets so that designs are portable across process
simulators. A flowsheet is modeled as a **directed graph**: `units` are nodes,
`streams` are edges, with `chemicals` and `utilities` as shared registries
referenced by ID, plus `metadata` for provenance. The format and its reference
corpus are consumed by [Project PISCES](https://projectpisces.org/).

```{image} images/SFF_visual_representation.png
:alt: Simplified visual representation of the SFF schema
:align: center
```

## Why SFF?

- **Interoperability** — read and write flowsheets across simulators; the schema is simulator-agnostic (BioSTEAM export ships in-box).
- **Machine- and human-readable** — plain JSON validated by a public JSON Schema (draft-07).
- **Provenance & reproducibility** — records simulator, DOI, feedstocks/products, TEA year, and machine-verified provenance tags.

## What this repo provides

- **The spec** — `pisces_sff/schema/sff_schema.json` (current version **0.1.3**).
- **A reference exporter** — serializes a simulated BioSTEAM `System` to conforming SFF JSON.
- **A two-layer validator** — a `jsonschema` structural gate plus a semantic-check engine.
- **A corpus** — 18 pre-exported bioindustrial flowsheets.

```{toctree}
:hidden:
:caption: Getting Started
:maxdepth: 1

getting_started/installation
getting_started/quickstart
```

```{toctree}
:hidden:
:caption: The Format
:maxdepth: 1

the_format/graph_model
the_format/quantity_units
```
