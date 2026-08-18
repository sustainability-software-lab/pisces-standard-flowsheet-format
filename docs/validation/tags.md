# Tags

The optional `metadata.tags` array records machine-verified provenance/quality
tags. `evaluate_sff_tags(file, *, run_harness=False, ...)` computes the verdict
for every tag and returns it as a per-tag `{earned, declared, blocking}` dict.

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
