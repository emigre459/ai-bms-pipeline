"""Image ingestion utilities for BMS screenshot analysis with Anthropic."""

from __future__ import annotations

import base64
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Optional

import anthropic
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ai_bms_pipeline.config import MAX_CONCURRENT_LLM_TASKS

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
    season: Optional[Literal["heating", "cooling", "shoulder"]] = None


class Fan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    role: Optional[Literal["supply", "return", "exhaust", "relief"]] = None
    status: Optional[Literal["on", "off", "fault"]] = None
    vfd_pct: Optional[float] = None


class Temperatures(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supply_air_actual_f: Optional[float] = None
    supply_air_setpoint_f: Optional[float] = None
    return_air_f: Optional[float] = None
    discharge_air_f: Optional[float] = None


class Economizer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: Optional[bool] = None
    position_pct: Optional[float] = None


class AirSystem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    mode: Optional[Literal["occupied", "unoccupied", "override", "off"]] = None
    out_of_schedule: Optional[bool] = None
    control_source: Optional[Literal["BAS", "local", "manual"]] = None
    fans: Optional[list[Fan]] = None
    sa_static_pressure_actual_inwc: Optional[float] = None
    sa_static_pressure_setpoint_inwc: Optional[float] = None
    temperatures: Temperatures
    economizer: Economizer
    vav_demand_pct: Optional[float] = None
    overrides: Optional[list[str]] = None
    notes: Optional[str] = None


class Boiler(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: Optional[Literal["on", "off", "standby"]] = None
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
    status: Optional[Literal["on", "off", "standby"]] = None
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
    description: Optional[str] = None
    severity: Optional[Literal["info", "warning", "critical"]] = None


class BmsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    building_id: str
    timestamp: str
    conditions: Conditions
    air_systems: Optional[list[AirSystem]] = None
    heating_plant: HeatingPlant
    cooling_plant: CoolingPlant
    zones: Optional[list[Zone]] = None
    anomalies: Optional[list[Anomaly]] = None

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp_has_timezone(cls, value: str) -> str:
        _parse_timezone_aware_timestamp(value)
        return value


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
        if raw_line.startswith(" ") or raw_line.startswith("\t"):
            continue
        code = raw_line.split("#", 1)[0].strip()
        if not code or ":" not in code:
            continue
        key = code.split(":", 1)[0].strip()
        if key and key not in fields:
            fields.append(key)
    return fields


def _schema_type_token_to_json_type(token: str) -> str:
    return {
        "string": "string",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(token.strip(), "string")


def _type_expression_to_schema(type_expr: str) -> dict[str, Any]:
    expr = type_expr.strip().strip('"')
    if expr.startswith("[") and expr.endswith("]"):
        return {
            "type": "array",
            "items": _type_expression_to_schema(expr[1:-1].strip()),
        }
    tokens = [t.strip() for t in expr.split("|") if t.strip()]
    if len(tokens) <= 1:
        return {"type": _schema_type_token_to_json_type(expr)}
    json_types = list(dict.fromkeys(_schema_type_token_to_json_type(t) for t in tokens))
    return {"type": json_types if len(json_types) > 1 else json_types[0]}


def _enum_from_comment(comment: str | None) -> list[str] | None:
    if not comment or "|" not in comment:
        return None
    prefix = comment.split("or null", 1)[0]
    prefix = prefix.split("if unknown", 1)[0]
    prefix = re.sub(r"\([^)]*\)", "", prefix)
    raw_tokens = [t.strip() for t in prefix.split("|")]
    tokens: list[str] = []
    for token in raw_tokens:
        cleaned = re.sub(r"[^A-Za-z0-9_\-]", "", token)
        if not cleaned:
            continue
        if cleaned.lower() in {"string", "number", "boolean", "null"}:
            continue
        tokens.append(cleaned)
    if len(tokens) < 2:
        return None
    return tokens


def _schema_lines(schema_text: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for raw_line in schema_text.splitlines():
        raw_no_newline = raw_line.rstrip()
        if not raw_no_newline.strip() or raw_no_newline.strip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        parts = raw_no_newline.split("#", 1)
        code = parts[0].rstrip()
        if not code.strip():
            continue
        comment = parts[1].strip() if len(parts) > 1 else None
        parsed.append(
            {
                "indent": indent,
                "text": code.strip(),
                "comment": comment,
                "optional": "(optional)" in raw_line.lower(),
            }
        )
    return parsed


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
        comment = str(line["comment"]) if line["comment"] else None
        optional = bool(line["optional"])

        if line_indent < indent:
            break
        if line_indent > indent:
            idx += 1
            continue
        if text.startswith("- ") or ":" not in text:
            idx += 1
            continue

        key, raw_value = text.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        idx += 1

        if value:
            field_schema = _type_expression_to_schema(value)
        elif idx < len(lines) and int(lines[idx]["indent"]) > indent:
            nested_indent = int(lines[idx]["indent"])
            if str(lines[idx]["text"]).startswith("- "):
                field_schema, idx = _parse_array_schema(lines, idx, nested_indent)
            else:
                field_schema, idx = _parse_object_schema(lines, idx, nested_indent)
        else:
            field_schema = {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }

        enum_values = _enum_from_comment(comment)
        if enum_values and (
            field_schema.get("type") == "string"
            or (
                isinstance(field_schema.get("type"), list)
                and "string" in field_schema["type"]
            )
        ):
            if (
                isinstance(field_schema.get("type"), list)
                and "null" in field_schema["type"]
            ):
                field_schema = {
                    "anyOf": [
                        {"type": "string", "enum": list(enum_values)},
                        {"type": "null"},
                    ]
                }
            else:
                field_schema["enum"] = list(enum_values)

        properties[key] = field_schema
        if not optional and "null" not in str(field_schema.get("type")):
            required.append(key)

    out: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        out["required"] = required
    return out, idx


def _parse_array_schema(
    lines: list[dict[str, Any]],
    start: int,
    indent: int,
) -> tuple[dict[str, Any], int]:
    idx = start
    item_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    while idx < len(lines):
        line = lines[idx]
        if int(line["indent"]) < indent:
            break
        if int(line["indent"]) != indent or not str(line["text"]).startswith("- "):
            break
        first = str(line["text"])[2:].strip()
        idx += 1
        if first and ":" in first:
            key, value = first.split(":", 1)
            key = key.strip()
            value = value.strip()
            item_schema["properties"][key] = (
                _type_expression_to_schema(value) if value else {"type": "string"}
            )
            item_schema.setdefault("required", []).append(key)
        if idx < len(lines) and int(lines[idx]["indent"]) > indent:
            nested, idx = _parse_object_schema(lines, idx, int(lines[idx]["indent"]))
            item_schema["properties"].update(nested.get("properties", {}))
            item_schema.setdefault("required", []).extend(nested.get("required", []))
    if item_schema.get("required"):
        item_schema["required"] = sorted(set(item_schema["required"]))
    return {"type": "array", "items": item_schema}, idx


def yaml_to_anthropic_json_schema(
    schema_path: Path | str | None = None,
) -> dict[str, Any]:
    lines = _schema_lines(_load_schema_text(schema_path))
    root, _ = _parse_object_schema(lines, 0, 0)
    if not root.get("properties"):
        raise ValueError("Parsed YAML schema has no top-level properties.")
    _require_all_object_properties(root)
    return root


def media_type_for_path(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix not in MEDIA_TYPES_BY_EXTENSION:
        raise ValueError(
            f"Unsupported image extension '{suffix}'. Supported: {', '.join(ALLOWED_EXTENSIONS)}"
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
    return sorted(
        [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in allowed]
    )


def derive_building_id_hint(
    image_path: str | Path, image_root: str | Path | None = None
) -> str:
    path = Path(image_path)
    if image_root is None:
        return path.stem
    root = Path(image_root).resolve()
    try:
        rel = path.resolve().relative_to(root)
    except ValueError:
        return path.stem
    return rel.parts[0] if len(rel.parts) >= 2 else path.stem


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
    scale = min(
        MAX_LONG_EDGE_PX / float(long_edge), (MAX_IMAGE_PIXELS / float(pixels)) ** 0.5
    )
    return image.resize(
        (max(1, int(width * scale)), max(1, int(height * scale))),
        Image.Resampling.LANCZOS,
    )


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
    return media_type, base64.b64encode(output.getvalue()).decode("utf-8")


def _extract_json_from_response(response: Any) -> dict[str, Any]:
    for block in getattr(response, "content", []):
        if hasattr(block, "input") and isinstance(block.input, dict):
            return block.input
        elif hasattr(block, "json") and isinstance(block.json, dict):
            return block.json
        elif hasattr(block, "text") and isinstance(block.text, str):
            text_value = block.text.strip()
            if text_value.startswith("{") and text_value.endswith("}"):
                parsed = json.loads(text_value)
                if isinstance(parsed, dict):
                    return parsed
    raise ValueError("Structured JSON object not present in response content.")


def _create_message_with_schema(
    api_client: anthropic.Anthropic,
    *,
    model: str,
    max_tokens: int,
    messages: list[dict[str, Any]],
    output_schema: dict[str, Any],
) -> Any:
    return api_client.messages.create(
        model=model,
        max_tokens=max_tokens,
        output_config={"format": {"type": "json_schema", "schema": output_schema}},
        messages=messages,
    )


def _is_stop_reason_max_tokens(response: Any) -> bool:
    return getattr(response, "stop_reason", None) == "max_tokens"


def _require_all_object_properties(schema: dict[str, Any]) -> None:
    if schema.get("type") == "object":
        props = schema.get("properties", {})
        schema["required"] = sorted(list(props.keys()))
        for child in props.values():
            if isinstance(child, dict):
                _require_all_object_properties(child)
    if schema.get("type") == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            _require_all_object_properties(items)
    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        for child in schema["anyOf"]:
            if isinstance(child, dict):
                _require_all_object_properties(child)


def _classifier_output_schema(schema_path: Path | str | None = None) -> dict[str, Any]:
    top_level_fields = _extract_yaml_top_level_fields(_load_schema_text(schema_path))
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


def _normalize_control_source(
    value: str | None, allowed_values: list[str]
) -> str | None:
    if value is None:
        return None
    if value in allowed_values:
        return value
    upper = value.upper()
    allowed_upper = {item.upper(): item for item in allowed_values}
    if upper in {"BMS", "BAS"}:
        if "BAS" in allowed_upper:
            return allowed_upper["BAS"]
        if "BMS" in allowed_upper:
            return allowed_upper["BMS"]
    return value


def _normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    _coerce_snapshot_shape(snapshot)
    systems = snapshot.get("air_systems")
    if isinstance(systems, list):
        for system in systems:
            if not isinstance(system, dict):
                continue
            normalized = _normalize_control_source(
                system.get("control_source"),
                ["BAS", "local", "manual"],
            )
            system["control_source"] = normalized
    return snapshot


def _coerce_snapshot_shape(snapshot: dict[str, Any]) -> None:
    # Normalize conditions aliases and required keys.
    conditions = snapshot.get("conditions")
    if not isinstance(conditions, dict):
        conditions = {}
    if "oat_f" not in conditions:
        conditions["oat_f"] = conditions.get("outdoor_temp_f")
        if conditions["oat_f"] is None:
            conditions["oat_f"] = conditions.get("outdoor_temperature_f")
    if "rh_pct" not in conditions:
        conditions["rh_pct"] = conditions.get("outdoor_humidity_pct")
    if "season" not in conditions:
        conditions["season"] = conditions.get("weather_season")
    snapshot["conditions"] = {
        "oat_f": conditions.get("oat_f"),
        "rh_pct": conditions.get("rh_pct"),
        "season": conditions.get("season"),
    }

    # Ensure required nested air_system blocks are present.
    air_systems_input = snapshot.get("air_systems")
    if not isinstance(air_systems_input, list):
        air_systems_input = []
    normalized_air_systems: list[dict[str, Any]] = []
    for system in air_systems_input:
        if not isinstance(system, dict):
            continue
        temps = system.get("temperatures")
        if not isinstance(temps, dict):
            temps = {}
        temps.setdefault("supply_air_actual_f", system.get("supply_air_actual_f"))
        temps.setdefault("supply_air_setpoint_f", system.get("supply_air_setpoint_f"))
        temps.setdefault("return_air_f", system.get("return_air_f"))
        temps.setdefault("discharge_air_f", system.get("discharge_air_f"))
        normalized_temperatures = {
            "supply_air_actual_f": temps.get("supply_air_actual_f"),
            "supply_air_setpoint_f": temps.get("supply_air_setpoint_f"),
            "return_air_f": temps.get("return_air_f"),
            "discharge_air_f": temps.get("discharge_air_f"),
        }

        econ = system.get("economizer")
        if not isinstance(econ, dict):
            econ = {}
        normalized_economizer = {
            "active": econ.get("active"),
            "position_pct": econ.get("position_pct"),
        }

        fans_input = system.get("fans")
        normalized_fans: list[dict[str, Any]] | None = None
        if isinstance(fans_input, list):
            normalized_fans = []
            for fan in fans_input:
                if not isinstance(fan, dict):
                    continue
                if "vfd_pct" not in fan:
                    fan["vfd_pct"] = fan.get("vfd_speed_pct")
                normalized_fans.append(
                    {
                        "id": fan.get("id") or fan.get("name") or "unknown-fan",
                        "role": fan.get("role"),
                        "status": fan.get("status"),
                        "vfd_pct": fan.get("vfd_pct"),
                    }
                )

        normalized_air_systems.append(
            {
                "id": system.get("id") or system.get("name") or "unknown-air-system",
                "mode": system.get("mode"),
                "out_of_schedule": system.get("out_of_schedule"),
                "control_source": system.get("control_source"),
                "fans": normalized_fans,
                "sa_static_pressure_actual_inwc": system.get(
                    "sa_static_pressure_actual_inwc"
                ),
                "sa_static_pressure_setpoint_inwc": system.get(
                    "sa_static_pressure_setpoint_inwc"
                ),
                "temperatures": normalized_temperatures,
                "economizer": normalized_economizer,
                "vav_demand_pct": system.get("vav_demand_pct"),
                "overrides": system.get("overrides"),
                "notes": system.get("notes"),
            }
        )
    snapshot["air_systems"] = normalized_air_systems

    # Ensure required plant structures exist.
    heating = snapshot.get("heating_plant")
    if not isinstance(heating, dict):
        heating = {}
    reset = heating.get("hws_oat_reset_active")
    if not isinstance(reset, dict):
        reset = {}
    snapshot["heating_plant"] = {
        "boilers": heating.get("boilers"),
        "hws_temp_actual_f": heating.get("hws_temp_actual_f"),
        "hws_temp_setpoint_f": heating.get("hws_temp_setpoint_f"),
        "hws_oat_reset_active": {
            "oat_min_f": reset.get("oat_min_f"),
            "oat_max_f": reset.get("oat_max_f"),
            "hws_min_f": reset.get("hws_min_f"),
            "hws_max_f": reset.get("hws_max_f"),
        },
        "vav_heat_request_pct": heating.get("vav_heat_request_pct"),
    }

    cooling = snapshot.get("cooling_plant")
    if not isinstance(cooling, dict):
        cooling = {}
    snapshot["cooling_plant"] = {
        "chws_temp_actual_f": cooling.get("chws_temp_actual_f"),
        "chws_temp_setpoint_f": cooling.get("chws_temp_setpoint_f"),
        "oat_reset_active": cooling.get("oat_reset_active"),
        "units": cooling.get("units"),
    }

    zones_input = snapshot.get("zones")
    if not isinstance(zones_input, list):
        snapshot["zones"] = None
    else:
        snapshot["zones"] = [
            {
                "id": zone.get("id") or zone.get("name") or f"zone-{idx+1}",
                "name": zone.get("name") or zone.get("space"),
                "space_temp_actual_f": (
                    zone.get("space_temp_actual_f")
                    if "space_temp_actual_f" in zone
                    else zone.get("zone_temp_f")
                ),
                "space_temp_setpoint_f": (
                    zone.get("space_temp_setpoint_f")
                    if "space_temp_setpoint_f" in zone
                    else zone.get("setpoint_f")
                ),
                "damper_position_pct": zone.get("damper_position_pct"),
                "reheat_active": zone.get("reheat_active"),
                "notes": zone.get("notes"),
            }
            for idx, zone in enumerate(zones_input)
            if isinstance(zone, dict)
        ]

    severity_map = {
        "low": "info",
        "medium": "warning",
        "high": "critical",
        "info": "info",
        "warning": "warning",
        "critical": "critical",
    }
    anomalies_input = snapshot.get("anomalies")
    if isinstance(anomalies_input, dict):
        anomalies_input = [
            {"system_id": "alarm_summary", "description": json.dumps(anomalies_input)}
        ]
    if not isinstance(anomalies_input, list):
        anomalies_input = []
    normalized_anomalies: list[dict[str, Any]] = []
    for idx, anomaly in enumerate(anomalies_input):
        if not isinstance(anomaly, dict):
            continue
        severity_raw = anomaly.get("severity")
        severity = (
            severity_map.get(str(severity_raw).lower())
            if isinstance(severity_raw, str)
            else None
        )
        normalized_anomalies.append(
            {
                "system_id": anomaly.get("system_id")
                or anomaly.get("id")
                or f"anomaly-{idx+1}",
                "description": anomaly.get("description"),
                "severity": severity,
            }
        )
    snapshot["anomalies"] = normalized_anomalies


def _validate_enums_against_schema(
    value: Any,
    schema: dict[str, Any],
    path: str = "$",
) -> None:
    if "enum" in schema and value is not None:
        allowed = schema["enum"]
        if value not in allowed:
            raise ValueError(f"Invalid enum at {path}: {value!r} not in {allowed!r}")

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if value is None and "null" in schema_type:
            return

    if value is None:
        return

    if schema_type == "object":
        props = schema.get("properties", {})
        if isinstance(value, dict):
            for key, child_schema in props.items():
                if key in value:
                    _validate_enums_against_schema(
                        value[key],
                        child_schema,
                        path=f"{path}.{key}",
                    )
        return

    if schema_type == "array":
        item_schema = schema.get("items", {})
        if isinstance(value, list):
            for idx, item in enumerate(value):
                _validate_enums_against_schema(item, item_schema, f"{path}[{idx}]")
        return


def _parse_timezone_aware_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone information.")
    return parsed


def safe_timestamp_for_filename(timestamp: str) -> str:
    parsed = _parse_timezone_aware_timestamp(timestamp)
    normalized = parsed.isoformat()
    return normalized.replace(":", "_")


def is_bms_screenshot(
    image_path: str | Path,
    *,
    client: anthropic.Anthropic | None = None,
    schema_path: str | Path | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    api_client = _get_client(client)
    media_type, image_data = _encode_image_for_api(image_path)
    response = _create_message_with_schema(
        api_client,
        model=model,
        max_tokens=600,
        output_schema=_classifier_output_schema(schema_path),
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
                    {
                        "type": "text",
                        "text": (
                            "Determine if this is a BMS screenshot. Return structured output only."
                        ),
                    },
                ],
            }
        ],
    )
    return BmsScreenshotCheck.model_validate(
        _extract_json_from_response(response)
    ).model_dump()


def _snapshot_list_schema(snapshot_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "snapshots": {"type": "array", "minItems": 1, "items": snapshot_schema}
        },
        "required": ["snapshots"],
    }


def _drop_null_unions_for_compilation(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") == "object":
        for key, child in list(schema.get("properties", {}).items()):
            if isinstance(child, dict):
                schema["properties"][key] = _drop_null_unions_for_compilation(child)
        return schema

    if schema.get("type") == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            schema["items"] = _drop_null_unions_for_compilation(items)
        return schema

    if isinstance(schema.get("type"), list):
        non_null = [t for t in schema["type"] if t != "null"]
        if non_null:
            schema["type"] = non_null[0] if len(non_null) == 1 else non_null
        else:
            schema["type"] = "string"
        return schema

    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        non_null_anyof = []
        for child in schema["anyOf"]:
            if isinstance(child, dict) and child.get("type") == "null":
                continue
            if isinstance(child, dict):
                non_null_anyof.append(_drop_null_unions_for_compilation(child))
        if len(non_null_anyof) == 1:
            return non_null_anyof[0]
        if len(non_null_anyof) > 1:
            schema["anyOf"] = non_null_anyof
            return schema
        return {"type": "string"}

    return schema


def _compact_snapshot_payload_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "snapshots": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "building_id": {"type": "string"},
                        "timestamp": {"type": "string"},
                        "snapshot_json": {"type": "string"},
                    },
                    "required": ["building_id", "timestamp", "snapshot_json"],
                },
            }
        },
        "required": ["snapshots"],
    }


def _snapshot_repair_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "snapshot_json": {"type": "string"},
        },
        "required": ["snapshot_json"],
    }


def _repair_snapshot_with_llm(
    api_client: anthropic.Anthropic,
    *,
    model: str,
    broken_snapshot: dict[str, Any],
    building_id_hint: str,
) -> dict[str, Any]:
    repair_messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Normalize this snapshot JSON into the exact required schema keys. "
                        "Return snapshot_json as a JSON-stringified object with top-level keys: "
                        "building_id, timestamp, conditions, air_systems, heating_plant, cooling_plant, zones, anomalies. "
                        "conditions must use keys oat_f, rh_pct, season. "
                        "Use null for missing values. "
                        f"If building_id is missing, use {building_id_hint}. "
                        f"Input snapshot:\n{json.dumps(broken_snapshot)}"
                    ),
                }
            ],
        }
    ]
    response = _create_message_with_schema(
        api_client,
        model=model,
        max_tokens=1800,
        output_schema=_snapshot_repair_schema(),
        messages=repair_messages,
    )
    if _is_stop_reason_max_tokens(response):
        response = _create_message_with_schema(
            api_client,
            model=model,
            max_tokens=3600,
            output_schema=_snapshot_repair_schema(),
            messages=repair_messages,
        )
    payload = _extract_json_from_response(response)
    if not isinstance(payload.get("snapshot_json"), str):
        raise ValueError("Snapshot repair response missing snapshot_json string.")
    repaired = json.loads(payload["snapshot_json"])
    if not isinstance(repaired, dict):
        raise ValueError("Snapshot repair output is not a JSON object.")
    return repaired


def extract_bms_snapshots(
    image_path: str | Path,
    *,
    building_id_hint: str | None = None,
    client: anthropic.Anthropic | None = None,
    schema_path: str | Path | None = None,
    model: str = DEFAULT_MODEL,
) -> list[dict[str, Any]]:
    api_client = _get_client(client)
    media_type, image_data = _encode_image_for_api(image_path)
    snapshot_schema = yaml_to_anthropic_json_schema(schema_path)
    request_schema = _compact_snapshot_payload_schema()
    request_messages = [
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
                {
                    "type": "text",
                    "text": (
                        "Extract one BMS snapshot per distinct timestamp shown. "
                        "If the screen shows a log or list (e.g. alarm list, event log, trend table) "
                        "where each row has its own timestamp, create one snapshot per row using "
                        "that row's timestamp — do not collapse multiple rows into one snapshot. "
                        "Use ISO 8601 timestamp with timezone. "
                        "Use null for unknown values. "
                        "Do not map indoor/equipment temperatures to outdoor temperature. "
                        "Treat BAS and BMS as equivalent where needed. "
                        "For each snapshot item, set snapshot_json to a JSON-stringified object "
                        "matching the full BMS snapshot structure with these exact top-level keys: "
                        "building_id, timestamp, conditions, air_systems, heating_plant, cooling_plant, zones, anomalies. "
                        "Do not output alternate objects like alarm/event summaries. "
                        f"If building id is unclear, use {building_id_hint or Path(image_path).stem}."
                    ),
                },
            ],
        }
    ]
    response = _create_message_with_schema(
        api_client,
        model=model,
        max_tokens=2600,
        output_schema=request_schema,
        messages=request_messages,
    )
    if _is_stop_reason_max_tokens(response):
        response = _create_message_with_schema(
            api_client,
            model=model,
            max_tokens=5200,
            output_schema=request_schema,
            messages=request_messages,
        )
    payload = _extract_json_from_response(response)
    snapshots_raw = payload.get("snapshots")
    if not isinstance(snapshots_raw, list):
        raise ValueError("Expected 'snapshots' list in structured output response.")
    validated: list[dict[str, Any]] = []
    for raw in snapshots_raw:
        if not isinstance(raw, dict):
            raise ValueError("Snapshot item must be an object.")
        item = dict(raw)
        snapshot_json = item.get("snapshot_json")
        if not isinstance(snapshot_json, str):
            raise ValueError("Expected snapshot_json as JSON string in compact schema.")
        parsed_snapshot = json.loads(snapshot_json)
        if not isinstance(parsed_snapshot, dict):
            raise ValueError("Parsed snapshot_json must be an object.")
        if not parsed_snapshot.get("building_id"):
            parsed_snapshot["building_id"] = (
                item.get("building_id") or building_id_hint or Path(image_path).stem
            )
        if not parsed_snapshot.get("timestamp") and isinstance(
            item.get("timestamp"), str
        ):
            parsed_snapshot["timestamp"] = item["timestamp"]
        normalized = _normalize_snapshot(parsed_snapshot)
        try:
            _validate_enums_against_schema(normalized, snapshot_schema)
            validated.append(BmsSnapshot.model_validate(normalized).model_dump())
        except ValidationError as exc:
            repaired = _repair_snapshot_with_llm(
                api_client,
                model=model,
                broken_snapshot=normalized,
                building_id_hint=building_id_hint or Path(image_path).stem,
            )
            _validate_enums_against_schema(repaired, snapshot_schema)
            repaired_normalized = _normalize_snapshot(repaired)
            validated.append(
                BmsSnapshot.model_validate(repaired_normalized).model_dump()
            )
    return validated


def extract_bms_snapshot(
    image_path: str | Path,
    *,
    building_id_hint: str | None = None,
    client: anthropic.Anthropic | None = None,
    schema_path: str | Path | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    snapshots = extract_bms_snapshots(
        image_path,
        building_id_hint=building_id_hint,
        client=client,
        schema_path=schema_path,
        model=model,
    )
    return snapshots[0]


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
            image_path, client=client, schema_path=schema_path, model=model
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
    image_paths = list_image_paths(directory)
    indexed_results: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LLM_TASKS) as pool:
        futures = {
            pool.submit(
                ingest_image,
                image_path,
                image_root=directory,
                skip_classifier=skip_classifier,
                client=client,
                schema_path=schema_path,
                model=model,
            ): idx
            for idx, image_path in enumerate(image_paths)
        }
        for future in as_completed(futures):
            idx = futures[future]
            result = future.result()
            if result is not None:
                indexed_results[idx] = result
    return [indexed_results[i] for i in sorted(indexed_results)]


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
    return "\n".join(
        [
            getattr(block, "text", "")
            for block in getattr(response, "content", [])
            if getattr(block, "text", None)
        ]
    ).strip()


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
