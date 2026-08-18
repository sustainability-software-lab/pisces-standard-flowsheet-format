# Installation

## For consumers of SFF files

SFF files are plain JSON validated against a JSON Schema (draft-07), so
reading or validating them does not require a process simulator at all. The
only dependencies you need are `jsonschema` (and `numpy`). Point
`validate_json_against_schema` or `validate_flowsheet_against_SFF` at any SFF
file plus `pisces_sff/schema/sff_schema.json` and you're set.

## For contributors / exporter use

The package is **not** pip-installable as committed — there is no
`pyproject.toml` or `setup.py` in this repo. Instead, it is used as a **live
editable clone**: the working tree itself resolves as `import pisces_sff`.
Exporting a flowsheet additionally needs `biosteam`/`thermosteam`; the
reference environment for this is the `HP_2024` conda env (Python 3.9).

The invocation pattern used throughout this repo for running against that
pinned environment calls the environment's Python directly, for example:

```bash
"C:/Users/saran/anaconda3/envs/HP_2024/python.exe" <script.py>
```
