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

## Adding a new model recipe

A model recipe is a directory under `pisces_sff/models/` — for BioSTEAM models,
`pisces_sff/models/biosteam_models/M_<SIM>_<NN>/` — holding up to three files.
`load.py` is the code: it builds and simulates the system, and its
`EXPORT_KWARGS` dictionary carries any export-behavior flags; its `MODEL_NAME`
constant must equal the directory name (a Tier 1 test enforces this).
`environment.yaml` pins the conda environment the export harness builds, or
reuses, to run the export reproducibly. `extended_metadata.yaml` is the
human-authored descriptive metadata — source DOI, process title, designers,
microorganisms — and may be omitted, in which case the export simply leaves
those optional fields out.

Names follow the `M_<SIMULATOR>_<NN>` / `SF_<SIMULATOR>_<NN>` convention
(`BST` for BioSTEAM): a new item takes the next free number, numbers are
opaque permanent IDs that are never reused, and they are zero-padded to two
digits — three from 100 on. A paired model and flowsheet usually share a
number, but the authoritative pairing is the entry in
`pisces_sff/models/all_models.yaml`, the model registry — never the string
convention. Registration there is mandatory, not advisory:
`regenerate_corpus` refuses to run at all while a recipe directory on disk is
absent from the registry, and a Tier 1 consistency test fails for the same
reason, so an unregistered recipe cannot slip into the corpus silently.

```{raw} html
<figure class="viz-fig viz-fig--wide">
  <svg viewBox="0 0 760 340" role="img" aria-labelledby="viz-reg-t">
    <title id="viz-reg-t">all_models.yaml is the single source of truth pairing each model recipe with its exported flowsheet; corpus regeneration and the two generated READMEs are derived from it and guarded by a pre-commit hook and a sync test.</title>
    <rect class="viz-node--ghost" x="56" y="76" width="150" height="54" rx="8"/>
    <rect class="viz-node--ghost" x="48" y="68" width="150" height="54" rx="8"/>
    <rect class="viz-node" x="40" y="60" width="150" height="54" rx="8"/>
    <text class="t-mono" x="115" y="83" text-anchor="middle" font-weight="600">M_BST_01</text>
    <text class="t-muted t-small" x="115" y="102" text-anchor="middle">model recipe</text>
    <text class="t-muted t-small" x="123" y="150" text-anchor="middle">M_BST_02 …</text>
    <rect class="viz-node--ghost" x="586" y="76" width="150" height="54" rx="8"/>
    <rect class="viz-node--ghost" x="578" y="68" width="150" height="54" rx="8"/>
    <rect class="viz-node" x="570" y="60" width="150" height="54" rx="8"/>
    <text class="t-mono" x="645" y="83" text-anchor="middle" font-weight="600">SF_BST_01</text>
    <text class="t-muted t-small" x="645" y="102" text-anchor="middle">exported flowsheet</text>
    <text class="t-muted t-small" x="653" y="150" text-anchor="middle">SF_BST_02 …</text>
    <rect class="viz-node--em" x="280" y="52" width="200" height="70" rx="8"/>
    <text class="t-mono" x="380" y="80" text-anchor="middle" font-weight="600">all_models.yaml</text>
    <text class="t-small" x="380" y="100" text-anchor="middle">the registry — single source of truth</text>
    <line class="viz-edge viz-edge--accent" x1="190" y1="87" x2="280" y2="87"/>
    <line class="viz-edge viz-edge--accent" x1="480" y1="87" x2="570" y2="87"/>
    <text class="t-muted t-small" x="380" y="42" text-anchor="middle">pairs model ↔ flowsheet</text>
    <line class="viz-edge" x1="330" y1="122" x2="240" y2="196"/>
    <path class="viz-head" d="M 236 190 l 2 12 l 9 -8 z"/>
    <line class="viz-edge" x1="430" y1="122" x2="520" y2="196"/>
    <path class="viz-head" d="M 513 200 l 11 4 l -2 -12 z"/>
    <rect class="viz-node" x="80" y="206" width="250" height="76" rx="8"/>
    <text class="t-mono t-small" x="205" y="230" text-anchor="middle" font-weight="600">regenerate_corpus</text>
    <text class="t-muted t-small" x="205" y="250" text-anchor="middle">unregistered recipe on disk →</text>
    <text class="t-muted t-small" x="205" y="266" text-anchor="middle">hard error before export</text>
    <rect class="viz-node" x="410" y="206" width="270" height="76" rx="8"/>
    <text class="t-small" x="545" y="228" text-anchor="middle" font-weight="600">generated READMEs</text>
    <text class="t-muted t-small" x="545" y="246" text-anchor="middle">models/ + exported_flowsheets/ — byte-identical</text>
    <rect class="viz-node--em" x="448" y="256" width="194" height="20" rx="4"/>
    <text class="t-small t-accent" x="545" y="270" text-anchor="middle">guarded: pre-commit hook + sync test</text>
    <text class="t-muted t-small" x="380" y="322" text-anchor="middle">old names traceable via <tspan class="t-mono">git log --follow</tspan></text>
  </svg>
  <figcaption class="viz-caption"><span class="viz-mono">all_models.yaml</span> pairs each model recipe with its flowsheet; corpus regeneration and the two generated READMEs derive from it.</figcaption>
</figure>
```

The registry validates that everything an entry references actually exists,
including the exported flowsheet file — so a brand-new model is exported once
before it is registered. `export_model(model_dir, output_path)` from
`pisces_sff._harness` is that first-export path: it provisions the pinned
environment and writes the SFF JSON, which for a corpus model lands at
`pisces_sff/exported_flowsheets/<corpus>/SF_<SIM>_<NN>.json`. With the file in
place, the registry entry is added — `flowsheet`, `simulator`, `model_dir`,
`flowsheet_file`, `title`, `description`, and `source_corpus` are all
required — and from then on `python -m pisces_sff._regenerate_corpus`
maintains the export (with an opt-in `--stamp-reproducible` pass that costs a
second full simulation per model but earns the `reproducible` tag). A new
corpus file also gets a row in the Tier 5 outcome table
(`tests/tier5/test_corpus_validation.py`), which pins the expected validation
outcome of every committed flowsheet and fails until the new file is recorded.

Two READMEs — one under `pisces_sff/models/`, one under
`pisces_sff/exported_flowsheets/` — are generated from the registry and
committed; edit `all_models.yaml`, never the READMEs themselves. Running
`python pisces_sff/_registry.py` regenerates them, and a committed pre-commit
hook (activated once per clone with `git config core.hooksPath .githooks`)
keeps them in sync automatically, with a test guard catching any drift in CI.
Recipes and flowsheets renamed from older descriptive filenames retain their
history — `git log --follow` traces any file across its rename.

## Adding a new requirement

A new semantic requirement is catalogued in `sff_checks.md` first, under a new
check ID, before any code is written against it. Once catalogued, it's
implemented in the schema and/or in `_validate.py` as a `_check_*(ctx)`
function, registered in the `_CHECKS` list so the validator picks it up
automatically. Cataloguing before implementing keeps the check ID and the code
that enforces it traceable to each other.
