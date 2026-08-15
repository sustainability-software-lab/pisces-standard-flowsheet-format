# SFF Checks

**Authoritative catalog of Standard Flowsheet Format (SFF) requirements and checks.**

This document is the source of truth for every requirement an SFF file must satisfy
*beyond* raw JSON-Schema shape. It exists to drive two downstream artifacts, which are
developed in a later session — **not** in the one that produced this file:

1. **Tighter constraints in `pisces_sff/schema/sff_schema.json`** — wherever a requirement
   can be expressed declaratively in JSON Schema (Draft-07).
2. **Validator helper functions in `pisces_sff/_validate.py`** — for everything the schema
   cannot express (cross-object referential integrity, graph properties, physical
   consistency). These helpers are invoked by a new entry point,
   **`validate_flowsheet_against_SFF`**, alongside the existing
   `validate_json_against_schema`.

Each check below carries a stable **ID**. Downstream schema edits and validator helpers
should cite the ID they implement, so this catalog and the code stay traceable to one
another.

> Scope note (physical checks): by deliberate decision, material-balance checking is
> limited to **local, per-stream self-consistency** (fraction sums and mass/molar-flow
> agreement). There is **no** cross-unit or system-wide total-mass closure, **no**
> component balances, and **no** elemental/atom balances across reactions. There is
> **no** energy-balance checking of any kind: an energy balance was catalogued and then
> removed (2026-08-15) because a per-unit enthalpy balance does not close against a clean
> reference flowsheet — inconsistent utility sign conventions, work duties not reflected in
> stream enthalpy, and heat-integration/reference-state effects at unit granularity. Any
> future energy check needs a model-aware design and a new ID.

---

## How to read a check

Every check is a record with these fields:

- **Statement** — the requirement, in prose.
- **Rationale** — why it matters / what breaks for a downstream consumer without it.
- **Scope** — the SFF objects and fields it touches.
- **Severity** — `error`, `warning`, or `info` (see below).
- **Skipped when** — the condition under which the check is *not applicable* because its
  required inputs are absent. A skipped check is reported explicitly as `skipped`; it is
  **never** silently treated as a pass, so missing data stays visible.
- **Enforcement** — where the check lives: `schema`, `validator`, or `schema + validator`.
  Validator entries include a suggested helper-function name as a hint for implementation.
- **Tolerance** — for numeric checks only: the comparison tolerance (see defaults below).

### Severity levels

| Level | Meaning |
| --- | --- |
| `error` | Hard violation of the SFF contract. The file is non-conforming: referential breaks, invalid enums, missing required data, impossible values. |
| `warning` | A physical or approximate discrepancy beyond tolerance, or a legal-but-suspicious construction. The file parses and cross-references resolve, but something is likely wrong. |
| `info` | Advisory only. Almost always a redundant or unused declaration that hints at an export bug but violates nothing. |

### Enforcement and the schema-change gate

Some checks are enforceable in JSON Schema. Three kinds of schema edit are **breaking or
narrowing** and therefore gated behind an explicit, announced version bump and sign-off
(see `CLAUDE.md` → "Never change without asking first" and the version protocol):

- **required-addition** — adding a property to a `required` list (invalidates files that
  omit it).
- **narrowing** — adding a `minimum`/`maximum`/`pattern`/`enum`/`oneOf` that rejects values
  previously accepted.
- **additive** — a new optional property or a new definition; safe, ungated.

Each schema-enforced check is tagged with which kind it is. `validator`-enforced checks are
never gated by the schema protocol (they touch no published shape), but they still must be
green on the corpus before shipping.

### Default tolerances

Referenced by ID from individual checks; concrete values are confirmed when the validator
is implemented.

| Tolerance | Default | Applies to |
| --- | --- | --- |
| `TOL_FRACTION` | absolute `1e-6` | fraction sums that should equal 1 |
| `TOL_FLOW` | relative `1e-3` | mass ↔ molar-flow agreement; phase-sum ↔ total-flow agreement |
| `TOL_MOLAR_MASS` | relative `1e-3` | formula-derived vs declared molar mass |
| `ZERO_FLOW` | absolute `1e-12` | treating a flow as exactly zero (empty-stream logic) |

---

## 0. Conventions & definitions

These are not checks; they define terms the checks rely on.

- **C-01 — Boundary sentinel.** In a stream, `source_unit_id` or `sink_unit_id` equal to the
  string `"None"` denotes the **system boundary**, not a reference to a unit named "None".
  A stream with `source_unit_id == "None"` enters the flowsheet from outside (a feed /
  input); one with `sink_unit_id == "None"` leaves it (a product / output).
- **C-02 — Terminology.** "units" always means **unit operations** (graph nodes).
  Measure-of-quantity information is always called **quantity units**, never "units".
- **C-03 — Quantity field.** A **quantity field** is any numeric leaf whose value is a
  physical quantity requiring a unit to interpret (e.g. `temperature`, `total_mass_flow`,
  a `design_results` value, a utility-result value, `price`). IDs, indices, counts,
  fractions, conversions, and efficiencies are **not** quantity fields.
- **C-04 — Empty stream.** A stream all of whose present flow scalars are zero (within
  `ZERO_FLOW`) and whose composition arrays are empty. Empty streams are legal (see STR-03,
  STR-13).
- **C-05 — Composition-weighted molar mass.** For a phase or stream with mole fractions
  `xᵢ` over components with molar masses `Mᵢ`, the mean molar mass is `M̄ = Σ xᵢ Mᵢ`. A
  component's molar mass is taken from `chemicals[].molar_mass` when present, else derived
  from `chemicals[].formula`.

---

## 1. metadata

### MET-01 — `sff_version` is valid semver
- **Statement:** `metadata.sff_version` matches a semantic-version pattern (`MAJOR.MINOR.PATCH`).
- **Rationale:** Consumers branch on schema version; a malformed version string cannot be
  compared or dispatched on.
- **Scope:** `metadata.sff_version`.
- **Severity:** `error`.
- **Skipped when:** never (field is required by schema).
- **Enforcement:** schema (narrowing — add `pattern`).

### MET-02 — feedstock / product stream references resolve
- **Statement:** every `metadata.feedstocks[].stream_id` and `metadata.products[].stream_id`
  equals the `id` of some stream in `streams`.
- **Rationale:** These lists are the curated entry points into the graph; a dangling
  reference makes a flowsheet's headline feeds/products unreachable.
- **Scope:** `metadata.feedstocks[]`, `metadata.products[]` → `streams[].id`.
- **Severity:** `error`.
- **Skipped when:** the respective list is absent or empty.
- **Enforcement:** validator (`_check_metadata_stream_refs`).

### MET-03 — feedstock / product roles agree with stream roles
- **Statement:** each stream named in `metadata.feedstocks` carries the `feedstock` role;
  each stream named in `metadata.products` carries the `product` role.
- **Rationale:** The two designations of "what is a feed/product" (metadata list and stream
  `roles`) must not disagree, or downstream tooling gets different answers depending on
  where it looks.
- **Scope:** `metadata.feedstocks[]`/`products[]` ↔ `streams[].roles`.
- **Severity:** `warning`.
- **Skipped when:** the referenced stream has no `roles` array (pre-v0.0.10 shape).
- **Enforcement:** validator (`_check_metadata_role_agreement`).

### MET-04 — `TEA_year` is plausible
- **Statement:** when present, `metadata.TEA_year` lies within a sane range (e.g. 1900 ≤ year
  ≤ current calendar year + 1).
- **Rationale:** A TEA year of 0 or 20000 is a data-entry error that silently corrupts every
  cost normalization downstream.
- **Scope:** `metadata.TEA_year`.
- **Severity:** `warning`.
- **Skipped when:** `TEA_year` absent.
- **Enforcement:** validator (`_check_tea_year_plausible`). The upper bound is the current
  calendar year + 1, evaluated at validation time, so it needs no annual schema bump.

### MET-05 — `TEA_currency` non-empty
- **Statement:** `metadata.TEA_currency` is a non-empty string.
- **Rationale:** All cost results are denominated in it; an empty currency makes every cost
  uninterpretable.
- **Scope:** `metadata.TEA_currency`.
- **Severity:** `error`.
- **Skipped when:** never (field is required by schema).
- **Enforcement:** schema (narrowing — add `minLength: 1`).

### MET-06 — reproducibility digests are well-formed
- **Statement:** every `sha256` field under `metadata.reproducibility` is a 64-character
  lowercase hex string when present.
- **Rationale:** These digests are the integrity check on embedded environment/load-script
  bytes; a malformed digest defeats the purpose.
- **Scope:** `metadata.reproducibility.*.sha256`, `metadata.reproducibility.resolved.env_key`.
- **Severity:** `error`.
- **Skipped when:** `reproducibility` block absent.
- **Enforcement:** schema (narrowing — add `pattern`).

---

## 2. units

### UNIT-01 — unit `id` unique
- **Statement:** all `units[].id` values are distinct.
- **Rationale:** Streams reference units by `id`; a duplicate id makes source/sink resolution
  ambiguous.
- **Scope:** `units[].id`.
- **Severity:** `error`.
- **Skipped when:** never.
- **Enforcement:** validator (`_check_unit_id_uniqueness`).

### UNIT-02 — utility-result keys reference declared utilities
- **Statement:** every key in a unit's `utility_consumption_results` and
  `utility_production_results` equals the `id` of some utility in
  `utilities.heat_utilities` / `power_utilities` / `other_utilities`.
- **Rationale:** Per-unit utility demands must point at a real utility object so their
  quantity units and prices resolve; a dangling key is an unusable, unpriceable duty. This
  is the "utilities actually being used to report unit utilities" requirement.
- **Scope:** `units[].utility_consumption_results` / `utility_production_results` keys →
  `utilities.*[].id`.
- **Severity:** `error`.
- **Skipped when:** the unit declares no utility results.
- **Enforcement:** validator (`_check_utility_result_refs`).

### UNIT-03 — design results are paired with quantity units
- **Statement:** every key in `units[].design_results` has a matching key in the same unit's
  `quantity_units_for_design_results`; conversely, `quantity_units_for_design_results` has no
  key absent from `design_results`.
- **Rationale:** A design-result value is a bare number; without its paired unit string it is
  uninterpretable, and an orphan unit entry signals a key mismatch/typo.
- **Scope:** `units[].design_results` ↔ `units[].quantity_units_for_design_results`.
- **Severity:** `error` (unit missing for a present result); `warning` (orphan unit key).
- **Skipped when:** the unit declares no `design_results`.
- **Enforcement:** validator (`_check_design_result_units_pairing`). Partly overlaps QU-01.

### UNIT-04 — reaction reactant and conversion are valid
- **Statement:** each reaction's `reactant` equals a chemical `id`, and `conversion` lies in
  [0, 1].
- **Rationale:** A reactant that names no chemical, or a conversion outside [0,1], cannot be
  applied to any stream and is physically meaningless.
- **Scope:** `units[].reactions[].reactant` → `chemicals[].id`; `units[].reactions[].conversion`.
- **Severity:** `error`.
- **Skipped when:** the reaction omits the respective field.
- **Enforcement:** schema (narrowing — `conversion` `minimum`/`maximum`) + validator
  (`_check_reaction_reactant_refs`).

### UNIT-05 — reaction specifies equation and/or stoichiometry, consistently
- **Statement:** each reaction provides **at least one** of `equation` or `stoichiometry`.
  When **both** are provided, the `equation` and the `stoichiometry` describe the **same**
  reaction (identical per-component stoichiometric coefficients, up to a common positive
  scale factor).
- **Rationale:** A reaction with neither representation cannot be applied at all. When both
  are given they are two encodings of one fact and must not contradict each other, or a
  consumer gets a different reaction depending on which field it reads.
- **Scope:** `units[].reactions[].equation`, `units[].reactions[].stoichiometry`, resolved
  against `chemicals[].id` / `chemicals[].index`.
- **Severity:** `error`.
- **Skipped when:** the consistency sub-check is skipped when only one of the two is present
  (the "at least one" sub-check still applies).
- **Enforcement:** schema (narrowing — `anyOf` requiring one of the two) + validator
  (`_check_reaction_equation_stoichiometry_consistency`).

### UNIT-06 — stoichiometry is well-formed and references valid chemicals
- **Statement:** when `stoichiometry` is an array, its length equals the number of chemicals
  and positions correspond to `chemicals[].index`; when it is an object, its keys resolve to
  chemical indices or ids. The declared `reactant` has a negative coefficient.
- **Rationale:** An array of the wrong length, or keys that resolve to no chemical, silently
  misassigns coefficients; a reactant with a non-negative coefficient contradicts its role.
- **Scope:** `units[].reactions[].stoichiometry` ↔ `chemicals[].index`/`id`;
  cross-checked with `reactant`.
- **Severity:** `error`.
- **Skipped when:** the reaction omits `stoichiometry`.
- **Enforcement:** validator (`_check_stoichiometry_wellformed`). See also CHEM-04.

### UNIT-07 — no orphan units
- **Statement:** every unit appears as the `source_unit_id` or `sink_unit_id` of at least one
  stream.
- **Rationale:** A unit connected to no stream carries no material and cannot participate in
  the process graph — almost always an export artifact.
- **Scope:** `units[].id` ↔ `streams[].source_unit_id`/`sink_unit_id`.
- **Severity:** `warning`.
- **Skipped when:** never.
- **Enforcement:** validator (`_check_unit_connectivity`).

---

## 3. streams

### STR-01 — stream `id` unique
- **Statement:** all `streams[].id` values are distinct.
- **Rationale:** Metadata feeds/products and any downstream index reference streams by `id`;
  duplicates make references ambiguous.
- **Scope:** `streams[].id`.
- **Severity:** `error`.
- **Skipped when:** never.
- **Enforcement:** validator (`_check_stream_id_uniqueness`).

### STR-02 — source/sink resolve to a unit or the boundary
- **Statement:** each `source_unit_id` and `sink_unit_id` is either an existing `units[].id`
  or the boundary sentinel `"None"` (C-01).
- **Rationale:** An endpoint that is neither a real unit nor the boundary is a broken edge in
  the process graph.
- **Scope:** `streams[].source_unit_id`/`sink_unit_id` → `units[].id` ∪ {`"None"`}.
- **Severity:** `error`.
- **Skipped when:** never (both fields required by schema).
- **Enforcement:** validator (`_check_stream_endpoint_refs`).

### STR-03 — isolated streams are empty
- **Statement:** if a stream has **both** `source_unit_id == "None"` and
  `sink_unit_id == "None"`, then all of its flow scalars are zero and all of its composition
  arrays (overall and per-phase) are empty.
- **Rationale:** A stream attached to neither a source nor a sink unit is not part of the
  connected graph; if it nonetheless carried material or energy, that material/energy would
  come from and go nowhere. Such a stream is only meaningful as an empty placeholder.
- **Scope:** `streams[]` where source and sink are both `"None"`;
  `stream_properties` flows and `phases[].composition`.
- **Severity:** `error`.
- **Skipped when:** the stream is not doubly isolated.
- **Enforcement:** validator (`_check_isolated_stream_empty`). Special case of STR-13.

### STR-04 — exactly one topology role
- **Statement:** when `roles` is present, it contains exactly one of the topology roles
  `input`, `output`, `internal`.
- **Rationale:** Topology role is a partition, not a set; zero or two topology roles make a
  stream's place in the graph ambiguous.
- **Scope:** `streams[].roles`.
- **Severity:** `error`.
- **Skipped when:** `roles` absent (pre-v0.0.10 shape).
- **Enforcement:** validator (`_check_stream_topology_role`). (JSON Schema cannot express
  "exactly one member of this subset".)

### STR-05 — topology role agrees with connectivity
- **Statement:** the topology role matches the endpoints: `source_unit_id == "None"` ⟺ role
  `input`; `sink_unit_id == "None"` ⟺ role `output`; both endpoints real units ⟺ role
  `internal`.
- **Rationale:** The stream's declared role and its actual graph position must not disagree.
- **Scope:** `streams[].roles` ↔ `source_unit_id`/`sink_unit_id`.
- **Severity:** `warning`.
- **Skipped when:** `roles` absent.
- **Enforcement:** validator (`_check_stream_role_topology_agreement`).

### STR-06 — designation roles are legal for the topology
- **Statement:** designation roles only appear where they can: `feedstock` and
  `purchased_raw_material` require the `input` topology role; `product` requires the `output`
  topology role.
- **Rationale:** A "product" that is an internal or input stream, or a "feedstock" leaving the
  system, is a mislabeling that corrupts feed/product accounting.
- **Scope:** `streams[].roles`.
- **Severity:** `warning`.
- **Skipped when:** `roles` absent or carries no designation role.
- **Enforcement:** validator (`_check_stream_designation_roles`).

### STR-07 — composition components reference chemicals
- **Statement:** every `component_name` in every phase composition equals a `chemicals[].id`.
- **Rationale:** A composition entry naming no known chemical cannot be resolved to a molar
  mass, formula, or thermo entry — it is an untyped mass fraction.
- **Scope:** `streams[].stream_properties.phases[].composition[].component_name` →
  `chemicals[].id`.
- **Severity:** `error`.
- **Skipped when:** the stream is empty (no composition entries).
- **Enforcement:** validator (`_check_composition_component_refs`).

### STR-08 — composition fractions sum to one *(material balance (i))*
- **Statement:** within each phase, mole fractions sum to 1 (within `TOL_FRACTION`); mass
  fractions sum to 1 when present. The same holds for the stream's overall composition when
  represented.
- **Rationale:** Fractions that do not sum to one indicate a truncated, double-counted, or
  mis-normalized composition.
- **Scope:** `streams[].stream_properties.phases[].composition[].mol_fraction`/`mass_fraction`.
- **Severity:** `warning`.
- **Skipped when:** the stream/phase is empty (no composition entries).
- **Enforcement:** validator (`_check_fraction_sums`).
- **Tolerance:** `TOL_FRACTION`.

### STR-09 — phase flows sum to the stream total
- **Statement:** the sum over phases of each `total_*_flow` equals the stream-level
  `total_*_flow` (within `TOL_FLOW`), for each flow quantity present at both levels.
- **Rationale:** Stream-level totals and the per-phase breakdown are two views of the same
  material and must agree.
- **Scope:** `streams[].stream_properties.total_{mass,molar,volumetric}_flow` ↔
  `phases[].total_{mass,molar,volumetric}_flow`.
- **Severity:** `warning`.
- **Skipped when:** phase-level or stream-level totals for that quantity are absent.
- **Enforcement:** validator (`_check_phase_flow_sums`).
- **Tolerance:** `TOL_FLOW`.

### STR-10 — mass flow agrees with molar flow × molar mass *(material balance (ii))*
- **Statement:** `total_mass_flow ≈ total_molar_flow × M̄`, where `M̄` is the
  composition-weighted molar mass (C-05), evaluated per stream and per phase.
- **Rationale:** Mass and molar flows are linked by composition; disagreement means the
  flows, the composition, or the molar masses are mutually inconsistent.
- **Scope:** `stream_properties.total_mass_flow`, `total_molar_flow`, phase composition, and
  `chemicals[].molar_mass`/`formula`.
- **Severity:** `warning`.
- **Skipped when:** the stream is empty; or `total_mass_flow`/`total_molar_flow` absent; or a
  molar mass cannot be resolved for some present component.
- **Enforcement:** validator (`_check_mass_molar_flow_consistency`).
- **Tolerance:** `TOL_FLOW`.

### STR-11 — pressure is positive
- **Statement:** `stream_properties.pressure > 0`.
- **Rationale:** Absolute pressure is strictly positive; a zero or negative pressure is
  unphysical.
- **Scope:** `streams[].stream_properties.pressure`.
- **Severity:** `error`.
- **Skipped when:** never (field is required by schema).
- **Enforcement:** schema (narrowing — add `exclusiveMinimum: 0`). *(temperature already
  carries `minimum: 0`.)*

### STR-12 — total mass flow is required
- **Statement:** every stream declares `stream_properties.total_mass_flow` (value 0 for empty
  streams).
- **Rationale:** Mass flow is the common denominator for balances, prices, and the STR-10
  consistency check; an absent mass flow leaves a stream only partially quantified.
- **Scope:** `streams[].stream_properties.total_mass_flow`.
- **Severity:** `error`.
- **Skipped when:** never.
- **Enforcement:** schema (required-addition — add `total_mass_flow` to
  `stream_properties.required`; **gated**, breaking).

### STR-13 — zero-flow streams are fully empty
- **Statement:** empty streams are allowed, but if **any** present flow scalar of a stream is
  zero (within `ZERO_FLOW`), then **all** of its flow scalars are zero and all of its
  composition arrays are empty.
- **Rationale:** A stream with, say, zero mass flow but a nonzero molar flow or a populated
  composition is internally contradictory — there is no material, yet material is described.
- **Scope:** `stream_properties.total_{mass,molar,volumetric}_flow` and
  `phases[].composition` (and per-phase flows analogously).
- **Severity:** `error`.
- **Skipped when:** all present flow scalars are nonzero.
- **Enforcement:** validator (`_check_zero_flow_consistency`). STR-03 is the doubly-isolated
  special case.

---

## 4. chemicals

### CHEM-01 — chemical `id` and `index` unique
- **Statement:** all `chemicals[].id` are distinct; all `chemicals[].index` values are
  distinct among chemicals that declare one.
- **Rationale:** Compositions reference chemicals by `id` and array-form stoichiometry by
  `index`; a duplicate on either makes the reference ambiguous.
- **Scope:** `chemicals[].id`, `chemicals[].index`.
- **Severity:** `error`.
- **Skipped when:** the `index` sub-check applies only to chemicals declaring `index`.
- **Enforcement:** validator (`_check_chemical_id_index_uniqueness`).

### CHEM-02 — molar mass is positive
- **Statement:** `chemicals[].molar_mass > 0` when present.
- **Rationale:** A non-positive molar mass is unphysical and poisons every mass↔molar
  conversion (STR-10) that uses it.
- **Scope:** `chemicals[].molar_mass`.
- **Severity:** `warning`.
- **Skipped when:** `molar_mass` absent.
- **Enforcement:** schema (narrowing — add `exclusiveMinimum: 0`).
- **Note (2026-08-15):** enforced via the schema (`exclusiveMinimum: 0`), so in practice a
  non-positive molar mass fails the schema gate and makes the file non-conforming — effectively
  `error`-gated despite the `warning` label above. Kept schema-enforced by decision: a
  non-positive molar mass is unphysical, not merely suspicious.

### CHEM-03 — formula agrees with declared molar mass
- **Statement:** when both `formula` and `molar_mass` are present, the molar mass computed
  from the formula equals the declared `molar_mass` (within `TOL_MOLAR_MASS`).
- **Rationale:** Formula and molar mass are redundant encodings of the same fact;
  disagreement signals a wrong formula or a wrong mass.
- **Scope:** `chemicals[].formula`, `chemicals[].molar_mass`.
- **Severity:** `warning`.
- **Skipped when:** either field absent, or the formula cannot be parsed to a molar mass.
- **Enforcement:** validator (`_check_formula_molar_mass_agreement`).
- **Tolerance:** `TOL_MOLAR_MASS`.

### CHEM-04 — index coverage when index-based stoichiometry is used
- **Statement:** if any reaction uses index-based (array or index-keyed) stoichiometry, then
  every chemical declares an `index`, and the indices form a consistent set matching the
  stoichiometry ordering.
- **Rationale:** Array-position and index-keyed stoichiometry are only interpretable if the
  chemical→index mapping is total and consistent.
- **Scope:** `chemicals[].index` ↔ `units[].reactions[].stoichiometry`.
- **Severity:** `error`.
- **Skipped when:** no reaction uses index-based stoichiometry.
- **Enforcement:** validator (`_check_index_coverage`). Pairs with UNIT-06.

### CHEM-05 — no unused chemicals
- **Statement:** every chemical is referenced by at least one stream/utility composition or
  reaction.
- **Rationale:** A chemical referenced nowhere is dead weight in the registry — usually an
  over-broad export.
- **Scope:** `chemicals[].id` ↔ all `composition[].component_name` and reaction references.
- **Severity:** `info`.
- **Skipped when:** never.
- **Enforcement:** validator (`_check_unused_chemicals`).

---

## 5. utilities

### UTIL-01 — utility `id` unique across all groups
- **Statement:** all utility `id` values are distinct across `heat_utilities`,
  `power_utilities`, and `other_utilities` combined.
- **Rationale:** Units reference utilities by a single flat `id` namespace; a collision
  between, say, a heat and a power utility makes the reference ambiguous.
- **Scope:** `utilities.{heat,power,other}_utilities[].id`.
- **Severity:** `error`.
- **Skipped when:** never.
- **Enforcement:** validator (`_check_utility_id_uniqueness`).

### UTIL-02 — no unused utilities
- **Statement:** every declared utility is referenced by at least one unit's
  `utility_consumption_results` or `utility_production_results`.
- **Rationale:** A utility that no unit uses is a dangling declaration — the reverse of
  UNIT-02 and usually an export artifact.
- **Scope:** `utilities.*[].id` ↔ `units[].utility_*_results` keys.
- **Severity:** `info`.
- **Skipped when:** never.
- **Enforcement:** validator (`_check_unused_utilities`).

### UTIL-03 — utility-result quantity units are parseable
- **Statement:** each utility's `quantity_units_for_utility_results` is a non-empty,
  parseable unit string.
- **Rationale:** The per-unit-operation utility values are bare numbers interpreted through
  this string; an empty or unparseable unit makes every referencing duty uninterpretable.
- **Scope:** `utilities.*[].quantity_units_for_utility_results`.
- **Severity:** `warning`.
- **Skipped when:** never (field required by schema for each group).
- **Enforcement:** validator (`_check_utility_result_units_parseable`). See QU-02.

### UTIL-04 — utility composition is valid
- **Statement:** for heat and other utilities, every composition `component_name` references a
  chemical `id`, and mole fractions sum to 1 (within `TOL_FRACTION`).
- **Rationale:** A utility stream's composition is subject to the same referential and
  normalization requirements as a process stream.
- **Scope:** `utilities.{heat,other}_utilities[].composition`.
- **Severity:** `error` (component ref); `warning` (fraction sum).
- **Skipped when:** the composition array is empty/absent.
- **Enforcement:** validator (`_check_utility_composition`).
- **Tolerance:** `TOL_FRACTION`.

### UTIL-05 — utility temperature and pressure are positive
- **Statement:** for heat and other utilities, `temperature > 0` and `pressure > 0`.
- **Rationale:** Absolute temperature and pressure are strictly positive; zero/negative
  values are unphysical.
- **Scope:** `utilities.{heat,other}_utilities[].temperature`/`pressure`.
- **Severity:** `error`.
- **Skipped when:** never (both required by schema for these groups).
- **Enforcement:** schema (narrowing — add `exclusiveMinimum: 0`).

---

## 6. quantity_units

### QU-01 — every quantity field is paired with a resolvable quantity unit
- **Statement:** every quantity field (C-03) in the flowsheet resolves to exactly one
  declared quantity-unit string — either through a `quantity_units_global` alias, or through
  a per-object map (`quantity_units_for_design_results`, `quantity_units_for_utility_results`).
- **Rationale:** SFF quantities are bare numbers; a quantity with no resolvable unit is
  uninterpretable. This is the "all quantities actually paired with quantity units"
  requirement.
- **Scope:** all quantity fields ↔ `quantity_units_global` aliases and per-object unit maps.
- **Severity:** `error`.
- **Skipped when:** never.
- **Enforcement:** validator (`_check_quantity_unit_pairing`).

### QU-02 — quantity-unit strings are parseable
- **Statement:** every quantity-unit string — in `quantity_units_global[].quantity_units`,
  `quantity_units_for_design_results` values, and `quantity_units_for_utility_results` — is
  non-empty and parseable by the unit system. (The sentinel `""` for an explicitly
  dimensionless design result is permitted where the schema already documents it.)
- **Rationale:** An unparseable unit string cannot be converted or compared and breaks any
  consumer that does unit math.
- **Scope:** all quantity-unit strings across the flowsheet.
- **Severity:** `error`.
- **Skipped when:** never.
- **Enforcement:** validator (`_check_quantity_unit_strings_parseable`).

### QU-03 — aliases are globally unambiguous
- **Statement:** no field-name alias appears under more than one `quantity_units_global`
  entry.
- **Rationale:** An alias maps a field name to a quantity's units; if the same field name
  maps to two quantities, resolution (QU-01) is ambiguous.
- **Scope:** `quantity_units_global[].aliases`.
- **Severity:** `error`.
- **Skipped when:** never.
- **Enforcement:** validator (`_check_alias_uniqueness`).

### QU-04 — no unused aliases
- **Statement:** every `quantity_units_global` **entry** is used — i.e. at least one of its
  `aliases` appears as a quantity field name somewhere in the flowsheet. (An entry
  deliberately carries synonym aliases, e.g. `mass_flow`/`total_mass_flow`/`F_mass`; only
  whole-entry disuse is flagged.)
- **Rationale:** An alias that names no present field is a stale registry entry — the
  "quantity units actually being used" requirement, in its reverse direction.
- **Scope:** `quantity_units_global[].aliases` ↔ quantity field names in use.
- **Severity:** `info`.
- **Skipped when:** never.
- **Enforcement:** validator (`_check_unused_aliases`).

---

## 7. cross-object

### XREF-01 — referential-integrity gate
- **Statement:** an umbrella statement that all cross-references resolve: streams↔units
  (STR-02), composition↔chemicals (STR-07, UTIL-04), utility-results↔utilities (UNIT-02),
  reactions↔chemicals (UNIT-04, UNIT-06, CHEM-04), and metadata↔streams (MET-02).
- **Rationale:** Referential integrity is the backbone of the directed-graph model; this
  entry names the whole class so a consumer can gate on "all references resolve" as one
  condition.
- **Scope:** all inter-object id references.
- **Severity:** `error` (aggregate of its constituent `error` checks).
- **Skipped when:** never.
- **Enforcement:** validator (composed of the constituent helpers above).

### GRAPH-01 — the flowsheet has a boundary in and a boundary out
- **Statement:** at least one stream enters from the boundary (`source_unit_id == "None"`)
  and at least one leaves to the boundary (`sink_unit_id == "None"`).
- **Rationale:** A process with no feed or no product is not a producing flowsheet; almost
  always a truncated export.
- **Scope:** `streams[].source_unit_id`/`sink_unit_id`.
- **Severity:** `warning`.
- **Skipped when:** never.
- **Enforcement:** validator (`_check_boundary_streams_exist`).

---

## Appendix — check index

| ID | Statement (short) | Severity | Enforcement |
| --- | --- | --- | --- |
| MET-01 | `sff_version` valid semver | error | schema (narrowing) |
| MET-02 | feed/product stream refs resolve | error | validator |
| MET-03 | feed/product roles agree with stream roles | warning | validator |
| MET-04 | `TEA_year` plausible | warning | validator |
| MET-05 | `TEA_currency` non-empty | error | schema (narrowing) |
| MET-06 | reproducibility digests well-formed | error | schema (narrowing) |
| UNIT-01 | unit `id` unique | error | validator |
| UNIT-02 | utility-result keys → declared utilities | error | validator |
| UNIT-03 | design results paired with quantity units | error/warning | validator |
| UNIT-04 | reaction reactant/conversion valid | error | schema + validator |
| UNIT-05 | reaction has ≥1 of equation/stoichiometry, consistent | error | schema + validator |
| UNIT-06 | stoichiometry well-formed | error | validator |
| UNIT-07 | no orphan units | warning | validator |
| STR-01 | stream `id` unique | error | validator |
| STR-02 | source/sink resolve to unit or boundary | error | validator |
| STR-03 | isolated streams are empty | error | validator |
| STR-04 | exactly one topology role | error | validator |
| STR-05 | topology role agrees with connectivity | warning | validator |
| STR-06 | designation roles legal for topology | warning | validator |
| STR-07 | composition components → chemicals | error | validator |
| STR-08 | composition fractions sum to 1 | warning | validator |
| STR-09 | phase flows sum to stream total | warning | validator |
| STR-10 | mass flow ≈ molar flow × molar mass | warning | validator |
| STR-11 | pressure positive | error | schema (narrowing) |
| STR-12 | `total_mass_flow` required | error | schema (required-addition, gated) |
| STR-13 | zero-flow streams fully empty | error | validator |
| CHEM-01 | chemical `id`/`index` unique | error | validator |
| CHEM-02 | molar mass positive | warning | schema (narrowing) |
| CHEM-03 | formula agrees with molar mass | warning | validator |
| CHEM-04 | index coverage for index-based stoichiometry | error | validator |
| CHEM-05 | no unused chemicals | info | validator |
| UTIL-01 | utility `id` unique across groups | error | validator |
| UTIL-02 | no unused utilities | info | validator |
| UTIL-03 | utility-result quantity units parseable | warning | validator |
| UTIL-04 | utility composition valid | error/warning | validator |
| UTIL-05 | utility temperature/pressure positive | error | schema (narrowing) |
| QU-01 | every quantity field paired with a unit | error | validator |
| QU-02 | quantity-unit strings parseable | error | validator |
| QU-03 | aliases globally unambiguous | error | validator |
| QU-04 | no unused aliases | info | validator |
| XREF-01 | referential-integrity gate | error | validator |
| GRAPH-01 | boundary in and boundary out exist | warning | validator |
