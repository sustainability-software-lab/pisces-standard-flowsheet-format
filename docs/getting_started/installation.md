# Installation

## For consumers of SFF files

SFF files are plain JSON validated against a JSON Schema (draft-07) — the
**format** itself is simulator-agnostic and needs nothing beyond `jsonschema`
to check a file against `pisces_sff/schema/sff_schema.json`.

Using the reference package's own validator functions is a different story,
though. `import pisces_sff` (and therefore
`from pisces_sff import validate_json_against_schema` or
`validate_flowsheet_against_SFF`) currently loads the full `biosteam` /
`thermosteam` / `pyyaml` stack at import time, because `pisces_sff/__init__.py`
eagerly imports the exporter and harness modules alongside the validator. So
calling these functions through the public `pisces_sff` API requires the same
reference `HP_2024` conda environment used for exporting — not just
`jsonschema` and `numpy`. A `jsonschema`-only environment can validate an SFF
file directly by loading `pisces_sff/_validate.py` without importing the
`pisces_sff` package (see `tests/tier3/test_metadata_schema.py` for the
pattern), but that is a workaround, not the documented entry point.

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
