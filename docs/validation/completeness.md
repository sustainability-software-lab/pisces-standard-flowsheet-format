# Completeness scoring

Validation asks whether an SFF file is well-formed and internally consistent.
The **completeness scorer** asks a different question: *how much of the
information BioSTEAM needs to rebuild this flowsheet does the file actually
state?* It lives in `pisces_sff/completeness/_completeness.py`, and its criteria
are catalogued in
[`criteria.yaml`](https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/pisces_sff/completeness/criteria.yaml)
beside it.

## Purpose

The target is **BioSTEAM re-import readiness**: could a reader rebuild an
equivalent `biosteam.System` from this file alone, one that can be simulated,
costed and put through a techno-economic analysis (TEA)? The scorer judges this
*declaratively*, from the SFF data. It never runs a simulation.

[Project PISCES](https://projectpisces.org/) extracts flowsheets from the
literature with an LLM pipeline, and many extracted files are valid but sparse.
The completeness score ranks such files, and the per-criterion list of missing
information shows what an extraction (or an exporter) left out. The score is
meaningful across the whole range of inputs: sparse extracted files with no
stream states or chemicals, full simulator exports, and legacy (<=0.0.6,
inline `{value, units}`) corpus files.

## How it differs from validation

|                        | Validation                                   | Completeness                                        |
| ---------------------- | -------------------------------------------- | --------------------------------------------------- |
| Question               | Does the file conform to the format?         | How much re-import information does it carry?       |
| Result                 | `is_valid` plus one `CheckResult` per check  | a score in [0, 1] plus one `CriterionResult` per criterion |
| Absent optional data   | acceptable (the check skips)                 | lowers the score wherever the flowsheet needs it   |
| Entry point            | `validate_flowsheet_against_SFF`             | `score_flowsheet_completeness`                      |

The two are independent. A valid file can be nearly empty, and an information-rich
file can fail validation. The scorer **reuses the validator** rather than
duplicating it. It first runs the full two-layer validator in memory (schema
gate, every semantic check, `XREF-01` and `TAG-01`), then reports `is_valid` and
a count of the validator results by severity and status (`validation_summary`)
beside the score. Three **integrity** criteria are read straight off the
validator's verdict:

- `CMP-INT-01` scores the schema gate (`SCHEMA`).
- `CMP-INT-02` scores the referential-integrity aggregate (`XREF-01`).
- `CMP-INT-04` scores `QU-01` and `QU-02`, half each: every quantity has
  declared units, and every unit string parses. For a legacy inline-shape
  file, it checks the units carried by each inline quantity.

The separate recipe route (below) uses `MET-07`. The pass fraction of the semantic
checks is deliberately *not* scored. Deleting the data a failing check looks at
turns the fail into a skip, so scoring that fraction would reward deleting
information.

## The scoring model

### Criteria, weights and categories

Each criterion in `criteria.yaml` has an id (`CMP-<AREA>-<NN>`), a category, a
weight in points, a criticality, the first SFF version that can state it
(`representable_since`), a title, a rationale, and the SFF paths it reads. Each
id maps to one pure scoring function. That function returns a score in [0, 1]
(or *not applicable*), a message, and a list of **missing locators**, such as
`streams[feed].stream_properties.temperature` or
`units[F1].design_input_specs: one of T|V|H`, which name exactly what is absent.

The **overall score** is the weighted mean over every scored criterion,
`sum(weight * score) / sum(weight)`. A category or criticality score is the same
weighted mean restricted to that group, and is `None` when nothing in the group
was scored. Many criteria average over a population, such as units, feeds,
chemicals or reactions. Per-unit averages are **unweighted**: each unit counts
once. A weight read from the unit's family would come from `unit_type` or
`design_simulation_method`, and deleting either would then move the unit's
weight and could raise a criterion whose per-unit score does not depend on the
family (deleting a costed pump's `unit_type` once raised `CMP-COST-01`). Major
equipment keeps its emphasis through its richer requirement sets: a reactor or
column has more to state, so more ways to lose credit.

### Criticality classes

- **essential**: needed to simulate at all (chemicals, feeds, unit classes and
  operating specifications, reactions).
- **costing**: needed to cost the equipment and run the TEA.
- **supplementary**: raises fidelity or enables verification, such as computed
  stream states, utility results and provenance.
- **integrity**: derived from the validator's verdict on the file.

### Applicability follows need, never data

A criterion is *not applicable* only when the flowsheet has no **need** for
what it measures, as judged from structure: the units and their families, the
streams, the referenced chemicals and the reactive units. It is never marked
not applicable just because the data are absent. The reaction criteria, for
example, do not apply to a flowsheet with no reactive units. A flowsheet with
streams but no chemicals, however, still scores 0 on the chemical criteria.
Inside a criterion's population, missing data scores 0. Not-applicable criteria
are left out of every weighted mean.

A unit whose family is unknown (no class, no `unit_type` keyword family, or a
claim its ports or stream physics contradict) could be anything, so it carries **every**
family-dependent need: it joins the reactive population of the reaction
criteria (scoring 0 unless it declares a well-formed reaction), it makes both
the heat-agent and the power-utility needs present, it is expected to record
utility results, it is costed, and it is held to the species-consistency guard
of a physical-only unit. A need is waived only on affirmative evidence, so
deleting a reactor's only family evidence cannot shed its reaction or utility
needs.

A family claim must also be plausible for the unit's ports. Besides the port
counts of each core class and the family conventions (a mixer has one outlet;
a pump, valve or utility exchanger one inlet and one outlet), a **mixer family
claimed without an exact class** (a `unit_type` of "Mixer") needs at least two
inlets that are not stated empty (a stream of unknown flow counts). A one-inlet
"mixer" mixes nothing and would otherwise shed the needs of the reactor or
exchanger it hides; relabelling such a unit "Mixer" now scores exactly like
deleting its `unit_type`. An exactly named core `Mixer` keeps its class on any
inlet count (BioSTEAM mixers often have a single non-empty inlet beside empty
ones).

Claims that **remove** needs must also be physically plausible, since a mixer
or a splitter is cost-free and needs no reactions, heat or power. A mixer claim
(an exact class such as `Mixer` or a "Mixer" label) is implausible when an
outlet is more than 1 K hotter than the hottest inlet or more than 1 % above the
highest inlet pressure. These are upper bounds only, because a mixer may cool
and mixes at the lowest inlet pressure. A pure-splitter claim (`Splitter`,
`PhaseSplitter`, `MockSplitter`/`FakeSplitter`, or a splitter label without an
exact class) is implausible when an outlet's temperature or pressure differs
from the inlet's by more than 1 K or 1 %. Either claim is also implausible when
a species appears or vanishes across the unit (flows computable, no
well-formed reaction declared) or when the unit records a non-zero heat or
power duty. The `@cost`-decorated `Mixer` subclasses, whose cost item may
carry agitator power, may record power. An implausible claim counts as absent,
so relabelling a pump, a heater or a fermenter "Mixer" leaves its family
unknown instead of shedding its needs. Streams stated empty are ignored, and
only stated values are compared, so deleting data never creates a
contradiction. Solids separators, centrifuges and other splitter classes that
are not pure splitters are not tested. Legacy corpus files hold `MockSplitter` units whose outlets differ from their inlet:
the class does nothing when simulated, so its stated outlets are not a split
of its inlet, and those units score as units of unknown family.

### Stated, derived, missing

A value the file states, and that passes the validity checks below, earns full
credit. Information the file does not state but that other fields imply earns
**derived credit** (`DERIVED_CREDIT = 0.75`). For example, a utility heat
exchanger's outlet temperature can be read off its outlet stream, and a
reaction's mass or molar basis follows from its mass balance. A derivation from
stream data counts only when the unit's mass balance closes within 1 %
(for a physical unit, every species' balance must close too). Species flows are
compared on the molar basis when every port of the unit states per-phase molar
flows and mol fractions, and otherwise on the mass basis when every port states
per-phase mass flows and mass fractions. A file written on the mass basis
therefore scores like its molar twin. The report says whether a species check
found species appearing or vanishing ("inconsistent") or could not run
because flows were not computable ("unverified"). A port-order credit that rests
on a unit side's stream count needs only the unit's total mass balance to
close, which alone shows no material-carrying port is missing. Chemical identity
has its own tiers, and each chemical scores the best tier it reaches:

- a registry id (CAS, InChI, InChIKey or SMILES) that the `chemicals` database
  resolves, to a record that agrees with the chemical's molar mass or formula,
  earns 1;
- a name that resolves the way thermosteam's `Chemical(ID)` resolves it, with
  its molar mass verified, earns 0.75. The lookup mirrors thermosteam's: its
  database search (`_` read as a space, CamelCase split into words, so
  `AceticAcid` finds acetic acid), then its built-in bioprocess
  pseudo-components (`Cellulose`, `Starch`, `Fiber`, `Yeast`, `Ash`, ...,
  kept as a static table so the scorer never imports thermosteam);
- a well-formed registry id that the database cannot resolve (an invented
  number, or a real one it does not list, such as cellulose's CAS), or any
  registry id while the database is unavailable, earns 0.5;
- a name that resolves but cannot be verified earns 0.5;
- a formula alone earns 0.25.

Only a chemical that actually resolves (in the database or as a built-in) is
spared the property-data requirement of `CMP-THM-05`; an unresolvable id proves
nothing about it. Chemicals that no stream, agent or reaction references can
lower the identity and molar-mass scores (`CMP-THM-02`, `CMP-THM-03`) but never
raise them, so padding a file with well-identified chemicals it never uses
earns nothing.

### Invalid is missing

A value earns credit only if it is plausible for its role. An implausible value
scores exactly like an absent one. One shared set of predicates decides this:

- temperatures in (0, 1e4) K, pressures > 0 and flows >= 0, for streams and
  utility agents alike;
- fractions in [0, 1] that sum to 1;
- per-parameter ranges for design specifications (for example, a distillation
  column's reflux factor `k` above 1, its key recoveries strictly inside
  (0, 1));
- well-formed registry ids and recognised thermosteam model class names;
- well-formed reactions, numeric costs and finite utility prices;
- class and family claims the unit's ports allow: a unit with more
  material-carrying inlets or outlets than its named class (or its family's
  convention) can have is scored as if the claim were absent, so a two-outlet
  unit labelled "Mixer" is no mixer; mixer and pure-splitter claims must also
  fit the unit's stream temperatures, pressures, species and duties (see
  above);
- stream endpoints: only the sentinel `"None"` states the system boundary. A
  missing `source_unit_id` or `sink_unit_id` key, a null or a blank is a
  **dangling** endpoint. A dangling stream stays in the feed populations
  (`CMP-FEED-01`, `CMP-FEED-02`) but scores 0 there. It does not resolve in
  `CMP-TOP-01`. It may be a missing port of any unit whose total mass balance
  would close with it as well as without it, so no such unit's balance counts
  as closed;
- the outlet flows of a unit whose inlets and outlets all state a mass flow
  but miss the balance by more than 1 % (except for classes that fix their own
  chemistry, such as dryers and combustors).

A `unit_type` never names a class, even when it spells one ("Separator"); it
yields a family by keywords read from its head noun. Only
`design_simulation_method` ("Class on URL") identifies a class exactly, and
only an exact class carries class knowledge: cost-free status, class defaults
and waivers, and the design-spec coverage of `CMP-UOP-03`.

Stream and utility-agent temperatures, pressures and flows are first converted
to BioSTEAM's internal units (K, Pa, kg/hr, kmol/hr, m3/hr) from the units the
file declares in `quantity_units_global` (or, in legacy files, inline). A file
written in degC and bar therefore scores exactly like the same file in K and
Pa. A unit string the scorer's built-in table does not know suspends the
plausibility bounds and consistency checks for that quantity instead of
guessing. Design specifications are read in BioSTEAM's native units.

Units, streams, chemicals and utility agents that share an id are collapsed to
the first occurrence, so repeating an element inflates nothing.

### The monotonicity guarantee and its limits

Because applicability rests on need and invalid values count as missing,
**deleting a field from a consistent document never raises the score**. A field
here is a value, or a whole object or list held under a key. Deletion
monotonicity tests pin this on synthetic documents (Tier 1) and on the corn
reference file (Tier 5). The Tier 1 sweeps include two sparse documents (a
mixer, two pumps, a heat exchanger, a reactor with a reaction and a vent, a
flash and a splitter), one identified by `unit_type` labels only and one by
`design_simulation_method` only. There, every field is deleted in turn,
including ids, stream endpoints, `unit_type` and specification keys. The
sweeps are strict: a criterion may not rise, and a scored criterion may not
become not applicable, even from a score of exactly 1. Dropping a criterion at
the maximum cannot raise the overall score, but it is how a shed need shows
while the need happens to be met. A junk-versus-absence test pins the
invalid-is-missing rule.

"Consistent" includes plausible claims. A document whose "Mixer" label sits on
a unit with a single non-empty inlet beside stated-empty ones makes an
implausible claim. Deleting an empty inlet's flow there can restore the claim,
because a stream of unknown flow counts as an inlet. Likewise, deleting the
temperature, pressure or composition that shows a "Mixer" heating its feed, or
species appearing across it, removes the evidence against the claim. The Tier
1 sweeps also run over mass-basis twins of the sparse documents, and the Tier 5
sample over a mass-basis twin of the corn file, deleting stream endpoints of
internal streams as well.

The guarantee does **not** cover deleting whole list elements (a unit, a stream,
a reaction). Those elements define the populations that criteria average over.
For example, removing an incomplete reaction declared on a non-reactor unit can
raise `CMP-RXN-02`.

### Overall score versus representable score

Some information BioSTEAM needs has no field in any SFF version: annual
operating hours, most TEA assumptions, convergence settings, unit port order,
the cost index the costs were evaluated at. The catalogue marks these criteria
`representable_since: null` ("never" in the tables below). They still count
toward the **overall score**, so the score measures re-import readiness rather
than conformance. At most, they earn derived credit where other fields imply
the information, and several always-applicable ones have nothing to derive from.
**The overall score of any current SFF file is therefore capped below 1.** The
gap is a measure of what the format itself cannot yet carry.

The **representable score** is the same weighted mean restricted to the
criteria that the file's own `sff_version` can state. A missing or unparseable
version counts as the current schema version. The representable score measures
how well a file uses the format it was written in, and an older file is not
penalized for fields its version lacked.

### The recipe route, kept separate

An exported file can also embed a reproducibility recipe: the model's load
script and a pinned conda environment (see [Tags](tags.md)). Re-executing that
recipe is a second, different route to a rebuilt flowsheet. It depends on code
and package versions rather than on declarative content. The report therefore
describes it separately in `recipe`, with three fields: `present`,
`digests_valid` (from `MET-07`) and `reproducible_declared`. It is never folded
into the score.

## Quick start

### Python

```python
from pisces_sff import score_flowsheet_completeness, format_completeness_report

report = score_flowsheet_completeness(
    "pisces_sff/export/exported_flowsheets/bioindustrial_park/SF_BST_01.json")
print(report.score, report.representable_score, report.is_valid)
print(format_completeness_report(report, max_missing=1))

# The criteria that lost points, with the first thing each is missing
for r in report.criteria:
    if r.status == "scored" and r.score < 1:
        print(r.criterion_id, round(r.score, 3), r.missing[:1])
```

`score_flowsheet_completeness` also accepts an already-parsed document (a
`dict`), which it never mutates. The criteria registry is available through
`load_completeness_criteria()`.

### Command line

The module runs as a script. It loads its sibling modules by file path, so it
does not import the whole package:

```console
$ python pisces_sff/completeness/_completeness.py FILE                    # text report
$ python pisces_sff/completeness/_completeness.py FILE --max-missing 1    # fewer locators
$ python pisces_sff/completeness/_completeness.py FILE --json             # full report as JSON
$ python pisces_sff/completeness/_completeness.py --render-docs           # criteria tables of this page
```

### Example: the corn reference file

Running the scorer on the corn dry-grind ethanol reference file gives the
report below, trimmed. The exact numbers change as the catalogue evolves; run
the command for current values.

```console
$ python pisces_sff/completeness/_completeness.py pisces_sff/export/exported_flowsheets/bioindustrial_park/SF_BST_01.json --max-missing 1
SFF completeness report
  sff_version         : 0.2.0   (valid: yes)
  validator checks    : error 23 pass / 2 skip; warning 15 pass; info 2 pass / 1 fail
  overall score       : 0.800   (all scored criteria)
  representable score : 0.924   (criteria this sff_version can state)
  recipe route        : embedded load script + environment; digests valid; 'reproducible' declared

  by category:
    thermo     0.716  Thermodynamic basis (chemicals and property package)
    topology   0.937  Flowsheet topology
    feeds      0.992  Feeds and boundary streams
    unit_ops   0.847  Unit operations
    reactions  0.955  Reactions
    costing    0.813  Equipment costing
    utilities  0.762  Utility agents
    system     0.207  System and TEA settings
    integrity  1.000  Integrity (validator-derived)
  by criticality: essential 0.897  costing 0.577  supplementary 0.816  integrity 1.000

  criterion    wt crit rep score  message
  CMP-THM-01    4 E    yes 1.000  18 of 18 referenced chemical(s) declared
  CMP-THM-02    5 E    yes 0.708  1 of 22 chemical(s) carry a database-resolved registry id, 19 more resolve by a verified name, 0 by an unverified one (5 of these as thermosteam built-in pseudo-components), 0 carry an unresolvable id; 4 unreferenced chemical(s) can only lower the score
      - chemicals[Ethanol].registry_id (id resolvable by name only; molar mass verified)
      ... and 20 more
  ...
  CMP-THM-04    2 E    yes 0.875  14 of 22 chemical(s) state included_in_thermo consistently with the stream phases; 5 more derivable (stream phases or a built-in locked phase)
      - chemicals[Ethanol]: phase behaviour derived from the stream phases only; included_in_thermo false contradicts the stream phases; stream phases seen...
      ... and 7 more
  CMP-THM-05    4 E    no  0.000  2 chemical(s) resolved neither by registry id nor by name (database or thermosteam built-in) need property data no SFF field can state
      - chemicals[SolubleProtein]: property data (V, Cn, H/S reference, Hf, Psat/Tb/Hvap, LHV) not representable
      ... and 1 more
  ...
  CMP-TOP-02    4 E    no  0.873  not representable; 100 of 142 unit port side(s) unambiguous by convention
      - units[MH101].ins: port index not representable (unexported or empty ports may exist)
      ... and 41 more
  ...
  CMP-UOP-06    2 C    yes 0.000  0 of 1 facility unit(s) state every binding and setting
      - units[T608].design_input_specs: one of process_water_streams
      ... and 1 more
  ...
  CMP-UTIL-02   2 C    yes 0.800  3 of 5 referenced/declared agent(s) priced (2 fuel agent(s) priced 0: half credit)
      - utilities.other_utilities[natural_gas].price: 0 on a fuel agent (priced by BioSTEAM, not representable)
      ... and 1 more
  ...
  CMP-SYS-02    5 C    no  0.214  3 of 14 TEA parameter groups stated (only currency, dollar year and target product are representable)
      - metadata: plant start year and duration not representable
      ... and 10 more
  ...
```

Each criterion row gives the weight (`wt`), the criticality initial (`crit`:
**E**ssential, **C**osting, **S**upplementary, **I**ntegrity), whether the
file's version can state it (`rep`), the score and a message, followed by the
first missing locators.

## Interpreting results

The corn reference file is a complete, valid, reproducible simulator export, yet
its overall score is about 0.80 while its representable score is about 0.92.
The gaps fall into two groups.

**What SFF cannot state (schema gaps).** These criteria are marked `rep = no`:

- **TEA settings.** Only the currency (`TEA_currency`), the dollar year the
  costs are expressed in (`TEA_year`) and the target products
  (`metadata.products`) have fields. The plant's start year and duration,
  discount rate, taxes, depreciation, construction and start-up schedules,
  working capital, the capital-cost model, financing and the price book do not,
  and neither do annual operating hours or convergence settings. This is why
  the system category scores about 0.2. The price book matters twice: the
  corn file's two natural-gas utility agents are priced 0, meaning "BioSTEAM
  prices this from its price book", which SFF cannot state (`CMP-UTIL-02`,
  half credit each).
- **Unit port order.** SFF records which unit a stream enters or leaves, not the
  port position, and BioSTEAM units address ports by position. About 70 % of the
  corn file's port sides are unambiguous by convention (a mixer's inlets, a
  single-outlet unit). The rest are not.
- **Chemical data outside the database.** SFF has no field for pure-component
  property data. Two corn pseudo-components, `SolubleProtein` and
  `InsolubleProtein`, resolve neither by registry id nor by name (thermosteam's
  built-ins such as `Ash`, `Fiber` or `Cellulose` do), so their property data
  cannot be stated. SFF can say whether a chemical is locked in one phase
  (`included_in_thermo`), but not which phase; the corn file's flags are also
  partly contradicted by its own stream phases (`CMP-THM-04`).
- **Costing detail.** SFF cannot state the target cost index (CEPCI), parallel
  equipment counts or equipment lifetimes. The cost index earns derived credit
  because it can be back-derived from the recorded correlations. Counts earn
  derived credit for seven units, from a "Number of ..." design result or from
  a correlation's size upper bound. Lifetimes earn derived credit for the 42
  core BioSTEAM units, whose class defaults apply.

**What the exporter could state but does not.** These criteria are marked
`rep = yes` with a score below 1:

- **Registry ids.** The exporter writes a CAS number only for chemicals flagged
  as included in thermo (`registry_id`), so in the corn file only water carries
  one. Fourteen other chemicals resolve by a name whose molar mass matches, for
  0.75 credit each.
- **Design specifications and facility settings.** About 60 initialization
  parameters listed in the design-spec registry go unstated across the units
  (`CMP-UOP-03`). The process-water facility does not state the streams it
  serves (`CMP-UOP-06`).
- **Smaller gaps.** Fifteen costed units are neither core BioSTEAM classes
  (which BioSTEAM re-costs itself) nor covered by a power-law correlation for
  every cost item (`CMP-COST-04`). Cooling water has no temperature limit
  (`CMP-UTIL-03`).

For an extracted flowsheet, the same report reads as a to-do list. The
essential criteria (chemicals, feeds, unit classes and specifications,
reactions) decide whether a rebuild can simulate at all. The costing criteria
decide whether the rebuild can be costed. The missing locators name the exact
fields to fill.

## Schema gaps the scorer exposes

Every criterion marked *never* in the tables below measures information that
BioSTEAM needs but no SFF version can carry. Each gap would close with an
**optional, additive** field, so no existing file would stop validating. The
table names the fields that would make the information representable. They are
proposals for a future schema version, not part of the current format.

| Information | Candidate optional field(s) | Criteria it would unlock |
| ----------- | --------------------------- | ------------------------ |
| Unit port order | `streams[].source_port_index` and `streams[].sink_port_index` (the position in the unit's `outs` / `ins`) | `CMP-TOP-02` |
| The phase a locked chemical is locked in (`included_in_thermo` only says it is locked) | `chemicals[].phase_ref` and `chemicals[].locked_state` (`s`, `l` or `g`) | full credit in `CMP-THM-04` for a locked chemical no stream shows |
| Property data for chemicals outside the database | `chemicals[].properties`: key constants and models such as `Hf`, `Cn`, liquid density (or molar volume `V`), `LHV`, `Tb`, `Tc`, `Pc` and a vapor-pressure model | `CMP-THM-05`; also the property overrides excluded as `undetectable_need` |
| Reaction basis and phase | `units[].reactions[].basis` (`mol` or `wt`) and `units[].reactions[].phase` | `CMP-RXN-04` |
| TEA parameters and operating hours | a `metadata.TEA` object: operating days or hours per year, plant start year and duration, IRR, income tax, depreciation schedule, construction schedule, start-up period and fractions, working capital, the fixed-operating-cost model, the capital-cost model (Lang or DPI/TDC/FCI factors, OSBL unit ids, TEA class), financing, and the price book (`stream_prices`, including utility-port prices) | `CMP-SYS-01`, `CMP-SYS-02`; the zero-price half credits of `CMP-FEED-02` and `CMP-UTIL-02` |
| Convergence settings | a `system.convergence` object: method, molar and temperature tolerances, `maxiter` | `CMP-SYS-03` |
| Heat-agent selection | `utilities.heat_utilities[].dT`, `utilities.heat_utilities[].isfuel` and a global heat-utility `dT` | `CMP-UTIL-05` |
| Parallel equipment | `units[].parallel` and `units[].purchase_cost_correlations.*.N` | `CMP-COST-06` |
| Equipment lifetimes | `units[].equipment_lifetime` (items can already carry `lifetime`) | `CMP-COST-07` |
| Cost index of the stated costs | `metadata.CE_index` (the target CEPCI) | `CMP-COST-05` |

Until such fields exist, these criteria rarely score 1 (most earn derived
credit at best), so the overall score of any current file stays below 1. The
representable score leaves them out.

## Criteria and exclusions reference

The tables below are **generated** from `criteria.yaml`; do not edit them by
hand (see *Extending the catalogue* below). "Representable
since" gives the first SFF version with a field that can state the criterion:
*all versions* means every version, and *never* marks a schema gap. The
exclusions list information BioSTEAM uses that is deliberately not scored. They
come in four kinds:

- `not_capturable`: cannot practically live in static JSON (for example, custom
  unit-class code).
- `derived`: recomputed from information that other criteria already score.
- `out_of_scope`: serves purposes beyond rebuilding, simulating, costing and
  running a TEA on one design point.
- `undetectable_need`: would matter, but nothing in an SFF file tells whether
  a given flowsheet needs it.

<!-- BEGIN GENERATED: completeness criteria -->

40 criteria in 9 categories, total weight 116.

| Key | Category | Criteria | Total weight | Share of weight |
| --- | --- | ---: | ---: | ---: |
| `thermo` | Thermodynamic basis (chemicals and property package) | 7 | 21 | 18.1 % |
| `topology` | Flowsheet topology | 2 | 8 | 6.9 % |
| `feeds` | Feeds and boundary streams | 2 | 13 | 11.2 % |
| `unit_ops` | Unit operations | 6 | 25 | 21.6 % |
| `reactions` | Reactions | 4 | 11 | 9.5 % |
| `costing` | Equipment costing | 7 | 13 | 11.2 % |
| `utilities` | Utility agents | 5 | 8 | 6.9 % |
| `system` | System and TEA settings | 4 | 10 | 8.6 % |
| `integrity` | Integrity (validator-derived) | 3 | 7 | 6.0 % |

### Thermodynamic basis (chemicals and property package)

| ID | Criterion | Weight | Criticality | Representable since |
| --- | --- | ---: | --- | --- |
| CMP-THM-01 | Chemical registry covers every referenced chemical | 4 | essential | all versions |
| CMP-THM-02 | Chemicals resolvable against a property database | 5 | essential | all versions |
| CMP-THM-03 | Molar mass available for every chemical | 2 | essential | all versions |
| CMP-THM-04 | Phase behaviour of each chemical | 2 | essential | all versions |
| CMP-THM-05 | Property data for chemicals outside the database | 4 | essential | never |
| CMP-THM-06 | Thermodynamic property package stated | 3 | essential | all versions |
| CMP-THM-07 | Chemical-set identity in multi-thermo flowsheets | 1 | supplementary | all versions |

CMP-THM-01 -- Chemical registry covers every referenced chemical
: BioSTEAM compiles one Chemicals object before any stream or reaction can exist: every component a stream, utility agent or reaction mentions must be defined there, or constructing the Stream, UtilityAgent or Reaction fails with an undefined-chemical error. A referenced but undeclared component therefore blocks the rebuild outright, which makes coverage of the chemical registry the first gate of any re-import.

CMP-THM-02 -- Chemicals resolvable against a property database
: thermosteam builds a pure-component model (vapor pressure, heat capacity, heat of vaporization, molar volume, viscosity, ...) by looking the chemical up in the chemicals/thermo databases. Each chemical scores the best of the tiers it reaches, so deleting one datum only removes an option. A registry id -- a CAS number with a correct check digit, an InChI whose formula layer parses, an InChIKey or SMILES -- that the chemicals database RESOLVES (looked up as that kind of identifier: "InChIKey=" and "SMILES=" prefixes, so "C" means methane, not carbon) to a record whose molar mass (within 2 %) or formula agrees with the chemical's, when the chemical states one, identifies the species and brings the database's correlations with it (credit 1). A well-formed id the database cannot resolve -- a real number it does not list, such as cellulose's CAS, or an invented one with a valid check digit -- or any id while the database is unavailable, brings nothing checkable and earns 0.5. A malformed id ("x", "InChI=1S/x"), one whose database record or InChI formula layer contradicts the chemical's molar mass or formula, counts as no id. The chemical id is also looked up by name exactly as thermosteam's Chemical(ID) looks it up: its search of the chemicals identifier database (a CAS number as such; otherwise the id with "\_" read as a space and CamelCase split into words -- AceticAcid as "acetic acid", CalciumDihydroxide as "calcium dihydroxide" -- then the id itself, as names, the first database file claiming a name keeping it; last the id as a formula), then thermosteam's built-in bioprocess pseudo-components (Cellulose, Starch, Fiber, Yeast, Ash, Protein, Xylan, ... -- the names its database does not know, with the molar mass, formula and locked phase thermosteam gives them). A name so resolved identifies the chemical when the record's molar mass (or formula) matches the chemical's declared or formula-derived one within 1 % (verified, 0.75), probably identifies it when the chemical states neither (unverified, 0.5), and misidentifies it on a mismatch (the name tier is then withheld: "HP" resolves to hematoporphyrin, not 3-hydroxypropionic acid; "Lignin" to the database's C81H92O28 record, not a C8H8O3 monomer). An id that is itself a molecular formula with two or more carbon atoms (C2H6O: ethanol or dimethyl ether) earns no name credit, being isomer-ambiguous; a formula id with at most one carbon atom (CO2, O2, CH4, NH3, H2SO4, P4O10, CaO) names one compound and earns name credit like any other name. The lookup is lazy and cached, always runs the scorer's own import-light replica of that search (so a file scores the same whether or not thermosteam is loaded in the process), and is unavailable when the chemicals package cannot be imported. A formula alone -- a parseable formula field, or a formula-like id -- narrows the search but does not identify an isomer (C2H6O is ethanol or dimethyl ether), so it earns 0.25. Without any of these, every property must be supplied by hand. The population is every declared chemical plus every referenced but undeclared id (which scores 0); a flowsheet with streams or units but no chemicals at all scores 0. When any chemical is referenced (by a stream or agent composition or a reaction), the score is the mean over the referenced chemicals plus every unreferenced one that lowers it -- the lowest mean over any set between the referenced chemicals and all of them, never above the lesser of the referenced and the overall means -- so unreferenced chemicals can lower the score but never raise it: padding a sparse file with well-identified chemicals it never uses earns nothing, and a chemical losing its last reference never lifts the score. With nothing referenced it is the plain mean.

CMP-THM-03 -- Molar mass available for every chemical
: Molar mass converts every flow between the mass and molar bases that BioSTEAM uses internally, closes stoichiometric mass balances, and sizes equipment costed on kg/hr. Pseudo-components such as Fiber or Protein have no database entry, so their molecular weight must be stated explicitly or implied by a formula. Scored over declared plus referenced-but-undeclared chemicals (the latter score 0), aggregated as CMP-THM-02 is: unreferenced chemicals can lower the score, never raise it.

CMP-THM-04 -- Phase behaviour of each chemical
: BioSTEAM distinguishes chemicals that partition between phases from chemicals locked in one state (Chemical.at\_state, phase\_ref, default phase): cellulose, ash or cell mass are fixed solids, and a flash or column that treated them as volatile would fail for want of a vapor pressure or partition them wrongly. The base field chemicals\[\].included\_in\_thermo says whether a chemical takes part in phase equilibrium, but not which phase a locked one is locked in. Each chemical scores the best of: its flag, when the stream phases do not contradict it -- true (it partitions), 1; false (locked) and shown in exactly one phase, which names the locked phase, 1; false and never shown in a stream, 0.5 -- where a flag the streams contradict (false but shown in two or more phases, true but only ever as a solid) counts as absent; the stream phases alone, two or more (it partitions) or only ever solid (it is locked), 0.75 (derived); or the locked phase of the thermosteam built-in pseudo-component its id resolves to (see CMP-THM-02), 0.75 (derived). The reference exporter currently mispopulates the flag (true only for the members of one representative stream's vapor-liquid equilibrium set, so ethanol or CO2 seen as vapor and as liquid are exported false), and such contradicted flags earn nothing beyond the stream evidence. Scored over declared plus referenced-but-undeclared chemicals (the latter score 0).

CMP-THM-05 -- Property data for chemicals outside the database
: A chemical that cannot be resolved from a database must be created with hand-supplied property models: molar volume, heat capacity, reference enthalpy and entropy, heat of formation (for heats of reaction and combustion), and vapor pressure, boiling point and heat of vaporization when it is volatile, plus a lower heating value for combustibles burned in a boiler. Thermosteam refuses to compile a chemical set with missing required properties -- for every member, so a declared but unreferenced chemical counts too -- and no SFF field can carry these models. The criterion looks at every declared and every referenced id and applies to each one thermosteam cannot resolve -- neither by a registry id the chemicals database finds without contradiction nor by a verified or unverified name, looked up as Chemical(ID) looks it up (see CMP-THM-02: the database, then thermosteam's built-in pseudo-components such as Cellulose, Starch, Fiber, Yeast and Ash, which come with their property models; a mismatching or isomer-ambiguous name does not count; an undeclared id is judged by its name alone) -- and then scores 0. A well-formed but unresolvable registry id exempts nothing (an invented CAS number, or cellulose's real one, brings no property data), and with the database unavailable only a built-in is exempt. Such a custom chemical also lacks what phase-equilibrium models need beyond pure-component data -- UNIFAC or Dortmund group assignments for activity-coefficient packages and any binary interaction parameters -- which no SFF field carries either; this criterion stands for that gap too. Weighted heavily because one such chemical blocks the whole rebuild.

CMP-THM-06 -- Thermodynamic property package stated
: The Thermo object pairs the chemicals with a mixture model, activity coefficients (ideal or Dortmund-modified UNIFAC, ...), fugacity coefficients and Poynting correction factors. These decide every vapor-liquid and liquid-liquid split, so the same flowsheet under a different package produces different flash, column and evaporator results. A descriptor counts only when its leading identifier is a thermosteam model class of its role (IdealMixture or an EOS mixture; an activity-coefficient class such as DortmundActivityCoefficients; a fugacity-coefficient class; a Poynting-correction class, either spelling), since only those rebuild the package; "x" or a bare "NRTL" count as absent. Each unit counts once.

CMP-THM-07 -- Chemical-set identity in multi-thermo flowsheets
: When areas of a flowsheet run under different Thermo objects (a subsystem with a reduced chemical set, or a different activity model), the importer must know which chemical set each unit binds so streams crossing the boundary can be converted. The property-package id names that set. Applies whenever the flowsheet has units (whether it is multi-thermo is itself data a deletion could remove): per unit, 1 when its package states an id (a non-blank string); derived credit when every unit states a full, valid package (mixture, gamma, phi and PCF each naming a thermosteam model class, as for CMP-THM-06) and all of them are identical, which is single-thermo evidence implying one chemical set; else 0. Each unit counts once.

### Flowsheet topology

| ID | Criterion | Weight | Criticality | Representable since |
| --- | --- | ---: | --- | --- |
| CMP-TOP-01 | Stream endpoints resolve and every unit is connected | 4 | essential | all versions |
| CMP-TOP-02 | Port order of multi-stream inlets and outlets | 4 | essential | never |

CMP-TOP-01 -- Stream endpoints resolve and every unit is connected
: Units are wired through their ins and outs, and System.from\_units builds the flowsheet graph from that wiring. A stream endpoint naming no unit (and not the system boundary) cannot be wired, and a unit attached to no stream is never reached by the simulation, so both leave the rebuilt system different from the original. Only the explicit sentinel "None" states the boundary: an endpoint that is not stated (a missing key, null or blank) is dangling and does not resolve.

CMP-TOP-02 -- Port order of multi-stream inlets and outlets
: BioSTEAM units address ports by position, and position carries meaning: outs\[0\] of a Flash or BinaryDistillation is the vapor or distillate and outs\[1\] the liquid or bottoms; a Splitter's split fractions apply to outs\[0\]; a JetCooker expects the slurry in ins\[0\] and steam in ins\[1\]. SFF records which unit a stream connects to but not the port index, so a unit side with several streams cannot be wired unambiguously. Order-free sides (mixer and tank inlets) earn full credit -- for a mixer or tank family its ports do not contradict (CMP-UOP-01: a 'Mixer' with two material-carrying outlets is no mixer). A side on which at most one stream carries material (stated-empty streams are placeholders, like unexported ports) earns full credit on a single-port side (pumps, valves, heat exchangers, ...) and derived credit elsewhere (unexported or empty ports may exist) -- but both rest on the stream count, so only while the unit's TOTAL mass balance closes, which alone shows no material-carrying port is missing whether or not its species flows are computable, and no dangling stream (an endpoint not stated) could be a missing port of it that the balance cannot rule out (detaching a stream never earns credit). Sides with several material-carrying streams earn derived credit only when the order follows from the data (a flash's vapor and liquid outlets, a splitter with explicit split fractions) and the unit's balances close. A unit without an id, whose ports no stream can name, scores 0, and so do both sides of every unit id a stream endpoint references but no unit declares. Each unit counts once.

### Feeds and boundary streams

| ID | Criterion | Weight | Criticality | Representable since |
| --- | --- | ---: | --- | --- |
| CMP-FEED-01 | Feed streams fully stated | 10 | essential | all versions |
| CMP-FEED-02 | Boundary stream prices | 3 | costing | all versions |

CMP-FEED-01 -- Feed streams fully stated
: In BioSTEAM's sequential-modular solve the feeds are the degrees of freedom: every feed's temperature, pressure, flow rate and composition must be set before System.simulate, and every other stream is computed from them; this is the heaviest single criterion. Each part counts only when plausible: a temperature in (0, 1e4) K, a pressure above zero, a flow of at least zero, and a composition whose every entry names its component and whose mol (or mass) fractions lie in \[0, 1\] and sum to 1 within 1e-3 per phase. A composition earns full credit only when its phases are stated -- given per phase (stream\_properties.phases, each phase with a molar or a mass flow -- or, for a single-phase stream, the stream total -- and mol or mass fractions), or a legacy flat list tagging every entry with a phase -- and 0.75 when a legacy flat list leaves an entry untagged, since BioSTEAM would then assume the feed liquid. A flowsheet with no feed defines no problem and scores 0. Feeds weigh equally and every feed counts: one whose state says it is empty (total flow numerically zero, an unused placeholder port) scores 1, since its zero flow states all such a port needs -- but only when some feed states a non-zero flow and the unit it enters has a class known exactly (confidence 1), whose fixed ports make a placeholder feed legitimate. Otherwise a stated zero counts as a missing flow with no composition exemption: with no non-zero feed nothing sets the problem's scale, and zero-flow feeds padding a unit of unknown class state nothing (so writing zeros earns nothing). The message reports how many feeds are empty and how many of those earn the placeholder credit. A stream with both endpoints at the boundary counts as a feed. A dangling stream (an endpoint not stated: a missing key, null or blank -- never the boundary) may be a feed, so it stays in the population but scores 0 and anchors nothing: losing a feed's endpoint never drops it, and detaching an internal stream never adds a credited feed. A feed whose flow is unknown scores its stated parts (temperature, pressure, composition). (Leaving empty feeds out, or weighting by mass-flow share, let deleting a flow raise the score.)

CMP-FEED-02 -- Boundary stream prices
: Stream.price on feeds and products drives the material cost and sales revenue of the TEA, and therefore every minimum selling price or NPV. Every boundary stream counts (one with both endpoints at the boundary too): an empty one (total flow numerically zero) needs no price and scores 1; every other one needs a price. A dangling stream (an endpoint not stated: a missing key, null or blank -- never the boundary) may be a boundary stream, so it counts too but scores 0, even when empty or priced: losing an endpoint never drops an unpriced stream nor turns a priced internal stream into a credited product. An explicit zero is a valid statement, except on a feed that is a unit's utility-port material, which BioSTEAM prices itself when the stream's price is 0 (from bst.stream\_utility\_prices, or a class default) in a way SFF cannot state, so it earns half credit: natural gas (at least 50 mol% CH4) into a combustor, dryer, boiler or boiler-turbogenerator; lime or boiler chemicals into a boiler or boiler-turbogenerator; water-dominated makeup water into a process-water center (or a facility of unknown kind). The port material is identified by the feed's mol fractions (from mol data, else from mass fractions and the chemicals' molar masses); a feed whose composition is unknown may be one and is treated as one, so deleting a composition never raises the score. Air and every other zero-priced feed are scored like any stated price.

### Unit operations

| ID | Criterion | Weight | Criticality | Representable since |
| --- | --- | ---: | --- | --- |
| CMP-UOP-01 | Unit class identity | 8 | essential | all versions |
| CMP-UOP-02 | Essential operating specifications | 8 | essential | all versions |
| CMP-UOP-03 | Full design-specification coverage | 3 | costing | all versions |
| CMP-UOP-04 | Computed stream states stated | 3 | supplementary | all versions |
| CMP-UOP-05 | Units of design specifications | 1 | supplementary | never |
| CMP-UOP-06 | Facility bindings and settings | 2 | costing | all versions |

CMP-UOP-01 -- Unit class identity
: Re-importing a unit means instantiating a class (Flash, HXutility, a biorefinery-specific subclass, ...) because the class carries the mass and energy balance, design and costing methods. Exporters state it as "\<Class\> on \<source URL\>" in design\_simulation\_method, the only field that names a class: unit\_type is free text that yields a family by keywords only, even when it spells a class name ('Separator', 'Evaporator'), since extracted files and exporters alike use class names as labels. Keywords are read from the head noun -- the rightmost equipment word ('Wastewater pump' is a pump, 'Fermentation tank' a reactor, 'Feed/effluent heat exchanger' a process-process exchanger); a service word (wastewater, boiler, process / chilled / cooling water, CIP, fire water) decides only when the head is generic (system, treatment, package, unit, ...) or absent. Full credit needs a concrete class: one on the scorer's static list of concrete BioSTEAM unit classes (enumerated from the biosteam source, facilities and the hensmith HeatExchangerNetwork included), or a custom (biorefinery) class whose source URL lies in the repository the flowsheet model package names and pins to a commit or version -- only then is its code located at a fixed revision; any other custom class (unpinned, a URL elsewhere, such as a tank\_factory class credited to biosteam's tank.py, or no URL) earns 0.75. A biosteam URL alone proves nothing: a class the URL attributes to biosteam that is neither on the core list nor a known custom class, and an abstract class (Unit, Tank, Compressor, Distillation, HX, ... -- declared isabstract in biosteam and never instantiated), identify at most a family: the unit resolves as if no class were named. Without a named class, a stated family (a unit\_type keyword, or the named abstract class's family) takes precedence: it earns 0.75 when every design-spec registry class that holds the unit's specification keys belongs to that family, else half credit; with no stated family, such an unambiguous key-set family earns 0.75. The key set never swaps a stated family, and an inferred class stays a family: only family-level rules apply to it. A class or family claim contradicted by the unit's ports -- more material-carrying (not stated-empty) inlets or outlets than the class can have (a static table of every core class's port counts from the biosteam source, most permissive over its history since 2022), or than its family's convention allows (a mixer has one outlet; a pump, valve or utility exchanger one inlet and one outlet), or a mixer family claimed without an exact class (a 'Mixer' label) on fewer than two inlets that are not stated empty (a one-inlet 'mixer' mixes nothing and would hide a reactor or an exchanger) -- counts as unstated; a bare 'heat exchanger' with two inlets and outlets is a process-process exchanger. So does a claim that removes needs but that the unit's streams or duties contradict: a mixer claim (exact class or label) whose outlet is more than 1 K hotter than its hottest inlet or more than 1 % above its highest inlet pressure (upper bounds only: a mixer may cool and mixes at the lowest inlet pressure), and a pure-splitter claim (Splitter, PhaseSplitter, MockSplitter / FakeSplitter, or a splitter label without an exact class) whose outlet differs from its inlet by more than 1 K or 1 %, either one across which a species appears or vanishes (flows computable, no well-formed reaction declared) or that records a non-zero heat or power duty (an adiabatic Mixer draws nothing; the @cost-decorated Mixer subclasses may draw agitator power). Streams stated empty are ignored and a bound is tested only on stated values, so deleting data never creates a contradiction; relabelling a pump, heater or fermenter as a mixer thus leaves its family unknown instead of shedding its needs. A unit whose family stays unknown carries every family-dependent need (reactions, heat and power agents, utility results, costing). A unit id that stream endpoints reference but no unit declares scores 0. Every per-unit average in this scorer is unweighted -- each unit counts once: a weight read from unit\_type or design\_simulation\_method would move when either is deleted, raising criteria whose per-unit score does not depend on them; major equipment keeps its emphasis through its richer requirement sets.

CMP-UOP-02 -- Essential operating specifications
: Each unit class needs a minimal set of specifications before it can run: an HXutility an outlet temperature or duty (plus a vapor fraction when a non-rigorous exchanger changes phase); a Flash two of T, P, V, Q, y and x; a Pump an outlet pressure (and dP\_design when it raises no pressure); a BinaryDistillation its keys, pressure, recoveries or purities and reflux factor; a MESH column its stages, feed stages, reflux and boilup; a reactor its temperature, residence time and working volume, and a stirred-tank reactor whose constructor takes it its batch flag (required too when the reactor's class is unknown); a batch bioreactor also its reactor count or volume; a splitter its split fractions and the moisture content of its retained solids (a SolidsSeparator's moisture\_content; biosteam.wastewater's sludge\_moisture); a drum dryer its split fractions, dried-solids moisture content and drying temperature; a thermal oxidizer its fuel duty per kg of feed (duty\_per\_kg). Such a class-knowledge requirement is waived only for a class known exactly (confidence 1) that affirmatively takes none of its parameters (a plain Splitter, MolecularSieve or liquids centrifuge has no moisture\_content); a class inferred from specification keys gets its family's rules only, and so does a unit\_type that merely spells a class name ('Separator'): only design\_simulation\_method identifies a class exactly. Boilers, boiler-turbogenerators, heat-exchanger networks and other facilities are scored by CMP-UOP-06 instead. Constructor arguments with defaults (V\_wf, a tank's tau, theta\_O2, feed stages, the wastewater units' loading rates, ...) must be stated: only a class-level attribute that is not a constructor argument (an NREL batch bioreactor's cleaning time tau\_0) earns derived credit as a class default, and only for a concrete class identified at confidence 1 whose design-spec registry entry confirms it is no constructor argument. Explicit specifications earn full credit only with a value plausible for the parameter (a temperature in (0, 1e4) K, a positive pressure or residence time, a working-volume fraction or efficiency in (0, 1\], split fractions in \[0, 1\], a column's key recoveries Lr and Hr and its y\_top and x\_bot strictly inside (0, 1), a distillation column's reflux factor k above 1, two light/heavy keys, a boolean flag, else any non-empty value); a blank or out-of-range value counts as unstated. A specification recoverable from stream states, design results or a class default earns derived credit, and one read from stream data only when the unit's mass balance -- and, for a physical unit, every species' balance, on the molar basis or else the mass basis -- closes within 1 %, no dangling stream (an endpoint not stated) could be a missing port the balance cannot rule out, and, for a physical-only unit without declared reactions, no species appears or vanishes across it (nor can the check be ruled out by uncomputable species flows); an explicit T or P that contradicts the unit's outlet streams earns nothing. A conditional requirement (the vapor fraction on a phase change, phase0/phase1 of a process exchanger, dP\_design) is waived only when the data show its condition false (both phase sets known and equal, a pressure rise of at least 1 Pa, over a unit whose balances close so no port is missing); when the data cannot tell, it applies. Units of unknown family, and unit ids that stream endpoints reference but no unit declares, score zero because nothing tells the importer what to specify. Each unit counts once.

CMP-UOP-03 -- Full design-specification coverage
: Beyond the essential specifications, a unit's remaining initialization parameters (materials of construction, vessel type, heat-transfer coefficient, number of shells, working-volume fraction, tray spacing, ...) change its design and purchase cost. Left unstated, BioSTEAM silently applies its own defaults, which may differ from the original model. A unit whose class is known exactly (named by design\_simulation\_method and identified at confidence 1; a class name in unit\_type does not count) is measured against the design-spec registry entry of its class, else of its nearest registry ancestor (a corn storage tank against StorageTank); unstated alternatives of an essential requirement already met (V and H for an HXutility given T) are excused. A parameter counts as stated only with a plausible value (as for CMP-UOP-02). A core BioSTEAM class known exactly that no registry entry covers is left out (a gap in the scorer's vocabulary, not in the file); an exact custom class that no registry entry covers scores 0 (the file states none of its parameters -- the exporter writes an empty object for it). Every other unit -- class inferred from its specification keys, family only, or unknown -- scores 0 because its parameter universe is unknown. Each unit counts once.

CMP-UOP-04 -- Computed stream states stated
: Internal and product stream states are simulation outputs rather than inputs, but they seed tear streams so recycle loops converge from a good initial guess, give a re-simulated flowsheet verification targets, and are the base from which missing unit specifications can be derived. Each state part counts only when plausible, as for CMP-FEED-01 (a stream stating a zero total flow needs no composition). Consistency guard: a physical-only unit (mixer, exchanger, pump, valve, compressor, turbine, flash, column, evaporator, steam mixer, tank, or a splitter -- or a unit of unknown family -- not identified as a custom class) that declares no reactions cannot create or destroy species; when one appears or vanishes across it (compared on the molar basis when every connected stream states mol data, else on the mass basis), its outlets' compositions do not count as stated ('inconsistent'), nor do they when its species flows cannot be computed on either basis for every connected stream ('unverified'; the message tells the two apart). Mass-balance guard: when every inlet and outlet of a unit states its mass flow and the totals disagree by more than 1 %, its outlets' flows do not count as stated (nothing tells which stated flow is wrong; a stated-zero flow then earns no composition exemption either) -- except for families whose chemistry the class fixes (combustors, dryers, boilers, facilities, equilibrium reactors), whose classes may account mass implicitly (BioSTEAM's DrumDryer emits the combustion products of its natural gas without drawing the oxygen from any inlet). A stream whose source\_unit\_id is not stated (only the "None" sentinel affirms the boundary) may be internal, so it stays in the population and scores 0: its state is no unit's outlet state; a unit's outlet whose sink\_unit\_id is not stated stays in as that unit's outlet.

CMP-UOP-05 -- Units of design specifications
: design\_input\_specs values are bare numbers with no quantity units (the schema's unit registries cover stream properties and design results, not specifications), so a tau, dP\_design or U can only be read in the units the class's own conventions imply: hours, Pa and kW/m2/K for the corresponding BioSTEAM class. That convention is available only when the class itself is identified at confidence 1, which earns derived credit; otherwise a re-importer cannot tell hours from seconds or bar from Pa. Applies to every unit that needs operating specifications or facility settings (the CMP-UOP-02 and CMP-UOP-06 populations).

CMP-UOP-06 -- Facility bindings and settings
: BioSTEAM facilities act on the whole plant through bindings and settings their constructors take with defaults, which a rebuild must restate: a BoilerTurbogenerator its boiler and turbogenerator efficiencies, whether it satisfies the system's electricity demand, its fuel source, and the steam agent(s) it raises; a plain Boiler its boiler efficiency; a ProcessWaterCenter the process-water and makeup- water streams it balances; a HeatExchangerNetwork its minimum approach temperature T\_min\_app and the units it integrates; a CoolingTower, ChilledWaterPackage or RefrigerationPackage the agent it regenerates (and a RefrigerationPackage its efficiency). The free-form design\_input\_specs object can state them in every SFF version. A value counts only when plausible (efficiencies in (0, 1\], a boolean flag, a positive T\_min\_app); stream- and unit-id lists only when non-empty and every id is a declared stream or unit; an agent only when it names a declared heat agent. The served agent is derivable (derived credit) when the facility's utility results name a declared heat agent. Population: every unit of a facility family (boilers, boiler-turbogenerators, heat-exchanger networks, other facilities) except cost-only facilities (CIP, fire water, air distribution, wastewater cost lumps); a facility whose kind is unknown scores 0. These requirements are scored here, not in CMP-UOP-02, so nothing is counted twice. Each unit counts once.

### Reactions

| ID | Criterion | Weight | Criticality | Representable since |
| --- | --- | ---: | --- | --- |
| CMP-RXN-01 | Reactive units carry their reactions | 4 | essential | all versions |
| CMP-RXN-02 | Reaction definitions complete | 4 | essential | all versions |
| CMP-RXN-03 | Series or parallel reaction structure | 1 | supplementary | all versions |
| CMP-RXN-04 | Reaction basis and reacting phase | 2 | essential | never |

CMP-RXN-01 -- Reactive units carry their reactions
: BioSTEAM reactors apply Reaction, ParallelReaction or SeriesReaction objects in their mass balance, so a reactor cannot be reproduced without its reaction set. The population is defined by need alone: the units of a reactor family (continuous reactors, batch, aerated and gas-fed bioreactors, and units whose type names a neutralizer, hydrotreater, gasifier, pyrolysis or hydrolysis step) and every unit whose family is unknown (no class, no keyword family, or a claim its ports or stream physics contradict -- a 'mixer' across which species appear is one: it could be a reactor, and only affirmative evidence waives a need), each of which must declare at least one well-formed reaction: an equation or stoichiometry naming at least one consumed and one produced species (\[\{\}\] or an equation "x" counts as none). Declared reactions and species appearing in a unit's outlets no longer make a unit reactive (a population read from data let deleting that data shrink it); species appearing across a physical-only unit (or one of unknown family) is instead a consistency guard on CMP-UOP-04 and on derivations. Families whose chemistry the class itself fixes (combustors, boilers, boiler-turbogenerators, facilities, Gibbs reactors, dryers) are not in the population. Each unit counts once.

CMP-RXN-02 -- Reaction definitions complete
: A thermosteam Reaction is defined by its stoichiometry, the reactant on which conversion is based, and the fractional conversion X (0 to 1). Missing any one of the three leaves the extent of reaction undefined. Per reaction: 0 unless it is well-formed (see CMP-RXN-01) -- a conversion attached to no parseable stoichiometry earns nothing -- else the mean of \{well-formed, the reactant is one of its consumed species, a numeric conversion in \[0, 1\]\}. Scored per unit (each counting once) over its reactions, for the reactive units of CMP-RXN-01 -- one without reactions scores 0 -- plus every other unit that declares reactions (a boiler's combustion set, ...). Deleting a field of a reaction never raises the score; deleting whole reactions (list elements) is outside the monotonicity guarantee.

CMP-RXN-03 -- Series or parallel reaction structure
: The same list of reactions yields different outlets as a ParallelReaction (every conversion based on the inlet) or a SeriesReaction (each based on the previous result). Scored per unit over the CMP-RXN-02 population: with two or more reactions, an index on every reaction is the minimal signal of their order (1, else 0); a single reaction has no order to state (1); a reactive unit without a well-formed reaction scores 0, since its reaction set and so their order are unknown.

CMP-RXN-04 -- Reaction basis and reacting phase
: Reactions may be defined on a molar or mass basis (mass basis is common for biomass pseudo-components) and restricted to a phase; both change the computed outlet. No SFF field states either, so each reaction of the CMP-RXN-02 population (a reactive unit without reactions scores 0, a reaction that is not well-formed scores 0) earns only derived credit, half for each part: the basis when sum(nu\*MW) = 0 (a molar reaction conserves mass) or sum(nu) = 0 (a mass-basis reaction) holds to 1e-3 relative, with every molar mass resolvable -- when both hold, as for an isomerisation, the basis is immaterial; the phase when the equation tags every species with a phase or every stream of the unit is single-phase. Each unit counts once.

### Equipment costing

| ID | Criterion | Weight | Criticality | Representable since |
| --- | --- | ---: | --- | --- |
| CMP-COST-01 | Purchase costs recorded | 3 | costing | all versions |
| CMP-COST-02 | Installed costs cover every purchase-cost item | 2 | costing | all versions |
| CMP-COST-03 | Design results stated with quantity units | 2 | costing | all versions |
| CMP-COST-04 | Purchase-cost correlations | 2 | costing | 0.1.2 |
| CMP-COST-05 | Cost-index basis (target CEPCI) | 2 | costing | never |
| CMP-COST-06 | Parallel counts (Unit.parallel, CostItem.N) | 1 | costing | never |
| CMP-COST-07 | Equipment lifetimes | 1 | costing | never |

CMP-COST-01 -- Purchase costs recorded
: A unit's purchase costs per cost item are the verification target for a re-costed flowsheet and the only cost information for procedurally costed units. Scored over the costed units -- every unit except the cost-free ones (mixers, Splitter-type splitters, junctions), which only a class named by design\_simulation\_method, known exactly and not contradicted by the unit's ports or stream physics makes (a 'Mixer' or 'Separator' unit\_type, or a family inferred from specification keys, is no evidence a unit carries no cost, nor is a 'Mixer' whose outlet is hotter than its inlets) -- each needing a purchase\_costs object holding at least one numeric cost of at least zero (\{"a": null\} records nothing). Each unit counts once.

CMP-COST-02 -- Installed costs cover every purchase-cost item
: Installed cost is purchase cost times the bare-module factors (design, pressure and material factors included), and the TEA's fixed capital investment is built on installed equipment cost. Each cost item of a costed unit (every item named by its purchase costs, installed costs or correlations) needs its installed cost, a number of at least zero; a costed unit with no items scores 0. Each unit counts once.

CMP-COST-03 -- Design results stated with quantity units
: Design results (area, volume, power, flow) are the sizes cost correlations are evaluated at. Without their units a size cannot be compared with a correlation's reference size, so each numeric result needs a unit: a non-blank string, or exactly the empty string, the schema's documented marker of a dimensionless result. Per costed unit: no numeric result (absent, empty, or only non-numeric values such as \{"a": "b"\}) 0, some numeric result without a unit 0.5, else 1. Each unit counts once.

CMP-COST-04 -- Purchase-cost correlations
: BioSTEAM's @cost decorator records a power-law correlation per cost item (basis, reference size and cost, exponent, reference cost index, installation factor, power rate). With it a consumer can re-cost the equipment at a different size or cost year; a custom\_function entry only records that a procedural cost model exists, so it earns half credit. Scored per costed unit: a core BioSTEAM class identified at confidence 1 scores 1, because BioSTEAM re-derives its cost from its class and design results; any other costed unit scores the mean credit over its cost items (every item named by its purchase costs, installed costs or correlations, so deleting one entry never drops an item), and 0 when it has none. Each unit counts once.

CMP-COST-05 -- Cost-index basis (target CEPCI)
: BioSTEAM scales every correlation by the ratio of the target plant cost index (bst.CE) to the correlation's reference index, so the index the costs were evaluated at is needed to reproduce or escalate them. SFF has no field for it, but it can be back-derived from any power-law item that states its purchase cost, sizing basis and correlation constants, which earns derived credit. Applicable whenever the flowsheet has costed units.

CMP-COST-06 -- Parallel counts (Unit.parallel, CostItem.N)
: A unit may run as several identical parallel copies (Unit.parallel) and a cost item may be split across N identical pieces of equipment (CostItem.N); both change the cost through economies of scale. SFF states neither. Per costed unit, derived credit when a design result states a count ('Number of reactors', ...) with a numeric value, or when a power-law correlation states a positive size\_upper\_bound and its basis has a numeric design result S (BioSTEAM then buys N = ceil(S / size\_upper\_bound) pieces), else 0. Each unit counts once.

CMP-COST-07 -- Equipment lifetimes
: BioSTEAM replaces equipment whose lifetime is shorter than the plant's (Unit.equipment\_lifetime, or a per-item lifetime set with @cost), and the TEA charges the replacement purchases, so lifetimes change the capital schedule. SFF has no unit-level field; only a correlation item can carry one. Per costed unit: 1 when it has cost items (named by its purchase costs, installed costs or correlations) and every one has a correlation stating a lifetime; derived credit for a core BioSTEAM class identified at confidence 1 (its class-default lifetime applies, which the importer gets by instantiating the class); else 0. The message counts both. Each unit counts once.

### Utility agents

| ID | Criterion | Weight | Criticality | Representable since |
| --- | --- | ---: | --- | --- |
| CMP-UTIL-01 | Utility agents declared and specified | 2 | essential | all versions |
| CMP-UTIL-02 | Utility prices | 2 | costing | all versions |
| CMP-UTIL-03 | Heat-agent efficiency and temperature limit | 2 | costing | all versions |
| CMP-UTIL-04 | Utility results per unit | 1 | supplementary | all versions |
| CMP-UTIL-05 | Agent selection parameters | 1 | costing | never |

CMP-UTIL-01 -- Utility agents declared and specified
: Heat utilities are satisfied by utility agents (cooling water, steam at several pressures, refrigerants) that BioSTEAM selects by temperature and that are defined by temperature, pressure and composition. A duty cannot be assigned to an agent that is not declared, and an agent without its state cannot be instantiated. Each part of a heat or other agent's state counts only when plausible, by the same predicates as a stream's (a temperature in (0, 1e4) K and a positive pressure, after conversion to internal units; a composition whose entries name their components and whose fractions lie in \[0, 1\] and sum to 1). A heat agent is scored over four parts: also its phase, stated when every composition entry carries the same phase, l or g -- BioSTEAM's utility agent is a one-phase stream (liquid cooling water, vapor steam) whose heat duty per kg depends on it. Applicable when a unit is in a utility-using family (heat exchange, electricity or fuel: reactors, columns, heaters, pumps, compressors, agitated tanks, conveyors, mixer-settlers, boilers, dryers, ...), any unit records utility results, or any agent is declared; with that need and no agent at all it scores 0. Need is also judged per kind: when a unit exchanges heat (a heat-exchanging family) but no heat-utility agent is declared, a missing "heat agent" entry scores 0 beside the declared and referenced agents; likewise a missing "power utility" entry when a unit draws or delivers electricity (pumps, compressors, turbines, solids handling, boiler-turbogenerators) but no power utility is declared -- a declared power utility does not stand in for steam. A unit of unknown family could be any of these, so it needs utilities of both kinds.

CMP-UTIL-02 -- Utility prices
: Heat agents are priced per mole regenerated or per unit of heat transferred, electricity per kWh, and other utilities per unit flow; together they make up the utility share of the variable operating cost in the TEA. A price counts only when finite, and a heat agent's regeneration or heat-transfer price only when also at least zero. A zero price on an other-utility agent that is a fuel (its id names natural gas or a fuel, or its composition is at least 50 mol% CH4) -- or may be one, its composition not being plausibly stated -- earns half credit: BioSTEAM then prices the fuel from bst.stream\_prices, which SFF cannot state (as CMP-FEED-02 treats utility-port feeds). Same applicability as CMP-UTIL-01; scored over declared agents plus referenced but undeclared ones and the missing heat-agent or power-utility kinds of CMP-UTIL-01 (all of which score 0).

CMP-UTIL-03 -- Heat-agent efficiency and temperature limit
: The heat-transfer efficiency and the temperature limit set how much agent a duty consumes: a sensible-heat agent may only warm up (or cool down) to its limit, so liquid water-based agents (cooling or chilled water, brine) and liquid heating agents require one. A phase-change agent delivers latent heat and needs none: a condensing heating agent (all vapor, such as steam) or an evaporating refrigerant (all liquid, not water-dominated, used for cooling). Heating or cooling is read off the sign of the duties units record for the agent (positive heating, negative cooling; agent-regenerating facilities ignored), and the exemption needs that affirmative evidence: a vapor agent no unit records a positive (heating) duty for still needs its limit. Half credit each for efficiency (in (0, 1\]) and limit (a plausible temperature). Applicable when a unit exchanges heat with a utility agent (or is of unknown family, so may) or a heat agent is declared; with that need and no heat agent it scores 0.

CMP-UTIL-04 -- Utility results per unit
: Per-unit utility consumption and production let a re-simulated flowsheet be checked against the original heat and power demands and show how utilities are allocated between units. Half credit per results object present; entries without a numeric value count as absent. A unit that always draws a utility (pump, utility heat exchanger, compressor, distillation column, evaporator, flash; or a unit of unknown family, which may be one) scores 0 when neither object holds a numeric entry, unless the data show it draws nothing: every connected stream states a zero flow (an idle unit), or a flash's specs state it adiabatic (Q = 0). Each unit counts once.

CMP-UTIL-05 -- Agent selection parameters
: BioSTEAM picks the heat agent for each duty from the declared agents using the minimum approach temperature (the global HeatUtility.dT and any per-agent dT) and whether an agent is a fuel (isfuel); a different approach temperature selects a different steam pressure or refrigerant and changes the utility cost. No SFF field states them, so a flowsheet that needs heat agents scores 0.

### System and TEA settings

| ID | Criterion | Weight | Criticality | Representable since |
| --- | --- | ---: | --- | --- |
| CMP-SYS-01 | Annual operating hours | 3 | costing | never |
| CMP-SYS-02 | TEA parameter set | 5 | costing | never |
| CMP-SYS-03 | Convergence settings | 1 | supplementary | never |
| CMP-SYS-04 | Simulator and model provenance | 1 | supplementary | 0.0.6 |

CMP-SYS-01 -- Annual operating hours
: The TEA's operating days (or hours) per year convert hourly flows into annual costs and revenues. No SFF field states them.

CMP-SYS-02 -- TEA parameter set
: A BioSTEAM TEA needs fourteen groups of assumptions: the currency; the dollar (cost) year every price and cost is expressed in; the target product whose selling price the TEA solves for; the plant's start year and duration; the internal rate of return; income tax; the depreciation schedule; the construction schedule; the startup period and fractions; working capital; the fixed-operating-cost model; the capital-cost model (a Lang factor or DPI/TDC/FCI factors, with the OSBL units and TEA class it is applied to); financing; and the price book (bst.stream\_prices, which also prices zero-priced utility ports). SFF can state three: TEA\_currency, TEA\_year -- the cost year, per the schema, not the plant's start year -- and the target products (metadata.products, non-empty, every stream\_id a declared boundary outlet), so at most three of fourteen are available. A currency counts only as a three-capital-letter code (USD, EUR), and a year only as an integer in \[1900, 2100\]: "x", "dollars", 99999 or 1.5 state nothing a TEA can use.

CMP-SYS-03 -- Convergence settings
: The recycle-convergence method (Wegstein, Aitken, fixed-point, ...), the molar and temperature tolerances and the iteration limit decide whether, and to what, a recycle-heavy flowsheet converges. No SFF field states them.

CMP-SYS-04 -- Simulator and model provenance
: Unit models and defaults change between BioSTEAM releases, so the simulator name and version, and ideally the exact commits or versions of the simulator and flowsheet-model packages, identify which behaviour the recorded numbers came from. Representable in full from 0.0.6, when the reproducibility block added pinned packages.

### Integrity (validator-derived)

| ID | Criterion | Weight | Criticality | Representable since |
| --- | --- | ---: | --- | --- |
| CMP-INT-01 | Schema conformance | 2 | integrity | all versions |
| CMP-INT-02 | Cross-references resolve | 2 | integrity | all versions |
| CMP-INT-04 | Quantity units declared | 3 | essential | all versions |

CMP-INT-01 -- Schema conformance
: A file that fails the JSON Schema may have any field above in an unexpected shape, so no reader can rely on the information it appears to carry.

CMP-INT-02 -- Cross-references resolve
: The validator's XREF-01 aggregate fails when any referential check fails (a stream to an unknown unit, a component missing from the chemicals, a utility result naming no agent); information behind a dangling reference cannot be used. Not applicable to a document with neither units nor streams.

CMP-INT-04 -- Quantity units declared
: Without units every number is ambiguous (K or degC, kg/hr or kmol/hr), and a re-import must convert each value into BioSTEAM's internal units. Current-shape files declare units once in quantity\_units\_global, and a unit is only useful if it parses: the score is 1 when the validator's QU-01 (every quantity field has a quantity\_units\_global alias) and QU-02 (every quantity-unit string is parseable) both pass, 0.5 when exactly one does (a QU-02 result that is missing earns nothing), and 0 otherwise. Legacy files carry units inline on each quantity, so there the score is the fraction of quantities carrying units, where a bare numeric design result (no inline units at all) counts as a quantity without units. Not applicable to a document with neither units nor streams.

### Exclusions (deliberately not scored)

| ID | Kind | Information | Why it is not scored |
| --- | --- | --- | --- |
| EXC-01 | `not_capturable` | Process specifications | Specifications attached with add\_specification and system-level bounded specifications (adjust a flow until a titer or purity is met) are arbitrary Python closures. Only the converged design-point values they produced are capturable in static JSON. |
| EXC-02 | `not_capturable` | Custom unit-class behaviour | Biorefinery-specific subclasses override \_run, \_design and \_cost with arbitrary code. SFF captures them by reference (class name and source URL in design\_simulation\_method) and through the embedded reproducibility recipe, not declaratively. |
| EXC-03 | `not_capturable` | Kinetic rate laws and property callables | Kinetic reactor models and user-defined property functions (callables of temperature and pressure) are code, not data, and cannot be represented in static JSON. |
| EXC-04 | `out_of_scope` | AgileSystem multi-mode operation | A BioSTEAM AgileSystem runs one flowsheet through several operation modes (seasonal feedstocks, campaigns), each a separate steady state with its own parameters and operating hours, and costs the equipment for the most demanding mode. An SFF file records one design point, so multi-mode operation is out of scope; each mode can be exported as its own flowsheet. |
| EXC-05 | `out_of_scope` | LCA characterization factors | Characterization factors (for example global-warming potential per kg of each feed, product and utility) drive BioSTEAM's life-cycle assessment, not the rebuild, simulation, costing or TEA this score measures, so they are out of scope. |
| EXC-06 | `undetectable_need` | Property overrides on database chemicals | A model may override database properties of a chemical the database does resolve (Hf, Cn, V, LHV, or copy\_models\_from another chemical); re-importing it by registry id or name then silently rebuilds it with the database values, changing heats of reaction, boiler duties and densities. Nothing in an SFF file marks a chemical as overridden, so the need cannot be detected and no population defined; scoring it would need per-chemical property fields in SFF. |
| EXC-07 | `undetectable_need` | Non-default phase-equilibrium parameters for database chemicals | Binary interaction parameters, or UNIFAC / Dortmund group assignments that differ from the database's, change activity coefficients and so every VLE / LLE split. For database-resolvable chemicals nothing in the file says whether defaults were replaced, so the need is undetectable. For custom chemicals outside the database the same information is part of the property data CMP-THM-05 already scores. |
| DER-01 | `derived` | Recycle structure and simulation order | Recycle loops, tear streams and unit simulation order are recomputed from the topology by System.from\_units, so they are scored through CMP-TOP-01 and CMP-TOP-02 rather than separately. |
| DER-02 | `derived` | Facility designation | Whether a unit is a facility (boiler-turbogenerator, cooling tower, process-water center, ...) follows from its class, which CMP-UOP-01 already scores. |
| DER-03 | `derived` | Joint bare-module factor | The combined bare-module factor of a cost item is the ratio of its installed to its purchase cost, both scored by CMP-COST-01 and CMP-COST-02. |

<!-- END GENERATED: completeness criteria -->

## Extending the catalogue

1. **Add the entry to `criteria.yaml`** with all eight keys (`id`, `category`,
   `title`, `weight`, `criticality`, `representable_since`, `rationale`,
   `sff_paths`). The loader rejects unknown or missing keys, duplicate ids,
   unknown categories and non-positive weights. Define the criterion's
   population by need, never by whether the data are present. Information
   deliberately left out goes in `exclusions` as an `EXC-`/`DER-` entry with a
   kind.
2. **Implement the scorer** as a pure `_score_<area>_<nn>(sc)` function in
   `_completeness.py` that returns `(score or None, message, missing)`. Judge
   values with the shared validity predicates, and award derived credit only
   through `DERIVED_CREDIT` with a closed balance. Register the function in
   `_CRITERIA`, in the same order as the YAML; a Tier 1 test pins the ids and
   their order.
3. **Test it** in `tests/tier1/test_completeness.py`. The coverage meta-test
   requires every new helper to be named there. Make sure the new criterion
   survives the deletion-monotonicity and junk-versus-absence tests.
4. **Regenerate this page's tables** with
   `python pisces_sff/completeness/_completeness.py --render-docs`, and replace
   the text between the `BEGIN GENERATED` / `END GENERATED` markers with the
   output. A Tier 1 test fails until the page matches.
