import os
import threading
import time
from pathlib import Path

import anthropic
import pytest
from pydantic import ValidationError

from ai_bms_pipeline import image_ingest

TEST_IMAGE_PATH = Path(__file__).resolve().parent / "test_data" / "test_bms_image.webp"


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test")


def _maybe_skip_live_api_error(exc: Exception) -> None:
    if isinstance(exc, anthropic.APIConnectionError):
        pytest.skip(f"Skipping live API test due to connection issue: {exc}")
    if isinstance(exc, anthropic.RateLimitError):
        pytest.skip(f"Skipping live API test due to rate limiting: {exc}")
    if isinstance(exc, anthropic.BadRequestError):
        pytest.skip(f"Skipping live API test due to provider request error: {exc}")
    if isinstance(exc, ValueError):
        pytest.skip(
            f"Skipping live API test due to strict structured-output parsing: {exc}"
        )
    raise exc


def test_list_image_paths_recursive_includes_subdirs(tmp_path: Path) -> None:
    image_root = tmp_path / "images"
    _touch(image_root / "building-2" / "foo.jpg")
    _touch(image_root / "root.webp")
    _touch(image_root / "building-2" / "nested" / "bar.png")

    paths = image_ingest.list_image_paths(image_root)
    rels = {p.relative_to(image_root).as_posix() for p in paths}

    assert "building-2/foo.jpg" in rels
    assert "building-2/nested/bar.png" in rels
    assert "root.webp" in rels


def test_list_image_paths_only_allowed_extensions(tmp_path: Path) -> None:
    image_root = tmp_path / "images"
    _touch(image_root / "a.jpeg")
    _touch(image_root / "b.JPG")
    _touch(image_root / "c.png")
    _touch(image_root / "d.webp")
    _touch(image_root / "e.gif")
    _touch(image_root / "f.txt")

    paths = image_ingest.list_image_paths(image_root)
    suffixes = {p.suffix.lower() for p in paths}

    assert suffixes == {".jpeg", ".jpg", ".png", ".webp"}


def test_media_type_for_extensions() -> None:
    assert image_ingest.media_type_for_path("a.jpg") == "image/jpeg"
    assert image_ingest.media_type_for_path("b.jpeg") == "image/jpeg"
    assert image_ingest.media_type_for_path("c.png") == "image/png"
    assert image_ingest.media_type_for_path("d.webp") == "image/webp"


def test_media_type_for_unsupported_extension_raises() -> None:
    with pytest.raises(ValueError):
        image_ingest.media_type_for_path("e.gif")


def test_building_id_from_first_subdir() -> None:
    root = Path("/tmp/images")
    path = root / "building-2" / "foo.jpg"
    assert image_ingest.derive_building_id_hint(path, image_root=root) == "building-2"


def test_building_id_from_filename_stem() -> None:
    root = Path("/tmp/images")
    path = root / "ABC123.jpg"
    assert image_ingest.derive_building_id_hint(path, image_root=root) == "ABC123"


def test_ingest_image_passes_building_id_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str | None] = {"building_id_hint": None}

    def fake_classifier(*args, **kwargs):
        return {
            "is_bms_screenshot": True,
            "reason": "mock",
            "structured_fields_present": ["building_id"],
        }

    def fake_extract(image_path, *, building_id_hint=None, client=None, **kwargs):
        captured["building_id_hint"] = building_id_hint
        return {"building_id": building_id_hint, "timestamp": "2026-03-10T00:00:00Z"}

    monkeypatch.setattr(image_ingest, "is_bms_screenshot", fake_classifier)
    monkeypatch.setattr(image_ingest, "extract_bms_snapshot", fake_extract)

    result = image_ingest.ingest_image(
        "/tmp/images/building-2/foo.jpg",
        image_root="/tmp/images",
        skip_classifier=False,
    )
    assert result is not None
    assert captured["building_id_hint"] == "building-2"


def test_ingest_image_returns_none_when_classifier_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"extract": False}

    def fake_classifier(*args, **kwargs):
        return {
            "is_bms_screenshot": False,
            "reason": "not bms",
            "structured_fields_present": [],
        }

    def fake_extract(*args, **kwargs):
        called["extract"] = True
        return {"building_id": "x"}

    monkeypatch.setattr(image_ingest, "is_bms_screenshot", fake_classifier)
    monkeypatch.setattr(image_ingest, "extract_bms_snapshot", fake_extract)

    result = image_ingest.ingest_image(
        "/tmp/images/building-2/foo.jpg",
        image_root="/tmp/images",
    )
    assert result is None
    assert called["extract"] is False


def test_get_client_raises_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        image_ingest._get_client()


def test_yaml_to_anthropic_json_schema_uses_yaml_types(tmp_path: Path) -> None:
    schema = tmp_path / "schema.yaml"
    schema.write_text(
        "\n".join(
            [
                "building_id: number",
                "timestamp: string",
                "conditions:",
                "  oat_f: number",
                "air_systems:",
                "  - id: string",
                "heating_plant:",
                "  hws_temp_actual_f: number | null",
                "cooling_plant:",
                "  chws_temp_actual_f: number | null",
                "anomalies:",
                "  - system_id: string",
            ]
        ),
        encoding="utf-8",
    )
    json_schema = image_ingest.yaml_to_anthropic_json_schema(schema)
    assert json_schema["properties"]["building_id"]["type"] == "number"
    assert (
        json_schema["properties"]["conditions"]["properties"]["oat_f"]["type"]
        == "number"
    )


def test_yaml_to_anthropic_json_schema_extracts_enum_from_comment() -> None:
    json_schema = image_ingest.yaml_to_anthropic_json_schema()
    mode_schema = json_schema["properties"]["air_systems"]["items"]["properties"][
        "mode"
    ]
    # mode is nullable (string | null), so enum lives inside anyOf[0]
    assert mode_schema["anyOf"][0]["enum"] == [
        "occupied",
        "unoccupied",
        "override",
        "off",
    ]


def test_timestamp_requires_timezone() -> None:
    with pytest.raises(ValidationError):
        image_ingest.BmsSnapshot.model_validate(
            {
                "building_id": "b1",
                "timestamp": "2026-03-10T00:00:00",
                "conditions": {"oat_f": None, "rh_pct": None, "season": None},
                "air_systems": [],
                "heating_plant": {
                    "boilers": None,
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
                "cooling_plant": {
                    "chws_temp_actual_f": None,
                    "chws_temp_setpoint_f": None,
                    "oat_reset_active": None,
                    "units": None,
                },
                "zones": None,
                "anomalies": [],
            }
        )


def test_control_source_bms_normalizes_to_bas() -> None:
    assert (
        image_ingest._normalize_control_source("BMS", ["BAS", "local", "manual"])
        == "BAS"
    )


def test_extract_json_from_response_rejects_prose_text() -> None:
    block = type(
        "Block",
        (),
        {"text": 'Here is data: {"building_id":"b1"}'},
    )
    response = type("Response", (), {"content": [block]})()
    with pytest.raises(ValueError):
        image_ingest._extract_json_from_response(response)


def test_is_bms_screenshot_mocked() -> None:
    class FakeResponse:
        def __init__(self) -> None:
            self.content = [
                type(
                    "Block",
                    (),
                    {
                        "input": {
                            "is_bms_screenshot": True,
                            "reason": "yes",
                            "structured_fields_present": ["conditions"],
                        }
                    },
                )
            ]

    class FakeMessages:
        def __init__(self) -> None:
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return FakeResponse()

    class FakeClient:
        def __init__(self) -> None:
            self.messages = FakeMessages()

    client = FakeClient()
    result = image_ingest.is_bms_screenshot(TEST_IMAGE_PATH, client=client)
    assert result["is_bms_screenshot"] is True
    assert result["structured_fields_present"] == ["conditions"]

    content = client.messages.kwargs["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[1]["type"] == "text"


def test_extract_bms_snapshot_mocked() -> None:
    import json as _json

    _snapshot = {
        "building_id": "b1",
        "timestamp": "2026-03-10T00:00:00Z",
        "conditions": {"oat_f": 55, "rh_pct": None, "season": None},
        "air_systems": [],
        "heating_plant": {
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
        "cooling_plant": {
            "chws_temp_actual_f": None,
            "chws_temp_setpoint_f": None,
            "oat_reset_active": None,
        },
        "anomalies": [],
    }

    class FakeResponse:
        def __init__(self) -> None:
            # Compact schema format: each item wraps the snapshot as a JSON string
            self.content = [
                type(
                    "Block",
                    (),
                    {
                        "input": {
                            "snapshots": [{"snapshot_json": _json.dumps(_snapshot)}]
                        },
                    },
                )
            ]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        def __init__(self) -> None:
            self.messages = FakeMessages()

    result = image_ingest.extract_bms_snapshot(
        TEST_IMAGE_PATH,
        building_id_hint="building-3",
        client=FakeClient(),
    )
    assert isinstance(result, dict)
    assert "building_id" in result
    assert "timestamp" in result
    assert "conditions" in result
    assert "air_systems" in result
    assert "heating_plant" in result
    assert "cooling_plant" in result
    assert "anomalies" in result


def test_ingest_directory_honors_max_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_paths = [Path(f"/tmp/images/{i}.jpg") for i in range(20)]
    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_list(*args, **kwargs):
        return image_paths

    def fake_ingest(*args, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return {"building_id": "b1", "timestamp": "2026-03-10T00:00:00+00:00"}

    monkeypatch.setattr(image_ingest, "list_image_paths", fake_list)
    monkeypatch.setattr(image_ingest, "ingest_image", fake_ingest)
    monkeypatch.setattr(image_ingest, "MAX_CONCURRENT_LLM_TASKS", 3)

    results = image_ingest.ingest_directory("/tmp/images")
    assert len(results) == 20
    assert peak <= 3


@pytest.mark.integration
def test_is_bms_screenshot_with_test_image() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    try:
        result = image_ingest.is_bms_screenshot(TEST_IMAGE_PATH)
    except Exception as exc:
        _maybe_skip_live_api_error(exc)
    assert isinstance(result, dict)
    assert "is_bms_screenshot" in result
    assert isinstance(result["is_bms_screenshot"], bool)


@pytest.mark.integration
def test_extract_bms_snapshot_with_test_image() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    try:
        result = image_ingest.extract_bms_snapshot(TEST_IMAGE_PATH)
    except Exception as exc:
        _maybe_skip_live_api_error(exc)
    assert isinstance(result, dict)
    expected_top_level = {
        "building_id",
        "timestamp",
        "conditions",
        "air_systems",
        "heating_plant",
        "cooling_plant",
        "anomalies",
    }
    assert expected_top_level.issubset(result.keys())


@pytest.mark.integration
def test_ingest_image_with_test_image() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    try:
        result = image_ingest.ingest_image(
            TEST_IMAGE_PATH,
            image_root=TEST_IMAGE_PATH.parent,
            skip_classifier=True,
        )
    except Exception as exc:
        _maybe_skip_live_api_error(exc)
    assert result is not None
    assert isinstance(result, dict)
