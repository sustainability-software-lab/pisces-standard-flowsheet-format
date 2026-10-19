# Validation & Checks

SFF files are validated in two layers, both housed in a single module,
`pisces_sff/validate/_validate.py`. This section covers those two layers, the catalogue
of semantic checks behind them, and the machine-verified provenance tags.
It closes with the completeness scorer, which measures how much of what BioSTEAM
needs to rebuild a flowsheet a file actually states, not whether the file conforms.

```{toctree}
:maxdepth: 2

two_layers
catalogue
tags
completeness
```
