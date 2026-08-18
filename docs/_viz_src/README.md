# Visualize Schema — bundle build recipe

Source for the committed pre-built bundle behind the docs **Visualize Schema**
page: `docs/_static/jsoncrack/sff-viz.js` + `sff-viz.css`.

Rebuild **only** when bumping `jsoncrack-react` (or the react/esbuild pins):

    cd docs/_viz_src
    npm install
    npm run build

Requires any Node.js >= 18 (build-time only — Read the Docs never runs Node;
it just serves the committed outputs). Commit the regenerated outputs and
`package-lock.json` together with the pin change.

The schema JSON the page displays is **not** part of this bundle:
`docs/conf.py` copies `pisces_sff/schema/sff_schema.json` into
`docs/_static/jsoncrack/sff_schema.json` (gitignored) at every docs build, and
the bundle fetches it at view time — the graph can never drift from the
committed spec.

This `package.json` is scoped entirely to this docs asset. It is **not**
packaging metadata for `pisces_sff`, which deliberately has none.
