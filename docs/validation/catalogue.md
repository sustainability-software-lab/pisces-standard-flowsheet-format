# The validation catalogue

Every requirement beyond raw schema shape — referential integrity, unit and
stream completeness, reproducibility integrity, and more — is catalogued in
[`sff_checks.md`](https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/sff_checks.md)
with a stable ID that both the schema and the validator cite, for example
`MET-01`, `MET-05`, `MET-06`, `STR-11`, `STR-12`, `UNIT-04`, and `UNIT-09`.

The [tag layer](tags.md) is backed by its own catalogued checks:
`MET-07` confirms that a flowsheet's embedded reproducibility content matches
its recorded digests; `UNIT-10` confirms that units are present and
well-identified, `STR-14` confirms that streams are present and identified;
and `TAG-01` is the aggregate
check that reports an `error` when a file declares a tag in `metadata.tags`
that it has not actually earned.
