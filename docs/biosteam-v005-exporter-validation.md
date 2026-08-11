# BioSTEAM v0.0.5 exporter validation record

This document holds the technical evidence for pull request #9.

## Why this exists

Project PISCES needs to regenerate 32 public BioSTEAM flowsheets in SFF v0.0.5. Fourteen older files have no chemicals catalog or molecular weights, so the website cannot reliably convert mole fractions to mass fractions.

On `main`, the canonical v0.0.5 exporter labels its output as v0.0.3. Full-corpus testing also shows that some real BioSTEAM values can produce invalid JSON or unstable reaction output. This PR fixes those boundaries so an export is valid and repeatable or fails with a useful exception.

## What the PR changes

`CURRENT_SFF_VERSION` is now the single version authority for both the package and exported metadata. This lets us update the version in one place when the standard advances.

The exporter also:

- converts NumPy and `deque` values to ordinary JSON values;
- omits undefined optional numbers and rejects any remaining `NaN` or infinity;
- raises exceptions instead of opening an interactive debugger;
- emits each top-level reaction once, in unit attribute order.

`get_composition` is unchanged from `main`. See the composition section below.

## What development testing found

| Evidence | Finding and result |
| --- | --- |
| `bffccf70` | The v0.0.5 entry point stamped `0.0.3`. The first regression test caught the mismatch. |
| `22220e99` | Corpus testing found NumPy values at the JSON boundary; the exporter now converts them. |
| `f57d3296` | The cellulosic acTAG model exposed a `collections.deque`; the exporter now converts it to a list. |
| Build `e3158dd6` | All 32 models ran, but strict parsing found two `NaN` vessel-cost values in `bfg_oleochemical`. |
| `22914bab`, build `e2af8f33` | The exporter began omitting undefined optional numbers and rejecting non-standard JSON. The next 32-model run passed. |
| `a786c311` | Comparing successful runs exposed hash-order-dependent reaction output. Reaction discovery now preserves attribute order. |
| Build `e8994df8-b48b-4875-8533-d8b4bc2e5b58` | The final exporter-code run completed 32/32 models with zero failures. Every file passed the v0.0.5 schema, strict JSON parsing, and positive finite molecular-weight checks. |

## Composition fractions: reverted, and why

An earlier revision of this PR (`05f6c4b2`) normalized each phase over its positive
components instead of the phase total, so that the emitted fractions summed to 1. It was a
response to schema failures where a fraction came out fractionally above `1`, which happens
when negative solver residue shrinks the phase total below the sum of the positive
components.

That change is reverted. The exporter reports what the solver converged on, and does not
rescale a component set to make it sum to 1. Rescaling would restate a converged number and
could propagate an inconsistency through the material and energy balance downstream. A
negative flow is still omitted from the emitted list, since a negative fraction is not
expressible, but it stays in the denominator where the solver put it.
`test_composition_reports_converged_fractions_without_rescaling` pins that convention.

The overshoot this originally addressed is at the last bit of a double. Measured across the
32-model corpus produced by this exporter, where the reported `total_mass_flow` and
`total_molar_flow` still include any negative residue:

- of the 1,016 pure single-component streams, which are the only population that can emit a
  fraction above `1`, 962 (94.7%) show exactly zero deviation between
  `total_mass_flow / total_molar_flow` and the component's molar mass, and the worst is
  1.97e-16, with none above 1e-15;
- across all 4,084 single-phase streams, `total_mass_flow / total_molar_flow` matches
  `sum(mol_fraction * molar_mass)` with a median of 0 and a maximum of 3.9e-16;
- all 87 multi-phase streams fall inside their per-phase molar-mass bracket.

So both conventions reconstruct component flows identically in double precision on this
corpus, and the failure they differ on is a value such as `1.0000000000000002` meeting a
hard `maximum: 1` bound. The tolerance therefore belongs at the bound, in
`pisces_sff/schema/sff_schema.json` and `pisces_sff/_validate.py`, rather than in the
exporter. Keeping that tolerance tight, on the order of `1e-9` rather than a decimal-place
rounding, preserves the check's ability to fail on a residue that is genuinely material.

## Current proof

Six dependency-isolated tests cover the central version authority, metadata stamp,
composition convention, JSON conversion, finite-number enforcement, and reaction ordering.
Four more cover release consistency, for ten in total.

Build `e8994df8-b48b-4875-8533-d8b4bc2e5b58` tests the exporter behavior at `a786c311`. The later version-authority refactor changes where the same `0.0.5` value comes from and is covered by the sixth unit test.

Project PISCES frontend pull request [#4671](https://github.com/sustainability-software-lab/project-pisces-frontend/pull/4671) already landed the regenerated corpus, schema support, data migration, and website regression tests, pinned to exporter commit `abb865e`. Those 32 files carry fractions that sum to 1 and are schema-valid under either convention, so the revert here does not change any committed file and needs no re-export. A future re-export at this PR's head would produce the converged values instead, which is when the validator tolerance above becomes load-bearing.

## Review focus

1. Does one constant control both package and export versions?
2. Does `get_composition` still match `main`, and does the test pin the converged convention?
3. Does the JSON boundary convert known containers and reject unknown ones?
4. Are undefined optional numbers omitted while other non-finite values fail?
5. Does reaction discovery preserve order and remove only nested duplicates?
