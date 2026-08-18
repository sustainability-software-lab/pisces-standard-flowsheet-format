// Entry point for the docs "Visualize Schema" page bundle.
// Built locally with esbuild (see package.json in this directory); the outputs
// docs/_static/jsoncrack/sff-viz.{js,css} are COMMITTED. Rebuild only when
// bumping jsoncrack-react -- see README.md alongside this file.
import React, { useEffect, useState } from "react";
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

function App({ json }) {
  const [theme, setTheme] = useState(currentTheme);
  useEffect(() => {
    // Follow the navbar theme switcher live.
    const observer = new MutationObserver(() => setTheme(currentTheme()));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => observer.disconnect();
  }, []);
  return (
    <JSONCrack
      json={json}
      theme={theme}
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
    container.innerHTML = ""; // remove the "Loading schema..." placeholder
    createRoot(container).render(<App json={schema} />);
  } catch (err) {
    showError(container, err instanceof Error ? err.message : String(err));
  }
}

main();
