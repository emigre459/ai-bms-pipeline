from __future__ import annotations

from pathlib import Path
from typing import Any

LOGGER_NAME = "ai_bms_pipeline"
LOGGER_FORMAT = "%(asctime)s: %(levelname)s (%(name)s:%(module)s.%(funcName)s:L%(lineno)d) - %(message)s"
DATETIME_FORMAT = "%Y-%m-%d_T%H_%M_%S%Z"
MAX_CONCURRENT_LLM_TASKS = 50

# Taken from https://www.color-hex.com/color-palette/1041937
# <a target="_blank" href="https://icons8.com/icon/7880/location">Location</a> icon by <a target="_blank" href="https://icons8.com">Icons8</a>
BAD_TO_GOOD_HEX_TO_RGB_COLORS = {
    "af1c17": (175, 28, 23),  # 0-20
    "cf7673": (207, 118, 115),  # 20-40
    "a1a38c": (161, 163, 140),  # 40-60
    "8bd7b3": (139, 215, 179),  # 60-80
    "17af68": (23, 175, 104),  # 80-100
}


# ─── Schema validation ────────────────────────────────────────────────────────


def validate_against_schema(
    output: dict,
    schema_path: Path | str,
) -> list[str]:
    """Validate a JSON output dict against a project YAML schema file.

    Parses the YAML schema (same format used by conf/*.schema.yaml) and
    recursively walks the output to check:
      - required fields are present
      - values have the declared type (string, number, boolean, object, array, null)
      - enum constraints from schema comments are obeyed
      - nesting structure matches (objects and array items recurse correctly)

    Does not use any LLM. Returns a list of human-readable violation strings.
    An empty list means the output is valid against the schema.

    Lazy-imports image_ingest to avoid the circular import that would result
    from a top-level import (image_ingest imports config at module load time).
    """
    # Lazy import to avoid circular dependency (image_ingest imports config)
    from ai_bms_pipeline.image_ingest import (  # noqa: PLC0415
        _load_schema_text,
        _parse_object_schema,
        _schema_lines,
    )

    lines = _schema_lines(_load_schema_text(schema_path))
    # Parse without _require_all_object_properties so optional fields
    # are not incorrectly marked as required.
    schema, _ = _parse_object_schema(lines, 0, 0)
    return _check_node(output, schema, "$")


def _check_node(value: Any, schema: dict, path: str) -> list[str]:
    """Recursively validate *value* against a JSON Schema node.

    Returns a list of violation strings; empty means valid.
    """
    violations: list[str] = []

    # anyOf: value must satisfy at least one sub-schema (handles nullable types)
    if "anyOf" in schema:
        for sub in schema["anyOf"]:
            if not _check_node(value, sub, path):
                return []  # matched one branch — valid
        options = " | ".join(str(s.get("type", "?")) for s in schema["anyOf"])
        violations.append(f"{path}: {_repr(value)} does not match anyOf [{options}]")
        return violations

    expected_type = schema.get("type")

    # Type check
    if expected_type is not None:
        ok, err = _check_type(value, expected_type, path)
        if not ok:
            violations.append(err)
            return violations  # no point recursing into a wrong-typed value

    # Enum constraint
    if "enum" in schema and value not in schema["enum"]:
        violations.append(f"{path}: {value!r} is not one of {schema['enum']}")

    # Object: required fields + recurse on present properties
    if expected_type == "object" and isinstance(value, dict):
        properties: dict[str, Any] = schema.get("properties", {})
        for field in schema.get("required", []):
            if field not in value:
                violations.append(f"{path}.{field}: required field is missing")
        for field, field_schema in properties.items():
            if field in value:
                violations.extend(
                    _check_node(value[field], field_schema, f"{path}.{field}")
                )

    # Array: recurse on every item
    if expected_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value):
                violations.extend(_check_node(item, item_schema, f"{path}[{i}]"))

    return violations


def _check_type(value: Any, expected: str | list, path: str) -> tuple[bool, str]:
    """Return (is_valid, error_message). error_message is empty string when valid."""
    if isinstance(expected, list):
        for t in expected:
            ok, _ = _check_type(value, t, path)
            if ok:
                return True, ""
        return False, f"{path}: expected type {expected}, got {_py_type_name(value)}"

    if expected == "null":
        ok = value is None
    elif expected == "boolean":
        ok = isinstance(value, bool)
    elif expected == "number":
        # bool is a subclass of int in Python — exclude it from number
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected == "string":
        ok = isinstance(value, str)
    elif expected == "object":
        ok = isinstance(value, dict)
    elif expected == "array":
        ok = isinstance(value, list)
    else:
        return True, ""  # unknown token — pass through

    if ok:
        return True, ""
    return (
        False,
        f"{path}: expected {expected}, got {_py_type_name(value)} ({_repr(value)})",
    )


def _py_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _repr(value: Any) -> str:
    """Short repr for violation messages — truncates long values."""
    r = repr(value)
    return r if len(r) <= 60 else r[:57] + "..."
