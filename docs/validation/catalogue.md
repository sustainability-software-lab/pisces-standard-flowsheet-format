# The validation catalogue

Every requirement beyond raw schema shape — referential integrity, unit and
stream completeness, reproducibility integrity, and more — is catalogued in
[`sff_checks.md`](https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/sff_checks.md)
with a stable ID that both the schema and the validator cite, for example
`MET-01`, `MET-05`, `MET-06`, `STR-11`, `STR-12`, `UNIT-04`, and `UNIT-09`.

The 48 checks are organized into families keyed to the document's sections:

```{raw} html
<div class="viz-fig">
  <div class="viz-bar" role="img" aria-label="48 checks by family, proportional: MET 7, UNIT 10, STR 14 (largest), CHEM 5, UTIL 5, QU 4, XREF plus GRAPH plus TAG 3.">
    <span>MET 7</span><span>UNIT 10</span><span>STR 14</span><span>CHEM 5</span><span>UTIL 5</span><span>QU</span><span></span>
  </div>
  <div class="viz-caption">48 checks by family — rightmost segments: <span class="viz-mono">QU</span> 4 · <span class="viz-mono">XREF</span>/<span class="viz-mono">GRAPH</span>/<span class="viz-mono">TAG</span> 3.</div>
  <div class="viz-fams">
    <div class="viz-fam"><span class="c">MET</span><span class="n">7</span><span class="d">Metadata — <code>sff_version</code> is valid semver, feedstock and product references resolve to real streams, <code>TEA_year</code> is plausible, and embedded reproducibility content actually matches its SHA-256 digests.</span></div>
    <div class="viz-fam"><span class="c">UNIT</span><span class="n">10</span><span class="d">Units — unique IDs, no orphan units, utility results keyed to declared utilities, reactions with valid conversions and well-formed stoichiometry, and complete purchase-cost correlations.</span></div>
    <div class="viz-fam"><span class="c">STR</span><span class="n">14</span><span class="d">Streams — the largest family: unique IDs, endpoints that resolve to a unit or the boundary, exactly one topology role that agrees with the actual connectivity, composition fractions that sum to one, phase flows that sum to the stream total, and mass flow that agrees with molar flow × molar mass.</span></div>
    <div class="viz-fam"><span class="c">CHEM</span><span class="n">5</span><span class="d">Chemicals — unique IDs and indices, positive molar mass (a warning since 0.1.1), and formula-derived molar mass agreeing with the declared value.</span></div>
    <div class="viz-fam"><span class="c">UTIL</span><span class="n">5</span><span class="d">Utilities — unique IDs across all utility groups, valid compositions, physically sensible temperatures and pressures, and no utilities declared but never used.</span></div>
    <div class="viz-fam"><span class="c">QU</span><span class="n">4</span><span class="d">Quantity units — every quantity field resolves to a registered unit, unit strings actually parse, and aliases are globally unambiguous.</span></div>
    <div class="viz-fam"><span class="c">XREF</span><span class="n">2</span><span class="d">Cross-object &amp; graph (with <code>GRAPH</code>) — an aggregate referential-integrity gate over all the ID cross-references, and the requirement that the flowsheet has at least one boundary inlet and one boundary outlet — a process that consumes nothing and produces nothing is, at minimum, suspicious.</span></div>
    <div class="viz-fam"><span class="c">TAG</span><span class="n">1</span><span class="d">Tags — declared tags are verified, as described on the <a href="tags.html">Tags</a> page.</span></div>
  </div>
</div>
```

The [tag layer](tags.md) is backed by its own catalogued checks:
`MET-07` confirms that a flowsheet's embedded reproducibility content matches
its recorded digests; `UNIT-10` confirms that units are present and
well-identified, `STR-14` confirms that streams are present and identified;
and `TAG-01` is the aggregate
check that reports an `error` when a file declares a tag in `metadata.tags`
that it has not actually earned.
