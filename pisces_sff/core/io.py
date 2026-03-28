import json
from pathlib import Path
from typing import Union


def write_sff(
    data: dict,
    file_path: Union[str, Path],
    validate: bool = True,
    schema_version: str = "0.0.2",
) -> None:
    """Write an SFF dict to a JSON file.

    Args:
        data: An SFF document as a Python dict.
        file_path: Destination path for the output JSON file.
        validate: If True (default), validate against the JSON schema before writing.
        schema_version: Schema version to validate against. Defaults to latest.

    Raises:
        jsonschema.ValidationError: If validate=True and the data fails schema validation.
    """
    if validate:
        from .validate import validate_sff
        validate_sff(data, schema_version=schema_version)
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)


def read_sff(file_path: Union[str, Path]) -> dict:
    """Read an SFF JSON file and return the parsed dict.

    Args:
        file_path: Path to an SFF JSON file.

    Returns:
        The SFF document as a Python dict.
    """
    with open(file_path) as f:
        return json.load(f)
