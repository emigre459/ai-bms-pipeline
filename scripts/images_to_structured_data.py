#!/usr/bin/env python3
"""Extract structured BMS JSON from one image or a directory of images."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ai_bms_pipeline import image_ingest
from ai_bms_pipeline.logs import logger


def _project_root() -> Path:
    return PROJECT_ROOT


def _default_schema_path() -> Path:
    return _project_root() / "conf" / "bms-snapshot.schema.yaml"


def _default_output_dir() -> Path:
    return _project_root() / "data" / "extracted_from_images"


def _is_supported_image(path: Path) -> bool:
    return path.suffix.lower() in image_ingest.ALLOWED_EXTENSIONS


def _resolve_input_images(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if not _is_supported_image(input_path):
            raise ValueError(
                f"Unsupported image file extension for '{input_path}'. "
                f"Supported: {', '.join(image_ingest.ALLOWED_EXTENSIONS)}"
            )
        return [input_path]

    if input_path.is_dir():
        return image_ingest.list_image_paths(input_path)

    raise FileNotFoundError(f"Input path does not exist: {input_path}")


def _write_output(output_dir: Path, image_path: Path, payload: dict) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{image_path.stem}.json"
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path


def run(
    input_path: str | Path,
    *,
    schema_path: str | Path,
    output_dir: str | Path,
    classify_only: bool = False,
    skip_classify: bool = False,
    model: str = image_ingest.DEFAULT_MODEL,
) -> list[Path]:
    if classify_only and skip_classify:
        raise RuntimeError(
            "Cannot use both --classify and --skip-classify at the same time."
        )

    resolved_input = Path(input_path).expanduser().resolve()
    resolved_schema = Path(schema_path).expanduser().resolve()
    resolved_output = Path(output_dir).expanduser().resolve()
    input_images = _resolve_input_images(resolved_input)
    is_directory_input = resolved_input.is_dir()

    iterator = (
        tqdm(input_images, desc="Processing images", unit="image")
        if is_directory_input
        else input_images
    )

    written_paths: list[Path] = []
    for image_path in iterator:
        if classify_only:
            payload = image_ingest.is_bms_screenshot(
                image_path,
                schema_path=resolved_schema,
                model=model,
            )
            is_viable = bool(payload.get("is_bms_screenshot", False))
            if not is_viable:
                logger.error("Image classified as not viable: %s", image_path)
            bms_indicator = "" if is_viable else "not "
            print(f"{image_path.name}: {bms_indicator}BMS image")
        else:
            classifier: dict | None = None
            is_viable = True
            if not skip_classify:
                classifier = image_ingest.is_bms_screenshot(
                    image_path=image_path,
                    schema_path=resolved_schema,
                    model=model,
                )
                is_viable = bool(classifier.get("is_bms_screenshot", False))
                if not is_viable:
                    logger.error("Image classified as not viable: %s", image_path)

            hint = image_ingest.derive_building_id_hint(
                image_path=image_path,
                image_root=resolved_input if resolved_input.is_dir() else None,
            )
            payload = image_ingest.extract_bms_snapshot(
                image_path=image_path,
                building_id_hint=hint,
                schema_path=resolved_schema,
                model=model,
            )
            if classifier is not None:
                payload["classifier"] = classifier

        out_path = _write_output(resolved_output, image_path, payload)
        written_paths.append(out_path)

    return written_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract structured BMS JSON from one image or from all images "
            "under a directory (recursively)."
        )
    )
    parser.add_argument(
        "input_path",
        help=(
            "Path to a single image file, or a directory to parse recursively "
            "for supported image types."
        ),
    )
    parser.add_argument(
        "--config",
        default=str(_default_schema_path()),
        help="Schema config file path.",
    )
    parser.add_argument(
        "--output",
        default=str(_default_output_dir()),
        help=(
            "Output directory. Each image result is written as "
            "<image_filename>.json."
        ),
    )
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="Run classifier step alone and write classifier output only.",
    )
    parser.add_argument(
        "--skip-classify",
        action="store_true",
        help=(
            "Skip classifier call and run extraction directly "
            "(no classifier result in output)."
        ),
    )
    parser.add_argument(
        "--model",
        default=image_ingest.DEFAULT_MODEL,
        help="Anthropic model to use.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    written = run(
        args.input_path,
        schema_path=args.config,
        output_dir=args.output,
        classify_only=args.classify_only,
        skip_classify=args.skip_classify,
        model=args.model,
    )
    for path in written:
        # logger.info(f"Wrote {path}")
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
