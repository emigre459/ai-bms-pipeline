"""
Image ingestion utilities for BMS screenshot analysis with Anthropic.
"""

from __future__ import annotations

import base64
import json
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import anthropic
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ALLOWED_EXTENSIONS: tuple[str, ...] = (".jpeg", ".jpg", ".png", ".webp")
MEDIA_TYPES_BY_EXTENSION: dict[str, str] = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

DEFAULT_MODEL = os.environ.get("DEFAULT_ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_LONG_EDGE_PX = 1568
MAX_IMAGE_PIXELS = 1_150_000


class Conditions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    oat_f: Optional[float] = None
    rh_pct: Optional[float] = None
    season: Optional[str] = None


class Fan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    role: str
    status: str
    vfd_pct: Optional[float] = None


class Temperatures(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supply_air_actual_f: Optional[float] = None
    supply_air_setpoint_f: Optional[float] = None
    return_air_f: Optional[float] = None
    discharge_air_f: Optional[float] = None


class Economizer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool
    position_pct: Optional[float] = None


class AirSystem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    mode: str
    out_of_schedule: bool
    control_source: str
    fans: list[Fan] = Field(default_factory=list)
    sa_static_pressure_actual_inwc: Optional[float] = None
    sa_static_pressure_setpoint_inwc: Optional[float] = None
    temperatures: Temperatures
    economizer: Economizer
    vav_demand_pct: Optional[float] = None
    overrides: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class Boiler(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: str
    firing_rate_pct: Optional[float] = None
    outlet_temp_f: Optional[float] = None
    runtime_hrs: Optional[float] = None
    cycle_count: Optional[float] = None


class HwsOatReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    oat_min_f: Optional[float] = None
    oat_max_f: Optional[float] = None
    hws_min_f: Optional[float] = None
    hws_max_f: Optional[float] = None


class HeatingPlant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    boilers: Optional[list[Boiler]] = None
    hws_temp_actual_f: Optional[float] = None
    hws_temp_setpoint_f: Optional[float] = None
    hws_oat_reset_active: HwsOatReset
    vav_heat_request_pct: Optional[float] = None


class CoolingUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: str
    current_load_pct: Optional[float] = None
    efficiency_kw_per_ton: Optional[float] = None


class CoolingPlant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chws_temp_actual_f: Optional[float] = None
    chws_temp_setpoint_f: Optional[float] = None
    oat_reset_active: Optional[bool] = None
    units: Optional[list[CoolingUnit]] = None


class Zone(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: Optional[str] = None
    space_temp_actual_f: Optional[float] = None
    space_temp_setpoint_f: Optional[float] = None
    damper_position_pct: Optional[float] = None
    reheat_active: Optional[bool] = None
    notes: Optional[str] = None


class Anomaly(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_id: str
    description: str
    severity: str


class BmsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    building_id: str
    timestamp: str
    conditions: Conditions
    air_systems: list[AirSystem] = Field(default_factory=list)
    heating_plant: HeatingPlant
    cooling_plant: CoolingPlant
    zones: Optional[list[Zone]] = None
    anomalies: list[Anomaly] = Field(default_factory=list)


class BmsScreenshotCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_bms_screenshot: bool
    reason: Optional[str] = None
    structured_fields_present: list[str] = Field(default_factory=list)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_schema_path() -> Path:
    return _project_root() / "conf" / "bms-snapshot.schema.yaml"


def _load_schema_text(schema_path: Path | str | None = None) -> str:
    path = Path(schema_path) if schema_path else default_schema_path()
    return path.read_text(encoding="utf-8")


def _extract_yaml_top_level_fields(schema_text: str) -> list[str]:
    fields: list[str] = []
    for raw_line in schema_text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line:
            continue
        if raw_line.startswith(" ") or raw_line.startswith("\t"):
            continue
        if ":" in line:
            key = line.split(":", 1)[0].strip()
            if key and key not in fields:
                fields.append(key)
    return fields


def _schema_type_token_to_json_type(token: str) -> str:
    mapping = {
        "string": "string",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }
    return mapping.get(token.strip(), "string")


def _type_expression_to_schema(type_expr: str) -> dict[str, Any]:
    expr = type_expr.strip()
    if expr.startswith("[") and expr.endswith("]"):
        inner = expr[1:-1].strip()
        return {
            "type": "array",
            "items": _type_expression_to_schema(inner),
        }

    if "|" in expr:
        tokens = [t.strip() for t in expr.split("|") if t.strip()]
        json_types = [_schema_type_token_to_json_type(t) for t in tokens]
        unique = list(dict.fromkeys(json_types))
        if len(unique) == 1:
            return {"type": unique[0]}
        return {"type": unique}

    return {"type": _schema_type_token_to_json_type(expr)}


def _schema_lines(schema_text: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for raw_line in schema_text.splitlines():
        stripped = raw_line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        parsed.append(
            {
                "indent": indent,
                "text": stripped.strip(),
                "optional": "(optional)" in raw_line.lower(),
            }
        )
    return parsed


def _merge_object_schema(
    base: dict[str, Any] | None,
    new: dict[str, Any],
) -> dict[str, Any]:
    if not base:
        return new
    if base.get("type") != "object" or new.get("type") != "object":
        return base
    base_props = base.setdefault("properties", {})
    base_required = set(base.get("required", []))
    for key, value in new.get("properties", {}).items():
        if key not in base_props:
            base_props[key] = value
    for key in new.get("required", []):
        base_required.add(key)
    if base_required:
        base["required"] = sorted(base_required)
    return base


def _parse_object_schema(
    lines: list[dict[str, Any]],
    start: int,
    indent: int,
) -> tuple[dict[str, Any], int]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    idx = start

    while idx < len(lines):
        line = lines[idx]
        line_indent = int(line["indent"])
        text = str(line["text"])
        optional = bool(line["optional"])

        if line_indent < indent:
            break
        if line_indent > indent:
            idx += 1
            continue
        if text.startswith("- "):
            break
        if ":" not in text:
            idx += 1
            continue

        key, raw_value = text.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        idx += 1

        if value:
            field_schema = _type_expression_to_schema(value)
        else:
            if idx < len(lines) and int(lines[idx]["indent"]) > indent:
                nested_indent = int(lines[idx]["indent"])
                nested_text = str(lines[idx]["text"])
                if nested_text.startswith("- "):
                    field_schema, idx = _parse_array_schema(
                        lines=lines,
                        start=idx,
                        indent=nested_indent,
                    )
                else:
                    field_schema, idx = _parse_object_schema(
                        lines=lines,
                        start=idx,
                        indent=nested_indent,
                    )
            else:
                field_schema = {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }

        properties[key] = field_schema
        if not optional:
            required.append(key)

    object_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        object_schema["required"] = required
    return object_schema, idx


def _parse_array_schema(
    lines: list[dict[str, Any]],
    start: int,
    indent: int,
) -> tuple[dict[str, Any], int]:
    idx = start
    item_schema: dict[str, Any] | None = None

    while idx < len(lines):
        line = lines[idx]
        line_indent = int(line["indent"])
        text = str(line["text"])

        if line_indent < indent:
            break
        if line_indent != indent or not text.startswith("- "):
            break

        rest = text[2:].strip()
        idx += 1

        if rest:
            if ":" in rest:
                key, raw_value = rest.split(":", 1)
                key = key.strip()
                value = raw_value.strip()
                first_field_schema = (
                    _type_expression_to_schema(value)
                    if value
                    else {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    }
                )
                candidate_item = {
                    "type": "object",
                    "properties": {key: first_field_schema},
                    "required": [key],
                    "additionalProperties": False,
                }
                if idx < len(lines) and int(lines[idx]["indent"]) > indent:
                    nested_schema, idx = _parse_object_schema(
                        lines=lines,
                        start=idx,
                        indent=int(lines[idx]["indent"]),
                    )
                    candidate_item = _merge_object_schema(candidate_item, nested_schema)
                item_schema = _merge_object_schema(item_schema, candidate_item)
            else:
                item_schema = _type_expression_to_schema(rest)
        else:
            if idx < len(lines) and int(lines[idx]["indent"]) > indent:
                nested_schema, idx = _parse_object_schema(
                    lines=lines,
                    start=idx,
                    indent=int(lines[idx]["indent"]),
                )
                item_schema = _merge_object_schema(item_schema, nested_schema)
            else:
                item_schema = item_schema or {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }

    if item_schema is None:
        item_schema = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    return {"type": "array", "items": item_schema}, idx


def yaml_to_anthropic_json_schema(
    schema_path: Path | str | None = None,
) -> dict[str, Any]:
    """
    Convert schema YAML text into Anthropic-compatible JSON Schema.
    """
    schema_text = _load_schema_text(schema_path)
    lines = _schema_lines(schema_text)
    root_schema, _ = _parse_object_schema(lines=lines, start=0, indent=0)
    if not root_schema.get("properties"):
        raise ValueError("Parsed YAML schema has no top-level properties.")
    return root_schema


def media_type_for_path(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix not in MEDIA_TYPES_BY_EXTENSION:
        raise ValueError(
            f"Unsupported image extension '{suffix}'. "
            f"Supported: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    return MEDIA_TYPES_BY_EXTENSION[suffix]


def list_image_paths(
    directory: str | Path,
    extensions: Optional[tuple[str, ...] | list[str]] = None,
) -> list[Path]:
    root = Path(directory)
    allowed = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in (extensions or ALLOWED_EXTENSIONS)
    }
    paths = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in allowed]
    return sorted(paths)


def derive_building_id_hint(
    image_path: str | Path,
    image_root: str | Path | None = None,
) -> str:
    path = Path(image_path)
    if image_root is None:
        return path.stem
    root = Path(image_root).resolve()
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return path.stem
    parts = rel.parts
    if len(parts) >= 2:
        return parts[0]
    return path.stem


def _get_client(client: anthropic.Anthropic | None = None) -> anthropic.Anthropic:
    if client is not None:
        return client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set.")
    return anthropic.Anthropic(api_key=api_key)


def _resize_if_needed(image: Image.Image) -> Image.Image:
    width, height = image.size
    long_edge = max(width, height)
    pixels = width * height
    if long_edge <= MAX_LONG_EDGE_PX and pixels <= MAX_IMAGE_PIXELS:
        return image

    scale_long = min(1.0, MAX_LONG_EDGE_PX / float(long_edge))
    scale_pixels = min(1.0, (MAX_IMAGE_PIXELS / float(pixels)) ** 0.5)
    scale = min(scale_long, scale_pixels)
    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))
    return image.resize((new_width, new_height), Image.Resampling.LANCZOS)


def _encode_image_for_api(image_path: str | Path) -> tuple[str, str]:
    path = Path(image_path)
    media_type = media_type_for_path(path)
    suffix = path.suffix.lower()

    with Image.open(path) as img:
        processed = _resize_if_needed(img)
        if suffix in {".jpeg", ".jpg"} and processed.mode not in {"RGB", "L"}:
            processed = processed.convert("RGB")
        output = BytesIO()
        if suffix in {".jpeg", ".jpg"}:
            processed.save(output, format="JPEG", quality=90, optimize=True)
        elif suffix == ".png":
            processed.save(output, format="PNG", optimize=True)
        elif suffix == ".webp":
            processed.save(output, format="WEBP", quality=90)
        else:
            raise ValueError(f"Unsupported extension: {suffix}")

    data = base64.b64encode(output.getvalue()).decode("utf-8")
    return media_type, data


def _extract_json_from_response(response: Any) -> dict[str, Any]:
    def _parse_json_object_from_text(text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        if not stripped:
            return None

        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        fence_pattern = re.compile(
            r"```(?:json)?\s*(\{[\s\S]*?\})\s*```",
            re.IGNORECASE,
        )
        for match in fence_pattern.finditer(text):
            candidate = match.group(1).strip()
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(text[index:])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
        return None

    for block in getattr(response, "content", []):
        if hasattr(block, "input") and isinstance(block.input, dict):
            return block.input
        if hasattr(block, "json") and isinstance(block.json, dict):
            return block.json
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parsed = _parse_json_object_from_text(text)
            if parsed is not None:
                return parsed
    raise ValueError("Anthropic response did not contain parseable JSON content.")


def _error_is_output_format_unsupported(error: Exception) -> bool:
    if not isinstance(error, anthropic.BadRequestError):
        return False
    message = str(error).lower()
    return "does not support output format" in message


def _error_is_schema_too_complex_for_structured_output(error: Exception) -> bool:
    if not isinstance(error, anthropic.BadRequestError):
        return False
    message = str(error).lower()
    indicators = [
        "schemas contains too many parameters with union types",
        "exponential compilation cost",
        "limit: 16 parameters with unions",
    ]
    return any(indicator in message for indicator in indicators)


def _create_message_with_optional_output_format(
    api_client: anthropic.Anthropic,
    *,
    model: str,
    max_tokens: int,
    messages: list[dict[str, Any]],
    output_schema: dict[str, Any],
    fallback_text: str,
) -> Any:
    try:
        return api_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": output_schema,
                }
            },
            messages=messages,
        )
    except Exception as exc:
        if not (
            _error_is_output_format_unsupported(exc)
            or _error_is_schema_too_complex_for_structured_output(exc)
        ):
            raise
    fallback_messages = [
        {
            "role": "user",
            "content": [
                messages[0]["content"][0],
                {"type": "text", "text": fallback_text},
            ],
        }
    ]
    return api_client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=fallback_messages,
    )


def _classifier_output_schema(schema_path: Path | str | None = None) -> dict[str, Any]:
    schema_text = _load_schema_text(schema_path)
    top_level_fields = _extract_yaml_top_level_fields(schema_text)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "is_bms_screenshot": {"type": "boolean"},
            "reason": {"type": ["string", "null"]},
            "structured_fields_present": {
                "type": "array",
                "items": {"type": "string", "enum": top_level_fields},
            },
        },
        "required": ["is_bms_screenshot", "reason", "structured_fields_present"],
    }


def is_bms_screenshot(
    image_path: str | Path,
    *,
    client: anthropic.Anthropic | None = None,
    schema_path: str | Path | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    api_client = _get_client(client)
    media_type, image_data = _encode_image_for_api(image_path)
    output_schema = _classifier_output_schema(schema_path)
    prompt = (
        "Determine if this image is a BMS screenshot with data relevant to the BMS "
        "snapshot schema (such as building metadata, conditions, air systems, "
        "heating/cooling plant, zones, or anomalies). "
        "Return only the structured output."
    )

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]
    fallback_text = (
        f"{prompt}\n\n"
        "Return exactly one JSON object and nothing else.\n"
        "Do not include markdown fences, explanations, or extra text.\n"
        "The first non-whitespace character must be '{' and the last must be '}'.\n"
        "JSON shape:\n"
        "{"
        '"is_bms_screenshot": boolean, '
        '"reason": string|null, '
        '"structured_fields_present": string[]'
        "}\n"
    )
    response = _create_message_with_optional_output_format(
        api_client,
        model=model,
        max_tokens=600,
        messages=messages,
        output_schema=output_schema,
        fallback_text=fallback_text,
    )

    parsed = _extract_json_from_response(response)
    return BmsScreenshotCheck.model_validate(parsed).model_dump()


def extract_bms_snapshot(
    image_path: str | Path,
    *,
    building_id_hint: str | None = None,
    client: anthropic.Anthropic | None = None,
    schema_path: str | Path | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    api_client = _get_client(client)
    media_type, image_data = _encode_image_for_api(image_path)
    json_schema = yaml_to_anthropic_json_schema(schema_path)
    prompt = (
        "Extract a BMS snapshot from this image and return only structured output "
        "matching the requested schema. Use null when values are not visible. "
        "If building_id is unclear, use this fallback building_id_hint: "
        f"{building_id_hint or Path(image_path).stem}."
    )

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]
    fallback_text = (
        f"{prompt}\n\n"
        "Return exactly one JSON object and nothing else.\n"
        "Do not include markdown fences, explanations, or extra text.\n"
        "The first non-whitespace character must be '{' and the last must be '}'.\n"
        f"Target JSON Schema:\n{json.dumps(json_schema)}"
    )
    response = _create_message_with_optional_output_format(
        api_client,
        model=model,
        max_tokens=2400,
        messages=messages,
        output_schema=json_schema,
        fallback_text=fallback_text,
    )

    parsed = _extract_json_from_response(response)
    try:
        snapshot = BmsSnapshot.model_validate(parsed).model_dump()
    except ValidationError:
        snapshot = dict(parsed) if isinstance(parsed, dict) else {}
        snapshot.setdefault("building_id", building_id_hint or Path(image_path).stem)
        snapshot.setdefault("timestamp", "")
        snapshot.setdefault(
            "conditions",
            {"oat_f": None, "rh_pct": None, "season": None},
        )
        if not isinstance(snapshot.get("conditions"), dict):
            snapshot["conditions"] = {"oat_f": None, "rh_pct": None, "season": None}
        snapshot.setdefault("air_systems", [])
        snapshot.setdefault(
            "heating_plant",
            {
                "hws_temp_actual_f": None,
                "hws_temp_setpoint_f": None,
                "hws_oat_reset_active": {
                    "oat_min_f": None,
                    "oat_max_f": None,
                    "hws_min_f": None,
                    "hws_max_f": None,
                },
                "vav_heat_request_pct": None,
            },
        )
        snapshot.setdefault(
            "cooling_plant",
            {
                "chws_temp_actual_f": None,
                "chws_temp_setpoint_f": None,
                "oat_reset_active": None,
            },
        )
        snapshot.setdefault("anomalies", [])
    if not snapshot.get("building_id"):
        snapshot["building_id"] = building_id_hint or Path(image_path).stem
    return snapshot


def ingest_image(
    image_path: str | Path,
    *,
    image_root: str | Path | None = None,
    skip_classifier: bool = False,
    client: anthropic.Anthropic | None = None,
    schema_path: str | Path | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any] | None:
    hint = derive_building_id_hint(image_path, image_root=image_root)
    if not skip_classifier:
        classifier = is_bms_screenshot(
            image_path,
            client=client,
            schema_path=schema_path,
            model=model,
        )
        if not classifier.get("is_bms_screenshot", False):
            return None
    return extract_bms_snapshot(
        image_path,
        building_id_hint=hint,
        client=client,
        schema_path=schema_path,
        model=model,
    )


def ingest_directory(
    directory: str | Path,
    *,
    skip_classifier: bool = False,
    client: anthropic.Anthropic | None = None,
    schema_path: str | Path | None = None,
    model: str = DEFAULT_MODEL,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for image_path in list_image_paths(directory):
        extracted = ingest_image(
            image_path,
            image_root=directory,
            skip_classifier=skip_classifier,
            client=client,
            schema_path=schema_path,
            model=model,
        )
        if extracted is not None:
            results.append(extracted)
    return results


def ask_image_question(
    image_path: str | Path,
    question: str,
    *,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
) -> str:
    api_client = _get_client(client)
    media_type, image_data = _encode_image_for_api(image_path)
    response = api_client.messages.create(
        model=model,
        max_tokens=1000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": question},
                ],
            }
        ],
    )
    texts = [
        getattr(block, "text", "")
        for block in getattr(response, "content", [])
        if getattr(block, "text", None)
    ]
    return "\n".join(texts).strip()


def ingest_directory_to_json(
    directory: str | Path = "data/images",
    *,
    output_dir: str | Path = "data/extracted_from_images",
    skip_classifier: bool = False,
    client: anthropic.Anthropic | None = None,
    schema_path: str | Path | None = None,
    model: str = DEFAULT_MODEL,
) -> Path:
    snapshots = ingest_directory(
        directory,
        skip_classifier=skip_classifier,
        client=client,
        schema_path=schema_path,
        model=model,
    )
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "snapshots.json"
    out_path.write_text(json.dumps(snapshots, indent=2), encoding="utf-8")
    return out_path
