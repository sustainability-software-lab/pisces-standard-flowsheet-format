# Visualize Schema

This page renders the current SFF JSON Schema
(`pisces_sff/schema/sff_schema.json`) as an interactive node graph, powered by
[JSON Crack](https://github.com/AykutSarac/jsoncrack.com). Drag to pan, scroll
or use the on-canvas controls to zoom. The graph is generated from the exact
committed schema file at docs-build time, so it can never drift from the spec.

```{raw} html
<style>
  #sff-schema-viz {
    height: 70vh;
    min-height: 480px;
    width: 100%;
    border: 1px solid var(--pst-color-border);
    border-radius: 0.25rem;
    overflow: hidden;
  }
  .sff-viz-loading,
  .sff-viz-error {
    padding: 1rem;
    color: var(--pst-color-text-muted);
  }
</style>
<link rel="stylesheet" href="_static/jsoncrack/sff-viz.css" />
<div id="sff-schema-viz">
  <p class="sff-viz-loading">Loading schema&hellip;</p>
</div>
<noscript>
  <p>This interactive view requires JavaScript. The raw schema file is
  available
  <a href="https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/pisces_sff/schema/sff_schema.json">on GitHub</a>.</p>
</noscript>
<script defer src="_static/jsoncrack/sff-viz.js"></script>
```

Looking for the raw schema file, or a previous version? See
[Full Schema](full_schema.md).
