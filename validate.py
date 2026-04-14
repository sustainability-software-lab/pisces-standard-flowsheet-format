# -*- coding: utf-8 -*-
# Code to export flowsheets from multiple tools into a standardized JSON format.
# Copyright (C) 2025-, Sarang S. Bhagwat <sarangbhagwat.developer@gmail.com>
# 
# This module is under the MIT open-source license. See 
# https://github.com/sustainability-software-lab/pisces-standard-flowsheet-format/blob/main/LICENSE
# for license details.

import json
from pathlib import Path
from typing import Any, Tuple

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError


def validate_json_against_schema(
    json_file,
    schema_file,
):
    """
    Validate a JSON file against a JSON Schema file.

    Returns:
        (is_valid, errors)
        - is_valid: True if the JSON adheres to the schema
        - errors: list of human-readable validation errors
    """
    json_file = Path(json_file)
    schema_file = Path(schema_file)

    with json_file.open("r", encoding="utf-8") as f:
        data: Any = json.load(f)

    with schema_file.open("r", encoding="utf-8") as f:
        schema: dict[str, Any] = json.load(f)

    try:
        validator = Draft7Validator(schema)
    except SchemaError as e:
        return False, [f"Invalid schema: {e.message}"]

    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))

    if not errors:
        return True, []

    formatted_errors = []
    for err in errors:
        path = ".".join(str(p) for p in err.path) or "<root>"
        formatted_errors.append(f"{path}: {err.message}")

    return False, formatted_errors
