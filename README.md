# Standard Flowsheet Format (SFF)

The [Standard Flowsheet Format (SFF)](https://pisces-standard-flowsheet-format.readthedocs.io/) is a JSON-based standard to represent chemical process flowsheets for interoperability across process simulators. Currently supports direct export from BioSTEAM and SuperPro Designer.

This format captures unit operations (including design and cost results, utility demands and production, reactions, and design input specifications), streams (material flows, phases, temperature, pressure, and source and sink unit operation ports), utilities (heating, cooling, power, combustion, and others), chemicals (registry IDs and user-defined properties), and metadata for source publication, flowsheet designers, TEA parameters, and process description.

The [documentation](https://pisces-standard-flowsheet-format.readthedocs.io/) includes installation instructions, tutorials for export and extending, and the full schema reference.

## Project PISCES

The SFF and associated export capabilities are used by [Project PISCES](https://projectpisces.org/) to generate a growing database of flowsheets and to train an LLM-based pipeline to extract flowsheets (as SFFs) from literature (as PDFs).

## Simplified overview of the SFF schema

<a href="https://pisces-standard-flowsheet-format.readthedocs.io/en/latest/visualize_schema.html" target="_blank" rel="noopener">
  <img src="images/schema_viz_light.png#gh-light-mode-only" alt="Interactive visualization of the SFF schema" width="100%">
  <img src="images/schema_viz_dark.png#gh-dark-mode-only" alt="Interactive visualization of the SFF schema" width="100%">
</a>

_Click the graph to open the interactive [schema visualization](https://pisces-standard-flowsheet-format.readthedocs.io/en/latest/visualize_schema.html)._


