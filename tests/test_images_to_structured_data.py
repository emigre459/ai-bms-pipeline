from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_script_module():
    script_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "images_to_structured_data.py"
    )
    spec = importlib.util.spec_from_file_location(
        "images_to_structured_data_script",
        script_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test")


def test_raises_if_classify_and_skip_classify_both_enabled(tmp_path: Path) -> None:
    script = _load_script_module()
    image_path = tmp_path / "img.jpg"
    _touch(image_path)

    with pytest.raises(RuntimeError, match="--classify and --skip-classify"):
        script.run(
            image_path,
            schema_path=tmp_path / "schema.yaml",
            output_dir=tmp_path / "out",
            classify_only=True,
            skip_classify=True,
        )


def test_single_image_classify_only_writes_filename_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_script_module()
    image_path = tmp_path / "single.webp"
    _touch(image_path)

    monkeypatch.setattr(
        script.image_ingest,
        "is_bms_screenshot",
        lambda *args, **kwargs: {"is_bms_screenshot": True, "reason": None},
    )

    written = script.run(
        image_path,
        schema_path=tmp_path / "schema.yaml",
        output_dir=tmp_path / "out",
        classify_only=True,
    )

    assert len(written) == 1
    assert written[0].name == "single.json"
    assert written[0].is_file()
    captured = capsys.readouterr()
    assert "single.webp: BMS image" in captured.out


def test_classify_prints_not_bms_and_logs_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_script_module()
    image_path = tmp_path / "non_bms.jpg"
    _touch(image_path)
    logged: list[str] = []

    monkeypatch.setattr(
        script.image_ingest,
        "is_bms_screenshot",
        lambda *args, **kwargs: {
            "is_bms_screenshot": False,
            "reason": "not relevant",
        },
    )
    monkeypatch.setattr(
        script.logger,
        "error",
        lambda message, *args: logged.append(message % args),
    )

    script.run(
        image_path,
        schema_path=tmp_path / "schema.yaml",
        output_dir=tmp_path / "out",
        classify_only=True,
    )

    captured = capsys.readouterr()
    assert "non_bms.jpg: not BMS image" in captured.out
    assert logged
    assert "not viable" in logged[0]


def test_directory_input_is_processed_recursively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _load_script_module()
    input_dir = tmp_path / "images"
    _touch(input_dir / "building-1" / "one.jpg")
    _touch(input_dir / "building-2" / "nested" / "two.png")

    monkeypatch.setattr(
        script.image_ingest,
        "extract_bms_snapshot",
        lambda *args, **kwargs: {"building_id": "b", "timestamp": "t"},
    )
    monkeypatch.setattr(
        script.image_ingest,
        "is_bms_screenshot",
        lambda *args, **kwargs: {"is_bms_screenshot": True, "reason": None},
    )
    tqdm_calls = {"count": 0}

    def _fake_tqdm(iterable, **_kwargs):
        tqdm_calls["count"] += 1
        return iterable

    monkeypatch.setattr(script, "tqdm", _fake_tqdm)

    written = script.run(
        input_dir,
        schema_path=tmp_path / "schema.yaml",
        output_dir=tmp_path / "out",
        classify_only=False,
        skip_classify=False,
    )

    names = {p.name for p in written}
    assert names == {"one.json", "two.json"}
    assert tqdm_calls["count"] == 1
