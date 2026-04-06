# Extending pisces-sff

This page explains how to add support for a new process simulator (export and/or import), and how to use the shared utilities in `pisces_sff.core`.

---

## Package Architecture

```
pisces_sff/
├── __init__.py                    # public API
├── base.py                        # SFFExporter and SFFImporter Protocols
├── core/
│   ├── validate.py                # validate_sff(data, schema_version)
│   └── io.py                      # write_sff(...), read_sff(...)
└── biosteam/                      # BioSTEAM adapter (reference implementation)
    ├── __init__.py
    └── export.py
```

Adding a new simulator = adding a new subpackage under `pisces_sff/`. The Protocols in `base.py` document the expected interface; `core.io` provides shared file I/O so every adapter writes files the same way.

---

## The Protocols

`pisces_sff.base` defines two `typing.Protocol` classes:

```python
from pisces_sff import SFFExporter, SFFImporter
```

### `SFFExporter`

```python
class SFFExporter(Protocol):
    def export(self, system: object, file_path: Union[str, Path]) -> None: ...
```

Implement this for any simulator you want to export *from*. The `system` argument is whatever the simulator uses as its top-level flowsheet or system object.

### `SFFImporter`

```python
class SFFImporter(Protocol):
    def load(self, file_path: Union[str, Path]) -> object: ...
```

Implement this when you want to load an SFF file *into* a simulator. No import adapters exist yet — the Protocol is defined to ensure future implementations use a consistent interface.

---

## Adding a New Export Adapter

### 1. Create a subpackage

```
pisces_sff/
└── mysimu/
    ├── __init__.py
    └── export.py
```

### 2. Implement the export function in `export.py`

```python
from __future__ import annotations
from pathlib import Path
from typing import Union

from pisces_sff.core.io import write_sff


def export_mysimu_flowsheet_sff(system, file_path: Union[str, Path]) -> None:
    """Export a MySimu system to an SFF JSON file."""
    units = []
    streams = []
    # ... extract units and streams from your simulator's API ...

    flowsheet = {
        "metadata": { ... },
        "units": units,
        "streams": streams,
        "heat_utilities": [],
        "power_utilities": [],
        "other_utilities": [],
    }
    write_sff(flowsheet, file_path)   # validates against schema by default
```

### 3. Re-export from `__init__.py`

```python
from .export import export_mysimu_flowsheet_sff

__all__ = ["export_mysimu_flowsheet_sff"]
```

### 4. Add an optional dependency in `pyproject.toml`

```toml
[project.optional-dependencies]
mysimu = ["mysimu-python-package"]
```

---

## Shared Utilities

### `pisces_sff.core.io`

| Function | Purpose |
|----------|---------|
| `write_sff(data, file_path, validate=True, schema_version="0.0.2")` | Write SFF dict to JSON, optionally validating first |
| `read_sff(file_path)` | Read an SFF JSON file and return the parsed dict |

```python
from pisces_sff.core.io import write_sff, read_sff

# Read an existing SFF file
flowsheet = read_sff("my_process.json")

# Write (with validation)
write_sff(flowsheet, "output.json")

# Write without validation
write_sff(flowsheet, "output.json", validate=False)
```

### `pisces_sff.core.validate`

```python
from pisces_sff import validate_sff

validate_sff(data)                          # validate against latest schema (v0.0.2)
validate_sff(data, schema_version="0.0.1")  # validate against a specific version
```

Raises `jsonschema.ValidationError` with a descriptive message on failure.

---

## Schema Conformance Note

The BioSTEAM adapter (`pisces_sff.biosteam`) currently calls `write_sff(..., validate=False)` because its output predates full schema alignment (the required `metadata` and `utilities` top-level keys are not yet populated). New adapters should target full schema conformance from the start and call `write_sff` with the default `validate=True`.

---

## Checking Protocol Conformance at Runtime

Because the Protocols are `@runtime_checkable`, you can verify conformance with `isinstance`:

```python
from pisces_sff import SFFExporter
from pisces_sff.biosteam import export_biosteam_flowsheet_sff

# Functions don't satisfy structural protocols this way — wrap in a class if needed:
class BioSTEAMExporter:
    def export(self, system, file_path):
        export_biosteam_flowsheet_sff(system, file_path)

assert isinstance(BioSTEAMExporter(), SFFExporter)  # True
```
