Standard Flowsheet Format (SFF): A JSON-based standard to represent chemical process flowsheets for interoperability across process simulators. Currently supports export from BioSTEAM. 
This format captures unit operations (including design and cost results, utility demands and production, reactions, and design input specifications), streams (material flows, phases, temperature, pressure, and source and sink unit operation ports), utilities (heating, cooling, power, combustion, and others), chemicals (registry IDs and user-defined properties), and metadata for source publication, flowsheet designers, TEA parameters, and process description.


![Simplified visual representation of the SFF schema](https://github.com/sarangbhagwat/SFF/blob/main/images/SFF_visual_representation.png)

## Schema Versions

| Version | File | Status |
|---------|------|--------|
| 0.0.3 | [schema_v_0.0.3.json](schema/schema_v_0.0.3.json) | **Latest** |
| 0.0.2 | [schema_v_0.0.2.json](schema/schema_v_0.0.2.json) | Deprecated (4 structural bugs — see PR description) |
| 0.0.1 | [schema_v_0.0.1.json](schema/schema_v_0.0.1.json) | Initial release |

