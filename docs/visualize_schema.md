# Visualize Schema

This page renders the current SFF JSON Schema
(`pisces_sff/schema/sff_schema.json`) as an interactive node graph, powered by
[JSON Crack](https://github.com/AykutSarac/jsoncrack.com). Drag to pan, scroll
or use the on-canvas controls to zoom. The graph starts
collapsed: the schema root and its top-level sections are shown, and clicking
a row's chevron expands that container by exactly one level (newly revealed
containers start collapsed). Hover a node to see the schema `description` of
the field it represents; use the **Expand all** and **Reset view** buttons to
jump between the full graph and the initial view. The graph is generated from
the exact committed schema file at docs-build time, so it can never drift from
the spec.

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
  /* Hover tooltip (appended to <body> by the bundle; fixed + clamped). */
  .sff-viz-tooltip {
    position: fixed;
    z-index: 102;
    max-width: 28rem;
    padding: 0.375rem 0.625rem;
    font-size: 0.8125rem;
    line-height: 1.4;
    border: 1px solid var(--pst-color-border);
    border-radius: 0.25rem;
    background: var(--pst-color-background);
    color: var(--pst-color-text-base);
    box-shadow: 0 0.125rem 0.5rem rgba(0, 0, 0, 0.25);
    pointer-events: none;
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
