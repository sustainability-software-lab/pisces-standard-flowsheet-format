# Validation

> **Current schema version: v0.1.3** — adds the optional `metadata.tags` array
> and its companion `metadata.reproducibility.comparison_rtol`, the machine-
> verified provenance/quality tag layer documented in full in
> [Tags](#tags) below. Its predecessor v0.1.2 added the per-unit
> `purchase_cost_correlations` object; v0.1.1 loosens one constraint — the
> positive `molar_mass` check (CHEM-02) is dropped from the schema and
> re-homed in the validator as a `warning` (a backwards-compatible widening).
> The predecessor v0.1.0 was a milestone release with no shape or constraint
> changes over v0.0.12. The `v0.0.12` reference below marks when the
> declarative constraints were introduced; they remain in force unchanged
> except for CHEM-02.

SFF files are validated in two layers, both reachable from `pisces_sff`:

## Schema validation

`validate_json_against_schema(json_file, schema_file)` checks a file against the
JSON Schema (`pisces_sff/schema/sff_schema.json`) and returns
`(is_valid, [error, ...])`. This is the structural gate — types, required
properties, enums, and the declarative constraints tightened in v0.0.12
(semver `sff_version`, non-empty `TEA_currency`, 64-hex
reproducibility digests, reaction `conversion` in `[0, 1]` and
equation-or-stoichiometry, positive stream pressure, required `total_mass_flow`,
positive utility temperature/pressure). Positive `molar_mass` (CHEM-02) is no
longer a schema constraint as of v0.1.1 — it moved to the validator as a
`warning` (see below).

## Full SFF validation

`validate_flowsheet_against_SFF(json_file, schema_file=None)` runs the schema
gate **and** the semantic checks catalogued in `sff_checks.md` — referential
integrity, stream roles, per-stream material-balance self-consistency,
quantity-unit pairing and parseability, purchase-cost-correlation referential
integrity and completeness (UNIT-08, a `warning`; UNIT-09, an `error`),
TEA-year plausibility (MET-04, a
`warning`-severity check with a dynamic upper bound of the current calendar
year + 1, rather than a schema constraint), and more. It returns
`(is_valid, [CheckResult, ...])`, where each `CheckResult` has fields
`check_id, severity, status, message, path`:

- `severity` ∈ `error` | `warning` | `info` — the check's declared level.
- `status` ∈ `pass` | `fail` | `skip` — the outcome (`skip` = not applicable
  because the check's inputs are absent; never a silent pass).
- `is_valid` is `True` unless the schema gate failed or any check produced an
  `error`-severity `fail`. `warning` and `info` findings never make a file
  non-conforming.

Every check cites the `sff_checks.md` ID it implements. See that catalogue for
the authoritative statement, rationale, and tolerance of each check.

The validator also computes the **TAG-01** aggregate (declared `metadata.tags`
are actually earned) and exposes `evaluate_sff_tags(file)` to report, per tag,
whether it is earned and declared. The four tags —
`exported-from-simulator`, `extracted-from-prose`, `extracted-from-image`,
`reproducible` — and MET-07 (reproducibility digest correctness) are
documented in full in [Tags](#tags) below.

> Scope of physical checking: balance checking is limited to **local, per-stream
> self-consistency** (fraction sums, phase-to-total flow agreement, and
> mass ↔ molar-flow agreement). Cross-unit and system-wide mass closure,
> component and elemental balances, and energy-balance checking are all
> **currently out of scope**.

## Tags

A **tag** is a machine-verified assertion that a flowsheet passed a
tag-associated subset of the checks above without any `warning`-severity
findings. Tags are additive on top of conformance, never a relaxation of
it: every file must still pass every check without `error`-severity findings
regardless of its tags, and `info` findings never block a tag. Tags are
stored in the optional `metadata.tags` array (schema `enum` over the four
names below, `uniqueItems`); a tag present there that the file does not earn
is a **TAG-01** error. See `sff_checks.md` section 8 for the authoritative
statement — this section mirrors it.

There are two classes of tag:

- **Static tags** — `exported-from-simulator`, `extracted-from-prose`,
  `extracted-from-image`. Earned by running a subset of the checks above; fast,
  no simulation.
- **Harness tag** — `reproducible`. Earned by re-running the export from the
  embedded reproducibility recipe and comparing; heavy, opt-in.

### Static earning rule

A file earns static tag `T` iff all of:

1. **Conformance** — schema-valid, and no `error`-severity *fail* among the
   checks other than TAG-01 (TAG-01 is excluded from its own precondition to
   avoid circularity).
2. **Subset warning-clean** — no check in `T`'s subset produced a
   `warning`-severity *fail*.
3. **Subset skip-clean** — no check in `T`'s subset produced a *skip*, except
   skips tolerated by `T`'s policy (table below).

`info` findings and `pass` are always acceptable and never block a tag.

| Tag | Subset | Tolerated skips |
| --- | --- | --- |
| `exported-from-simulator` | **all** checks | `STR-03`, `STR-13`, `CHEM-04` always; `STR-10` when all streams are empty; `UNIT-04`/`UNIT-05`/`UNIT-06` when the flowsheet has no reactions. Every other skip (including `MET-07`) blocks. |
| `extracted-from-prose` | `{UNIT-10, STR-14}` | none (the subset checks never skip) |
| `extracted-from-image` | `{UNIT-10, STR-14}` | none |

`UNIT-10` (units present and well-identified) and `STR-14` (streams present and
identified) are the substantive requirement behind the two `extracted-from-*`
tags — a completeness floor appropriate for a flowsheet extracted from prose or
an image, where the richer numeric/referential checks the simulator subset
relies on may not apply.

### `reproducible` earning rule

Earned iff all of:

1. **Static conformance** (as above).
2. The reproducibility recipe is present and complete (`environment`,
   `load_script`, and `extended_metadata` when used) with a recorded
   `metadata.reproducibility.comparison_rtol`, and `MET-07` does not fail.
3. Reconstructing the model from the embedded recipe and re-running the export
   produces a document matching this file field-by-field within
   `comparison_rtol`.

Steps 1–2 are the cheap static precondition that `TAG-01` enforces on every
validation run. Step 3 is the heavy harness run, performed only by
`evaluate_sff_tags(run_harness=True)` / `verify_reproducible` — never by the
static `TAG-01` check.

The deep-compare in step 3 ignores the following paths (the shipped
`_REPRO_IGNORE_PATHS` in `pisces_sff/_validate.py`), because each legitimately
varies between two faithful runs or is a post-hoc annotation the exporter never
re-emits:

- `metadata.tags` — the tag claim itself is not part of what re-exporting
  reproduces.
- `metadata.reproducibility.comparison_rtol` — a post-hoc annotation of the
  tolerance being asserted, not an exported value.
- `metadata.reproducibility.resolved.exported_at`,
  `.platform`, `.python_version` — volatile per-run environment facts that
  differ by construction between the original run and the verification run.
- `metadata.reproducibility.environment.path`,
  `.load_script.path`, `.extended_metadata.path` — each recorded only when the
  *original* model directory lived under the repo (`_runner.py`'s
  `_file_record`, via `path.relative_to(REPO_ROOT)`); the verification harness
  always reconstructs the recipe into a tempdir **outside** the repo (by design,
  for isolation), so a re-export's `path` key is always absent regardless of
  whether the original had one. Without ignoring this, no in-repo model could
  ever earn `reproducible`.

Everything else — including `.content`/`.sha256` on each recipe block and
`resolved.env_key`/`resolved.package_versions` — is still compared, so the
recipe-bytes integrity guarantee holds: a `reproducible`-tagged file's embedded
recipe is provably the one that was actually re-run, and numeric leaves must
match within `comparison_rtol` (with an absolute floor near zero for
near-zero values).

### API

- **`evaluate_sff_tags(file, *, run_harness=False, rtol=None, conda_exe=None, recreate_env=False, export=None)`**
  — computes the tag verdict for an SFF file. Returns a dict
  `{tag: {"earned": bool | None, "declared": bool, "blocking": list}}` for each
  of the four tags. With the default `run_harness=False`, the three static tags
  are fully evaluated and `reproducible["earned"]` is `None` ("not evaluated")
  — fast, no simulation. With `run_harness=True`, it additionally calls
  `verify_reproducible` so `reproducible["earned"]` becomes a real `bool`; this
  path is heavy (see below).
- **`verify_reproducible(file, *, conda_exe=None, rtol=None, recreate_env=False, export=None) -> (matches, diffs)`**
  — the only path that confirms `reproducible` step 3. Reconstructs the
  embedded recipe into a temporary model directory, re-runs the export inside
  the pinned conda environment, and deep-compares the result to the original
  file per the ignore-list above. `rtol` defaults to the file's
  `metadata.reproducibility.comparison_rtol`, falling back to `1e-4` when the
  file records none. **Heavy**: it provisions/reuses a conda environment and
  simulates, under the harness export lock — never run it concurrently with
  another simulation. Returns `(matches, diffs)`, where `diffs` is a list of
  human-readable differences (empty when `matches` is `True`).

### Checks

- **MET-07 — reproducibility content matches its digests.** For every content
  block under `metadata.reproducibility` that carries both `content` and
  `sha256` (`environment`, `load_script`, `extended_metadata`), the recomputed
  SHA-256 of the `content` string's UTF-8 bytes must equal the stored `sha256`.
  `error` severity. This is the necessary precondition behind the
  `reproducible` tag — MET-06 already checks digest *shape*; MET-07 checks the
  digest is *correct*. Hashing is byte-exact (LF, no newline translation).
  Skipped when `metadata.reproducibility` is absent, or no block carries both
  `content` and `sha256`.
- **TAG-01 — declared tags are earned.** Every tag in `metadata.tags` is
  actually earned by the file: static tags are evaluated by the full static
  rule; `reproducible` is evaluated only against its cheap precondition
  statically (recipe present + `comparison_rtol` recorded + MET-07 not
  failing) — full sufficiency (step 3) is confirmed only by
  `evaluate_sff_tags(run_harness=True)`. `error` severity. Computed as a
  post-pass aggregate (like `XREF-01`): not in the ordinary `_CHECKS` list, but
  evaluated inside `validate_flowsheet_against_SFF` from the results of the
  checks that already ran, plus the tag policies above. Never runs the
  harness. Skipped when `metadata.tags` is absent or empty.
