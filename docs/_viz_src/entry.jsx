// Entry point for the docs "Visualize Schema" page bundle.
// Built locally with esbuild (see package.json in this directory); the outputs
// docs/_static/jsoncrack/sff-viz.{js,css} are COMMITTED. Rebuild only when
// bumping jsoncrack-react -- see README.md alongside this file.
import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { JSONCrack } from "jsoncrack-react";
import "jsoncrack-react/style.css";

// docs/conf.py copies pisces_sff/schema/sff_schema.json next to this bundle in
// _static/jsoncrack/ at every docs build. Resolve it from this script's own
// URL (document.currentScript is set for classic deferred scripts), so the
// lookup is independent of where the page sits in the site tree.
const SCHEMA_URL = new URL("sff_schema.json", document.currentScript.src).href;

// pydata-sphinx-theme resolves the visitor's choice into
// html[data-theme="light"|"dark"]; fall back to the OS preference if we ever
// observe a transient "auto".
function currentTheme() {
  const t = document.documentElement.getAttribute("data-theme");
  if (t === "dark") return "dark";
  if (t === "light") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

// ---- Collapse state -------------------------------------------------------
// The component's controlled `collapsedPaths` prop expects each JSONPath
// serialized exactly as JSON.stringify(path) (verified from the library's
// dist source: pathKey === JSON.stringify; membership is a Set of these).
const pathKey = (path) => JSON.stringify(path);

// Collect the JSONPath of every container value that renders a collapse
// chevron: object/array values with at least one entry, reached via a string
// object key. Array items themselves (numeric path tail) render as nodes
// WITHOUT a chevron -- putting them in the collapsed set would hide them with
// no affordance to ever reveal them -- so we recurse through them but never
// collect them.
function enumerateCollapsiblePaths(value, path = [], out = []) {
  if (value === null || typeof value !== "object") return out;
  const entries = Array.isArray(value)
    ? value.map((v, i) => [i, v])
    : Object.entries(value);
  for (const [key, child] of entries) {
    if (child === null || typeof child !== "object") continue;
    const childPath = [...path, key];
    const size = Array.isArray(child)
      ? child.length
      : Object.keys(child).length;
    if (typeof key === "string" && size > 0) out.push(childPath);
    enumerateCollapsiblePaths(child, childPath, out);
  }
  return out;
}

// Initial view: everything collapsed except the root (never in the set: []
// is a prefix of every path and would blank the graph) and "properties", so
// the top-level SFF section nodes are visible-but-collapsed on load.
function initialCollapsedPaths(schema) {
  return enumerateCollapsiblePaths(schema)
    .filter((p) => !(p.length === 1 && p[0] === "properties"))
    .map(pathKey);
}

function App({ json, initialCollapsed }) {
  const [theme, setTheme] = useState(currentTheme);
  const [collapsedPaths, setCollapsedPaths] = useState(initialCollapsed);
  useEffect(() => {
    // Follow the navbar theme switcher live.
    const observer = new MutationObserver(() => setTheme(currentTheme()));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => observer.disconnect();
  }, []);
  // One chevron click toggles exactly that path. Every not-yet-visited
  // container is already in the set, so expanding a node reveals children
  // that are themselves collapsed -- iterative deepening for free.
  const handleToggleCollapse = useCallback((path) => {
    const key = pathKey(path);
    setCollapsedPaths((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  }, []);
  return (
    <JSONCrack
      json={json}
      theme={theme}
      collapsedPaths={collapsedPaths}
      onToggleCollapse={handleToggleCollapse}
      onParse={(graph) =>
        // Verification hook: the schema must stay under the default
        // maxRenderableNodes (1500) or the canvas renders a fallback instead.
        console.log(
          `SFF schema graph: ${graph.nodes.length} nodes (render cap 1500)`
        )
      }
    />
  );
}

function showError(container, message) {
  container.innerHTML = "";
  const box = document.createElement("div");
  box.className = "sff-viz-error";
  box.textContent =
    "The schema graph could not be displayed (" +
    message +
    "). The raw schema file is linked from the Full Schema page.";
  container.appendChild(box);
}

async function main() {
  const container = document.getElementById("sff-schema-viz");
  if (!container) return;
  try {
    const resp = await fetch(SCHEMA_URL);
    if (!resp.ok) throw new Error("HTTP " + resp.status + " fetching the schema");
    const schema = await resp.json();
    const initialCollapsed = initialCollapsedPaths(schema);
    container.innerHTML = ""; // remove the "Loading schema..." placeholder
    createRoot(container).render(
      <App json={schema} initialCollapsed={initialCollapsed} />
    );
  } catch (err) {
    showError(container, err instanceof Error ? err.message : String(err));
  }
}

main();
