import json
import importlib.resources


def validate_sff(data: dict, schema_version: str = "0.0.2") -> None:
    """Validate an SFF dict against the versioned JSON schema.

    Args:
        data: An SFF document as a Python dict.
        schema_version: Schema version string, e.g. "0.0.2". Defaults to "0.0.2".

    Raises:
        jsonschema.ValidationError: If the data does not conform to the schema.
        FileNotFoundError: If the requested schema version file does not exist.
    """
    import jsonschema

    schema_filename = f"schema_v_{schema_version}.json"
    schemas_pkg = importlib.resources.files("pisces_sff.schemas")
    schema_file = schemas_pkg / schema_filename
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)
