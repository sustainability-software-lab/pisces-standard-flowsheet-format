# Two layers, one module (`pisces_sff/_validate.py`)

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
