# Extending SFF

## Adding a new schema version

Adding a new schema version means writing one small function in
`pisces_sff/_export.py`, named
`export_biosteam_flowsheet_sff_<major>_<minor>_<patch>`. That name is its only
registration: `export_biosteam_flowsheet` resolves the versioned function to
call purely by looking up the name built from the `sff_version` string it was
given, so there is no separate registry to update. The function's own
`sff_version` default must match its name suffix, since that default is what
ends up in the exported document's `metadata.sff_version` when the caller
doesn't override it.

The heavy lifting is shared. `_build_sff_dict` assembles the core document —
`metadata`, `units`, `streams`, `chemicals`, `utilities` — once, and each
versioned `export_biosteam_flowsheet_sff_*` function is a thin wrapper around
it that adds only the differences introduced by that particular version. This
keeps the cost of a new version proportional to what actually changed, rather
than a full copy of the exporter that would drift from its siblings over time.

Where a change must apply only from some version onward, it's gated on a
module-level threshold — a `_*_SINCE` constant — compared against
`version_tuple(sff_version)`. This is what keeps older exporters byte-stable:
a function written against schema 0.0.9 keeps producing exactly the same
output it always has, even after later versions add fields or tighten
constraints, because the gated behavior simply doesn't trigger below its
threshold.

## Adding a new simulator

Supporting a new simulator means writing a new versioned exporter that maps
that simulator's own objects onto the same SFF document shape the BioSTEAM
exporter produces — units as nodes, streams as edges, chemicals and utilities
as shared registries. Validation is deliberately decoupled from export:
`validate_json_against_schema` and `validate_flowsheet_against_SFF` operate on
any SFF JSON file, regardless of which exporter — or which simulator —
produced it, so a new adapter gets the same validation path for free.

## Adding a new requirement

A new semantic requirement is catalogued in `sff_checks.md` first, under a new
check ID, before any code is written against it. Once catalogued, it's
implemented in the schema and/or in `_validate.py` as a `_check_*(ctx)`
function, registered in the `_CHECKS` list so the validator picks it up
automatically. Cataloguing before implementing keeps the check ID and the code
that enforces it traceable to each other.
