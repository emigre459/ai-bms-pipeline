#!/usr/bin/env python3
"""Stage 2: Analyze extracted BMS snapshots and produce energy efficiency reports.

Reads all JSON files under data/extracted_from_images/ (one subdir per building),
runs deterministic checks + LLM analysis, and writes one analysis JSON per building
to data/analyses/<building_id>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import dotenv

dotenv.load_dotenv(PROJECT_ROOT / ".env")

from ai_bms_pipeline import analysis as analysis_module
from ai_bms_pipeline.image_ingest import DEFAULT_MODEL
from ai_bms_pipeline.logs import logger
from ai_bms_pipeline.utils import DEFAULT_FACTORS


def _default_input_dir() -> Path:
    return PROJECT_ROOT / "data" / "extracted_from_images"


def _default_output_dir() -> Path:
    return PROJECT_ROOT / "data" / "analyses"


def _safe_filename(building_id: str) -> str:
    """Replace characters that are invalid in filenames."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in building_id)


def run(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    building_ids: list[str] | None = None,
    model: str = DEFAULT_MODEL,
    max_workers: int = 4,
    dry_run: bool = False,
) -> list[Path]:
    """Analyze all (or selected) buildings and write analysis JSON files.

    Args:
        input_dir: Directory containing per-building snapshot subdirs.
        output_dir: Directory to write analysis JSON files into.
        building_ids: If provided, only analyze these building IDs.
        model: Anthropic model to use.
        max_workers: Max concurrent LLM calls.
        dry_run: Skip LLM calls; run only deterministic checks and log findings.

    Returns:
        List of paths to written analysis files.
    """
    in_path = Path(input_dir).expanduser().resolve()
    out_path = Path(output_dir).expanduser().resolve()

    all_buildings = analysis_module.load_all_buildings(in_path)
    if not all_buildings:
        logger.error("No building snapshot directories found in %s", in_path)
        return []

    # Filter if specific building IDs requested
    if building_ids:
        all_buildings = {
            bid: snaps for bid, snaps in all_buildings.items() if bid in building_ids
        }

    if not all_buildings:
        logger.error("No matching buildings found.")
        return []

    out_path.mkdir(parents=True, exist_ok=True)
    written_paths: list[Path] = []

    def _process(building_id: str, snapshots: list[dict]) -> Path | None:
        out_file = out_path / f"{_safe_filename(building_id)}.json"
        try:
            if dry_run:
                findings = analysis_module.run_deterministic_checks(snapshots)
                result = {
                    "building_id": building_id,
                    "dry_run": True,
                    "snapshot_count": len(snapshots),
                    "deterministic_findings": [
                        {
                            "domain": f.domain,
                            "severity": f.severity,
                            "description": f.description,
                            "affected_systems": f.affected_systems,
                        }
                        for f in findings
                    ],
                }
            else:
                result = analysis_module.analyze_building(
                    building_id,
                    snapshots,
                    model=model,
                    factors=DEFAULT_FACTORS,
                )
            out_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return out_file
        except Exception as exc:
            logger.error("Failed to analyze building %s: %s", building_id, exc)
            return None

    items = list(all_buildings.items())
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process, bid, snaps): bid for bid, snaps in items}
        for future in tqdm(
            as_completed(futures),
            total=len(futures),
            desc="Analyzing buildings",
            unit="building",
        ):
            bid = futures[future]
            result_path = future.result()
            if result_path is not None:
                written_paths.append(result_path)
                print(f"Wrote {result_path}")
            else:
                print(f"FAILED: {bid}", file=sys.stderr)

    return written_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze extracted BMS snapshots and produce energy efficiency reports."
    )
    parser.add_argument(
        "--input",
        default=str(_default_input_dir()),
        help="Directory containing per-building snapshot subdirectories.",
    )
    parser.add_argument(
        "--output",
        default=str(_default_output_dir()),
        help="Directory to write per-building analysis JSON files.",
    )
    parser.add_argument(
        "--buildings",
        nargs="+",
        metavar="BUILDING_ID",
        help="Only analyze these building IDs (space-separated).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Anthropic model to use.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Max concurrent LLM calls (default: 4).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run only deterministic checks without calling the LLM.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    run(
        args.input,
        args.output,
        building_ids=args.buildings,
        model=args.model,
        max_workers=args.workers,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
