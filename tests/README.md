# Test Suite Organization

This guide describes the six-tier test suite and where your new test belongs.

## Six Tiers, One Sentence Each

- **Tier 1** — every helper function, with **fake** objects. Import-light.
- **Tier 2** — every helper function **except** those in `_harness.py` and `_runner.py`, with **real** biosteam/thermosteam objects (no fake stubs).
- **Tier 3** — every SFF requirement that `sff_checks.md` marks as **schema**-enforced is actually enforced by the schema.
- **Tier 4** — every SFF requirement that `sff_checks.md` marks as **validator**-enforced is actually enforced by `validate_flowsheet_against_SFF`.
- **Tier 5** — validate **all** exported flowsheets in `exported_flowsheets/`.
- **Tier 6** *(run when a change affects the exporter or how exported flowsheets are validated)* — re-export **all** models via the **full harness**, then re-validate each and cross-check numeric/other values against the live biosteam objects **and** stored baselines.

## Gating

| Tier | Default | Disable with | Heavy? |
| --- | --- | --- | --- |
| 1 | on | `SFF_TEST_TIER1=0` | no |
| 2 | on | `SFF_TEST_TIER2=0` | yes (biosteam objects) |
| 3 | on | `SFF_TEST_TIER3=0` | no |
| 4 | on | `SFF_TEST_TIER4=0` | no (may lazily import thermosteam for unit parsing) |
| 5 | on | `SFF_TEST_TIER5=0` | no |
| 6 | on | `SFF_TEST_TIER6=0` | yes (full sim + conda env) |

**Documented fast path** (routine schema/validator/docs work — disables the two simulating tiers):

```
SFF_TEST_TIER2=0 SFF_TEST_TIER6=0 pytest tests -q
```

**Full run** (exporter changes):

```
pytest tests -q
```

## Shared Fixtures

All tiers can use these fixture modules under `tests/`:

- `_gating.py` — `tier_enabled(n)`, `skip_if_disabled(n)`, `RUN_TIER1…RUN_TIER6` booleans.
- `_fakes.py` — fake biosteam/thermosteam objects for Tier 1 (moved from `tier1/_export_stub.py`).
- `_real_objects.py` — cached real biosteam/thermosteam builders for Tier 2 (moved from `tier2/_small_system.py`).
- `_docs.py` — `valid_doc()` minimal-valid SFF dictionary, plus `mutate(doc, path, value)` and `remove(doc, path)` nested-path mutators for Tiers 3/4.
- `_validate_loader.py` — `load_validate_module()` to load `_validate.py` by file path without importing `pisces_sff` (import-light discipline).

## Where a New Test Goes

- **New helper function** → Tier 1 (fake) **and** Tier 2 (real), unless it lives in `_harness.py`/`_runner.py` (Tier 1 only).
- **New schema constraint** → Tier 3 (reject + accept).
- **New validator check** → Tier 4 (end-to-end) **and** register it in `_CHECKS`.
- **New corpus file** → add to the Tier 5 outcome table.
- **New model recipe** → Tier 6 (baseline + consistency).

## Docstring Rule

Every test method carries a docstring stating *what it exercises* **and its expected output**. Example:

```python
"""STR-01 — two streams share an id → validate_flowsheet_against_SFF returns CheckResult(STR-01, error, fail); is_valid is False."""
```

## Meta-Tests (Coverage Guards)

Two forward-looking meta-tests keep the suite honest and drive coverage completeness:

**`tests/tier1/test_coverage_meta.py`** (Tier 1 coverage, built in Phase 3)

Enumerates every module-level helper in `pisces_sff/*.py` (via `ast`, import-light) and asserts each one is named in some `tests/tier1/test_*.py` source. Exempts public entry points covered elsewhere or untested trivial wrappers (each exemption carries a one-line reason). Failing this test means a new helper lacks a Tier 1 test.

**`tests/tier4/test_coverage_meta.py`** (Tier 4 coverage, built in Phase 5)

Parses the validator-enforced ID set from the `sff_checks.md` Appendix table and asserts each ID appears in some `tests/tier4/test_*.py` source. Failing this test means a newly-catalogued validator check lacks a Tier 4 end-to-end test.
