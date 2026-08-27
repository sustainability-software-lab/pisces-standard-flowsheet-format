# Tags

The optional `metadata.tags` array records machine-verified provenance/quality
tags. `evaluate_sff_tags(file, *, run_harness=False, ...)` computes the verdict
for every tag and returns it as a per-tag `{earned, declared, blocking}` dict.

```{raw} html
<div class="viz-fig">
  <div class="viz-tags">
    <div class="viz-tag"><code>exported-from-simulator</code> <span class="viz-chip">static</span><span class="m">Produced by a direct simulator export; passes the export-quality check subset.</span></div>
    <div class="viz-tag-link"><svg viewBox="0 0 40 16" width="40" height="16" role="img" aria-label="verified via the static lane"><title>verified via the static lane</title><line class="viz-edge" x1="0" y1="8" x2="30" y2="8"/><path class="viz-head" d="M 30 3 l 10 5 l -10 5 z"/></svg></div>
    <div class="viz-lane viz-lane--static"><h4>Static lane</h4>Verified by passing a tag-specific subset of the 48 checks with no warning-severity findings — fast, no simulation.</div>
    <div class="viz-tag"><code>extracted-from-prose</code> <span class="viz-chip">static</span><span class="m">Extracted from text (e.g., a publication) and meets the substantive floor: units and streams present and well-identified (<code>UNIT-10</code>, <code>STR-14</code>).</span></div>
    <div class="viz-tag-link"><svg viewBox="0 0 40 16" width="40" height="16" role="img" aria-label="verified via the static lane"><title>verified via the static lane</title><line class="viz-edge" x1="0" y1="8" x2="30" y2="8"/><path class="viz-head" d="M 30 3 l 10 5 l -10 5 z"/></svg></div>
    <div class="viz-tag"><code>extracted-from-image</code> <span class="viz-chip">static</span><span class="m">As above, but extracted from figures/diagrams.</span></div>
    <div class="viz-tag-link"><svg viewBox="0 0 40 16" width="40" height="16" role="img" aria-label="verified via the static lane"><title>verified via the static lane</title><line class="viz-edge" x1="0" y1="8" x2="30" y2="8"/><path class="viz-head" d="M 30 3 l 10 5 l -10 5 z"/></svg></div>
    <div class="viz-tag"><code>reproducible</code> <span class="viz-chip viz-chip--copper">harness</span><span class="m">Re-running the embedded recipe in its pinned environment regenerates the file within the declared tolerance (<code>metadata.reproducibility.comparison_rtol</code>).</span></div>
    <div class="viz-tag-link"><svg viewBox="0 0 40 16" width="40" height="16" role="img" aria-label="verified via the harness lane"><title>verified via the harness lane</title><line class="viz-edge" x1="0" y1="8" x2="30" y2="8"/><path class="viz-head" d="M 30 3 l 10 5 l -10 5 z"/></svg></div>
    <div class="viz-lane viz-lane--harness"><h4>Harness lane</h4>Re-run the embedded recipe in its pinned environment → deep-compare within <code>comparison_rtol</code>.</div>
  </div>
  <div class="viz-caption">Either lane resolves every declared tag to one of:</div>
  <div class="viz-outcomes">
    <div class="viz-outcome viz-outcome--earned"><strong>earned</strong> — the tag is trustworthy: it was verified against the file itself.</div>
    <div class="viz-outcome viz-outcome--unearned"><strong>declared but unearned</strong> — a validation <em>error</em> (<code>TAG-01</code>).</div>
  </div>
</div>
```

Three tags are **static**: `exported-from-simulator`, `extracted-from-prose`,
and `extracted-from-image`. A file earns a static tag by passing a
tag-specific subset of the catalogue's checks with no `warning`-severity
finding — fast, and requiring no simulation.
The tag names, subsets, and tolerated-skip policies live in one committed
registry file, `pisces_sff/tags/tags.yaml`, which the validator reads at
evaluation time.

The fourth tag, `reproducible`, is a **harness** tag. It is earned only via
`verify_reproducible(file, *, rtol=None, ...) -> (matches, diffs)`, which
reconstructs the flowsheet's embedded reproducibility recipe, re-runs the
export inside the pinned conda environment, and deep-compares the result
against the original file. Because that path means provisioning an
environment and re-running a simulation, it is heavy and opt-in rather than
something every validation run performs.

The `reproducible` tag rests on the flowsheet carrying its own instructions for
rebuilding itself. A three-file **model recipe** — `load.py` (code + export
flags), `environment.yaml` (pinned conda environment), and
`extended_metadata.yaml` (human-authored provenance) — drives a harness export,
and the exported file embeds that same recipe verbatim, with SHA-256 digests
(`MET-07`), so `verify_reproducible` can re-run it and close the loop:

```{raw} html
<figure class="viz-fig viz-fig--wide">
  <svg viewBox="0 0 760 310" role="img" aria-labelledby="viz-loop-t">
    <title id="viz-loop-t">The three-file model recipe drives a harness export, the exported SFF file embeds that same recipe with SHA-256 digests, and verify_reproducible can re-run the embedded recipe — closing the loop.</title>
    <rect class="viz-edge viz-edge--dashed" x="20" y="34" width="204" height="176" rx="10" stroke-width="1"/>
    <text class="t-muted t-small" x="122" y="52" text-anchor="middle" font-weight="600">model recipe</text>
    <rect class="viz-node" x="34" y="62" width="176" height="38" rx="6"/>
    <text class="t-mono t-small" x="42" y="78">load.py</text>
    <text class="t-muted t-small" x="42" y="93">code + export flags</text>
    <rect class="viz-node" x="34" y="108" width="176" height="38" rx="6"/>
    <text class="t-mono t-small" x="42" y="124">environment.yaml</text>
    <text class="t-muted t-small" x="42" y="139">pinned env</text>
    <rect class="viz-node" x="34" y="154" width="176" height="38" rx="6"/>
    <text class="t-mono t-small" x="42" y="170">extended_metadata.yaml</text>
    <text class="t-muted t-small" x="42" y="185">human-authored provenance</text>
    <line class="viz-edge" x1="224" y1="122" x2="262" y2="122"/>
    <path class="viz-head" d="M 262 117 l 10 5 l -10 5 z"/>
    <rect class="viz-node--heavy" x="274" y="92" width="150" height="60" rx="8"/>
    <text x="349" y="116" text-anchor="middle" font-weight="600">harness</text>
    <text class="t-muted t-small" x="349" y="136" text-anchor="middle">provisions the pinned env</text>
    <line class="viz-edge viz-edge--accent" x1="424" y1="122" x2="486" y2="122"/>
    <path class="viz-head viz-head--accent" d="M 486 117 l 10 5 l -10 5 z"/>
    <text class="t-muted t-small" x="455" y="110" text-anchor="middle">export runs</text>
    <text class="t-muted t-small" x="455" y="146" text-anchor="middle">inside it</text>
    <path class="viz-node--em" d="M 498 34 h 214 l 24 24 v 186 h -238 z"/>
    <path class="viz-edge" d="M 712 34 v 24 h 24" stroke-width="1"/>
    <text class="t-mono" x="617" y="60" text-anchor="middle" font-weight="600">SFF file</text>
    <rect class="viz-edge viz-edge--dashed" x="512" y="74" width="210" height="150" rx="8" stroke-width="1"/>
    <text class="t-mono t-small" x="617" y="92" text-anchor="middle">metadata.reproducibility</text>
    <rect class="viz-node" x="526" y="102" width="182" height="26" rx="5"/>
    <text class="t-mono t-small" x="534" y="119">load.py</text>
    <rect class="viz-node" x="526" y="134" width="182" height="26" rx="5"/>
    <text class="t-mono t-small" x="534" y="151">environment.yaml</text>
    <rect class="viz-node" x="526" y="166" width="182" height="26" rx="5"/>
    <text class="t-mono t-small" x="534" y="183">extended_metadata.yaml</text>
    <text class="t-small" x="617" y="214" text-anchor="middle">+ SHA-256 digests (<tspan class="t-mono">MET-07</tspan>)</text>
    <path class="viz-edge viz-edge--accent" d="M 600 260 C 500 300, 220 300, 126 214"/>
    <path class="viz-head viz-head--accent" d="M 121 220 l -1 -12 l 11 5 z"/>
    <text class="t-small t-accent" x="380" y="296" text-anchor="middle"><tspan class="t-mono">verify_reproducible()</tspan> re-runs the embedded recipe</text>
  </svg>
  <figcaption class="viz-caption">The three-file recipe drives the export; the file embeds the recipe with digests; <span class="viz-mono">verify_reproducible()</span> re-runs it to close the loop.</figcaption>
</figure>
```
