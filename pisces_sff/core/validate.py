import json
from pathlib import Path

_SCHEMA_DIR = Path(__file__).parent.parent.parent / "schema"


def validate_sff(data: dict, schema_version: str = "0.0.2") -> None:
    """Validate an SFF dict against the versioned JSON schema.

    Args:
        data: An SFF document as a Python dict.
        schema_version: Schema version string, e.g. "0.0.2". Defaults to latest.

    Raises:
        jsonschema.ValidationError: If the data does not conform to the schema.
        FileNotFoundError: If the requested schema version file does not exist.
    """
    import jsonschema

    schema_file = _SCHEMA_DIR / f"schema_v_{schema_version}.json"
    with open(schema_file) as f:
        schema = json.load(f)
    jsonschema.validate(data, schema)
