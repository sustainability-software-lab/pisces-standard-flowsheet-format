# Standard Flowsheet Format (SFF)

The [Standard Flowsheet Format (SFF)](https://sustainability-software-lab.github.io/pisces-standard-flowsheet-format/) is a JSON-based standard to represent chemical process flowsheets for interoperability across process simulators. Currently supports direct export from BioSTEAM and SuperPro Designer.

This format captures unit operations (including design and cost results, utility demands and production, reactions, and design input specifications), streams (material flows, phases, temperature, pressure, and source and sink unit operation ports), utilities (heating, cooling, power, combustion, and others), chemicals (registry IDs and user-defined properties), and metadata for source publication, flowsheet designers, TEA parameters, and process description.

The [documentation](https://sustainability-software-lab.github.io/pisces-standard-flowsheet-format/) includes installation instructions, tutorials for export and extending, and the full schema reference.

# Project PISCES

The SFF and associated export capabilities are used by [Project PISCES](https://projectpisces.org/) to generate a growing database of flowsheets and to train an LLM-based pipeline to extract flowsheets (as SFFs) from literature (as PDFs).

![Simplified visual representation of the SFF schema](https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/images/SFF_visual_representation.png)


