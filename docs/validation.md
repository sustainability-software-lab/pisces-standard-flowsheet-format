# Validation & Checks

SFF files are validated in two layers, both housed in a single module,
`pisces_sff/_validate.py`.

## Two layers, one module (`pisces_sff/_validate.py`)

`validate_json_against_schema(json_file, schema_file) -> (is_valid, errors)`
is the structural gate: it checks a file against the JSON Schema
(draft-07) shipped at `pisces_sff/schema/sff_schema.json` and returns whether
the file is valid, along with a list of human-readable errors.

`validate_flowsheet_against_SFF(json_file, schema_file=None) -> (is_valid,
[CheckResult, ...])` runs that same schema gate and then every semantic check
in the catalogue. Each outcome is reported as a `CheckResult`, a namedtuple of
`(check_id, severity, status, message, path)`: `severity` is one of `error`,
`warning`, or `info` — the check's own declared level — and `status` is one
of `pass`, `fail`, or `skip` — what actually happened when the check ran
against this file.

`is_valid` is `False` only if the schema gate failed, or some check produced
an `error`-severity `fail`; a `warning` or `info` finding never makes a file
non-conforming. A `skip` is not a silent pass — it means the check's inputs
were absent from the file, so there was nothing for the check to evaluate.

## The catalogue (`sff_checks.md`)

Every requirement beyond raw schema shape — referential integrity, unit and
stream completeness, reproducibility integrity, and more — is catalogued in
`sff_checks.md` with a stable ID that both the schema and the validator cite,
for example `MET-01`, `MET-05`, `MET-06`, `STR-11`, `STR-12`, `UNIT-04`, and
`UNIT-09`.

The tag layer described below is backed by its own catalogued checks:
`MET-07` confirms that a flowsheet's embedded reproducibility content matches
its recorded digests; `UNIT-10` confirms that units are present and
well-identified, `STR-14` confirms that streams are present and identified;
and `TAG-01` is the aggregate
check that reports an `error` when a file declares a tag in `metadata.tags`
that it has not actually earned.

## Tags (0.1.3)

As of schema v0.1.3, the optional `metadata.tags` array records
machine-verified provenance/quality tags. `evaluate_sff_tags(file, *,
run_harness=False, ...)` computes the verdict for every tag and returns it as
a per-tag `{earned, declared, blocking}` dict.

Three tags are **static**: `exported-from-simulator`, `extracted-from-prose`,
and `extracted-from-image`. A file earns a static tag by passing a
tag-specific subset of the catalogue's checks with no `warning`-severity
finding — fast, and requiring no simulation.

The fourth tag, `reproducible`, is a **harness** tag. It is earned only via
`verify_reproducible(file, *, rtol=None, ...) -> (matches, diffs)`, which
reconstructs the flowsheet's embedded reproducibility recipe, re-runs the
export inside the pinned conda environment, and deep-compares the result
against the original file. Because that path means provisioning an
environment and re-running a simulation, it is heavy and opt-in rather than
something every validation run performs.
