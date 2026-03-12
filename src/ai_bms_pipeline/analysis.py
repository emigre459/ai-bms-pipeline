"""Stage 2: Energy efficiency analysis of extracted BMS snapshots.

Strategy:
  1. Deterministic rule checks — flag specific patterns that are unambiguous
     given the data (e.g. HWS temp too high for OAT, fan speed imbalance).
  2. LLM synthesis — one call per building covers all 7 analysis domains,
     uses the deterministic findings as grounded evidence, adds qualitative
     observations, and produces the full analysis-output schema payload.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from ai_bms_pipeline.image_ingest import DEFAULT_MODEL, _get_client
from ai_bms_pipeline.logs import logger
from ai_bms_pipeline.utils import DEFAULT_FACTORS, aggregate_totals

# Minimum number of non-null operational fields a snapshot must have to be
# included in analysis.  Below this threshold the snapshot is essentially empty.
_MIN_DATA_SCORE = 2

# ─── Deterministic findings ────────────────────────────────────────────────────


@dataclass
class Finding:
    domain: str
    description: str
    affected_systems: list[str] = field(default_factory=list)
    severity: str = "medium"  # high | medium | low
    evidence: dict[str, Any] = field(default_factory=dict)


def _check_hws_reset(snapshots: list[dict]) -> list[Finding]:
    """HWS temp high relative to OAT with no OAT-reset curve configured."""
    findings: list[Finding] = []
    for snap in snapshots:
        hp = snap.get("heating_plant") or {}
        hws_act = hp.get("hws_temp_actual_f")
        hws_set = hp.get("hws_temp_setpoint_f")
        reset = hp.get("hws_oat_reset_active") or {}
        oat = (snap.get("conditions") or {}).get("oat_f")

        reset_configured = any(
            reset.get(k) is not None
            for k in ("oat_min_f", "oat_max_f", "hws_min_f", "hws_max_f")
        )

        if hws_act is not None and hws_act > 130 and oat is not None and oat > 45:
            findings.append(
                Finding(
                    domain="hws_reset",
                    description=(
                        f"HWS actual {hws_act}°F while OAT is {oat}°F — "
                        "water temperature is elevated for the ambient condition."
                    ),
                    affected_systems=["heating_plant"],
                    severity="high",
                    evidence={
                        "hws_temp_actual_f": hws_act,
                        "oat_f": oat,
                        "reset_configured": reset_configured,
                    },
                )
            )
        elif hws_set is not None and hws_set > 140 and not reset_configured:
            findings.append(
                Finding(
                    domain="hws_reset",
                    description=(
                        f"HWS setpoint is fixed at {hws_set}°F with no OAT-reset curve — "
                        "boiler likely firing to maintain elevated temperatures on mild days."
                    ),
                    affected_systems=["heating_plant"],
                    severity="medium",
                    evidence={
                        "hws_temp_setpoint_f": hws_set,
                        "reset_configured": reset_configured,
                    },
                )
            )
    return findings


def _check_fan_imbalance(snapshots: list[dict]) -> list[Finding]:
    """Multiple fans in the same system running at materially different VFD speeds."""
    findings: list[Finding] = []
    for snap in snapshots:
        for system in snap.get("air_systems") or []:
            fans = [
                f for f in (system.get("fans") or []) if f.get("vfd_pct") is not None
            ]
            if len(fans) < 2:
                continue
            speeds = [f["vfd_pct"] for f in fans]
            spread = max(speeds) - min(speeds)
            if spread > 15:
                findings.append(
                    Finding(
                        domain="fan_balancing",
                        description=(
                            f"Fan speeds in {system['id']} span {spread:.0f}% "
                            f"({min(speeds):.0f}%–{max(speeds):.0f}%). "
                            "Speed imbalance causes backpressure and energy waste."
                        ),
                        affected_systems=[system["id"]],
                        severity="high" if spread > 30 else "medium",
                        evidence={
                            "system_id": system["id"],
                            "fan_speeds": {f["id"]: f["vfd_pct"] for f in fans},
                            "spread_pct": spread,
                        },
                    )
                )
    return findings


def _check_economizer(snapshots: list[dict]) -> list[Finding]:
    """Economizer not exploiting free-cooling conditions, or humidity risk."""
    findings: list[Finding] = []
    for snap in snapshots:
        oat = (snap.get("conditions") or {}).get("oat_f")
        rh = (snap.get("conditions") or {}).get("rh_pct")
        for system in snap.get("air_systems") or []:
            econ = system.get("economizer") or {}
            active = econ.get("active")
            pos = econ.get("position_pct")
            rat = (system.get("temperatures") or {}).get("return_air_f")

            # Missed free-cooling: OAT below return air and economizer closed/disabled
            if (
                oat is not None
                and rat is not None
                and oat < rat - 5
                and (active is False or pos == 0.0)
            ):
                findings.append(
                    Finding(
                        domain="economizer",
                        description=(
                            f"OAT ({oat}°F) is {rat - oat:.0f}°F below return air ({rat}°F) "
                            f"but economizer for {system['id']} is closed/disabled — "
                            "free cooling opportunity missed."
                        ),
                        affected_systems=[system["id"]],
                        severity="high",
                        evidence={
                            "oat_f": oat,
                            "return_air_f": rat,
                            "economizer_active": active,
                            "position_pct": pos,
                        },
                    )
                )

            # Humidity risk: economizer open when RH is high
            if (
                rh is not None
                and rh > 70
                and active is True
                and pos is not None
                and pos > 30
            ):
                findings.append(
                    Finding(
                        domain="economizer",
                        description=(
                            f"Economizer for {system['id']} is open ({pos:.0f}%) "
                            f"while outdoor RH is {rh:.0f}% — "
                            "humid outdoor air is increasing latent cooling load."
                        ),
                        affected_systems=[system["id"]],
                        severity="medium",
                        evidence={"rh_pct": rh, "position_pct": pos},
                    )
                )
    return findings


def _check_simultaneous_heat_cool(snapshots: list[dict]) -> list[Finding]:
    """Heating and cooling plants both appear active at the same time."""
    findings: list[Finding] = []
    for snap in snapshots:
        hp = snap.get("heating_plant") or {}
        cp = snap.get("cooling_plant") or {}
        oat = (snap.get("conditions") or {}).get("oat_f")

        hws_act = hp.get("hws_temp_actual_f")
        vav_heat = hp.get("vav_heat_request_pct")
        boilers_on = any((b.get("status") == "on") for b in (hp.get("boilers") or []))
        heating_active = (
            boilers_on
            or (hws_act is not None and hws_act > 120)
            or (vav_heat is not None and vav_heat > 10)
        )

        chws_act = cp.get("chws_temp_actual_f")
        cooling_units_on = any(
            (u.get("status") == "on") for u in (cp.get("units") or [])
        )
        cooling_active = chws_act is not None or cooling_units_on

        zone_reheat = any(
            z.get("reheat_active") is True for z in (snap.get("zones") or [])
        )

        if heating_active and cooling_active and oat is not None and oat > 50:
            evidence: dict[str, Any] = {
                "oat_f": oat,
                "hws_temp_actual_f": hws_act,
                "vav_heat_request_pct": vav_heat,
                "chws_temp_actual_f": chws_act,
                "zone_reheat_detected": zone_reheat,
            }
            findings.append(
                Finding(
                    domain="simultaneous_heat_cool",
                    description=(
                        "Heating and cooling plants appear simultaneously active "
                        f"with OAT at {oat}°F. "
                        + ("Zone reheat also active. " if zone_reheat else "")
                        + "Cross-system conditioning is one of the most wasteful HVAC patterns."
                    ),
                    affected_systems=["heating_plant", "cooling_plant"],
                    severity="high",
                    evidence=evidence,
                )
            )
    return findings


def _check_scheduling(snapshots: list[dict]) -> list[Finding]:
    """Equipment running out-of-schedule or in occupied mode at off-hours."""
    findings: list[Finding] = []
    for snap in snapshots:
        ts_str = snap.get("timestamp")
        try:
            ts = (
                datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts_str
                else None
            )
        except (ValueError, AttributeError):
            ts = None

        for system in snap.get("air_systems") or []:
            if system.get("out_of_schedule") is True:
                findings.append(
                    Finding(
                        domain="scheduling",
                        description=(
                            f"{system['id']} is flagged as running out-of-schedule. "
                            "Verify whether the schedule matches actual occupancy."
                        ),
                        affected_systems=[system["id"]],
                        severity="high",
                        evidence={"out_of_schedule": True, "timestamp": ts_str},
                    )
                )
                continue

            if ts and system.get("mode") == "occupied":
                hour = ts.hour
                weekday = ts.weekday()  # 0=Mon, 6=Sun
                is_weekend = weekday >= 5
                is_off_hours = hour < 6 or hour >= 21

                if is_weekend or is_off_hours:
                    context = "weekend" if is_weekend else f"{hour:02d}:00 local"
                    findings.append(
                        Finding(
                            domain="scheduling",
                            description=(
                                f"{system['id']} is in occupied mode at {context} "
                                f"({ts_str}). Verify this reflects actual occupancy."
                            ),
                            affected_systems=[system["id"]],
                            severity="medium",
                            evidence={
                                "mode": "occupied",
                                "timestamp": ts_str,
                                "is_weekend": is_weekend,
                                "hour": hour,
                            },
                        )
                    )
    return findings


def _check_static_pressure(snapshots: list[dict]) -> list[Finding]:
    """Static pressure elevated above setpoint at low VAV demand."""
    findings: list[Finding] = []
    for snap in snapshots:
        for system in snap.get("air_systems") or []:
            sp_act = system.get("sa_static_pressure_actual_inwc")
            sp_set = system.get("sa_static_pressure_setpoint_inwc")
            vav = system.get("vav_demand_pct")

            if sp_act is None or sp_set is None or sp_set == 0:
                continue
            if sp_act > sp_set * 1.15 and (vav is None or vav < 80):
                findings.append(
                    Finding(
                        domain="static_pressure",
                        description=(
                            f"{system['id']} static pressure actual {sp_act:.2f} in.wc "
                            f"exceeds setpoint {sp_set:.2f} in.wc "
                            + (
                                f"at only {vav:.0f}% VAV demand."
                                if vav is not None
                                else "— VAV demand unknown."
                            )
                            + " Static pressure reset could allow fan slowdown."
                        ),
                        affected_systems=[system["id"]],
                        severity="medium",
                        evidence={
                            "sp_actual": sp_act,
                            "sp_setpoint": sp_set,
                            "vav_demand_pct": vav,
                        },
                    )
                )
    return findings


def _check_supply_air_temperature(snapshots: list[dict]) -> list[Finding]:
    """Supply air temp very low on mild/cool days, or below setpoint."""
    findings: list[Finding] = []
    for snap in snapshots:
        oat = (snap.get("conditions") or {}).get("oat_f")
        season = (snap.get("conditions") or {}).get("season")
        for system in snap.get("air_systems") or []:
            temps = system.get("temperatures") or {}
            sa_act = temps.get("supply_air_actual_f")
            sa_set = temps.get("supply_air_setpoint_f")

            if sa_act is None:
                continue

            if sa_set is not None and sa_act < sa_set - 3:
                findings.append(
                    Finding(
                        domain="supply_air_temperature",
                        description=(
                            f"{system['id']} supply air actual {sa_act:.1f}°F is "
                            f"{sa_set - sa_act:.1f}°F below setpoint {sa_set:.1f}°F — "
                            "overcooling supply air increases heating/reheat demand downstream."
                        ),
                        affected_systems=[system["id"]],
                        severity="medium",
                        evidence={"sa_actual": sa_act, "sa_setpoint": sa_set},
                    )
                )

            if (
                sa_act < 55
                and season in ("heating", "shoulder")
                and oat is not None
                and oat < 65
            ):
                findings.append(
                    Finding(
                        domain="supply_air_temperature",
                        description=(
                            f"{system['id']} supply air is {sa_act:.1f}°F in {season} season "
                            f"(OAT {oat:.0f}°F) — SAT reset would raise supply temperature "
                            "as cooling demand drops, reducing reheat energy."
                        ),
                        affected_systems=[system["id"]],
                        severity="medium",
                        evidence={"sa_actual": sa_act, "oat_f": oat, "season": season},
                    )
                )
    return findings


def _snapshot_data_score(snap: dict) -> int:
    """Count how many meaningful operational fields are non-null in a snapshot.

    Used to determine if a snapshot has enough data to be worth analysing.
    Each distinct data point that would actually affect an energy analysis
    increments the score by 1.
    """
    score = 0
    c = snap.get("conditions") or {}
    if c.get("oat_f") is not None:
        score += 1
    if c.get("rh_pct") is not None:
        score += 1

    hp = snap.get("heating_plant") or {}
    if hp.get("hws_temp_actual_f") is not None:
        score += 1
    if hp.get("hws_temp_setpoint_f") is not None:
        score += 1
    if hp.get("vav_heat_request_pct") is not None:
        score += 1

    cp = snap.get("cooling_plant") or {}
    if cp.get("chws_temp_actual_f") is not None:
        score += 1
    if cp.get("chws_temp_setpoint_f") is not None:
        score += 1

    for system in snap.get("air_systems") or []:
        temps = system.get("temperatures") or {}
        if temps.get("supply_air_actual_f") is not None:
            score += 1
        if temps.get("return_air_f") is not None:
            score += 1
        if system.get("sa_static_pressure_actual_inwc") is not None:
            score += 1
        if system.get("vav_demand_pct") is not None:
            score += 1
        for fan in system.get("fans") or []:
            if fan.get("vfd_pct") is not None:
                score += 1

    for zone in snap.get("zones") or []:
        if zone.get("space_temp_actual_f") is not None:
            score += 1

    return score


def _filter_useful_snapshots(
    snapshots: list[dict],
    building_id: str,
) -> list[dict]:
    """Return only snapshots that have enough operational data to analyse.

    Filters out snapshots where:
    - The classifier marked the image as not a BMS screenshot, OR
    - The data score is below _MIN_DATA_SCORE (essentially all-null extraction).

    Logs a warning for each filtered snapshot so it can be traced.
    """
    useful = []
    for snap in snapshots:
        ts = snap.get("timestamp", "unknown")
        classifier = snap.get("classifier") or {}

        if classifier.get("is_bms_screenshot") is False:
            logger.warning(
                "Skipping snapshot %s / %s: classifier says not a BMS screenshot (%s)",
                building_id,
                ts,
                classifier.get("reason", "no reason given"),
            )
            continue

        score = _snapshot_data_score(snap)
        if score < _MIN_DATA_SCORE:
            logger.warning(
                "Skipping snapshot %s / %s: data score %d < %d (too many nulls). "
                "Classifier fields present: %s",
                building_id,
                ts,
                score,
                _MIN_DATA_SCORE,
                classifier.get("structured_fields_present", []),
            )
            continue

        useful.append(snap)
    return useful


def run_deterministic_checks(snapshots: list[dict]) -> list[Finding]:
    """Run all deterministic rule checks and return deduplicated findings."""
    findings: list[Finding] = []
    for checker in [
        _check_hws_reset,
        _check_fan_imbalance,
        _check_economizer,
        _check_simultaneous_heat_cool,
        _check_scheduling,
        _check_static_pressure,
        _check_supply_air_temperature,
    ]:
        findings.extend(checker(snapshots))

    # Deduplicate: keep only the highest-severity finding per domain+system combo
    seen: dict[tuple[str, str], str] = {}
    deduped: list[Finding] = []
    severity_rank = {"high": 3, "medium": 2, "low": 1}
    for f in findings:
        key = (f.domain, ",".join(sorted(f.affected_systems)))
        existing = seen.get(key)
        if existing is None or severity_rank[f.severity] > severity_rank[existing]:
            seen[key] = f.severity
            deduped = [
                x
                for x in deduped
                if (x.domain, ",".join(sorted(x.affected_systems))) != key
            ]
            deduped.append(f)
    return deduped


# ─── LLM synthesis ────────────────────────────────────────────────────────────


def _analysis_output_schema_text() -> str:
    schema_path = (
        Path(__file__).resolve().parents[2] / "conf" / "analysis-output.schema.yaml"
    )
    return schema_path.read_text(encoding="utf-8")


def _analysis_output_json_schema() -> dict:
    """Minimal JSON schema for Anthropic structured output enforcement.

    Only constrains the fields that matter most: ECM enum values and the
    energy quantities that aggregate_totals reads. The totals/factors/regulatory
    blocks are injected or recomputed deterministically after the LLM call, so
    they are not included in the schema to keep grammar size within API limits.
    """
    nullable_number = {"type": ["number", "null"]}
    nullable_string = {"type": ["string", "null"]}

    ecm_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "category": {
                "type": "string",
                "enum": [
                    "scheduling",
                    "setpoint_reset",
                    "controls_sequence",
                    "recommissioning",
                    "equipment_upgrade",
                    "lighting",
                    "plug_load",
                    "envelope",
                    "other",
                ],
            },
            "description": {"type": "string"},
            "affected_systems": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            # Flat energy quantities — aggregate_totals reads these paths
            "kwh_yr": nullable_number,
            "therms_yr": nullable_number,
            "mlb_yr": nullable_number,
            "capital_cost_usd": nullable_number,
            "complexity": {"type": "string", "enum": ["low", "medium", "high"]},
            "priority": {"type": "string", "enum": ["high", "medium", "low"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "implementation_notes": nullable_string,
        },
        "required": [
            "id",
            "name",
            "category",
            "description",
            "affected_systems",
            "assumptions",
            "kwh_yr",
            "therms_yr",
            "mlb_yr",
            "capital_cost_usd",
            "complexity",
            "priority",
            "confidence",
            "implementation_notes",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "building_id": {"type": "string"},
            "analysis_date": {"type": "string"},
            "analyst_notes": nullable_string,
            "ecms": {"type": "array", "items": ecm_schema},
            "key_findings": {"type": "array", "items": {"type": "string"}},
            "priority_actions": {"type": "array", "items": {"type": "string"}},
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "building_id",
            "analysis_date",
            "analyst_notes",
            "ecms",
            "key_findings",
            "priority_actions",
            "open_questions",
        ],
    }


def _analysis_domains_text() -> str:
    domains_path = (
        Path(__file__).resolve().parents[2] / "references" / "analysis_domains.md"
    )
    return domains_path.read_text(encoding="utf-8")


_NOTES_MAX_CHARS = 400


def _trim_snapshot_for_prompt(snap: dict) -> dict:
    """Strip verbose fields that inflate token count without aiding analysis."""
    import copy

    s = copy.deepcopy(snap)
    s.pop("classifier", None)
    for system in s.get("air_systems") or []:
        notes = system.get("notes")
        if notes and len(notes) > _NOTES_MAX_CHARS:
            system["notes"] = notes[:_NOTES_MAX_CHARS] + "…[truncated]"
    for zone in s.get("zones") or []:
        notes = zone.get("notes")
        if notes and len(notes) > _NOTES_MAX_CHARS:
            zone["notes"] = notes[:_NOTES_MAX_CHARS] + "…[truncated]"
    return s


def _snapshots_summary(snapshots: list[dict]) -> str:
    """Compact multi-line summary of all snapshots for the LLM prompt."""
    lines = [f"Total snapshots: {len(snapshots)}"]
    for i, s in enumerate(snapshots):
        lines.append(f"\n--- Snapshot {i + 1} ---")
        lines.append(json.dumps(_trim_snapshot_for_prompt(s), indent=2))
    return "\n".join(lines)


def _findings_summary(findings: list[Finding]) -> str:
    if not findings:
        return "No deterministic findings triggered."
    lines = []
    for f in findings:
        lines.append(
            f"[{f.domain.upper()} | {f.severity}] {f.description} "
            f"(systems: {', '.join(f.affected_systems) or 'unknown'}) "
            f"| evidence: {json.dumps(f.evidence)}"
        )
    return "\n".join(lines)


def analyze_building(
    building_id: str,
    snapshots: list[dict],
    *,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    factors: dict | None = None,
) -> dict:
    """Produce a full analysis-output schema document for one building.

    Reads only from the pre-extracted snapshot dicts — no images are re-ingested.

    Steps:
      1. Filter snapshots to those with sufficient operational data.
      2. Run deterministic rule checks on the filtered set.
      3. Call LLM (text-only) to synthesise ECMs and narrative.
      4. Recompute totals block via utils.aggregate_totals for arithmetic auditability.

    Raises:
        ValueError: if no snapshots survive the data-quality filter.
    """
    useful = _filter_useful_snapshots(snapshots, building_id)
    if not useful:
        logger.error(
            "Skipping analysis for building %s: all %d snapshot(s) failed data-quality "
            "filter (too many null fields or classifier rejected). "
            "Re-run Stage 1 extraction with better source images to get usable data.",
            building_id,
            len(snapshots),
        )
        raise ValueError(
            f"No usable snapshots for building {building_id!r} — "
            "all snapshots are below the minimum data score or were classifier-rejected."
        )

    api_client = _get_client(client)
    f = factors or DEFAULT_FACTORS
    findings = run_deterministic_checks(useful)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = f"""You are an expert commercial building energy analyst. Analyze the BMS data below
and produce a complete energy efficiency analysis as JSON.

## Building ID
{building_id}

## Analysis Date
{today}

## Energy Factors (use these for all cost/carbon calculations)
{json.dumps(f, indent=2)}

## BMS Snapshot Data ({len(useful)} of {len(snapshots)} snapshots passed data-quality filter)
{_snapshots_summary(useful)}

## Deterministic Pre-Checks (already computed — incorporate these as ECMs where relevant)
{_findings_summary(findings)}

## Analysis Domains to Cover
{_analysis_domains_text()}

## ECM Output Format
Each ECM in the `ecms` array must have these fields:
- id: short snake_case identifier
- name: human-readable name
- category: one of scheduling|setpoint_reset|controls_sequence|recommissioning|equipment_upgrade|lighting|plug_load|envelope|other
- description: 2-4 sentences
- affected_systems: list of system names
- assumptions: list of key assumptions made
- kwh_yr: annual electricity savings in kWh (number or null)
- therms_yr: annual gas savings in therms (number or null)
- mlb_yr: annual steam savings in Mlb (number or null)
- capital_cost_usd: estimated capital cost in USD (number or null)
- complexity: low|medium|high
- priority: high|medium|low
- confidence: high|medium|low
- implementation_notes: brief notes on implementation approach (string or null)

## Instructions
- Compute savings using: cost = energy_qty × factor_rate, carbon = energy_qty × co2e_factor.
- Set kwh_yr/therms_yr/mlb_yr to null for commodities this ECM doesn't affect.
- Confidence: "high" = clear measured data; "medium" = estimated from BMS + typical values; "low" = rough order-of-magnitude.
- If data is sparse, note data gaps in open_questions and use lower confidence.
- Include at least 3 key_findings and 3 priority_actions.
- Do not fabricate building characteristics unless inferable from data; document inferences in assumptions[].
"""

    output_schema = _analysis_output_json_schema()
    messages = [{"role": "user", "content": prompt}]
    for max_tokens in (6000, 10000):
        response = api_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            output_config={"format": {"type": "json_schema", "schema": output_schema}},
            messages=messages,
        )
        if getattr(response, "stop_reason", None) != "max_tokens":
            break

    from ai_bms_pipeline.image_ingest import _extract_json_from_response
    from ai_bms_pipeline.utils import ecm_savings_block, simple_payback

    analysis = _extract_json_from_response(response)

    # Expand flat ECM fields into proper nested savings/implementation blocks
    ecms = analysis.get("ecms") or []
    for ecm in ecms:
        kwh = ecm.pop("kwh_yr", None)
        therms = ecm.pop("therms_yr", None)
        mlb = ecm.pop("mlb_yr", None)
        capital = ecm.pop("capital_cost_usd", None)
        complexity = ecm.pop("complexity", "medium")
        impl_notes = ecm.pop("implementation_notes", None)

        ecm["savings"] = ecm_savings_block(
            kwh_yr=kwh, therms_yr=therms, mlb_yr=mlb, factors=f
        )
        total_cost = ecm["savings"]["total"]["cost_usd_yr"]
        ecm["implementation"] = {
            "capital_cost_usd": round(capital, 2) if capital is not None else None,
            "requires_capital": capital is not None and capital > 0,
            "complexity": complexity,
            "payback_years": simple_payback(capital, total_cost) if capital else None,
            "notes": impl_notes,
        }

    # Recompute totals deterministically from expanded ECM savings blocks
    analysis["totals"] = aggregate_totals(ecms, f)

    # Inject fields that are computed by code, not the LLM
    analysis["building_id"] = building_id
    analysis["analysis_date"] = today
    analysis["factors"] = f
    analysis.setdefault("regulatory_impact", [])

    return analysis


# ─── File I/O helpers ─────────────────────────────────────────────────────────


def load_building_snapshots(building_dir: Path) -> list[dict]:
    """Load and sort all JSON snapshot files for one building directory."""
    snapshots = []
    for p in sorted(building_dir.glob("*.JSON")):
        try:
            snapshots.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return snapshots


def load_all_buildings(
    extracted_dir: Path,
) -> dict[str, list[dict]]:
    """Return {building_id: [snapshots]} for every subdirectory."""
    buildings: dict[str, list[dict]] = {}
    for building_dir in sorted(extracted_dir.iterdir()):
        if not building_dir.is_dir():
            continue
        snaps = load_building_snapshots(building_dir)
        if snaps:
            # Use building_id from first snapshot if it differs from dir name
            bid = snaps[0].get("building_id") or building_dir.name
            buildings[bid] = snaps
    return buildings
