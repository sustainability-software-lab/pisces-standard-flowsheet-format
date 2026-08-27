# Two layers, one module (`pisces_sff/validate/_validate.py`)

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

```{raw} html
<figure class="viz-fig viz-fig--wide">
  <svg viewBox="0 0 760 250" role="img" aria-labelledby="viz-pipe-t">
    <title id="viz-pipe-t">One call runs two layers — the structural schema gate, then 48 semantic checks — returning findings graded error, warning, or info; only error-severity failures gate conformance.</title>
    <path class="viz-node" d="M 24 92 h 88 l 18 18 v 60 h -106 z"/>
    <path class="viz-edge" d="M 112 92 v 18 h 18" stroke-width="1"/>
    <text class="t-mono" x="77" y="142" text-anchor="middle">SFF file</text>
    <line class="viz-edge" x1="130" y1="135" x2="168" y2="135"/>
    <path class="viz-head" d="M 168 130 l 10 5 l -10 5 z"/>
    <rect class="viz-node" x="180" y="78" width="204" height="114" rx="8"/>
    <text x="282" y="103" text-anchor="middle" font-weight="600">Layer 1 — structural gate</text>
    <text class="t-mono t-small" x="282" y="124" text-anchor="middle">validate_json_against_schema()</text>
    <text class="t-muted t-small" x="282" y="148" text-anchor="middle">types · required · enums</text>
    <text class="t-muted t-small" x="282" y="164" text-anchor="middle">0.0.12 declarative constraints</text>
    <line class="viz-edge" x1="384" y1="135" x2="422" y2="135"/>
    <path class="viz-head" d="M 422 130 l 10 5 l -10 5 z"/>
    <rect class="viz-node--em" x="434" y="78" width="176" height="114" rx="8"/>
    <text x="522" y="106" text-anchor="middle" font-weight="600">Layer 2 — semantic</text>
    <text x="522" y="128" text-anchor="middle">48 catalogued checks</text>
    <text class="t-mono t-small" x="522" y="152" text-anchor="middle">sff_checks.md</text>
    <path class="viz-edge" d="M 610 110 C 630 100, 640 92, 654 88"/>
    <path class="viz-head" d="M 652 83 l 11 4 l -9 7 z"/>
    <line class="viz-edge" x1="610" y1="135" x2="654" y2="135"/>
    <path class="viz-head" d="M 654 130 l 10 5 l -10 5 z"/>
    <path class="viz-edge" d="M 610 160 C 630 170, 640 178, 654 182"/>
    <path class="viz-head" d="M 654 176 l 9 7 l -11 4 z"/>
    <rect class="viz-node--heavy" x="666" y="72" width="72" height="26" rx="4"/>
    <text class="t-mono t-small t-copper" x="702" y="89" text-anchor="middle">error</text>
    <rect class="viz-node" x="666" y="122" width="72" height="26" rx="4"/>
    <text class="t-mono t-small" x="702" y="139" text-anchor="middle">warning</text>
    <rect class="viz-node" x="666" y="170" width="72" height="26" rx="4"/>
    <text class="t-mono t-small" x="702" y="187" text-anchor="middle">info</text>
    <path class="viz-edge viz-edge--dashed" d="M 77 92 C 77 34, 500 24, 520 72"/>
    <path class="viz-head" d="M 514 66 l 8 10 l -12 1 z"/>
    <text class="t-muted t-small" x="300" y="30" text-anchor="middle">schema-invalid files still run the structural checks</text>
    <text class="t-muted t-small" x="738" y="236" text-anchor="end">only error-severity failures gate conformance</text>
  </svg>
  <figcaption class="viz-caption">One call runs both layers, returning findings graded <span class="viz-mono">error</span>&nbsp;·&nbsp;<span class="viz-mono">warning</span>&nbsp;·&nbsp;<span class="viz-mono">info</span>.</figcaption>
</figure>
```

`is_valid` is `False` only if the schema gate failed, or some check produced
an `error`-severity `fail`; a `warning` or `info` finding never makes a file
non-conforming. A `skip` is not a silent pass — it means the check's inputs
were absent from the file, so there was nothing for the check to evaluate.
