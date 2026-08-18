# Visualize Schema

This page renders the current SFF JSON Schema
(`pisces_sff/schema/sff_schema.json`) as an interactive node graph, powered by
[JSON Crack](https://github.com/AykutSarac/jsoncrack.com). Drag to pan, scroll
or use the on-canvas controls to zoom. The graph is generated from the exact
committed schema file at docs-build time, so it can never drift from the spec.

```{raw} html
<style>
  #sff-schema-viz {
    position: relative;
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
  /* Overlay controls: top-right, opposite the library's bottom-left zoom
     controls (z-index 100). Pydata CSS variables follow the theme switcher. */
  .sff-viz-controls {
    position: absolute;
    top: 10px;
    right: 10px;
    z-index: 101;
    display: flex;
    gap: 0.375rem;
  }
  .sff-viz-controls button {
    padding: 0.25rem 0.625rem;
    font-size: 0.8125rem;
    border: 1px solid var(--pst-color-border);
    border-radius: 0.25rem;
    background: var(--pst-color-background);
    color: var(--pst-color-text-base);
    cursor: pointer;
  }
  .sff-viz-controls button:hover {
    background: var(--pst-color-surface);
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
