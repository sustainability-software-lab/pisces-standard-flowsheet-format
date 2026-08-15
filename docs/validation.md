# Validation

SFF files are validated in two layers, both reachable from `pisces_sff`:

## Schema validation

`validate_json_against_schema(json_file, schema_file)` checks a file against the
JSON Schema (`pisces_sff/schema/sff_schema.json`) and returns
`(is_valid, [error, ...])`. This is the structural gate — types, required
properties, enums, and the declarative constraints tightened in v0.0.12
(semver `sff_version`, non-empty `TEA_currency`, 64-hex
reproducibility digests, reaction `conversion` in `[0, 1]` and
equation-or-stoichiometry, positive stream pressure, required `total_mass_flow`,
positive molar mass, positive utility temperature/pressure).

## Full SFF validation

`validate_flowsheet_against_SFF(json_file, schema_file=None)` runs the schema
gate **and** the semantic checks catalogued in `sff_checks.md` — referential
integrity, stream roles, per-stream material-balance self-consistency,
quantity-unit pairing and parseability, TEA-year plausibility (MET-04, a
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

> Scope of physical checking: balance checking is limited to **local, per-stream
> self-consistency** (fraction sums, phase-to-total flow agreement, and
> mass ↔ molar-flow agreement). Cross-unit and system-wide mass closure,
> component and elemental balances, and energy-balance checking are all
> **currently out of scope**.
