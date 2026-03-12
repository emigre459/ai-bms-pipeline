#!/usr/bin/env python3
"""End-to-end pipeline example: Stage 1 (images → JSON) + Stage 2 (analysis).

Processes 4 individual BMS screenshots plus all images under
data/images/building-1/, then writes a browsable HTML summary to
logs/analysis_<datetime>.html.

Usage:
    uv run scripts/pipeline_example.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
for _p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dotenv

dotenv.load_dotenv(PROJECT_ROOT / ".env")

import analyze_buildings as stage2_script
import images_to_structured_data as stage1_script

from ai_bms_pipeline.config import validate_against_schema
from ai_bms_pipeline.logs import logger

# ─── Demo input set ──────────────────────────────────────────────────────────
_IMAGES = PROJECT_ROOT / "data" / "images"

DEMO_INPUTS: list[Path] = [
    _IMAGES / "11570ca0bd67d822.jpg",
    _IMAGES / "1348aee6cf5141a7.jpg",
    _IMAGES / "1d239babbec2618b.jpg",
    _IMAGES / "2339a2d747c4d647.jpg",
    _IMAGES / "building-1",  # directory — all images inside become one building
]

EXTRACTED_DIR = PROJECT_ROOT / "data" / "extracted_from_images"
ANALYSES_DIR = PROJECT_ROOT / "data" / "analyses"
LOGS_DIR = PROJECT_ROOT / "logs"
SCHEMA_PATH = PROJECT_ROOT / "conf" / "bms-snapshot.schema.yaml"
ANALYSIS_SCHEMA_PATH = PROJECT_ROOT / "conf" / "analysis-output.schema.yaml"


# ─── Stage 1 ─────────────────────────────────────────────────────────────────


def _run_stage1() -> tuple[set[str], list[str]]:
    """Extract BMS snapshots from the demo inputs.

    Returns:
        building_ids: set of building IDs that produced at least one snapshot.
        errors: list of human-readable error strings.
    """
    building_ids: set[str] = set()
    errors: list[str] = []

    for inp in DEMO_INPUTS:
        if not inp.exists():
            msg = f"Input not found, skipping: {inp}"
            logger.warning(msg)
            errors.append(msg)
            continue
        logger.info("Stage 1 — extracting: %s", inp)
        try:
            written = stage1_script.run(
                inp,
                schema_path=SCHEMA_PATH,
                output_dir=EXTRACTED_DIR,
            )
            for p in written:
                building_ids.add(p.parent.name)
        except Exception as exc:
            msg = f"Stage 1 failed for {inp.name}: {exc}"
            logger.error(msg)
            errors.append(msg)

    return building_ids, errors


# ─── Stage 2 ─────────────────────────────────────────────────────────────────


def _run_stage2(building_ids: set[str]) -> tuple[list[Path], list[str]]:
    """Analyze extracted snapshots for the given building IDs.

    Returns:
        written: list of paths to analysis JSON files.
        errors: list of human-readable error strings for failed buildings.
    """
    if not building_ids:
        return [], ["No buildings to analyse — all Stage 1 extractions failed."]

    written = stage2_script.run(
        EXTRACTED_DIR,
        ANALYSES_DIR,
        building_ids=sorted(building_ids),
        max_workers=4,
    )

    errors: list[str] = []
    for bid in sorted(building_ids):
        out = ANALYSES_DIR / f"{bid}.json"
        if not out.exists():
            errors.append(
                f"Analysis not written for building {bid} (likely skipped — insufficient data)."
            )

    return written, errors


# ─── HTML report ─────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f0f4f8;
    color: #1a202c;
    padding: 2rem 1rem;
}
.page { max-width: 960px; margin: 0 auto; }
h1 { font-size: 1.6rem; color: #1a365d; margin-bottom: .25rem; }
.run-meta { color: #718096; font-size: .9rem; margin-bottom: 2rem; }
.summary-bar {
    display: flex; gap: 1rem; flex-wrap: wrap;
    background: #ebf8ff; border: 1px solid #bee3f8;
    border-radius: 8px; padding: 1rem 1.5rem;
    margin-bottom: 2rem; font-size: .95rem;
}
.summary-bar span { font-weight: 600; color: #2b6cb0; }
h2 { font-size: 1.15rem; color: #2d3748; margin: 1.5rem 0 .75rem; }
.card {
    background: #fff; border: 1px solid #e2e8f0;
    border-radius: 10px; padding: 1.5rem;
    margin-bottom: 1.25rem; box-shadow: 0 1px 3px rgba(0,0,0,.06);
}
.card.skipped { border-left: 4px solid #f6ad55; background: #fffaf0; }
.card.error   { border-left: 4px solid #fc8181; background: #fff5f5; }
.card-header {
    display: flex; justify-content: space-between; align-items: baseline;
    flex-wrap: wrap; gap: .5rem; margin-bottom: 1rem;
}
.bid { font-size: 1.05rem; font-weight: 700; color: #2b6cb0; font-family: monospace; }
.cost {
    font-size: 1.1rem; font-weight: 700;
    color: #276749; background: #f0fff4;
    border: 1px solid #9ae6b4; border-radius: 6px;
    padding: .2em .7em;
}
.section-label { font-size: .75rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: .06em; color: #718096; margin: .75rem 0 .35rem; }
.badges { display: flex; flex-wrap: wrap; gap: .3rem; }
.badge {
    display: inline-flex; align-items: center; gap: .3em;
    font-size: .8rem; border-radius: 999px;
    padding: .2em .65em; border: 1px solid transparent;
}
.badge.zero   { background: #edf2f7; color: #a0aec0; border-color: #e2e8f0; }
.badge.nonzero { background: #ebf8ff; color: #2b6cb0; border-color: #bee3f8; font-weight: 600; }
.badge .count { font-size: .95em; }
ul.findings { list-style: none; padding: 0; }
ul.findings li {
    padding: .4em .5em .4em 1.3em;
    position: relative; font-size: .88rem; line-height: 1.5;
    border-bottom: 1px solid #f7fafc;
}
ul.findings li:last-child { border-bottom: none; }
ul.findings li::before {
    content: "›"; position: absolute; left: .1em;
    color: #4a90d9; font-weight: bold; font-size: 1.1em;
}
.violations { font-size: .8rem; color: #c53030; margin-top: .5rem; }
.json-link { font-size: .82rem; color: #4a90d9; text-decoration: none; }
.json-link:hover { text-decoration: underline; }
.errors-section { margin-top: 1rem; }
.error-item { font-size: .88rem; color: #c53030; padding: .25em 0; }
.footer { margin-top: 2.5rem; font-size: .8rem; color: #a0aec0; text-align: center; }
"""

_CATEGORY_LABELS: dict[str, str] = {
    "scheduling": "Scheduling",
    "setpoint_reset": "Setpoint Reset",
    "controls_sequence": "Controls Seq.",
    "recommissioning": "Recommissioning",
    "equipment_upgrade": "Equip. Upgrade",
    "other": "Other",
}


def _ecm_badges(ecm_counts: dict) -> str:
    parts = []
    for key, label in _CATEGORY_LABELS.items():
        count = ecm_counts.get(key, 0)
        cls = "nonzero" if count else "zero"
        parts.append(
            f'<span class="badge {cls}">'
            f'<span class="count">{count}</span> {label}'
            f"</span>"
        )
    return '<div class="badges">' + "".join(parts) + "</div>"


def _building_card(analysis_path: Path, logs_dir: Path) -> str:
    doc = json.loads(analysis_path.read_text())
    bid = doc.get("building_id", analysis_path.stem)
    totals = doc.get("totals") or {}
    total_block = totals.get("total") or {}
    cost = total_block.get("cost_usd_yr")
    co2 = total_block.get("co2e_tons_yr")
    ecm_counts = totals.get("ecm_count_by_category") or {}
    key_findings = (doc.get("key_findings") or [])[:3]
    priority_actions = (doc.get("priority_actions") or [])[:3]

    # Schema validation
    violations = validate_against_schema(doc, ANALYSIS_SCHEMA_PATH)

    # Relative path from logs/ to the analysis JSON
    try:
        rel = analysis_path.relative_to(logs_dir.parent)
        json_href = f"../{rel}"
    except ValueError:
        json_href = str(analysis_path)

    cost_str = f"${cost:,.0f} / yr" if cost is not None else "n/a"
    co2_str = f"{co2:.1f} t CO₂e / yr" if co2 is not None else ""

    findings_html = (
        "".join(f"<li>{_esc(f)}</li>" for f in key_findings)
        if key_findings
        else "<li><em>None recorded.</em></li>"
    )

    actions_html = (
        "".join(f"<li>{_esc(a)}</li>" for a in priority_actions)
        if priority_actions
        else "<li><em>None recorded.</em></li>"
    )

    violations_html = ""
    if violations:
        items = "".join(f"<div>⚠ {_esc(v)}</div>" for v in violations)
        violations_html = f'<div class="violations">{items}</div>'

    num_ecms = len(doc.get("ecms") or [])

    return f"""
<div class="card">
  <div class="card-header">
    <span class="bid">{_esc(bid)}</span>
    <span class="cost">{cost_str}{" &nbsp;·&nbsp; " + _esc(co2_str) if co2_str else ""}</span>
  </div>
  <div class="section-label">{num_ecms} ECM{"s" if num_ecms != 1 else ""} by category</div>
  {_ecm_badges(ecm_counts)}
  <div class="section-label">Key findings</div>
  <ul class="findings">{findings_html}</ul>
  <div class="section-label">Priority actions</div>
  <ul class="findings">{actions_html}</ul>
  {violations_html}
  <div style="margin-top:.75rem">
    <a class="json-link" href="{json_href}">📄 View full analysis JSON</a>
  </div>
</div>
"""


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_html(
    *,
    run_dt: datetime,
    elapsed_s: float,
    n_inputs: int,
    building_ids: set[str],
    written_paths: list[Path],
    stage1_errors: list[str],
    stage2_errors: list[str],
    logs_dir: Path,
) -> str:
    dt_str = run_dt.strftime("%Y-%m-%d %H:%M UTC")
    elapsed_str = f"{elapsed_s:.0f}s" if elapsed_s < 120 else f"{elapsed_s/60:.1f} min"
    n_written = len(written_paths)
    n_errors = len(stage1_errors) + len(stage2_errors)

    summary = (
        f"<span>{n_inputs} input sources</span> &nbsp;·&nbsp; "
        f"<span>{len(building_ids)} building{'s' if len(building_ids) != 1 else ''} extracted</span> &nbsp;·&nbsp; "
        f"<span>{n_written} analysis report{'s' if n_written != 1 else ''} generated</span> &nbsp;·&nbsp; "
        f"<span>{elapsed_str}</span>"
    )
    if n_errors:
        summary += f" &nbsp;·&nbsp; <span style='color:#c53030'>{n_errors} error{'s' if n_errors != 1 else ''}</span>"

    # Building cards (sorted by building ID)
    cards_html = ""
    for p in sorted(written_paths, key=lambda x: x.stem):
        try:
            cards_html += _building_card(p, logs_dir)
        except Exception as exc:
            cards_html += f'<div class="card error"><b>{_esc(p.stem)}</b> — could not render card: {_esc(str(exc))}</div>\n'

    # Errors section
    errors_html = ""
    all_errors = stage1_errors + stage2_errors
    if all_errors:
        items = "".join(
            f'<div class="error-item">⚠ {_esc(e)}</div>' for e in all_errors
        )
        errors_html = f'<h2>Skipped / Errors</h2><div class="card skipped errors-section">{items}</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BMS Pipeline — {_esc(dt_str)}</title>
  <style>{_CSS}</style>
</head>
<body>
<div class="page">
  <h1>BMS Pipeline Run</h1>
  <p class="run-meta">Generated {_esc(dt_str)}</p>
  <div class="summary-bar">{summary}</div>
  <h2>Analysis Results</h2>
  {cards_html if cards_html else '<div class="card skipped">No analyses were produced.</div>'}
  {errors_html}
  <div class="footer">Generated by scripts/pipeline_example.py</div>
</div>
</body>
</html>
"""


# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    run_dt = datetime.now(timezone.utc)
    t0 = time.monotonic()

    print("── Stage 1: extracting BMS snapshots from images ──────────────────")
    building_ids, stage1_errors = _run_stage1()
    print(
        f"   Extracted data for {len(building_ids)} building(s): {sorted(building_ids)}"
    )

    print("\n── Stage 2: analysing buildings ────────────────────────────────────")
    written_paths, stage2_errors = _run_stage2(building_ids)
    print(f"   Wrote {len(written_paths)} analysis file(s).")

    elapsed = time.monotonic() - t0

    # Write HTML report
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    dt_tag = run_dt.strftime("%Y-%m-%d_T%H_%M_%S")
    log_path = LOGS_DIR / f"analysis_{dt_tag}.html"
    html = _render_html(
        run_dt=run_dt,
        elapsed_s=elapsed,
        n_inputs=len(DEMO_INPUTS),
        building_ids=building_ids,
        written_paths=written_paths,
        stage1_errors=stage1_errors,
        stage2_errors=stage2_errors,
        logs_dir=LOGS_DIR,
    )
    log_path.write_text(html, encoding="utf-8")

    print(f"\n── Report written ──────────────────────────────────────────────────")
    print(f"   {log_path}")


if __name__ == "__main__":
    main()
