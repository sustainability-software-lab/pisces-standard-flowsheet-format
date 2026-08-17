# Validation

> **Current schema version: v0.1.1** — loosens one constraint: the positive
> `molar_mass` check (CHEM-02) is dropped from the schema and re-homed in the
> validator as a `warning` (a backwards-compatible widening). The predecessor
> v0.1.0 was a milestone release with no shape or constraint changes over
> v0.0.12. The `v0.0.12` reference below marks when the declarative constraints
> were introduced; they remain in force unchanged except for CHEM-02.

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
documented in full below.

> Scope of physical checking: balance checking is limited to **local, per-stream
> self-consistency** (fraction sums, phase-to-total flow agreement, and
> mass ↔ molar-flow agreement). Cross-unit and system-wide mass closure,
> component and elemental balances, and energy-balance checking are all
> **currently out of scope**.
